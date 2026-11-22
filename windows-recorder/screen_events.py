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
import ctypes, ctypes.wintypes as W, json, logging, os, sys, threading, time
from pathlib import Path

log = logging.getLogger("screen")

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
        self.lines = self.bytes = 0
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
        self.start = self.last = None
        self.dirty = False

    def tick(self, now, typed, private):
        if not TYPING_EVENTS:
            return
        if private:
            if self.start is not None:
                self.dirty = True
            elif typed:
                self.start = self.last = int(now)
                self.dirty = True
            return
        if typed:
            now_i = int(now)
            if self.start is None:
                self.start, self.dirty = now_i, False
            self.last = now_i

    def due(self, now):
        if self.start is None or int(now) - self.last < TYPING_GAP_MS:
            return None
        return self.flush(now)

    def flush(self, now=None):
        if self.start is None:
            return None
        start, last, dirty = self.start, self.last, self.dirty
        self.start = self.last = None
        self.dirty = False
        return None if dirty else {"t": start, "k": "typing", "ms": max(0, last - start)}


class Clicks:
    """Detect left or right mouse presses inside the recorded screen."""

    def __init__(self):
        self.prev_left = self.prev_right = False

    def tick(self, now, left, right, x, y, inside, private, ours):
        left_b, right_b = bool(left), bool(right)
        left_down = left_b and not self.prev_left
        right_down = right_b and not self.prev_right
        self.prev_left, self.prev_right = left_b, right_b
        if not (left_down or right_down) or not inside or private or ours:
            return None
        return {"t": int(now), "k": "click", "b": "left" if left_down else "right", "x": int(x), "y": int(y)}


class Windows:
    """Track front window switches, title stability, and private windows."""

    def __init__(self):
        self.in_private = False
        self.pid = self.app = self.title = None
        self.pending_title = self.pending_since = self.last_window = None

    def tick(self, now, pid, exe, title, private, ours):
        if ours:
            return None
        if private:
            if self.in_private:
                return None
            self.in_private = True
            self.pid = self.app = self.title = self.pending_title = self.pending_since = self.last_window = None
            return {"t": int(now), "k": "private"}

        exe_name = (exe or "").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        app, clean_title = clip(exe_name, APP_MAX), clip(title, TITLE_MAX)
        if not pid and not app and not clean_title:
            return None
        self.in_private = False
        if pid != self.pid or app != self.app:
            self.pid, self.app, self.title = pid, app, clean_title
            self.pending_title = self.pending_since = None
            if self.last_window == (app, clean_title):
                return None
            self.last_window = (app, clean_title)
            return {"t": int(now), "k": "window", "app": app, "title": clean_title}
        if clean_title == self.title:
            self.pending_title = self.pending_since = None
            return None
        if clean_title != self.pending_title:
            self.pending_title, self.pending_since = clean_title, int(now)
            return None
        if int(now) - self.pending_since >= TITLE_STABLE_MS:
            since = self.pending_since
            self.title, self.pending_title, self.pending_since = clean_title, None, None
            if self.last_window == (app, clean_title):
                return None
            self.last_window = (app, clean_title)
            return {"t": since, "k": "window", "app": app, "title": clean_title}
        return None


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


def _bind(dll, specs):
    for name, argtypes, restype in specs:
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = argtypes, restype


