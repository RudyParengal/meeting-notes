r"""Notewell - the meeting recorder and the notes dashboard as one Windows app.

One process does everything:
  - the notes server (backend/), reachable from this computer only
  - the recorder (windows-recorder/), as the tray icon
  - an app window (Microsoft Edge in app mode) showing the dashboard

Everything Notewell keeps is in %USERPROFILE%\Notewell: notes, AI settings,
the recorder's config and logs. It never touches the standalone recorder's
%USERPROFILE%\MeetingRecorder, so both can be installed on one PC.

    python desktop/notewell.py

Nobody types a key. The master key is generated on first start and stays in
the Notewell folder; the app window is signed in with a token made from it.
"""
import ctypes
import importlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

# Source checkout: the repository. Installed (PyInstaller): the bundle folder,
# which keeps backend/, dashboard/ and windows-recorder/ side by side the same way.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
HOME = Path(os.environ.get("NOTEWELL_HOME") or (Path.home() / "Notewell"))
KEY_FILE = HOME / "app.key"
PORT_FILE = HOME / "port"
LOG_FILE = HOME / "notewell.log"
ADMIN = "admin@localhost"
PREFERRED_PORT = 8767          # 8766 is the server's own default; a PC may run both

# The recorder's own defaults suit a fast PC and English meetings. These suit
# anyone: a model that understands most languages and downloads in minutes.
FIRST_RUN = {"whisper_model": "small", "spool_enabled": True,
             "auto_detect": False, "live_transcript": False}

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
_mutex = None


def already_running() -> bool:
    """One Notewell per Windows user. The handle is held until the process ends."""
    global _mutex
    if not _kernel32:
        return False
    _mutex = _kernel32.CreateMutexW(None, False, "Local\\Notewell")
    return ctypes.get_last_error() == 183          # ERROR_ALREADY_EXISTS


def show_error(text: str) -> None:
    """The installed app has no console, so a failure must reach the screen."""
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, "Notewell", 0x10)
    else:
        print(text, file=sys.stderr)


def master_key() -> str:
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if len(key) >= 24:
            return key
    key = "mn_" + secrets.token_hex(24)
    KEY_FILE.write_text(key, encoding="utf-8")
    return key


def pick_port() -> int:
    for port in (PREFERRED_PORT, 0):              # 0 = any free port
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port on 127.0.0.1")


def prepare_recorder_config(port: int, key: str) -> None:
    """Point the recorder at this app's server. Every start, because the port can change."""
    path = HOME / "recorder" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else dict(FIRST_RUN)
    cfg.update(api_url=f"http://127.0.0.1:{port}", api_key=key)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def find_edge():
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(base):
            exe = Path(os.environ[base]) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.exists():
                return str(exe)
    return shutil.which("msedge")


def open_window(url: str) -> None:
    """Edge in app mode: its own window and taskbar entry, no tabs or address bar.
    Its own profile folder, so it shares nothing with the person's browsing."""
    edge = find_edge()
    if edge:
        subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={HOME / 'window'}",
                          "--window-size=1280,860", "--no-first-run"])
    else:
        webbrowser.open(url)


def open_running_copy() -> None:
    """A second start (double-clicking the icon again) opens the first copy's window."""
    try:
        port = int(PORT_FILE.read_text().strip())
        body = json.dumps({"email": ADMIN, "password": master_key()}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/login", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            token = json.load(r)["token"]
        open_window(f"http://127.0.0.1:{port}/#token={token}")
    except Exception as e:
        show_error(f"Notewell is already running but did not answer ({e}).\n\n"
                   f"Right-click the Notewell icon by the clock and choose Quit, then start it again.")


def run() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    console = sys.stdout is not None
    if not console:                               # the installed app has no console
        sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    if already_running():
        open_running_copy()
        return

    key, port = master_key(), pick_port()
    PORT_FILE.write_text(str(port))
    # Read once, at import, by backend/config.py and windows-recorder/recorder.py,
    # so they are set before either is imported.
    os.environ.update({
        "DATA_DIR": str(HOME / "data"),
        "MEETING_RECORDER_HOME": str(HOME / "recorder"),
        "MEETING_NOTES_API_KEY": key,
        "ALLOW_UNCONFIGURED_AI": "1",             # the AI is chosen on the Settings screen
        "TOKEN_TTL_HOURS": "720",                 # the window stays signed in for 30 days
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "ADMIN_EMAIL": ADMIN,
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",   # harmless on Windows, alarming in a log
    })
    prepare_recorder_config(port, key)
    # The server and the recorder each call logging.basicConfig and only the
    # first call counts. Without this the server's came first and the recorder's
    # log - the one its "View logs" opens - stayed empty. One log for both.
    handlers = [logging.FileHandler(HOME / "recorder" / "recorder.log", encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "windows-recorder")]

    import uvicorn
    server = importlib.import_module("main")     # backend/main.py
    import recorder
    import recorder_bridge
    recorder.ARCHIVE_ROOT = HOME / "archive"      # not the standalone recorder's MeetingArchive

    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_config=None, access_log=False))
    threading.Thread(target=srv.run, daemon=True, name="server").start()
    held = {}

    def warm_up(ctl):
        """Load (on first start, download) the speech model before the first meeting
        ends, so the dashboard can say 'getting ready' instead of stalling later."""
        try:
            recorder.get_whisper(ctl["cfg"])
            ctl["manager"].model_ready = True
        except Exception:
            recorder.log.exception("speech model failed to load")

    def open_dashboard():
        open_window(f"http://127.0.0.1:{port}/#token={server.tokens.issue(ADMIN)}")

    def on_ready(ctl):
        held["manager"] = ctl["manager"]
        recorder_bridge.attach(**ctl)
        # Imported audio files use the recorder's model: one copy in memory, one download.
        server._get_whisper = lambda: recorder.get_whisper(ctl["cfg"])
        threading.Thread(target=warm_up, args=(ctl,), daemon=True).start()
        for _ in range(200):                      # the server takes a second or two
            if srv.started:
                break
            time.sleep(0.1)
        open_dashboard()

    recorder.main(hooks={"on_ready": on_ready, "open_dashboard": open_dashboard,
                         "open_settings": lambda: open_window(
                             f"http://127.0.0.1:{port}/#token={server.tokens.issue(ADMIN)}&view=settings")})

    # Quit was chosen. A meeting that was just stopped is still being transcribed
    # and sent to the server: finish that before the process ends.
    mgr = held.get("manager")
    while mgr is not None and mgr.processing:
        time.sleep(1)
    srv.should_exit = True


if __name__ == "__main__":
    try:
        run()
    except SystemExit as e:
        if e.code not in (0, None):               # a settings problem; the reason is in the log
            show_error(f"Notewell could not start (code {e.code}).\n\nDetails: {LOG_FILE}")
        raise
    except Exception:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        show_error(f"Notewell could not start.\n\n{traceback.format_exc(limit=1)}\n"
                   f"Details: {LOG_FILE}")
        sys.exit(1)
