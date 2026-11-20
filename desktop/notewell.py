r"""Notewell - the meeting recorder and the notes dashboard as one Windows app.

One process does everything:
  - the notes server (backend/), reachable from this computer only
  - the recorder (windows-recorder/), as the tray icon
  - its own app window (desktop/window.py) showing the dashboard; no browser needed

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
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
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


def instance_name(base: str) -> str:
    """One Notewell per Windows user and data folder. A copy given its own folder
    with NOTEWELL_HOME (a test copy) runs beside the installed one."""
    if not os.environ.get("NOTEWELL_HOME"):
        return base
    import hashlib
    return base + "." + hashlib.sha256(str(HOME.resolve()).lower().encode()).hexdigest()[:12]


def already_running() -> bool:
    """One Notewell per Windows user. The handle is held until the process ends."""
    global _mutex
    if not _kernel32:
        return False
    _mutex = _kernel32.CreateMutexW(None, False, instance_name("Local\\Notewell"))
    return ctypes.get_last_error() == 183          # ERROR_ALREADY_EXISTS


def show_error(text: str) -> None:
    """The installed app has no console, so a failure must reach the screen."""
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, text, "Notewell", 0x10)
    else:
        print(text, file=sys.stderr)


def master_key() -> str:
    """Kept in app.key locked to this Windows user (backend/sealed.py). A key
    saved by an older version in plain text is locked the first time it is read."""
    import sealed
    if KEY_FILE.exists():
        saved = KEY_FILE.read_text(encoding="utf-8").strip()
        try:
            key = sealed.unseal(saved)
        except (OSError, ValueError):
            key = ""                  # locked by another user or PC: start a new key
        if len(key) >= 24:
            if saved == key and sealed.available():
                KEY_FILE.write_text(sealed.seal(key), encoding="utf-8")
            return key
    key = "mn_" + secrets.token_hex(24)
    KEY_FILE.write_text(sealed.seal(key), encoding="utf-8")
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


def prepare_recorder_config(port: int, key: str) -> dict:
    """Point the recorder at this app's server. Every start, because the port can change.
    Linked to a server of your own (linked_server.py): meetings go there first,
    and to this app only when that server does not answer.
    The keys are returned, for recorder.KEYS, and never written to config.json."""
    import linked_server
    path = HOME / "recorder" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else dict(FIRST_RUN)
    here = f"http://127.0.0.1:{port}"
    linked = HOME / "linked-server.json"
    if linked.exists():
        far = linked_server.read_link(HOME)
        cfg.update(api_url=far["url"].rstrip("/"), fallback_api_url=here)
        keys = {"api_key": far["api_key"], "fallback_api_key": key}
    else:
        cfg["api_url"] = here
        cfg.pop("fallback_api_url", None)
        keys = {"api_key": key}
    for k in ("api_key", "fallback_api_key"):     # older versions wrote them here
        cfg.pop(k, None)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return keys


_windows = []


def open_window(url: str) -> None:
    """Notewell's own window (desktop/window.py), a second copy of this program.
    The address carries the sign-in token, so it goes in the environment, which
    only this Windows user can read, not on the command line."""
    if os.name == "nt":
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # let the window come to the front
    _windows.append(subprocess.Popen(child_command("--window"), env={**os.environ, "NOTEWELL_WINDOW_URL": url}))


def child_command(flag: str) -> list:
    """Another copy of this program doing one job (`--window`, `--screen-record`)."""
    return [sys.executable, flag] if getattr(sys, "frozen", False) \
        else [sys.executable, str(Path(__file__).resolve()), flag]


def _screen_flash(kind: str):
    """Show a screen flash from another copy: this one runs the tray, so no windows here."""
    try:
        subprocess.Popen(child_command("--screen-flash") + [kind],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        logging.getLogger("screen").exception("screen flash could not start")


def close_windows() -> None:
    for p in _windows:
        if p.poll() is None:
            p.terminate()


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
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "windows-recorder")]
    HOME.mkdir(parents=True, exist_ok=True)
    console = sys.stdout is not None
    import logfiles                               # every log: 5 MB, 3 old copies kept
    if not console:                               # the installed app has no console
        sys.stdout = sys.stderr = logfiles.Stream(LOG_FILE)
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
        "ALLOWED_HOSTS": "127.0.0.1,localhost",   # see backend/config.py
        "PORT": str(port),
        "ADMIN_EMAIL": ADMIN,
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",   # harmless on Windows, alarming in a log
    })
    recorder_keys = prepare_recorder_config(port, key)
    # The server and the recorder each call logging.basicConfig and only the
    # first call counts. Without this the server's came first and the recorder's
    # log - the one its "View logs" opens - stayed empty. One log for both.
    handlers = [logfiles.handler(HOME / "recorder" / "recorder.log")]   # 5 MB, 3 old copies kept
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import soundgaps                              # sound card gaps: one log line a minute, not one each
    soundgaps.install()

    import uvicorn
    server = importlib.import_module("main")     # backend/main.py
    import recorder
    import recorder_bridge
    recorder.ARCHIVE_ROOT = HOME / "archive"      # not the standalone recorder's MeetingArchive
    recorder.KEYS.update(recorder_keys)

    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_config=None, access_log=False))
    threading.Thread(target=srv.run, daemon=True, name="server").start()
    import linked_server
    held = {}

    def word_store():
        return held["dictation"].store if held.get("dictation") else None

    try:
        held["link"] = linked_server.start(HOME, server.storage, ADMIN, dictation=word_store)
    except Exception:
        logging.getLogger("linked").exception("linked-server.json could not be used")

    def account_status():
        link = held.get("link")
        return link.account() if link else {"linked": False}

    def account_sign_in(url, email, password):
        """Sign this PC in with its own pass. Meetings, notes and dictation use it
        from the next request; nothing has to restart."""
        name = f"Notewell on {os.environ.get('COMPUTERNAME') or 'this PC'}"
        new_pass = linked_server.sign_in(HOME, url, email, password, name)
        keys = prepare_recorder_config(port, key)        # re-reads linked-server.json
        recorder.KEYS.update(keys)
        cfg = held.get("cfg")
        if cfg is not None:                              # the recorder's live settings
            cfg.update(keys, api_url=linked_server._https(url), fallback_api_url=f"http://127.0.0.1:{port}")
        if held.get("link"):
            held["link"].set_key(new_pass, url)
        else:
            held["link"] = linked_server.start(HOME, server.storage, ADMIN, dictation=word_store)
        logging.getLogger("linked").info("signed in to %s as %s", held["link"].url, email)
        return account_status()

    def _link():
        link = held.get("link")
        if link is None:
            raise recorder_bridge.NotSignedIn()
        return link

    recorder_bridge.attach_account(status=account_status, sign_in=account_sign_in,
                                   recordings=lambda: _link().recordings(),
                                   change_recording=lambda rid, changes: _link().change_recording(rid, changes))

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

    def start_dictation(ctl):
        """Hold-to-talk dictation (windows-recorder/dictation.py). A fault here must
        never stop meetings being recorded, so it is caught and logged."""
        try:
            import dictation, dictation_store, settings_store
            svc = dictation.DictationService(
                ctl["cfg"], ctl["save_cfg"], dictation_store.Store(HOME / "data" / "dictation.db"),
                get_ai=settings_store.ai, models_dir=HOME / "recorder" / "models")
            held["dictation"] = svc
            recorder_bridge.attach_dictation(svc)
            svc.start()
        except Exception:
            logging.getLogger("dictation").exception("dictation could not start")

    hooks = {}
    try:                                          # screen recording (windows-recorder/screen_record.py)
        import screen_record
        screen = screen_record.ScreenService(
            HOME, child_command("--screen-record"), get_link=lambda: held.get("link"),
            notify=lambda title, msg: hooks["notify"](title, msg) if hooks.get("notify") else None,
            flash=_screen_flash)
        hooks["screen"] = screen
        recorder_bridge.attach_screen(screen)
    except Exception:
        screen = None
        logging.getLogger("screen").exception("screen recording could not be set up")

    def on_ready(ctl):
        held["manager"], held["cfg"] = ctl["manager"], ctl["cfg"]
        recorder_bridge.attach(**ctl)
        start_dictation(ctl)
        if screen is not None:                    # a fault here never stops meetings
            try:
                screen.cfg = ctl["cfg"]
                screen.start()
            except Exception:
                logging.getLogger("screen").exception("screen recording could not start")
        # Imported audio files use the recorder's model: one copy in memory, one download.
        server._get_whisper = lambda: recorder.get_whisper(ctl["cfg"])
        threading.Thread(target=warm_up, args=(ctl,), daemon=True).start()
        for _ in range(200):                      # the server takes a second or two
            if srv.started:
                break
            time.sleep(0.1)
        open_dashboard()

    hooks.update({"on_ready": on_ready, "open_dashboard": open_dashboard,
                  "open_settings": lambda: open_window(
                      f"http://127.0.0.1:{port}/#token={server.tokens.issue(ADMIN)}&view=settings")})
    recorder.main(hooks=hooks)

    # Quit was chosen. A meeting that was just stopped is still being transcribed
    # and sent to the server: finish that before the process ends. A screen
    # recording in progress is stopped and saved; it uploads on the next start.
    if screen is not None:
        screen.shutdown()
    if held.get("dictation"):
        held["dictation"].stop()
    mgr = held.get("manager")
    while mgr is not None and mgr.processing:
        time.sleep(1)
    close_windows()
    srv.should_exit = True


if __name__ == "__main__":
    if "--window" in sys.argv:                    # the app window, started by open_window
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import window
        sys.exit(window.main())
    if "--screen-flash" in sys.argv:              # a moment of colour on screen ("Link copied" ...)
        sys.path[:0] = [str(ROOT / "windows-recorder")]
        import screen_record
        sys.exit(screen_record.flash_main(sys.argv[sys.argv.index("--screen-flash") + 1]))
    if "--sound-check" in sys.argv:               # 2 minutes: is captured sound being lost?
        sys.path[:0] = [str(ROOT / "windows-recorder")]
        import soundgaps
        HOME.mkdir(parents=True, exist_ok=True)
        with open(HOME / "sound-check.txt", "a", encoding="utf-8") as out:
            out.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} sound check, 2 minutes of a faint test tone\n")
            soundgaps.tone_check(say=lambda line: (out.write(line + "\n"), out.flush()))
        sys.exit(0)
    if "--screen-record" in sys.argv:             # one screen recording, started by ScreenService
        sys.path[:0] = [str(ROOT / "windows-recorder")]
        (HOME / "recorder").mkdir(parents=True, exist_ok=True)
        import logfiles                           # 5 MB, 3 old copies kept
        logging.basicConfig(level=logging.INFO, handlers=[logfiles.handler(HOME / "recorder" / "screen.log")],
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        import screen_record
        try:
            sys.exit(screen_record.child_main(os.environ["NOTEWELL_SCREEN_DIR"]))
        except Exception:
            logging.getLogger("screen").exception("screen recording failed")
            sys.exit(1)
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
