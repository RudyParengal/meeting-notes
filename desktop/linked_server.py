r"""Optional: keep this Notewell in step with a Meeting Notes server you run yourself.

Off unless %USERPROFILE%\Notewell\linked-server.json exists:

    {"url": "https://notes.example.com", "api_key": "<that server's key>"}

Then, within seconds of the server saying something changed (it is asked every
5 seconds), and at least once a minute:
  - every note on that server is copied here (new ones, and changed ones again)
  - tags, trash and restore follow whichever side changed since the last pass;
    when both did, the server wins
  - a meeting the recorder had to hand to this app instead (the server did not
    answer) is sent to the server once it answers, under the same id

  - the dictation word list and history are shared with the account, so every
    device signed in to it has the same words and the same history

Signing in (sign_in) swaps an email and password for this PC's own device
pass, which replaces the key in linked-server.json.

Nothing here is ever deleted. A note the server erased stays on this PC.
Edits to a note's text stay on this PC: the server has no way to receive them.

The key file and linked-state.json stay on this PC; nothing is sent anywhere
but the server named in the file. A key typed into the file in plain text is
locked to this Windows user (backend/sealed.py) the first time it is read.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

import sealed

log = logging.getLogger("linked")
INTERVAL = 60      # the longest this PC waits between passes
CHECK_EVERY = 5    # how often it asks the server "has anything of mine changed?" (18 Nov 2026)


def read_link(home: Path) -> dict:
    """linked-server.json with its key unlocked."""
    path = home / "linked-server.json"
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    key = sealed.unseal(cfg["api_key"])
    if cfg["api_key"] == key and sealed.available():
        path.write_text(json.dumps({**cfg, "api_key": sealed.seal(key)}, indent=2), encoding="utf-8")
    return {**cfg, "api_key": key}


def _row(item: dict, trashed: bool) -> dict:
    return {"tags": sorted(item.get("tags") or []), "trashed": trashed,
            "status": item.get("status"), "title": item.get("title")}


def _https(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("The server address must start with https://")
    return url


def agent(version: str = "") -> dict:
    """Which Notewell is asking: the server sees it on every request (2.7.0)."""
    if not version:
        return {"User-Agent": "Notewell"}
    return {"User-Agent": f"Notewell/{version}", "X-Notewell-Version": version}


def sign_in(home: Path, url: str, email: str, password: str, device_name: str, version: str = "") -> str:
    """Swap email + password for this PC's own device pass, and keep it in
    linked-server.json (sealed to this Windows user). The file it replaces is
    kept beside it as linked-server.json.bak-<time>. Returns the pass, which
    the caller hands to the recorder; it is never logged."""
    url = _https(url)
    r = requests.post(url + "/auth/device-signin", timeout=30, allow_redirects=False,
                      headers=agent(version),
                      json={"email": email.strip(), "password": password, "device_name": device_name[:80]})
    if r.status_code in (401, 429):
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise ValueError(detail or "Wrong email or password")
    if r.status_code in (404, 405):
        raise ValueError("That server does not have accounts yet")
    r.raise_for_status()
    key = r.json()["pass"]
    path = home / "linked-server.json"
    if path.exists():
        path.with_name(f"linked-server.json.bak-{int(time.time())}").write_bytes(path.read_bytes())
    path.write_text(json.dumps({"url": url, "api_key": sealed.seal(key) if sealed.available() else key},
                               indent=2), encoding="utf-8")
    return key


class Link:
    def __init__(self, home: Path, storage, owner: str, dictation=None, version: str = ""):
        cfg = read_link(home)
        self.url = _https(cfg["url"])
        self.http = requests.Session()
        # A fresh connection is sometimes cut before it reaches the server (17 Nov:
        # "server not reached" right after sign-in). Try again twice. Reads are
        # retried on any cut; uploads only when they never got through, so a
        # meeting is never sent twice.
        again = HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.5, status=0))
        self.http.mount("https://", again)
        self.http.mount("http://", again)
        self.http.headers.update({"X-API-Key": cfg["api_key"], **agent(version)})
        self.dictation = dictation or (lambda: None)   # the word store, once dictation has started
        self.storage, self.owner = storage, owner
        self.state_file = home / "linked-state.json"
        self.undelivered = home / "recorder" / "undelivered.txt"
        self.state = (json.loads(self.state_file.read_text(encoding="utf-8"))
                      if self.state_file.exists() else {})
        self.wake = threading.Event()

    # --- the server ------------------------------------------------------------

    def _get(self, path, **params):
        r = self.http.get(self.url + path, params=params, timeout=30, allow_redirects=False)
        r.raise_for_status()
        return r.json()

    def _send(self, method, path, **kw):
        r = self.http.request(method, self.url + path, timeout=30, allow_redirects=False, **kw)
        r.raise_for_status()
        return r

    # --- this PC ----------------------------------------------------------------

    def _local(self, sid):
        data = self.storage.load_session(sid)
        if data is not None:
            return data, False
        data = self.storage.load_trashed_session(sid)
        return (data, True) if data is not None else (None, None)

    def _store(self, sid, remote_trashed):
        """Copy the server's full note here, in the list or the trash to match."""
        data = self._get(f"/trash/{sid}" if remote_trashed else f"/results/{sid}")
        if data.get("session_id") != sid:
            raise ValueError(f"server answered with a different note for {sid}")
        data["imported_owner"] = data.get("user_email", "")
        data["user_email"] = self.owner
        data["imported_from"] = "linked"
        _, here_trashed = self._local(sid)
        if here_trashed:                           # save_session writes to the list
            self.storage._move(sid, self.storage.TRASH_DIR, self.storage.NOTES_DIR)
        self.storage.save_session(sid, data)
        if remote_trashed:
            self.storage._move(sid, self.storage.NOTES_DIR, self.storage.TRASH_DIR)

    def _move_here(self, sid, to_trash):
        src, dst = self.storage.NOTES_DIR, self.storage.TRASH_DIR
        self.storage._move(sid, *((src, dst) if to_trash else (dst, src)))

    # --- one pass ------------------------------------------------------------------

    def deliver_waiting(self, on_server: set) -> int:
        """Meetings the recorder handed to this app because the server was down."""
        if not self.undelivered.exists():
            return 0
        waiting = [s for s in self.undelivered.read_text(encoding="utf-8").split() if s]
        left, sent = [], 0
        for sid in waiting:
            data, _ = self._local(sid)
            if sid in on_server or data is None:
                continue                           # already there, or gone from here
            if data.get("status") != "complete" or len(data.get("transcript") or "") < 10:
                left.append(sid)                   # still being written up here
                continue
            try:
                self._send("POST", "/transcribe", json={
                    "session_id": sid, "transcript": data["transcript"],
                    "duration_sec": int(data.get("duration_sec") or 0),
                    "timestamp": data.get("timestamp"),
                    "meeting_type": data.get("meeting_type") or "auto"})
                sent += 1
            except Exception as e:
                log.warning("could not send %s to the linked server yet: %s", sid, e)
                left.append(sid)
        self.undelivered.write_text("\n".join(left), encoding="utf-8")
        return sent

    def sync(self) -> dict:
        live = self._get("/sessions", limit=500, all="true")["sessions"]   # the server's maximum
        trash = self._get("/trash", all="true")["sessions"]
        remote = {i["session_id"]: _row(i, False) for i in live}
        remote.update({i["session_id"]: _row(i, True) for i in trash})
        n = {"copied": 0, "updated": 0, "sent": 0, "failed": 0}
        for sid, r in remote.items():
            if not self.storage.is_valid_session_id(sid) or r["status"] not in ("complete", "failed"):
                continue                           # still being processed there: next pass
            try:
                data, here_trashed = self._local(sid)
                base = self.state.get(sid)
                if data is None:
                    self._store(sid, r["trashed"])
                    n["copied"] += 1
                else:
                    here = {"tags": sorted(data.get("tags") or []), "trashed": here_trashed}
                    mine = {k: r[k] for k in here}
                    if base is None or r != base:  # the server changed (or first sight): it wins
                        if base is None or (r["status"], r["title"]) != (base["status"], base["title"]) \
                                or data.get("status") != "complete":
                            self._store(sid, r["trashed"])
                        else:
                            if here["trashed"] != r["trashed"]:
                                self._move_here(sid, r["trashed"])
                            if here["tags"] != r["tags"]:
                                data, _ = self._local(sid)
                                data["tags"] = r["tags"]
                                self.storage.save_session(sid, data)
                                if r["trashed"]:   # save_session writes to the list
                                    self._move_here(sid, True)
                        n["updated"] += here != mine or base is None
                    elif here != mine:             # only this PC changed: tell the server
                        if here["tags"] != r["tags"]:
                            self._send("PUT", f"/results/{sid}/tags", json={"tags": here["tags"]})
                        if here["trashed"] != r["trashed"]:
                            self._send("DELETE", f"/results/{sid}") if here["trashed"] \
                                else self._send("POST", f"/results/{sid}/restore")
                        r = {**r, **here}
                        n["sent"] += 1
                self.state[sid] = r
            except Exception as e:
                n["failed"] += 1
                log.warning("linked server: %s not synced: %s", sid, e)
        n["sent"] += self.deliver_waiting(set(remote))
        self.state_file.write_text(json.dumps(self.state), encoding="utf-8")
        return n

    def sync_dictation(self, store) -> dict:
        """The dictation word list and history, shared with every device on the account.
        A server without these routes (older than accounts) is skipped quietly."""
        try:
            words = self._send("POST", "/account/words", json={"words": store.words_for_sync()}).json()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 405):
                return {}
            raise
        store.merge_words(words["words"])
        n = {"words": len(words["words"]), "sent": 0, "received": 0}
        for _ in range(50):                        # at most 25,000 rows a pass
            state, unsent = store.sync_state(), store.unsent_dictations(500)
            got = self._send("POST", "/account/dictations",
                             json={"items": unsent, **state}).json()
            store.mark_sent([d["uid"] for d in unsent])
            store.merge_dictations(got["items"], got["cursor"], got["cleared_at"])
            n["sent"] += len(unsent)
            n["received"] += len(got["items"])
            if not got["more"] and len(unsent) < 500:
                break
        return n

    def set_key(self, key: str, url: str) -> None:
        """A new pass (after signing in): used from the next request on."""
        self.url = _https(url)
        self.http.headers["X-API-Key"] = key
        self.wake.set()

    def account(self) -> dict:
        """Who this PC is signed in as on the server. Never includes the key."""
        key = self.http.headers.get("X-API-Key", "")
        out = {"linked": True, "url": self.url,
               "pass": "device pass" if key.startswith("nwd_") else "shared key"}
        try:
            out["signed_in_as"] = self._get("/me")
        except Exception as e:
            out["error"] = f"server not reached: {type(e).__name__}"
        return out

    # --- screen recordings: they live on the server only; this PC reads and changes them ---

    def recordings(self) -> list:
        return self._get("/recordings").get("recordings", [])

    def change_recording(self, rid: str, changes: dict) -> dict:
        """Rename a recording or switch its link on or off. Nothing else is passed on."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid or ""):
            raise ValueError("not a recording id")
        body = {k: changes[k] for k in ("title", "share_on") if k in changes}
        return self._send("PATCH", f"/recordings/{rid}", json=body).json()

    def changes(self):
        """The server's change marks, or None when it cannot say (an older server, offline)."""
        try:
            return self._get("/account/changes", all="true")
        except Exception:
            return None

    def pause(self, marks, store=None) -> None:
        """Wait up to INTERVAL, but stop early once the server says a meeting or
        dictation changed since `marks`, or this PC has dictation waiting to go.
        While the server does not answer, it simply waits the full INTERVAL."""
        end = time.monotonic() + INTERVAL
        while (left := end - time.monotonic()) > 0:
            if self.wake.wait(min(CHECK_EVERY, left)):
                return
            now = self.changes()
            if now is None:
                continue
            if marks is None:
                marks = now
            if any(now.get(k) != marks.get(k) for k in ("meetings", "dictation")):
                return
            if store is not None and store.unsent_dictations(1):
                return

    def run(self):
        while True:
            marks = self.changes()   # taken before the pass, so nothing that changes during it is missed
            try:
                n = self.sync()
                if any(n.values()):
                    log.info("linked server: %s", n)
            except Exception as e:
                log.warning("linked server not reached: %s", e)
            store = self.dictation()
            if store is not None:
                try:
                    n = self.sync_dictation(store)
                    if n.get("sent") or n.get("received"):
                        log.info("dictation synced: %s", n)
                except Exception as e:
                    log.warning("dictation not synced: %s", e)
            self.pause(marks, store)
            self.wake.clear()


def start(home: Path, storage, owner: str, dictation=None, version: str = ""):
    """Start syncing in the background if linked-server.json exists; else do nothing."""
    if not (home / "linked-server.json").exists():
        return None
    link = Link(home, storage, owner, dictation, version)
    threading.Thread(target=link.run, daemon=True, name="linked").start()
    log.info("linked to %s", link.url)
    return link
