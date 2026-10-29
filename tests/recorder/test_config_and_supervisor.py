r"""Tests for the pieces a new user hits first: the config, the checksum-verified
model download, and the supervisor's safety rules.

    python tests/recorder/test_config_and_supervisor.py

No audio device, no network, no server needed.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Isolated data folder, set BEFORE importing the recorder (it reads this at import).
_HOME = tempfile.mkdtemp(prefix="mr-test-")
os.environ["MEETING_RECORDER_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))

import fetch_models
import recorder
import supervisor

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


GOOD = {"api_key": "mn_" + "ab" * 24, "api_url": "http://127.0.0.1:8766"}

# ---------------------------------------------------------------------------
print("\nWhere the data lives")


def t_home_override():
    assert recorder.APPDIR == Path(_HOME) and supervisor.APPDIR == Path(_HOME)


def t_never_under_appdata_by_default():
    code = ("import os;os.environ.pop('MEETING_RECORDER_HOME',None);"
            "import sys;sys.path.insert(0,r'%s');import recorder;print(recorder.APPDIR)"
            % (Path(recorder.__file__).parent))
    import subprocess
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "MEETING_RECORDER_HOME"}).stdout.strip()
    assert "AppData" not in out, f"default data folder is {out}"
    assert out.endswith("MeetingRecorder")


check("MEETING_RECORDER_HOME is honoured by recorder and supervisor", t_home_override)
check("by default data is NOT under AppData", t_never_under_appdata_by_default)

# ---------------------------------------------------------------------------
print("\nConfig validation (a bad config must be a loud, explained failure)")


def t_good_config():
    assert recorder.config_problems(GOOD) == []


def t_missing_or_placeholder_key():
    for key in ("", "   ", "PASTE_YOUR_KEY_HERE", "short"):
        probs = recorder.config_problems({**GOOD, "api_key": key})
        assert probs and "API key" in probs[0], key


def t_plain_http_to_another_machine_is_refused():
    probs = recorder.config_problems({**GOOD, "api_url": "http://203.0.113.9:8766"})
    assert probs and "unencrypted" in probs[0]


def t_loopback_http_is_fine():
    for u in ("http://127.0.0.1:8766", "http://localhost:8766", "http://[::1]:8766", "http://127.0.0.5/"):
        assert recorder.config_problems({**GOOD, "api_url": u}) == [], u


def t_https_is_fine():
    assert recorder.config_problems({**GOOD, "api_url": "https://notes.example.org"}) == []


def t_insecure_http_needs_an_explicit_opt_in():
    cfg = {**GOOD, "api_url": "http://192.168.1.50:8766"}
    assert recorder.config_problems(cfg)
    assert recorder.config_problems({**cfg, "allow_insecure_http": True}) == []


def t_bad_scheme():
    assert recorder.config_problems({**GOOD, "api_url": "ftp://x"})
    assert recorder.config_problems({**GOOD, "api_url": ""})


def t_a_trick_url_is_not_treated_as_local():
    # "127.0.0.1" appearing in the path / userinfo must not count as loopback
    assert recorder.config_problems({**GOOD, "api_url": "http://evil.example/127.0.0.1"})
    assert recorder.config_problems({**GOOD, "api_url": "http://127.0.0.1@evil.example/"})  # secret-scan: allow
    # a hostname that merely STARTS with 127. is an ordinary internet name
    assert recorder.config_problems({**GOOD, "api_url": "http://127.example.com:8766"})
    assert recorder.config_problems({**GOOD, "api_url": "http://127.0.0.1.evil.example/"})
    assert recorder.config_problems({**GOOD, "api_url": "http://localhost.evil.example/"})


check("a correct config has no problems", t_good_config)
check("missing / placeholder / short keys are caught", t_missing_or_placeholder_key)
check("plain http:// to another computer is refused", t_plain_http_to_another_machine_is_refused)
check("plain http:// on this computer is allowed", t_loopback_http_is_fine)
check("https:// is allowed", t_https_is_fine)
check("insecure http needs an explicit opt-in", t_insecure_http_needs_an_explicit_opt_in)
check("a bad or empty url is caught", t_bad_scheme)
check("tricks like http://127.0.0.1@evil.example are not 'local'", t_a_trick_url_is_not_treated_as_local)  # secret-scan: allow


# ---------------------------------------------------------------------------
print("\nReading config.json")


def t_bom_is_tolerated():
    """Windows PowerShell 5 writes a byte-order mark; that must not break the recorder."""
    recorder.CONFIG_PATH.write_bytes(b"\xef\xbb\xbf" + json.dumps(GOOD).encode())
    assert recorder.load_config()["api_key"] == GOOD["api_key"]


def t_new_settings_are_merged_in():
    recorder.CONFIG_PATH.write_text(json.dumps(GOOD), encoding="utf-8")
    cfg = recorder.load_config()
    assert cfg["spool_enabled"] is True and cfg["auto_detect"] is False


def t_broken_json_never_overwrites_the_file():
    recorder.CONFIG_PATH.write_text('{"api_key": "mn_precious", ', encoding="utf-8")
    try:
        recorder.load_config()
        raise AssertionError("did not stop on broken JSON")
    except SystemExit as e:
        assert "not valid JSON" in str(e)
    assert "mn_precious" in recorder.CONFIG_PATH.read_text(), "the user's key was erased"


def t_safe_defaults_for_a_public_tool():
    d = recorder.DEFAULT_CONFIG
    assert d["auto_detect"] is False, "must not record calls automatically unless asked"
    assert d["api_key"] == "", "no placeholder key that looks plausible"
    assert d["api_url"].startswith("http://127.0.0.1")


def t_keys_handed_over_in_memory_are_never_written():
    """The Notewell app passes its keys in recorder.KEYS; saving a setting must not
    put them in config.json. The standalone recorder (KEYS empty) keeps its own."""
    recorder.CONFIG_PATH.write_text(json.dumps({"whisper_model": "small"}), encoding="utf-8")
    fake = "mn" + "_in_memory_only_0123456789"     # made up; split so secret scanners skip it
    recorder.KEYS.update(api_key=fake)
    try:
        cfg = recorder.load_config()
        assert cfg["api_key"] == fake
        cfg["auto_detect"] = True
        recorder.write_config(cfg)
        assert "mn_in_memory_only" not in recorder.CONFIG_PATH.read_text(encoding="utf-8")
    finally:
        recorder.KEYS.clear()
    recorder.write_config({**GOOD})
    assert GOOD["api_key"] in recorder.CONFIG_PATH.read_text(encoding="utf-8")


check("a byte-order mark in config.json is tolerated", t_bom_is_tolerated)
check("settings added in newer versions are merged in", t_new_settings_are_merged_in)
check("a broken config.json is reported, never overwritten", t_broken_json_never_overwrites_the_file)
check("the shipped defaults are safe and generic", t_safe_defaults_for_a_public_tool)
check("keys given in memory never reach config.json", t_keys_handed_over_in_memory_are_never_written)

# ---------------------------------------------------------------------------
print("\nModel download is checksum-verified")


def t_tampered_download_is_rejected():
    # Use a private location, so the result does not depend on whether the real
    # model happens to be installed on the machine running the test.
    target = Path(tempfile.mkdtemp(prefix="mr-model-")) / "model.onnx"
    fetch_models.EXPECTED[target] = fetch_models.EXPECTED[fetch_models.EMBEDDING]
    try:
        try:
            fetch_models.save_verified(target, b"definitely not the real model")
            raise AssertionError("a wrong file was accepted")
        except SystemExit as e:
            assert "CHECKSUM MISMATCH" in str(e)
        assert not target.exists(), "a bad file was left on disk"
    finally:
        fetch_models.EXPECTED.pop(target, None)


def t_a_correct_download_is_saved():
    """The other half: rejecting everything would also pass the test above."""
    target = Path(tempfile.mkdtemp(prefix="mr-model-")) / "model.onnx"
    payload = b"pretend this is the model"
    fetch_models.EXPECTED[target] = hashlib.sha256(payload).hexdigest()
    try:
        fetch_models.save_verified(target, payload)
        assert target.read_bytes() == payload
    finally:
        fetch_models.EXPECTED.pop(target, None)


def t_pinned_hashes_look_real():
    for h in fetch_models.EXPECTED.values():
        assert len(h) == 64 and set(h) <= set("0123456789abcdef")


check("a tampered download is rejected and not saved", t_tampered_download_is_rejected)
check("a correct download is saved", t_a_correct_download_is_saved)
check("pinned checksums are well-formed", t_pinned_hashes_look_real)

# ---------------------------------------------------------------------------
print("\nSupervisor safety rules")


def _fresh_spool(age_seconds):
    root = supervisor.APPDIR / "spool"
    d = root / "sess"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "sys.pcm"
    f.write_bytes(b"\0" * 16)
    t = time.time() - age_seconds
    os.utime(f, (t, t))
    return d


def t_live_recording_is_detected():
    d = _fresh_spool(5)
    assert supervisor.recording_in_progress() is True
    assert supervisor.kill_stray_recorders() == [], "must refuse to stop anything mid-meeting"


def t_old_audio_is_not_a_live_recording():
    d = _fresh_spool(3600)
    assert supervisor.recording_in_progress() is False


def t_a_finished_spool_is_not_live():
    d = _fresh_spool(1)
    (d / "DONE").write_text("x")
    assert supervisor.recording_in_progress() is False


def t_stale_lock_does_not_block_start():
    supervisor.LOCK.write_text("999999")           # a PID that does not exist
    assert supervisor.already_running() is False


check("a recently-written spool means a meeting is live, and nothing is killed", t_live_recording_is_detected)
check("an hour-old spool is not a live recording", t_old_audio_is_not_a_live_recording)
check("a spool marked DONE is not live", t_a_finished_spool_is_not_live)
check("a stale lock file does not stop the supervisor starting", t_stale_lock_does_not_block_start)


def t_only_this_folders_recorder_counts_as_a_stray():
    """Another install, or a copy under test, must never be killed by this one."""
    mine = str(supervisor.RECORDER)
    other = str(Path(tempfile.gettempdir()) / "some-other-install" / "recorder.py")
    assert supervisor.belongs_to_this_install('"pythonw.exe" "%s"' % mine)
    assert supervisor.belongs_to_this_install("python " + mine.replace("\\", "/"))     # either slash style
    assert not supervisor.belongs_to_this_install('"pythonw.exe" "%s"' % other)
    assert not supervisor.belongs_to_this_install("python recorder.py")      # bare name: ambiguous, leave it alone
    assert not supervisor.belongs_to_this_install('python "%s"' % (supervisor.HERE / "supervisor.py"))


check("only a recorder from this folder counts as a stray", t_only_this_folders_recorder_counts_as_a_stray)

# ---------------------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
