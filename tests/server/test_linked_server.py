"""desktop/linked_server.py: a Notewell kept in step with a Meeting Notes server.

The far server is a fake held in memory, so each case controls exactly what
changed where. The near side is the real storage of the test server.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop"))
import linked_server  # noqa: E402

OWNER = "admin@example.test"
TRANSCRIPT = "[00:00] [Me] Let's plan the quarter and send the proposal on Friday."


def note(sid, title="Planning", tags=(), status="complete"):
    return {"session_id": sid, "timestamp": "2026-10-20T10:00:00+00:00", "duration_sec": 600,
            "meeting_type": "sales", "status": status, "tags": list(tags),
            "summary": f"## Meeting Title\n{title}\n\n## Summary\nThey planned.",
            "actions": "- [ ] Send it", "transcript": TRANSCRIPT, "user_email": "rudy@far.test"}


class Far:
    """The far server's notes routes, as linked_server uses them."""

    def __init__(self):
        self.notes, self.trashed, self.calls = {}, set(), []

    def row(self, d):
        return {"session_id": d["session_id"], "status": d["status"], "tags": d["tags"],
                "title": d["summary"].split("\n")[1], "timestamp": d["timestamp"]}

    def get(self, path, **params):
        if path == "/sessions":
            return {"sessions": [self.row(d) for s, d in self.notes.items() if s not in self.trashed]}
        if path == "/trash":
            return {"sessions": [self.row(d) for s, d in self.notes.items() if s in self.trashed]}
        sid = path.rsplit("/", 1)[1]
        return dict(self.notes[sid])

    def send(self, method, path, json=None):
        self.calls.append((method, path, json))
        if path == "/transcribe":
            self.notes[json["session_id"]] = {**note(json["session_id"]), "status": "processing"}
            return
        sid = path.split("/")[2]
        if method == "PUT":
            self.notes[sid]["tags"] = json["tags"]
        elif method == "DELETE":
            self.trashed.add(sid)
        else:
            self.trashed.discard(sid)


@pytest.fixture()
def link(client, app_module, tmp_path):
    (tmp_path / "recorder").mkdir()
    (tmp_path / "linked-server.json").write_text(
        json.dumps({"url": "https://far.test", "api_key": "not-a-real-key"}))
    lk = linked_server.Link(tmp_path, app_module.storage, OWNER)
    far = Far()
    lk._get, lk._send = far.get, far.send
    lk.far = far
    return lk


def here(storage, sid):
    if storage.load_session(sid):
        return storage.load_session(sid), False
    return storage.load_trashed_session(sid), True


A, B = "a" * 32, "b" * 32


def test_new_notes_are_copied_here_owned_by_this_app_and_trash_stays_trash(link):
    link.far.notes = {A: note(A, tags=["angie"]), B: note(B)}
    link.far.trashed = {B}
    assert link.sync()["copied"] == 2
    a, a_trashed = here(link.storage, A)
    assert not a_trashed and a["user_email"] == OWNER and a["imported_owner"] == "rudy@far.test"
    assert a["tags"] == ["angie"] and a["transcript"] == TRANSCRIPT
    assert here(link.storage, B)[1] is True


def test_a_second_pass_with_nothing_changed_does_nothing(link):
    link.far.notes = {A: note(A)}
    link.sync()
    assert link.sync() == {"copied": 0, "updated": 0, "sent": 0, "failed": 0}
    assert link.far.calls == []


def test_changes_on_the_server_arrive_here(link):
    link.far.notes = {A: note(A)}
    link.sync()
    link.far.notes[A]["tags"] = ["client"]
    link.far.trashed.add(A)
    link.sync()
    data, trashed = here(link.storage, A)
    assert trashed and data["tags"] == ["client"]
    link.far.trashed.clear()
    link.far.notes[A]["summary"] = "## Meeting Title\nRenamed\n\n## Summary\nNew."
    link.sync()
    data, trashed = here(link.storage, A)
    assert not trashed and "Renamed" in data["summary"]


def test_changes_made_here_go_to_the_server(link):
    link.far.notes = {A: note(A)}
    link.sync()
    data = link.storage.load_session(A)
    data["tags"] = ["internal"]
    link.storage.save_session(A, data)
    link.storage.delete_session(A, OWNER, True)
    assert link.sync()["sent"] == 1
    assert ("PUT", f"/results/{A}/tags", {"tags": ["internal"]}) in link.far.calls
    assert A in link.far.trashed and link.far.notes[A]["tags"] == ["internal"]
    link.storage.restore_session(A, OWNER, True)
    link.sync()
    assert A not in link.far.trashed
    assert link.sync()["sent"] == 0                # agreed now: nothing more to send


def test_when_both_sides_changed_the_server_wins(link):
    link.far.notes = {A: note(A)}
    link.sync()
    data = link.storage.load_session(A)
    data["tags"] = ["mine"]
    link.storage.save_session(A, data)
    link.far.notes[A]["tags"] = ["theirs"]
    link.sync()
    assert link.storage.load_session(A)["tags"] == ["theirs"]
    assert not [c for c in link.far.calls if c[0] == "PUT"]


def test_a_note_erased_on_the_server_is_kept_here(link):
    link.far.notes = {A: note(A)}
    link.sync()
    link.far.notes.clear()
    link.sync()
    assert link.storage.load_session(A) is not None


def test_a_note_still_being_written_up_there_waits_for_the_next_pass(link):
    link.far.notes = {A: note(A, status="processing")}
    assert link.sync()["copied"] == 0
    link.far.notes[A]["status"] = "complete"
    assert link.sync()["copied"] == 1


def test_a_meeting_the_fallback_took_is_sent_on_under_the_same_id(link):
    link.storage.save_session(A, {**note(A), "user_email": OWNER})
    link.undelivered.write_text(A + "\n")
    assert link.sync()["sent"] == 1
    method, path, body = link.far.calls[0]
    assert (method, path, body["session_id"], body["transcript"]) == ("POST", "/transcribe", A, TRANSCRIPT)
    assert link.undelivered.read_text().split() == []
    assert link.sync()["sent"] == 0                # never sent twice


def test_only_an_https_server_is_accepted(tmp_path, app_module):
    (tmp_path / "linked-server.json").write_text(json.dumps({"url": "http://far.test", "api_key": "x"}))
    with pytest.raises(ValueError):
        linked_server.Link(tmp_path, app_module.storage, OWNER)


def test_without_the_file_nothing_starts(tmp_path, app_module):
    assert linked_server.start(tmp_path, app_module.storage, OWNER) is None
