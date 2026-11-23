"""Screen recorder: the on-screen pieces (area picker, draw overlay, warning, effects).

Moved out of screen_record.py unchanged. screen_record.py re-exports every name.
"""
from pathlib import Path

from screen_windows import _clip_region, _monitor_for_rect, hide_from_capture, pick_windows


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

