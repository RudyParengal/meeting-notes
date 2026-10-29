r"""Tests for what the Notewell desktop app needs from the recorder: a controller
the dashboard can drive, a timer and "transcribing" state, a chosen language,
and no supervisor revival inside the app.

    python tests/recorder/test_embedded.py

No audio device, no network, no server needed.
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="nw-test-")
os.environ["MEETING_RECORDER_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))

import recorder

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


class FakeCapture:
    sample_rate = 16000
    level_system = level_mic = 0.0

    def start(self):
        pass

    def stop(self):
        return None, None, 0          # under 10 s: _finish discards it quietly


def manager():
    cfg = dict(recorder.DEFAULT_CONFIG, sound_effects=False)
    mgr = recorder.MeetingManager(cfg, lambda *a: None)
    mgr.capture = FakeCapture()
    return mgr


# ---------------------------------------------------------------
print("\nWhat the dashboard reads")


def t_start_records_the_time():
    mgr = manager()
    assert mgr.started_at is None and mgr.processing is False
    mgr.start()
    assert mgr.in_meeting and mgr.started_at is not None


def t_processing_is_true_until_the_meeting_is_finished():
    mgr = manager()
    mgr.start()
    seen = []
    real = mgr._finish
    mgr._finish = lambda: (seen.append(mgr.processing), real())
    mgr.stop()
    for t in list(__import__("threading").enumerate()):
        if t.name != "MainThread" and t.daemon:
            t.join(timeout=5)
    assert seen == [True], seen
    assert mgr.processing is False


def t_stopping_the_capture_zeroes_the_meters():
    cap = recorder.DualAudioCapture()
    cap.level_system, cap.level_mic = 0.4, 0.2
    cap.stop()
    assert cap.level_system == 0.0 and cap.level_mic == 0.0


check("starting a meeting records when it began", t_start_records_the_time)
check("'transcribing' shows until the meeting is finished", t_processing_is_true_until_the_meeting_is_finished)
check("stopping zeroes the level meters", t_stopping_the_capture_zeroes_the_meters)

# ---------------------------------------------------------------
print("\nSpeech model and language")


class FakeWhisper:
    loads = []

    def __init__(self, model_id, **kw):
        FakeWhisper.loads.append(model_id)
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        return iter(()), None


def with_fake_whisper(fn):
    def run():
        sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeWhisper)
        recorder._whisper_model, recorder._whisper_name = None, None
        FakeWhisper.loads.clear()
        try:
            fn()
        finally:
            sys.modules.pop("faster_whisper", None)
            recorder._whisper_model, recorder._whisper_name = None, None
    return run


def t_a_chosen_language_reaches_whisper():
    cfg = dict(recorder.DEFAULT_CONFIG, whisper_model="small", language="de")
    recorder.transcribe([0.0], cfg)
    assert recorder._whisper_model.kwargs["language"] == "de"


def t_no_language_means_detect():
    cfg = dict(recorder.DEFAULT_CONFIG, whisper_model="small", language="")
    recorder.transcribe([0.0], cfg)
    assert "language" not in recorder._whisper_model.kwargs


def t_changing_the_model_reloads_it():
    cfg = dict(recorder.DEFAULT_CONFIG, whisper_model="small")
    recorder.get_whisper(cfg)
    recorder.get_whisper(cfg)
    cfg["whisper_model"] = "base"
    recorder.get_whisper(cfg)
    assert FakeWhisper.loads == ["small", "base"], FakeWhisper.loads


check("a chosen language is passed to the speech model", with_fake_whisper(t_a_chosen_language_reaches_whisper))
check("no language set means the model detects it", with_fake_whisper(t_no_language_means_detect))
check("changing the speech model loads the new one, once", with_fake_whisper(t_changing_the_model_reloads_it))

# ---------------------------------------------------------------
print("\nThe controller the app attaches")


class FakeIcon:
    icon = "idle"


def t_controller_drives_the_meeting_and_the_tray_colour():
    mgr = manager()
    icon = FakeIcon()
    c = recorder._controller(mgr, mgr.cfg, icon, "idle", "rec")
    assert set(c) == {"manager", "cfg", "start", "stop", "devices", "save_cfg"}
    c["start"]()
    assert mgr.in_meeting and icon.icon == "rec"
    c["stop"]()
    assert not mgr.in_meeting and icon.icon == "idle"


def t_save_cfg_writes_the_config_file():
    mgr = manager()
    mgr.cfg["language"] = "fr"
    recorder._controller(mgr, mgr.cfg, FakeIcon(), "idle", "rec")["save_cfg"]()
    assert json.loads(recorder.CONFIG_PATH.read_text())["language"] == "fr"


check("start/stop work and recolour the tray icon", t_controller_drives_the_meeting_and_the_tray_colour)
check("saving settings writes config.json", t_save_cfg_writes_the_config_file)

# ---------------------------------------------------------------
print("\nSupervisor revival")


class _Stop(Exception):
    pass


def heartbeat_once(embedded):
    """Run one pass of heartbeat_loop with the supervisor looking dead."""
    started = []
    old = (recorder.EMBEDDED, recorder.SUPERVISOR_ALIVE, recorder.SUPERVISOR_LAUNCHER,
           recorder.subprocess.Popen, recorder.time.sleep)
    launcher = Path(_HOME) / "start-supervisor.vbs"
    launcher.write_text("")
    recorder.EMBEDDED = embedded
    recorder.SUPERVISOR_ALIVE = Path(_HOME) / "never-written.alive"
    recorder.SUPERVISOR_LAUNCHER = launcher
    recorder.subprocess.Popen = lambda *a, **k: started.append(a)

    def stop(_):
        raise _Stop

    recorder.time.sleep = stop
    try:
        recorder.heartbeat_loop()
    except _Stop:
        pass
    finally:
        (recorder.EMBEDDED, recorder.SUPERVISOR_ALIVE, recorder.SUPERVISOR_LAUNCHER,
         recorder.subprocess.Popen, recorder.time.sleep) = old
    return started


check("the standalone recorder still revives a dead supervisor",
      lambda: (lambda s: None if len(s) == 1 else (_ for _ in ()).throw(AssertionError(s)))(heartbeat_once(False)))
check("inside Notewell it never tries to",
      lambda: (lambda s: None if s == [] else (_ for _ in ()).throw(AssertionError(s)))(heartbeat_once(True)))

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
