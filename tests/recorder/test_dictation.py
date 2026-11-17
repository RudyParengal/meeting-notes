r"""Tests for hold-to-talk dictation (windows-recorder/dictation.py).

The parts that decide things are tested with stand-ins for the microphone, the
speech model and the Windows desktop. On Windows a few real checks run too:
the password-box detector, the indicator being left out of screen captures,
and the clipboard being put back. None of them presses a key.

Run:  python tests/recorder/test_dictation.py
"""
import io, json, os, sys, tempfile, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
os.environ.pop("NOTEWELL_DICTATION_TEST_WAV", None)
import numpy as np
import dictation as D
from dictation_store import Store

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


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__}")


CTRL, RCTRL, SHIFT, ALT, WIN, V, RIGHT = 0xA2, 0xA3, 0xA0, 0xA4, 0x5B, 0x56, 0x27


# ---------------------------------------------------------------- hotkeys ---
def t_parse_ok():
    assert D.parse_hotkey("Ctrl + Win")[0] == "ctrl+win"
    assert D.parse_hotkey("win+control")[0] == "ctrl+win"
    assert D.parse_hotkey("V+Shift+Alt")[0] == "alt+shift+v"
    assert D.parse_hotkey("rctrl")[0] == "rctrl"
    assert D.parse_hotkey("F13")[0] == "f13"
    assert D.parse_hotkey("ctrl+f9")[0] == "ctrl+f9"


def t_parse_refuses():
    for text, words in [("", "at least two"), ("a", "on its own"), ("space", "on its own"),
                        ("shift+a", "Ctrl, Alt or Win"), ("ctrl+ctrl", "twice"),
                        ("ctrl+rctrl", "same key"), ("ctrl+alt+shift+win+a", "at most four"),
                        ("ctrl+banana", "not a key")]:
        e = raises(ValueError, D.parse_hotkey, text)
        assert words in str(e), (text, str(e))


def hk(**named):
    return D.HotkeyState({n: D.parse_hotkey(t)[1] for n, t in named.items()})


def feed(state, *events):
    """events: (vk, down). Returns every action and the keys that were kept back."""
    acts, kept = [], []
    for vk, down in events:
        a, swallow = state.event(vk, down)
        acts += a
        if swallow:
            kept.append((vk, down))
    return acts, kept


def t_exact_match():
    s = hk(dictate="ctrl+win")
    acts, kept = feed(s, (CTRL, True), (WIN, True))
    assert acts == [("dictate", "start")] and kept == [], (acts, kept)
    acts, _ = feed(s, (WIN, False), (CTRL, False))
    assert acts == [("dictate", "stop")], acts
    # Ctrl+Shift+Win is someone else's shortcut: it must not start dictation
    s = hk(dictate="ctrl+win")
    acts, _ = feed(s, (CTRL, True), (SHIFT, True), (WIN, True))
    assert acts == [], acts
    # right-hand Ctrl works as Ctrl
    s = hk(dictate="ctrl+win")
    acts, _ = feed(s, (RCTRL, True), (WIN, True))
    assert acts == [("dictate", "start")], acts


def t_cancel_on_other_key():
    s = hk(dictate="ctrl+win")
    acts, kept = feed(s, (CTRL, True), (WIN, True), (RIGHT, True), (RIGHT, False), (WIN, False))
    assert acts == [("dictate", "start"), ("dictate", "cancel")], acts
    assert kept == [], "the other key must still reach Windows"


def t_trigger_key_kept_back():
    s = hk(dictate="ctrl+win", repaste="alt+shift+v")
    acts, kept = feed(s, (ALT, True), (SHIFT, True), (V, True), (V, True), (V, False),
                      (SHIFT, False), (ALT, False))
    assert acts == [("repaste", "start"), ("repaste", "stop")], acts
    assert kept == [(V, True), (V, True), (V, False)], kept     # V never typed; Alt/Shift pass


def t_auto_repeat():
    s = hk(dictate="ctrl+win")
    acts, _ = feed(s, (CTRL, True), (WIN, True), (WIN, True), (CTRL, True), (WIN, True))
    assert acts == [("dictate", "start")], acts


