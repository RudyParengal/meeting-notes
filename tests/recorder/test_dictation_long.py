r"""Tests for long dictation: keys held stop at 5 minutes, hands-free runs to 20.

Run:  python tests/recorder/test_dictation_long.py

The real microphone code (dictation.Mic) is fed a recording through a stand-in
for the soundcard library. With NOTEWELL_LONG_CLIP set to a WAV over 5 minutes
(chunk 08 made a 6-minute spoken one, runbook/progress/out/08/long-dictation.wav),
the real speech model also writes it out, to prove the end was not lost.
"""
import os, sys, tempfile, threading, time, wave
from pathlib import Path
import numpy as np

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "windows-recorder"))
os.environ.pop("NOTEWELL_DICTATION_TEST_WAV", None)
import dictation as D
from dictation_store import Store

CLIP = os.environ.get("NOTEWELL_LONG_CLIP") or str(
    APP.parent / "runbook" / "progress" / "out" / "08" / "long-dictation.wav")
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


def load_clip():
    if os.path.exists(CLIP):
        with wave.open(CLIP) as w:
            assert w.getframerate() == D.SR and w.getnchannels() == 1
            return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    t = np.arange(D.SR * 360) / D.SR                       # 6 minutes of something loud
    return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


AUDIO = load_clip()


class FakeSoundcard:
    """Hands the clip out as fast as it is asked for, once `go` is set; then silence in real time."""

    def __init__(self, audio):
        self.audio, self.go, self.done = audio, threading.Event(), threading.Event()

    def default_microphone(self):
        card = self

        class Rec:
            def __init__(self):
                self.pos = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def record(self, numframes):
                card.go.wait(10)
                if self.pos >= len(card.audio):
                    card.done.set()
                    time.sleep(numframes / D.SR)
                    return np.zeros((numframes, 1), np.float32)
                piece = card.audio[self.pos:self.pos + numframes]
                self.pos += numframes
                return piece.reshape(-1, 1)

        class Mic:
            def recorder(self, samplerate, channels, blocksize=None):
                return Rec()
        return Mic()


class FakeDesktop:
    def __init__(self):
        self.pasted, self.flashes, self.shown = [], [], []

    def foreground(self):
        return "notepad.exe", "Notepad"

    def focus_is_password(self):
        return False

    def wait_keys_up(self):
        return True

    def paste(self, text):
        self.pasted.append(text)
        return True

    def send_mask_key(self):
        pass

    def show(self, state):
        self.shown.append(state)

    def flash(self, text):
        self.flashes.append(text)

    def hide(self):
        pass


class FakeModel:
    ready = True

    def __init__(self):
        self.heard = []

    def __call__(self, audio, cfg, hint=""):
        self.heard.append(len(audio) / D.SR)
        return f"{len(audio) / D.SR:.0f} seconds of words"

    def load(self, *a):
        pass


tmp = Path(tempfile.mkdtemp())


def service(audio=AUDIO, model=None):
    card = FakeSoundcard(audio)
    sys.modules["soundcard"] = card
    svc = D.DictationService({}, lambda: None, Store(tmp / f"s{len(list(tmp.iterdir()))}.db"),
                             desktop=FakeDesktop(), transcriber=model or FakeModel(), mic=D.Mic())
    svc.show_after = 0
    svc.card = card
    return svc


def type_it(svc):
    kind, audio, t = svc._jobs.get(timeout=10)
    assert kind == "dictate"
    return svc.process(audio, t)


