import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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

    # Excluding all hidden files or .git would lose secrets and unpushed work.
    def test_real_encrypted_backup_and_restore_preserves_work_excludes_dependencies(self):
        self.make_db()
        for name, data in {'.env': 'private setting', 'projects/app/.git/unpushed': 'local commit',
                           'projects/app/node_modules/lib/index.js': 'rebuild me',
                           'projects/app/asset.png': 'valuable image'}.items():
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