def t_missed_release_corrected():
    down = {CTRL, WIN}
    s = D.HotkeyState({"dictate": D.parse_hotkey("ctrl+win")[1]}, is_down=lambda vk: vk in down)
    s.held.add(SHIFT)                     # Shift's release was never seen (e.g. a lock screen)
    acts, _ = feed(s, (CTRL, True), (WIN, True))
    assert acts == [("dictate", "start")], acts
    # without the real-keyboard check the stuck Shift blocks dictation for good
    s = hk(dictate="ctrl+win")
    s.held.add(SHIFT)
    assert feed(s, (CTRL, True), (WIN, True))[0] == []


def t_hotkeys_swap_live():
    s = hk(dictate="ctrl+win")
    s.set_hotkeys({"dictate": D.parse_hotkey("rctrl")[1]})
    assert feed(s, (RCTRL, True))[0] == [("dictate", "start")]


# --------------------------------------------------------- password guard ---
def t_hold_reason():
    assert D.hold_reason("notepad.exe", "Notepad", False) is None
    assert D.hold_reason("chrome.exe", "Chrome_WidgetWin_1", None) is None
    assert "password box" in D.hold_reason("chrome.exe", "Chrome_WidgetWin_1", True)
    assert "password app" in D.hold_reason("1Password.exe", "x", False)
    assert "password app" in D.hold_reason("KeePassXC.exe", "x", False)
    assert "Windows password" in D.hold_reason("explorer.exe", "Credential Dialog Xaml Host", False)
    assert D.hold_reason("CredentialUIBroker.exe", "", False)


# --------------------------------------------------------------- tidy-up ---
def t_tidy_local():
    assert D.tidy_local("um, so I think, you know, we should uh go") == "So I think we should go"
    assert D.tidy_local("I like cats") == "I like cats"
    assert D.tidy_local("it was sort of fine") == "It was sort of fine"
    assert D.tidy_local("Hmm. Okay.") == "Okay."


def t_private_provider():
    for url in ["https://api.groq.com/openai/v1", "https://api.mistral.ai/v1",
                "https://api.cloudflare.com/client/v4/accounts/x/ai/v1", "http://localhost:11434/v1",
                "http://127.0.0.1:4000/v1"]:
        assert D.private_provider(url), url
    for url in ["https://api.cerebras.ai/v1", "https://openrouter.ai/api/v1",
                "https://generativelanguage.googleapis.com/v1beta/openai", "https://api.openscale.so/v1",
                "https://api.groq.com.evil.example/v1", "", None, "not a url"]:
        assert not D.private_provider(url), url


class FakeReply:
    def __init__(self, content):
        self.body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    def __enter__(self):
        return io.BytesIO(self.body)

    def __exit__(self, *a):
        return False


def opener_returning(content, calls):
    def opener(req, timeout):
        calls.append(req)
        return FakeReply(content)
    return opener


def t_ai_tidy():
    calls = []
    ai = {"base_url": "https://api.mistral.ai/v1/", "model": "ministral-14b-latest", "api_key": "k"}
    out = D.ai_tidy("so we meet on monday", ai, opener=opener_returning('"So we meet on Monday."', calls))
    assert out == "So we meet on Monday.", out
    req = calls[0]
    assert req.full_url == "https://api.mistral.ai/v1/chat/completions"
    assert req.get_header("User-agent") == "Notewell-dictation"
    body = json.loads(req.data)
    assert body["messages"][1]["content"] == "so we meet on monday"
    # never sent to a provider that may train on it
    calls.clear()
    raises(PermissionError, D.ai_tidy, "x", {"base_url": "https://api.cerebras.ai/v1"},
           opener=opener_returning("y", calls))
    assert calls == []
    # an answer instead of a tidy-up is thrown away
    raises(ValueError, D.ai_tidy, "hi", ai, opener=opener_returning("Sure! " + "blah " * 30, calls))
    raises(ValueError, D.ai_tidy, "hi", ai, opener=opener_returning("  ", calls))


