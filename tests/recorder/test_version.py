r"""The version in the tray and on every meeting sent (chunk 08, step 8).

Run:  python tests/recorder/test_version.py

The desktop app sets recorder.APP_VERSION. The tray tooltip then reads "Notewell 2.7.0"
(was "Notewell") and each meeting upload carries X-Notewell-Version. The standalone
recorder, which has no version set, behaves as before.
"""
import http.server, json, os, sys, tempfile, threading
from pathlib import Path

os.environ["MEETING_RECORDER_HOME"] = tempfile.mkdtemp(prefix="nw-version-")
APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "windows-recorder"))
import recorder as R

_passed, _failed = 0, []


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


class Server(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Server.seen.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"session_id": "s1", "status": "queued"}).encode())

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=srv.serve_forever, daemon=True).start()
CFG = {"api_url": f"http://127.0.0.1:{srv.server_port}", "api_key": "not-a-real-key"}


def t_tray_shows_version():
    R.APP_VERSION = "9.8.7"
    assert R.tray_tip() == "Notewell 9.8.7"
    src = (APP / "windows-recorder" / "recorder.py").read_text(encoding="utf-8")
    assert 'pystray.Icon("notewell", icon_idle, tray_tip(), menu)' in src


def t_upload_says_version():
    R.APP_VERSION = "9.8.7"
    R.upload("[00:00] [Me] hello", 5, CFG)
    h = Server.seen[-1]
    assert h.get("X-Notewell-Version") == "9.8.7" and h.get("X-API-Key") == "not-a-real-key", h


def t_standalone_unchanged():
    R.APP_VERSION = ""
    assert R.tray_tip() == "Notewell"
    R.upload("[00:00] [Me] hello", 5, CFG)
    assert "X-Notewell-Version" not in Server.seen[-1]


check("the tray tooltip shows the version", t_tray_shows_version)
check("each meeting sent to the server says which version sent it", t_upload_says_version)
check("with no version set (standalone recorder) nothing changes", t_standalone_unchanged)

srv.shutdown()
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
