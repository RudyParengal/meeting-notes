#!/usr/bin/env python3
"""Checks that a Meeting Notes install actually works, link by link.

    python scripts/doctor.py                    # server + recorder, using your saved settings
    python scripts/doctor.py --e2e              # also push a test meeting all the way through
    python scripts/doctor.py --server http://127.0.0.1:8766 --key mn_...
    python scripts/doctor.py --json             # machine-readable, for scripts and AI agents

Exit code 0 = nothing failed. Anything else = at least one FAIL.

Why this exists: "the service started" and "the file exists" have each been wrong
about whether the system works. Every check here exercises the real thing - an
authenticated request, a real upload, a real heartbeat - and says what it saw.

Uses only the Python standard library, so it runs anywhere Python does.
Never prints a key.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = []
SEEN_KEYS = set()


def record(status, name, detail=""):
    RESULTS.append({"status": status, "name": name, "detail": detail})
    if not ARGS.json:
        color = {"PASS": "32", "FAIL": "31", "WARN": "33", "SKIP": "90"}.get(status, "0")
        mark = f"\033[{color}m{status}\033[0m" if sys.stdout.isatty() else status
        print(f"[{mark}] {name}" + (f"  -  {detail}" if detail else ""))


def mask(text):
    """Remove any key we have seen from anything about to be printed."""
    for k in SEEN_KEYS:
        if k and len(k) > 8:
            text = text.replace(k, k[:3] + "***")
    return text


# --------------------------------------------------------------------- http ---
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def call(method, url, key=None, body=None, timeout=20):
    """Returns (status, headers, text). Never follows redirects: a redirect to a
    login page that answers 200 is how an upload gets silently lost."""
    headers = {"User-Agent": "meeting-notes-doctor"}
    data = None
    if key:
        headers["X-API-Key"] = key
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
    except Exception as e:                                # connection refused, DNS, timeout, TLS
        return 0, {}, f"{type(e).__name__}: {e}"


def as_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


# ----------------------------------------------------------------- discovery ---
def recorder_home():
    return Path(os.environ.get("MEETING_RECORDER_HOME") or (Path.home() / "MeetingRecorder"))


def read_env_file(path):
    out = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    except OSError:
        pass
    return out


def load_recorder_config():
    p = recorder_home() / "config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def find_connection():
    server = ARGS.server or os.environ.get("MN_SERVER")
    key = ARGS.key or os.environ.get("MN_API_KEY")
    source = "command line / environment" if (ARGS.server or os.environ.get("MN_SERVER")) else None
    cfg = load_recorder_config()
    if not server and cfg and cfg.get("api_url"):
        server, source = cfg["api_url"], "recorder config.json"
    if not key and cfg and cfg.get("api_key"):
        key = cfg["api_key"]
    if not key:
        env = read_env_file(REPO / "backend" / ".env")
        key = env.get("MEETING_NOTES_API_KEY") or None
    if not server:
        server = "http://127.0.0.1:8766"
    if key:
        SEEN_KEYS.add(key)
    return server.rstrip("/"), key, source


# ------------------------------------------------------------------- server ---
def check_server(server, key):
    status, headers, text = call("GET", f"{server}/health")
    if status == 0:
        record("FAIL", "server reachable", mask(text) + ". Is it running? (python backend/main.py)")
        return False
    body = as_json(text)
    if status in (301, 302, 303, 307, 308):
        record("FAIL", "server reachable",
               f"redirected to {headers.get('Location', '?')[:80]} - a login page or proxy is in front of the "
               f"API. The recorder would think uploads worked when they did not. Let API requests through.")
        return False
    if status != 200 or not isinstance(body, dict) or body.get("status") != "ok":
        record("FAIL", "server reachable", f"HTTP {status} but not the Meeting Notes API (got {text[:60]!r}...)")
        return False
    record("PASS", "server reachable", f"{server} answers /health")

    if server.startswith("http://") and not re.match(r"^http://(127\.|localhost|\[::1\])", server):
        record("WARN", "transport encryption",
               "plain http:// to another computer sends your key and meetings unencrypted. Use HTTPS.")
    elif server.startswith("https://"):
        record("PASS", "transport encryption", "HTTPS")

    # Security: an open server is the worst possible state. Prove it is closed.
    s_none, _, _ = call("GET", f"{server}/sessions")
    s_bad, _, _ = call("GET", f"{server}/sessions", key="mn_" + "0" * 48)
    if s_none == 401 and s_bad == 401:
        record("PASS", "refuses strangers", "no key and a wrong key are both rejected (401)")
    else:
        record("FAIL", "refuses strangers",
               f"/sessions answered {s_none} with no key and {s_bad} with a wrong key - expected 401. "
               f"Do NOT expose this server until fixed.")

    if not key:
        record("SKIP", "authenticated checks", "no API key found (pass --key or set MN_API_KEY)")
        return True
    status, _, text = call("GET", f"{server}/health/deep", key=key)
    if status == 401:
        record("FAIL", "API key accepted", "the server rejected this key. Check MEETING_NOTES_API_KEY in "
                                           "backend/.env matches the recorder's api_key.")
        return False
    deep = as_json(text)
    if status != 200 or not isinstance(deep, dict):
        record("FAIL", "API key accepted", f"HTTP {status}: {mask(text)[:100]}")
        return False
    record("PASS", "API key accepted", f"server version {deep.get('version')}")
    record("PASS" if deep.get("storage_writable") else "FAIL", "storage writable",
           "notes folder can be written" if deep.get("storage_writable") else "the notes folder is not writable")
    for stage, info in (deep.get("llm") or {}).items():
        record("PASS", f"AI step: {stage}", f"{info.get('model')} @ {info.get('base_url')}")
    record("PASS" if deep.get("audio_upload") else "SKIP", "server-side audio transcription",
           "installed" if deep.get("audio_upload") else "not installed (optional)")
    return True


def check_e2e(server, key):
    """A real meeting through the real pipeline, then cleaned up."""
    if not key:
        record("SKIP", "end-to-end test", "needs an API key")
        return
    transcript = ("[00:00] [Me] Let's decide who sends the proposal.\n"
                  "[00:06] [Speaker 1] I will send it on Friday and copy the whole team.\n"
                  "[00:12] [Me] Great. We also need to book the demo for next week.\n")
    t0 = time.time()
    status, _, text = call("POST", f"{server}/transcribe", key=key,
                           body={"transcript": transcript, "duration_sec": 90, "meeting_type": "auto"})
    body = as_json(text)
    if status != 202 or not isinstance(body, dict) or not body.get("session_id"):
        record("FAIL", "end-to-end: upload", f"HTTP {status}: {mask(text)[:120]}")
        return
    sid = body["session_id"]
    record("PASS", "end-to-end: upload", "202 accepted with a session id (JSON, not a login page)")

    result = None
    while time.time() - t0 < ARGS.e2e_timeout:
        status, _, text = call("GET", f"{server}/results/{sid}", key=key)
        result = as_json(text)
        if isinstance(result, dict) and result.get("status") in ("complete", "failed"):
            break
        time.sleep(2)
    took = time.time() - t0
    try:
        if not isinstance(result, dict) or result.get("status") != "complete":
            record("FAIL", "end-to-end: processing",
                   f"not finished after {took:.0f}s (status: {result and result.get('status')}, "
                   f"error: {result and result.get('error')})")
            return
        summary, actions = result.get("summary", ""), result.get("actions", "")
        record("PASS" if len(summary) > 40 and "failed" not in summary[:60].lower() else "FAIL",
               "end-to-end: summary", f"{len(summary)} characters in {took:.0f}s")
        if "unavailable" in actions.lower() or "failed" in actions[:60].lower():
            record("FAIL", "end-to-end: action items", mask(actions)[:200])
        else:
            record("PASS", "end-to-end: action items", f"{len(actions)} characters")
    finally:                                              # never leave a test meeting behind
        call("DELETE", f"{server}/results/{sid}", key=key)
        call("DELETE", f"{server}/results/{sid}/purge", key=key)


# ----------------------------------------------------------------- recorder ---
def age(path):
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def check_recorder():
    home = recorder_home()
    if os.name != "nt":
        record("SKIP", "recorder", "the recorder is Windows-only; nothing to check on this OS")
        return
    cfg_path = home / "config.json"
    cfg = load_recorder_config()
    if cfg is None:
        record("FAIL", "recorder config", f"{cfg_path} is missing or not valid JSON. Run windows-recorder\\install.ps1")
        return
    SEEN_KEYS.add(cfg.get("api_key", ""))
    key = (cfg.get("api_key") or "").strip()
    if not key or key.upper().startswith("PASTE") or len(key) < 16:
        record("FAIL", "recorder config", "no usable api_key in config.json")
    else:
        record("PASS", "recorder config", f"{cfg_path} (key present, not shown)")
    url = cfg.get("api_url", "")
    if url.startswith("http://") and not re.match(r"^http://(127\.|localhost|\[::1\])", url) and not cfg.get("allow_insecure_http"):
        record("FAIL", "recorder server address", "plain http:// to another computer - the recorder will refuse to start")

    if home.resolve() != (Path.home() / "MeetingRecorder").resolve() and not os.environ.get("MEETING_RECORDER_HOME"):
        record("WARN", "data folder", f"{home} - expected {Path.home() / 'MeetingRecorder'}")

    # liveness: are the heartbeat files fresh?
    for name, limit, what in (("recorder.alive", 120, "recorder"), ("supervisor.alive", 120, "supervisor")):
        a = age(home / name)
        if a is None:
            record("FAIL", f"{what} running", f"no {name}. It is not running. Start it: "
                                              f"explorer.exe \"{REPO / 'windows-recorder' / 'start-supervisor.vbs'}\"")
        elif a > limit:
            record("FAIL", f"{what} running", f"{name} is {a:.0f}s old (should be under {limit}s) - it has stopped")
        else:
            record("PASS", f"{what} running", f"heartbeat {a:.0f}s ago")

    log = home / "recorder.log"
    try:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except OSError:
        tail = []
    started = [l for l in tail if "startup: complete" in l]
    errors = [l for l in tail if " ERROR " in l]
    if started:
        record("PASS", "recorder started cleanly", started[-1][:19] + "  startup: complete")
    else:
        record("FAIL", "recorder started cleanly", f"no 'startup: complete' in {log}")
    if errors:
        record("WARN", "recent errors in recorder.log", mask(errors[-1])[:160])

    sup = home / "supervisor.log"
    try:
        last = sup.read_text(encoding="utf-8", errors="replace").splitlines()[-1:]
    except OSError:
        last = []
    if last and "CONFIG PROBLEM" in last[0]:
        record("FAIL", "supervisor", last[0][:160])
    elif last:
        record("PASS", "supervisor log", last[0][:120])

    lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/MeetingRecorder.lnk"
    record("PASS" if lnk.exists() else "WARN", "starts at login",
           "Startup shortcut present" if lnk.exists() else "no Startup shortcut - it will not start after a reboot")

    free = shutil.disk_usage(home).free / 1e9
    record("PASS" if free > 5 else "WARN", "disk space", f"{free:.0f} GB free (a 1-hour meeting uses ~0.5 GB while recording)")

    if cfg.get("diarize_others"):
        seg = REPO / "windows-recorder/models/diarization/sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
        emb = REPO / "windows-recorder/models/diarization/campplus_en_voxceleb_16k.onnx"
        record("PASS" if seg.exists() and emb.exists() else "FAIL", "speaker-separation models",
               "present" if seg.exists() and emb.exists() else "missing - run: python windows-recorder/fetch_models.py")

    if ARGS.audio:
        try:
            import soundcard as sc
            spk, mic = sc.default_speaker(), sc.default_microphone()
            record("PASS", "audio devices", f"speaker: {spk.name} | microphone: {mic.name}")
        except Exception as e:
            record("FAIL", "audio devices", f"{type(e).__name__}: {e}  (run with the recorder's own python: "
                                            f"windows-recorder\\venv\\Scripts\\python.exe)")


# --------------------------------------------------------------------- main ---
def main():
    global ARGS
    ap = argparse.ArgumentParser(description="Check a Meeting Notes install.")
    ap.add_argument("--server", help="server address (default: from the recorder config, else http://127.0.0.1:8766)")
    ap.add_argument("--key", help="API key (better: set MN_API_KEY so it is not in your shell history)")
    ap.add_argument("--recorder", action="store_true", help="only check the recorder")
    ap.add_argument("--server-only", action="store_true", help="only check the server")
    ap.add_argument("--e2e", action="store_true", help="push a test meeting through the whole pipeline")
    ap.add_argument("--audio", action="store_true", help="also list audio devices (use the recorder's python)")
    ap.add_argument("--e2e-timeout", type=int, default=180)
    ap.add_argument("--json", action="store_true")
    ARGS = ap.parse_args()

    server, key, source = find_connection()
    if not ARGS.json:
        print(f"Meeting Notes doctor  |  server: {server}" + (f"  (from {source})" if source else ""))
        print()
    if not ARGS.recorder:
        if check_server(server, key) and ARGS.e2e:
            check_e2e(server, key)
    if not ARGS.server_only and (ARGS.recorder or os.name == "nt"):
        check_recorder()

    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    warns = [r for r in RESULTS if r["status"] == "WARN"]
    if ARGS.json:
        print(json.dumps({"ok": not fails, "failures": len(fails), "warnings": len(warns),
                          "checks": [{**r, "detail": mask(r["detail"])} for r in RESULTS]}, indent=2))
    else:
        print()
        n_pass = sum(r["status"] == "PASS" for r in RESULTS)
        print(f"{n_pass} passed, {len(warns)} warnings, {len(fails)} FAILED")
        if fails:
            print("\nFix the FAIL lines above, top to bottom - later ones are often caused by earlier ones.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
