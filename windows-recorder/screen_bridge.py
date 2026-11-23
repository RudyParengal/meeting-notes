"""Local HTTP bridge for the Notewell Chrome extension (Phase 3 step B1).

Runs only on 127.0.0.1 (first free port of 47811..47815) while a screen recording is active.
Writes port and one-use random secret to <screen_root>/bridge.json on start and moves it to
bridge.old.json on stop. Never logs or prints the secret.

POST (or GET) /hello returns {"secret": "..."} ONLY when Origin equals EXTENSION_ORIGIN.
The helper uses POST, because Chrome sends no Origin header on an extension's GET.
POST /events requires header X-Notewell-Bridge matching the secret, body <= 256 KB, JSON list
of <= 200 validated browser events (kinds page, console, request only).
"""
import hmac
import http.server
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

log = logging.getLogger("screen")

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORTS = (47811, 47812, 47813, 47814, 47815)
EXTENSION_ID = "pfaojjicmeaiddcpdjdanoikppjeocfa"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
MAX_BODY_BYTES = 256 * 1024
MAX_BATCH_LINES = 200
URL_MAX = 500
TITLE_MAX = 300
MSG_MAX = 500
T_MAX = 24 * 3600 * 1000
MS_MAX = 600_000
ALLOWED_LEVELS = frozenset(("log", "info", "warn", "error"))
_ALLOWED_KEYS = {
    "page": frozenset(("k", "url", "title", "age_ms")),
    "console": frozenset(("k", "level", "message", "age_ms")),
    "request": frozenset(("k", "method", "url", "status", "ms", "age_ms")),
}


def _strip_ctrl(text):
    return "".join(ch for ch in str(text) if ord(ch) >= 0x20 and ord(ch) != 0x7F)


def clip_text(text, max_len):
    if text is None:
        return ""
    return " ".join(_strip_ctrl(text).split())[:max_len]


def clean_url(raw):
    """Keep http/https scheme, host and path; replace query values with *; drop fragment and userinfo."""
    if type(raw) is not str or not raw:
        return ""
    try:
        u = urlsplit(raw)
    except ValueError:
        return ""
    scheme = (u.scheme or "").lower()
    if scheme not in ("http", "https") or not u.hostname:
        return ""
    host = u.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = u.port
    except ValueError:
        return ""
    default_port = 80 if scheme == "http" else 443
    netloc = f"{host}:{port}" if (port is not None and port != default_port) else host
    path = u.path or "/"
    base = _strip_ctrl(f"{scheme}://{netloc}{path}")
    if not base:
        return ""
    if len(base) >= URL_MAX:
        return base[:URL_MAX]
    if not u.query:
        return base
    keys = []
    for k, _v in parse_qsl(u.query, keep_blank_values=True):
        ck = _strip_ctrl(k)
        if ck:
            keys.append(quote(ck, safe="") + "=*")
    if not keys:
        return base
    out = base
    for idx, item in enumerate(keys):
        piece = ("?" if idx == 0 else "&") + item
        if len(out) + len(piece) > URL_MAX:
            break
        out += piece
    return out


def validate_event(item, now_ms):
    """Validate one browser event dict. Returns a clean event dict with `t`, or None if invalid."""
    if type(item) is not dict:
        return None
    k = item.get("k")
    if type(k) is not str or k not in _ALLOWED_KEYS:
        return None
    if set(item.keys()) - _ALLOWED_KEYS[k]:
        return None
    age = item.get("age_ms", 0)
    if type(age) is not int or not (0 <= age <= T_MAX):
        return None
    t = max(0, min(T_MAX, int(now_ms) - age))

    if k == "page":
        if "url" not in item or "title" not in item:
            return None
        if type(item["url"]) is not str or type(item["title"]) is not str:
            return None
        url = clean_url(item["url"])
        if not url:
            return None
        return {"t": t, "k": "page", "url": url, "title": clip_text(item["title"], TITLE_MAX)}

    if k == "console":
        if "level" not in item or "message" not in item:
            return None
        lvl, msg = item["level"], item["message"]
        if type(lvl) is not str or lvl not in ALLOWED_LEVELS or type(msg) is not str:
            return None
        clean_msg = clip_text(msg, MSG_MAX)
        if not clean_msg:
            return None
        return {"t": t, "k": "console", "level": lvl, "message": clean_msg}

    if k == "request":
        for req in ("method", "url", "status", "ms"):
            if req not in item:
                return None
        method, raw_url, status, ms = item["method"], item["url"], item["status"], item["ms"]
        if type(method) is not str or type(raw_url) is not str:
            return None
        clean_method = method.strip().upper()
        if not (1 <= len(clean_method) <= 16) or not clean_method.isascii() or not clean_method.isalpha():
            return None
        if type(status) is not int or not (0 <= status <= 599):
            return None
        if type(ms) is not int or not (0 <= ms <= MS_MAX):
            return None
        url = clean_url(raw_url)
        if not url:
            return None
        return {"t": t, "k": "request", "method": clean_method, "url": url, "status": status, "ms": ms}
    return None


class _BridgeHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows, True lets two programs share a port
    daemon_threads = True


