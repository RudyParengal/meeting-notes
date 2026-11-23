"""Screen recorder: the outbox of waiting recordings and the upload calls.

Moved out of screen_record.py unchanged. screen_record.py re-exports every name.
"""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("screen")

SENT_KEEP_DAYS = 7


# ------------------------------------------------------------------ outbox ---
class Outbox:
    """Recordings waiting to go up. One <id>.mp4 and one <id>.json each."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / "outbox"
        self.sent = self.root / "sent"
        self.dir.mkdir(parents=True, exist_ok=True)

    def video(self, cid):
        return self.dir / f"{cid}.mp4"

    def untrimmed(self, cid):
        return self.dir / f"{cid}.untrimmed.mp4"

    def events(self, cid):
        return self.dir / f"{cid}.events.jsonl"

    def _meta(self, cid):
        return self.dir / f"{cid}.json"

    def new_job(self, title):
        cid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(4).hex()
        job = {"client_id": cid, "title": title, "state": "recording", "started": time.time()}
        self.save(job)
        return job

    def save(self, job):
        p = self._meta(job["client_id"])
        tmp = p.with_suffix(".json.new")
        tmp.write_text(json.dumps(job, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def jobs(self):
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                log.warning("unreadable outbox entry %s", p.name)
        return sorted(out, key=lambda j: j.get("started", 0))

    def waiting(self):
        return [j for j in self.jobs() if j.get("state") == "ready"]

    def recover(self, live=()):
        """After a crash: a recording no copy is still writing is finished as it
        stands. An empty one (stopped before the first frame) is moved aside."""
        for job in self.jobs():
            if job.get("state") != "recording" or job["client_id"] in live:
                continue
            v = self.video(job["client_id"])
            if v.exists() and v.stat().st_size > 0:
                job.update(state="ready", recovered=True)
                self.save(job)
                log.info("recovered unfinished screen recording %s", job["client_id"])
            else:
                self.move_aside(job, "empty")

    def move_aside(self, job, why):
        """Out of the queue but kept: outbox/<why>/."""
        dest = self.dir / why
        dest.mkdir(exist_ok=True)
        cid = job["client_id"]
        for p in (self.video(cid), self.untrimmed(cid), self._meta(cid), self.events(cid)):
            if p.exists():
                os.replace(p, dest / p.name)

    def mark_sent(self, job, share_url):
        self.sent.mkdir(exist_ok=True)
        job.update(state="sent", share_url=share_url, sent=time.time())
        self.save(job)
        cid = job["client_id"]
        for p in (self.video(cid), self.untrimmed(cid), self._meta(cid), self.events(cid)):
            if p.exists():
                os.replace(p, self.sent / p.name)

    def prune_sent(self, days=SENT_KEEP_DAYS, now=None):
        """The server has these; the laptop copy is only kept for a week."""
        cutoff = (now or time.time()) - days * 86400
        gone = 0
        for p in self.sent.glob("*") if self.sent.exists() else ():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                gone += 1
        return gone


# ------------------------------------------------------------------ upload ---
class NotVideo(Exception):
    """The server could not read the file as video. Retrying will not help."""


class UploadStuck(Exception):
    """The server keeps answering without taking any more of the file. Retrying will not help."""


STUCK_AFTER = 3          # answers in a row with no progress before giving up


def upload(job, path: Path, http, base_url: str):
    """Send one recording in pieces, carrying on from wherever the server got to.
    Returns the share link. Raises on a network fault (try again later), NotVideo or UploadStuck."""
    size = path.stat().st_size
    r = http.post(f"{base_url}/recordings", timeout=30, json={
        "title": job["title"], "size": size, "content_type": "video/mp4", "client_id": job["client_id"]})
    r.raise_for_status()
    rec = r.json()
    rid, piece, off = rec["id"], rec.get("piece_max", 8 * 1024 * 1024), rec.get("received", 0)
    if rec.get("status") == "failed":
        raise NotVideo(rid)
    stuck = 0
    with open(path, "rb") as f:
        while rec.get("status") != "ready":
            if rec.get("status") == "failed":
                raise NotVideo(rid)
            f.seek(off)
            data = f.read(piece)
            r = http.put(f"{base_url}/recordings/{rid}/data", params={"offset": off}, data=data,
                         headers={"Content-Type": "application/octet-stream"}, timeout=300)
            if r.status_code == 409:                     # the server is somewhere else: go there
                new = r.json()["received"]
            elif r.status_code == 422:
                raise NotVideo(rid)
            else:
                r.raise_for_status()
                rec = r.json()
                if rec.get("status") == "failed":
                    raise NotVideo(rid)
                if rec.get("status") == "ready":
                    break
                new = rec["received"]
            stuck = stuck + 1 if new == off else 0
            if stuck >= STUCK_AFTER:
                raise UploadStuck(rid)
            off = new
    job["server_id"] = rid
    return rec["share_url"]


EVENTS_UPLOAD_MAX = 5 * 1024 * 1024


def upload_events(job, path: Path, http, base_url: str) -> str:
    """Send the events file once the video is in. Never raises: the link matters more.
    Returns "sent", "none", "old server" or "failed" for the log and the job file."""
    rid = job.get("server_id")
    if not rid or not path or not path.exists():
        return "none"
    try:
        size = path.stat().st_size
    except OSError as e:
        log.warning("screen events for %s unreadable: %s", rid, type(e).__name__)
        return "failed"
    if size == 0:
        return "none"
    if size > EVENTS_UPLOAD_MAX:
        log.warning("screen events for %s over 5 MB (%d bytes), skipped", rid, size)
        return "failed"
    try:
        r = http.put(f"{base_url}/recordings/{rid}/events", data=path.read_bytes(),
                     headers={"Content-Type": "application/x-ndjson"}, timeout=20)
    except Exception as e:
        log.warning("screen events upload for %s failed: %s", rid, type(e).__name__)
        return "failed"
    if r.status_code == 200:
        return "sent"
    if r.status_code in (404, 405):
        return "old server"
    log.warning("screen events upload for %s failed: status %d", rid, r.status_code)
    return "failed"


