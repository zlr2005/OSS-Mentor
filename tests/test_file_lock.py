from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from oss_mentor.file_lock import acquire_lock,release_lock


class FileLockTests(unittest.TestCase):
    def test_other_process_cannot_acquire_until_owner_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'worker.lock'
            owner=acquire_lock(path)
            probe="""from pathlib import Path
import sys
from oss_mentor.file_lock import acquire_lock,release_lock
try: handle=acquire_lock(Path(sys.argv[1]))
except OSError: raise SystemExit(3)
release_lock(handle)
"""
            try:
                blocked=subprocess.run([sys.executable,'-c',probe,str(path)],capture_output=True,timeout=15)
                self.assertEqual(blocked.returncode,3,blocked.stderr.decode(errors='replace'))
            finally:release_lock(owner)
            allowed=subprocess.run([sys.executable,'-c',probe,str(path)],capture_output=True,timeout=15)
            self.assertEqual(allowed.returncode,0,allowed.stderr.decode(errors='replace'))

    def test_release_closes_handle(self):
        with tempfile.TemporaryDirectory() as temporary:
            handle=acquire_lock(Path(temporary)/'worker.lock')
            release_lock(handle)
            self.assertTrue(handle.closed)
