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
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import tempfile
from zoneinfo import ZoneInfo
import socket

SKIP_DIRS = {'node_modules', '.venv', 'venv', '__pycache__', '.cache', '.npm', '.rustup'}
# The runner's own credentials are machine-bound and kept in the recovery record instead.
EXCLUDED_RELATIVE = ('.local/share/pipx/venvs', '.cargo/registry', '.config/restic-offsite')
CONFIG = Path.home() / '.config/restic-offsite/config.json'
STDERR_TAIL = 32768
STDOUT_LIMIT = 1024 * 1024
TERMINAL = ('ok', 'failed', 'interrupted')
DISK_RESERVE = 512 * 1024 * 1024


class Terminated(RuntimeError):
    """A stop signal arrived; raised so the run records a failed status."""


def _terminate(signum, frame):
    # Let cleanup and status writes finish after the first stop request.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    raise Terminated('stopped by ' + signal.Signals(signum).name)


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
    command = [config.get('restic', '/usr/bin/restic'), '--no-cache', *args]
    timeout = config.get('restic_timeout', 10 * 60 * 60)
    if '_deadline' in config:
        # Stay inside the run budget so the runner, not systemd, records a timeout.
        timeout = min(timeout, max(1, config['_deadline'] - time.monotonic()))
    if not capture:
        return subprocess.run(command, env=env, check=True, timeout=timeout)
    # Drain both pipes concurrently; keep only a bounded stderr tail, never spooled to disk.
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, tail, cut, overflow = [], bytearray(), [False], [False]
    def drain_stdout():
        # Captured commands print one summary line or one probe; refuse anything larger.
        stdout.append(process.stdout.read(STDOUT_LIMIT + 1))
        while process.stdout.read1(65536):
            overflow[0] = True
        overflow[0] = overflow[0] or len(stdout[0]) > STDOUT_LIMIT
    def drain_stderr():
        for chunk in iter(lambda: process.stderr.read1(65536), b''):
            tail.extend(chunk)
            if len(tail) > STDERR_TAIL:
                del tail[:-STDERR_TAIL]
                cut[0] = True
    readers = [threading.Thread(target=f, daemon=True) for f in (drain_stdout, drain_stderr)]
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except BaseException:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=5)
        raise
    for reader in readers:
        reader.join()
    process.stdout.close()
    process.stderr.close()
    if overflow[0] and returncode == 0:
        raise RuntimeError(f'restic {args[0]} printed more output than expected')
    result = subprocess.CompletedProcess(command, returncode, stdout[0] if stdout else b'', None)
    if returncode == 0:
        return result
    tail, start = bytes(tail), cut[0]
    if start:
        # Drop the partial first line so a cut-off credential cannot escape redaction.
        tail = tail.partition(b'\n')[2]
    # Keep diagnostics, never dump restored stdout or credentials.
    detail = tail.decode('utf-8', errors='replace')
    for field in ('password', 'secret_key', 'access_key'):
        if config.get(field):
            detail = detail.replace(config[field], '[redacted]')
    try:
        atomic_json(Path(config['state']) / 'last-error.json', {
            'operation': args[0] if args else 'unknown', 'exit_code': returncode,
            'stderr': detail[-STDERR_TAIL:], 'stderr_truncated': bool(start),
            'at': datetime.now(timezone.utc).isoformat()})
    except Exception:
        # Diagnostics are best effort; preserve the actual command failure.
        print('Could not save private command diagnostics.', file=sys.stderr)
    raise subprocess.CalledProcessError(returncode, command, result.stdout,
                                        detail[-STDERR_TAIL:].encode())