class WinProbe:
    """Reads what Windows already shows: front window, cursor, mouse buttons, last input time."""

    def __init__(self, own_pid=None):
        self.own_pid = os.getpid() if own_pid is None else own_pid
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _bind(u32, (
            ("GetForegroundWindow", [], W.HWND),
            ("GetWindowTextLengthW", [W.HWND], ctypes.c_int),
            ("GetWindowTextW", [W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int),
            ("GetWindowThreadProcessId", [W.HWND, ctypes.POINTER(W.DWORD)], W.DWORD),
            ("GetCursorPos", [ctypes.POINTER(W.POINT)], W.BOOL),
            ("WindowFromPoint", [W.POINT], W.HWND),
            ("GetAncestor", [W.HWND, W.UINT], W.HWND),
            ("GetLastInputInfo", [ctypes.POINTER(_LASTINPUTINFO)], W.BOOL),
            ("GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
        ))
        _bind(k32, (
            ("OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            ("QueryFullProcessImageNameW", [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)], W.BOOL),
            ("CloseHandle", [W.HANDLE], W.BOOL),
        ))
        self._u32, self._k32 = u32, k32

    def _window_info(self, hwnd):
        if not hwnd:
            return (0, "", "")
        pid = W.DWORD(0)
        self._u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = self._u32.GetWindowTextLengthW(hwnd)
        title = ""
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            self._u32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
        exe = ""
        if pid.value:
            hp = self._k32.OpenProcess(0x1000, False, pid.value)
            if hp:
                try:
                    size, big = W.DWORD(32768), ctypes.create_unicode_buffer(32768)
                    if self._k32.QueryFullProcessImageNameW(hp, 0, big, ctypes.byref(size)):
                        exe = big.value.rsplit("\\", 1)[-1]
                finally:
                    self._k32.CloseHandle(hp)
        return (int(pid.value), exe, title)

    def foreground(self):
        return self._window_info(self._u32.GetForegroundWindow())

    def cursor(self):
        pt = W.POINT(0, 0)
        self._u32.GetCursorPos(ctypes.byref(pt))
        return (int(pt.x), int(pt.y))

    def buttons(self):
        swapped = bool(self._u32.GetSystemMetrics(23))
        v1 = bool(self._u32.GetAsyncKeyState(0x01) & 0x8000)
        v2 = bool(self._u32.GetAsyncKeyState(0x02) & 0x8000)
        return (v2, v1) if swapped else (v1, v2)

    def last_input(self):
        lii = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO), dwTime=0)
        return int(lii.dwTime) if self._u32.GetLastInputInfo(ctypes.byref(lii)) else 0

    def window_at(self, x, y):
        hwnd = self._u32.WindowFromPoint(W.POINT(int(x), int(y)))
        if not hwnd:
            return (0, "", "")
        return self._window_info(self._u32.GetAncestor(hwnd, 2) or hwnd)


def _os_label():
    try:
        v = sys.getwindowsversion()
        return clip(f"Windows {v.major}.{v.minor}.{v.build}", 80)
    except AttributeError:
        return clip(sys.platform, 80)


class Watcher:
    """Polls WinProbe in a daemon thread and writes <cid>.events.jsonl. Never raises from stop()."""

    def __init__(self, path, area, clock, video_size, is_private, probe=None, own_pid=None, paused=None):
        self.path = Path(path)
        self.area = tuple(int(v) for v in area)
        self.clock = clock
        self.video_size = [int(video_size[0]), int(video_size[1])]
        self.is_private, self.probe, self.paused = is_private, probe, paused
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self._windows, self._clicks, self._typing = Windows(), Clicks(), Typing()
        self._writer = self._thread = self._last_input = self._last_cursor = None
        self._stop = threading.Event()
        self._stopped = self._front_private = self._prev_any_btn = self._was_paused = False
        self._last_now = 0

    def _header(self):
        ax, ay, aw, ah = self.area
        return {"v": 1, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "os": _os_label(), "app": clip(os.environ.get("NOTEWELL_VERSION", ""), 32),
                "screen": {"x": ax, "y": ay, "w": aw, "h": ah}, "video": [self.video_size[0], self.video_size[1]]}

    def _ensure_open(self):
        if self._writer is None:
            self._writer = Writer(self.path)
            self._writer.line(self._header())

    def start(self):
        self._ensure_open()
        if self.probe is not None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="screen-events")
            self._thread.start()

    def step(self, now, windows=True):
        if self.probe is None:
            return
        self._ensure_open()
        now_i = max(0, int(now))
        if self.paused is not None and self.paused():
            if not self._was_paused:
                tev = self._typing.flush(now_i)
                if tev is not None:
                    self._writer.line(tev)
                self._was_paused = True
            return
        if self._was_paused:
            self._was_paused = False
            left, right = self.probe.buttons()
            self._clicks.prev_left, self._clicks.prev_right = bool(left), bool(right)
            self._last_input = self.probe.last_input()
            self._last_cursor = self.probe.cursor()
            self._prev_any_btn = bool(left) or bool(right)
        self._last_now = now_i

        if windows:
            pid, exe, title = self.probe.foreground()
            ours = (pid == self.own_pid)
            priv = False if ours else bool(self.is_private(exe, title))
            if not ours:
                self._front_private = priv
            wev = self._windows.tick(now_i, pid, exe, title, priv, ours)
            if wev is not None:
                self._writer.line(wev)

        cx, cy = self.probe.cursor()
        left, right = self.probe.buttons()
        li = self.probe.last_input()
        ax, ay, aw, ah = self.area
        inside = (ax <= cx < ax + aw) and (ay <= cy < ay + ah)
        left_b, right_b = bool(left), bool(right)
        newly_down = (left_b and not self._clicks.prev_left) or (right_b and not self._clicks.prev_right)
        wpriv = wours = False
        if newly_down and inside:
            wpid, wexe, wtitle = self.probe.window_at(cx, cy)
            wours = (wpid == self.own_pid)
            wpriv = False if wours else bool(self.is_private(wexe, wtitle))
        cev = self._clicks.tick(now_i, left_b, right_b, cx - ax, cy - ay, inside, wpriv, wours)
        if cev is not None:
            self._writer.line(cev)

        any_btn = left_b or right_b
        typed = (self._last_input is not None and li != self._last_input
                 and (cx, cy) == self._last_cursor and not any_btn and not self._prev_any_btn)
        due = self._typing.due(now_i)
        if due is not None:
            self._writer.line(due)
        self._typing.tick(now_i, typed, self._front_private or wpriv)
        self._last_input, self._last_cursor, self._prev_any_btn = li, (cx, cy), any_btn

    def _run(self):
        errors, last_win = 0, -WINDOW_POLL_S
        while not self._stop.is_set():
            t_mono = time.monotonic()
            do_win = (t_mono - last_win) >= WINDOW_POLL_S
            if do_win:
                last_win = t_mono
            try:
                self.step(max(0, int(self.clock())), windows=do_win)
                errors = 0
            except Exception:
                errors += 1
                if errors == 1:
                    log.exception("screen events poll failed")
                if errors >= 50:
                    log.error("screen events stopping after 50 poll errors in a row")
                    return
            self._stop.wait(POLL_S)

    def stop(self):
        try:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=2)
            if self._stopped:
                return
            self._stopped = True
            if self._writer is None:
                return
            try:
                now = max(0, int(self.clock()))
            except Exception:
                now = self._last_now
            tev = self._typing.flush(now)
            if tev is not None:
                self._writer.line(tev)
            self._writer.end(now)
        except Exception:
            log.exception("screen events stop failed")