# ------------------------------------------------------------- the service ---
class FakeDesktop:
    def __init__(self, app="notepad.exe", cls="Notepad", password=False, paste_ok=True):
        self.app, self.cls, self.password, self.paste_ok = app, cls, password, paste_ok
        self.pasted, self.flashes, self.shown, self.hidden, self.masks = [], [], [], 0, 0

    def foreground(self):
        return self.app, self.cls

    def focus_is_password(self):
        return self.password

    def wait_keys_up(self):
        return True

    def paste(self, text):
        self.pasted.append(text)
        return self.paste_ok

    def send_mask_key(self):
        self.masks += 1

    def show(self, state):
        self.shown.append(state)

    def flash(self, text):
        self.flashes.append(text)

    def hide(self):
        self.hidden += 1


class FakeModel:
    ready = True

    def __init__(self, text):
        self.text, self.calls = text, []

    def __call__(self, audio, cfg, hint=""):
        self.calls.append(hint)
        return self.text

    def load(self, *a):
        pass


class FakeMic:
    def __init__(self, audio):
        self.audio, self.started, self.on_live = audio, 0, None

    def start(self, on_live=None):
        self.started += 1
        self.on_live = on_live

    def stop(self):
        return self.audio


tmp = Path(tempfile.mkdtemp())
SPEECH = np.full(D.SR * 2, 0.1, np.float32)          # 2 s, loud enough to be speech


def service(text="hello there", desktop=None, ai=None, cfg=None, audio=SPEECH):
    saves = []
    store = Store(tmp / f"s{len(list(tmp.iterdir()))}.db")
    svc = D.DictationService(dict(cfg or {}), lambda: saves.append(1), store, desktop=desktop or FakeDesktop(),
                             transcriber=FakeModel(text), mic=FakeMic(audio),
                             get_ai=(lambda: ai) if ai is not None else None)
    svc.saves = saves
    return svc


def t_defaults_filled():
    svc = service()
    for k, v in D.DEFAULTS.items():
        assert svc.cfg[k] == v, k
    svc = service(cfg={"dictation_hotkey": "rctrl"})
    assert svc.cfg["dictation_hotkey"] == "rctrl"


def t_silence_not_typed():
    svc = service(audio=np.zeros(D.SR * 2, np.float32))
    assert svc.process(svc.mic.audio, time.perf_counter()) is None
    assert svc.transcribe.calls == [], "the model must not be asked to hear silence"
    assert svc.desktop.pasted == [] and svc.desktop.flashes == ["Didn't hear anything"]
    svc = service(audio=np.full(int(D.SR * 0.1), 0.1, np.float32))   # a tap of the key
    assert svc.process(svc.mic.audio, time.perf_counter()) is None and svc.desktop.pasted == []


def t_silence_log_says_why():
    # 17 Nov: three "Didn't hear anything" in a row could not be explained from the log.
    # The silence line must say how loud it was and how long the microphone took to start.
    import logging
    seen = []
    h = logging.Handler(); h.emit = lambda r: seen.append(r.getMessage())
    D.log.addHandler(h); old_level = D.log.level; D.log.setLevel(logging.INFO)
    try:
        svc = service(audio=np.full(D.SR * 2, 0.001, np.float32))
        svc._mic_wait = 3.2
        assert svc.process(svc.mic.audio, time.perf_counter()) is None
    finally:
        D.log.removeHandler(h); D.log.setLevel(old_level)
    line =[m for m in seen if "silence" in m]
    assert line and "loudness 0.0010" in line[0] and "microphone took 3.2 s" in line[0], seen


def _quiet_speech():
    # 17 Nov, 18:44: three of four dictations said "Didn't hear anything" at average
    # loudness 0.0023-0.0036 with the built-in mic, whose room noise is 0.0004. The
    # microphone start-up and the pauses between words pulled the average under 0.004.
    rng = np.random.default_rng(1)
    parts = [np.zeros(int(D.SR * 0.4), np.float32)]                     # mic starting
    for _ in range(4):
        parts.append(rng.normal(0, 0.005, int(D.SR * 0.5)).astype(np.float32))   # a few words
        parts.append(rng.normal(0, 0.0004, int(D.SR * 0.5)).astype(np.float32))  # a pause
    return np.concatenate(parts)


def t_quiet_speech_with_pauses_is_heard():
    audio = _quiet_speech()
    assert float(np.sqrt(np.mean(audio ** 2))) < D.QUIET_RMS, "the average must be under the old cut-off"
    svc = service(text="this is quiet", audio=audio)
    assert svc.process(audio, time.perf_counter()) is not None, svc.desktop.flashes
    assert svc.desktop.pasted, "quiet speech with pauses must be typed"


