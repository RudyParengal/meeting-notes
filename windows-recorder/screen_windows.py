"""Screen recorder: the never-record list and the Windows helpers.

Moved out of screen_record.py unchanged (clipboard, monitors, window list, hotkey
parsing, capture-source choice, masking). screen_record.py re-exports every name.
"""
import os
import re
import time

FPS = 15
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


