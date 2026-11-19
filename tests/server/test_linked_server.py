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


# ------------------------------------------- dictation shared with the account ---
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
from dictation_store import Store  # noqa: E402


class Reply:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class FarAccount:
    """The far server's /account/words and /account/dictations, held in memory,
    following the rules of the server's backend/dictation_sync.py."""

    def __init__(self):
        self.words, self.rows, self.cleared, self.seq = {}, [], 0.0, 0

    def send(self, method, path, json=None):
        if path == "/account/words":
            for w in json["words"]:
                old = self.words.get(w["uid"])
                if old is None or w["updated_at"] > old["updated_at"]:
                    self.words[w["uid"]] = dict(w)
            return Reply({"words": list(self.words.values())})
        if json["cleared_at"] > self.cleared:
            self.cleared = json["cleared_at"]
            self.rows = [r for r in self.rows if r["created_at"] > self.cleared]
        have = {r["uid"] for r in self.rows}
        for d in json["items"]:
            if d["created_at"] > self.cleared and d["uid"] not in have:
                self.seq += 1
                self.rows.append({**d, "seq": self.seq})
        after = [r for r in self.rows if r["seq"] > json["since"]]
        page = after[:500]
        return Reply({"items": page, "cursor": page[-1]["seq"] if page else json["since"],
                      "more": len(after) > 500, "cleared_at": self.cleared})


def _account_link(tmp_path, app_module, far, name):
    home = tmp_path / name
    (home / "recorder").mkdir(parents=True)
    (home / "linked-server.json").write_text(json.dumps({"url": "https://far.test", "api_key": "nwd_not-real"}))
    lk = linked_server.Link(home, app_module.storage, OWNER)
    lk._send = far.send
    return lk, Store(home / "dictation.db")


def test_words_and_dictations_made_on_one_pc_reach_another(tmp_path, app_module):
    far = FarAccount()
    laptop, laptop_store = _account_link(tmp_path, app_module, far, "laptop")
    other, other_store = _account_link(tmp_path, app_module, far, "other")
    laptop_store.add_word("Parengal", "para angle")
    laptop_store.add_dictation("Send the Parengal proposal", "send the para angle proposal", app="word.exe")
    assert laptop.sync_dictation(laptop_store)["sent"] == 1
    assert other.sync_dictation(other_store)["received"] == 1
    assert [w["term"] for w in other_store.words()] == ["Parengal"]
    assert [d["text"] for d in other_store.history()] == ["Send the Parengal proposal"]
    assert laptop.sync_dictation(laptop_store)["sent"] == 0      # never sent twice


def test_a_word_removed_on_one_pc_is_removed_on_the_other(tmp_path, app_module):
    far = FarAccount()
    laptop, laptop_store = _account_link(tmp_path, app_module, far, "laptop")
    other, other_store = _account_link(tmp_path, app_module, far, "other")
    w = laptop_store.add_word("Notewell")
    laptop.sync_dictation(laptop_store)
    other.sync_dictation(other_store)
    assert [x["term"] for x in other_store.words()] == ["Notewell"]
    laptop_store.delete_word(w["id"])
    laptop.sync_dictation(laptop_store)
    other.sync_dictation(other_store)
    assert other_store.words() == []


def test_more_than_one_page_of_history_arrives_in_one_pass(tmp_path, app_module):
    far = FarAccount()
    laptop, laptop_store = _account_link(tmp_path, app_module, far, "laptop")
    other, other_store = _account_link(tmp_path, app_module, far, "other")
    for i in range(620):
        laptop_store.add_dictation(f"dictation {i}", "", app="notepad.exe")
    assert laptop.sync_dictation(laptop_store)["sent"] == 620
    assert other.sync_dictation(other_store)["received"] == 620


def test_a_server_without_accounts_is_skipped_quietly(tmp_path, app_module):
    lk, store = _account_link(tmp_path, app_module, FarAccount(), "laptop")

    def older_server(method, path, json=None):
        raise linked_server.requests.HTTPError(response=type("R", (), {"status_code": 404})())
    lk._send = older_server
    assert lk.sync_dictation(store) == {}


# ----------------------------------------------------------- signing in ---
class Posted:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise linked_server.requests.HTTPError(response=self)


