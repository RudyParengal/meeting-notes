"""JSON + Markdown storage for meeting notes with multi-user and tagging support."""
import json, os, re, shutil, logging
from datetime import datetime
from pathlib import Path
from typing import Optional

NOTES_DIR = Path(os.environ.get('NOTES_DIR', '/var/meeting-notes'))
NOTES_DIR.mkdir(parents=True, exist_ok=True)

TRASH_DIR = NOTES_DIR / 'trash'
TRASH_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@example.com')

# Auto-export folder (B4) - if set, copy .md files here too
EXPORT_FOLDER = os.environ.get('EXPORT_FOLDER', '').strip()

log = logging.getLogger('storage')


def _safe_title(title: str) -> str:
    t = re.sub(r'[^\w\s-]', '', title)[:50].strip()
    return re.sub(r'\s+', '-', t) or 'meeting'


def _extract_title(summary_md: str) -> str:
    m = re.search(r'## Meeting Title\s*\n(.+?)(?:\n|$)', summary_md)
    if m:
        return m.group(1).strip().strip('*').strip('_')
    return 'Untitled Meeting'


def _export_to_folder(md_path: Path):
    """Copy .md file to the export folder if configured (B4)."""
    if not EXPORT_FOLDER:
        return
    try:
        export_dir = Path(EXPORT_FOLDER)
        export_dir.mkdir(parents=True, exist_ok=True)
        dest = export_dir / md_path.name
        shutil.copy2(str(md_path), str(dest))
        log.info(f'Exported to {dest}')
    except Exception as e:
        log.warning(f'Export to folder failed: {e}')


def save_session(session_id: str, data: dict):
    ts = datetime.fromisoformat(data['timestamp'])
    title_slug = _safe_title(_extract_title(data.get('summary', '')))
    date_prefix = ts.strftime('%Y-%m-%d_%H-%M')
    base = NOTES_DIR / f'{date_prefix}_{title_slug}_{session_id[:8]}'
    for old_p in NOTES_DIR.glob(f'*_{session_id[:8]}.*'):
        if old_p.stem != base.stem:
            old_p.unlink(missing_ok=True)

    md = f"""# Meeting Notes \u2014 {ts.strftime('%Y-%m-%d %H:%M')}

**Session ID:** {session_id}
**Duration:** {data.get('duration_sec', 0) // 60} min
**Type:** {data.get('meeting_type', 'unknown')}

---

{data.get('summary', '(summary failed)')}

---

# Action Plan

{data.get('actions', '(action extraction failed)')}

---

## Raw Transcript
<details>
<summary>Click to expand</summary>

```
{data.get('transcript', '')}
```
</details>
"""
    md_path = base.with_suffix('.md')
    json_path = base.with_suffix('.json')
    md_path.write_text(md, encoding='utf-8')
    json_path.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')

    # Auto-export (B4)
    if data.get('status') == 'complete':
        _export_to_folder(md_path)

    return str(md_path), str(json_path)


def load_session(session_id: str) -> Optional[dict]:
    for p in NOTES_DIR.glob(f'*_{session_id[:8]}.json'):
        return json.loads(p.read_text(encoding='utf-8'))
    return None


def list_sessions(limit: int = 100, search: Optional[str] = None,
                  user_email: Optional[str] = None, show_all: bool = False,
                  tag: Optional[str] = None) -> list[dict]:
    items = []
    for p in sorted(NOTES_DIR.glob('*.json'), reverse=True)[:limit * 3]:
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            # User filtering: if not show_all, filter by user_email
            if user_email and not show_all:
                owner = data.get('user_email', ADMIN_EMAIL)
                if owner != user_email:
                    continue
            if search:
                hay = (data.get('summary', '') + data.get('actions', '') + data.get('transcript', '')).lower()
                if search.lower() not in hay:
                    continue
            if tag:
                session_tags = data.get('tags', [])
                if tag.lower() not in [t.lower() for t in session_tags]:
                    continue
            items.append({
                'session_id': data.get('session_id'),
                'timestamp': data.get('timestamp'),
                'title': _extract_title(data.get('summary', '')),
                'meeting_type': data.get('meeting_type'),
                'duration_sec': data.get('duration_sec', 0),
                'status': data.get('status', 'complete'),
                'user_email': data.get('user_email', ADMIN_EMAIL),
                'tags': data.get('tags', []),
            })
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