def t_room_noise_with_a_click_is_silence():
    rng = np.random.default_rng(2)
    audio = rng.normal(0, 0.0004, D.SR * 3).astype(np.float32)          # a quiet room
    audio[D.SR: D.SR + 320] += 0.05                                      # one bump of the desk
    svc = service(audio=audio)
    assert svc.process(audio, time.perf_counter()) is None
    assert svc.transcribe.calls == [] and svc.desktop.flashes == ["Didn't hear anything"]


def t_typed_with_word_list():
    svc = service(text="I sent it to note well today")
    w = svc.store.add_word("Notewell", "note well")
    svc.store.add_word("Kharkiv", "")
    row = svc.process(SPEECH, time.perf_counter())
    assert svc.desktop.pasted == ["I sent it to Notewell today"], svc.desktop.pasted
    assert "Notewell" in svc.transcribe.calls[0] and "Kharkiv" in svc.transcribe.calls[0]
    h = svc.store.history()[0]
    assert h["id"] == row and h["outcome"] == "pasted" and h["app"] == "notepad.exe"
    assert h["raw_text"] == "I sent it to note well today" and h["seconds"] == 2.0
    assert {x["term"]: x["use_count"] for x in svc.store.words()} == {"Kharkiv": 0, "Notewell": 1}
    assert svc.last["words"] == 6 and svc.last["outcome"] == "pasted"


def t_password_box_held():
    svc = service(text="my secret words", desktop=FakeDesktop(app="chrome.exe", password=True))
    svc.process(SPEECH, time.perf_counter())
    assert svc.desktop.pasted == [], "nothing may be typed into a password box"
    h = svc.store.history()[0]
    assert h["outcome"] == "held" and h["text"] == "my secret words"
    assert "password" in svc.desktop.flashes[0]
    svc = service(desktop=FakeDesktop(app="KeePassXC.exe"))
    svc.process(SPEECH, time.perf_counter())
    assert svc.desktop.pasted == []


def t_tidy_off_never_calls_ai():
    asked = []
    svc = service(text="um so hello", ai={"base_url": "https://api.groq.com/openai/v1", "model": "m"})
    svc.opener = opener_returning("Hello.", asked)
    svc.process(SPEECH, time.perf_counter())
    assert asked == [] and svc.desktop.pasted == ["um so hello"], (asked, svc.desktop.pasted)


def t_tidy_on_private_ai():
    asked = []
    svc = service(text="um so we use note well", cfg={"dictation_tidy": True},
                  ai={"base_url": "https://api.groq.com/openai/v1", "model": "m"})
    svc.store.add_word("Notewell", "note well")
    svc.opener = opener_returning("So we use note well.", asked)
    svc.process(SPEECH, time.perf_counter())
    assert len(asked) == 1
    assert json.loads(asked[0].data)["messages"][1]["content"] == "So we use Notewell"
    assert svc.desktop.pasted == ["So we use Notewell."], svc.desktop.pasted   # word list wins again


def t_tidy_on_other_ai_stays_local():
    asked = []
    svc = service(text="um so hello", cfg={"dictation_tidy": True},
                  ai={"base_url": "https://openrouter.ai/api/v1", "model": "m"})
    svc.opener = opener_returning("Hello.", asked)
    svc.process(SPEECH, time.perf_counter())
    assert asked == [] and svc.desktop.pasted == ["So hello"], svc.desktop.pasted


def t_tidy_ai_failure_keeps_text():
    def broken(req, timeout):
        raise OSError("no internet")
    svc = service(text="um so hello", cfg={"dictation_tidy": True},
                  ai={"base_url": "https://api.groq.com/openai/v1", "model": "m"})
    svc.opener = broken
    svc.process(SPEECH, time.perf_counter())
    assert svc.desktop.pasted == ["So hello"]


def t_failed_paste_recorded():
    svc = service(desktop=FakeDesktop(paste_ok=False))
    svc.process(SPEECH, time.perf_counter())
    assert svc.store.history()[0]["outcome"] == "failed"


