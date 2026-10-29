"""The behaviour of the server: a transcript goes in, a note comes out - and when
a provider fails, the note says so instead of the whole session hanging."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

from conftest import BACKEND, make_user

TRANSCRIPT = "[00:00] [Me] Let's plan the quarter.\n[00:05] [Speaker 1] I will send the proposal on Friday.\n" * 3


def post(client, headers, **extra):
    r = client.post("/transcribe", headers=headers,
                    json={"transcript": TRANSCRIPT, "duration_sec": 600, **extra})
    assert r.status_code == 202, r.text
    return r.json()["session_id"]


# ------------------------------------------------------------ the happy path ---
def test_a_transcript_becomes_a_note(client, admin, llm):
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert got["status"] == "complete"
    assert got["meeting_type"] == "sales"                       # classified
    assert "Quarterly Planning" in got["summary"]
    assert "Send the proposal" in got["actions"]
    assert got["transcript"] == TRANSCRIPT                      # kept verbatim
    assert got["timing"]["total_sec"] >= 0


def test_the_note_appears_in_the_list_with_its_title(client, admin, llm):
    sid = post(client, admin)
    rows = client.get("/sessions", headers=admin).json()["sessions"]
    assert [r["session_id"] for r in rows] == [sid]
    assert rows[0]["title"] == "Quarterly Planning"


def test_an_explicit_meeting_type_skips_classification(client, admin, llm):
    post(client, admin, meeting_type="interview")
    assert "m-classify" not in [c["model"] for c in llm.calls]


def test_markdown_download_contains_everything(client, admin, llm):
    sid = post(client, admin)
    md = client.get(f"/results/{sid}/markdown", headers=admin).text
    assert "Quarterly Planning" in md and "Send the proposal" in md and "Full Transcript" in md


def test_the_recorders_session_id_is_honoured(client, admin, llm):
    """The recorder generates the id so it can poll for it."""
    sid = "ab" * 16
    assert post(client, admin, session_id=sid) == sid


# ---------------------------------------------------- when the provider breaks ---
def test_an_action_stage_outage_degrades_the_note_not_the_session(client, admin, llm):
    """A spent quota on one provider must cost the action items, never the meeting."""
    llm.fail.add("m-action")
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert got["status"] == "complete"                          # not stuck on 'processing'
    assert "Quarterly Planning" in got["summary"]               # summary unaffected
    assert "unavailable" in got["actions"].lower()
    assert "insufficient_quota" in got["actions"]               # and it says WHY, in the note


def test_a_summary_outage_is_reported_in_the_note(client, admin, llm):
    llm.fail.add("m-summary")
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert got["status"] == "complete"
    assert "failed" in got["summary"].lower()
    assert "Send the proposal" in got["actions"]


def test_a_classify_outage_falls_back_to_generic(client, admin, llm):
    llm.fail.add("m-classify")
    sid = post(client, admin)
    assert client.get(f"/results/{sid}", headers=admin).json()["meeting_type"] == "generic"


def test_a_nonsense_category_falls_back_to_generic(client, admin, llm):
    llm.classify_as = "banana"
    sid = post(client, admin)
    assert client.get(f"/results/{sid}", headers=admin).json()["meeting_type"] == "generic"


def test_a_failed_note_can_be_filled_in_later(client, admin, llm):
    """The recovery path used after a provider outage."""
    llm.fail.add("m-action")
    sid = post(client, admin)
    llm.fail.clear()
    assert client.post(f"/results/{sid}/reprocess?meeting_type=sales", headers=admin).status_code == 202
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert "Send the proposal" in got["actions"]


# ------------------------------------------------------- provider settings ---
def _run(code: str, **env):
    full = {**os.environ, **env}
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=full,
                          capture_output=True, text=True, timeout=60)


def test_each_stage_can_use_its_own_provider_and_model():
    out = _run("""
import pipeline, json
st = pipeline.init()
print(json.dumps({k: [v.model, v.base_url] for k, v in st.items()}))
""", LLM_BASE_URL="https://main.invalid/v1", LLM_MODEL="base-model",
        ACTION_BASE_URL="https://other.invalid/v1", ACTION_MODEL="big-model", ACTION_API_KEY="k2",
        SUMMARY_MODEL="", CLASSIFY_MODEL="", MEETING_NOTES_API_KEY="mn_" + "b2" * 24)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["summary"] == ["base-model", "https://main.invalid/v1"]
    assert got["classify"] == ["base-model", "https://main.invalid/v1"]
    assert got["action"] == ["big-model", "https://other.invalid/v1"]


def test_there_is_no_vendor_routing_by_model_name():
    """The old code picked a provider by looking at the model's name. Never again."""
    src = (BACKEND / "pipeline.py").read_text()
    for banned in ("_pick_action_client", "startswith(ALIBABA", '"grok" in', "ALIBABA_HOSTED"):
        assert banned not in src


