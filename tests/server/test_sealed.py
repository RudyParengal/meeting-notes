"""Secrets the desktop app keeps on disk: locked to the Windows user, never in
the recorder's config.json, and an older version's plain key still works."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop"))
import notewell  # noqa: E402
import sealed  # noqa: E402

SECRET = "mn_" + "a1" * 24
windows_only = pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows only")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(notewell, "HOME", tmp_path)
    monkeypatch.setattr(notewell, "KEY_FILE", tmp_path / "app.key")
    return tmp_path


@windows_only
def test_a_sealed_value_comes_back_and_does_not_show_in_the_file():
    locked = sealed.seal(SECRET)
    assert locked.startswith("dpapi:") and SECRET not in locked
    assert sealed.unseal(locked) == SECRET
    assert sealed.seal(locked) == locked                     # never locked twice


def test_plain_text_from_an_older_version_reads_as_it_is():
    assert sealed.unseal(SECRET) == SECRET
    assert sealed.unseal("") == ""


def test_the_master_key_is_kept_locked_and_an_old_plain_one_is_locked_on_first_read(home):
    (home / "app.key").write_text(SECRET, encoding="utf-8")
    assert notewell.master_key() == SECRET
    on_disk = (home / "app.key").read_text(encoding="utf-8")
    assert (SECRET not in on_disk) == sealed.available()
    assert notewell.master_key() == SECRET                   # and still reads after that


def test_a_new_master_key_is_written_locked(home):
    key = notewell.master_key()
    assert len(key) >= 24
    assert (key not in (home / "app.key").read_text(encoding="utf-8")) == sealed.available()


def test_the_recorder_settings_file_never_holds_a_key(home):
    (home / "recorder").mkdir()
    old = {"whisper_model": "small", "api_key": SECRET, "fallback_api_key": SECRET}
    (home / "recorder" / "config.json").write_text(json.dumps(old), encoding="utf-8")
    keys = notewell.prepare_recorder_config(8767, SECRET)
    written = (home / "recorder" / "config.json").read_text(encoding="utf-8")
    assert SECRET not in written and "api_key" not in written
    assert json.loads(written)["api_url"] == "http://127.0.0.1:8767"
    assert keys == {"api_key": SECRET}


def test_a_linked_server_key_is_locked_and_handed_to_the_recorder_in_memory(home):
    far_key = "far-" + "b2" * 20
    (home / "linked-server.json").write_text(json.dumps(
        {"url": "https://far.test", "api_key": far_key}), encoding="utf-8")
    keys = notewell.prepare_recorder_config(8767, SECRET)
    assert keys == {"api_key": far_key, "fallback_api_key": SECRET}
    cfg = json.loads((home / "recorder" / "config.json").read_text(encoding="utf-8"))
    assert cfg["api_url"] == "https://far.test" and cfg["fallback_api_url"] == "http://127.0.0.1:8767"
    assert (far_key not in (home / "linked-server.json").read_text(encoding="utf-8")) == sealed.available()
