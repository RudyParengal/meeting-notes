r"""Tests for the live transcript: spool slicing, the decode loop, the window.

Run:  python tests/recorder/test_live.py

Whisper is stubbed out. What is tested is the loop around it - which windows
get decoded, which are skipped, what happens when it falls behind - because
that is where a live view goes wrong, not in the model.
"""
import sys, tempfile, time, threading
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import spool
import live_transcribe

SR = 16000
_passed = 0
_failed = []


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def tone(seconds, amp=0.5):
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


class FakeWindow:
    def __init__(self):
        self.lines = []
        self.status = None

    def append(self, text, speaker=None):
        self.lines.append((speaker, text))

    def set_status(self, t):
        self.status = t


class FakeModel:
    """Stands in for Whisper. Records what it was asked to decode."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append((len(audio), kw))
        seg = type("S", (), {"text": f"window of {len(audio)} samples"})()
        return [seg], None


def install_fake_model(monkey_cfg=None):
    fake = FakeModel()
    live_transcribe.get_live_model = lambda cfg: fake
    return fake


# ---------------------------------------------------------------
print("\nReading behind the write head")


def t_slice_returns_exact_window():
    d = Path(tempfile.mkdtemp()) / "s"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(3.0))
    w.close()
    out = spool.read_track_slice(d, "sys", 0, SR)
    assert out is not None and len(out) == SR


def t_slice_refuses_a_partial_window():
    """A short read would be decoded now and again later - duplicating text."""
    d = Path(tempfile.mkdtemp()) / "s"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(1.5))
    w.close()
    assert spool.read_track_slice(d, "sys", 0, 2 * SR) is None


def t_slice_reads_from_an_offset():
    d = Path(tempfile.mkdtemp()) / "s"
    w = spool.SpoolWriter(d, SR)
    first, second = tone(1.0, amp=0.2), tone(1.0, amp=0.8)
    w.append_system(first)
    w.append_system(second)
    w.close()
    out = spool.read_track_slice(d, "sys", SR, SR)
    assert np.array_equal(out, second), "offset read returned the wrong samples"


def t_track_samples_counts_without_reading():
    d = Path(tempfile.mkdtemp()) / "s"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(2.0))
    w.close()
    assert spool.track_samples(d, "sys") == 2 * SR
    assert spool.track_samples(d, "mic") == 0


check("a full window reads back exactly", t_slice_returns_exact_window)
check("a partial window returns None, not a short read", t_slice_refuses_a_partial_window)
check("reads from an offset return the right samples", t_slice_reads_from_an_offset)
check("sample count comes from the file size", t_track_samples_counts_without_reading)

# ---------------------------------------------------------------
print("\nThe decode loop")


def _prepared(seconds_of_audio, amp=0.5, window_seconds=2):
    d = Path(tempfile.mkdtemp()) / "s"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(seconds_of_audio, amp=amp))
    w.append_mic(np.zeros(int(seconds_of_audio * SR), dtype=np.float32))
    w.close()
    cfg = {"sample_rate": SR, "live_window_seconds": window_seconds,
           "live_poll_seconds": 0.05, "live_min_rms": 0.005}
    return d, cfg


def t_loud_windows_are_decoded():
    fake = install_fake_model()
    d, cfg = _prepared(6.0, amp=0.5, window_seconds=2)
    win = FakeWindow()
    lt = live_transcribe.LiveTranscriber(d, cfg, win, SR)
    chunk = 2 * SR
    for _ in range(3):
        lt._maybe_decode("sys", "Others", chunk)
    assert lt.decoded_windows == 3, lt.decoded_windows
    assert len(win.lines) == 3, win.lines
    assert all(spk == "Others" for spk, _ in win.lines)


def t_quiet_windows_are_skipped_not_decoded():
    """Silence used to be decoded and came back as invented text in Arabic."""
    fake = install_fake_model()
    d, cfg = _prepared(6.0, amp=0.0005, window_seconds=2)   # near-silence
    win = FakeWindow()
    lt = live_transcribe.LiveTranscriber(d, cfg, win, SR)
    for _ in range(3):
        lt._maybe_decode("sys", "Others", 2 * SR)
    assert lt.skipped_quiet == 3, lt.skipped_quiet
    assert lt.decoded_windows == 0, "a silent window was sent to the model"
    assert win.lines == [], "silence produced text"


def t_silent_mic_track_produces_nothing():
    fake = install_fake_model()
    d, cfg = _prepared(6.0, amp=0.5, window_seconds=2)
    win = FakeWindow()
    lt = live_transcribe.LiveTranscriber(d, cfg, win, SR)
    for _ in range(3):
        lt._maybe_decode("mic", "Me", 2 * SR)
    assert win.lines == [], "the silent mic track produced text"


def t_language_is_forced():
    fake = install_fake_model()
    d, cfg = _prepared(4.0, amp=0.5, window_seconds=2)
    lt = live_transcribe.LiveTranscriber(d, cfg, FakeWindow(), SR)
    lt._maybe_decode("sys", "Others", 2 * SR)
    assert fake.calls, "model was never called"
    assert fake.calls[0][1].get("language") == "en", fake.calls[0][1]


def t_falls_behind_then_skips_forward():
    """A live view three minutes stale is worse than one with a gap."""
    fake = install_fake_model()
    d, cfg = _prepared(30.0, amp=0.5, window_seconds=2)
    lt = live_transcribe.LiveTranscriber(d, cfg, FakeWindow(), SR)
    chunk = 2 * SR
    lt._maybe_decode("sys", "Others", chunk)
    # 30s is on disk and nothing has been consumed, so the loop is 15 windows
    # behind. It must jump to the last window rather than work through them:
    # position moves to (available - chunk), then advances by chunk.
    assert lt._pos["sys"] == 30 * SR, (
        f"expected to land at the write head ({30*SR}), got {lt._pos['sys']}")
    assert lt.decoded_windows == 1, "skipping forward should still decode once"


def t_keeping_up_does_not_skip():
    """The counterpart: only a long lag justifies dropping audio."""
    fake = install_fake_model()
    d, cfg = _prepared(4.0, amp=0.5, window_seconds=2)
    lt = live_transcribe.LiveTranscriber(d, cfg, FakeWindow(), SR)
    chunk = 2 * SR
    lt._maybe_decode("sys", "Others", chunk)
    assert lt._pos["sys"] == chunk, (
        f"skipped forward while only 2 windows behind: {lt._pos['sys']}")


def t_no_audio_yet_is_not_an_error():
    fake = install_fake_model()
    d, cfg = _prepared(0.5, amp=0.5, window_seconds=2)
    lt = live_transcribe.LiveTranscriber(d, cfg, FakeWindow(), SR)
    assert lt._maybe_decode("sys", "Others", 2 * SR) is False
    assert lt.decoded_windows == 0


check("loud windows are decoded and shown", t_loud_windows_are_decoded)
check("quiet windows are skipped, never decoded", t_quiet_windows_are_skipped_not_decoded)
check("a silent mic track produces nothing", t_silent_mic_track_produces_nothing)
check("the language is forced, not detected", t_language_is_forced)
check("falling behind skips forward to the write head", t_falls_behind_then_skips_forward)
check("keeping up does not skip", t_keeping_up_does_not_skip)
check("no audio yet is not an error", t_no_audio_yet_is_not_an_error)

# ---------------------------------------------------------------
print("\nThe window itself")


def t_window_opens_appends_and_closes():
    from live_window import LiveWindow
    w = LiveWindow(title="test-window")
    w.start()
    for _ in range(40):
        if w.is_open():
            break
        time.sleep(0.1)
    assert w.is_open(), "window never opened"
    for i in range(5):
        w.append(f"line {i}", speaker="Me" if i % 2 else "Speaker 1")
    time.sleep(0.6)
    assert w.is_open(), "window died while being written to"
    w.close()
    for _ in range(40):
        if not w.is_open():
            break
        time.sleep(0.1)
    assert not w.is_open(), "window did not close"


def t_appending_after_close_does_not_raise():
    from live_window import LiveWindow
    w = LiveWindow(title="test-window-2")
    w.start()
    time.sleep(1.0)
    w.close()
    time.sleep(1.0)
    w.append("after close")      # must be silently harmless
    w.set_status("after close")


check("the window opens, takes lines from another thread, and closes", t_window_opens_appends_and_closes)
check("writing to a closed window does not raise", t_appending_after_close_does_not_raise)

# ---------------------------------------------------------------
print("\nGuards")


def t_live_view_needs_the_spool():
    import recorder
    cfg = {**recorder.DEFAULT_CONFIG, "live_transcript": True, "spool_enabled": False}
    notes = []
    mgr = recorder.MeetingManager(cfg, lambda *a: notes.append(a))
    mgr.capture.session_dir = None
    mgr._start_live_view()
    assert mgr._live_window is None, "live view started without a spool to read"
    assert notes, "the user was not told why"


def t_live_view_off_by_default():
    import recorder
    assert recorder.DEFAULT_CONFIG["live_transcript"] is False


check("no spool means no live view, and the user is told", t_live_view_needs_the_spool)
check("the live view is off by default", t_live_view_off_by_default)

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
