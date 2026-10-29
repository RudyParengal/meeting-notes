"""JSON + Markdown storage for meeting notes, with owners, tags and a trash can.

Each meeting is two files in NOTES_DIR:

    2026-09-04_13-43_Some-Title_f392a3b641324078b813ffbbc7375095.json    the full record
    2026-09-04_13-43_Some-Title_f392a3b641324078b813ffbbc7375095.md      the readable note

The name ends in the full 32-character session id, so two meetings can never
share a file. Notes written by earlier versions end in only the first 8
characters; those are still found, and are renamed the next time they are saved.
Every lookup also checks the id stored inside the file.
"""
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from security import is_valid_session_id

log = logging.getLogger("storage")

NOTES_DIR: Path = config.NOTES_DIR
TRASH_DIR: Path = NOTES_DIR / "trash"
ADMIN_EMAIL: str = config.ADMIN_EMAIL
EXPORT_FOLDER: str = config.EXPORT_FOLDER


def init() -> None:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)


def _safe_title(title: str) -> str:
    t = re.sub(r"[^\w\s-]", "", title)[:50].strip()
    return re.sub(r"\s+", "-", t) or "meeting"


def _extract_title(summary_md: str) -> str:
    m = re.search(r"## Meeting Title\s*\n(.+?)(?:\n|$)", summary_md or "")
    if m:
        return m.group(1).strip().strip("*").strip("_")
    return "Untitled Meeting"


def _atomic_write(path: Path, text: str) -> None:
    """Write to a temp file in the same folder, then rename over the target.
    A crash or a full disk leaves the old file intact instead of half a file."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _export_to_folder(md_path: Path) -> None:
    """Copy the finished note to EXPORT_FOLDER, if one is configured."""
    if not EXPORT_FOLDER:
        return
    try:
        export_dir = Path(EXPORT_FOLDER)
        export_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(md_path), str(export_dir / md_path.name))
    except Exception as e:
        log.warning("export to folder failed: %s", e)


def _files_for(session_id: str, directory: Path) -> list[Path]:
    """Every file belonging to exactly this session (the .json and its .md).

    Callers validate the id first; it is re-checked here so no path can build a
    glob pattern out of untrusted input.
    """
    if not is_valid_session_id(session_id):
        return []
    jsons = list(directory.glob(f"*_{session_id}.json"))
    if not jsons:                                    # notes from versions before 2.0
        for js in directory.glob(f"*_{session_id[:8]}.json"):
            d = _read_json(js)
            if d and d.get("session_id") == session_id:
                jsons.append(js)
    files = {*jsons, *directory.glob(f"*_{session_id}.md")}
    files.update(js.with_suffix(".md") for js in jsons if js.with_suffix(".md").exists())
    return sorted(files)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_from(session_id: str, directory: Path) -> Optional[dict]:
    for p in _files_for(session_id, directory):
        if p.suffix != ".json":
            continue
        data = _read_json(p)
        if data and data.get("session_id") == session_id:
            return data
    return None


def save_session(session_id: str, data: dict) -> tuple[str, str]:
    if not is_valid_session_id(session_id):
        raise ValueError("invalid session id")
    ts = datetime.fromisoformat(str(data["timestamp"]).replace("Z", "+00:00"))
    title_slug = _safe_title(_extract_title(data.get("summary", "")))
    base = NOTES_DIR / f"{ts.strftime('%Y-%m-%d_%H-%M')}_{title_slug}_{session_id}"

    # The title can change between "processing" and "complete", and old notes
    # carry the short id; drop whatever name this session had before.
    for old in _files_for(session_id, NOTES_DIR):
        if old.stem != base.stem:
            old.unlink(missing_ok=True)

    md = f"""# Meeting Notes — {ts.strftime('%Y-%m-%d %H:%M')}