class Bridge:
    """Short-lived localhost bridge active only while a recording is in progress."""

    def __init__(self, screen_root, clock, write_event, ports=BRIDGE_PORTS, origin=EXTENSION_ORIGIN):
        self.screen_root = Path(screen_root)
        self.clock = clock
        self.write_event = write_event
        self.ports = tuple(ports)
        self.origin = origin
        self.port = None
        self.secret = None
        self._srv = None
        self._thread = None
        self._stopped = False

    def _bridge_file(self):
        return self.screen_root / "bridge.json"

    def _old_file(self):
        return self.screen_root / "bridge.old.json"

    def start(self):
        self.secret = secrets.token_urlsafe(24)
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def _send_json(self, code, payload, cors_origin=None):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                if cors_origin:
                    self.send_header("Access-Control-Allow-Origin", cors_origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                if self.path not in ("/hello", "/events"):
                    self._send_json(404, {"error": "not found"})
                    return
                if self.headers.get("Origin") != bridge.origin:
                    self._send_json(403, {"error": "forbidden"})
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", bridge.origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Notewell-Bridge")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self):
                if self.path != "/hello":
                    self._send_json(404, {"error": "not found"})
                    return
                if self.headers.get("Origin") != bridge.origin:
                    self._send_json(403, {"error": "forbidden"})
                    return
                self._send_json(200, {"secret": bridge.secret}, cors_origin=bridge.origin)

            def _drain(self):
                """Drain modest request bodies before an early error so Windows does not TCP-RST."""
                try:
                    rem = min(max(0, int(self.headers.get("Content-Length") or 0)), 2 * 1024 * 1024)
                except ValueError:
                    rem = 0
                while rem > 0:
                    chunk = self.rfile.read(min(rem, 16384))
                    if not chunk:
                        break
                    rem -= len(chunk)
                self.close_connection = True

            def do_POST(self):
                if self.path == "/hello":
                    # The helper asks with POST: Chrome sends no Origin on its GET (measured 23 Nov).
                    self._drain()
                    if self.headers.get("Origin") != bridge.origin:
                        self._send_json(403, {"error": "forbidden"})
                        return
                    self._send_json(200, {"secret": bridge.secret}, cors_origin=bridge.origin)
                    return
                if self.path != "/events":
                    self._drain()
                    self._send_json(404, {"error": "not found"})
                    return
                req_origin = self.headers.get("Origin")
                if req_origin and req_origin != bridge.origin:
                    self._drain()
                    self._send_json(403, {"error": "forbidden"})
                    return
                cors = bridge.origin if req_origin == bridge.origin else None
                got = self.headers.get("X-Notewell-Bridge") or ""
                if not hmac.compare_digest(got, bridge.secret):
                    self._drain()
                    self._send_json(403, {"error": "forbidden"}, cors_origin=cors)
                    return
                raw_len = self.headers.get("Content-Length")
                try:
                    clen = int(raw_len) if raw_len is not None else -1
                except ValueError:
                    self._send_json(400, {"error": "bad length"}, cors_origin=cors)
                    return
                if clen > MAX_BODY_BYTES:
                    self._drain()
                    self._send_json(413, {"error": "too large"}, cors_origin=cors)
                    return
                if clen < 0:
                    self._send_json(400, {"error": "length required"}, cors_origin=cors)
                    return
                raw = self.rfile.read(min(clen, MAX_BODY_BYTES + 1))
                if len(raw) > MAX_BODY_BYTES:
                    self._send_json(413, {"error": "too large"}, cors_origin=cors)
                    return
                try:
                    batch = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    self._send_json(400, {"error": "bad json"}, cors_origin=cors)
                    return
                if type(batch) is not list or len(batch) > MAX_BATCH_LINES:
                    self._send_json(400, {"error": "bad batch"}, cors_origin=cors)
                    return
                now_ms = max(0, int(bridge.clock()))
                clean = []
                for item in batch:
                    ev = validate_event(item, now_ms)
                    if ev is None:
                        self._send_json(400, {"error": "bad event"}, cors_origin=cors)
                        return
                    clean.append(ev)
                written = 0
                for ev in clean:
                    if bridge.write_event(ev):
                        written += 1
                self._send_json(200, {"accepted": written}, cors_origin=cors)

        last_err = None
        for port in self.ports:
            try:
                srv = _BridgeHTTPServer((BRIDGE_HOST, port), Handler)
                self._srv, self.port = srv, port
                break
            except OSError as e:
                last_err = e
        if self._srv is None:
            raise OSError(f"no free bridge port in {self.ports}: {last_err}")

        self.screen_root.mkdir(parents=True, exist_ok=True)
        bp = self._bridge_file()
        tmp = bp.with_suffix(".json.new")
        tmp.write_text(json.dumps({"port": self.port, "secret": self.secret}), encoding="utf-8")
        os.replace(tmp, bp)

        self._thread = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.1},
                                        daemon=True, name="screen-bridge")
        self._thread.start()
        return self.port

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        try:
            if self._srv is not None:
                self._srv.shutdown()
                self._srv.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
        except Exception:
            log.exception("screen bridge did not stop cleanly")
        try:
            bp = self._bridge_file()
            if bp.exists():
                os.replace(bp, self._old_file())
        except OSError:
            log.warning("could not move bridge.json aside")