def t_repaste():
    svc = service()
    assert svc.repaste() is None and svc.desktop.flashes == ["No dictation yet"]
    svc.process(SPEECH, time.perf_counter())
    assert svc.repaste() == "pasted" and svc.desktop.pasted == ["hello there", "hello there"]


def t_keys_drive_the_mic():
    svc = service()
    svc.on_hotkey("dictate", "start")
    assert svc.state == "listening" and svc.mic.started == 1 and svc.desktop.shown == ["waiting"]
    svc.mic.on_live()                                      # the first sound arrives
    assert svc.desktop.shown == ["waiting", "listening"]
    assert svc.desktop.masks == 1, "Win on its own must not open the Start menu"
    svc.on_hotkey("dictate", "start")                      # repeated: ignored
    assert svc.mic.started == 1
    svc.on_hotkey("dictate", "stop")
    assert svc.state == "writing" and svc._jobs.qsize() == 1
    svc = service()
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "cancel")
    assert svc.state == "ready" and svc._jobs.qsize() == 0 and svc.desktop.hidden == 1
    svc.on_hotkey("repaste", "start")
    assert svc._jobs.qsize() == 1


class _FakeSoundcard:
    """Stands in for the soundcard library; records every microphone open."""
    def __init__(self):
        self.opened = []
        sc = self

        class Rec:
            def __enter__(self):
                sc.opened.append("open"); return self

            def __exit__(self, *a):
                sc.opened.append("close")

            def record(self, numframes):
                time.sleep(0.01)
                return np.zeros((numframes, 1), np.float32)

        class M:
            def recorder(self, samplerate, channels):
                return Rec()
        self.default_microphone = lambda: M()

    def __enter__(self):
        self.real = sys.modules.get("soundcard")
        sys.modules["soundcard"] = self
        return self

    def __exit__(self, *a):
        if self.real is None:
            sys.modules.pop("soundcard", None)
        else:
            sys.modules["soundcard"] = self.real


def t_no_mic_open_at_start_up():
    # 2.3.1 opened the microphone when Notewell started; twice on 17 Nov that froze
    # the laptop's built-in microphone. Switching dictation on must not touch it.
    with _FakeSoundcard() as sc:
        svc = service()
        svc.mic = D.Mic()
        svc._warm()
        time.sleep(0.1)
    assert sc.opened == [], f"the microphone was opened at start-up: {sc.opened}"


def t_mic_says_when_live():
    # The microphone can take 4-7 s to start. The real Mic must say when sound arrives,
    # once, so the bubble can switch from "Wait" to "Listening".
    live = []
    with _FakeSoundcard() as sc:
        mic = D.Mic()
        mic.start(on_live=lambda: live.append(1))
        end = time.perf_counter() + 2
        while not live and time.perf_counter() < end:
            time.sleep(0.01)
        time.sleep(0.05)
        audio = mic.stop()
    assert live == [1], live
    assert mic.opened_after is not None and len(audio) > 0
    assert sc.opened == ["open", "close"], sc.opened


def t_late_mic_does_not_say_listening():
    # Key let go before the microphone started: the bubble must not jump back to "Listening".
    svc = service()
    svc.on_hotkey("dictate", "start")
    svc.on_hotkey("dictate", "stop")
    svc.mic.on_live()
    assert svc.desktop.shown == ["waiting", "writing"], svc.desktop.shown
    svc.on_hotkey("dictate", "start")                      # next press works as normal
    svc.mic.on_live()
    assert svc.desktop.shown[-1] == "listening"


def t_update_checks_everything_first():
    svc = service()
    before = dict(svc.cfg)
    assert "Ctrl, Alt or Win" in str(raises(ValueError, svc.update, {"tidy": True, "hotkey": "shift+a"}))
    assert svc.cfg == before and svc.saves == [], "nothing saved when one change is wrong"
    assert "different" in str(raises(ValueError, svc.update, {"hotkey": "alt+shift+v"}))
    raises(ValueError, svc.update, {"model": "large"})
    raises(ValueError, svc.update, {"language": "english"})
    st = svc.update({"hotkey": "Win + Ctrl", "tidy": True, "model": "small", "language": "", "repaste_hotkey": ""})
    assert svc.cfg["dictation_hotkey"] == "ctrl+win" and svc.cfg["dictation_tidy"] is True
    assert svc.cfg["dictation_repaste_hotkey"] == "" and svc.cfg["dictation_language"] == ""
    assert svc.saves == [1] and st["model"] == "small" and st["repaste_hotkey"] == ""


