"""Local browser bridge tests (windows-recorder/screen_bridge.py).

    python tests/recorder/test_screen_bridge.py

Uses loopback only (127.0.0.1) and made-up data; never touches real Chrome or real servers.
"""
import http.client
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "windows-recorder"))
import screen_bridge as sb  # noqa: E402
import screen_events as se  # noqa: E402
import screen_record as sr  # noqa: E402

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


def tmp():
    return Path(tempfile.mkdtemp(prefix="nw-bridge-"))


def req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


# -------------------------------------------------------------------- clean_url
print("\nclean_url parity")


def t_clean_url_cases():
    cases_file = ROOT / "tests" / "extension" / "clean_url.test.txt"
    checked = 0
    for line in cases_file.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        raw, expected = line.split("\t", 1)
        got = sb.clean_url(raw)
        assert got == expected, (raw, got, expected)
        checked += 1
    assert checked >= 20, checked


check("clean_url matches every case in tests/extension/clean_url.test.txt", t_clean_url_cases)

# ------------------------------------------------------------------- HTTP bridge
print("\nBridge HTTP and validation")


def t_hello_origin_and_lifecycle():
    d = tmp()
    ev_path = d / "out.events.jsonl"
    w = se.Watcher(ev_path, (0, 0, 1920, 1080), lambda: 5000, (1920, 1080), lambda e, t: False, probe=None)
    w.start()
    b = sb.Bridge(d, lambda: 5000, w.write_event)
    port = b.start()
    try:
        assert port in sb.BRIDGE_PORTS, port
        bp = d / "bridge.json"
        assert bp.exists()
        info = json.loads(bp.read_text(encoding="utf-8"))
        assert info["port"] == port and info["secret"] == b.secret and len(b.secret) >= 24

        # Wrong / missing Origin on /hello -> 403, no secret
        for bad_hdrs in ({}, {"Origin": "https://evil.example"}, {"Origin": "chrome-extension://wrongid"}):
            code, _h, raw = req(port, "GET", "/hello", headers=bad_hdrs)
            assert code == 403 and b.secret.encode() not in raw, (bad_hdrs, code, raw)

        # Exact extension Origin -> 200 + secret + CORS
        code, hdrs, raw = req(port, "GET", "/hello", headers={"Origin": sb.EXTENSION_ORIGIN})
        assert code == 200 and json.loads(raw) == {"secret": b.secret}, (code, raw)
        assert hdrs.get("Access-Control-Allow-Origin") == sb.EXTENSION_ORIGIN, hdrs

        # OPTIONS preflight for extension Origin -> 204
        code, hdrs, _ = req(port, "OPTIONS", "/events", headers={"Origin": sb.EXTENSION_ORIGIN})
        assert code == 204 and hdrs.get("Access-Control-Allow-Origin") == sb.EXTENSION_ORIGIN

        # Unknown path -> 404
        code, _, _ = req(port, "GET", "/other")
        assert code == 404, code
        code, _, _ = req(port, "POST", "/other", body=b"[]")
        assert code == 404, code
    finally:
        b.stop()
        w.stop()
    assert not (d / "bridge.json").exists() and (d / "bridge.old.json").exists()


def t_post_events_good_scrubbed_and_rejected():
    d = tmp()
    ev_path = d / "ev.events.jsonl"
    now = {"t": 4200}
    w = se.Watcher(ev_path, (0, 0, 1920, 1080), lambda: now["t"], (1920, 1080), lambda e, t: False, probe=None)
    w.start()
    b = sb.Bridge(d, lambda: now["t"], w.write_event)
    port = b.start()
    good_hdrs = {"Content-Type": "application/json", "X-Notewell-Bridge": b.secret, "Origin": sb.EXTENSION_ORIGIN}
    try:
        # Wrong secret -> 403
        code, _, _ = req(port, "POST", "/events", body=b"[]",
                         headers={"Content-Type": "application/json", "X-Notewell-Bridge": "wrong"})
        assert code == 403, code

        # Good lines: query value on page must be stored as *, age_ms subtracted from clock
        batch = [
            {"k": "page", "url": "https://example.com/search?q=secret&page=2#frag", "title": "Example\x07 Tab", "age_ms": 200},
            {"k": "console", "level": "error", "message": "TypeError: x is undefined", "age_ms": 50},
            {"k": "request", "method": "get", "url": "https://example.com/api?token=abc", "status": 500, "ms": 812, "age_ms": 5000},
        ]
        code, _, raw = req(port, "POST", "/events", body=json.dumps(batch).encode("utf-8"), headers=good_hdrs)
        assert code == 200 and json.loads(raw) == {"accepted": 3}, (code, raw)

        # Header-bearing line -> 400 (rejected, nothing written)
        bad_batches = [
            [{"k": "request", "method": "GET", "url": "https://example.com/a", "status": 500, "ms": 10,
              "headers": {"Authorization": "Bearer secret"}}],
            [{"k": "window", "app": "chrome.exe", "title": "Not allowed from bridge"}],
            [{"k": "console", "level": "debug", "message": "unknown level"}],
            [{"k": "console", "level": "error", "message": "   "}],
            [{"k": "page", "url": "javascript:alert(1)", "title": "bad"}],
            [{"k": "request", "method": "GET", "url": "https://example.com/a", "status": True, "ms": 10}],
            [{"k": "console", "level": "info", "message": "x"}] * 201,
        ]
        for bad in bad_batches:
            code, _, _ = req(port, "POST", "/events", body=json.dumps(bad).encode("utf-8"), headers=good_hdrs)
            assert code == 400, (bad[0], code)

        # Too large (> 256 KB) -> 413
        big = b"[" + b" " * (sb.MAX_BODY_BYTES + 10) + b"]"
        code, _, _ = req(port, "POST", "/events", body=big, headers=good_hdrs)
        assert code == 413, code
    finally:
        b.stop()
        w.stop()

    rows = [json.loads(ln) for ln in ev_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5, rows  # header + 3 browser events + end
    assert rows[1] == {"t": 4000, "k": "page", "url": "https://example.com/search?q=*&page=*", "title": "Example Tab"}, rows[1]
    assert rows[2] == {"t": 4150, "k": "console", "level": "error", "message": "TypeError: x is undefined"}, rows[2]
    assert rows[3] == {"t": 0, "k": "request", "method": "GET", "url": "https://example.com/api?token=*", "status": 500, "ms": 812}, rows[3]
    assert rows[4]["k"] == "end" and rows[4]["lines"] == 4, rows[4]


def t_start_stop_bridge_safe():
    class BrokenBox:
        @property
        def root(self):
            raise RuntimeError("boom")

    class BrokenBridge:
        def stop(self):
            raise RuntimeError("stop boom")

    assert sr.start_bridge(BrokenBox(), object(), object(), test_mode=True) is None
    assert sr.start_bridge(BrokenBox(), object(), object(), test_mode=False) is None
    sr.stop_bridge(None)
    sr.stop_bridge(BrokenBridge())


check("/hello checks exact extension Origin and bridge.json moves to bridge.old.json on stop", t_hello_origin_and_lifecycle)
check("/events accepts valid scrubbed lines, rejects wrong secret (403), bad/header lines (400), >256 KB (413)", t_post_events_good_scrubbed_and_rejected)
check("start_bridge and stop_bridge never raise and skip test_mode", t_start_stop_bridge_safe)

print(f"\n{_passed} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)
