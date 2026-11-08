"""Nonblocking file locks shared by Windows and POSIX local workers."""
import os
from pathlib import Path


def acquire_lock(path: Path):
    handle=Path(path).open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            if Path(path).stat().st_size==0:
                handle.write(b'0');handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise


def release_lock(handle):
    try:
        if os.name=='nt':
            import msvcrt
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    finally:
        handle.close()