def snapshot_databases(home, stage, timeout=600, discovered=None):
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
                        if discovered is not None:
                            discovered.append(path.relative_to(home))
            except FileNotFoundError:
                # A removed transient file contains nothing that can still be backed up.
                continue
    # Keep space for the OS and for DB growth during online backup.
    def size(path):
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0
    required = sum(size(p) + size(Path(str(p) + '-wal')) for p in databases)
    if shutil.disk_usage(home).free < required * 2 + DISK_RESERVE:
        raise RuntimeError('Insufficient disk space for consistent database copies')
    stage.mkdir(parents=True, mode=0o700)
    manifest = []
    for source in databases:
        if shutil.disk_usage(stage).free < DISK_RESERVE:
            raise RuntimeError('Disk space fell below the reserve during database copies')
        if not source.exists():
            # Deleted since discovery: nothing left to copy, and it is not excluded raw.
            continue
        relative = source.relative_to(home)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        deadline = time.monotonic() + timeout
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError('Database snapshot timed out')
            # Databases can grow after the estimate; stop while the reserve still remains.
            if shutil.disk_usage(stage).free < DISK_RESERVE:
                raise RuntimeError('Disk space fell below the reserve during database copies')
        try:
            source_db = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30)
        except sqlite3.OperationalError:
            if not source.exists():
                # Deleted after the existence check; still nothing left to copy.
                continue
            raise
        with closing(source_db) as src:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst, pages=256, progress=progress)
                if dst.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise RuntimeError('Database copy failed integrity check')
        target.chmod(0o600)
        manifest.append(str(relative))
    atomic_json(stage / 'manifest.json', manifest)
    return manifest