# ------------------------------------------------------------------ storage ---
def test_trash_restore_and_purge(client, admin, llm):
    sid = post(client, admin)
    assert client.delete(f"/results/{sid}", headers=admin).status_code == 200
    assert client.get("/sessions", headers=admin).json()["sessions"] == []
    assert [r["session_id"] for r in client.get("/trash", headers=admin).json()["sessions"]] == [sid]
    assert client.post(f"/results/{sid}/restore", headers=admin).status_code == 200
    assert len(client.get("/sessions", headers=admin).json()["sessions"]) == 1
    # purge only works from the trash
    assert client.delete(f"/results/{sid}/purge", headers=admin).status_code == 404
    client.delete(f"/results/{sid}", headers=admin)
    assert client.delete(f"/results/{sid}/purge", headers=admin).status_code == 200
    assert client.get(f"/results/{sid}", headers=admin).status_code == 404


def test_search_and_tags(client, admin, llm):
    sid = post(client, admin)
    assert client.put(f"/results/{sid}/tags", headers=admin, json={"tags": ["Important", " follow-up "]}).json()["tags"] == ["important", "follow-up"]
    assert len(client.get("/sessions?tag=important", headers=admin).json()["sessions"]) == 1
    assert client.get("/sessions?tag=nope", headers=admin).json()["sessions"] == []
    assert len(client.get("/sessions?search=proposal", headers=admin).json()["sessions"]) == 1
    assert client.get("/sessions?search=zzzzzz", headers=admin).json()["sessions"] == []


def test_a_crash_mid_write_cannot_corrupt_a_saved_note(app_module, tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "NOTES_DIR", tmp_path)
    sid = "cd" * 16
    base = {"session_id": sid, "timestamp": "2026-01-01T10:00:00+00:00", "summary": "## Meeting Title\nGood",
            "actions": "a", "transcript": "t", "status": "complete"}
    storage.save_session(sid, base)
    before = [p.read_text() for p in sorted(tmp_path.glob("*.json"))]

    real_replace = os.replace
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        storage.save_session(sid, {**base, "summary": "## Meeting Title\nGood", "actions": "NEW"})
    monkeypatch.setattr(os, "replace", real_replace)

    assert [p.read_text() for p in sorted(tmp_path.glob("*.json"))] == before, "a failed save damaged the old note"
    assert not list(tmp_path.glob(".tmp-*")), "temp files were left behind"


def test_two_ids_sharing_a_prefix_are_not_confused(app_module, tmp_path, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "NOTES_DIR", tmp_path)
    a, b = "ee" * 4 + "1" * 24, "ee" * 4 + "2" * 24          # same first 8 characters
    for sid, text in ((a, "AAA"), (b, "BBB")):
        storage.save_session(sid, {"session_id": sid, "timestamp": "2026-01-01T10:00:00+00:00",
                                   "summary": "## Meeting Title\nT", "actions": "", "transcript": text, "status": "complete"})
    assert storage.load_session(a)["transcript"] == "AAA"
    assert storage.load_session(b)["transcript"] == "BBB"


def test_the_health_check_reports_the_real_configuration(client, admin):
    got = client.get("/health/deep", headers=admin).json()
    assert got["status"] == "ok" and got["storage_writable"] is True
    assert set(got["llm"]) == {"classify", "summary", "action"}
    assert got["version"]


def test_notes_saved_by_older_versions_are_still_found_and_then_renamed(app_module, tmp_path, monkeypatch):
    """Upgrading must never hide anyone's existing meetings."""
    import storage
    monkeypatch.setattr(storage, "NOTES_DIR", tmp_path)
    sid = "f3" * 16
    record = {"session_id": sid, "timestamp": "2026-04-15T10:00:00+00:00",
              "summary": "## Meeting Title\nOld Note", "actions": "a", "transcript": "t", "status": "complete"}
    # exactly how versions before 2.0 named files: first 8 characters only
    (tmp_path / f"2026-04-15_10-00_Old-Note_{sid[:8]}.json").write_text(json.dumps(record))
    (tmp_path / f"2026-04-15_10-00_Old-Note_{sid[:8]}.md").write_text("# old")

    assert storage.load_session(sid)["summary"].endswith("Old Note")
    assert storage.get_markdown(sid) == "# old"
    assert [r["session_id"] for r in storage.list_sessions()] == [sid]

    storage.save_session(sid, {**record, "actions": "updated"})           # any later save
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [f"2026-04-15_10-00_Old-Note_{sid}.json", f"2026-04-15_10-00_Old-Note_{sid}.md"], names
    assert storage.load_session(sid)["actions"] == "updated"


