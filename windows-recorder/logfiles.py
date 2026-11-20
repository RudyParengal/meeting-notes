"""Log files that cannot grow forever: 5 MB each, the 3 newest old copies kept.

notewell.log, recorder.log and screen.log all go through here. When one
reaches 5 MB it becomes name.log.1 (the old .1 becomes .2, .2 becomes .3, and
the old .3 is dropped) and a fresh file starts.

- `handler(path)` for Python logging (recorder.log, screen.log).
- `Stream(path)` for sys.stdout / sys.stderr when the app has no console
  (notewell.log).

If Windows will not let a file be renamed (another program holding it open),
logging carries on in the same file and tries again a minute later, rather
than failing on every line.
"""
import logging.handlers
import os
import threading
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
KEEP = 3
RETRY_SECONDS = 60


def shift(path, keep=KEEP):
    """name.log -> name.log.1, .1 -> .2 ... the oldest copy is overwritten."""
    path = str(path)
    for i in range(keep - 1, 0, -1):
        if os.path.exists(f"{path}.{i}"):
            os.replace(f"{path}.{i}", f"{path}.{i + 1}")
    if os.path.exists(path):
        os.replace(path, f"{path}.1")


class _Handler(logging.handlers.RotatingFileHandler):
    _next_try = 0.0

    def shouldRollover(self, record):
        return time.monotonic() >= self._next_try and super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            self._next_try = time.monotonic() + RETRY_SECONDS
            if self.stream is None:
                self.stream = self._open()


def handler(path, max_bytes=MAX_BYTES, keep=KEEP):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return _Handler(path, maxBytes=max_bytes, backupCount=keep, encoding="utf-8")


class Stream:
    """A stand-in for sys.stdout / sys.stderr that writes to a rolling file."""

    def __init__(self, path, max_bytes=MAX_BYTES, keep=KEEP):
        self.path, self.max_bytes, self.keep = Path(path), max_bytes, keep
        self._lock = threading.RLock()
        self._next_try = 0.0
        self._open()

    def _open(self):
        self._f = open(self.path, "a", encoding="utf-8", buffering=1)
        self._size = self.path.stat().st_size

    def write(self, text):
        with self._lock:
            n = self._f.write(text)
            self._size += len(text)
            if self._size >= self.max_bytes and time.monotonic() >= self._next_try:
                self._roll()
            return n

    def _roll(self):
        self._f.close()
        try:
            shift(self.path, self.keep)
        except OSError:
            self._next_try = time.monotonic() + RETRY_SECONDS
        self._open()

    def flush(self):
        with self._lock:
            self._f.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):          # fileno, encoding, closed ...
        return getattr(self._f, name)