def t_switch_off_while_running():
    svc = service()
    svc.wanted, svc.state = True, "listening"           # as if Notewell started it and a key is held

    class Hook:
        closed = False

        def close(self):
            Hook.closed = True
    svc._hook = Hook()
    svc.update({"enabled": False})
    assert Hook.closed and svc._hook is None and svc.state == "off" and svc.desktop.hidden == 1


def t_status():
    svc = service(ai={"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1"})
    st = svc.status()
    assert st["tidy_ai"] == {"provider": "openrouter", "allowed": False}, st["tidy_ai"]
    assert st["enabled"] and st["hotkey"] == "ctrl+win" and st["totals"]["dictations"] == 0
    assert service().status()["tidy_ai"] is None


# ------------------------------------------------- real Windows (no keys) ---
def on_ui_thread(make):
    """Run `make()` on a thread with a Windows message loop, as Notewell does."""
    import ctypes
    from ctypes import wintypes as W
    box, ready = {}, threading.Event()

    def run():
        box["tid"] = D.GetCurrentThreadId()
        try:
            box["obj"] = make()
        except Exception as e:
            box["err"] = e
        ready.set()
        msg = W.MSG()
        while D.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            D.TranslateMessage(ctypes.byref(msg))
            D.DispatchMessageW(ctypes.byref(msg))
        if "done" in box:
            box["done"]()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(5)
    if "err" in box:
        raise box["err"]

    def stop(done=None):
        box["done"] = done
        D.PostThreadMessageW(box["tid"], D.WM_QUIT, 0, 0)
        t.join(3)
    return box["obj"], stop


def t_win_structures():
    import ctypes
    assert ctypes.sizeof(D.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert ctypes.sizeof(D.KBDLLHOOKSTRUCT) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)


def t_win_password_box_detected():
    """The accessibility check (used for browsers) on a real password box and a plain one."""
    import ctypes

    def make():
        inst = D.GetModuleHandleW(None)
        top = D.CreateWindowExW(0, "STATIC", "test", 0, 0, 0, 10, 10, None, None, inst, None)
        pw = D.CreateWindowExW(0, "EDIT", "", 0x40000000 | 0x20, 0, 0, 5, 5, top, None, inst, None)
        plain = D.CreateWindowExW(0, "EDIT", "", 0x40000000, 0, 0, 5, 5, top, None, inst, None)
        return top, pw, plain
    (top, pw, plain), stop = on_ui_thread(make)
    try:
        uia = D._UIA()
        a, b = uia.element(pw, timeout=5), uia.element(plain, timeout=5)
        assert a is not None and b is not None, "accessibility layer did not answer"
        assert a["is_password"] is True and b["is_password"] is False, (a, b)
        assert a["pid"] == os.getpid() and a["class"].lower() == "edit", a
    finally:
        stop(lambda: D.DestroyWindow(top))


def t_win_bubble_hidden_from_capture():
    import ctypes
    from ctypes import wintypes as W
    for _ in range(2):          # twice: switching dictation off and on makes a new bubble
        bubble, stop = on_ui_thread(D._Bubble)
        try:
            aff = W.DWORD()
            assert D.GetWindowDisplayAffinity(bubble.hwnd, ctypes.byref(aff))
            assert bubble.hidden_from_capture and aff.value == D.WDA_EXCLUDEFROMCAPTURE, aff.value
            bubble.show("Listening", 0x3C3CE8)
            bubble.hide()
        finally:
            stop(bubble.destroy)


