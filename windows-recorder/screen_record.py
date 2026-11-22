"""Record the screen and the microphone, upload, put the share link on the clipboard.

Two halves:

* The recording itself runs in a separate copy of Notewell (`Notewell.exe
  --screen-record`, see `child_main`). It owns the screen picker, the warning,
  the red badge, and writes one MP4 straight into the outbox. If it crashes, the
  meeting recorder and dictation in the main copy carry on, and the file written
  so far is still a playable video (fragmented MP4: every two seconds or so is
  complete on its own).
* `ScreenService` in the main copy: the hotkey, starting and stopping that
  child, and the upload queue. A recording waits in the outbox until the server
  has all of it, so with no internet it goes up later, and the link is copied then.

Files: HOME/screen/outbox/<id>.mp4 + <id>.json while waiting; moved to
HOME/screen/sent/ once the server has it, and removed from there after
SENT_KEEP_DAYS (the server keeps the video). Nothing else is written: no frames,
no temporary files.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("screen")

FPS = 15
SENT_KEEP_DAYS = 7
DEFAULT_HOTKEY = "ctrl+shift+r"
DEFAULT_SHOT_HOTKEY = "ctrl+shift+s"
WDA_EXCLUDEFROMCAPTURE = 0x11

# ------------------------------------------------------- never-record list ---
# Password managers: the dictation guard's list (dictation.BLOCKED_APPS) plus
# their names in a window title, which catches the browser-extension versions.
PASSWORD_WORDS = ("1password", "bitwarden", "lastpass", "keepass", "keepassxc", "dashlane",
                  "nordpass", "roboform", "enpass", "proton pass", "password manager")
# Banking: words seen in the titles of bank and payment sites (the browser puts the
# page title in the window title). Whole words only, so "otherwise" never matches "wise".
BANK_WORDS = ("bank", "banking", "online banking", "internet banking", "paypal", "revolut", "monzo",
              "starling", "barclays", "hsbc", "natwest", "lloyds", "santander", "halifax",
              "nationwide", "first direct", "metro bank", "wise account", "privatbank",
              "приватбанк", "monobank", "ощадбанк", "приват24", "privat24", "банк")
_BANK_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in BANK_WORDS) + r")(?!\w)", re.I)
_PW_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in PASSWORD_WORDS) + r")(?!\w)", re.I)


def never_record_reasons(windows, blocked_apps=None):
    """`windows` = [(exe name, window title), ...] on the screen about to be recorded.
    Returns short reasons to warn about, one per window, or [] if none."""
    if blocked_apps is None:
        try:
            from dictation import BLOCKED_APPS as blocked_apps
        except Exception:
            blocked_apps = set()
    out = []
    for exe, title in windows:
        exe, title = (exe or "").lower(), (title or "").strip()
        short = title if len(title) <= 60 else title[:57] + "..."
        if exe in blocked_apps or _PW_RE.search(title):
            why = f"a password manager is open ({short or exe})"
        elif _BANK_RE.search(title):
            why = f"a banking page is open ({short})"
        else:
            continue
        if why not in out:
            out.append(why)
    return out


# ------------------------------------------------------------------ outbox ---
class Outbox:
    """Recordings waiting to go up. One <id>.mp4 and one <id>.json each."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "outbox"
        self.sent = self.root / "sent"
        self.dir.mkdir(parents=True, exist_ok=True)

    def video(self, cid):
        return self.dir / f"{cid}.mp4"

    def untrimmed(self, cid):
        return self.dir / f"{cid}.untrimmed.mp4"

    def events(self, cid):
        return self.dir / f"{cid}.events.jsonl"

    def _meta(self, cid):
        return self.dir / f"{cid}.json"

    def new_job(self, title):
        cid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(4).hex()
        job = {"client_id": cid, "title": title, "state": "recording", "started": time.time()}
        self.save(job)
        return job

    def save(self, job):
        p = self._meta(job["client_id"])
        tmp = p.with_suffix(".json.new")
        tmp.write_text(json.dumps(job, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def jobs(self):
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                log.warning("unreadable outbox entry %s", p.name)
        return sorted(out, key=lambda j: j.get("started", 0))

    def waiting(self):
        return [j for j in self.jobs() if j.get("state") == "ready"]

    def recover(self, live=()):
        """After a crash: a recording no copy is still writing is finished as it
        stands. An empty one (stopped before the first frame) is moved aside."""
        for job in self.jobs():
            if job.get("state") != "recording" or job["client_id"] in live:
                continue
            v = self.video(job["client_id"])
            if v.exists() and v.stat().st_size > 0:
                job.update(state="ready", recovered=True)
                self.save(job)
                log.info("recovered unfinished screen recording %s", job["client_id"])
            else:
                self.move_aside(job, "empty")

    def move_aside(self, job, why):
        """Out of the queue but kept: outbox/<why>/."""
        dest = self.dir / why
        dest.mkdir(exist_ok=True)
        cid = job["client_id"]
        for p in (self.video(cid), self.untrimmed(cid), self._meta(cid), self.events(cid)):
            if p.exists():
                os.replace(p, dest / p.name)

    def mark_sent(self, job, share_url):
        self.sent.mkdir(exist_ok=True)
        job.update(state="sent", share_url=share_url, sent=time.time())
        self.save(job)
        cid = job["client_id"]
        for p in (self.video(cid), self.untrimmed(cid), self._meta(cid), self.events(cid)):
            if p.exists():
                os.replace(p, self.sent / p.name)

    def prune_sent(self, days=SENT_KEEP_DAYS, now=None):
        """The server has these; the laptop copy is only kept for a week."""
        cutoff = (now or time.time()) - days * 86400
        gone = 0
        for p in self.sent.glob("*") if self.sent.exists() else ():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                gone += 1
        return gone


# ------------------------------------------------------------------ upload ---
class NotVideo(Exception):
    """The server could not read the file as video. Retrying will not help."""


class UploadStuck(Exception):
    """The server keeps answering without taking any more of the file. Retrying will not help."""


STUCK_AFTER = 3          # answers in a row with no progress before giving up


def upload(job, path: Path, http, base_url: str):
    """Send one recording in pieces, carrying on from wherever the server got to.
    Returns the share link. Raises on a network fault (try again later), NotVideo or UploadStuck."""
    size = path.stat().st_size
    r = http.post(f"{base_url}/recordings", timeout=30, json={
        "title": job["title"], "size": size, "content_type": "video/mp4", "client_id": job["client_id"]})
    r.raise_for_status()
    rec = r.json()
    rid, piece, off = rec["id"], rec.get("piece_max", 8 * 1024 * 1024), rec.get("received", 0)
    if rec.get("status") == "failed":
        raise NotVideo(rid)
    stuck = 0
    with open(path, "rb") as f:
        while rec.get("status") != "ready":
            if rec.get("status") == "failed":
                raise NotVideo(rid)
            f.seek(off)
            data = f.read(piece)
            r = http.put(f"{base_url}/recordings/{rid}/data", params={"offset": off}, data=data,
                         headers={"Content-Type": "application/octet-stream"}, timeout=300)
            if r.status_code == 409:                     # the server is somewhere else: go there
                new = r.json()["received"]
            elif r.status_code == 422:
                raise NotVideo(rid)
            else:
                r.raise_for_status()
                rec = r.json()
                if rec.get("status") == "failed":
                    raise NotVideo(rid)
                if rec.get("status") == "ready":
                    break
                new = rec["received"]
            stuck = stuck + 1 if new == off else 0
            if stuck >= STUCK_AFTER:
                raise UploadStuck(rid)
            off = new
    job["server_id"] = rid
    return rec["share_url"]


EVENTS_UPLOAD_MAX = 5 * 1024 * 1024


def upload_events(job, path: Path, http, base_url: str) -> str:
    """Send the events file once the video is in. Never raises: the link matters more.
    Returns "sent", "none", "old server" or "failed" for the log and the job file."""
    rid = job.get("server_id")
    if not rid or not path or not path.exists():
        return "none"
    try:
        size = path.stat().st_size
    except OSError as e:
        log.warning("screen events for %s unreadable: %s", rid, type(e).__name__)
        return "failed"
    if size == 0:
        return "none"
    if size > EVENTS_UPLOAD_MAX:
        log.warning("screen events for %s over 5 MB (%d bytes), skipped", rid, size)
        return "failed"
    try:
        r = http.put(f"{base_url}/recordings/{rid}/events", data=path.read_bytes(),
                     headers={"Content-Type": "application/x-ndjson"}, timeout=20)
    except Exception as e:
        log.warning("screen events upload for %s failed: %s", rid, type(e).__name__)
        return "failed"
    if r.status_code == 200:
        return "sent"
    if r.status_code in (404, 405):
        return "old server"
    log.warning("screen events upload for %s failed: status %d", rid, r.status_code)
    return "failed"


# --------------------------------------------------------- Windows helpers ---
def _win():
    import ctypes
    from ctypes import wintypes as W
    return ctypes, W


def set_clipboard_text(text: str) -> bool:
    """Put text on the clipboard from any thread. Uses a hidden window of its own:
    a clipboard opened with no window may refuse the data."""
    ctypes, W = _win()
    u32, k32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    u32.CreateWindowExW.restype = W.HWND
    u32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    u32.OpenClipboard.argtypes = [W.HWND]
    u32.SetClipboardData.restype = W.HANDLE
    u32.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
    u32.DestroyWindow.argtypes = [W.HWND]
    k32.GlobalAlloc.restype = W.HGLOBAL
    k32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = W.LPVOID
    k32.GlobalLock.argtypes = [W.HGLOBAL]
    k32.GlobalUnlock.argtypes = [W.HGLOBAL]
    k32.GlobalFree.argtypes = [W.HGLOBAL]
    hwnd = u32.CreateWindowExW(0, "STATIC", "notewell-clip", 0, 0, 0, 0, 0, W.HWND(-3), None, None, None)
    try:
        for _ in range(20):                      # another program may hold it for a moment
            if u32.OpenClipboard(hwnd):
                break
            time.sleep(0.05)
        else:
            return False
        try:
            u32.EmptyClipboard()
            data = (text + "\0").encode("utf-16-le")
            h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
            ctypes.memmove(k32.GlobalLock(h), data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(13, h):              # CF_UNICODETEXT; Windows owns h now
                k32.GlobalFree(h)
                return False
            return True
        finally:
            u32.CloseClipboard()
    finally:
        if hwnd:
            u32.DestroyWindow(hwnd)


def build_dib(bgra) -> bytes:
    """Build a 32-bit bottom-up CF_DIB byte buffer from an H x W x 4 BGRA uint8 array."""
    import struct
    import numpy as np
    arr = np.asarray(bgra, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise ValueError("expected H x W x 4 BGRA array")
    h, w = int(arr.shape[0]), int(arr.shape[1])
    pixels = np.ascontiguousarray(arr[::-1].copy())
    pixels[:, :, 3] = 255
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    return header + pixels.tobytes()


def set_clipboard_dib(dib_bytes: bytes) -> bool:
    """Put a CF_DIB (format 8) image on the Windows clipboard."""
    ctypes, W = _win()
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.CreateWindowExW.restype = W.HWND
    u32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    u32.OpenClipboard.argtypes = [W.HWND]
    u32.SetClipboardData.restype = W.HANDLE
    u32.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
    u32.DestroyWindow.argtypes = [W.HWND]
    k32.GlobalAlloc.restype = W.HGLOBAL
    k32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = W.LPVOID
    k32.GlobalLock.argtypes = [W.HGLOBAL]
    k32.GlobalUnlock.argtypes = [W.HGLOBAL]
    k32.GlobalFree.argtypes = [W.HGLOBAL]
    hwnd = u32.CreateWindowExW(0, "STATIC", "notewell-clip", 0, 0, 0, 0, 0, W.HWND(-3), None, None, None)
    try:
        for _ in range(20):
            if u32.OpenClipboard(hwnd):
                break
            time.sleep(0.05)
        else:
            return False
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(0x0002, len(dib_bytes))
            ctypes.memmove(k32.GlobalLock(h), dib_bytes, len(dib_bytes))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(8, h):              # CF_DIB; Windows owns h now
                k32.GlobalFree(h)
                return False
            return True
        finally:
            u32.CloseClipboard()
    finally:
        if hwnd:
            u32.DestroyWindow(hwnd)


def hide_from_capture(hwnd) -> bool:
    """Keep a window of ours out of screen recordings (and screenshots)."""
    ctypes, W = _win()
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.SetWindowDisplayAffinity.argtypes = [W.HWND, W.DWORD]
    return bool(u32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


def monitors():
    """[(left, top, width, height)] in the order Windows lists them, which is the
    order the screen grabber numbers them."""
    ctypes, W = _win()
    u32 = ctypes.windll.user32
    out = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)

    def each(hmon, hdc, rect, lp):
        r = rect.contents
        out.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True
    u32.EnumDisplayMonitors(None, None, proc(each), 0)
    return out


def visible_windows(area):
    """[(exe name, title)] of the normal windows showing on `area` (left, top, w, h)."""
    ctypes, W = _win()
    u32, k32, dwm = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.dwmapi
    k32.OpenProcess.restype = W.HANDLE
    left, top, w, h = area
    found = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def each(hwnd, lp):
        if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
            return True
        cloaked = W.DWORD()
        dwm.DwmGetWindowAttribute(W.HWND(hwnd), 14, ctypes.byref(cloaked), 4)   # hidden by Windows itself
        if cloaked.value:
            return True
        r = W.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right <= left or r.left >= left + w or r.bottom <= top or r.top >= top + h:
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        pid = W.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        hp = k32.OpenProcess(0x1000, False, pid.value)                  # query limited information
        if hp:
            size = W.DWORD(32768)
            big = ctypes.create_unicode_buffer(32768)
            if k32.QueryFullProcessImageNameW(W.HANDLE(hp), 0, big, ctypes.byref(size)):
                exe = big.value.rsplit("\\", 1)[-1]
            k32.CloseHandle(W.HANDLE(hp))
        if buf.value or exe:
            found.append((exe, buf.value))
        return True
    u32.EnumWindows(proc(each), 0)
    return found


def parse_hotkey(text):
    """'ctrl+shift+r' -> (modifiers, virtual key) for RegisterHotKey."""
    mods = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
    m, vk = 0, None
    for part in (text or DEFAULT_HOTKEY).lower().replace(" ", "").split("+"):
        if part in mods:
            m |= mods[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"unknown key {part!r} in {text!r}")
    if vk is None or not m:
        raise ValueError(f"{text!r} needs a modifier and a key")
    return m, vk


def hotkey_label(text):
    return "+".join(p.capitalize() if len(p) > 1 else p.upper()
                    for p in (text or DEFAULT_HOTKEY).lower().split("+"))


def _clip_region(monitor, region):
    """Clip region (x, y, w, h) to monitor (x, y, w, h) and round size down to even."""
    mx, my, mw, mh = (int(v) for v in monitor)
    rx, ry, rw, rh = (int(v) for v in region)
    x1, y1 = max(mx, rx), max(my, ry)
    x2, y2 = min(mx + mw, rx + rw), min(my + mh, ry + rh)
    w, h = (x2 - x1) - (x2 - x1) % 2, (y2 - y1) - (y2 - y1) % 2
    if w <= 0 or h <= 0:
        raise ValueError("region is outside the monitor")
    return (x1, y1, w, h)


def capture_source(idx, monitor, region=None):
    """ddagrab source for a whole monitor or a part of it. monitor and region are (x, y, w, h)."""
    base = f"ddagrab=output_idx={idx}:framerate={FPS}:draw_mouse=1"
    if region is None:
        return f"{base},hwdownload,format=bgra"
    x1, y1, w, h = _clip_region(monitor, region)
    ox, oy = x1 - int(monitor[0]), y1 - int(monitor[1])
    return f"{base}:video_size={w}x{h}:offset_x={ox}:offset_y={oy},hwdownload,format=bgra"


def _monitor_for_rect(screens, rect):
    """Index of the monitor holding the largest part of rect (x, y, w, h)."""
    rx, ry, rw, rh = rect
    best_i, best_area = 0, -1
    for i, (mx, my, mw, mh) in enumerate(screens):
        iw = max(0, min(mx + mw, rx + rw) - max(mx, rx))
        ih = max(0, min(my + mh, ry + rh) - max(my, ry))
        if iw * ih > best_area:
            best_i, best_area = i, iw * ih
    return best_i


def _walk_windows():
    """Return [(hwnd, pid, exe, title, rect)] for visible top-level windows with a title."""
    ctypes, W = _win()
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    u32.IsWindowVisible.argtypes, u32.IsWindowVisible.restype = [W.HWND], W.BOOL
    u32.IsIconic.argtypes, u32.IsIconic.restype = [W.HWND], W.BOOL
    u32.GetWindowRect.argtypes, u32.GetWindowRect.restype = [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL
    u32.GetWindowTextLengthW.argtypes, u32.GetWindowTextLengthW.restype = [W.HWND], ctypes.c_int
    u32.GetWindowTextW.argtypes, u32.GetWindowTextW.restype = [W.HWND, W.LPWSTR, ctypes.c_int], ctypes.c_int
    u32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    u32.GetWindowThreadProcessId.restype = W.DWORD
    u32.EnumWindows.argtypes, u32.EnumWindows.restype = [ctypes.c_void_p, W.LPARAM], W.BOOL
    k32.OpenProcess.argtypes, k32.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    k32.QueryFullProcessImageNameW.restype = W.BOOL
    k32.CloseHandle.argtypes, k32.CloseHandle.restype = [W.HANDLE], W.BOOL
    dwm.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, ctypes.c_void_p, W.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long

    found = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def each(hwnd, lp):
        if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
            return True
        cloaked = W.DWORD(0)
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return True
        r = W.RECT()
        # Attribute 9 = DWMWA_EXTENDED_FRAME_BOUNDS (visible frame without drop shadow).
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            u32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 100 or h < 100:
            return True
        pid = W.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        hp = k32.OpenProcess(0x1000, False, pid.value)
        if hp:
            try:
                size, big = W.DWORD(32768), ctypes.create_unicode_buffer(32768)
                if k32.QueryFullProcessImageNameW(hp, 0, big, ctypes.byref(size)):
                    exe = big.value.rsplit("\\", 1)[-1]
            finally:
                k32.CloseHandle(hp)
        found.append((int(hwnd), int(pid.value), exe, title, (r.left, r.top, w, h)))
        return True

    cb = proc(each)
    u32.EnumWindows(ctypes.cast(cb, ctypes.c_void_p), 0)
    return found


def pick_windows(walk=None, own_pid=None, blocked_apps=None):
    """Visible top-level windows with a title, as [(hwnd, exe, title, rect)].
    Leaves out windows of this process and every window never_record_reasons flags."""
    my_pid = os.getpid() if own_pid is None else own_pid
    items = _walk_windows() if walk is None else walk()
    out = []
    for hwnd, pid, exe, title, rect in items:
        if pid == my_pid or not (title or "").strip():
            continue
        if never_record_reasons([(exe, title)], blocked_apps=blocked_apps):
            continue
        out.append((hwnd, exe, title, rect))
    return out


def mask_frame(bgra, rects, block=16):
    """Pixelate each rect in place on an H x W x 4 uint8 array; return the array."""
    h, w = bgra.shape[:2]
    step = max(1, int(block))
    for rx, ry, rw, rh in rects:
        x1 = max(0, min(w, int(rx)))
        y1 = max(0, min(h, int(ry)))
        x2 = max(0, min(w, int(rx) + int(rw)))
        y2 = max(0, min(h, int(ry) + int(rh)))
        if x2 <= x1 or y2 <= y1:
            continue
        for y in range(y1, y2, step):
            ye = min(y + step, y2)
            for x in range(x1, x2, step):
                xe = min(x + step, x2)
                bgra[y:ye, x:xe] = bgra[y:ye, x:xe].mean(axis=(0, 1), dtype=float).astype(bgra.dtype)
    return bgra


# ============================================================ the recording ===
class Capture:
    """Screen + microphone into one MP4. Runs in the child copy."""

    def __init__(self, path: Path, output_idx: int, source=None, mic=True, monitor=None, region=None):
        self.path, self.idx = Path(path), output_idx
        self.want_mic = bool(mic)
        # A made-up test picture instead of the screen, for tests only.
        self.test = bool(source)
        if source:
            self.source = source
        elif monitor is not None:
            self.source = capture_source(output_idx, monitor, region)
        else:
            self.source = capture_source(output_idx, (0, 0, 0, 0), None)
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self._paused_at = None
        self.muted = False
        self.masks = []
        self.error = None
        self.mic_ok = False
        self.encoder = None
        self.frames = 0

    def add_mask(self, rect):
        rx, ry, rw, rh = (int(v) for v in rect)
        if rw > 0 and rh > 0:
            with self.lock:
                self.masks.append((rx, ry, rw, rh))

    def clear_masks(self):
        with self.lock:
            self.masks.clear()

    def mute(self, on: bool):
        with self.lock:
            self.muted = bool(on)

    def pause(self):
        with self.lock:
            if self._paused_at is None:
                self._paused_at = time.monotonic()

    def resume(self):
        with self.lock:
            if self._paused_at is not None:
                self.t0 += time.monotonic() - self._paused_at
                self._paused_at = None

    @property
    def paused(self):
        return self._paused_at is not None

    def start(self):
        import av
        from fractions import Fraction
        self.src = av.open(self.source, format="lavfi")
        first = next(self.src.decode(video=0))
        w, h = first.width - first.width % 2, first.height - first.height % 2
        enc, fmt, opts = self._pick_encoder(w, h)
        mic = self._open_mic() if self.want_mic else None
        # Fragmented MP4: what is written so far stays playable if this process dies.
        self.out = av.open(str(self.path), "w", format="mp4",
                           options={"movflags": "frag_keyframe+empty_moov+default_base_moof"})
        self.v = self.out.add_stream(enc, rate=FPS)
        self.v.width, self.v.height, self.v.pix_fmt = w, h, fmt
        self.v.time_base = Fraction(1, 1000)
        # The encoder must count in milliseconds too. Left at 1/15 s it rounds two
        # close frames onto the same tick and the file writer refuses the second.
        self.v.codec_context.time_base = Fraction(1, 1000)
        self.v.codec_context.gop_size = FPS * 2
        self.v.options = opts
        self.encoder, self.fmt = enc, fmt
        self.a = None
        if mic is not None:
            self.a = self.out.add_stream("aac", rate=48000)
            self.a.layout = "mono"
            self.a.time_base = Fraction(1, 48000)
        self.t0 = time.monotonic()
        self.threads = [threading.Thread(target=self._video, args=(first,), daemon=True, name="screen-video")]
        if mic is not None:
            self.threads.append(threading.Thread(target=self._audio, args=(mic,), daemon=True, name="screen-mic"))
        for t in self.threads:
            t.start()

    @staticmethod
    def _pick_encoder(w, h):
        """Windows' own H.264 encoder (no licence question); the one inside PyAV if
        that is missing. Tried on its own first, so a refusal leaves no half-made
        track in the file."""
        import av
        from fractions import Fraction
        last = None
        for enc, fmt, opts in (("h264_mf", "nv12", {"rate_control": "quality", "quality": "60"}),
                               ("libx264", "yuv420p", {"preset": "veryfast", "crf": "28"})):
            try:
                c = av.CodecContext.create(enc, "w")
                c.width, c.height, c.pix_fmt = w, h, fmt
                c.time_base, c.framerate = Fraction(1, 1000), Fraction(FPS, 1)
                c.options = dict(opts)
                c.open()
                return enc, fmt, opts
            except Exception as e:
                log.warning("video encoder %s unavailable: %s", enc, e)
                last = e
        raise last

    def _open_mic(self):
        if self.test:                       # tests: a tone, never the real microphone
            self.mic_ok = True
            return _ToneMic()
        try:
            import soundcard as sc
            import soundgaps
            soundgaps.install()
            rec = sc.default_microphone().recorder(samplerate=48000, channels=1, blocksize=soundgaps.blocksize(48000))
            rec.__enter__()
            self.mic_ok = True
            return rec
        except Exception as e:
            log.warning("microphone unavailable, recording without sound: %s", e)
            return None

    def _mux(self, packets):
        with self.lock:
            for p in packets:
                self.out.mux(p)

    def _video(self, first):
        import av
        from fractions import Fraction
        try:
            last = -1
            frame = first
            frames = self.src.decode(video=0)
            while True:
                with self.lock:
                    paused = self._paused_at is not None
                    pts = None if paused else int((time.monotonic() - self.t0) * 1000)
                    masks = list(self.masks)
                if not paused:
                    if pts <= last:
                        pts = last + 1
                    if masks:
                        src_f = frame if frame.format.name == "bgra" else frame.reformat(format="bgra")
                        arr = src_f.to_ndarray()
                        mask_frame(arr, masks)
                        frame_in = av.VideoFrame.from_ndarray(arr, format="bgra")
                    else:
                        frame_in = frame
                    f = frame_in.reformat(width=self.v.width, height=self.v.height, format=self.fmt)
                    f.pts, f.time_base = pts, Fraction(1, 1000)
                    self._mux(self.v.encode(f))
                    last = pts
                    self.frames += 1
                if self.stop_ev.is_set():
                    break
                frame = next(frames)
            self._mux(self.v.encode())
        except Exception as e:
            log.exception("screen video stopped")
            self.error = self.error or f"screen capture stopped: {e}"
            self.stop_ev.set()
        finally:
            try:
                self.src.close()
            except Exception:
                pass

    def _audio(self, mic):
        import av
        from fractions import Fraction
        import numpy as np
        try:
            pts = None
            min_pts = 0
            while not self.stop_ev.is_set():
                block = mic.record(numframes=1024)
                with self.lock:
                    paused = self._paused_at is not None
                    muted = self.muted
                    t0 = self.t0
                if paused:
                    pts = None
                    continue
                if muted:
                    block = np.zeros_like(block)
                if pts is None:     # line the sound up with the picture
                    pts = max(min_pts, int((time.monotonic() - t0) * 48000) - len(block))
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block[:, :1].T, dtype=np.float32),
                                                   format="flt", layout="mono")
                frame.sample_rate, frame.pts, frame.time_base = 48000, pts, Fraction(1, 48000)
                pts += len(block)
                min_pts = pts
                self._mux(self.a.encode(frame))
            self._mux(self.a.encode())
        except Exception as e:
            log.exception("screen microphone stopped")
            self.error = self.error or f"microphone stopped: {e}"
        finally:
            try:
                mic.__exit__(None, None, None)
            except Exception:
                pass

    def video_ms(self):
        """Milliseconds since the first frame's clock started: the events file uses this clock."""
        with self.lock:
            return int(((self._paused_at or time.monotonic()) - self.t0) * 1000)

    def stop(self):
        self.resume()
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=15)
        with self.lock:
            self.out.close()
        return time.monotonic() - self.t0


class _ToneMic:
    """Stands in for the microphone in tests: a quiet 440 Hz tone, in real time."""

    def __init__(self):
        self.n = 0
        self.t0 = time.monotonic()

    def record(self, numframes):
        import numpy as np
        due = self.t0 + (self.n + numframes) / 48000
        time.sleep(max(0.0, due - time.monotonic()))
        t = np.arange(self.n, self.n + numframes) / 48000
        self.n += numframes
        return (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)

    def __exit__(self, *a):
        pass


def _drag_region(root, area, tk, prompt="Drag an area. Esc to go back.", min_size=100):
    """Dimmed overlay over area; returns dragged (x, y, w, h) in screen pixels or None."""
    ax, ay, aw, ah = area
    res = {"rect": None}
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.35)
    win.geometry(f"{aw}x{ah}+{ax}+{ay}")
    win.configure(bg="#000000", cursor="crosshair")
    hide_from_capture(_hwnd(win))
    cv = tk.Canvas(win, bg="#000000", highlightthickness=0, width=aw, height=ah)
    cv.pack(fill="both", expand=True)
    msg_id = cv.create_text(aw // 2, 36, text=prompt, fill="#ECEAF3", font=("Segoe UI", 13, "bold"))
    drag = {"x0": None, "y0": None, "box": None}

    def down(e):
        drag["x0"], drag["y0"] = e.x, e.y
        if drag["box"] is not None:
            cv.delete(drag["box"])
            drag["box"] = None

    def move(e):
        if drag["x0"] is None:
            return
        if drag["box"] is not None:
            cv.delete(drag["box"])
        drag["box"] = cv.create_rectangle(drag["x0"], drag["y0"], e.x, e.y,
                                          outline="#EF4444", width=3)

    def up(e):
        if drag["x0"] is None:
            return
        x1, x2 = sorted((drag["x0"], e.x))
        y1, y2 = sorted((drag["y0"], e.y))
        drag["x0"] = drag["y0"] = None
        w, h = x2 - x1, y2 - y1
        if w < min_size or h < min_size:
            cv.itemconfigure(msg_id, text=f"Area is too small (at least {min_size}x{min_size}). Drag again or press Esc.")
            return
        res["rect"] = (ax + x1, ay + y1, w, h)
        win.destroy()

    cv.bind("<ButtonPress-1>", down)
    cv.bind("<B1-Motion>", move)
    cv.bind("<ButtonRelease-1>", up)
    win.bind("<Escape>", lambda e: win.destroy())
    win.focus_force()
    root.wait_window(win)
    return res["rect"]


def _pick_window_dialog(root, area, tk):
    """List visible windows and return the chosen rect (x, y, w, h), or None on Esc."""
    wins = pick_windows()
    res = {"rect": None}
    win = tk.Toplevel(root)
    win.title("Record which window?")
    win.attributes("-topmost", True)
    win.configure(bg="#1C1926", padx=18, pady=14)
    tk.Label(win, text="Record which window?", fg="#ECEAF3", bg="#1C1926",
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    if not wins:
        tk.Label(win, text="No windows available to record.", fg="#B9B5C6", bg="#1C1926",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=8)
        tk.Button(win, text="Back", command=win.destroy, width=10, relief="flat",
                  bg="#2D2A36", fg="#ECEAF3").pack(anchor="e")
    else:
        lb = tk.Listbox(win, width=56, height=min(12, len(wins)), bg="#2D2A36", fg="#ECEAF3",
                        selectbackground="#4F46E5", selectforeground="#ECEAF3",
                        relief="flat", highlightthickness=0, font=("Segoe UI", 10))
        for _, exe, title, _ in wins:
            short = title if len(title) <= 50 else title[:47] + "..."
            lb.insert("end", f"{short}  ({exe})" if exe else short)
        lb.selection_set(0)
        lb.pack(fill="both", expand=True, pady=(8, 6))

        def choose(e=None):
            sel = lb.curselection()
            if sel:
                res["rect"] = wins[sel[0]][3]
                win.destroy()

        lb.bind("<Double-Button-1>", choose)
        win.bind("<Return>", choose)
        row = tk.Frame(win, bg="#1C1926")
        row.pack(fill="x")
        tk.Button(row, text="Back (Esc)", command=win.destroy, relief="flat",
                  bg="#2D2A36", fg="#ECEAF3", padx=10).pack(side="right")
        tk.Button(row, text="Record window", command=choose, relief="flat",
                  bg="#EF4444", fg="#ECEAF3", padx=10).pack(side="right", padx=6)
        lb.focus_force()
    win.bind("<Escape>", lambda e: win.destroy())
    _centre(win, area)
    if not wins:
        win.focus_force()
    root.wait_window(win)
    return res["rect"]


class _StrokeBoard:
    """Tracks freehand strokes on a canvas and deletes each one after fade_ms."""

    def __init__(self, canvas, after_fn, fade_ms=3000):
        self.canvas = canvas
        self.after_fn = after_fn
        self.fade_ms = fade_ms
        self._current = []
        self._last = None

    def on_down(self, x, y):
        self._current = []
        self._last = (x, y)

    def on_move(self, x, y):
        if self._last is None:
            return
        lx, ly = self._last
        item = self.canvas.create_line(lx, ly, x, y, fill="#EF4444", width=4,
                                       capstyle="round", joinstyle="round")
        self._current.append(item)
        self._last = (x, y)

    def on_up(self, x=None, y=None):
        if self._last is not None and not self._current:
            lx, ly = self._last
            item = self.canvas.create_line(lx, ly, lx + 1, ly, fill="#EF4444", width=4,
                                           capstyle="round", joinstyle="round")
            self._current.append(item)
        items = list(self._current)
        self._current.clear()
        self._last = None
        if items:
            def erase():
                for item in items:
                    try:
                        self.canvas.delete(item)
                    except Exception:
                        pass
            self.after_fn(self.fade_ms, erase)


class DrawOverlay:
    """Lazy two-window drawing overlay: hidden mouse catcher + recorded click-through ink."""

    _KEY = "#010203"

    def __init__(self, root, tk, area, on_change=None):
        self.root, self.tk, self.area, self.on_change = root, tk, area, on_change
        self.on = False
        self._built = False
        self.catcher = self.ink = self.board = None

    def _build(self):
        ax, ay, aw, ah = self.area
        tk = self.tk
        ink = tk.Toplevel(self.root)
        ink.overrideredirect(True)
        ink.attributes("-topmost", True)
        ink.configure(bg=self._KEY)
        ink.attributes("-transparentcolor", self._KEY)
        ink.geometry(f"{aw}x{ah}+{ax}+{ay}")
        canvas = tk.Canvas(ink, bg=self._KEY, highlightthickness=0, width=aw, height=ah)
        canvas.pack(fill="both", expand=True)
        _click_through(_hwnd(ink))

        catcher = tk.Toplevel(self.root)
        catcher.overrideredirect(True)
        catcher.attributes("-topmost", True)
        catcher.attributes("-alpha", 0.004)
        catcher.configure(bg="#000000", cursor="pencil")
        catcher.geometry(f"{aw}x{ah}+{ax}+{ay}")
        hide_from_capture(_hwnd(catcher))

        board = _StrokeBoard(canvas, self.root.after, fade_ms=3000)
        catcher.bind("<ButtonPress-1>", lambda e: board.on_down(e.x, e.y))
        catcher.bind("<B1-Motion>", lambda e: board.on_move(e.x, e.y))
        catcher.bind("<ButtonRelease-1>", lambda e: board.on_up(e.x, e.y))
        catcher.bind("<Escape>", lambda e: self.set_on(False))

        self.ink, self.catcher, self.board, self._built = ink, catcher, board, True

    def set_on(self, on: bool):
        want = bool(on)
        if want:
            if not self._built:
                self._build()
            self.catcher.deiconify()
            self.catcher.focus_force()
            self.on = True
        else:
            if self._built and self.catcher is not None:
                self.catcher.withdraw()
            self.on = False
        if self.on_change is not None:
            self.on_change(self.on)

    def destroy(self):
        for w in (self.catcher, self.ink):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.catcher = self.ink = self.board = None
        self.on = self._built = False


def _show_region_frame(root, tk, area):
    """Thin 2 px red frame placed 2 px outside area, click-through and hidden from capture."""
    x, y, w, h = area
    wins = []
    for geo in (f"{w + 8}x2+{x - 4}+{y - 4}", f"{w + 8}x2+{x - 4}+{y + h + 2}",
                f"2x{h + 8}+{x - 4}+{y - 4}", f"2x{h + 8}+{x + w + 2}+{y - 4}"):
        edge = tk.Toplevel(root)
        edge.overrideredirect(True)
        edge.configure(bg="#EF4444")
        edge.geometry(geo)
        edge.attributes("-topmost", True)
        hwnd = _hwnd(edge)
        hide_from_capture(hwnd)
        _click_through(hwnd)
        wins.append(edge)
    return wins


def _pick_screen(root, screens, mouse_idx, tk, camera_default=False):
    """Ask what to record. Returns (screen_idx, region, mic, camera) or None for cancel."""
    choice = {"val": None}
    mic_var = tk.BooleanVar(value=True)
    cam_var = tk.BooleanVar(value=bool(camera_default))
    win = tk.Toplevel(root)
    win.title("Record which screen?")
    win.attributes("-topmost", True)
    win.configure(bg="#1C1926", padx=18, pady=14)
    tk.Label(win, text="What would you like to record?", fg="#ECEAF3", bg="#1C1926",
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    order = sorted(range(len(screens)), key=lambda i: (screens[i][0], screens[i][1]))
    names = {i: (["left", "middle", "right"][order.index(i)] if len(screens) == 3 else
                 ["left", "right"][order.index(i)] if len(screens) == 2 else f"screen {i + 1}")
             for i in range(len(screens))}

    def pick(i, region=None):
        choice["val"] = (i, region, bool(mic_var.get()), bool(cam_var.get()))
        win.destroy()

    def pick_window(e=None):
        win.withdraw()
        rect = _pick_window_dialog(root, screens[mouse_idx], tk)
        if rect is None:
            win.deiconify()
            win.focus_force()
            return
        mon_i = _monitor_for_rect(screens, rect)
        try:
            clipped = _clip_region(screens[mon_i], rect)
        except ValueError:
            win.deiconify()
            win.focus_force()
            return
        pick(mon_i, clipped)

    def pick_area(e=None):
        win.withdraw()
        rect = _drag_region(root, screens[mouse_idx], tk)
        if rect is None:
            win.deiconify()
            win.focus_force()
            return
        try:
            clipped = _clip_region(screens[mouse_idx], rect)
        except ValueError:
            win.deiconify()
            win.focus_force()
            return
        pick(mouse_idx, clipped)

    for i, (x, y, w, h) in enumerate(screens):
        here = "   (mouse is here)" if i == mouse_idx else ""
        tk.Button(win, text=f"{i + 1}   {names[i].capitalize()} screen, {w}x{h}{here}", anchor="w",
                  width=44, command=lambda i=i: pick(i), relief="flat", bg="#2D2A36", fg="#ECEAF3",
                  activebackground="#3D3A47", font=("Segoe UI", 10)).pack(fill="x", pady=3)
        win.bind(str(i + 1), lambda e, i=i: pick(i))
    tk.Button(win, text="W   One window...", anchor="w", width=44, command=pick_window,
              relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
              font=("Segoe UI", 10)).pack(fill="x", pady=3)
    tk.Button(win, text="R   Drag an area...", anchor="w", width=44, command=pick_area,
              relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
              font=("Segoe UI", 10)).pack(fill="x", pady=3)
    opts = tk.Frame(win, bg="#1C1926")
    opts.pack(fill="x", pady=(6, 0))
    tk.Checkbutton(opts, text="Record sound", variable=mic_var, bg="#1C1926", fg="#ECEAF3",
                   selectcolor="#2D2A36", activebackground="#1C1926", activeforeground="#ECEAF3",
                   font=("Segoe UI", 10)).pack(side="left")
    tk.Checkbutton(opts, text="Show camera", variable=cam_var, bg="#1C1926", fg="#ECEAF3",
                   selectcolor="#2D2A36", activebackground="#1C1926", activeforeground="#ECEAF3",
                   font=("Segoe UI", 10)).pack(side="left", padx=(16, 0))
    tk.Label(win, text="Enter = whole screen with mouse.  W = window.  R = area.  Esc = cancel.",
             fg="#B9B5C6", bg="#1C1926", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
    win.bind("w", pick_window)
    win.bind("W", pick_window)
    win.bind("r", pick_area)
    win.bind("R", pick_area)
    win.bind("<Return>", lambda e: pick(mouse_idx))
    win.bind("<Escape>", lambda e: win.destroy())
    _centre(win, screens[mouse_idx])
    win.focus_force()
    root.wait_window(win)
    return choice["val"]


def _warn(root, reasons, area, tk):
    """The never-record warning. True = record anyway."""
    ok = {"go": False}
    win = tk.Toplevel(root)
    win.title("Before you record")
    win.attributes("-topmost", True)
    win.configure(bg="#1C1926", padx=18, pady=14)
    tk.Label(win, text="Before you record this screen", fg="#F59E0B", bg="#1C1926",
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    for r in reasons:
        tk.Label(win, text="•  " + r, fg="#ECEAF3", bg="#1C1926", font=("Segoe UI", 10),
                 wraplength=420, justify="left").pack(anchor="w", pady=1)
    tk.Label(win, text="Anyone you send the link to will see it.", fg="#B9B5C6", bg="#1C1926",
             font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 8))
    row = tk.Frame(win, bg="#1C1926")
    row.pack(fill="x")

    def go():
        ok["go"] = True
        win.destroy()
    tk.Button(row, text="Cancel", command=win.destroy, width=12).pack(side="right")
    tk.Button(row, text="Record anyway", command=go, width=14).pack(side="right", padx=6)
    win.bind("<Escape>", lambda e: win.destroy())
    _centre(win, area)
    win.focus_force()
    root.wait_window(win)
    return ok["go"]


def _centre(win, area):
    win.update_idletasks()
    x, y, w, h = area
    win.geometry(f"+{x + (w - win.winfo_reqwidth()) // 2}+{y + (h - win.winfo_reqheight()) // 3}")


def _hwnd(win):
    import ctypes
    win.update_idletasks()
    return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()


# ======================================================== on-screen flashes ===
# A moment of colour on the screen at Start, at Stop and when the link is ready.
# Every piece is hidden from capture (never in the video, a screenshot or a
# screen share) and lets the mouse click straight through it.
# Colours are the website's dark-mode design colours (notewell-ui.css): destructive,
# info, success, warning; background popover #1C1926, text #ECEAF3.
EFFECTS = {
    "start":   ("#EF4444", "\u25cf  Recording", "Press {key} again to stop"),
    "stop":    ("#828CFF", "\u25a0  Stopped", "Getting your link\u2026"),
    "copied":  ("#10B981", "\u2713  Link copied", "Ready to paste: Ctrl+V"),
    "shot":    ("#10B981", "\u2713  Screenshot copied", "Ready to paste: Ctrl+V"),
    "waiting": ("#F59E0B", "Saved on this PC", "The link is copied when it uploads"),
}


def _click_through(hwnd):
    import ctypes
    u32 = ctypes.windll.user32
    ex = u32.GetWindowLongW(hwnd, -20)                      # GWL_EXSTYLE
    # layered, mouse goes through, no taskbar button, never takes the keyboard
    u32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x8000000)


def show_effect(root, tk, area, kind, key="", ms=1600):
    """A coloured frame round `area` and a big message in its middle, gone after
    `ms` (fading over the last 300 ms). Returns the windows."""
    colour, title, sub = EFFECTS[kind]
    x, y, w, h = area
    t = max(6, w // 240)
    wins = []
    for geo in (f"{w}x{t}+{x}+{y}", f"{w}x{t}+{x}+{y + h - t}",
                f"{t}x{h}+{x}+{y}", f"{t}x{h}+{x + w - t}+{y}"):
        edge = tk.Toplevel(root)
        edge.overrideredirect(True)
        edge.configure(bg=colour)
        edge.geometry(geo)
        wins.append(edge)
    pill = tk.Toplevel(root)
    pill.overrideredirect(True)
    pill.configure(bg="#1C1926", highlightthickness=3, highlightbackground=colour, padx=34, pady=18)
    tk.Label(pill, text=title, fg=colour, bg="#1C1926", font=("Segoe UI", 26, "bold")).pack()
    tk.Label(pill, text=sub.format(key=key), fg="#ECEAF3", bg="#1C1926", font=("Segoe UI", 13)).pack(pady=(2, 0))
    pill.update_idletasks()
    pill.geometry(f"+{x + (w - pill.winfo_reqwidth()) // 2}+{y + (h - pill.winfo_reqheight()) // 3}")
    wins.append(pill)
    for win in wins:
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.94)
        hwnd = _hwnd(win)
        hide_from_capture(hwnd)
        _click_through(hwnd)

    def fade(step=6):
        if step == 0:
            for win in wins:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
            return
        for win in wins:
            try:
                win.attributes("-alpha", 0.94 * step / 7)
            except tk.TclError:
                pass
        root.after(50, fade, step - 1)
    root.after(max(0, ms - 300), fade)
    return wins


def _pick_shot(root, monitor, tk):
    """Ask whole screen (Enter) or dragged area (R). Returns (True, region) or None on cancel."""
    choice = {"val": None}
    win = tk.Toplevel(root)
    win.title("Take a screenshot")
    win.attributes("-topmost", True)
    win.configure(bg="#1C1926", padx=18, pady=14)
    tk.Label(win, text="Take a screenshot", fg="#ECEAF3", bg="#1C1926",
             font=("Segoe UI", 12, "bold")).pack(anchor="w")

    def whole(e=None):
        choice["val"] = (True, None)
        win.destroy()

    def area(e=None):
        win.withdraw()
        rect = _drag_region(root, monitor, tk, prompt="Drag an area for the screenshot. Esc to go back.")
        if rect is None:
            win.deiconify()
            win.focus_force()
            return
        try:
            clipped = _clip_region(monitor, rect)
        except ValueError:
            win.deiconify()
            win.focus_force()
            return
        choice["val"] = (True, clipped)
        win.destroy()

    w, h = monitor[2], monitor[3]
    tk.Button(win, text=f"Enter   Whole screen, {w}x{h}", anchor="w", width=40, command=whole,
              relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
              font=("Segoe UI", 10)).pack(fill="x", pady=3)
    tk.Button(win, text="R   Drag an area...", anchor="w", width=40, command=area,
              relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
              font=("Segoe UI", 10)).pack(fill="x", pady=3)
    tk.Label(win, text="Enter = whole screen.  R = drag area.  Esc = cancel.", fg="#B9B5C6",
             bg="#1C1926", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
    win.bind("<Return>", whole)
    win.bind("r", area)
    win.bind("R", area)
    win.bind("<Escape>", lambda e: win.destroy())
    _centre(win, monitor)
    win.focus_force()
    root.wait_window(win)
    return choice["val"]


def _save_png(frame, path: Path):
    import av
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = frame.reformat(format="rgb24")
    with av.open(str(path), "w", format="image2") as out:
        stream = out.add_stream("png")
        stream.width = rgb.width
        stream.height = rgb.height
        stream.pix_fmt = "rgb24"
        for pkt in stream.encode(rgb):
            out.mux(pkt)
        for pkt in stream.encode():
            out.mux(pkt)


def shot_main() -> int:
    """`Notewell.exe --screen-shot`: grab one frame, save PNG, copy CF_DIB, show toast."""
    import ctypes
    import ctypes.wintypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    import av
    import numpy  # noqa: F401
    test_source = os.environ.get("NOTEWELL_SCREEN_TEST_SOURCE")
    shot_dir = Path(os.environ["NOTEWELL_SHOT_DIR"]) if os.environ.get("NOTEWELL_SHOT_DIR") else (
        Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Pictures" / "Notewell")
    auto = bool(os.environ.get("NOTEWELL_SHOT_AUTO"))

    screens = monitors()
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    mon_idx = next((i for i, (x, y, w, h) in enumerate(screens)
                    if x <= pt.x < x + w and y <= pt.y < y + h), 0)
    mon = screens[mon_idx]

    root = None
    tk = None
    region = None
    if test_source:
        source = test_source
    else:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        if auto:
            raw_r = os.environ.get("NOTEWELL_SCREEN_REGION")
            if raw_r:
                region = _clip_region(mon, tuple(int(v) for v in raw_r.split(",")))
        else:
            picked = _pick_shot(root, mon, tk)
            if picked is None:
                root.destroy()
                return 0
            _, region = picked
        area = region or mon
        reasons = never_record_reasons(visible_windows(area))
        if reasons and not _warn(root, reasons, mon, tk):
            root.destroy()
            return 0
        root.update()
        time.sleep(0.05)
        source = capture_source(mon_idx, mon, region)

    with av.open(source, format="lavfi") as src:
        frame = next(src.decode(video=0))
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    out_path = shot_dir / f"Screenshot {stamp}.png"
    n = 1
    while out_path.exists():
        out_path = shot_dir / f"Screenshot {stamp}-{n}.png"
        n += 1
    _save_png(frame, out_path)
    log.info("saved screenshot %s (%dx%d)", out_path.name, frame.width, frame.height)

    if not test_source:
        bgra = frame.reformat(format="bgra").to_ndarray()
        set_clipboard_dib(build_dib(bgra))
        show_effect(root, tk, mon, "shot", ms=1800)
        end_at = time.monotonic() + 1.9
        while time.monotonic() < end_at:
            root.update()
            time.sleep(0.03)
        root.destroy()
    return 0


def flash_main(kind, area=None) -> int:
    """`Notewell.exe --screen-flash <kind>`: one flash on the screen with the
    mouse, then exit. The main copy cannot draw windows itself."""
    import ctypes
    import ctypes.wintypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    if kind not in EFFECTS:
        return 2
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    if area is None:
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        screens = monitors()
        area = next((a for a in screens if a[0] <= pt.x < a[0] + a[2] and a[1] <= pt.y < a[1] + a[3]),
                    screens[0])
    show_effect(root, tk, area, kind, ms=2600)
    root.after(2800, root.quit)
    root.mainloop()
    return 0


def start_events(box, job, cap, area, test_mode):
    """Never raises: if the events watcher cannot start, the video still records."""
    try:
        import screen_events
        w = screen_events.Watcher(
            box.events(job["client_id"]), area, cap.video_ms, (cap.v.width, cap.v.height),
            is_private=lambda exe, title: bool(never_record_reasons([(exe, title)])),
            probe=None if test_mode else screen_events.WinProbe(os.getpid()),
            own_pid=os.getpid(),
            paused=lambda: cap.paused)
        w.start()
        return w
    except Exception:
        log.exception("screen events did not start; the video records without them")
        return None


def stop_events(w):
    if w is not None:
        try:
            w.stop()
        except Exception:
            log.exception("screen events did not stop cleanly")


def parse_trim(start_text, end_text, seconds):
    """Parse trim fields (m:ss); see screen_trim.parse_trim."""
    import screen_trim
    return screen_trim.parse_trim(start_text, end_text, seconds)


def _review_dialog(root, tk, job, video_path, seconds, area, stop_asked, stdin_closed, review_cmds):
    """Show the review window after Stop. Returns (action, title, trim_range).

    Esc, closing the window, or pressing the hotkey again all mean Send so a
    person who walks away still gets the link.
    """
    import screen_trim

    res = {"val": ("send", job["title"], None)}
    win = tk.Toplevel(root)
    win.title("Review recording")
    win.attributes("-topmost", True)
    win.configure(bg="#1C1926", padx=18, pady=14)
    secs_i = max(0, int(round(seconds)))
    top = tk.Frame(win, bg="#1C1926")
    top.pack(fill="x")
    tk.Label(top, text="Review your recording", fg="#ECEAF3", bg="#1C1926",
             font=("Segoe UI", 12, "bold")).pack(side="left")
    tk.Label(top, text=f"{secs_i // 60}:{secs_i % 60:02d}", fg="#B9B5C6", bg="#1C1926",
             font=("Segoe UI", 11)).pack(side="right")

    tk.Label(win, text="Title", fg="#B9B5C6", bg="#1C1926", font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 2))
    title_e = tk.Entry(win, width=44, bg="#2D2A36", fg="#ECEAF3", insertbackground="#ECEAF3",
                       relief="flat", font=("Segoe UI", 10))
    title_e.insert(0, job["title"])
    title_e.pack(fill="x", ipady=4)

    cut = tk.Frame(win, bg="#1C1926")
    cut.pack(fill="x", pady=(10, 0))
    tk.Label(cut, text="Cut from start (m:ss)", fg="#B9B5C6", bg="#1C1926",
             font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
    tk.Label(cut, text="Cut to (m:ss)", fg="#B9B5C6", bg="#1C1926",
             font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=(12, 0))
    start_e = tk.Entry(cut, width=16, bg="#2D2A36", fg="#ECEAF3", insertbackground="#ECEAF3",
                       relief="flat", font=("Segoe UI", 10))
    start_e.grid(row=1, column=0, sticky="w", pady=(2, 0), ipady=3)
    end_e = tk.Entry(cut, width=16, bg="#2D2A36", fg="#ECEAF3", insertbackground="#ECEAF3",
                     relief="flat", font=("Segoe UI", 10))
    end_e.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(2, 0), ipady=3)

    err_lbl = tk.Label(win, text="", fg="#EF4444", bg="#1C1926", font=("Segoe UI", 9))
    err_lbl.pack(anchor="w", pady=(6, 0))

    def current_title():
        return title_e.get().strip() or job["title"]

    def do_send(e=None, strict=True):
        try:
            tr = screen_trim.parse_trim(start_e.get(), end_e.get(), seconds)
        except ValueError as exc:
            if strict:
                err_lbl.config(text=str(exc))
                return
            tr = None
        res["val"] = ("send", current_title(), tr)
        win.destroy()

    def do_discard():
        res["val"] = ("discard", None, None)
        win.destroy()

    def do_play():
        try:
            os.startfile(str(video_path))
        except OSError:
            pass

    row = tk.Frame(win, bg="#1C1926")
    row.pack(fill="x", pady=(10, 0))
    tk.Button(row, text="Play", command=do_play, relief="flat", bg="#2D2A36", fg="#ECEAF3",
              activebackground="#3D3A47", font=("Segoe UI", 10), padx=12).pack(side="left")
    tk.Button(row, text="Send", command=lambda: do_send(strict=True), relief="flat",
              bg="#10B981", fg="#17141F", activebackground="#059669",
              font=("Segoe UI", 10, "bold"), padx=14).pack(side="right")
    tk.Button(row, text="Discard", command=do_discard, relief="flat", bg="#2D2A36", fg="#ECEAF3",
              activebackground="#3D3A47", font=("Segoe UI", 10), padx=12).pack(side="right", padx=8)

    win.bind("<Return>", lambda e: do_send(strict=True))
    win.bind("<Escape>", lambda e: do_send(strict=False))
    win.protocol("WM_DELETE_WINDOW", lambda: do_send(strict=False))

    def poll():
        if not win.winfo_exists():
            return
        if review_cmds:
            cmd = review_cmds.pop(0)
            if cmd == "discard":
                do_discard()
                return
            if cmd == "send":
                do_send(strict=False)
                return
            if cmd.startswith("trim "):
                parts = cmd.split()
                if len(parts) >= 3:
                    start_e.delete(0, "end")
                    start_e.insert(0, parts[1])
                    end_e.delete(0, "end")
                    end_e.insert(0, parts[2])
                    do_send(strict=True)
                    return
        if stop_asked.is_set() or stdin_closed.is_set():
            do_send(strict=False)
            return
        root.after(100, poll)

    _centre(win, area)
    win.focus_force()
    root.after(100, poll)
    root.wait_window(win)
    return res["val"]


def finalize_job(box, job, seconds, stopped_at, cap_info, action="send", title=None,
                 trim_range=None, trim_video_fn=None, trim_events_fn=None):
    """Apply Send/Trim/Discard to a finished recording. Returns (status, error_message)."""
    cid = job["client_id"]
    if action == "discard":
        job.update(state="discarded", seconds=round(seconds, 1), stopped_at=stopped_at, **cap_info)
        box.save(job)
        box.move_aside(job, "discarded")
        return "discarded", None

    new_title = (title or "").strip() or job["title"]
    if trim_range is not None:
        start_s, end_s = trim_range
        if trim_video_fn is None:
            import screen_trim
            trim_video_fn = screen_trim.trim_video
        if trim_events_fn is None:
            import screen_events
            trim_events_fn = screen_events.trim_events
        vid = box.video(cid)
        untrimmed = box.untrimmed(cid)
        ev = box.events(cid)
        tmp_vid = vid.with_suffix(".trimmed.mp4")
        tmp_ev = ev.with_suffix(".trimmed.jsonl")
        try:
            trim_video_fn(vid, tmp_vid, start_s, end_s, cap_info.get("encoder"))
            if ev.exists():
                trim_events_fn(ev, tmp_ev, int(round(start_s * 1000)), int(round(end_s * 1000)))
            os.replace(vid, untrimmed)
            os.replace(tmp_vid, vid)
            if ev.exists() and tmp_ev.exists():
                os.replace(tmp_ev, ev)
        except Exception as e:
            log.exception("screen trim failed for %s", cid)
            for p in (tmp_vid, tmp_ev):
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass
            job.update(title=new_title, state="stuck", seconds=round(seconds, 1), stopped_at=stopped_at,
                       trim=[round(start_s, 3), round(end_s, 3)], trim_error=str(e), **cap_info)
            box.save(job)
            box.move_aside(job, "stuck")
            return "trim_failed", (f'Could not trim "{new_title}". '
                                   f"The original recording is kept on this PC in {box.dir / 'stuck'}.")
        seconds = end_s - start_s
        job["trim"] = [round(start_s, 3), round(end_s, 3)]

    job.update(title=new_title, state="ready", seconds=round(seconds, 1), stopped_at=stopped_at, **cap_info)
    box.save(job)
    return "ready", None


def child_main(outbox_dir: str) -> int:
    """`Notewell.exe --screen-record`. Talks to the main copy by one JSON line per
    event on stdout; reads "stop" on stdin. Writes into the outbox only."""
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # real pixels
    except Exception:
        pass
    import tkinter as tk
    # Load the heavy libraries here, before any recording thread exists. Loading
    # numpy from the sound thread mid-recording froze every thread until Stop.
    import av            # noqa: F401
    import numpy         # noqa: F401
    # The installed app has no console, so Python may not have set these up even
    # though the main copy handed us pipes.
    out = sys.stdout or open(1, "w", encoding="utf-8", closefd=False)
    inp = sys.stdin or open(0, "r", encoding="utf-8", closefd=False)

    def say(**event):
        out.write(json.dumps(event) + "\n")
        out.flush()

    stop_asked = threading.Event()
    stdin_closed = threading.Event()
    cmd_lock = threading.Lock()
    cmd_handler = {"fn": None}
    pending_cmds = []
    review_cmds = []

    def read_stdin():
        for line in inp:
            cmd = line.strip()
            if not cmd:
                continue
            if cmd == "stop":
                stop_asked.set()
                with cmd_lock:
                    fn = cmd_handler["fn"]
                if fn is not None:
                    fn(cmd)
                continue
            if cmd in ("send", "discard") or cmd.startswith("trim "):
                review_cmds.append(cmd)
                continue
            with cmd_lock:
                fn = cmd_handler["fn"]
                if fn is None:
                    pending_cmds.append(cmd)
                else:
                    fn(cmd)
        stdin_closed.set()
        stop_asked.set()                       # the main copy went away: stop too
    threading.Thread(target=read_stdin, daemon=True).start()

    root = tk.Tk()
    root.withdraw()
    screens = monitors()
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    mouse = next((i for i, (x, y, w, h) in enumerate(screens)
                  if x <= pt.x < x + w and y <= pt.y < y + h), 0)
    test_source = os.environ.get("NOTEWELL_SCREEN_TEST_SOURCE")       # tests only: no real screen
    test_pick = os.environ.get("NOTEWELL_SCREEN_TEST_PICK")
    test_area = os.environ.get("NOTEWELL_SCREEN_TEST_AREA")
    env_region = os.environ.get("NOTEWELL_SCREEN_REGION")
    if test_source or test_pick is not None or env_region:
        idx = int(test_pick) if test_pick is not None else (0 if test_source else mouse)
        raw_area = test_area if test_source else env_region
        region = tuple(int(v) for v in raw_area.split(",")) if raw_area else None
        want_mic, want_cam = True, False
    else:
        cam_default = os.environ.get("NOTEWELL_SCREEN_CAMERA") == "1"
        picked = _poll_dialog(root, stop_asked,
                              lambda: _pick_screen(root, screens, mouse, tk, camera_default=cam_default))
        if picked is None:
            say(event="cancelled")
            return 0
        idx, region, want_mic, want_cam = picked
        say(event="camera_pref", enabled=want_cam)
    mon = screens[idx] if idx < len(screens) else screens[0]
    if region is not None and not test_source:
        region = _clip_region(mon, region)
    area = region or mon
    reasons = [] if test_source else never_record_reasons(visible_windows(area))
    if reasons:
        log.info("never-record warning: %d window(s)", len(reasons))
        if not _poll_dialog(root, stop_asked, lambda: _warn(root, reasons, mon, tk)):
            say(event="cancelled")
            return 0
    if stop_asked.is_set():
        say(event="cancelled")
        return 0

    box = Outbox(Path(outbox_dir).parent)
    job = box.new_job(time.strftime("Screen recording %d %b %Y %H:%M").replace(" 0", " ", 1))
    cap = Capture(box.video(job["client_id"]), idx, source=test_source, mic=want_mic,
                  monitor=mon, region=region)
    try:
        cap.start()
    except Exception as e:
        log.exception("screen recording could not start")
        box.move_aside(job, "failed")
        say(event="error", message=f"Screen recording could not start: {e}")
        return 1
    watcher = start_events(box, job, cap, area, bool(test_source))
    say(event="started", client_id=job["client_id"], screen=idx + 1, mic=cap.mic_ok, encoder=cap.encoder)
    effects = not test_source                  # tests never flash the real screen
    if effects:
        key = hotkey_label(os.environ.get("NOTEWELL_SCREEN_HOTKEY") or DEFAULT_HOTKEY)
        show_effect(root, tk, mon, "start", key=key, ms=1800)
    region_frame = _show_region_frame(root, tk, area) if (region is not None and not test_source) else []
    cam_bubble = None
    if want_cam and not test_source:
        try:
            import screen_camera
            cams = screen_camera.list_cameras()
            if cams:
                cam_bubble = screen_camera.CameraBubble(root, tk, area, camera_name=cams[0])
                if not cam_bubble.start():
                    cam_bubble = None
                    say(event="notice", message="Could not open the camera. Recording without it.")
            else:
                say(event="notice", message="No camera found. Recording without it.")
        except Exception as e:
            log.warning("camera bubble failed: %s", e)
            cam_bubble = None
            say(event="notice", message="Could not open the camera. Recording without it.")

    badge = tk.Toplevel(root)
    badge.overrideredirect(True)
    badge.attributes("-topmost", True)
    badge.configure(bg="#17141F")
    dot = tk.Label(badge, text="●", fg="#EF4444", bg="#17141F", font=("Segoe UI", 12))
    dot.pack(side="left", padx=(10, 4))
    clock = tk.Label(badge, text="0:00", fg="#ECEAF3", bg="#17141F", font=("Segoe UI", 11))
    clock.pack(side="left")

    def set_paused(on):
        if on:
            cap.pause()
        else:
            cap.resume()
        dot.config(fg="#9CA3AF" if cap.paused else "#EF4444")
        pause_btn.config(text="Resume" if cap.paused else "Pause")

    pause_btn = tk.Button(badge, text="Pause", width=7, command=lambda: set_paused(not cap.paused),
                          relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
                          font=("Segoe UI", 10), padx=6)
    pause_btn.pack(side="left", padx=(10, 0), pady=6)
    mic_btn = None

    def set_muted(on):
        cap.mute(on)
        if mic_btn is not None:
            mic_btn.config(text="Mic off" if cap.muted else "Mic on",
                           fg="#9CA3AF" if cap.muted else "#ECEAF3")

    if cap.a is not None:
        mic_btn = tk.Button(badge, text="Mic on", width=7, command=lambda: set_muted(not cap.muted),
                            relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
                            font=("Segoe UI", 10), padx=6)
        mic_btn.pack(side="left", padx=(6, 0), pady=6)
    draw_btn = None

    def on_draw_change(is_on):
        if draw_btn is not None:
            draw_btn.config(text="Drawing" if is_on else "Draw",
                            bg="#4F46E5" if is_on else "#2D2A36")
        try:
            badge.lift()
        except tk.TclError:
            pass

    draw = DrawOverlay(root, tk, area, on_change=on_draw_change)
    draw_btn = tk.Button(badge, text="Draw", width=7, command=lambda: draw.set_on(not draw.on),
                         relief="flat", bg="#2D2A36", fg="#ECEAF3", activebackground="#3D3A47",
                         font=("Segoe UI", 10), padx=6)
    draw_btn.pack(side="left", padx=(6, 0), pady=6)
    badge.bind("<Escape>", lambda e: draw.set_on(False))

    def sync_clear_btn():
        with cap.lock:
            has_masks = bool(cap.masks)
        if has_masks and not clear_btn.winfo_ismapped():
            clear_btn.pack(side="left", padx=(6, 0), pady=6, before=stop_btn)
        elif not has_masks and clear_btn.winfo_ismapped():
            clear_btn.pack_forget()

    def add_mask_rect(rect):
        cap.add_mask(rect)
        sync_clear_btn()

    def clear_all_masks():
        cap.clear_masks()
        sync_clear_btn()

    def pick_hide_area():
        if draw.on:
            draw.set_on(False)
        rect = _drag_region(root, area, tk, prompt="Drag an area to hide. Esc to cancel.", min_size=16)
        if rect is not None:
            add_mask_rect((rect[0] - area[0], rect[1] - area[1], rect[2], rect[3]))
        try:
            badge.lift()
        except tk.TclError:
            pass

    tk.Button(badge, text="Hide area", command=pick_hide_area, relief="flat", bg="#2D2A36",
              fg="#ECEAF3", activebackground="#3D3A47", font=("Segoe UI", 10), padx=6).pack(
                  side="left", padx=(6, 0), pady=6)
    clear_btn = tk.Button(badge, text="Clear", command=clear_all_masks, relief="flat", bg="#2D2A36",
                          fg="#ECEAF3", activebackground="#3D3A47", font=("Segoe UI", 10), padx=6)
    stop_btn = tk.Button(badge, text="Stop", command=stop_asked.set, relief="flat", bg="#EF4444", fg="#ECEAF3",
                         activebackground="#DC2626", font=("Segoe UI", 10, "bold"), padx=10)
    stop_btn.pack(side="left", padx=10, pady=6)
    badge.update_idletasks()
    mx, my, mw, mh = mon
    badge.geometry(f"+{mx + (mw - badge.winfo_reqwidth()) // 2}+{my + 8}")
    hidden = hide_from_capture(_hwnd(badge))
    log.info("recording screen %d (%dx%d), encoder %s, mic %s, badge hidden from capture: %s",
             idx + 1, area[2], area[3], cap.encoder, cap.mic_ok, hidden)

    def handle_cmd(cmd):
        if cmd == "stop":
            root.quit()
        elif cmd == "pause":
            set_paused(True)
        elif cmd == "resume":
            set_paused(False)
        elif cmd == "mute":
            set_muted(True)
        elif cmd == "unmute":
            set_muted(False)
        elif cmd == "draw on":
            draw.set_on(True)
        elif cmd == "draw off":
            draw.set_on(False)
        elif cmd == "mask clear":
            clear_all_masks()
        elif cmd.startswith("mask "):
            parts = cmd[5:].strip().split(",")
            if len(parts) == 4:
                try:
                    add_mask_rect(tuple(int(p) for p in parts))
                except ValueError:
                    pass

    def schedule_cmd(cmd):
        try:
            root.after(0, handle_cmd, cmd)
        except Exception:
            pass

    with cmd_lock:
        cmd_handler["fn"] = schedule_cmd
        for c in pending_cmds:
            schedule_cmd(c)
        pending_cmds.clear()

    def tick():
        if stop_asked.is_set() or cap.stop_ev.is_set():
            root.quit()
            return
        s = max(0, cap.video_ms() // 1000)
        clock.config(text=f"{s // 60}:{s % 60:02d}")
        root.after(250, tick)
    # Started from inside the loop: if capture already failed, quit() before
    # mainloop() would be lost and Stop would never be noticed.
    root.after(0, tick)
    root.mainloop()
    with cmd_lock:
        cmd_handler["fn"] = None
    stopped_at = time.time()
    if cam_bubble is not None:
        cam_bubble.stop()
    draw.destroy()
    badge.destroy()
    for edge in region_frame:
        try:
            edge.destroy()
        except tk.TclError:
            pass
    res = {}

    def finish():
        try:
            res["seconds"] = cap.stop()
        except BaseException as e:
            res["error"] = e
        finally:
            stop_events(watcher)
    th = threading.Thread(target=finish, daemon=True)
    th.start()
    while th.is_alive():
        root.update()
        time.sleep(0.03)
    if "error" in res:
        raise res["error"]
    seconds = res["seconds"]
    log.info("stopped: %.1f s, %d frames, error %s", seconds, cap.frames, cap.error)
    say(event="reviewing", client_id=job["client_id"], seconds=round(seconds, 1))
    if test_source:
        action, new_title, trim_range = "send", job["title"], None
    else:
        stop_asked.clear()
        action, new_title, trim_range = _review_dialog(
            root, tk, job, box.video(job["client_id"]), seconds, mon,
            stop_asked, stdin_closed, review_cmds)
    cap_info = {"mic": cap.mic_ok, "encoder": cap.encoder, "error": cap.error}
    status, err_msg = finalize_job(box, job, seconds, stopped_at, cap_info,
                                   action=action, title=new_title, trim_range=trim_range)
    if status == "discarded":
        say(event="discarded", client_id=job["client_id"])
        return 0
    if status == "trim_failed":
        say(event="error", message=err_msg)
        return 1
    shown_until = time.monotonic() + 1.5
    if effects:
        show_effect(root, tk, mon, "stop", ms=1500)
    say(event="done", client_id=job["client_id"], seconds=job["seconds"], stopped_at=stopped_at,
        mic=cap.mic_ok, error=cap.error, size=box.video(job["client_id"]).stat().st_size)
    while effects and time.monotonic() < shown_until:     # let "Stopped" finish showing
        root.update()
        time.sleep(0.03)
    return 0


def _poll_dialog(root, stop_asked, show):
    """Run a dialog, closing it early if the main copy asks us to stop."""
    open_ = {"yes": True}

    def check():
        if not open_["yes"]:
            return
        if stop_asked.is_set():
            for w in root.winfo_children():
                w.destroy()
        else:
            root.after(200, check)
    root.after(200, check)
    try:
        return show()
    finally:
        open_["yes"] = False


# =================================================== the main copy's side ===
class ScreenService:
    """Hotkey, start/stop the recording copy, upload queue, clipboard."""

    def __init__(self, home: Path, child_cmd, get_link, notify, cfg=None, flash=None, shot_cmd=None, save_cfg=None):
        self.box = Outbox(Path(home) / "screen")
        self.child_cmd = list(child_cmd)
        self.shot_cmd = list(shot_cmd) if shot_cmd is not None else None
        self.save_cfg = save_cfg
        self.get_link = get_link            # -> linked_server.Link or None
        self.notify = notify                # (title, message)
        self.flash = flash or (lambda kind: None)   # (kind): a moment of colour on screen
        self._flashed_wait = set()          # recordings already shown "saved, link later"
        self.cfg = cfg if cfg is not None else {}
        self.proc = None
        self.started = None                 # time.monotonic() when recording began
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.uploading = None
        self.last_link = None
        self._said_sign_in = False
        self._stopping = False

    # -- life
    def start(self):
        self.box.recover()
        try:
            self.box.prune_sent()
        except OSError:
            log.exception("could not tidy screen/sent")
        threading.Thread(target=self._upload_loop, daemon=True, name="screen-upload").start()
        threading.Thread(target=self._hotkey_loop, daemon=True, name="screen-hotkey").start()

    def shutdown(self, wait=20):
        """Quit was chosen: finish a recording in progress so it is not lost."""
        p = self.proc
        if p and p.poll() is None:
            self._send("stop")
            try:
                p.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                p.kill()

    # -- recording
    @property
    def recording(self):
        return self.proc is not None and self.proc.poll() is None

    def hotkey(self):
        return self.cfg.get("screen_hotkey") or DEFAULT_HOTKEY

    def shot_hotkey(self):
        return self.cfg.get("screenshot_hotkey") or DEFAULT_SHOT_HOTKEY

    def take_screenshot(self):
        if not self.shot_cmd:
            return
        subprocess.Popen(self.shot_cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def label(self):
        return "Stop screen recording" if self.recording else f"Record screen ({hotkey_label(self.hotkey())})"

    def toggle(self):
        with self.lock:
            if self.recording:
                self._send("stop")
                return "stopping"
            if not self.get_link():
                self.notify("Screen recording", "Sign in to your server in Notewell Settings first, "
                            "so the recording has somewhere to go. It will still record.")
            env = {**os.environ, "NOTEWELL_SCREEN_DIR": str(self.box.dir),
                   "NOTEWELL_SCREEN_HOTKEY": self.hotkey(),
                   "NOTEWELL_SCREEN_CAMERA": "1" if self.cfg.get("screen_camera") else "0"}
            self.proc = subprocess.Popen(self.child_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         env=env, text=True, encoding="utf-8",
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.started = None
            threading.Thread(target=self._watch, args=(self.proc,), daemon=True, name="screen-watch").start()
            return "starting"

    def _send(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def _watch(self, proc):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("event")
            if kind == "started":
                self.started = time.monotonic()
                log.info("screen recording started: screen %s, mic %s, encoder %s",
                         ev.get("screen"), ev.get("mic"), ev.get("encoder"))
                if not ev.get("mic"):
                    self.notify("Screen recording", "Recording without sound: the microphone is not available.")
            elif kind == "camera_pref":
                self.cfg["screen_camera"] = bool(ev.get("enabled"))
                if self.save_cfg is not None:
                    try:
                        self.save_cfg()
                    except Exception:
                        log.exception("could not save camera preference")
            elif kind == "notice":
                self.notify("Screen recording", ev.get("message", ""))
            elif kind == "reviewing":
                self.started = None
            elif kind == "discarded":
                self.started = None
                log.info("screen recording discarded: %s", ev.get("client_id"))
            elif kind == "done":
                self.started = None
                log.info("screen recording stopped: %.0f s, %.1f MB", ev.get("seconds", 0), ev.get("size", 0) / 1e6)
                if ev.get("error"):
                    self.notify("Screen recording", f"Saved, but: {ev['error']}")
                self.wake.set()
            elif kind == "error":
                self.notify("Screen recording", ev.get("message", "could not start"))
        proc.wait()
        self.started = None
        if proc.returncode not in (0, None):
            log.warning("screen recording copy ended with code %s", proc.returncode)
        self.box.recover()                  # a crash: keep what was recorded
        self.wake.set()

    def status(self):
        return {"available": True, "recording": self.recording,
                "seconds": int(time.monotonic() - self.started) if self.started and self.recording else 0,
                "waiting": len(self.box.waiting()), "uploading": self.uploading,
                "hotkey": hotkey_label(self.hotkey()), "last_link": self.last_link}

    # -- upload
    def _upload_loop(self):
        while True:
            self.wake.wait(60)
            self.wake.clear()
            try:
                self.upload_waiting()
            except Exception:
                log.exception("screen upload pass failed")

    def _flash_waiting(self, job):
        if job["client_id"] not in self._flashed_wait:
            self._flashed_wait.add(job["client_id"])
            self.flash("waiting")

    def upload_waiting(self):
        for job in self.box.waiting():
            link = self.get_link()
            if not link:
                self._flash_waiting(job)
                if not self._said_sign_in:
                    self._said_sign_in = True
                    self.notify("Screen recording waiting", "Sign in to your server in Notewell Settings "
                                "and it will upload by itself.")
                return
            self._said_sign_in = False
            path = self.box.video(job["client_id"])
            if not path.exists():
                self.box.move_aside(job, "missing")
                continue
            self.uploading = job["title"]
            try:
                url = upload(job, path, link.http, link.url)
            except NotVideo:
                self.box.move_aside(job, "unreadable")
                self.notify("Screen recording", f"The server could not read “{job['title']}”. "
                            f"It is kept on this PC in {self.box.dir / 'unreadable'}.")
                continue
            except UploadStuck:
                self.box.move_aside(job, "stuck")
                self.notify("Screen recording", f"“{job['title']}” could not finish uploading. "
                            f"It is kept on this PC in {self.box.dir / 'stuck'}.")
                continue
            except Exception as e:                      # offline, server down: next pass
                log.info("screen upload of %s waits: %s", job["client_id"], e)
                self._flash_waiting(job)
                return
            finally:
                self.uploading = None
            job["events_upload"] = upload_events(job, self.box.events(job["client_id"]), link.http, link.url)
            copied = set_clipboard_text(url)
            self.last_link = url
            took = time.time() - job["stopped_at"] if job.get("stopped_at") else None
            log.info("screen recording %s uploaded; link %s; %s after Stop", job["client_id"],
                     "copied" if copied else "NOT copied",
                     f"{took:.1f} s" if took is not None else "?")
            self.box.mark_sent(job, url)
            if copied:
                self.flash("copied")
            self.notify("Link copied" if copied else "Recording uploaded",
                        f"{job['title']} — paste the link anywhere." if copied else url)

    # -- hotkey
    def _hotkey_loop(self):
        import ctypes
        from ctypes import wintypes as W
        u32 = ctypes.windll.user32
        registered = 0
        try:
            mods, vk = parse_hotkey(self.hotkey())
            if u32.RegisterHotKey(None, 0x5C2E, mods | 0x4000, vk):      # MOD_NOREPEAT
                log.info("screen hotkey %s ready", hotkey_label(self.hotkey()))
                registered += 1
            else:
                log.warning("screen hotkey %s is taken by another program", self.hotkey())
                self.notify("Screen recording", f"{hotkey_label(self.hotkey())} is already used by another "
                            "program. Use “Record screen” in the Notewell menu instead.")
        except ValueError as e:
            log.warning("screen hotkey: %s", e)

        try:
            s_mods, s_vk = parse_hotkey(self.shot_hotkey())
            if u32.RegisterHotKey(None, 0x5C2F, s_mods | 0x4000, s_vk):  # MOD_NOREPEAT
                log.info("screenshot hotkey %s ready", hotkey_label(self.shot_hotkey()))
                registered += 1
            else:
                log.warning("screenshot hotkey %s is taken by another program", self.shot_hotkey())
                self.notify("Screenshot", f"{hotkey_label(self.shot_hotkey())} is already used by another "
                            "program.")
        except ValueError as e:
            log.warning("screenshot hotkey: %s", e)

        if not registered:
            return
        msg = W.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                                     # WM_HOTKEY
                try:
                    if msg.wParam == 0x5C2F:
                        self.take_screenshot()
                    else:
                        self.toggle()
                except Exception:
                    log.exception("screen hotkey action failed")