def update_session_tags(session_id: str, tags: list[str]) -> bool:
    """Update tags for a session. Returns True if successful."""
    for p in NOTES_DIR.glob(f'*_{session_id[:8]}.json'):
        data = json.loads(p.read_text(encoding='utf-8'))
        data['tags'] = tags
        p.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
        return True
    return False


def get_session_owner(session_id: str) -> str:
    """Get the owner email for a session. Defaults to admin for legacy sessions."""
    data = load_session(session_id)
    if data:
        return data.get('user_email', ADMIN_EMAIL)
    return ADMIN_EMAIL


def get_markdown(session_id: str) -> Optional[str]:
    for p in NOTES_DIR.glob(f'*_{session_id[:8]}.md'):
        return p.read_text(encoding='utf-8')
    return None


# -----------------------------------------------------------------
# Soft-delete (trash) support
# -----------------------------------------------------------------
def _find_session_files(session_id: str, directory: Path) -> list[Path]:
    """Return [.md, .json] (either/both may be missing) for a session in the given dir."""
    return list(directory.glob(f'*_{session_id[:8]}.*'))


def _load_session_from(session_id: str, directory: Path) -> Optional[dict]:
    for p in directory.glob(f'*_{session_id[:8]}.json'):
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return None
    return None


def delete_session(session_id: str, user_email: str, is_admin: bool) -> bool:
    """Soft-delete: move session files from NOTES_DIR to TRASH_DIR.
    Enforces ownership (user can only delete their own unless admin)."""
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_session_from(session_id, NOTES_DIR)
    if not data:
        return False
    owner = data.get('user_email', ADMIN_EMAIL)
    if not is_admin and owner != user_email:
        raise PermissionError('forbidden: not owner')
    files = _find_session_files(session_id, NOTES_DIR)
    if not files:
        return False
    for src in files:
        dest = TRASH_DIR / src.name
        # If the destination already exists (e.g. prior trash of same id), overwrite
        if dest.exists():
            dest.unlink(missing_ok=True)
        shutil.move(str(src), str(dest))
    log.info(f'Soft-deleted session {session_id} by {user_email}')
    return True


def restore_session(session_id: str, user_email: str, is_admin: bool) -> bool:
    """Restore files back from TRASH_DIR to NOTES_DIR. Enforces ownership."""
    data = _load_session_from(session_id, TRASH_DIR)
    if not data:
        return False
    owner = data.get('user_email', ADMIN_EMAIL)
    if not is_admin and owner != user_email:
        raise PermissionError('forbidden: not owner')
    files = _find_session_files(session_id, TRASH_DIR)
    if not files:
        return False
    for src in files:
        dest = NOTES_DIR / src.name
        if dest.exists():
            dest.unlink(missing_ok=True)
        shutil.move(str(src), str(dest))
    log.info(f'Restored session {session_id} by {user_email}')
    return True


def list_trash(user_email: Optional[str] = None, show_all: bool = False) -> list[dict]:
    """Return sessions currently in the trash, filtered like list_sessions."""
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(TRASH_DIR.glob('*.json'), reverse=True):
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            if user_email and not show_all:
                owner = data.get('user_email', ADMIN_EMAIL)
                if owner != user_email:
                    continue
            items.append({
                'session_id': data.get('session_id'),
                'timestamp': data.get('timestamp'),
                'title': _extract_title(data.get('summary', '')),
                'meeting_type': data.get('meeting_type'),
                'duration_sec': data.get('duration_sec', 0),
                'status': data.get('status', 'complete'),
                'user_email': data.get('user_email', ADMIN_EMAIL),
                'tags': data.get('tags', []),
                'trashed': True,
            })
        except Exception:
            continue
    return items


def purge_session(session_id: str, user_email: str, is_admin: bool) -> bool:
    """Permanently delete a session that is already in the trash."""
    data = _load_session_from(session_id, TRASH_DIR)
    if not data:
        return False
    owner = data.get('user_email', ADMIN_EMAIL)
    if not is_admin and owner != user_email:
        raise PermissionError('forbidden: not owner')
    files = _find_session_files(session_id, TRASH_DIR)
    if not files:
        return False
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except Exception as e:
            log.warning(f'Failed to purge {f}: {e}')
    log.info(f'Purged session {session_id} by {user_email}')
    return True


def load_trashed_session(session_id: str) -> Optional[dict]:
    """Load a session JSON from the trash dir (for detail view)."""
    return _load_session_from(session_id, TRASH_DIR)


def get_trashed_markdown(session_id: str) -> Optional[str]:
    for p in TRASH_DIR.glob(f'*_{session_id[:8]}.md'):
        return p.read_text(encoding='utf-8')
    return None
