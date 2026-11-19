"""What the Notewell desktop app adds to the server: an AI chosen on a Settings
screen instead of in .env, notes a person can edit, context for the AI, and a
recorder the dashboard can drive."""
import base64
import json
import os

import pytest

from conftest import make_user

TRANSCRIPT = "[00:00] [Me] Let's plan the quarter.\n[00:05] [Speaker 1] I will send the proposal on Friday.\n" * 3
SAMPLE_PROVIDER_KEY = "sk-test-" + "z9" * 10 + "WXYZ"     # fake; only its last 4 may ever be shown
_AI_ENV = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "CLASSIFY_MODEL", "SUMMARY_MODEL", "ACTION_MODEL")


def post(client, headers):
    r = client.post("/transcribe", headers=headers, json={"transcript": TRANSCRIPT, "duration_sec": 600})
    assert r.status_code == 202, r.text
    return r.json()["session_id"]


@pytest.fixture()
def desktop(app_module, client):
    """The server as the desktop app runs it: no LLM_* settings, nothing chosen yet.
    Restores the test environment afterwards and rebuilds the stages from it."""
    saved_env = {k: os.environ.pop(k) for k in _AI_ENV if k in os.environ}
    store = app_module.settings_store
    store.SETTINGS_FILE.unlink(missing_ok=True)
    before = app_module.config.ALLOW_UNCONFIGURED_AI
    app_module.config.ALLOW_UNCONFIGURED_AI = True
    app_module.pipeline.init()
    yield app_module
    app_module.config.ALLOW_UNCONFIGURED_AI = before
    store.SETTINGS_FILE.unlink(missing_ok=True)
    os.environ.update(saved_env)
    app_module.pipeline.init()


def save(client, admin, **body):
    return client.put("/settings/ai", headers=admin, json={
        "provider": "mistral", "base_url": "https://api.mistral.ai/v1",
        "model": "ministral-14b-latest", "api_key": SAMPLE_PROVIDER_KEY, **body})


# ------------------------------------------------------------- AI settings ---
def test_a_server_configured_by_env_shows_its_ai_but_will_not_change_it(client, admin):
    got = client.get("/settings/ai", headers=admin).json()
    assert got["source"] == "env" and got["editable"] is False and got["ready"] is True
    assert {p["id"] for p in got["presets"]} >= {"mistral", "groq", "ollama", "openai", "anthropic", "xai"}
    assert save(client, admin).status_code == 409


def test_only_the_admin_sees_or_changes_the_ai(client, admin):
    alice = {"X-API-Key": make_user(client, admin)["api_key"]}
    assert client.get("/settings/ai", headers=alice).status_code == 403
    assert save(client, alice).status_code == 403
    assert client.post("/settings/ai/test", headers=alice, json={
        "base_url": "https://x.invalid/v1", "model": "m"}).status_code == 403


def test_with_no_ai_chosen_a_meeting_fails_clearly_and_keeps_its_transcript(desktop, client, admin):
    assert client.get("/settings/ai", headers=admin).json()["ready"] is False
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert got["status"] == "failed"
    assert got["error"] == desktop.pipeline.NOT_SET_UP
    assert got["transcript"] == TRANSCRIPT


def test_with_no_ai_chosen_an_imported_file_keeps_its_transcript(desktop, client, admin, monkeypatch):
    monkeypatch.setattr(desktop, "_have", lambda name: True)
    monkeypatch.setattr(desktop, "_transcribe_file", lambda path: (TRANSCRIPT, 600))
    r = client.post("/upload-audio", headers=admin, files={"file": ("call.wav", b"RIFF", "audio/wav")})
    assert r.status_code == 202, r.text
    got = client.get(f"/results/{r.json()['session_id']}", headers=admin).json()
    assert got["status"] == "failed"
    assert got["error"] == desktop.pipeline.NOT_SET_UP
    assert got["transcript"] == TRANSCRIPT and got["duration_sec"] == 600


