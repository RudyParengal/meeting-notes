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
import subprocess
import sys
import threading
import time
from pathlib import Path

# The code is split over sibling files by job; every name is imported back here so
# `screen_record.NAME` keeps working. ScreenService and child_main stay in this
# file because tests replace set_clipboard_text, upload and friends on this module.
from screen_windows import (  # noqa: F401
    FPS, DEFAULT_HOTKEY, DEFAULT_SHOT_HOTKEY, WDA_EXCLUDEFROMCAPTURE,
    PASSWORD_WORDS, BANK_WORDS, _BANK_RE, _PW_RE, never_record_reasons,
    _win, set_clipboard_text, build_dib, set_clipboard_dib, hide_from_capture, monitors,
    visible_windows, parse_hotkey, hotkey_label, _clip_region, capture_source,
    _monitor_for_rect, _walk_windows, pick_windows, mask_frame,
)
from screen_upload import (  # noqa: F401
    SENT_KEEP_DAYS, Outbox, NotVideo, UploadStuck, STUCK_AFTER, upload, EVENTS_UPLOAD_MAX,
    upload_events,
)
from screen_capture import Capture, _ToneMic  # noqa: F401
from screen_ui import (  # noqa: F401
    _drag_region, _pick_window_dialog, _StrokeBoard, DrawOverlay, _show_region_frame,
    _pick_screen, _warn, _centre, _hwnd, EFFECTS, _click_through, show_effect, _pick_shot,
    _save_png,
)

log = logging.getLogger("screen")


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


def start_bridge(box, cap, watcher, test_mode):
    """Start the localhost extension bridge while recording. Never raises; skipped in test_mode."""
    if test_mode or watcher is None:
        return None
    try:
        import screen_bridge
        b = screen_bridge.Bridge(box.root, cap.video_ms, watcher.write_event)
        b.start()
        return b
    except Exception:
        log.exception("screen bridge did not start; the video records without browser events")
        return None


def stop_bridge(b):
    if b is not None:
        try:
            b.stop()
        except Exception:
            log.exception("screen bridge did not stop cleanly")


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
        if win.state() == "iconic":
            # Minimised, nobody found it and the recording waited unsent for hours (23 Nov 03:41).
            win.deiconify()
            win.lift()
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
    bridge = start_bridge(box, cap, watcher, bool(test_source))
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
            stop_bridge(bridge)
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
        self.buffer = None

    # -- life
    def start(self):
        self.box.recover()
        try:
            self.box.prune_sent()
        except OSError:
            log.exception("could not tidy screen/sent")
        if self.cfg.get("screen_replay_buffer") is True:
            try:
                import screen_buffer
                self.buffer = screen_buffer.RollingBuffer(self.box.root)
                self.buffer.start()
            except Exception:
                log.exception("could not start rolling last-2-minutes buffer")
                self.buffer = None
        threading.Thread(target=self._upload_loop, daemon=True, name="screen-upload").start()
        threading.Thread(target=self._hotkey_loop, daemon=True, name="screen-hotkey").start()

    def shutdown(self, wait=20):
        """Quit was chosen: finish a recording in progress so it is not lost."""
        if self.buffer is not None:
            try:
                self.buffer.stop()
            except Exception:
                log.exception("could not stop rolling buffer cleanly")
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

    @property
    def buffer_active(self):
        return self.buffer is not None and self.buffer.running

    def save_last_two_minutes(self):
        if self.buffer is None:
            return "disabled", None
        status, info = self.buffer.save_last(self.box)
        if status == "blocked":
            self.notify("Last 2 minutes not saved",
                        "A private window is open: " + "; ".join(info))
        elif status == "saved":
            self.wake.set()
        return status, info

    def hotkey(self):
        return self.cfg.get("screen_hotkey") or DEFAULT_HOTKEY

    def shot_hotkey(self):
        return self.cfg.get("screenshot_hotkey") or DEFAULT_SHOT_HOTKEY

    def take_screenshot(self):
        if not self.shot_cmd:
            return
        subprocess.Popen(self.shot_cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def label(self):
        if self.recording:
            return "Stop screen recording"
        suffix = " (last-2-min active)" if self.buffer_active else ""
        return f"Record screen ({hotkey_label(self.hotkey())}){suffix}"

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
                "hotkey": hotkey_label(self.hotkey()), "last_link": self.last_link,
                "buffer_enabled": bool(self.cfg.get("screen_replay_buffer")),
                "buffer_running": self.buffer_active}

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
