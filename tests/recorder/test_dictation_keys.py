r"""Tests for which other keys end a dictation (chunk 08, step 4).

Run:  python tests/recorder/test_dictation_keys.py

The dictation keys are Ctrl+Shift, so every Ctrl+Shift shortcut (Ctrl+Shift+T,
Ctrl+Shift+V, Ctrl+Shift+Right...) opens the microphone for a moment and the
third key closes it again: 98 "dictation cancelled" lines on 19-20 Nov against
19 real dictations. That is right for a shortcut, but the same rule threw away
a real dictation if any key was touched while speaking. Here real key presses
go through the real key tracker into the real dictation service.
"""
import logging, sys, tempfile, types
from pathlib import Path
import numpy as np

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "windows-recorder"))
import dictation as D
from dictation_store import Store

CTRL, RCTRL, SHIFT, RSHIFT = 0xA2, 0xA3, 0xA0, 0xA1
T, V, RIGHT, ESC, VOLUME_UP = 0x54, 0x56, 0x27, 0x1B, 0xAF
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


class FakeDesktop:
    def __init__(self):
        self.pasted, self.shown, self.hidden = [], [], 0

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
        pass

    def hide(self):
        self.hidden += 1


class FakeMic:
    def __init__(self):
        self.started = 0

    def start(self, on_live=None):
        self.started += 1

    def stop(self):
        return np.full(D.SR * 2, 0.1, np.float32)        # 2 s, loud enough to be speech


class FakeModel:
    ready = True

    def __call__(self, audio, cfg, hint=""):
        return "the words I said"

    def load(self, *a):
        pass


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


tmp = Path(tempfile.mkdtemp())
LOG = Lines()
logging.getLogger("dictation").addHandler(LOG)
logging.getLogger("dictation").setLevel(logging.INFO)


def service():
    svc = D.DictationService({}, lambda: None, Store(tmp / f"s{len(list(tmp.iterdir()))}.db"),
                             desktop=FakeDesktop(), transcriber=FakeModel(), mic=FakeMic())
    svc.show_after = 0
    # stands in for the keyboard hook: the real key tracker, updated as hands-free starts and ends
    svc._hook = types.SimpleNamespace(state=D.HotkeyState(svc._hotkeys()))
    return svc


def play(svc, *events):
    """events: (vk, down) as the keyboard sends them, or a number = seconds the keys stay held.
    Returns the keys that were kept from Windows."""
    kept = []
    for e in events:
        if not isinstance(e, tuple):
            svc._t_start -= e                              # as if that much time went by
            continue
        vk, down = e
        actions, swallow = svc._hook.state.event(vk, down)
        if swallow:
            kept.append(e)
        for a in actions:                                  # what the hook does with them
            svc.on_hotkey(*(a + (vk,) if a[1] == "cancel" else a))
    return kept


def typed(svc):
    """Run what is waiting to be typed; returns the number of jobs typed."""
    n = 0
    while svc._jobs.qsize():
        kind, audio, t = svc._jobs.get()
        svc.process(audio, t)
        n += 1
    return n


def hold(first=CTRL, second=SHIFT):
    return (first, True), (second, True)


# ---------------------------------------------------------------- shortcuts --
def t_quick_shortcut_still_cancels():
    for key in (T, RIGHT, V):                              # Ctrl+Shift+T, +Right, +V used at once
        svc = service()
        kept = play(svc, *hold(), 0.2, (key, True), (key, False), (SHIFT, False), (CTRL, False))
        assert kept == [], "the shortcut key must still reach Windows"
        assert svc.state == "ready" and typed(svc) == 0 and not svc.desktop.pasted, D.key_name(key)
        assert not svc._handsfree, "a shortcut must not leave it listening hands-free"


def t_cancel_log_names_the_key():
    LOG.lines.clear()
    svc = service()
    play(svc, *hold(), 0.1, (T, True), (T, False), (SHIFT, False), (CTRL, False))
    assert any("T was pressed with the shortcut" in x for x in LOG.lines), LOG.lines