def test_saving_a_provider_makes_it_ready_and_never_shows_the_key(desktop, client, admin):
    r = save(client, admin)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["ready"] is True and got["source"] == "app"
    assert got["model"] == "ministral-14b-latest" and got["key_hint"] == "...WXYZ"
    assert SAMPLE_PROVIDER_KEY not in r.text
    assert SAMPLE_PROVIDER_KEY not in client.get("/settings/ai", headers=admin).text
    assert all(s.model == "ministral-14b-latest" for s in desktop.pipeline.stages().values())


def test_a_blank_key_keeps_the_saved_one_only_for_the_same_provider(desktop, client, admin):
    save(client, admin)
    r = save(client, admin, model="mistral-small-latest", api_key="")
    assert r.status_code == 200 and r.json()["key_hint"] == "...WXYZ"
    # Switching company with a blank key must not send Mistral's key to OpenAI.
    r = save(client, admin, provider="openai", base_url="https://api.openai.com/v1", api_key="")
    assert r.status_code == 422


def test_a_local_model_needs_no_key(desktop, client, admin):
    r = save(client, admin, provider="ollama", base_url="http://localhost:11434/v1",
             model="llama3.1:8b", api_key="")
    assert r.status_code == 200 and r.json()["ready"] is True


def test_a_bad_address_is_refused(desktop, client, admin):
    assert save(client, admin, base_url="file:///etc/passwd").status_code == 422


def test_a_damaged_settings_file_reads_as_not_set_up(desktop):
    desktop.settings_store.SETTINGS_FILE.write_text("{not json", encoding="utf-8")
    assert desktop.settings_store.ai() == {}


def test_on_windows_the_saved_key_is_unreadable_in_the_file(desktop, client, admin):
    save(client, admin)
    on_disk = desktop.settings_store.SETTINGS_FILE.read_text(encoding="utf-8")
    if os.name == "nt":
        assert SAMPLE_PROVIDER_KEY not in on_disk and '"dpapi:' in on_disk
    assert desktop.settings_store.ai()["api_key"] == SAMPLE_PROVIDER_KEY


def test_a_key_saved_by_an_older_version_still_works(desktop):
    desktop.settings_store.SETTINGS_FILE.write_text(json.dumps({"ai": {
        "provider": "mistral", "base_url": "https://api.mistral.ai/v1",
        "model": "ministral-14b-latest", "api_key": SAMPLE_PROVIDER_KEY}}), encoding="utf-8")
    assert desktop.settings_store.ai()["api_key"] == SAMPLE_PROVIDER_KEY


def test_a_key_locked_by_another_user_reads_as_no_key(desktop):
    desktop.settings_store.SETTINGS_FILE.write_text(json.dumps({"ai": {
        "provider": "mistral", "base_url": "https://api.mistral.ai/v1",
        "model": "ministral-14b-latest", "api_key": "dpapi:" + base64.b64encode(b"sealed on some other PC" * 4).decode()}}),
        encoding="utf-8")
    assert desktop.settings_store.ai()["api_key"] == ""


def test_the_test_button_reports_success_and_failure(desktop, client, admin, monkeypatch):
    seen = {}

    async def works(base_url, model, key):
        seen.update(base_url=base_url, model=model, key=key)
        return "ready"

    async def broken(base_url, model, key):
        raise RuntimeError("401 invalid api key")

    body = {"base_url": "https://api.mistral.ai/v1", "model": "ministral-14b-latest", "api_key": "k" * 20}
    monkeypatch.setattr(desktop.pipeline, "test_connection", works)
    got = client.post("/settings/ai/test", headers=admin, json=body).json()
    assert got["ok"] is True and got["reply"] == "ready"
    assert seen["key"] == "k" * 20                   # the unsaved key on screen is what gets tried

    monkeypatch.setattr(desktop.pipeline, "test_connection", broken)
    got = client.post("/settings/ai/test", headers=admin, json=body).json()
    assert got["ok"] is False and "401" in got["error"]


