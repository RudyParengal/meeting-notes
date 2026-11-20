"""The version is shown in Settings and the tray, and sent to the server (chunk 08, step 8).

Before 2.7.0 nothing on the laptop said which version was running, and the server
could not tell which Notewell was syncing: every request said only "Notewell".
"""
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "desktop"))
import linked_server  # noqa: E402


def test_the_settings_page_is_told_the_version(client, admin, app_module):
    me = client.get("/me", headers=admin).json()
    assert me["version"] == app_module.config.__version__


def test_the_settings_page_shows_it():
    js = (APP / "dashboard" / "app.js").read_text(encoding="utf-8")
    settings = js[js.index("function renderSettings()"):js.index("async function loadAccount()")]
    assert "'Notewell ' + currentUser.version" in settings


def test_every_sync_request_says_which_version(tmp_path, app_module):
    (tmp_path / "linked-server.json").write_text(json.dumps({"url": "https://far.test", "api_key": "not-a-real-key"}))
    lk = linked_server.Link(tmp_path, app_module.storage, "admin@example.test", version="9.8.7")
    assert lk.http.headers["X-Notewell-Version"] == "9.8.7"
    assert lk.http.headers["User-Agent"] == "Notewell/9.8.7"
    assert lk.http.headers["X-API-Key"] == "not-a-real-key"


def test_the_app_starts_the_sync_with_its_version(tmp_path, app_module, monkeypatch):
    (tmp_path / "linked-server.json").write_text(json.dumps({"url": "https://far.test", "api_key": "not-a-real-key"}))
    monkeypatch.setattr(linked_server.Link, "run", lambda self: None)
    lk = linked_server.start(tmp_path, app_module.storage, "admin@example.test", version="9.8.7")
    assert lk.http.headers["X-Notewell-Version"] == "9.8.7"
    src = (APP / "desktop" / "notewell.py").read_text(encoding="utf-8")
    assert "version = server.config.__version__" in src
    assert src.count("version=version") == 3, "start (twice) and sign-in must pass the version"
    assert "recorder.APP_VERSION = version" in src


def test_signing_in_says_which_version(tmp_path, monkeypatch):
    sent = {}

    class Answer:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"device_id": 1, "pass": "nwd_not-real"}

    def post(url, headers=None, **kw):
        sent.update(headers or {})
        return Answer()
    monkeypatch.setattr(linked_server.requests, "post", post)
    linked_server.sign_in(tmp_path, "https://far.test", "a@far.test", "pw-not-real", "Notewell on PC", version="9.8.7")
    assert sent == {"User-Agent": "Notewell/9.8.7", "X-Notewell-Version": "9.8.7"}


def test_without_a_version_nothing_changes(tmp_path, app_module):
    (tmp_path / "linked-server.json").write_text(json.dumps({"url": "https://far.test", "api_key": "not-a-real-key"}))
    lk = linked_server.Link(tmp_path, app_module.storage, "admin@example.test")
    assert lk.http.headers["User-Agent"] == "Notewell" and "X-Notewell-Version" not in lk.http.headers
