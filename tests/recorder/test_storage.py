r"""Tests for what the laptop keeps, and for how long (chunk 08, step 7).

Run:  python tests/recorder/test_storage.py

The rule (chunk 06, 20 Nov): a meeting's audio and transcript copies stay on the laptop
7 days, and only go once the server has that meeting's note. If the server cannot be
asked, they stay. Each install keeps only the newest 2 backup copies of dictation.db,
config.json and linked-server.json. Anything removed goes to the Recycle Bin.
Before 2.7.0 archived audio went after 30 days whether or not the server had the note,
transcript copies were never cleared, and backup copies piled up (9 of dictation.db).
"""
import glob, http.server, json, os, shutil, sys, tempfile, threading, time, uuid
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="nw-storage-"))
os.environ["MEETING_RECORDER_HOME"] = str(HOME / "recorder")
APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "windows-recorder"))
import recorder as R
import tidy

R.ARCHIVE_ROOT = HOME / "archive"            # never the real archive
DAY = 86400
CFG = {"archive_retention_days": 7, "api_url": "http://far.test", "api_key": "k-main"}
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


def reset():
    shutil.rmtree(R.ARCHIVE_ROOT, ignore_errors=True)
    shutil.rmtree(R.APPDIR, ignore_errors=True)
    R.APPDIR.mkdir(parents=True)


def meeting(stamp, age_days, listed=True, server="http://far.test"):
    """A sent meeting as the app leaves it: two archived WAVs and a transcript copy."""
    day = R.ARCHIVE_ROOT / "wav-2026-11-01"
    day.mkdir(parents=True, exist_ok=True)
    txt = R.APPDIR / f"last_transcript_{stamp + 90}.txt"
    files = [day / f"last_meeting_{stamp}_mic.wav", day / f"last_meeting_{stamp}_sys.wav", txt]
    when = time.time() - age_days * DAY
    for f in files:
        f.write_bytes(b"x")
        os.utime(f, (when, when))
    if listed:
        R.note_sent(stamp, txt, f"s{stamp}", {"api_url": server, "api_key": "k-main"})
    return files


BIN = []


def to_bin(paths):                            # stands in for the Recycle Bin
    for p in paths:
        Path(p).unlink()
        BIN.append(Path(p).name)
    return list(paths)


def prune(answer):
    asked = []
    n = R.prune_archive(CFG, has_note=lambda e, c: asked.append(e["session_id"]) or answer, recycle=to_bin)
    return n, asked


# ------------------------------------------------ meeting audio and transcripts --
def t_seven_days():
    assert R.DEFAULT_CONFIG["archive_retention_days"] == 7


def t_old_with_note_goes():
    reset()
    files = meeting(1000, 8)
    assert prune(True) == (3, ["s1000"])
    assert not any(f.exists() for f in files) and BIN[-3:] == [f.name for f in files], BIN[-3:]


def t_old_without_note_stays():
    reset()
    files = meeting(1000, 8)
    assert prune(False) == (0, ["s1000"]) and all(f.exists() for f in files)


def t_server_cannot_be_asked():
    reset()
    files = meeting(1000, 8) + meeting(2000, 9)
    n, asked = prune(None)
    assert n == 0 and all(f.exists() for f in files)
    assert asked == ["s1000"], f"asked again after the server did not answer: {asked}"


def t_young_files_stay_unasked():
    reset()
    files = meeting(1000, 6)
    assert prune(True) == (0, []) and all(f.exists() for f in files)


def t_unlisted_files_stay():
    reset()
    files = meeting(1000, 30, listed=False)
    stray = R.ARCHIVE_ROOT / "wav-2026-11-01" / "something-else.wav"
    stray.write_bytes(b"x")
    os.utime(stray, (1, 1))
    assert prune(True) == (0, []) and all(f.exists() for f in files + [stray])


def t_sent_list():
    reset()
    R.note_sent(1000, R.APPDIR / "last_transcript_1090.txt", "s1000", {"api_url": "http://far.test/", "api_key": "k-main"})
    R.note_sent(2000, None, "s2000", {"api_url": "http://near.test", "api_key": "k-near"})
    with open(R.APPDIR / R.SENT_LIST, "a", encoding="utf-8") as f:
        f.write("{half a line\n")
    got = R.sent_meetings()
    assert got == [{"stamp": 1000, "transcript": "last_transcript_1090.txt", "session_id": "s1000", "server": "http://far.test"},
                   {"stamp": 2000, "transcript": "", "session_id": "s2000", "server": "http://near.test"}], got
    assert "k-main" not in (R.APPDIR / R.SENT_LIST).read_text(), "a key was written to the list"
    R.note_sent(3000, None, "s3000", "not a config")          # must never stop a meeting finishing
    assert len(R.sent_meetings()) == 2