# -------------------------------------------------- editing and context ---
def test_edited_notes_are_saved_and_exported(client, admin, llm):
    sid = post(client, admin)
    r = client.put(f"/results/{sid}/notes", headers=admin,
                   json={"summary": "## Meeting Title\nRenamed by hand\n\nMy own words."})
    assert r.status_code == 200, r.text
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert "My own words." in got["summary"]
    assert "Send the proposal" in got["actions"]       # the part not sent is untouched
    assert got["edited_at"]
    assert "My own words." in client.get(f"/results/{sid}/markdown", headers=admin).text


def test_notes_cannot_be_edited_while_the_ai_is_writing_them(client, admin, app_module):
    sid = "cd" * 16
    app_module.storage.save_session(sid, {"session_id": sid, "timestamp": "2026-10-29T10:00:00+00:00",
                                          "duration_sec": 60, "transcript": TRANSCRIPT, "status": "processing",
                                          "summary": "", "actions": "", "user_email": "admin@example.test"})
    assert client.put(f"/results/{sid}/notes", headers=admin, json={"summary": "x"}).status_code == 409


def test_someone_elses_notes_cannot_be_edited(client, admin, llm):
    sid = post(client, admin)
    alice = {"X-API-Key": make_user(client, admin)["api_key"]}
    assert client.put(f"/results/{sid}/notes", headers=alice, json={"summary": "x"}).status_code == 403


def test_context_reaches_both_prompts_and_is_remembered(client, admin, llm):
    sid = post(client, admin)
    llm.calls.clear()
    r = client.post(f"/results/{sid}/reprocess?meeting_type=sales", headers=admin,
                    json={"context": "The client is spelled Acme-Quorn."})
    assert r.status_code == 202, r.text
    prompts = [c["messages"][0]["content"] for c in llm.calls]
    assert sum("Acme-Quorn" in p for p in prompts) == 2           # summary and action plan
    assert client.get(f"/results/{sid}", headers=admin).json()["context"] == "The client is spelled Acme-Quorn."
    llm.calls.clear()
    client.post(f"/results/{sid}/reprocess?meeting_type=sales", headers=admin)   # no body: keeps it
    assert any("Acme-Quorn" in c["messages"][0]["content"] for c in llm.calls)


# ----------------------------------------------------------- recorder ---
class FakeManager:
    in_meeting = False
    started_at = None
    model_ready = True


def test_a_server_has_no_built_in_recorder(client, admin):
    assert client.get("/recorder", headers=admin).json() == {"available": False}
    assert client.post("/recorder/start", headers=admin).status_code == 501


def test_the_dashboard_can_drive_an_attached_recorder(client, admin, app_module, monkeypatch):
    bridge = app_module.recorder_bridge
    mgr, cfg, saved = FakeManager(), {"audio_source": "", "language": "", "api_key": "secret-stays-here"}, []

    def start():
        mgr.in_meeting, mgr.started_at = True, 1.0

    monkeypatch.setattr(bridge, "_controller", None)
    bridge.attach(manager=mgr, cfg=cfg, start=start, stop=lambda: setattr(mgr, "in_meeting", False),
                  devices=lambda: [{"name": "Headset"}], save_cfg=lambda: saved.append(1))
    assert client.post("/recorder/start", headers=admin).json()["recording"] is True
    assert client.get("/recorder/devices", headers=admin).json()["devices"] == [{"name": "Headset"}]
    got = client.put("/recorder/settings", headers=admin,
                     json={"language": "de", "auto_detect": True}).json()["settings"]
    assert got["language"] == "de" and got["auto_detect"] is True and saved
    assert "api_key" not in client.get("/recorder", headers=admin).text
    assert client.post("/recorder/stop", headers=admin).json()["recording"] is False
    alice = {"X-API-Key": make_user(client, admin)["api_key"]}
    assert client.post("/recorder/start", headers=alice).status_code == 403