# ------------------------------------------------------------------ retries ---
class _HttpError(Exception):
    """Looks like the SDK's error: has .status_code, and a message."""
    def __init__(self, status, msg=""):
        super().__init__(f"Error code: {status} - {msg}")
        self.status_code = status


class Flaky(object):
    """A provider that fails `n` times in a given way, then works."""
    def __init__(self, llm, model, n, status, msg=""):
        self.n, self.calls, self.status, self.msg = n, 0, status, msg
        real = llm.create
        async def create(**kw):
            if kw["model"] == model:
                self.calls += 1
                if self.calls <= self.n:
                    raise _HttpError(self.status, self.msg)
            return await real(**kw)
        llm.create = create
        llm.chat.completions = llm


def test_a_temporary_overload_is_retried_and_the_note_comes_out_whole(client, admin, llm):
    """Real Gemini answered '503 high demand' to two of three calls in testing."""
    flaky = Flaky(llm, "m-summary", n=2, status=503, msg="This model is currently experiencing high demand")
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert "Quarterly Planning" in got["summary"] and "failed" not in got["summary"].lower()
    assert flaky.calls == 3, f"expected 2 failures + 1 success, saw {flaky.calls} calls"


def test_a_rate_limit_is_retried(client, admin, llm):
    flaky = Flaky(llm, "m-action", n=1, status=429, msg="Too Many Requests")
    sid = post(client, admin)
    assert "Send the proposal" in client.get(f"/results/{sid}", headers=admin).json()["actions"]
    assert flaky.calls == 2


def test_a_spent_quota_is_never_retried(client, admin, llm):
    """Waiting cannot fix a used-up plan; retrying only delays the explanation."""
    flaky = Flaky(llm, "m-action", n=99, status=429, msg="insufficient_quota: your plan is used up")
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert flaky.calls == 1, f"retried a spent quota {flaky.calls} times"
    assert "insufficient_quota" in got["actions"] and got["status"] == "complete"


def test_a_wrong_key_or_model_is_never_retried(client, admin, llm):
    for status in (401, 403, 404):
        flaky = Flaky(llm, "m-action", n=99, status=status, msg="nope")
        post(client, admin)
        assert flaky.calls == 1, f"HTTP {status} was retried"


def test_retries_give_up_and_degrade_instead_of_hanging(client, admin, llm):
    flaky = Flaky(llm, "m-action", n=99, status=503, msg="still overloaded")
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert flaky.calls == 4                                   # the first try + 3 retries, then stop
    assert got["status"] == "complete" and "unavailable" in got["actions"].lower()
    assert "Quarterly Planning" in got["summary"]


# ------------------------------------------------- thinking models (real bug) ---
def test_classification_leaves_room_for_a_thinking_model(client, admin, llm):
    """Real Gemini 2.5 answered nothing at max_tokens=16 and 64: its hidden reasoning used
    the whole budget, so every meeting was filed as 'generic'. The fake AI never thinks,
    so only a guard on the request itself can catch this."""
    post(client, admin)
    asked = [c["max_tokens"] for c in llm.calls if c["model"] == "m-classify"]
    assert asked and min(asked) >= 512, f"classification asked for only {asked} tokens"


def test_summary_and_actions_get_a_generous_budget(client, admin, llm):
    post(client, admin)
    for model in ("m-summary", "m-action"):
        assert min(c["max_tokens"] for c in llm.calls if c["model"] == model) >= 4000


def test_a_chatty_category_answer_is_still_understood(client, admin, llm):
    for said, want in (("Category: sales.", "sales"), ("The meeting is a technical review.", "technical"),
                       ("  INTERVIEW\n", "interview"), ("project", "project")):
        llm.classify_as = said
        sid = post(client, admin)
        assert client.get(f"/results/{sid}", headers=admin).json()["meeting_type"] == want, said


def test_a_model_that_returns_no_text_is_reported_not_saved_as_blank(client, admin, llm):
    llm.empty.add("m-summary")
    sid = post(client, admin)
    got = client.get(f"/results/{sid}", headers=admin).json()
    assert got["status"] == "complete"
    assert "LLM_MAX_TOKENS" in got["summary"] and "no text" in got["summary"]
    assert got["summary"].strip() not in ("", "_Processing..._")
