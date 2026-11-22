r"""Screen recording (windows-recorder/screen_record.py).

    python tests/recorder/test_screen_record.py

Never touches the real screen, microphone, clipboard or server: the recording
tests use FFmpeg's made-up colour bars and a 440 Hz tone, and the upload tests
use a pretend server in memory. Needs PyAV and numpy (both in the app).

SCREEN_RECORD_DIR=<folder> runs the same tests against another copy of
screen_record.py (used to prove the tests fail on the old code).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODDIR = os.environ.get("SCREEN_RECORD_DIR") or str(ROOT / "windows-recorder")
sys.path.insert(0, MODDIR)
sys.path.insert(1, str(ROOT / "windows-recorder"))      # dictation.py for the blocked-app list
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
    return Path(tempfile.mkdtemp(prefix="nw-screen-"))


# ---------------------------------------------------------------- never-record
print("\nNever-record list")


def t_password_manager_app():
    r = sr.never_record_reasons([("1Password.exe", "All items"), ("notepad.exe", "notes")],
                                blocked_apps={"1password.exe"})
    assert len(r) == 1 and "password manager" in r[0], r


def t_password_manager_in_browser_title():
    r = sr.never_record_reasons([("chrome.exe", "Bitwarden Web vault - Google Chrome")], blocked_apps=set())
    assert r and "password manager" in r[0], r


def t_banking_titles():
    for title in ("Barclays Online Banking - Google Chrome", "Приват24 — Microsoft Edge",
                  "monobank | Mozilla Firefox", "Log in to your PayPal account"):
        r = sr.never_record_reasons([("chrome.exe", title)], blocked_apps=set())
        assert r and "banking" in r[0], (title, r)


def t_ordinary_words_do_not_match():
    titles = ("Otherwise it works - Visual Studio Code", "Riverbank walk photos", "Wise words.docx",
              "Notewell", "Bankruptcy law notes.pdf", "")
    r = sr.never_record_reasons([("chrome.exe", t) for t in titles], blocked_apps=set())
    assert r == [], r


def t_same_reason_once():
    r = sr.never_record_reasons([("chrome.exe", "HSBC - Online banking")] * 3, blocked_apps=set())
    assert len(r) == 1, r


check("a password manager app is flagged", t_password_manager_app)
check("a password manager in a browser tab is flagged", t_password_manager_in_browser_title)
check("bank and payment pages are flagged (English and Ukrainian)", t_banking_titles)
check("ordinary titles that contain 'bank' or 'wise' are not flagged", t_ordinary_words_do_not_match)
check("the same warning is listed once", t_same_reason_once)

# ---------------------------------------------------------------------- outbox
print("\nOutbox")


def t_recover_finishes_or_moves_aside():
    box = sr.Outbox(tmp())
    had_video = box.new_job("a")
    box.video(had_video["client_id"]).write_bytes(b"x" * 100)
    empty = box.new_job("b")
    live = box.new_job("c")
    box.video(live["client_id"]).write_bytes(b"x")
    box.recover(live={live["client_id"]})
    states = {j["client_id"]: j["state"] for j in box.jobs()}
    assert states[had_video["client_id"]] == "ready", states
    assert states[live["client_id"]] == "recording", states       # still being written: left alone
    assert empty["client_id"] not in states, states
    assert (box.dir / "empty" / f"{empty['client_id']}.json").exists()   # moved, not deleted
    assert [j["client_id"] for j in box.waiting()] == [had_video["client_id"]]


def t_mark_sent_moves_both_files():
    box = sr.Outbox(tmp())
    j = box.new_job("a")
    box.video(j["client_id"]).write_bytes(b"x")
    box.events(j["client_id"]).write_text('{"v":1}\n', encoding="utf-8")
    box.mark_sent(j, "https://example.test/s/abc")
    assert not list(box.dir.glob("*.*")), list(box.dir.iterdir())
    meta = json.loads((box.sent / f"{j['client_id']}.json").read_text(encoding="utf-8"))
    assert meta["state"] == "sent" and meta["share_url"].endswith("/abc"), meta
    assert (box.sent / f"{j['client_id']}.mp4").exists()
    assert (box.sent / f"{j['client_id']}.events.jsonl").exists()


def t_move_aside_moves_all_three_files():
    box = sr.Outbox(tmp())
    j = box.new_job("a")
    box.video(j["client_id"]).write_bytes(b"x")
    box.events(j["client_id"]).write_text('{"v":1}\n', encoding="utf-8")
    box.move_aside(j, "failed")
    assert not list(box.dir.glob("*.*")), list(box.dir.iterdir())
    dest = box.dir / "failed"
    for suffix in (".mp4", ".json", ".events.jsonl"):
        assert (dest / f"{j['client_id']}{suffix}").exists(), suffix


def t_jobs_ignores_events_file():
    box = sr.Outbox(tmp())
    j = box.new_job("a")
    box.events(j["client_id"]).write_text('{"v":1}\n{"t":0,"k":"private"}\n', encoding="utf-8")
    jobs = box.jobs()
    assert len(jobs) == 1 and jobs[0]["client_id"] == j["client_id"], jobs


def t_prune_sent_only_old():
    box = sr.Outbox(tmp())
    box.sent.mkdir()
    old, new = box.sent / "old.mp4", box.sent / "new.mp4"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    eight_days = time.time() - 8 * 86400
    os.utime(old, (eight_days, eight_days))
    assert box.prune_sent() == 1
    assert not old.exists() and new.exists()


def t_no_stray_files():
    box = sr.Outbox(tmp())
    j = box.new_job("a")
    box.save(j)
    names = sorted(p.name for p in box.dir.iterdir())
    assert names == [f"{j['client_id']}.json"], names       # no .new left from the safe save


check("after a crash: a started recording is finished, an empty one moved aside", t_recover_finishes_or_moves_aside)
check("once uploaded, all three files move to sent/", t_mark_sent_moves_both_files)
check("move_aside moves all three files", t_move_aside_moves_all_three_files)
check("jobs() ignores the events file", t_jobs_ignores_events_file)
check("sent/ keeps a week, then tidies", t_prune_sent_only_old)
check("saving leaves no temporary file behind", t_no_stray_files)

# ---------------------------------------------------------------------- upload
print("\nUpload")


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeServer:
    """The live server's rules (backend recordings.py) in memory."""

    def __init__(self, piece=1000, received=0, fail_put_after=None, not_video=False, ready=False, events_code=200):
        self.piece, self.data, self.puts = piece, b"", 0
        self.received, self.fail_after, self.not_video, self.ready = received, fail_put_after, not_video, ready
        self.events_code, self.events_body, self.events_headers = events_code, None, None

    def rec(self):
        return {"id": "r1", "piece_max": self.piece, "received": self.received,
                "status": "ready" if self.ready else "uploading",
                "share_url": "https://notes.example.test/s/tok" if self.ready else None}

    def post(self, url, json=None, timeout=None):
        self.size = json["size"]
        return _Resp(201, self.rec())

    def put(self, url, params=None, data=None, headers=None, timeout=None):
        if url.endswith("/events"):
            self.events_body, self.events_headers = data, headers
            n = max(0, len((data or b"").splitlines()) - 2)
            return _Resp(self.events_code, {"events_status": "ready", "events_count": n})
        if self.fail_after is not None and self.puts >= self.fail_after:
            raise ConnectionError("offline")
        self.puts += 1
        if params["offset"] != self.received:
            return _Resp(409, {"received": self.received})
        if len(data) > self.piece:
            return _Resp(413, {})
        self.data += data
        self.received += len(data)
        if self.received >= self.size:
            if self.not_video:
                return _Resp(422, {})
            self.ready = True
        return _Resp(200, self.rec())


