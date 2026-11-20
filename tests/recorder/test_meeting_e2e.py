r"""End to end: a made-up meeting becomes a note on a test copy of the server (chunk 08, step 6).

Run:  python tests/recorder/test_meeting_e2e.py        (about 4 minutes, this PC only)

The real recorder - capture threads, spool, quiet clock, speech model, upload -
is fed a two-voice meeting made with Windows text-to-speech (the far end on the
speaker track, me on the microphone: runbook/progress/out/08/meeting-others.wav
and meeting-me.wav) through a stand-in for the soundcard library. The server is
the real one, started here on a free port with a temporary data folder; only the
AI summary step is a stand-in, as in tests/server. Skips if the clips or the
speech model are not on this PC.
"""
import os, socket, subprocess, sys, tempfile, threading, time, types, wave
from pathlib import Path
import numpy as np

APP = Path(__file__).resolve().parents[2]
CLIPS = Path(os.environ.get("NOTEWELL_MEETING_CLIPS") or APP.parent / "runbook" / "progress" / "out" / "08")
KEY = "mn_" + "e2" * 24              # a key for this test server only
SR = 16000
CRASH_AT = 31.5                      # seconds of meeting recorded before the recorder is killed
_passed, _failed, _skipped = 0, [], []


def check(name, fn):
    global _passed
    try:
        if fn() == "skip":
            _skipped.append(name)
            print(f"  SKIP  {name}")
            return
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def clip(name):
    with wave.open(str(CLIPS / name)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


class FakeCard:
    """Stands in for the soundcard library: the far end on the speaker loopback, me on the
    microphone. Plays the meeting in real time (or as fast as asked), up to `upto` seconds,
    then silence in real time."""

    def __init__(self, upto=None, real_time=False):
        self.clips = {"sys": clip("meeting-others.wav"), "mic": clip("meeting-me.wav")}
        self.upto = int((upto or 1e9) * SR)
        self.real_time = real_time
        self.played = {"sys": threading.Event(), "mic": threading.Event()}

    def _device(self, track):
        card = self

        class Rec:
            pos = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def record(self, numframes):
                end = min(len(card.clips[track]), card.upto)
                if self.pos >= end:
                    card.played[track].set()
                    time.sleep(numframes / SR)
                    return np.zeros((numframes, 1), np.float32)
                piece = card.clips[track][self.pos:min(self.pos + numframes, end)]
                self.pos += len(piece)
                if card.real_time:
                    time.sleep(len(piece) / SR)
                return piece.reshape(-1, 1)

        return types.SimpleNamespace(name="Test speaker", recorder=lambda samplerate, channels, blocksize=None: Rec())

    def default_speaker(self):
        return types.SimpleNamespace(name="Test speaker")

    def all_speakers(self):
        return [self.default_speaker()]

    def get_microphone(self, id, include_loopback=False):
        return self._device("sys")

    def default_microphone(self):
        return self._device("mic")


def load_recorder(home):
    os.environ["MEETING_RECORDER_HOME"] = str(home)
    sys.path.insert(0, str(APP / "windows-recorder"))
    import logging
    import recorder
    recorder.ARCHIVE_ROOT = Path(home) / "MeetingArchive"     # never the real archive
    logging.basicConfig(level=logging.INFO, handlers=[recorder.logfiles.handler(recorder.LOG_PATH)],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return recorder


def settings(R, port, **extra):
    return dict(R.DEFAULT_CONFIG, api_url=f"http://127.0.0.1:{port}", api_key=KEY, language="en",
                sound_effects=False, live_transcript=False, auto_detect=False, spool_enabled=True,
                fallback_api_url="", **extra)


# ------------------------------------------------------- the test server --
def serve(port):
    """The real server with the AI provider replaced, as tests/server/conftest.py does."""
    sys.path.insert(0, str(APP / "backend"))
    import main, uvicorn

    class StandInAI:
        def __init__(self):
            self.chat = types.SimpleNamespace(completions=self)

        async def create(self, **kw):
            heard = sum(len(m.get("content") or "") for m in kw["messages"])
            text = {"m-classify": "team",
                    "m-summary": f"## Meeting Title\nTest meeting\n\n## Summary\nThe stand-in read {heard} characters."
                    }.get(kw["model"], "- [ ] Send the signed contract (Friday)")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=text), finish_reason="stop")])

    for stage in main.pipeline.stages().values():
        stage.client = StandInAI()
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