# ------------------------------------------------- nothing harmless cancels --
def t_ctrl_shift_v_while_dictating_keeps_the_words():
    LOG.lines.clear()
    svc = service()
    kept = play(svc, *hold(), 3.0, (V, True), (V, False), 2.0, (SHIFT, False), (CTRL, False))
    assert kept == []
    assert typed(svc) == 1 and svc.desktop.pasted == ["the words I said"], svc.desktop.pasted
    assert any("V pressed while dictating; still listening" in x for x in LOG.lines), LOG.lines


def t_volume_or_stray_key_while_dictating():
    for key in (VOLUME_UP, T, RIGHT):
        svc = service()
        play(svc, *hold(), 1.5, (key, True), (key, True), (key, False), 1.0, (CTRL, False), (SHIFT, False))
        assert typed(svc) == 1 and svc.desktop.pasted, D.key_name(key)


def t_any_release_order_types_it():
    orders = [((CTRL, SHIFT), (SHIFT, CTRL)), ((CTRL, SHIFT), (CTRL, SHIFT)),
              ((SHIFT, CTRL), (SHIFT, CTRL)), ((SHIFT, CTRL), (CTRL, SHIFT)),
              ((CTRL, RSHIFT), (RSHIFT, CTRL)), ((RCTRL, SHIFT), (RCTRL, SHIFT))]
    for down, up in orders:
        svc = service()
        play(svc, (down[0], True), (down[1], True), 2.0, (down[1], True),   # auto-repeat
             (up[0], False), (up[1], False))
        assert typed(svc) == 1 and svc.desktop.pasted == ["the words I said"], (down, up)


def t_handsfree_any_release_order():
    for up in ((CTRL, SHIFT), (SHIFT, CTRL)):
        svc = service()
        svc.tap_seconds = D.TAP_SECONDS
        play(svc, *hold(), 0.1, (up[0], False), (up[1], False))          # tap: hands-free
        assert svc._handsfree and svc.state == "listening"
        play(svc, 5.0, *hold(SHIFT, CTRL), 0.1, (V, True), (V, False))   # Ctrl+Shift+V meanwhile
        play(svc, (up[1], False), (up[0], False))
        assert svc._handsfree and svc.state == "listening", "Ctrl+Shift+V ended hands-free"
        play(svc, *hold(), 0.1, (up[1], False), (up[0], False))          # tap again to finish
        assert typed(svc) == 1 and svc.desktop.pasted == ["the words I said"]


def t_escape_still_throws_it_away():
    svc = service()
    play(svc, *hold(), 4.0, (ESC, True), (ESC, False), (SHIFT, False), (CTRL, False))
    assert svc.state == "ready" and typed(svc) == 0 and not svc.desktop.pasted


def t_key_names():
    assert [D.key_name(k) for k in (T, 0x35, 0x74, VOLUME_UP, RIGHT, 0xBA, None)] == \
        ["T", "5", "F5", "Volume up", "Right", "key 0xba", "another key"]


def t_constant():
    assert D.SHORTCUT_SECONDS == 1.0 and service().shortcut_seconds == 1.0


check("a quick Ctrl+Shift+T / +Right / +V still closes the microphone", t_quick_shortcut_still_cancels)
check("the log says which key it was", t_cancel_log_names_the_key)
check("Ctrl+Shift+V while dictating: keeps listening, types on release", t_ctrl_shift_v_while_dictating_keeps_the_words)
check("volume or a stray key while dictating does not lose the words", t_volume_or_stray_key_while_dictating)
check("keys let go in any order (left or right keys): it types", t_any_release_order_types_it)
check("hands-free: any order, and Ctrl+Shift+V in between, keep it going", t_handsfree_any_release_order)
check("Esc while dictating still throws it away on purpose", t_escape_still_throws_it_away)
check("key names for the log", t_key_names)
check("the shortcut window is 1 second", t_constant)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