def trim_events(src, dst, start_ms, end_ms):
    """Trim an events JSONL file to [start_ms, end_ms], shifting timestamps to start at 0."""
    start_i, end_i = int(start_ms), int(end_ms)
    if start_i < 0 or end_i < start_i:
        raise ValueError("invalid trim range")
    raw_lines = [ln for ln in Path(src).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not raw_lines:
        raise ValueError("empty events file")
    rows = [json.loads(ln) for ln in raw_lines]
    header = rows[0]
    events, orig_cut = [], False
    for idx, row in enumerate(rows[1:]):
        if row.get("k") == "end":
            orig_cut = bool(row.get("cut", False))
        elif "t" in row:
            events.append((int(row["t"]), idx, row))
    events.sort(key=lambda item: (item[0], item[1]))
    carry = None
    kept = []
    for t, _, row in events:
        if t < start_i:
            if row.get("k") in ("window", "private"):
                carry = row
        elif t <= end_i:
            shifted = dict(row)
            shifted["t"] = t - start_i
            if shifted.get("k") == "typing" and "ms" in shifted:
                shifted["ms"] = max(0, min(int(shifted["ms"]), end_i - t))
            kept.append(shifted)
    out = [header]
    if carry is not None:
        carried = dict(carry)
        carried["t"] = 0
        out.append(carried)
    out.extend(kept)
    out.append({"t": end_i - start_i, "k": "end", "lines": len(out), "cut": orig_cut})
    dst_p = Path(dst)
    tmp_p = dst_p.with_suffix(".jsonl.new")
    text = "".join(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n" for obj in out)
    tmp_p.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp_p, dst_p)