**Session ID:** {session_id}
**Duration:** {int(data.get('duration_sec', 0)) // 60} min
**Type:** {data.get('meeting_type', 'unknown')}

---

{data.get('summary', '(summary failed)')}

---

# Action Plan

{data.get('actions', '(action extraction failed)')}

---

## Full Transcript

```
{data.get('transcript', '')}
```
"""
    md_path, json_path = base.with_suffix(".md"), base.with_suffix(".json")
    _atomic_write(md_path, md)
    _atomic_write(json_path, json.dumps(data, indent=2, default=str))
    if data.get("status") == "complete":
        _export_to_folder(md_path)
    return str(md_path), str(json_path)


def load_session(session_id: str) -> Optional[dict]:
    return _load_from(session_id, NOTES_DIR)


def load_trashed_session(session_id: str) -> Optional[dict]:
    return _load_from(session_id, TRASH_DIR)


def get_markdown(session_id: str) -> Optional[str]:
    for p in _files_for(session_id, NOTES_DIR):
        if p.suffix == ".md":
            return p.read_text(encoding="utf-8")
    return None


def get_trashed_markdown(session_id: str) -> Optional[str]:
    for p in _files_for(session_id, TRASH_DIR):
        if p.suffix == ".md":
            return p.read_text(encoding="utf-8")
    return None


def _summary_row(data: dict, **extra) -> dict:
    row = {
        "session_id": data.get("session_id"),
        "timestamp": data.get("timestamp"),
        "title": _extract_title(data.get("summary", "")),
        "meeting_type": data.get("meeting_type"),
        "duration_sec": data.get("duration_sec", 0),
        "status": data.get("status", "complete"),
        "user_email": data.get("user_email", ADMIN_EMAIL),
        "tags": data.get("tags", []),
    }
    row.update(extra)
    return row


def list_sessions(limit: int = 100, search: Optional[str] = None,
                  user_email: Optional[str] = None, show_all: bool = False,
                  tag: Optional[str] = None) -> list[dict]:
    items: list[dict] = []
    needle = search.lower() if search else None
    for p in sorted(NOTES_DIR.glob("*.json"), reverse=True):
        data = _read_json(p)
        if not data:
            continue
        if user_email and not show_all and data.get("user_email", ADMIN_EMAIL) != user_email:
            continue
        if needle:
            hay = (data.get("summary", "") + data.get("actions", "") + data.get("transcript", "")).lower()
            if needle not in hay:
                continue
        if tag and tag.lower() not in [t.lower() for t in data.get("tags", [])]:
            continue
        items.append(_summary_row(data))
        if len(items) >= limit:
            break
    return items


def _move(session_id: str, src: Path, dst: Path) -> bool:
    files = _files_for(session_id, src)
    if not files:
        return False
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = dst / f.name
        target.unlink(missing_ok=True)
        shutil.move(str(f), str(target))
    return True


def _check_owner(data: dict, user_email: str, is_admin: bool) -> None:
    if not is_admin and data.get("user_email", ADMIN_EMAIL) != user_email:
        raise PermissionError("forbidden: not owner")


def delete_session(session_id: str, user_email: str, is_admin: bool) -> bool:
    """Soft delete: move to the trash folder. Nothing is erased."""
    data = _load_from(session_id, NOTES_DIR)
    if not data:
        return False
    _check_owner(data, user_email, is_admin)
    ok = _move(session_id, NOTES_DIR, TRASH_DIR)
    if ok:
        log.info("trashed %s by %s", session_id, user_email)
    return ok


def restore_session(session_id: str, user_email: str, is_admin: bool) -> bool:
    data = _load_from(session_id, TRASH_DIR)
    if not data:
        return False
    _check_owner(data, user_email, is_admin)
    ok = _move(session_id, TRASH_DIR, NOTES_DIR)
    if ok:
        log.info("restored %s by %s", session_id, user_email)
    return ok


def list_trash(user_email: Optional[str] = None, show_all: bool = False) -> list[dict]:
    items: list[dict] = []
    for p in sorted(TRASH_DIR.glob("*.json"), reverse=True):
        data = _read_json(p)
        if not data:
            continue
        if user_email and not show_all and data.get("user_email", ADMIN_EMAIL) != user_email:
            continue
        items.append(_summary_row(data, trashed=True))
    return items


def purge_session(session_id: str, user_email: str, is_admin: bool) -> bool:
    """Permanently delete a session that is already in the trash."""
    data = _load_from(session_id, TRASH_DIR)
    if not data:
        return False
    _check_owner(data, user_email, is_admin)
    files = _files_for(session_id, TRASH_DIR)
    for f in files:
        f.unlink(missing_ok=True)
    log.info("purged %s by %s", session_id, user_email)
    return bool(files)
