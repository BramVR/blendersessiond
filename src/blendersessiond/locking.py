"""Cross-platform advisory file locks."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock for the lifetime of the context."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    if os.name != "nt":
        os.chmod(path, 0o600)
    stream = os.fdopen(descriptor, "a+b")
    acquired = False
    try:
        _lock(stream)
        acquired = True
        yield
    finally:
        if acquired:
            _unlock(stream)
        stream.close()


def _lock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.05)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
