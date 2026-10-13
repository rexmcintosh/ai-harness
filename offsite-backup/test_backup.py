import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import contextlib
import io
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('backup', Path(__file__).with_name('backup.py'))
backup = importlib.util.module_from_spec(spec)
if spec.loader and Path(spec.origin).exists():
    spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.state = self.home / '.local/state/restic-offsite'
        self.state.mkdir(parents=True)
        self.config = {'home': str(self.home), 'state': str(self.state),
                       'repository': str(self.base / 'repo'), 'password': 'test-only-secret',
                       'host': 'test-vm-a', 'access_key': '', 'secret_key': '', 'restic': '/usr/bin/restic'}

    def make_db(self):
        db = self.home / r'projects/app/state[work]?*\data.sqlite'
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE notes (text TEXT)')
        conn.execute("INSERT INTO notes VALUES ('survives live WAL')")
        conn.commit()
        return db

    # Copying only the main file would lose committed WAL rows.
    def test_sqlite_online_snapshot_includes_committed_wal(self):
        self.make_db()
        backup.snapshot_databases(self.home, self.state / 'sqlite')
        with sqlite3.connect(self.state / r'sqlite/projects/app/state[work]?*\data.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT text FROM notes').fetchall(),
                             [('survives live WAL',)])

    def test_failed_cloud_command_keeps_error_without_credentials(self):
        executable = self.base / 'cloud-error'
        executable.write_text('#!/bin/sh\nprintf "storage cap exceeded: %s %s\\n" "$AWS_SECRET_ACCESS_KEY" "$RESTIC_PASSWORD" >&2\nexit 1\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        self.config['secret_key'] = 'test-cloud-secret'
        with self.assertRaises(subprocess.CalledProcessError):
            backup.restic(self.config, ['backup', str(self.home)], capture=True)
        error = json.loads((self.state / 'last-error.json').read_text())
        self.assertIn('storage cap exceeded', error['stderr'])
        self.assertNotIn('test-cloud-secret', error['stderr'])
        self.assertNotIn('test-only-secret', error['stderr'])
        self.assertEqual(error['exit_code'], 1)
        self.assertEqual((self.state / 'last-error.json').stat().st_mode & 0o777, 0o600)

    def test_diagnostic_write_failure_preserves_original_command_error(self):
        executable = self.base / 'command-error'
        executable.write_text('#!/bin/sh\nprintf "original failure\\n" >&2\nexit 17\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        # A real filesystem failure during atomic replacement.
        (self.state / 'last-error.json').mkdir()
        for args in (['backup'], []):
            with self.subTest(args=args):
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    backup.restic(self.config, args, capture=True)
                self.assertEqual(caught.exception.returncode, 17)
                self.assertIn(b'original failure', caught.exception.stderr)

    def test_excluded_dependency_database_is_not_staged(self):
        db = self.home / '.local/share/pipx/venvs/tool/data.sqlite'
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE derived (value TEXT)')
        self.assertEqual(backup.snapshot_databases(self.home, self.state / 'sqlite'), [])
        self.assertFalse((self.state / 'sqlite/.local/share/pipx').exists())

    def test_database_timeout_stops_incomplete_copy(self):
        self.make_db()
        with self.assertRaises(TimeoutError):
            backup.snapshot_databases(self.home, self.state / 'sqlite', timeout=0)

    def test_disappearing_transient_file_does_not_drop_database(self):
        self.make_db()
        transient = self.home / 'temporary.json'
        transient.write_text('temporary')
        original_open = Path.open
        def raced_open(path, *args, **kwargs):
            if path == transient:
                raise FileNotFoundError('file was removed during discovery')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', raced_open):
            manifest = backup.snapshot_databases(self.home, self.state / 'sqlite')
        self.assertEqual(manifest, [r'projects/app/state[work]?*\data.sqlite'])
        with sqlite3.connect(self.state / r'sqlite/projects/app/state[work]?*\data.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT text FROM notes').fetchone()[0], 'survives live WAL')

    # Ignoring live WAL size would overcommit disk space despite a small main DB.
    def test_disk_reserve_includes_wal(self):
        self.make_db()
        with patch.object(backup.shutil, 'disk_usage', return_value=SimpleNamespace(free=536879104)):
            with self.assertRaisesRegex(RuntimeError, 'Insufficient disk space'):
                backup.snapshot_databases(self.home, self.state / 'sqlite')
        self.assertFalse((self.state / 'sqlite').exists())

    # Live databases can grow after the one-time estimate; copying must stop before the disk fills.
    def test_disk_reserve_rechecked_during_copy(self):
        self.make_db()
        free = iter([10 ** 12])
        def usage(path):
            return SimpleNamespace(free=next(free, backup.DISK_RESERVE - 1))
        with patch.object(backup.shutil, 'disk_usage', side_effect=usage):
            with self.assertRaisesRegex(RuntimeError, 'fell below the reserve'):
                backup.snapshot_databases(self.home, self.state / 'sqlite')

    # Excluding all hidden files or .git would lose secrets and unpushed work.
    def test_real_encrypted_backup_and_restore_preserves_work_excludes_dependencies(self):
        self.make_db()
        for name, data in {'.env': 'private setting', 'projects/app/.git/unpushed': 'local commit',
                           'projects/app/node_modules/lib/index.js': 'rebuild me',
                           'projects/app/asset.png': 'valuable image',
                           '.config/restic-offsite/config.json': 'machine-bound credentials'}.items():
            p = self.home / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        backup.restic(self.config, ['init'])
        backup.run_backup(self.config)
        backup.verify(self.config)
        target = self.base / 'restore'
        backup.restic(self.config, ['restore', 'latest', '--target', str(target)])
        restored = target / str(self.home).lstrip('/')
        self.assertEqual((restored / '.env').read_text(), 'private setting')
        self.assertEqual((restored / 'projects/app/.git/unpushed').read_text(), 'local commit')
        self.assertEqual((restored / 'projects/app/asset.png').read_text(), 'valuable image')
        self.assertFalse((restored / 'projects/app/node_modules').exists())
        self.assertFalse((restored / '.config/restic-offsite').exists())
        self.assertFalse((restored / r'projects/app/state[work]?*\data.sqlite').exists())
        self.assertFalse((restored / r'projects/app/state[work]?*\data.sqlite-wal').exists())
        with sqlite3.connect(restored / r'.local/state/restic-offsite/sqlite/projects/app/state[work]?*\data.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT text FROM notes').fetchone()[0], 'survives live WAL')
        self.assertTrue(json.loads((self.state / 'status.json').read_text())['verified'])

    # Exit 3 is a partial backup; it must never trigger retention or record success.
    def test_partial_backup_fails_without_retention_or_success(self):
        backup.restic(self.config, ['init'])
        backup.run_backup(self.config)
        previous = (self.state / 'last-success.json').read_bytes()
        executable = self.base / 'partial-restic'
        executable.write_text('#!/bin/sh\ncase "$*" in *backup*) exit 3;; *) exit 99;; esac\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        with self.assertRaises(subprocess.CalledProcessError) as error:
            backup.run_backup(self.config)
        self.assertEqual(error.exception.returncode, 3)
        self.assertEqual((self.state / 'last-success.json').read_bytes(), previous)
        self.assertEqual(json.loads((self.state / 'status.json').read_text())['status'], 'failed')
        self.assertFalse((self.state / 'sqlite').exists())
        with self.assertRaisesRegex(RuntimeError, 'No complete backup'):
            backup.verify(self.config)

    def test_cleanup_failure_preserves_original_backup_error(self):
        executable = self.base / 'backup-error'
        executable.write_text('#!/bin/sh\nexit 3\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        with patch.object(backup.shutil, 'rmtree', side_effect=PermissionError('cleanup blocked')):
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                backup.run_backup(self.config)
        self.assertEqual(caught.exception.returncode, 3)
        self.assertEqual(json.loads((self.state / 'status.json').read_text())['restic_exit_code'], 3)

    def test_verification_metadata_cache_is_removed_after_success_or_failure(self):
        executable = self.base / 'cache-observer'
        record = self.base / 'observed-cache'
        fail = self.base / 'fail-check'
        executable.write_text('#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\n'
            + f"record=Path({str(record)!r});fail=Path({str(fail)!r})\n"
            + "if 'check' in args:\n"
            + " if '--cache-dir' not in args:sys.exit(82)\n"
            + " flags=[a for a in args if a=='--no-cache' or a.startswith('--no-cache=')]\n"
            + " if flags[-1]!='--no-cache=false' or '--with-cache' not in args:sys.exit(82)\n"
            + " cache=Path(args[args.index('--cache-dir')+1]);record.write_text(str(cache))\n"
            + " (cache/'scratch-metadata').write_text('temporary')\n"
            + " if fail.exists():sys.exit(19)\n"
            + "os.execv('/usr/bin/restic',['restic',*args])\n")
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        backup.restic(self.config, ['init'])
        backup.run_backup(self.config)
        self.assertFalse(Path(record.read_text()).exists())
        fail.touch()
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            backup.run_backup(self.config)
        self.assertEqual(caught.exception.returncode, 19)
        self.assertFalse(Path(record.read_text()).exists())

    def test_retention_failure_preserves_verified_backup(self):
        backup.restic(self.config, ['init'])
        executable = self.base / 'retention-failure'
        executable.write_text('#!/bin/sh\ncase \"$*\" in *forget*) exit 19;; *) exec /usr/bin/restic \"$@\";; esac\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        with self.assertRaises(subprocess.CalledProcessError):
            backup.run_backup(self.config)
        status = json.loads((self.state / 'status.json').read_text())
        self.assertTrue(status['verified'])
        previous = json.loads((self.state / 'last-success.json').read_text())
        self.assertEqual(previous['snapshot_id'], status['snapshot_id'])
        self.assertEqual(status['failed_stage'], 'retention')
        self.assertEqual(status['restic_exit_code'], 19)

    # Buffered per-second status lines from a multi-hour upload can exhaust memory.
    def test_backup_requests_summary_only_json(self):
        executable = self.base / 'quiet-observer'
        executable.write_text('#!/bin/sh\ncase "$*" in *" backup "*) case " $* " in *" --quiet "*) ;; *) exit 82;; esac;; esac\n'
                              'exec /usr/bin/restic "$@"\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        backup.restic(self.config, ['init'])
        backup.run_backup(self.config)
        status = json.loads((self.state / 'status.json').read_text())
        self.assertEqual(status['status'], 'ok')
        self.assertTrue(status['snapshot_id'])

    # A run that cannot remove its staging must not report itself healthy.
    def test_post_success_cleanup_failure_is_reported_failed(self):
        backup.restic(self.config, ['init'])
        stage = self.state / 'sqlite'
        real_rmtree = backup.shutil.rmtree
        def blocked(path, *args, **kwargs):
            if Path(path) == stage:
                raise PermissionError('cleanup blocked')
            return real_rmtree(path, *args, **kwargs)
        with patch.object(backup.shutil, 'rmtree', side_effect=blocked):
            with self.assertRaises(PermissionError):
                backup.run_backup(self.config)
        status = json.loads((self.state / 'status.json').read_text())
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(status['failed_stage'], 'cleanup')
        self.assertTrue(status['verified'])
        self.assertEqual(json.loads((self.state / 'last-success.json').read_text())['snapshot_id'],
                         status['snapshot_id'])

    # Per-file error lines on a large tree must not be held whole in memory or leak a cut credential.
    def test_stderr_flood_keeps_bounded_redacted_tail(self):
        executable = self.base / 'stderr-flood'
        executable.write_text('#!/usr/bin/python3\nimport os,sys\nkey=os.environ["AWS_SECRET_ACCESS_KEY"]\n'
                              'for i in range(200000):sys.stderr.write(f"error {i} {key} unreadable\\n")\n'
                              'sys.stderr.write("final cause\\n");sys.exit(1)\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        self.config['secret_key'] = 'test-cloud-secret'
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            backup.restic(self.config, ['backup', str(self.home)], capture=True)
        self.assertLessEqual(len(caught.exception.stderr), backup.STDERR_TAIL)
        error = json.loads((self.state / 'last-error.json').read_text())
        self.assertTrue(error['stderr_truncated'])
        self.assertLessEqual(len(error['stderr']), backup.STDERR_TAIL)
        self.assertTrue(error['stderr'].endswith('final cause\n'))
        self.assertTrue(error['stderr'].startswith('error '))
        self.assertNotIn('secret', error['stderr'])
        self.assertNotIn(b'secret', caught.exception.stderr)
        self.assertEqual(list(self.state.glob('tmp*')), [])

    def run_cli(self, *args, wait=True):
        config_dir = self.home / '.config/restic-offsite'
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = dict(self.config, machine_id=Path('/etc/machine-id').read_text().strip())
        (config_dir / 'config.json').write_text(json.dumps(config))
        (config_dir / 'config.json').chmod(0o600)
        process = subprocess.Popen([sys.executable, str(Path(backup.__file__)), *args],
                                   env=dict(os.environ, HOME=str(self.home)),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if wait:
            process.wait(timeout=120)
        return process

    # systemd's TimeoutStartSec or a stop sends SIGTERM; status must not stay "running".
    def test_stop_signal_records_failure_and_removes_staging(self):
        self.make_db()
        marker = self.base / 'backup-started'
        executable = self.base / 'slow-restic'
        executable.write_text(f'#!/bin/sh\ncase "$*" in *" backup "*) touch {marker}; exec sleep 60;; esac\n'
                              'exec /usr/bin/restic "$@"\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        process = self.run_cli('backup', wait=False)
        deadline = time.monotonic() + 60
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(marker.exists())
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 1)
        self.assertIn(b'stopped by SIGTERM', stderr)
        status = json.loads((self.state / 'status.json').read_text())
        self.assertEqual((status['status'], status['failed_stage'], status['error_type']),
                         ('failed', 'backup', 'Terminated'))
        self.assertFalse((self.state / 'sqlite').exists())

    # A kill after verification must not leave a healthy-looking status for an unfinished run.
    def test_kill_during_retention_leaves_unfinished_status(self):
        backup.restic(self.config, ['init'])
        executable = self.base / 'kill-in-retention'
        executable.write_text('#!/bin/sh\ncase "$*" in *forget*) kill -9 $PPID; exit 0;; esac\n'
                              'exec /usr/bin/restic "$@"\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        process = self.run_cli('backup')
        self.assertEqual(process.returncode, -signal.SIGKILL)
        status = json.loads((self.state / 'status.json').read_text())
        self.assertEqual((status['status'], status['stage'], status['verified']), ('running', 'retention', True))
        self.assertEqual(json.loads((self.state / 'last-success.json').read_text())['snapshot_id'],
                         status['snapshot_id'])
        self.assertEqual(json.loads(self.run_cli('status').stdout.read())['status'], 'interrupted')
        backup.atomic_json(self.state / 'status.json', status)
        self.config['restic'] = '/usr/bin/restic'
        backup.run_backup(self.config)
        rerun = json.loads((self.state / 'status.json').read_text())
        self.assertEqual(rerun['status'], 'ok')
        self.assertEqual(rerun['previous_run_interrupted'],
                         {'started_at': status['started_at'], 'stage': 'retention'})

    # SIGKILL or OOM cannot be caught; status must still show the run did not finish.
    def test_status_reports_killed_run_as_interrupted(self):
        backup.atomic_json(self.state / 'status.json', {'status': 'running', 'stage': 'backup'})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            backup.show_status(self.config)
        self.assertEqual(json.loads(out.getvalue())['status'], 'interrupted')
        self.assertEqual(json.loads((self.state / 'status.json').read_text())['status'], 'interrupted')
        backup.atomic_json(self.state / 'status.json', {'status': 'running', 'stage': 'backup'})
        import fcntl
        with (self.state / 'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                backup.show_status(self.config)
        self.assertEqual(json.loads(out.getvalue())['status'], 'running')

    # Unexpected bulk stdout (for example, lost --quiet) must fail rather than fill memory.
    def test_oversized_stdout_fails(self):
        executable = self.base / 'stdout-flood'
        executable.write_text('#!/usr/bin/python3\nimport sys\nsys.stdout.write("x" * (3 * 1024 * 1024))\n')
        executable.chmod(0o700)
        self.config['restic'] = str(executable)
        with self.assertRaisesRegex(RuntimeError, 'more output than expected'):
            backup.restic(self.config, ['backup'], capture=True)

    def test_status_reports_missing_or_corrupt_file(self):
        for content in (None, '{not json', '[]'):
            with self.subTest(content=content):
                path = self.state / 'status.json'
                path.unlink(missing_ok=True)
                if content is not None:
                    path.write_text(content)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    backup.show_status(self.config)
                self.assertEqual(json.loads(out.getvalue())['status'], 'unknown')

    # Two runs sharing staging would overwrite the only consistent DB copy.
    def test_overlap_refused(self):
        import fcntl
        with (self.state / 'run.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                backup.run_backup(self.config)
            with self.assertRaises(BlockingIOError):
                backup.verify(self.config)


if __name__ == '__main__':
    unittest.main()
