r"""Tests for soundgaps.py: sound card gap warnings counted, not printed.

Run:  python tests/recorder/test_soundgaps.py

On 19 Nov notewell.log held 11,076 lines, 11,072 of them one soundcard warning.
These prove the warning now becomes one counted line a minute, other warnings
still show, and the tone check can tell a clean recording from a broken one.
"""
import io, logging, sys, threading, warnings
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import soundgaps

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


def tone(seconds, freq=soundgaps.TONE_HZ, amp=0.003):
    n = np.arange(int(SR * seconds))
    return (amp * np.sin(2 * np.pi * freq * n / SR)).astype(np.float32)


class SoundcardRuntimeWarning(RuntimeWarning):
    pass


def t_buffer_is_half_a_second():
    assert soundgaps.blocksize(16000) == 8000
    assert soundgaps.blocksize(48000) == 24000


def t_thousand_warnings_one_line():
    soundgaps.install()
    soundgaps.flush()                       # start from nothing
    err, real_err = io.StringIO(), sys.stderr
    logged = []
    h = logging.Handler()
    h.emit = lambda r: logged.append(r.getMessage())
    soundgaps.log.addHandler(h)
    try:
        sys.stderr = err

        def gaps():
            for _ in range(1000):
                warnings.warn("data discontinuity in recording", SoundcardRuntimeWarning)
        t = threading.Thread(target=gaps, name="dictation-mic")
        t.start(); t.join()
        warnings.resetwarnings()            # what other libraries do; must not bring the flood back
        soundgaps.install()                 # second call is harmless
        warnings.filterwarnings("always", message=soundgaps.MESSAGE)
        t = threading.Thread(target=lambda: warnings.warn("data discontinuity in recording",
                             SoundcardRuntimeWarning), name="meeting-mic")
        t.start(); t.join()
    finally:
        sys.stderr = real_err
    line = soundgaps.flush()
    soundgaps.log.removeHandler(h)
    assert err.getvalue() == "", f"printed: {err.getvalue()[:80]!r}"
    assert line == "sound card gaps in the last minute: 1001 (dictation-mic 1000, meeting-mic 1)", line
    assert logged == [line], logged
    assert soundgaps.flush() is None, "a quiet minute writes nothing"
    assert soundgaps.totals["dictation-mic"] >= 1000, "the running total is kept"


def t_other_warnings_still_show():
    soundgaps.install()
    err, real_err = io.StringIO(), sys.stderr
    try:
        sys.stderr = err
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("something else entirely", UserWarning)
    finally:
        sys.stderr = real_err
    assert "something else entirely" in err.getvalue(), err.getvalue()


def t_clean_tone_has_no_breaks():
    assert soundgaps.find_breaks(tone(10), SR) == 0


def t_three_lost_pieces_are_three_breaks():
    x = tone(10)
    for at in (30000, 80000, 130000):      # take out 10 ms three times
        x = np.concatenate([x[:at], x[at + 160:]])
    assert soundgaps.find_breaks(x, SR) == 3, soundgaps.find_breaks(x, SR)


def t_silence_slipped_in_is_a_break():
    x = tone(10)
    x = np.concatenate([x[:50000], np.zeros(800, np.float32), x[50000:]])
    assert soundgaps.find_breaks(x, SR) >= 1


def t_summary_thread_writes_on_its_own():
    soundgaps.flush()
    soundgaps.note_gap("meeting-system")
    stop = threading.Event()
    got = []
    h = logging.Handler()
    h.emit = lambda r: (got.append(r.getMessage()), stop.set())
    soundgaps.log.addHandler(h)
    try:
        threading.Thread(target=soundgaps._every_minute, args=(stop, 0.05), daemon=True).start()
        stop.wait(2)
    finally:
        soundgaps.log.removeHandler(h)
    assert got == ["sound card gaps in the last minute: 1 (meeting-system 1)"], got


check("Windows gets half a second of room (8000 frames at 16 kHz)", t_buffer_is_half_a_second)
check("1000 gap warnings print nothing and make one counted line", t_thousand_warnings_one_line)
check("other warnings still show", t_other_warnings_still_show)
check("tone check: a clean tone has no breaks", t_clean_tone_has_no_breaks)
check("tone check: three lost 10 ms pieces are three breaks", t_three_lost_pieces_are_three_breaks)
check("tone check: silence slipped in is a break", t_silence_slipped_in_is_a_break)
check("the minute timer writes the line by itself", t_summary_thread_writes_on_its_own)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