def wait_for(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def t_limits():
    assert D.MAX_SECONDS == 300 and D.HANDSFREE_SECONDS == 1200 and D.WARN_SECONDS == 60


def t_held_keys_stop_at_five_minutes():
    svc = service()
    svc.tap_seconds = 0                                    # every press is a hold
    svc.on_hotkey("dictate", "start")
    svc.card.go.set()
    assert wait_for(lambda: not svc.mic._thread.is_alive(), 20), "the microphone never stopped"
    svc.on_hotkey("dictate", "stop")
    assert type_it(svc)
    assert svc.transcribe.heard == [300.0], svc.transcribe.heard


def t_handsfree_keeps_all_six_minutes():
    svc = service()
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")                       # a tap: hands-free
    assert svc._handsfree and svc.mic.max_seconds > 1200
    svc.card.go.set()
    assert svc.card.done.wait(20), "the clip was not all heard"
    svc.on_hotkey("dictate", "start")                      # tap again to finish
    svc.on_hotkey("dictate", "stop")
    assert type_it(svc)
    heard = svc.transcribe.heard[0]
    assert heard >= len(AUDIO) / D.SR > 300, (heard, len(AUDIO) / D.SR)
    assert svc.desktop.pasted == [f"{heard:.0f} seconds of words"], svc.desktop.pasted


def t_one_minute_left_then_types_at_limit():
    svc = service(audio=AUDIO[:D.SR * 4])                 # the stand-in hands it over at once
    svc.handsfree_seconds, svc.warn_seconds = 1.5, 0.75    # 20 and 1 minutes, shortened
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")
    svc.card.go.set()
    assert wait_for(lambda: "lastminute" in svc.desktop.shown, 3), svc.desktop.shown
    assert svc.state == "listening", "still listening during the last minute"
    if hasattr(D, "_Bubble"):
        assert D._Bubble.STYLES["lastminute"][0] == "1 minute left"
    assert wait_for(lambda: svc._jobs.qsize() == 1, 3), "the limit did not end it"
    assert svc.state == "writing" and not svc._handsfree
    row = type_it(svc)
    assert row and svc.desktop.pasted, "what was heard was not typed"
    assert svc.transcribe.heard[0] >= 4, f"audio thrown away: {svc.transcribe.heard}"


def t_tap_at_the_limit_types_once():
    svc = service(audio=AUDIO[:D.SR * 5])
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")
    svc.card.go.set()
    svc._reach_limit(svc._press)                           # the limit and a tap together
    svc.on_hotkey("dictate", "stop")
    assert svc._jobs.qsize() == 1, svc._jobs.qsize()


def t_finishing_early_cancels_the_limit():
    svc = service(audio=AUDIO[:D.SR * 5])
    svc.handsfree_seconds, svc.warn_seconds = 0.6, 0.3
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")
    svc.card.go.set()
    assert svc.card.done.wait(5)
    svc.on_hotkey("dictate", "start")                      # tapped again before the warning
    svc.on_hotkey("dictate", "stop")
    type_it(svc)
    time.sleep(0.8)
    assert "lastminute" not in svc.desktop.shown and svc._jobs.qsize() == 0
    assert len(svc.desktop.pasted) == 1


def t_real_speech_model_writes_the_end():
    if not os.path.exists(CLIP):
        return "skip"
    svc = service(model=D.Transcriber())
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")
    svc.card.go.set()
    assert svc.card.done.wait(20)
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")
    t0 = time.monotonic()
    assert type_it(svc)
    text = svc.desktop.pasted[0].lower()
    print(f"        {len(text.split())} words typed from {len(AUDIO) / D.SR:.0f} s in "
          f"{time.monotonic() - t0:.0f} s; ends: ...{text[-60:]!r}")
    assert "orchard" in text, "the start is missing"
    assert "elephant" in text, "the end (after 5 minutes) is missing"


check("limits: 5 minutes held, 20 hands-free, warning 1 minute before", t_limits)
check("keys held: the stuck-key guard still stops at 5 minutes", t_held_keys_stop_at_five_minutes)
check("hands-free: a 6-minute clip is heard and typed in full", t_handsfree_keeps_all_six_minutes)
check("hands-free: '1 minute left', then it types what it heard", t_one_minute_left_then_types_at_limit)
check("a tap at the same moment as the limit types it once", t_tap_at_the_limit_types_once)
check("finishing early: no warning or second typing later", t_finishing_early_cancels_the_limit)
check("real speech model: 6-minute clip typed with its last words", t_real_speech_model_writes_the_end)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed" + (f", {len(_skipped)} skipped" if _skipped else ""))
