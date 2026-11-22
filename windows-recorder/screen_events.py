"""Record a timeline of windows, clicks and typing duration alongside a screen recording.

What is recorded (into <client_id>.events.jsonl):
- Header: format version, UTC start time, Windows version, Notewell version, screen area, video size.
- Window changes: program file name only (never a folder path) and clipped window title.
- Clicks: left or right mouse button press inside the recorded screen, with x/y relative to that screen.
- Typing bursts: start time and duration in milliseconds only (controlled by TYPING_EVENTS).
- Private windows (password managers, banking): a bare {"t": ..., "k": "private"} line only.

What is NEVER recorded:
- Which keys were pressed, how many keys were pressed, or any typed text.
- Any title, program name, click, or typing while a private window is active or under the cursor.
- Clipboard contents, window contents, screenshots, user names, PC names, or folder paths.

Known limit in version 1: typing is inferred from Windows' last-input time moving while the
mouse stays still and no mouse button is down, so turning the mouse wheel without moving the
pointer also counts as typing.
"""
import json
from pathlib import Path

TYPING_EVENTS = True        # False: no typing lines at all (decision D2)
POLL_S = 0.03               # buttons and cursor
WINDOW_POLL_S = 0.25        # foreground window
TYPING_GAP_MS = 1500        # a burst ends after this long with no typing
TITLE_STABLE_MS = 1000      # a changed title is written once it has held this long
TITLE_MAX = 300
APP_MAX = 100
MAX_LINES = 20000
MAX_BYTES = 4 * 1024 * 1024


def clip(text, n):
    """Turn text into a clean single-line string: strip control chars, collapse spaces, cut to n."""
    s = "" if text is None else str(text)
    cleaned = "".join(ch for ch in s if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    return " ".join(cleaned.split())[:n]


class Writer:
    """Append one compact JSON object per line until the line or byte cap is reached."""

    def __init__(self, path, max_lines=None, max_bytes=None):
        self.path = Path(path)
        self.max_lines = MAX_LINES if max_lines is None else max_lines
        self.max_bytes = MAX_BYTES if max_bytes is None else max_bytes
        self.lines = 0
        self.bytes = 0
        self.cut = False
        self._f = open(self.path, "w", encoding="utf-8", newline="\n")

    def line(self, obj):
        if self._f is None or self.cut:
            return False
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(raw.encode("utf-8"))
        if self.lines >= self.max_lines or self.bytes + size > self.max_bytes:
            self.cut = True
            return False
        self._f.write(raw)
        self._f.flush()
        self.lines += 1
        self.bytes += size
        return True

    def end(self, t):
        if self._f is None:
            return
        try:
            obj = {"t": int(t), "k": "end", "lines": self.lines, "cut": bool(self.cut)}
            self._f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._f.flush()
        finally:
            try:
                self._f.close()
            finally:
                self._f = None


class Typing:
    """Group typing polls into bursts with a start time and duration only.

    A burst during which a private window was ever active is dropped whole and never returned.
    """

    def __init__(self):
        self.start = None
        self.last = None
        self.dirty = False

    def tick(self, now, typed, private):
        if not TYPING_EVENTS:
            return
        if private:
            if self.start is not None:
                self.dirty = True
            elif typed:
                self.start = int(now)
                self.last = int(now)
                self.dirty = True
            return
        if typed:
            now_i = int(now)
            if self.start is None:
                self.start = now_i
                self.dirty = False
            self.last = now_i

    def due(self, now):
        if self.start is None:
            return None
        if int(now) - self.last < TYPING_GAP_MS:
            return None
        return self.flush(now)

    def flush(self, now=None):
        if self.start is None:
            return None
        start, last, dirty = self.start, self.last, self.dirty
        self.start = None
        self.last = None
        self.dirty = False
        if dirty:
            return None
        return {"t": start, "k": "typing", "ms": max(0, last - start)}


class Clicks:
    """Detect left or right mouse presses inside the recorded screen."""

    def __init__(self):
        self.prev_left = False
        self.prev_right = False

    def tick(self, now, left, right, x, y, inside, private, ours):
        left_b, right_b = bool(left), bool(right)
        left_down = left_b and not self.prev_left
        right_down = right_b and not self.prev_right
        self.prev_left, self.prev_right = left_b, right_b
        if not (left_down or right_down):
            return None
        if not inside or private or ours:
            return None
        btn = "left" if left_down else "right"
        return {"t": int(now), "k": "click", "b": btn, "x": int(x), "y": int(y)}


class Windows:
    """Track front window switches, title stability, and private windows."""

    def __init__(self):
        self.in_private = False
        self.pid = None
        self.app = None
        self.title = None
        self.pending_title = None
        self.pending_since = None
        self.last_window = None

    def tick(self, now, pid, exe, title, private, ours):
        if ours:
            return None
        if private:
            if self.in_private:
                return None
            self.in_private = True
            self.pid = None
            self.app = None
            self.title = None
            self.pending_title = None
            self.pending_since = None
            self.last_window = None
            return {"t": int(now), "k": "private"}

        exe_name = (exe or "").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        app = clip(exe_name, APP_MAX)
        clean_title = clip(title, TITLE_MAX)
        if not pid and not app and not clean_title:
            return None

        self.in_private = False
        if pid != self.pid or app != self.app:
            self.pid = pid
            self.app = app
            self.title = clean_title
            self.pending_title = None
            self.pending_since = None
            if self.last_window == (app, clean_title):
                return None
            self.last_window = (app, clean_title)
            return {"t": int(now), "k": "window", "app": app, "title": clean_title}

        if clean_title == self.title:
            self.pending_title = None
            self.pending_since = None
            return None

        if clean_title != self.pending_title:
            self.pending_title = clean_title
            self.pending_since = int(now)
            return None

        if int(now) - self.pending_since >= TITLE_STABLE_MS:
            since = self.pending_since
            self.title = clean_title
            self.pending_title = None
            self.pending_since = None
            if self.last_window == (app, clean_title):
                return None
            self.last_window = (app, clean_title)
            return {"t": since, "k": "window", "app": app, "title": clean_title}
        return None
