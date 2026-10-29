"""What the Notewell desktop app adds to the server: an AI chosen on a Settings
screen instead of in .env, notes a person can edit, context for the AI, and a
recorder the dashboard can drive."""
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