def start_server(data):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, MEETING_NOTES_API_KEY=KEY, LLM_API_KEY="test-not-a-real-key",
               LLM_BASE_URL="https://llm.invalid/v1", CLASSIFY_MODEL="m-classify", SUMMARY_MODEL="m-summary",
               ACTION_MODEL="m-action", DATA_DIR=str(data), NOTES_DIR=str(Path(data) / "notes"),
               USERS_FILE=str(Path(data) / "users.json"), ADMIN_EMAIL="admin@example.test", HOST="127.0.0.1",
               WEBHOOK_URL="", EXPORT_FOLDER="", PUBLIC_URL="", CORS_ORIGINS="", TRUST_PROXY="")
    Path(data).mkdir(parents=True, exist_ok=True)
    out = open(Path(data) / "server.log", "w")
    proc = subprocess.Popen([sys.executable, __file__, "--server", str(port)], env=env,
                            stdout=out, stderr=subprocess.STDOUT)
    import requests
    for _ in range(60):
        try:
            if requests.get(f"http://127.0.0.1:{port}/health", timeout=2).ok:
                return proc, port
        except requests.RequestException:
            pass
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError("test server did not start: " + (Path(data) / "server.log").read_text()[-500:])


def note(port, sid):
    import requests
    r = requests.get(f"http://127.0.0.1:{port}/results/{sid}", headers={"X-API-Key": KEY}, timeout=10)
    r.raise_for_status()
    return r.json()


def said(transcript, label, words):
    """Every word turns up in a line the transcript gives to `label` (Me / Others)."""
    lines = [l.lower() for l in transcript.splitlines() if f"[{label}]" in l]
    missing = [w for w in words if not any(w in l for l in lines)]
    assert not missing, f"[{label}] lines lack {missing}: {transcript[:600]!r}"


