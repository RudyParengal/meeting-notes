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
        for p in (self.video(cid), self._meta(cid), self.events(cid)):
            if p.exists():
                os.replace(p, dest / p.name)

    def mark_sent(self, job, share_url):
        self.sent.mkdir(exist_ok=True)
        job.update(state="sent", share_url=share_url, sent=time.time())
        self.save(job)
        cid = job["client_id"]
        for p in (self.video(cid), self._meta(cid), self.events(cid)):
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


# ============================================================ the recording ===
class Capture:
    """Screen + microphone into one MP4. Runs in the child copy."""

    def __init__(self, path: Path, output_idx: int, source=None):
        self.path, self.idx = Path(path), output_idx
        # A made-up test picture instead of the screen, for tests only.
        self.test = bool(source)
        self.source = source or (f"ddagrab=output_idx={output_idx}:framerate={FPS}:draw_mouse=1,"
                                 f"hwdownload,format=bgra")
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self._paused_at = None
        self.error = None
        self.mic_ok = False
        self.encoder = None
        self.frames = 0

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
        mic = self._open_mic()
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
        from fractions import Fraction
        try:
            last = -1
            frame = first
            frames = self.src.decode(video=0)
            while True:
                with self.lock:
                    paused = self._paused_at is not None
                    pts = None if paused else int((time.monotonic() - self.t0) * 1000)
                if not paused:
                    if pts <= last:
                        pts = last + 1
                    f = frame.reformat(width=self.v.width, height=self.v.height, format=self.fmt)
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
                    t0 = self.t0
                if paused:
                    pts = None
                    continue
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


def _pick_screen(root, screens, mouse_idx, tk):
    """Ask which screen. Returns its index, or None for cancel. Keys 1-9, Enter, Esc."""
    choice = {"i": None}
    win = tk.Toplevel(root)
    win.title("Record which screen?")
    win.attributes("-topmost", True)
    win.configure(bg="#1C1926", padx=18, pady=14)
    tk.Label(win, text="Record which screen?", fg="#ECEAF3", bg="#1C1926",
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    order = sorted(range(len(screens)), key=lambda i: (screens[i][0], screens[i][1]))
    names = {i: (["left", "middle", "right"][order.index(i)] if len(screens) == 3 else
                 ["left", "right"][order.index(i)] if len(screens) == 2 else f"screen {i + 1}")
             for i in range(len(screens))}

    def pick(i):
        choice["i"] = i
        win.destroy()
    for i, (x, y, w, h) in enumerate(screens):
        here = "   (mouse is here)" if i == mouse_idx else ""
        tk.Button(win, text=f"{i + 1}   {names[i].capitalize()} screen, {w}×{h}{here}", anchor="w",
                  width=44, command=lambda i=i: pick(i), relief="flat", bg="#2D2A36", fg="#ECEAF3",
                  activebackground="#3D3A47", font=("Segoe UI", 10)).pack(fill="x", pady=3)
        win.bind(str(i + 1), lambda e, i=i: pick(i))
    tk.Label(win, text="Enter = the screen with the mouse.  Esc = cancel.", fg="#B9B5C6", bg="#1C1926",
             font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
    win.bind("<Return>", lambda e: pick(mouse_idx))
    win.bind("<Escape>", lambda e: win.destroy())
    _centre(win, screens[mouse_idx])
    win.focus_force()
    root.wait_window(win)
    return choice["i"]


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
    cmd_lock = threading.Lock()
    cmd_handler = {"fn": None}
    pending_cmds = []

    def read_stdin():
        for line in inp:
            cmd = line.strip()
            if not cmd:
                continue
            if cmd == "stop":
                stop_asked.set()
                continue
            with cmd_lock:
                fn = cmd_handler["fn"]
                if fn is None:
                    pending_cmds.append(cmd)
                else:
                    fn(cmd)
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
    if test_source:
        idx = int(os.environ.get("NOTEWELL_SCREEN_TEST_PICK", "0"))
    else:
        idx = mouse if len(screens) == 1 else _poll_dialog(root, stop_asked,
                                                           lambda: _pick_screen(root, screens, mouse, tk))
    if idx is None:
        say(event="cancelled")
        return 0
    area = screens[idx] if idx < len(screens) else screens[0]
    reasons = [] if test_source else never_record_reasons(visible_windows(area))
    if reasons:
        log.info("never-record warning: %d window(s)", len(reasons))
        if not _poll_dialog(root, stop_asked, lambda: _warn(root, reasons, area, tk)):
            say(event="cancelled")
            return 0
    if stop_asked.is_set():
        say(event="cancelled")
        return 0

    box = Outbox(Path(outbox_dir).parent)
    job = box.new_job(time.strftime("Screen recording %d %b %Y %H:%M").replace(" 0", " ", 1))
    cap = Capture(box.video(job["client_id"]), idx, source=test_source)
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
        show_effect(root, tk, area, "start", key=key, ms=1800)

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
    tk.Button(badge, text="Stop", command=stop_asked.set, relief="flat", bg="#EF4444", fg="#ECEAF3",
              activebackground="#DC2626", font=("Segoe UI", 10, "bold"), padx=10).pack(side="left", padx=10, pady=6)
    badge.update_idletasks()
    x, y, w, h = area
    badge.geometry(f"+{x + (w - badge.winfo_reqwidth()) // 2}+{y + 8}")
    hidden = hide_from_capture(_hwnd(badge))
    log.info("recording screen %d (%dx%d), encoder %s, mic %s, badge hidden from capture: %s",
             idx + 1, w, h, cap.encoder, cap.mic_ok, hidden)

    def handle_cmd(cmd):
        if cmd == "pause":
            set_paused(True)
        elif cmd == "resume":
            set_paused(False)

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
    stopped_at = time.time()
    badge.destroy()
    # Finish the file in the background so "Stopped" shows at once.
    shown_until = time.monotonic() + 1.5
    if effects:
        show_effect(root, tk, area, "stop", ms=1500)
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
    job.update(state="ready", seconds=round(seconds, 1), stopped_at=stopped_at, mic=cap.mic_ok,
               encoder=cap.encoder, error=cap.error)
    box.save(job)
    say(event="done", client_id=job["client_id"], seconds=round(seconds, 1), stopped_at=stopped_at,
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

    def __init__(self, home: Path, child_cmd, get_link, notify, cfg=None, flash=None):
        self.box = Outbox(Path(home) / "screen")
        self.child_cmd = list(child_cmd)
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
                   "NOTEWELL_SCREEN_HOTKEY": self.hotkey()}
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
        try:
            mods, vk = parse_hotkey(self.hotkey())
        except ValueError as e:
            log.warning("screen hotkey: %s", e)
            return
        if not u32.RegisterHotKey(None, 0x5C2E, mods | 0x4000, vk):      # MOD_NOREPEAT
            log.warning("screen hotkey %s is taken by another program", self.hotkey())
            self.notify("Screen recording", f"{hotkey_label(self.hotkey())} is already used by another "
                        "program. Use “Record screen” in the Notewell menu instead.")
            return
        log.info("screen hotkey %s ready", hotkey_label(self.hotkey()))
        msg = W.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                                     # WM_HOTKEY
                try:
                    self.toggle()
                except Exception:
                    log.exception("screen hotkey toggle failed")