def t_win_clipboard_put_back():
    """Whatever was on the clipboard before a dictation is there again after it.
    Uses the real clipboard for well under a second, then puts it back."""
    bubble, stop = on_ui_thread(D._Bubble)
    try:
        cb = D.Clipboard(bubble.hwnd)
        original = cb.save()
        assert original is not None, "clipboard could not be opened"
        try:
            assert cb.set_text("placeholder one") is not None
            before = cb.save()
            seq = cb.set_text("dictated words")
            now = dict(cb.save())
            assert now[13].decode("utf-16-le").rstrip("\0") == "dictated words"
            assert cb.restore(before, seq)
            assert dict(cb.save())[13].decode("utf-16-le").rstrip("\0") == "placeholder one"
            # if the person copied something meanwhile, theirs is kept
            seq = cb.set_text("dictated again")
            cb.set_text("the person copied this")
            assert cb.restore(before, seq) is False
            assert dict(cb.save())[13].decode("utf-16-le").rstrip("\0") == "the person copied this"
        finally:
            seq = D.GetClipboardSequenceNumber()
            cb.restore(original, seq)
    finally:
        stop(bubble.destroy)


def t_win_hook_starts_and_stops():
    for _ in range(2):
        desk = D.WindowsDesktop()
        h = D._HookThread({"dictate": D.parse_hotkey("ctrl+win")[1]}, lambda *a: None, desktop=desk)
        h.start()
        assert h.ready.wait(5) and h.ok, "keyboard hook not set up"
        assert desk.bubble is not None and desk.clipboard is not None
        h.close()
        assert not h.is_alive() and desk.bubble is None


print("dictation")
check("shortcuts are read in any order and spelling", t_parse_ok)
check("shortcuts that would get in the way are refused, in plain words", t_parse_refuses)
check("dictation starts only on exactly its keys", t_exact_match)
check("another key pressed with the shortcut cancels (Windows shortcut, not dictation)", t_cancel_on_other_key)
check("the V of Alt+Shift+V is not typed; Alt and Shift still reach Windows", t_trigger_key_kept_back)
check("holding the keys down does not restart dictation", t_auto_repeat)
check("a missed key release does not block dictation", t_missed_release_corrected)
check("a new shortcut takes effect at once", t_hotkeys_swap_live)
check("password boxes, prompts and password apps are recognised", t_hold_reason)
check("tidy-up removes filler words and keeps the meaning", t_tidy_local)
check("only AI providers that do not train on text are allowed", t_private_provider)
check("AI tidy-up: right request, refuses other providers, rejects odd answers", t_ai_tidy)
check("settings get their defaults", t_defaults_filled)
check("silence and key taps type nothing", t_silence_not_typed)
check("the silence log line says how loud and how slow the microphone was", t_silence_log_says_why)
check("quiet speech with pauses is heard (judged by its loudest moments)", t_quiet_speech_with_pauses_is_heard)
check("room noise with one bump of the desk is still silence", t_room_noise_with_a_click_is_silence)
check("speech is typed with the word list applied and counted", t_typed_with_word_list)
check("nothing is typed into a password box; it is kept in history", t_password_box_held)
check("tidy-up off: the AI is never contacted", t_tidy_off_never_calls_ai)
check("tidy-up on: a private AI polishes, the word list still wins", t_tidy_on_private_ai)
check("tidy-up on with a provider that trains: stays on this laptop", t_tidy_on_other_ai_stays_local)
check("AI tidy-up failing still types the text", t_tidy_ai_failure_keeps_text)
check("a paste that fails is recorded as failed", t_failed_paste_recorded)
check("paste the last one again", t_repaste)
check("the keys open and close the microphone; the bubble says 'Wait' until it is live", t_keys_drive_the_mic)
check("the microphone is not opened when Notewell starts", t_no_mic_open_at_start_up)
check("the microphone says when sound first arrives", t_mic_says_when_live)
check("a microphone that starts after the key is let go changes nothing", t_late_mic_does_not_say_listening)
check("settings: every change is checked before anything is saved", t_update_checks_everything_first)
check("switching dictation off in Settings stops it at once", t_switch_off_while_running)
check("status says whether the chosen AI may be used", t_status)
if os.name == "nt":
    check("Windows: key and input structures have the right size", t_win_structures)
    check("Windows: a password box is told apart from a normal box", t_win_password_box_detected)
    check("Windows: the indicator is left out of screen shares", t_win_bubble_hidden_from_capture)
    check("Windows: the clipboard is put back after a dictation", t_win_clipboard_put_back)
    check("Windows: the keyboard hook starts and stops cleanly, twice", t_win_hook_starts_and_stops)

total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"   - {n}: {e}")
    sys.exit(1)
print(f"{total}/{total} passed")
