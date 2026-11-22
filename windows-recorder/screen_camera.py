"""Optional draggable camera bubble shown during a screen recording.

Reads a Windows DirectShow camera via PyAV at 320x240, 15 fps, renders it
into a round-ish borderless topmost Tk window (not hidden from capture so it
appears in the recording).
"""
import logging
import re
import threading

log = logging.getLogger("screen")

KEY_HEX = "#010203"
KEY_RGB = (1, 2, 3)
BORDER_RGB = (23, 20, 31)


def parse_dshow_cameras(log_entries):
    """Extract unique video device names from av.logging.Capture() dshow entries."""
    joined = "".join(msg for _, name, msg in log_entries if "dshow" in (name or "").lower())
    seen, out = set(), []
    for dev in re.findall(r'"([^"]+)"\s*\(video\)', joined):
        if dev not in seen:
            seen.add(dev)
            out.append(dev)
    return out


def list_cameras():
    """Return a list of Windows DirectShow camera names available to PyAV."""
    import av
    prev = av.logging.get_level()
    av.logging.set_level(av.logging.INFO)
    try:
        with av.logging.Capture() as logs:
            try:
                av.open("dummy", format="dshow", options={"list_devices": "true"})
            except Exception:
                pass
        return parse_dshow_cameras(logs)
    finally:
        av.logging.set_level(prev)


def frame_to_ppm(rgb, size=180):
    """Center-crop an H x W x 3 uint8 RGB array to a round bubble PPM byte string."""
    import numpy as np
    arr = np.asarray(rgb, dtype=np.uint8)
    h, w = arr.shape[:2]
    side = min(h, w)
    y0, x0 = (h - side) // 2, (w - side) // 2
    crop = arr[y0:y0 + side, x0:x0 + side, :3]
    idx = np.linspace(0, side - 1, size).astype(int)
    scaled = crop[np.ix_(idx, idx)].copy()
    yy, xx = np.ogrid[:size, :size]
    c = (size - 1) / 2.0
    dist2 = (xx - c) ** 2 + (yy - c) ** 2
    outer = (size / 2.0) ** 2
    inner = (size / 2.0 - 2.5) ** 2
    scaled[(dist2 >= inner) & (dist2 <= outer)] = BORDER_RGB
    scaled[dist2 > outer] = KEY_RGB
    return f"P6\n{size} {size}\n255\n".encode("ascii") + scaled.tobytes()


class CameraBubble:
    """Draggable, borderless, topmost camera bubble (intentionally recorded)."""

    def __init__(self, root, tk, area, camera_name=None, source_fn=None, size=180):
        self.root, self.tk, self.area = root, tk, area
        self.camera_name, self.source_fn, self.size = camera_name, source_fn, size
        self.win = self.lbl = self._photo = self._container = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest = None
        self._thread = None
        self.updates = 0
        self.error = None

    def _open_source(self):
        if self.source_fn is not None:
            return iter(self.source_fn())
        import av
        self._container = av.open(
            f"video={self.camera_name}", format="dshow",
            options={"video_size": "320x240", "framerate": "15"},
        )
        return (f.to_ndarray(format="rgb24") for f in self._container.decode(video=0))

    def start(self) -> bool:
        try:
            frames = self._open_source()
        except Exception as e:
            self.error = str(e)
            log.warning("camera could not open: %s", e)
            return False

        ax, ay, aw, ah = self.area
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEY_HEX)
        win.attributes("-transparentcolor", KEY_HEX)
        win.geometry(f"{self.size}x{self.size}+{ax + 16}+{ay + max(16, ah - self.size - 16)}")
        lbl = tk.Label(win, bg=KEY_HEX, bd=0, highlightthickness=0)
        lbl.pack(fill="both", expand=True)

        drag = {"x": 0, "y": 0}

        def down(e):
            drag["x"], drag["y"] = e.x, e.y

        def move(e):
            nx = win.winfo_x() + e.x - drag["x"]
            ny = win.winfo_y() + e.y - drag["y"]
            win.geometry(f"+{nx}+{ny}")

        lbl.bind("<ButtonPress-1>", down)
        lbl.bind("<B1-Motion>", move)
        self.win, self.lbl = win, lbl

        def reader():
            try:
                for rgb in frames:
                    if self._stop.is_set():
                        break
                    ppm = frame_to_ppm(rgb, size=self.size)
                    with self._lock:
                        self._latest = ppm
            except Exception as e:
                if not self._stop.is_set():
                    self.error = str(e)
                    log.warning("camera stream stopped: %s", e)
            finally:
                if self._container is not None:
                    try:
                        self._container.close()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=reader, daemon=True, name="screen-camera")
        self._thread.start()
        self.root.after(40, self._poll)
        return True

    def _poll(self):
        if self._stop.is_set() or self.win is None:
            return
        with self._lock:
            ppm = self._latest
            self._latest = None
        if ppm is not None:
            try:
                self._photo = self.tk.PhotoImage(data=ppm)
                self.lbl.config(image=self._photo)
                self.updates += 1
            except Exception:
                return
        self.root.after(66, self._poll)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = self.lbl = self._photo = None