def test_signing_in_keeps_the_new_pass_and_a_copy_of_the_old_file(tmp_path, monkeypatch):
    old = {"url": "https://far.test", "api_key": "shared-not-real"}
    (tmp_path / "linked-server.json").write_text(json.dumps(old))
    sent = {}

    def post(url, json=None, **kw):
        sent.update(url=url, body=json)
        return Posted(200, {"device_id": 7, "pass": "nwd_new-not-real"})
    monkeypatch.setattr(linked_server.requests, "post", post)
    key = linked_server.sign_in(tmp_path, "https://far.test/", "rudy@far.test", "pw-not-real", "Notewell on PC")
    assert key == "nwd_new-not-real"
    assert sent["url"] == "https://far.test/auth/device-signin"
    assert sent["body"]["device_name"] == "Notewell on PC"
    assert linked_server.read_link(tmp_path) == {"url": "https://far.test", "api_key": "nwd_new-not-real"}
    backups = list(tmp_path.glob("linked-server.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == old
    assert "pw-not-real" not in (tmp_path / "linked-server.json").read_text()


def test_a_wrong_password_changes_nothing(tmp_path, monkeypatch):
    (tmp_path / "linked-server.json").write_text(json.dumps({"url": "https://far.test", "api_key": "shared"}))
    before = (tmp_path / "linked-server.json").read_text()
    monkeypatch.setattr(linked_server.requests, "post",
                        lambda *a, **k: Posted(401, {"detail": "Wrong email or password"}))
    with pytest.raises(ValueError, match="Wrong email or password"):
        linked_server.sign_in(tmp_path, "https://far.test", "rudy@far.test", "nope", "PC")
    assert (tmp_path / "linked-server.json").read_text() == before
    assert not list(tmp_path.glob("linked-server.json.bak-*"))


def test_signing_in_needs_an_https_address(tmp_path):
    with pytest.raises(ValueError, match="https"):
        linked_server.sign_in(tmp_path, "http://far.test", "a@b.test", "pw", "PC")


def test_a_new_pass_is_used_at_once_and_never_shown(link):
    link._get = lambda path, **p: {"email": "rudy@far.test", "name": "Rudy", "role": "admin"}
    assert link.account()["pass"] == "shared key"
    link.set_key("nwd_secret-not-real", "https://far.test")
    acc = link.account()
    assert link.http.headers["X-API-Key"] == "nwd_secret-not-real"
    assert acc["pass"] == "device pass" and acc["signed_in_as"]["email"] == "rudy@far.test"
    assert "secret-not-real" not in json.dumps(acc)


def test_a_connection_cut_before_any_answer_is_tried_again(app_module, tmp_path):
    """17 Nov: right after sign-in the account check failed with
    'server not reached: ConnectionError' - the way to the server cut a fresh
    connection before it got there. One cut must not show as an outage."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    seen = []

    def serve():
        for n in range(2):
            conn, _ = srv.accept()
            conn.recv(65536)
            seen.append(n)
            if n == 0:
                conn.close()          # hang up without answering
                continue
            body = b'{"email": "rudy@far.test", "name": "Rudy", "role": "admin"}'
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                         b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            conn.close()
    threading.Thread(target=serve, daemon=True).start()

    (tmp_path / "recorder").mkdir()
    (tmp_path / "linked-server.json").write_text(
        json.dumps({"url": "https://far.test", "api_key": "nwd_not-real"}))
    lk = linked_server.Link(tmp_path, app_module.storage, OWNER)
    lk.url = "http://127.0.0.1:%d" % srv.getsockname()[1]
    acc = lk.account()
    srv.close()
    assert "error" not in acc, acc
    assert acc["signed_in_as"]["email"] == "rudy@far.test"
    assert seen == [0, 1]


def test_an_upload_cut_after_sending_is_not_sent_twice(app_module, tmp_path):
    """The try-again above must never send a meeting twice: once a POST has
    gone out, a cut answer is left for the next sync to sort out."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(5)
    seen = []

    def serve():
        try:
            while True:
                conn, _ = srv.accept()
                conn.recv(65536)
                seen.append(1)
                conn.close()          # took the upload, hung up without answering
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()

    (tmp_path / "recorder").mkdir()
    (tmp_path / "linked-server.json").write_text(
        json.dumps({"url": "https://far.test", "api_key": "nwd_not-real"}))
    lk = linked_server.Link(tmp_path, app_module.storage, OWNER)
    lk.url = "http://127.0.0.1:%d" % srv.getsockname()[1]
    with pytest.raises(Exception):
        lk._send("POST", "/upload", json={"note": "x"})
    srv.close()
    assert seen == [1]


# --- live: sync within seconds of a change (18 Nov 2026) ------------------------

class Marks:
    """The far server's /account/changes answers, one per question."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), 0

    def __call__(self):
        self.asked += 1
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


class Unsent:
    def __init__(self, rows):
        self.rows = rows

    def unsent_dictations(self, limit=500):
        return self.rows[:limit]


QUIET = {"meetings": "m1", "dictation": "d1", "recordings": "r1"}


def _timed_pause(link, monkeypatch, marks, store=None, interval=2.0):
    import time
    monkeypatch.setattr(linked_server, "INTERVAL", interval)
    monkeypatch.setattr(linked_server, "CHECK_EVERY", 0.02)
    t = time.monotonic()
    link.pause(marks, store)
    return time.monotonic() - t


def test_a_meeting_changed_on_the_server_starts_a_pass_within_seconds(link, monkeypatch):
    link.changes = Marks(QUIET, QUIET, {**QUIET, "meetings": "m2"})
    took = _timed_pause(link, monkeypatch, QUIET)
    assert took < 1.0 and link.changes.asked == 3


def test_dictation_changed_on_the_server_starts_a_pass_too(link, monkeypatch):
    link.changes = Marks(QUIET, {**QUIET, "dictation": "d2"})
    assert _timed_pause(link, monkeypatch, QUIET) < 1.0


def test_dictation_waiting_on_this_pc_is_sent_without_waiting_a_minute(link, monkeypatch):
    link.changes = Marks(QUIET)
    assert _timed_pause(link, monkeypatch, QUIET, Unsent([{"uid": "local-row-0001"}])) < 1.0


def test_nothing_changed_means_it_waits_the_full_interval_and_keeps_asking(link, monkeypatch):
    link.changes = Marks(QUIET)
    took = _timed_pause(link, monkeypatch, QUIET, Unsent([]), interval=0.3)
    assert took >= 0.29 and link.changes.asked >= 5


def test_a_server_that_cannot_say_falls_back_to_once_a_minute(link, monkeypatch):
    """Older server (no such route) or offline: no early pass, no crash, even with dictation waiting."""
    link.changes = Marks(None)
    took = _timed_pause(link, monkeypatch, None, Unsent([{"uid": "local-row-0001"}]), interval=0.3)
    assert took >= 0.29


def test_a_new_recording_alone_does_not_start_a_pass(link, monkeypatch):
    """Recordings are not copied to this PC, so their mark moving is no reason to sync."""
    link.changes = Marks({**QUIET, "recordings": "r2"})
    assert _timed_pause(link, monkeypatch, QUIET, interval=0.3) >= 0.29


def test_changes_asks_for_everyones_meetings_and_never_raises(link):
    asked = []
    link._get = lambda path, **p: asked.append((path, p)) or QUIET
    assert link.changes() == QUIET and asked == [("/account/changes", {"all": "true"})]

    def boom(path, **p):
        raise linked_server.requests.HTTPError("404")
    link._get = boom
    assert link.changes() is None


# --- screen recordings, read and changed from this PC's dashboard (18 Nov 2026) ---
def test_recordings_are_read_from_the_server(link):
    asked = []
    link._get = lambda path, **p: asked.append(path) or {"recordings": [{"id": "abc123", "title": "Made-up"}]}
    assert link.recordings() == [{"id": "abc123", "title": "Made-up"}] and asked == ["/recordings"]


def test_only_the_title_and_link_switch_are_sent(link):
    sent = []
    link._send = lambda method, path, **kw: sent.append((method, path, kw)) or Reply({"id": "abc123", "share_on": False})
    out = link.change_recording("abc123", {"share_on": False, "title": "New name", "password": "not-real"})
    assert out == {"id": "abc123", "share_on": False}
    assert sent == [("PATCH", "/recordings/abc123", {"json": {"share_on": False, "title": "New name"}})]


def test_an_odd_recording_id_is_refused_before_anything_is_sent(link):
    link._send = lambda *a, **k: pytest.fail("nothing should be sent")
    for bad in ("../account", "abc/def", "", "x" * 65):
        with pytest.raises(ValueError):
            link.change_recording(bad, {"share_on": True})
