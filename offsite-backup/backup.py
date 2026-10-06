#!/usr/bin/env python3
"""Encrypted home backups with online SQLite copies and fail-closed retention."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import tempfile
from zoneinfo import ZoneInfo
import socket

SKIP_DIRS = {'node_modules', '.venv', 'venv', '__pycache__', '.cache', '.npm', '.rustup'}
EXCLUDED_RELATIVE = ('.local/share/pipx/venvs', '.cargo/registry')
CONFIG = Path.home() / '.config/restic-offsite/config.json'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        tmp.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def run_lock(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield



def restic(config, args, capture=False):
    env = os.environ.copy()
    # Do not inherit another repository, password command or cloud session token.
    for key in list(env):
        if key.startswith(('RESTIC_', 'AWS_')):
            env.pop(key)
    env.update(RESTIC_REPOSITORY=config['repository'], RESTIC_PASSWORD=config['password'],
               AWS_ACCESS_KEY_ID=config['access_key'], AWS_SECRET_ACCESS_KEY=config['secret_key'])
    try:
        return subprocess.run([config.get('restic', '/usr/bin/restic'), '--no-cache', *args],
                              env=env, check=True, capture_output=capture,
                              timeout=config.get('restic_timeout', 10 * 60 * 60))
    except subprocess.CalledProcessError as error:
        if capture:
            # Keep diagnostics, never dump restored stdout or credentials.
            detail = (error.stderr or b'').decode('utf-8', errors='replace')
            for field in ('password', 'secret_key', 'access_key'):
                if config.get(field):
                    detail = detail.replace(config[field], '[redacted]')
            atomic_json(Path(config['state']) / 'last-error.json', {
                'operation': args[0], 'exit_code': error.returncode,
                'stderr': detail[-32768:],
                'at': datetime.now(timezone.utc).isoformat()})
        raise



def snapshot_databases(home, stage, timeout=600):
    """Inspect regular files; SQLite backup API includes committed WAL transactions."""
    databases = []
    def walk_error(error):
        if not isinstance(error, FileNotFoundError):
            raise error
    for base, dirs, files in os.walk(home, onerror=walk_error):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != '.git'
                   and not (Path(base) / d).is_symlink()
                   and Path(base) / d not in (stage, home / '.codex/recovery-backups', home / 'migration-archive')
                   and Path(base) / d not in {home / relative for relative in EXCLUDED_RELATIVE}]
        for name in files:
            path = Path(base) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                with path.open('rb') as file:
                    if file.read(16) == b'SQLite format 3\x00':
                        databases.append(path)
            except FileNotFoundError:
                # A removed transient file contains nothing that can still be backed up.
                continue
    # Keep space for the OS and for DB growth during online backup.
    required = sum(p.stat().st_size + (Path(str(p) + '-wal').stat().st_size
                   if Path(str(p) + '-wal').exists() else 0) for p in databases)
    if shutil.disk_usage(home).free < required * 2 + 512 * 1024 * 1024:
        raise RuntimeError('Insufficient disk space for consistent database copies')
    stage.mkdir(parents=True, mode=0o700)
    manifest = []
    for source in databases:
        relative = source.relative_to(home)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        deadline = time.monotonic() + timeout
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError('Database snapshot timed out')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30)) as src:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst, pages=256, progress=progress)
                if dst.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise RuntimeError('Database copy failed integrity check')
        target.chmod(0o600)
        manifest.append(str(relative))
    atomic_json(stage / 'manifest.json', manifest)
    return manifest


def run_backup(config):
    home, state = Path(config['home']), Path(config['state'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with run_lock(state):
        stage = state / 'sqlite'
        status = {'status': 'running', 'stage': 'sqlite', 'verified': False,
                  'started_at': datetime.now(timezone.utc).isoformat()}
        atomic_json(state / 'status.json', status)
        try:
            # Only this runner owns this exact staging directory.
            if stage.exists():
                shutil.rmtree(stage)
            manifest = snapshot_databases(home, stage, timeout=config.get('sqlite_timeout', 600))
            probe = secrets.token_hex(32)
            (stage / 'recovery-probe.txt').write_text(probe)
            args = ['backup', str(home), '--host', config['host'], '--tag', 'bebop-offsite', '--json']
            for directory in sorted(SKIP_DIRS):
                args += ['--exclude', directory]
            # Exclude live SQLite files and journals; restore the consistent copies instead.
            for relative in manifest:
                for suffix in ('', '-wal', '-shm', '-journal'):
                    path = str(home / relative) + suffix
                    pattern = ''.join('\\' + c if c in '\\*?[]' else c for c in path)
                    args += ['--exclude', pattern]
            for relative in EXCLUDED_RELATIVE:
                args += ['--exclude', str(home / relative)]
            status['stage'] = 'backup'
            atomic_json(state / 'status.json', status)
            result = restic(config, args, capture=True)
            events = [json.loads(line) for line in result.stdout.splitlines() if line]
            summary = next(e for e in reversed(events) if e.get('message_type') == 'summary')
            status.update(status='backed_up', snapshot_id=summary['snapshot_id'],
                          probe=probe, database_count=len(manifest),
                          finished_at=datetime.now(timezone.utc).isoformat())
            atomic_json(state / 'status.json', status)
            status['stage'] = 'verification'
            atomic_json(state / 'status.json', status)
            status = _verify_locked(config)
            atomic_json(state / 'last-success.json', status)
            status['stage'] = 'retention'
            atomic_json(state / 'status.json', status)
            retention = ['forget', '--host', config['host'], '--tag', 'bebop-offsite',
                         '--group-by', 'host,tags', '--keep-daily', '30', '--keep-monthly', '6']
            # Weekly pruning frees old data; retention itself runs after each good backup.
            if datetime.now(ZoneInfo('Europe/Lisbon')).weekday() == 6:
                retention += ['--prune']
            restic(config, retention)
            status.update(status='ok', stage='complete')
            atomic_json(state / 'status.json', status)
            print('Backup and recovery check passed.', flush=True)
        except Exception as error:
            status.update(status='failed', failed_stage=status['stage'],
                          error_type=type(error).__name__,
                          restic_exit_code=getattr(error, 'returncode', None),
                          failed_at=datetime.now(timezone.utc).isoformat())
            atomic_json(state / 'status.json', status)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def verify(config):
    with run_lock(Path(config['state'])):
        return _verify_locked(config)


def _verify_locked(config):
    state = Path(config['state'])
    status = json.loads((state / 'status.json').read_text())
    if status['status'] not in ('backed_up', 'ok'):
        raise RuntimeError('No complete backup available to verify')
    # Every run checks repository structure and restores an exact snapshot's probe.
    restic(config, ['check'])
    probe_path = str(state / 'sqlite/recovery-probe.txt')
    actual = restic(config, ['dump', status['snapshot_id'], probe_path], capture=True).stdout.decode()
    if actual != status['probe']:
        raise RuntimeError('Restored recovery probe does not match')
    status.update(status='ok', verified=True, verified_at=datetime.now(timezone.utc).isoformat())
    atomic_json(state / 'status.json', status)
    return status


def configure():
    restic_binary = shutil.which('restic')
    if not restic_binary:
        raise RuntimeError('Install restic before configuring the backup')
    if CONFIG.exists():
        raise RuntimeError('Configuration already exists; refusing to overwrite recovery credentials')
    print('Create a PRIVATE B2 bucket. Use a bucket-scoped Read and Write application key.')
    print('Save the endpoint, bucket, key ID, application key and a new backup password in Bitwarden.')
    endpoint = input('B2 S3 endpoint (s3.REGION.backblazeb2.com): ').strip()
    bucket = input('Bucket name: ').strip()
    if not re.fullmatch(r's3\.[a-z0-9-]+\.backblazeb2\.com', endpoint):
        raise ValueError('Use the S3 endpoint displayed for your B2 bucket')
    if not re.fullmatch(r'[a-zA-Z0-9-]{6,63}', bucket):
        raise ValueError('Invalid B2 bucket name')
    key_id = getpass.getpass('Application key ID: ').strip()
    app_key = getpass.getpass('Application key: ').strip()
    password = getpass.getpass('Backup password saved in Bitwarden (at least 20 characters): ')
    repeat = getpass.getpass('Repeat backup password: ')
    if not key_id or not app_key or len(password) < 20 or password != repeat:
        raise ValueError('Keys must be present; passwords must match and contain at least 20 characters')
    CONFIG.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    CONFIG.parent.chmod(0o700)
    atomic_json(CONFIG, {'home': str(Path.home()),
                        'state': str(Path.home() / '.local/state/restic-offsite'),
                        'machine_id': Path('/etc/machine-id').read_text().strip(),
                        'host': socket.gethostname() + '-' + Path('/etc/machine-id').read_text().strip()[:12],
                        'restic': restic_binary,
                        'sqlite_timeout': 600, 'restic_timeout': 36000,
                        'repository': f's3:https://{endpoint}/{bucket}',
                        'password': password, 'access_key': key_id, 'secret_key': app_key})
    print('Private configuration saved. Credentials were not placed in shell history.')


def load_config():
    if stat.S_IMODE(CONFIG.stat().st_mode) & 0o077:
        raise RuntimeError('Configuration must be readable only by its owner: chmod 600 ' + str(CONFIG))
    config = json.loads(CONFIG.read_text())
    if config['machine_id'] != Path('/etc/machine-id').read_text().strip():
        raise RuntimeError('Configuration belongs to another machine; configure this machine separately')
    return config


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['configure', 'init', 'backup', 'verify', 'status', 'restic'])
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'configure':
        configure()
        return
    config = load_config()
    if args.command == 'init':
        restic(config, ['init'])
    elif args.command == 'backup':
        run_backup(config)
    elif args.command == 'verify':
        verify(config)
    elif args.command == 'status':
        print((Path(config['state']) / 'status.json').read_text())
    else:
        restic(config, args.args)


if __name__ == '__main__':
    try:
        main()
    except BlockingIOError:
        print('Backup or verification already running; this operation did not change its state.', file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        # Do not dump config or subprocess output; either can contain private data.
        if isinstance(error, subprocess.CalledProcessError):
            detail = f'restic exit code {error.returncode}'
        elif isinstance(error, (RuntimeError, ValueError, OSError)):
            detail = str(error)
        else:
            detail = type(error).__name__
        print(f'Backup operation failed: {detail}. Check status and the run journal.', file=sys.stderr)
        sys.exit(1)
