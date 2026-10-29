r"""Optional: keep this Notewell in step with a Meeting Notes server you run yourself.

Off unless %USERPROFILE%\Notewell\linked-server.json exists:

    {"url": "https://notes.example.com", "api_key": "<that server's key>"}

Then, once a minute:
  - every note on that server is copied here (new ones, and changed ones again)
  - tags, trash and restore follow whichever side changed since the last pass;
    when both did, the server wins
  - a meeting the recorder had to hand to this app instead (the server did not
    answer) is sent to the server once it answers, under the same id

Nothing here is ever deleted. A note the server erased stays on this PC.
Edits to a note's text stay on this PC: the server has no way to receive them.

The key file and linked-state.json stay on this PC; nothing is sent anywhere
but the server named in the file. A key typed into the file in plain text is
locked to this Windows user (backend/sealed.py) the first time it is read.
"""
import json
import logging
import threading
from pathlib import Path

import requests

import sealed

log = logging.getLogger("linked")
INTERVAL = 60


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


class Link:
    def __init__(self, home: Path, storage, owner: str):
        cfg = read_link(home)
        self.url = cfg["url"].rstrip("/")
        if not self.url.startswith("https://"):
            raise ValueError("linked-server.json: url must start with https://")
        self.http = requests.Session()
        self.http.headers.update({"X-API-Key": cfg["api_key"], "User-Agent": "Notewell"})
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

    def run(self):
        while True:
            try:
                n = self.sync()
                if any(n.values()):
                    log.info("linked server: %s", n)
            except Exception as e:
                log.warning("linked server not reached: %s", e)
            self.wake.wait(INTERVAL)
            self.wake.clear()


def start(home: Path, storage, owner: str):
    """Start syncing in the background if linked-server.json exists; else do nothing."""
    if not (home / "linked-server.json").exists():
        return None
    link = Link(home, storage, owner)
    threading.Thread(target=link.run, daemon=True, name="linked").start()
    log.info("linked to %s", link.url)
    return link
