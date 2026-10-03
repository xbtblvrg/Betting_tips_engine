import fcntl
import gzip
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from betting_engine.log_rotation import rotate_log
from betting_engine.supervisor import compact_summary


class RotationTests(unittest.TestCase):
    def test_same_inode_and_append_descriptor_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()/'supervisor.log'
            original = b'first error\n' * 1000
            path.write_bytes(original)
            inode = path.stat().st_ino
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            try:
                result = rotate_log(path, fd=fd, max_bytes=10)
                os.write(fd, b'next\n')
            finally: os.close(fd)
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(path.read_bytes(), b'next\n')
            self.assertEqual(gzip.decompress((path.parent/result['archive']).read_bytes()), original)

    def test_retention_only_owns_exact_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()/'supervisor-actions.jsonl'
            unrelated = path.parent/'supervisor-actions.jsonl.evidence.gz'
            unrelated.write_bytes(b'preserve')
            for i in range(5):
                path.write_bytes(str(i).encode()*20)
                rotate_log(path, max_bytes=10, keep=2)
            archives = [p for p in path.parent.glob('*.gz') if p != unrelated]
            self.assertEqual(len(archives), 2)
            self.assertEqual({gzip.decompress(p.read_bytes()) for p in archives}, {b'3'*20, b'4'*20})
            self.assertEqual(unrelated.read_bytes(), b'preserve')

    def test_archive_failure_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()/'supervisor.log'; path.write_bytes(b'error'*20)
            with patch('betting_engine.log_rotation.os.fsync', side_effect=OSError('disk full')):
                with self.assertRaises(OSError): rotate_log(path, max_bytes=1)
            self.assertEqual(path.read_bytes(), b'error'*20)
            self.assertEqual(list(path.parent.glob('*.gz')), [])

    def test_skip_nonappend_and_reject_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()/'supervisor.log'; path.write_bytes(b'original')
            fd = os.open(path, os.O_WRONLY)
            try: self.assertEqual(rotate_log(path, fd=fd, max_bytes=1)['state'], 'skipped_not_append')
            finally: os.close(fd)
            link = path.parent/'supervisor-actions.jsonl'; link.symlink_to(path)
            with self.assertRaises(OSError): rotate_log(link, max_bytes=1)
            self.assertEqual(path.read_bytes(), b'original')

    def test_changed_source_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()/'supervisor.log'; path.write_bytes(b'original')
            real_fsync = os.fsync
            def changed(fd):
                real_fsync(fd)
                with path.open('ab') as stream: stream.write(b'concurrent')
            with patch('betting_engine.log_rotation.os.fsync', side_effect=changed):
                with self.assertRaises(ValueError): rotate_log(path, max_bytes=1)
            self.assertEqual(path.read_bytes(), b'originalconcurrent')

    def test_busy_supervisor_never_rotates_or_reads_health(self):
        from betting_engine.supervisor import run_once
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve()
            with (data/'supervisor.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch('betting_engine.log_rotation.rotate_log') as rotate:
                    self.assertEqual(run_once(data), {'busy': True})
                    rotate.assert_not_called()

    def test_summary_does_not_repeat_large_health(self):
        action = {'service': 'engine', 'action': 'restart_failed', 'reason': 'exact error'}
        result = {'state': 'degraded', 'actions': [action], 'services': {'engine':
            {'state': 'restart_failed', 'error': 'exact error', 'progress': {'huge': 'x'*1000000}}},
            'components': {'state': 'degraded', 'checks': ['x'*1000000]}}
        summary = compact_summary(result)
        self.assertLess(len(str(summary)), 2000)
        self.assertEqual(summary['actions'], [action])
        self.assertEqual(summary['services']['engine']['error'], 'exact error')


if __name__ == '__main__': unittest.main()