# ---------------------------------------------------- the crashing recorder --
def crash_child():
    """A recorder that records the first CRASH_AT seconds of the meeting, then is killed."""
    R = load_recorder(os.environ["MEETING_RECORDER_HOME"])
    sys.modules["soundcard"] = FakeCard(upto=CRASH_AT)
    mgr = R.MeetingManager(settings(R, 9), lambda title, msg: None)
    mgr.start()
    print("recording", flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__" and sys.argv[1:2] == ["--server"]:
    serve(int(sys.argv[2]))
    sys.exit(0)
if __name__ == "__main__" and sys.argv[1:2] == ["--crash-child"]:
    crash_child()


# ------------------------------------------------------------------- tests --
SENT = []


def spy(real):
    def deliver(*a, **k):
        out = real(*a, **k)
        SENT.append(out[0])
        return out
    return deliver


def ready():
    if not (CLIPS / "meeting-others.wav").exists() or not (CLIPS / "meeting-me.wav").exists():
        return f"the meeting clips are not in {CLIPS}"
    try:
        from faster_whisper.utils import download_model
        download_model(R.DEFAULT_CONFIG["whisper_model"], local_files_only=True)
    except Exception as e:
        return f"the speech model is not on this PC ({type(e).__name__})"
    return None


def manager(cfg, card):
    sys.modules["soundcard"] = card
    told = []
    return R.MeetingManager(cfg, lambda title, msg: told.append(title)), told


def until_done(mgr, timeout=300):
    end = time.time() + timeout
    while (mgr.in_meeting or mgr.processing) and time.time() < end:
        time.sleep(0.5)
    assert not mgr.in_meeting and not mgr.processing, f"still busy after {timeout} s"


def complete(sid, timeout=60):
    end = time.time() + timeout
    while True:
        n = note(PORT, sid)
        if n["status"] == "complete" or time.time() > end:
            assert n["status"] == "complete", n["status"]
            return n
        time.sleep(0.5)


def t_meeting_becomes_a_note():
    card = FakeCard()
    mgr, told = manager(settings(R, PORT), card)
    mgr.start()
    assert card.played["sys"].wait(60) and card.played["mic"].wait(60), "the meeting did not play"
    on_disk = mgr.capture.session_dir
    mgr.stop(manual=True)                                     # Stop clicked
    until_done(mgr)
    assert "Meeting processed" in told, told
    n = complete(SENT[-1])
    said(n["transcript"], "Others", ["warehouse", "leeds", "vans", "goodbye"])
    said(n["transcript"], "Me", ["builders", "march", "dealers", "tuesday"])
    assert "stand-in read" in n["summary"] and "contract" in n["actions"], (n["summary"], n["actions"])
    assert n["duration_sec"] >= 53, n["duration_sec"]
    assert on_disk and not on_disk.exists(), "the recording on disk was not cleared after the upload"
    assert len(list(R.ARCHIVE_ROOT.glob("wav-*/*.wav"))) >= 2, "both tracks are kept in the archive"
    listed = [e for e in R.sent_meetings() if e["session_id"] == SENT[-1]]
    assert listed and R.server_has_note(listed[0], settings(R, PORT)) is True, "not on the sent list with its note"
    print(f"        note: {len(n['transcript'].splitlines())} lines, {n['duration_sec']} s")


def t_quiet_meeting_stops_itself():
    R.AUTO_STOP_CHECK_SECONDS = 0.5
    card = FakeCard(real_time=True)                           # 54 s meeting with 1 s pauses
    mgr, told = manager(settings(R, PORT, auto_stop_silence_minutes=0.1), card)   # 6 s, not 15 min
    t0 = time.monotonic()
    mgr.start()
    while mgr.in_meeting and time.monotonic() - t0 < 120:
        time.sleep(0.2)
    took = time.monotonic() - t0
    until_done(mgr)
    assert "Recording stopped" in told, told
    assert 56 <= took <= 64, f"stopped {took:.1f} s in; the last word is at 52.9 s, then 6 s of quiet"
    n = complete(SENT[-1])
    said(n["transcript"], "Others", ["warehouse", "goodbye"])
    said(n["transcript"], "Me", ["builders", "dealers"])
    print(f"        stopped by itself {took - 52.9:.1f} s after the last word; note complete")


def t_crash_mid_meeting_is_recovered():
    home = HOME / "crashed"
    log = open(HOME / "crash-child.log", "w")
    child = subprocess.Popen([sys.executable, __file__, "--crash-child"], stdout=log, stderr=subprocess.STDOUT,
                             env=dict(os.environ, MEETING_RECORDER_HOME=str(home)))
    try:
        end = time.time() + 90
        while time.time() < end:
            dirs = list((home / "spool").glob("*")) if (home / "spool").is_dir() else []
            if dirs and R.spool.track_seconds(dirs[0], "sys") >= CRASH_AT + 2:
                break
            time.sleep(0.5)
        assert child.poll() is None, "the recorder ended by itself"
    finally:
        child.kill()                    # the crash: Windows ends it at once, nothing is tidied up
        child.wait(10)
    orphans = R.spool.list_orphans(home / "spool")
    assert len(orphans) == 1, f"unfinished recordings found: {orphans}"
    told, kept = [], len(list(R.ARCHIVE_ROOT.glob("wav-*/*.wav")))
    sid = R.recover_orphan(orphans[0], settings(R, PORT), lambda title, msg: told.append(title))
    assert sid and "Meeting recovered" in told, told
    n = complete(sid)
    said(n["transcript"], "Others", ["warehouse", "friday"])
    said(n["transcript"], "Me", ["builders", "contract"])
    late = [w for w in ("vans", "dealers", "goodbye") if w in n["transcript"].lower()]
    assert not late, f"words from after the crash: {late}"
    assert not orphans[0].exists(), "the recovered recording was not cleared"
    assert len(list(R.ARCHIVE_ROOT.glob("wav-*/*.wav"))) == kept + 2, "the recovered tracks were not archived"
    assert sid in [e["session_id"] for e in R.sent_meetings()], "the recovered meeting is not on the sent list"
    print(f"        killed at {CRASH_AT} s; recovered {n['duration_sec']} s into a note")


if __name__ == "__main__":
    HOME = Path(tempfile.mkdtemp(prefix="nw-e2e-"))
    R = load_recorder(HOME / "recorder")
    R.deliver = spy(R.deliver)
    why = ready()
    if why:
        print(f"  SKIP  all three: {why}")
        sys.exit(0)
    server, PORT = start_server(HOME / "server")
    try:
        check("a made-up meeting, stopped by hand, becomes a note with both voices", t_meeting_becomes_a_note)
        check("a meeting nobody stops ends itself after the quiet time (6 s here)", t_quiet_meeting_stops_itself)
        check("a recorder killed mid-meeting: the recording is recovered into a note", t_crash_mid_meeting_is_recovered)
    finally:
        server.kill()
    print()
    total = _passed + len(_failed)
    if _failed:
        print(f"{_passed}/{total} passed, {len(_failed)} FAILED (logs in {HOME}):")
        for n, e in _failed:
            print(f"  - {n}: {e}")
        sys.exit(1)
    print(f"{_passed}/{total} passed")