def _video_file(n=3500):
    p = tmp() / "v.mp4"
    p.write_bytes(bytes(range(256)) * (n // 256) + b"z" * (n % 256))
    return p


def t_upload_in_pieces():
    p, srv = _video_file(), FakeServer()
    url = sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
    assert url.endswith("/s/tok") and srv.data == p.read_bytes() and srv.puts == 4, (url, srv.puts)


def t_upload_resumes_after_going_offline():
    p, srv = _video_file(), FakeServer(fail_put_after=2)
    try:
        sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
        raise AssertionError("should have failed while offline")
    except ConnectionError:
        pass
    srv.fail_after = None
    sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
    assert srv.data == p.read_bytes() and srv.puts == 4, srv.puts      # nothing sent twice


def t_upload_follows_server_offset():
    p, srv = _video_file(), FakeServer()
    srv.received = 2000
    srv.data = p.read_bytes()[:2000]
    real_post = srv.post
    srv.post = lambda url, json=None, timeout=None: (real_post(url, json), _Resp(201, {**srv.rec(), "received": 0}))[1]
    sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")     # starts at 0, told 409 -> 2000
    assert srv.data == p.read_bytes(), len(srv.data)


def t_upload_not_video():
    p, srv = _video_file(), FakeServer(not_video=True)
    try:
        sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
        raise AssertionError("expected NotVideo")
    except sr.NotVideo:
        pass


def t_upload_already_done():
    p, srv = _video_file(), FakeServer(ready=True, received=3500)
    assert sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x").endswith("/s/tok")
    assert srv.puts == 0


def t_upload_piece_says_failed():
    p, srv = _video_file(), FakeServer()
    def put_fail(url, params=None, data=None, headers=None, timeout=None):
        srv.puts += 1
        return _Resp(200, {**srv.rec(), "status": "failed"})
    srv.put = put_fail
    try:
        sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
        raise AssertionError("expected NotVideo")
    except sr.NotVideo:
        pass
    assert srv.puts <= 2, srv.puts


def t_upload_server_claims_more_than_file():
    p, srv = _video_file(3500), FakeServer(received=5000)
    def put_same(url, params=None, data=None, headers=None, timeout=None):
        srv.puts += 1
        return _Resp(200, {**srv.rec(), "received": 5000, "status": "uploading"})
    srv.put = put_same
    try:
        sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
        raise AssertionError("expected UploadStuck")
    except sr.UploadStuck:
        pass
    assert srv.puts <= 5, srv.puts


def t_upload_409_same_offset_forever():
    p, srv = _video_file(), FakeServer()
    def put_409(url, params=None, data=None, headers=None, timeout=None):
        srv.puts += 1
        return _Resp(409, {"received": 0})
    srv.put = put_409
    try:
        sr.upload({"title": "t", "client_id": "c"}, p, srv, "https://x")
        raise AssertionError("expected UploadStuck")
    except sr.UploadStuck:
        pass
    assert srv.puts <= 5, srv.puts


class _Link:
    def __init__(self, srv):
        self.http, self.url = srv, "https://x"


def t_upload_waiting_moves_stuck_aside():
    srv = FakeServer()
    srv.put = lambda url, params=None, data=None, headers=None, timeout=None: _Resp(409, {"received": 0})
    notes, home = [], tmp()
    svc = sr.ScreenService(home, ["unused"], get_link=lambda: _Link(srv),
                           notify=lambda t, m: notes.append((t, m)))
    job = svc.box.new_job("Stuck clip")
    svc.box.video(job["client_id"]).write_bytes(_video_file().read_bytes())
    job["state"] = "ready"
    svc.box.save(job)
    svc.upload_waiting()
    assert (svc.box.dir / "stuck" / f"{job['client_id']}.json").exists()
    assert len(notes) == 1 and "Stuck clip" in notes[0][1], notes


def _events_file():
    p = tmp() / "c.events.jsonl"
    p.write_bytes(b'{"v":1}\n{"t":10,"k":"private"}\n{"t":20,"k":"end","lines":2,"cut":false}\n')
    return p


def t_upload_sets_server_id():
    p, srv = _video_file(), FakeServer()
    job = {"title": "t", "client_id": "c"}
    sr.upload(job, p, srv, "https://x")
    assert job.get("server_id") == "r1", job


def t_upload_events_sent():
    ep, srv = _events_file(), FakeServer()
    res = sr.upload_events({"server_id": "r1"}, ep, srv, "https://x")
    assert res == "sent", res
    assert srv.events_body == ep.read_bytes()
    assert srv.events_headers == {"Content-Type": "application/x-ndjson"}, srv.events_headers


def t_upload_events_old_server():
    ep, srv = _events_file(), FakeServer(events_code=404)
    assert sr.upload_events({"server_id": "r1"}, ep, srv, "https://x") == "old server"


def t_upload_events_server_down():
    ep, srv = _events_file(), FakeServer()
    real_put = srv.put
    def put_err(url, **kw):
        if url.endswith("/events"):
            raise ConnectionError("offline")
        return real_put(url, **kw)
    srv.put = put_err
    assert sr.upload_events({"server_id": "r1"}, ep, srv, "https://x") == "failed"


def t_upload_events_missing_file():
    srv = FakeServer()
    res = sr.upload_events({"server_id": "r1"}, tmp() / "missing.events.jsonl", srv, "https://x")
    assert res == "none" and srv.events_body is None


def t_upload_waiting_copies_link_when_events_fail():
    srv = FakeServer(events_code=500)
    copied, real = [], sr.set_clipboard_text
    sr.set_clipboard_text = lambda text: (copied.append(text), True)[1]
    try:
        home = tmp()
        svc = sr.ScreenService(home, ["unused"], get_link=lambda: _Link(srv), notify=lambda t, m: None)
        job = svc.box.new_job("Events fail")
        svc.box.video(job["client_id"]).write_bytes(_video_file().read_bytes())
        svc.box.events(job["client_id"]).write_bytes(_events_file().read_bytes())
        job["state"] = "ready"
        svc.box.save(job)
        svc.upload_waiting()
    finally:
        sr.set_clipboard_text = real
    assert len(copied) == 1 and copied[0].endswith("/s/tok"), copied
    meta = json.loads((svc.box.sent / f"{job['client_id']}.json").read_text(encoding="utf-8"))
    assert meta.get("events_upload") == "failed", meta


check("a video goes up in pieces and comes back as a link", t_upload_in_pieces)
check("offline half-way: the next try carries on, nothing sent twice", t_upload_resumes_after_going_offline)
check("if the server is somewhere else, the upload goes there", t_upload_follows_server_offset)
check("a file the server cannot read stops, not retried forever", t_upload_not_video)
check("already on the server: just the link, nothing re-sent", t_upload_already_done)
check("a piece answer with status failed stops with NotVideo", t_upload_piece_says_failed)
check("server claiming more bytes than the file stops with UploadStuck", t_upload_server_claims_more_than_file)
check("repeated 409 with the same offset stops with UploadStuck", t_upload_409_same_offset_forever)
check("upload_waiting moves a stuck job aside and notifies once", t_upload_waiting_moves_stuck_aside)
check("upload sets job['server_id']", t_upload_sets_server_id)
check("events file is uploaded with application/x-ndjson", t_upload_events_sent)
check("old server returning 404 on events gives 'old server'", t_upload_events_old_server)
check("connection error on events returns 'failed'", t_upload_events_server_down)
check("missing events file returns 'none' without calling /events", t_upload_events_missing_file)
check("upload_waiting still copies the link when events fail", t_upload_waiting_copies_link_when_events_fail)

# ---------------------------------------------------------------------- hotkey
print("\nHotkey")


def t_hotkey_parse():
    assert sr.parse_hotkey("ctrl+alt+r") == (0x3, 0x52)
    assert sr.parse_hotkey("Ctrl + Shift + F9") == (0x6, 0x78)
    for bad in ("r", "ctrl+alt", "ctrl+alt+enterr"):
        try:
            sr.parse_hotkey(bad)
            raise AssertionError(f"{bad!r} accepted")
        except ValueError:
            pass


check("hotkey text is understood, nonsense refused", t_hotkey_parse)


def t_default_hotkey_is_ctrl_shift_r():
    assert sr.parse_hotkey(sr.DEFAULT_HOTKEY) == (0x6, 0x52), sr.DEFAULT_HOTKEY      # Ctrl+Shift, R


check("the screen key is Ctrl+Shift+R out of the box", t_default_hotkey_is_ctrl_shift_r)

# ------------------------------------------------------------- screen flashes
print("\nScreen flashes")


def _service(get_link):
    flashes, home = [], tmp()
    svc = sr.ScreenService(home, ["unused"], get_link=get_link, notify=lambda t, m: None,
                           flash=flashes.append)
    job = svc.box.new_job("t")
    svc.box.video(job["client_id"]).write_bytes(_video_file().read_bytes())
    job["state"] = "ready"
    svc.box.save(job)
    return svc, flashes


def t_flash_link_copied():
    real = sr.set_clipboard_text
    sr.set_clipboard_text = lambda text: True
    try:
        svc, flashes = _service(lambda: _Link(FakeServer()))
        svc.upload_waiting()
    finally:
        sr.set_clipboard_text = real
    assert flashes == ["copied"], flashes


def t_flash_waiting_once_when_signed_out():
    svc, flashes = _service(lambda: None)
    svc.upload_waiting()
    svc.upload_waiting()
    assert flashes == ["waiting"], flashes


def t_flash_waiting_when_offline():
    svc, flashes = _service(lambda: _Link(FakeServer(fail_put_after=0)))
    svc.upload_waiting()
    svc.upload_waiting()
    assert flashes == ["waiting"], flashes


FLASH_CHILD = ("import os, sys; sys.path.insert(0, sys.argv[1]); import screen_record as s; "
               "print(os.getpid(), flush=True); "     # a venv's python.exe starts the real one as a child
               "sys.exit(s.flash_main('copied', area=(-20000, -20000, 800, 600)))")


def t_flash_hidden_and_click_through():
    """Drawn far off-screen, so nothing shows on the real screen."""
    import ctypes
    from ctypes import wintypes as W
    u32 = ctypes.windll.user32
    t0 = time.monotonic()
    p = subprocess.Popen([sys.executable, "-c", FLASH_CHILD, MODDIR], stdout=subprocess.PIPE, text=True)
    line = p.stdout.readline().strip()
    real_pid = int(line) if line.isdigit() else p.pid
    time.sleep(1.5)
    found = []

    def each(hwnd, _):
        pid = W.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == real_pid and u32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, cls, 64)
            if cls.value == "TkTopLevel":
                aff = W.DWORD()
                u32.GetWindowDisplayAffinity(hwnd, ctypes.byref(aff))
                found.append((aff.value, u32.GetWindowLongW(hwnd, -20) & 0x20))
        return True
    u32.EnumWindows(ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(each), 0)
    code = p.wait(10)
    took = time.monotonic() - t0
    assert code == 0, code
    assert len(found) >= 5, found                                   # 4 edges + the message
    assert all(a == 0x11 and ct for a, ct in found), found          # hidden from capture, mouse goes through
    assert took < 6, took


check("link copied: the screen flashes once", t_flash_link_copied)
check("not signed in: 'saved on this PC' flashes once, not every pass", t_flash_waiting_once_when_signed_out)
check("offline: 'saved on this PC' flashes once", t_flash_waiting_when_offline)
check("the flash is hidden from recordings, clicks go through it, it closes itself",
      t_flash_hidden_and_click_through)

# -------------------------------------------------- recording (made-up picture)
print("\nRecording (colour bars and a tone - not the screen, not the microphone)")

CHILD = ("import sys; sys.path.insert(0, sys.argv[2]); sys.path.insert(1, sys.argv[3]); "
         "import screen_record; sys.exit(screen_record.child_main(sys.argv[1]))")


def _record(seconds, kill=False):
    home = tmp()
    outdir = home / "screen" / "outbox"
    outdir.mkdir(parents=True)
    env = {**os.environ, "NOTEWELL_SCREEN_TEST_SOURCE": "testsrc=size=1280x720:rate=15,realtime"}
    p = subprocess.Popen([sys.executable, "-c", CHILD, str(outdir), MODDIR, str(ROOT / "windows-recorder")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=env, text=True, encoding="utf-8")
    time.sleep(seconds)
    if kill:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        p.wait(15)
        return home, []
    p.stdin.write("stop\n")
    p.stdin.flush()
    try:
        out, _ = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        raise AssertionError("Stop was ignored: the recording copy did not finish within 30 s")
    return home, [json.loads(line) for line in out.splitlines() if line.strip()]


def _frames(path):
    import av
    with av.open(str(path)) as c:
        kinds = sorted(s.type for s in c.streams)
        n = 0
        for pkt in c.demux(video=0):
            try:
                n += len(pkt.decode())
            except av.error.InvalidDataError:      # the torn last piece after a crash
                pass
        return kinds, n


def t_records_picture_and_sound():
    home, events = _record(5)
    done = [e for e in events if e.get("event") == "done"]
    assert done and not done[0]["error"], events
    box = sr.Outbox(home / "screen")
    jobs = box.waiting()
    assert len(jobs) == 1, box.jobs()
    kinds, n = _frames(box.video(jobs[0]["client_id"]))
    assert kinds == ["audio", "video"], kinds
    # 15 frames a second for about 5 s. Two of the bugs this guards against gave
    # 1 frame (a thread froze loading a library) and stopped at 2.1 s (two
    # frames on one timestamp).
    assert n >= 60, f"only {n} frames in 5 s"
    left = sorted(p.name for p in (home / "screen" / "outbox").iterdir())
    cid = jobs[0]["client_id"]
    assert left == sorted([f"{cid}.mp4", f"{cid}.json", f"{cid}.events.jsonl"]), left
    el = box.events(cid).read_text(encoding="utf-8").splitlines()
    assert len(el) == 2, el
    hdr, end = json.loads(el[0]), json.loads(el[1])
    assert set(hdr.keys()) == {"v", "started_utc", "os", "app", "screen", "video"}, hdr
    assert hdr["v"] == 1 and hdr["video"] == [1280, 720] and set(hdr["screen"].keys()) == {"x", "y", "w", "h"}, hdr
    assert end.get("k") == "end" and end.get("lines") == 1 and end.get("cut") is False and type(end.get("t")) is int, end


def t_start_and_stop_events_never_raise():
    class BrokenBox:
        def events(self, cid):
            raise RuntimeError("disk fault")

    class BrokenWatcher:
        def stop(self):
            raise RuntimeError("stop fault")

    assert sr.start_events(BrokenBox(), {"client_id": "c1"}, object(), (0, 0, 100, 100), True) is None
    sr.stop_events(None)
    sr.stop_events(BrokenWatcher())


def t_crash_keeps_what_was_recorded():
    home, _ = _record(6, kill=True)
    box = sr.Outbox(home / "screen")
    box.recover()
    jobs = box.waiting()
    assert len(jobs) == 1 and jobs[0].get("recovered"), box.jobs()
    kinds, n = _frames(box.video(jobs[0]["client_id"]))
    assert "video" in kinds and n >= 30, (kinds, n)
    ep = box.events(jobs[0]["client_id"])
    assert ep.exists()
    rows = [json.loads(ln) for ln in ep.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows and rows[0].get("v") == 1 and set(rows[0].keys()) == {"v", "started_utc", "os", "app", "screen", "video"}, rows
    assert all(r.get("k") != "end" for r in rows), rows


def _record_script(steps, env_extra=None):
    """Run the child with test source; wait for 'started', then run [(delay_s, cmd), ...]."""
    home = tmp()
    outdir = home / "screen" / "outbox"
    outdir.mkdir(parents=True)
    env = {**os.environ, "NOTEWELL_SCREEN_TEST_SOURCE": "testsrc=size=1280x720:rate=15,realtime"}
    if env_extra:
        env.update(env_extra)
    p = subprocess.Popen([sys.executable, "-c", CHILD, str(outdir), MODDIR, str(ROOT / "windows-recorder")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         env=env, text=True, encoding="utf-8")
    events = []
    first = p.stdout.readline()
    if first.strip():
        events.append(json.loads(first))
    for delay, cmd in steps:
        if delay:
            time.sleep(delay)
        if cmd is not None:
            p.stdin.write(cmd + "\n")
            p.stdin.flush()
    try:
        out, _ = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        raise AssertionError("the recording copy did not finish within 30 s")
    for line in out.splitlines():
        if line.strip():
            events.append(json.loads(line))
    return home, events


def _pts_and_duration(path):
    import av
    v_pts, a_pts = [], []
    with av.open(str(path)) as c:
        vs = c.streams.video[0]
        as_ = c.streams.audio[0] if c.streams.audio else None
        for pkt in c.demux():
            if pkt.dts is None and pkt.pts is None:
                continue
            if pkt.stream.type == "video":
                for f in pkt.decode():
                    if f.pts is not None:
                        v_pts.append(f.pts)
            elif pkt.stream.type == "audio":
                for f in pkt.decode():
                    if f.pts is not None:
                        a_pts.append(f.pts)
        v_dur = (v_pts[-1] - v_pts[0]) * float(vs.time_base) if len(v_pts) >= 2 else 0.0
    return v_pts, a_pts, v_dur


def t_video_ms_freezes_on_pause():
    cap = sr.Capture(tmp() / "v.mp4", 0)
    cap.t0 = time.monotonic() - 1.0
    cap.pause()
    assert cap.paused
    a = cap.video_ms()
    time.sleep(0.2)
    b = cap.video_ms()
    assert a == b, (a, b)
    cap.resume()
    assert not cap.paused
    c = cap.video_ms()
    assert abs(c - a) < 100, (a, c)
    time.sleep(0.2)
    d = cap.video_ms()
    assert d - c >= 120, (c, d)


def t_record_with_pause():
    home, events = _record_script([(1.5, "pause"), (2.0, "resume"), (1.5, "stop")])
    done = [e for e in events if e.get("event") == "done"]
    assert done and not done[0]["error"], events
    box = sr.Outbox(home / "screen")
    jobs = box.waiting()
    assert len(jobs) == 1, jobs
    v_pts, a_pts, v_dur = _pts_and_duration(box.video(jobs[0]["client_id"]))
    assert 2.5 <= v_dur <= 3.6, f"duration {v_dur:.2f} s"
    assert len(v_pts) >= 30 and all(v_pts[i] < v_pts[i + 1] for i in range(len(v_pts) - 1)), v_pts[:10]
    assert len(a_pts) >= 30 and all(a_pts[i] < a_pts[i + 1] for i in range(len(a_pts) - 1)), a_pts[:10]


def t_stop_while_paused():
    home, events = _record_script([(1.5, "pause"), (1.0, "stop")])
    done = [e for e in events if e.get("event") == "done"]
    assert done and not done[0]["error"], events
    box = sr.Outbox(home / "screen")
    jobs = box.waiting()
    assert len(jobs) == 1, jobs
    kinds, n = _frames(box.video(jobs[0]["client_id"]))
    assert kinds == ["audio", "video"] and n >= 10, (kinds, n)


check("5 s of recording: picture and sound, 15 frames a second, 3 files", t_records_picture_and_sound)
check("killed mid-recording: what was recorded still plays", t_crash_keeps_what_was_recorded)
check("start_events and stop_events never raise when something breaks", t_start_and_stop_events_never_raise)
check("Capture.video_ms freezes during pause and resumes from the same value", t_video_ms_freezes_on_pause)
check("recording with a 2 s pause leaves the pause out and keeps pts rising", t_record_with_pause)
def t_mute_silences_middle_second():
    import av
    import numpy as np
    home, events = _record_script([(1.0, "mute"), (1.0, "unmute"), (1.0, "stop")])
    done = [e for e in events if e.get("event") == "done"]
    assert done and not done[0]["error"], events
    box = sr.Outbox(home / "screen")
    jobs = box.waiting()
    assert len(jobs) == 1, jobs
    # Job file gains no mute fields
    assert not any("mute" in k.lower() for k in jobs[0].keys()), jobs[0]
    segs = [[], [], []]  # [0.0..0.8], [1.2..1.8], [2.2..end]
    with av.open(str(box.video(jobs[0]["client_id"]))) as c:
        as_ = c.streams.audio[0]
        tb = float(as_.time_base)
        for frame in c.decode(audio=0):
            t = frame.pts * tb if frame.pts is not None else 0.0
            arr = frame.to_ndarray().astype(np.float32).ravel()
            if 0.0 <= t <= 0.8:
                segs[0].append(arr)
            elif 1.2 <= t <= 1.8:
                segs[1].append(arr)
            elif t >= 2.2:
                segs[2].append(arr)
    rms = [float(np.sqrt(np.mean(np.concatenate(s) ** 2))) for s in segs]
    assert rms[0] > 0.01, f"first second rms {rms}"
    assert rms[1] < 0.001, f"middle muted rms {rms}"
    assert rms[2] > 0.01, f"last second rms {rms}"


check("stopping while paused still finishes and produces a playable file", t_stop_while_paused)
def t_capture_source_whole_monitor():
    s = sr.capture_source(0, (0, 0, 1920, 1080))
    assert "video_size" not in s and "ddagrab=output_idx=0:framerate=15:draw_mouse=1" in s, s


def t_capture_source_region_exact():
    s = sr.capture_source(1, (1920, 0, 1920, 1080), (2020, 100, 801, 601))
    expected = "ddagrab=output_idx=1:framerate=15:draw_mouse=1:video_size=800x600:offset_x=100:offset_y=100,hwdownload,format=bgra"
    assert s == expected, s


def t_capture_source_clipped_and_outside():
    s = sr.capture_source(0, (0, 0, 1920, 1080), (-50, 900, 400, 300))
    assert "video_size=350x180:offset_x=0:offset_y=900" in s, s
    try:
        sr.capture_source(0, (0, 0, 1920, 1080), (2000, 0, 300, 300))
        raise AssertionError("expected ValueError for region outside monitor")
    except ValueError:
        pass


def t_header_with_test_area():
    home, events = _record_script([(1.0, "stop")], env_extra={"NOTEWELL_SCREEN_TEST_AREA": "120,80,640,480"})
    done = [e for e in events if e.get("event") == "done"]
    assert done and not done[0]["error"], events
    box = sr.Outbox(home / "screen")
    cid = box.waiting()[0]["client_id"]
    hdr = json.loads(box.events(cid).read_text(encoding="utf-8").splitlines()[0])
    assert hdr["screen"] == {"x": 120, "y": 80, "w": 640, "h": 480}, hdr


def t_pick_windows_excludes_own_pid_and_private():
    def fake_walk():
        return [
            (1, 999, "notewell.exe", "Notewell Badge", (0, 0, 200, 100)),
            (2, 101, "notepad.exe", "notes.txt - Notepad", (10, 20, 800, 600)),
            (3, 202, "keepass.exe", "My Vault - KeePass", (50, 50, 700, 500)),
            (4, 303, "chrome.exe", "Barclays Online Banking", (50, 50, 700, 500)),
            (5, 404, "code.exe", "   ", (0, 0, 800, 600)),
        ]
    got = sr.pick_windows(walk=fake_walk, own_pid=999, blocked_apps={"keepass.exe"})
    assert got == [(2, "notepad.exe", "notes.txt - Notepad", (10, 20, 800, 600))], got


check("muting silences the middle second while keeping audio in step", t_mute_silences_middle_second)
check("capture_source whole monitor omits video_size", t_capture_source_whole_monitor)
check("capture_source region builds exact ddagrab offsets and even size", t_capture_source_region_exact)
check("capture_source clips partial regions and rejects outside ones", t_capture_source_clipped_and_outside)
check("NOTEWELL_SCREEN_TEST_AREA sets the events header screen", t_header_with_test_area)
def t_parse_trim_table():
    import screen_trim as st
    assert st.parse_trim("", "", 30.0) is None
    assert st.parse_trim("   ", "", 30.0) is None
    assert st.parse_trim("0:05", "0:20", 30.0) == (5.0, 20.0)
    assert st.parse_trim("", "0:15", 30.0) == (0.0, 15.0)
    assert st.parse_trim("0:10", "", 30.0) == (10.0, 30.0)
    assert st.parse_trim("0:05", "1:00", 30.0) == (5.0, 30.0)   # clipped past the end
    assert sr.parse_trim("0:02", "0:07", 10.0) == (2.0, 7.0)
    for bad_s, bad_e in (("0:20", "0:05"), ("0:10", "0:10"), ("0:10", "0:10.5"), ("bad", "0:20"), ("0:75", "1:00")):
        try:
            st.parse_trim(bad_s, bad_e, 30.0)
            raise AssertionError(f"accepted {(bad_s, bad_e)}")
        except ValueError:
            pass


def t_trim_video_4s_cut_1_to_3():
    import screen_trim as st
    home, events = _record_script([(4.0, "stop")])
    done = [e for e in events if e.get("event") == "done"]
    assert done and not done[0]["error"], events
    box = sr.Outbox(home / "screen")
    job = box.waiting()[0]
    src = box.video(job["client_id"])
    dst = tmp() / "trimmed.mp4"
    st.trim_video(src, dst, 1.0, 3.0, job["encoder"])
    v_pts, a_pts, v_dur = _pts_and_duration(dst)
    assert 1.8 <= v_dur <= 2.3, f"trimmed duration {v_dur:.2f} s"
    assert len(v_pts) >= 20 and all(v_pts[i] < v_pts[i + 1] for i in range(len(v_pts) - 1)), v_pts[:10]
    assert len(a_pts) >= 20 and all(a_pts[i] < a_pts[i + 1] for i in range(len(a_pts) - 1)), a_pts[:10]


def t_failed_trim_moves_to_stuck_and_never_uploads():
    srv = FakeServer()
    home = tmp()
    svc = sr.ScreenService(home, ["unused"], get_link=lambda: _Link(srv), notify=lambda t, m: None)
    job = svc.box.new_job("Trim fail clip")
    cid = job["client_id"]
    svc.box.video(cid).write_bytes(_video_file().read_bytes())
    svc.box.events(cid).write_bytes(_events_file().read_bytes())

    def boom(*a, **kw):
        raise RuntimeError("encoder error")

    status, msg = sr.finalize_job(svc.box, job, 10.0, time.time(),
                                  {"mic": True, "encoder": "libx264", "error": None},
                                  action="send", title="Renamed", trim_range=(1.0, 5.0),
                                  trim_video_fn=boom)
    assert status == "trim_failed" and "Renamed" in msg, (status, msg)
    assert (svc.box.dir / "stuck" / f"{cid}.mp4").exists()
    assert (svc.box.dir / "stuck" / f"{cid}.json").exists()
    assert svc.box.waiting() == []
    svc.upload_waiting()
    assert srv.puts == 0 and srv.data == b""


def t_discard_and_untrimmed_move_aside():
    box = sr.Outbox(tmp())
    j1 = box.new_job("Drop me")
    cid1 = j1["client_id"]
    box.video(cid1).write_bytes(b"v1")
    status, _ = sr.finalize_job(box, j1, 3.0, time.time(),
                                {"mic": True, "encoder": "libx264", "error": None}, action="discard")
    assert status == "discarded"
    assert (box.dir / "discarded" / f"{cid1}.mp4").exists()
    assert box.waiting() == []

    j2 = box.new_job("Keep untrimmed")
    cid2 = j2["client_id"]
    box.video(cid2).write_bytes(b"v2")
    box.untrimmed(cid2).write_bytes(b"orig")
    box.mark_sent(j2, "https://example.test/v/ok")
    assert (box.sent / f"{cid2}.untrimmed.mp4").exists()


check("pick_windows excludes our own pid and private windows", t_pick_windows_excludes_own_pid_and_private)
check("parse_trim handles empty, valid ranges, clipping, and rejects bad ranges", t_parse_trim_table)
check("trim_video cuts a 4 s test recording to 1..3 s and plays", t_trim_video_4s_cut_1_to_3)
check("a failed trim moves the job to stuck/ and uploads nothing", t_failed_trim_moves_to_stuck_and_never_uploads)
check("discard keeps files in discarded/ and mark_sent moves .untrimmed.mp4", t_discard_and_untrimmed_move_aside)

print(f"\n{_passed} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)