# ---------------------------------------------------------- dictation ---
class FakeDictation:
    """Stands in for windows-recorder/dictation.py; the word list and history are real."""
    def __init__(self, store):
        self.store, self.cfg = store, {"hotkey": "ctrl+win", "tidy": False}

    def status(self):
        return {"enabled": True, "hotkey": self.cfg["hotkey"], "tidy": self.cfg["tidy"]}

    def update(self, changes):
        if "hotkey" in changes and "+" not in changes["hotkey"]:
            raise ValueError("Choose at least two keys, for example Ctrl+Win")
        self.cfg.update(changes)
        return self.status()


def test_a_server_has_no_dictation(client, admin):
    assert client.get("/dictation", headers=admin).status_code == 501


def test_the_dashboard_manages_dictation(client, admin, app_module, monkeypatch, tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
    from dictation_store import Store
    store = Store(tmp_path / "dictation.db")
    monkeypatch.setattr(app_module.recorder_bridge, "_dictation", None)
    app_module.recorder_bridge.attach_dictation(FakeDictation(store))

    got = client.put("/dictation/settings", headers=admin, json={"hotkey": "alt+shift+d", "tidy": True}).json()
    assert got["hotkey"] == "alt+shift+d" and got["tidy"] is True and got["words"] == []
    bad = client.put("/dictation/settings", headers=admin, json={"hotkey": "d"})
    assert bad.status_code == 422 and "two keys" in bad.json()["detail"]

    w = client.post("/dictation/words", headers=admin, json={"term": "Notewell", "mishearing": "note well"}).json()
    assert client.post("/dictation/words", headers=admin, json={"term": "notewell"}).status_code == 422
    changed = client.put(f"/dictation/words/{w['id']}", headers=admin, json={"mishearing": "no tell"}).json()
    assert changed["term"] == "Notewell" and changed["mishearing"] == "no tell"
    assert client.get("/dictation", headers=admin).json()["words"][0]["term"] == "Notewell"
    assert client.delete(f"/dictation/words/{w['id']}", headers=admin).json() == {"ok": True}
    assert client.delete(f"/dictation/words/{w['id']}", headers=admin).status_code == 404
    assert client.put("/dictation/words/999", headers=admin, json={"term": "x"}).status_code == 404

    store.add_dictation("first", "first", app="notepad.exe", seconds=1.5)
    store.add_dictation("second", "second", outcome="held")
    items = client.get("/dictation/history?limit=1", headers=admin).json()["items"]
    assert [i["text"] for i in items] == ["second"] and items[0]["outcome"] == "held"
    older = client.get(f"/dictation/history?before={items[0]['created_at']}", headers=admin).json()["items"]
    assert "second" not in [i["text"] for i in older]
    assert client.delete("/dictation/history", headers=admin).json() == {"cleared": 2}

    alice = {"X-API-Key": make_user(client, admin)["api_key"]}       # dictation is the owner's alone
    assert client.get("/dictation", headers=alice).status_code == 403
    assert client.get("/dictation/history", headers=alice).status_code == 403


def test_a_test_copy_with_its_own_folder_runs_beside_the_installed_one(monkeypatch, tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop"))
    import notewell
    monkeypatch.delenv("NOTEWELL_HOME", raising=False)
    assert notewell.instance_name("Local\\Notewell") == "Local\\Notewell"     # the installed app: unchanged
    monkeypatch.setenv("NOTEWELL_HOME", str(tmp_path))
    monkeypatch.setattr(notewell, "HOME", tmp_path)
    one = notewell.instance_name("Local\\Notewell")
    monkeypatch.setattr(notewell, "HOME", tmp_path / "other")
    assert one.startswith("Local\\Notewell.") and notewell.instance_name("Local\\Notewell") != one


# ------------------------------------------------ your account (desktop only) ---
def test_a_server_has_no_account_screen(client, admin, app_module, monkeypatch):
    monkeypatch.setattr(app_module.recorder_bridge, "_account", None)
    assert client.get("/account", headers=admin).status_code == 501


def test_the_dashboard_signs_this_pc_in(client, admin, app_module, monkeypatch):
    state = {"linked": False}

    def sign_in(url, email, password):
        if password != "right-not-real":
            raise ValueError("Wrong email or password")
        state.update({"linked": True, "url": url, "pass": "device pass", "signed_in_as": {"email": email}})
        return state

    monkeypatch.setattr(app_module.recorder_bridge, "_account", None)
    app_module.recorder_bridge.attach_account(status=lambda: state, sign_in=sign_in)
    assert client.get("/account", headers=admin).json() == {"linked": False}
    form = {"url": "https://far.test", "email": "rudy@far.test"}
    bad = client.post("/account/signin", headers=admin, json={**form, "password": "wrong"})
    assert bad.status_code == 422 and "Wrong email" in bad.json()["detail"]
    ok = client.post("/account/signin", headers=admin, json={**form, "password": "right-not-real"})
    assert ok.status_code == 200 and ok.json()["pass"] == "device pass"
    assert "right-not-real" not in ok.text
    alice = {"X-API-Key": make_user(client, admin)["api_key"]}
    assert client.get("/account", headers=alice).status_code == 403
    assert client.post("/account/signin", headers=alice, json={**form, "password": "x"}).status_code == 403


# ---------------------------- screen recordings on this PC's dashboard (18 Nov) ---
def _with_recordings(app_module, monkeypatch, linked=True, fail=None):
    changed = []

    def recordings():
        if not linked:
            raise app_module.recorder_bridge.NotSignedIn()
        if fail:
            raise fail
        return [{"id": "abc123", "title": "Made-up", "share_on": True}]

    def change(rid, changes):
        if rid == "gone00":
            raise KeyError(rid)
        changed.append((rid, changes))
        return {"id": rid, **changes}

    monkeypatch.setattr(app_module.recorder_bridge, "_account", None)
    app_module.recorder_bridge.attach_account(status=lambda: pytest.fail("no need to ask the server who we are"), sign_in=lambda *a: {},
                                              recordings=recordings, change_recording=change)
    return changed


def test_the_dashboard_lists_screen_recordings(client, admin, app_module, monkeypatch):
    _with_recordings(app_module, monkeypatch)
    r = client.get("/account/recordings", headers=admin)
    assert r.status_code == 200 and r.json() == {"recordings": [{"id": "abc123", "title": "Made-up", "share_on": True}]}


def test_the_dashboard_switches_a_link_off_and_renames(client, admin, app_module, monkeypatch):
    changed = _with_recordings(app_module, monkeypatch)
    r = client.patch("/account/recordings/abc123", headers=admin, json={"share_on": False, "title": "New name"})
    assert r.status_code == 200 and r.json()["share_on"] is False
    assert changed == [("abc123", {"share_on": False, "title": "New name"})]
    assert client.patch("/account/recordings/gone00", headers=admin, json={"share_on": True}).status_code == 404


def test_recordings_need_this_pc_signed_in_and_say_so(client, admin, app_module, monkeypatch):
    _with_recordings(app_module, monkeypatch, linked=False)
    r = client.get("/account/recordings", headers=admin)
    assert r.status_code == 409 and "Sign in" in r.json()["detail"]


def test_an_unreachable_server_is_a_plain_message(client, admin, app_module, monkeypatch):
    _with_recordings(app_module, monkeypatch, fail=OSError("cut"))
    r = client.get("/account/recordings", headers=admin)
    assert r.status_code == 502 and "could not be reached" in r.json()["detail"]


def test_only_the_admin_sees_recordings(client, admin, app_module, monkeypatch):
    _with_recordings(app_module, monkeypatch)
    alice = {"X-API-Key": make_user(client, admin)["api_key"]}
    assert client.get("/account/recordings", headers=alice).status_code == 403
    assert client.patch("/account/recordings/abc123", headers=alice, json={"share_on": False}).status_code == 403
