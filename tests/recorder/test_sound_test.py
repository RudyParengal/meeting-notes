r"""Tests for the tray's "Test the sound (5 seconds)" verdict (2.7.1).

Run:  python tests/recorder/test_sound_test.py

Before 2.7.1 the test listened only to the speakers, in silence, so it said "No audio"
unless something happened to be playing, and it never checked the microphone.
It now plays a beep and listens to both sides; this checks what it then says.
"""
import sys
from pathlib import Path

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


def t_both_heard():
    ok, text = R.sound_test_verdict({"call": 0.08, "mic": 0.03}, "Speakers")
    assert ok, text
    assert "Call sound on Speakers: heard clearly." in text, text
    assert "Your microphone: heard clearly." in text, text
    assert "Meetings will record both sides." in text, text


def t_silent_mic_fails_with_a_fix():
    ok, text = R.sound_test_verdict({"call": 0.08, "mic": 0.0}, "Speakers")
    assert not ok
    assert "Your microphone: silence." in text, text
    assert "not muted" in text and "Kaspersky" in text, text
    assert "Listen to" not in text, text          # the speaker advice is not shown


def t_silent_call_fails_with_a_fix():
    ok, text = R.sound_test_verdict({"call": 0.0, "mic": 0.05}, "Headset")
    assert not ok
    assert "Call sound on Headset: silence." in text, text
    assert "Listen to" in text, text


def t_faint_is_still_heard():
    ok, text = R.sound_test_verdict({"call": 0.005, "mic": 0.002}, "Speakers")
    assert ok, text
    assert text.count("very quietly") == 2, text


def t_device_error_is_named():
    ok, text = R.sound_test_verdict({"call": 0.08, "mic": "RuntimeError: no device"}, "Speakers")
    assert not ok
    assert "could not be opened (RuntimeError: no device)" in text, text


def t_missing_side_is_not_called_heard():
    ok, text = R.sound_test_verdict({"call": 0.08}, "Speakers")   # the mic thread never finished
    assert not ok
    assert "Your microphone: could not be opened" in text, text


check("both sides heard: says so, and that meetings record both", t_both_heard)
check("silent microphone: fails and says how to fix it", t_silent_mic_fails_with_a_fix)
check("silent call sound: fails and points to Listen to", t_silent_call_fails_with_a_fix)
check("faint sound still counts as heard, flagged as quiet", t_faint_is_still_heard)
check("a device that will not open is named", t_device_error_is_named)
check("a side that never answered is not reported as heard", t_missing_side_is_not_called_heard)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
