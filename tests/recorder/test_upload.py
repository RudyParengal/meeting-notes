r"""The recorder's upload() against real HTTP servers, including every way an
upload can silently fail. A meeting reported as "uploaded" that the server never
received is the worst failure this system can have, so each one is reproduced.

    python tests/recorder/test_upload.py

Starts small throw-away HTTP servers on random local ports. No real server needed.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["MEETING_RECORDER_HOME"] = tempfile.mkdtemp(prefix="mr-upload-")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import recorder

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


def serve(behaviour):
    """Start a server whose POST /transcribe does `behaviour(handler)`. Returns (url, stop)."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            behaviour(self)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv.shutdown


def reply(h, code, body, ctype="application/json", extra=None):
    raw = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(raw)))
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(raw)


def try_upload(behaviour):
    url, stop = serve(behaviour)
    try:
        cfg = {**recorder.DEFAULT_CONFIG, "api_url": url, "api_key": "mn_" + "cd" * 24}
        return recorder.upload("[00:00] a long enough transcript to be accepted by the server", 120, cfg)
    finally:
        stop()


def must_reject(behaviour, expect):
    try:
        try_upload(behaviour)
    except Exception as e:
        assert expect.lower() in str(e).lower(), f"rejected, but for the wrong reason: {e}"
        return
    raise AssertionError("the upload was reported as successful - the meeting would have been lost")


print("\nA good upload")


def t_good():
    sent = {}

    def ok(h):
        sent["key"] = h.headers.get("X-API-Key")
        reply(h, 202, {"session_id": "ab" * 16, "status": "accepted"})

    assert try_upload(ok) == "ab" * 16
    assert sent["key"] == "mn_" + "cd" * 24, "the API key was not sent"


check("a 202 with a session id is accepted, and the key is sent", t_good)

print("\nEvery way it can fail silently (each must be reported as a failure)")
check("a redirect to a login page (the classic: it returns 200 after following)",
      lambda: must_reject(lambda h: reply(h, 302, b"", extra={"Location": "/login"}), "redirect"))
check("a 200 that is an HTML login page, not the API",
      lambda: must_reject(lambda h: reply(h, 200, "<html><title>Sign in</title></html>", "text/html"), "not JSON"))
check("a 200 JSON with no session id",
      lambda: must_reject(lambda h: reply(h, 200, {}), "session_id"))
check("a wrong API key (401)",
      lambda: must_reject(lambda h: reply(h, 401, {"detail": "invalid or missing credentials"}), "401"))
check("a server error (500)",
      lambda: must_reject(lambda h: reply(h, 500, {"detail": "boom"}), "500"))


def t_down():
    cfg = {**recorder.DEFAULT_CONFIG, "api_url": "http://127.0.0.1:9", "api_key": "mn_" + "cd" * 24}
    try:
        recorder.upload("[00:00] a long enough transcript to be accepted", 120, cfg)
    except Exception:
        return
    raise AssertionError("an unreachable server was reported as success")


check("a server that is not running", t_down)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
