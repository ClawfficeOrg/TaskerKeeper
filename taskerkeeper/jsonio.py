"""JSON reads, atomic writes, and the file lock, shared by the CLI and config.

Parallel agents are the point of this tool, so every write goes through here:
a temp file in the same directory followed by an atomic rename, under a lock.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


class LockTimeout(RuntimeError):
    """Another process holds the lock and did not release it in time."""


class FileLock:
    """Cooperative lock on <target>.lock via exclusive create."""

    def __init__(self, target: str | Path, timeout: float = 10.0, poll: float = 0.05):
        self.path = Path(str(target) + ".lock")
        self.timeout = timeout
        self.poll = poll
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"could not acquire {self.path} within {self.timeout:g}s; "
                        f"delete it if no other agent is running"
                    )
                time.sleep(self.poll)

    def __exit__(self, *exc_info) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def read_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(data: dict, path: str | Path) -> None:
    """Write JSON atomically: temp file in the same dir, then rename."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