# --------------------------------------------------- asking the server for a note --
class Server(http.server.BaseHTTPRequestHandler):
    ANSWERS = {"/results/sdone": (200, {"status": "complete"}), "/results/sbusy": (200, {"status": "processing"}),
               "/results/sbroken": (500, {"detail": "boom"})}
    keys = []

    def do_GET(self):
        Server.keys.append((self.server.server_port, self.headers.get("X-API-Key")))
        code, body = self.ANSWERS.get(self.path, (404, {"detail": "not found"}))
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a):
        pass


def t_asks_the_server_that_took_it():
    far, near = (http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server) for _ in range(2))
    for s in (far, near):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    url = lambda s: f"http://127.0.0.1:{s.server_port}"
    cfg = {"api_url": url(far) + "/", "api_key": "k-main", "fallback_api_url": url(near), "fallback_api_key": "k-near"}
    ask = lambda sid, server: R.server_has_note({"session_id": sid, "server": server}, cfg)
    try:
        assert ask("sdone", url(far)) is True and Server.keys[-1] == (far.server_port, "k-main")
        assert ask("sdone", url(near)) is True and Server.keys[-1] == (near.server_port, "k-near")
        assert ask("sbusy", url(far)) is False, "a note still being written is not a note yet"
        assert ask("sgone", url(far)) is False
        assert ask("sbroken", url(far)) is None
        asked = len(Server.keys)
        assert ask("sdone", "http://another.test") is None and len(Server.keys) == asked, "asked a server no longer set"
        far.shutdown(); far.server_close()
        assert ask("sdone", url(far)) is None, "a server that is off must mean 'cannot ask'"
    finally:
        near.shutdown(); near.server_close()


# ------------------------------------------------------------------ backup copies --
def t_newest_two_backups_kept():
    home = HOME / "notewell"
    made = {"data/dictation.db": ["20261117-240", "20261117-241", "20261117-242", "20261117-243", "20261117-244",
                                  "20261117-245", "20261118-250", "20261118-260", "20261119-261"],
            "recorder/config.json": ["20261116", "20261119-261", "20261120-06"],
            "linked-server.json": ["20261119-261"], "linked-state.json": ["20261116", "20261117"]}
    for i, (rel, tags) in enumerate(made.items()):
        live = home / rel
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text("live")
        for n, tag in enumerate(tags):
            copy = live.with_name(f"{live.name}.bak-{tag}")
            copy.write_text(tag)
            when = 1_790_000_000 + n * 60 - (60 if tag == "20261118-260" else 0)   # 250 and 260: same time
            os.utime(copy, (when, when))
    sent = []
    tidy.trim_backups(home, send=lambda ps: sent.extend(p.name for p in ps) or ps)
    assert sorted(sent) == sorted([f"dictation.db.bak-{t}" for t in made["data/dictation.db"][:7]]
                                  + ["config.json.bak-20261116"]), sent
    assert all((home / rel).read_text() == "live" for rel in made), "a live file was touched"


def t_recycle_bin_really_used():
    if sys.platform != "win32":
        return
    mark = "notewell-recycle-test-" + uuid.uuid4().hex
    p = HOME / "bin-test.txt"
    p.write_text(mark)
    assert tidy.recycle([p]) == [p] and not p.exists()
    hits = [f for f in glob.glob(p.resolve().drive + "/$Recycle.Bin/*/$R*")
            if os.path.isfile(f) and os.path.getsize(f) == len(mark) and Path(f).read_text(errors="ignore") == mark]
    if not hits and os.environ.get("CI"):      # a build server's account may have no Recycle Bin to look in
        print("        (build server: sent, but its Recycle Bin could not be checked)")
    else:
        assert len(hits) == 1, "not found in the Recycle Bin"
    for f in hits:                            # take this test's own file back out of the bin
        os.remove(f)
        info = os.path.join(os.path.dirname(f), "$I" + os.path.basename(f)[2:])
        if os.path.exists(info):
            os.remove(info)
    missing = HOME / "not-there.txt"
    assert tidy.recycle([missing]) == [], "a file that is not there cannot have gone to the bin"


def t_app_trims_at_start():
    src = (APP / "desktop" / "notewell.py").read_text(encoding="utf-8")
    assert "tidy.trim_backups(HOME)" in src


check("meeting audio and transcripts are kept 7 days by default (was 30)", t_seven_days)
check("over 7 days and the server has the note: audio and transcript go to the bin", t_old_with_note_goes)
check("over 7 days with no note on the server: audio and transcript are kept", t_old_without_note_stays)
check("server cannot be asked: everything kept, asked once only", t_server_cannot_be_asked)
check("under 7 days: kept, the server is not even asked", t_young_files_stay_unasked)
check("files with no record of being sent are kept", t_unlisted_files_stay)
check("the list of sent meetings: read back, damaged line skipped, no key in it", t_sent_list)
check("asks the server that took the meeting, with that server's key", t_asks_the_server_that_took_it)
check("backup copies: newest 2 of each kept, live files untouched", t_newest_two_backups_kept)
check("removed files really go to the Windows Recycle Bin", t_recycle_bin_really_used)
check("the app trims backup copies each time it starts", t_app_trims_at_start)

print()
total = _passed + len(_failed)
shutil.rmtree(HOME, ignore_errors=True)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