def run_backup(config):
    # One budget for the whole run, below the service's TimeoutStartSec of 12 hours.
    config = dict(config, _deadline=time.monotonic() + config.get('run_timeout', 11 * 60 * 60))
    home, state = Path(config['home']), Path(config['state'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with run_lock(state):
        stage = state / 'sqlite'
        status = {'status': 'running', 'stage': 'sqlite', 'verified': False,
                  'started_at': datetime.now(timezone.utc).isoformat()}
        try:
            previous = json.loads((state / 'status.json').read_text())
        except FileNotFoundError:
            previous = {}
        except (OSError, ValueError):
            previous = {'status': 'unknown'}
        if not isinstance(previous, dict):
            previous = {'status': 'unknown'}
        if previous.get('status', 'ok') not in ('ok', 'failed'):
            # The lock is ours, so that run was killed before it recorded a result.
            status['previous_run_interrupted'] = {k: previous.get(k) for k in ('started_at', 'stage')}
        atomic_json(state / 'status.json', status)
        try:
            # Only this runner owns this exact staging directory and its verify caches.
            if stage.exists():
                shutil.rmtree(stage)
            for stale_cache in state.glob('verify-cache-*'):
                try:
                    if stale_cache.is_dir() and not stale_cache.is_symlink():
                        shutil.rmtree(stale_cache)
                    else:
                        stale_cache.unlink()
                except OSError:
                    # Excluded from backups, so a leftover cache only costs local space.
                    print(f'Could not remove stale {stale_cache.name}; continuing.', file=sys.stderr)
            discovered = []
            manifest = snapshot_databases(home, stage, timeout=config.get('sqlite_timeout', 600),
                                          discovered=discovered)
            probe = secrets.token_hex(32)
            (stage / 'recovery-probe.txt').write_text(probe)
            # --quiet keeps JSON output to the final summary; status lines would
            # otherwise accumulate in memory for the whole multi-hour upload.
            args = ['backup', str(home), '--host', config['host'], '--tag', 'bebop-offsite',
                    '--json', '--quiet']
            for directory in sorted(SKIP_DIRS):
                args += ['--exclude', directory]
            # Exclude every discovered live SQLite file and journal, even one deleted and
            # recreated during copying; restore the consistent copies instead.
            for relative in discovered:
                for suffix in ('', '-wal', '-shm', '-journal'):
                    path = str(home / relative) + suffix
                    pattern = ''.join('\\' + c if c in '\\*?[]' else c for c in path)
                    args += ['--exclude', pattern]
            for relative in EXCLUDED_RELATIVE:
                args += ['--exclude', str(home / relative)]
            # A verify cache left by a killed run is scratch metadata, not data to keep.
            escaped_state = ''.join('\\' + c if c in '\\*?[]' else c for c in str(state))
            args += ['--exclude', escaped_state + '/verify-cache-*']
            status['stage'] = 'backup'
            atomic_json(state / 'status.json', status)
            result = restic(config, args, capture=True)
            events = (json.loads(line) for line in reversed(result.stdout.splitlines()) if line)
            summary = next(e for e in events if e.get('message_type') == 'summary')
            # The run stays "running" until cleanup; only last-success records a verified snapshot.
            status.update(snapshot_id=summary['snapshot_id'], probe=probe,
                          database_count=len(manifest), stage='verification',
                          uploaded_at=datetime.now(timezone.utc).isoformat())
            atomic_json(state / 'status.json', status)
            verified = _verify_locked(config, dict(status))
            atomic_json(state / 'last-success.json', verified)
            status.update(verified=True, verified_at=verified['verified_at'], stage='retention')
            atomic_json(state / 'status.json', status)
            retention = ['forget', '--host', config['host'], '--tag', 'bebop-offsite',
                         '--group-by', 'host,tags', '--keep-daily', '30', '--keep-monthly', '6']
            # Weekly pruning frees old data; retention itself runs after each good backup.
            if datetime.now(ZoneInfo('Europe/Lisbon')).weekday() == 6:
                retention += ['--prune']
            restic(config, retention)
            # Report success only after this run's staging is gone.
            status['stage'] = 'cleanup'
            atomic_json(state / 'status.json', status)
            shutil.rmtree(stage)
            status.update(status='ok', stage='complete', finished_at=datetime.now(timezone.utc).isoformat())
            atomic_json(state / 'status.json', status)
            print('Backup and recovery check passed.', flush=True)
        except Exception as error:
            status.update(status='failed', failed_stage=status['stage'],
                          error_type=type(error).__name__,
                          restic_exit_code=getattr(error, 'returncode', None),
                          failed_at=datetime.now(timezone.utc).isoformat())
            status['finished_at'] = status['failed_at']
            atomic_json(state / 'status.json', status)
            raise
        finally:
            if stage.exists():
                already_failed = sys.exc_info()[0] is not None
                try:
                    shutil.rmtree(stage)
                except OSError:
                    if not already_failed:
                        raise
                    print('Could not remove database staging; original failure preserved.', file=sys.stderr)


def verify(config):
    state = Path(config['state'])
    with run_lock(state):
        try:
            current = json.loads((state / 'status.json').read_text()).get('status')
        except (OSError, ValueError, AttributeError):
            current = None
        if current == 'ok':
            return _verify_locked(config)
        # A later failed run does not erase an earlier verified snapshot; check that one
        # without overwriting the failed current state.
        try:
            last = json.loads((state / 'last-success.json').read_text())
        except (OSError, ValueError):
            last = None
        if not isinstance(last, dict) or not last.get('verified'):
            raise RuntimeError('No complete backup available to verify')
        verified = _verify_locked(config, dict(last))
        atomic_json(state / 'last-success.json', verified)
        print(f"Latest run is {current or 'unknown'}; verified last successful snapshot "
              f"{verified['snapshot_id'][:8]}.", flush=True)
        return verified


def _verify_locked(config, status=None):
    state = Path(config['state'])
    in_run = status is not None
    if not in_run:
        status = json.loads((state / 'status.json').read_text())
        if status['status'] != 'ok':
            raise RuntimeError('No complete backup available to verify')
    # Every run checks repository structure and restores an exact snapshot's probe.
    # A fresh, temporary cache avoids fetching the same remote metadata pack
    # for every tree. It never carries previously cached data into the check.
    with tempfile.TemporaryDirectory(prefix='verify-cache-', dir=state) as cache:
        restic(config, ['check', '--no-cache=false', '--cache-dir', cache, '--with-cache'])
    probe_path = str(state / 'sqlite/recovery-probe.txt')
    actual = restic(config, ['dump', status['snapshot_id'], probe_path], capture=True).stdout.decode()
    if actual != status['probe']:
        raise RuntimeError('Restored recovery probe does not match')
    status.update(status='ok', verified=True, verified_at=datetime.now(timezone.utc).isoformat())
    if not in_run:
        atomic_json(state / 'status.json', status)
    return status


def show_status(config):
    state = Path(config['state'])
    try:
        status = json.loads((state / 'status.json').read_text())
        if not isinstance(status, dict):
            raise ValueError('status is not an object')
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'unknown', 'note': f'Status file unreadable ({type(error).__name__}); '
                          'check the journal and last-success.json.'}, indent=2))
        return
    if status.get('status') not in TERMINAL:
        try:
            with run_lock(state):
                # Nothing holds the lock, so the run was killed before it could record a result.
                status.update(status='interrupted', note='Run stopped without a result; check the journal.',
                              interrupted_detected_at=datetime.now(timezone.utc).isoformat())
                atomic_json(state / 'status.json', status)
        except BlockingIOError:
            pass
    print(json.dumps(status, indent=2))


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
    for path, mode in ((CONFIG.parent, '700'), (CONFIG, '600')):
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError(f'Configuration must be private to its owner: chmod {mode} {path}')
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
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _terminate)
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
        show_status(config)
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
