"""The user list (users.json): who may log in and which API keys exist.

The master key (MEETING_NOTES_API_KEY) always works and acts as the admin
account, so a single-person install needs no users.json at all. Add people
with the admin API or the dashboard's User Management panel.

users.json holds password hashes and API-key *hashes*. It never holds a key
you could use. Older versions stored the key itself; load() upgrades such a
file in place, once, and the plaintext is gone from disk afterwards.
"""
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

import config
from security import hash_api_key, hash_password, keys_equal, new_api_key

log = logging.getLogger("users")
_lock = threading.Lock()


class UsersFileCorrupt(Exception):
    """users.json exists but cannot be read. Raised instead of returning an empty
    list, because the next save would then overwrite every account with nothing."""


def _read() -> list[dict]:
    path: Path = config.USERS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("users", [])
    except Exception as e:
        raise UsersFileCorrupt(f"{path} is not valid JSON ({e}). Fix or restore it; "
                               f"it has not been modified.")


def _write(users: list[dict]) -> None:
    path: Path = config.USERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".users-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"users": users}, f, indent=2)
        try:
            os.chmod(tmp, 0o600)          # no-op on Windows; owner-only on Linux/macOS
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def migrate() -> int:
    """Replace any plaintext api_key with its hash. Returns how many were fixed."""
    try:
        _read()
    except UsersFileCorrupt as e:
        raise config.ConfigError(str(e))        # at start-up: stop, loudly
    with _lock:
        users = _read()
        fixed = 0
        for u in users:
            if u.get("api_key"):
                u["api_key_hash"] = hash_api_key(u.pop("api_key"))
                fixed += 1
        if fixed:
            _write(users)
            log.warning("upgraded %d stored API key(s) to hashes in %s", fixed, config.USERS_FILE)
        return fixed


def admin_user() -> dict:
    return {"email": config.ADMIN_EMAIL, "name": "Admin", "role": "admin"}


def all_users() -> list[dict]:
    return _read()


def find_by_email(email: str) -> Optional[dict]:
    email = (email or "").strip().lower()
    if email == config.ADMIN_EMAIL:
        return admin_user()
    for u in _read():
        if u["email"].lower() == email:
            return u
    return None


def find_by_api_key(key: str) -> Optional[dict]:
    want = hash_api_key(key)
    for u in _read():
        if u.get("api_key_hash") and keys_equal(u["api_key_hash"], want):
            return u
    return None


def create(email: str, password: str, name: str, role: str = "member") -> tuple[dict, str]:
    """Returns (user, api_key). The key is shown to the caller once and never again."""
    email = email.strip().lower()
    with _lock:
        users = _read()
        if email == config.ADMIN_EMAIL or any(u["email"].lower() == email for u in users):
            raise ValueError("user already exists")
        key = new_api_key()
        user = {"email": email, "name": name, "role": role,
                "password_hash": hash_password(password), "api_key_hash": hash_api_key(key)}
        users.append(user)
        _write(users)
    return user, key


def rotate_key(email: str) -> Optional[str]:
    email = email.strip().lower()
    with _lock:
        users = _read()
        for u in users:
            if u["email"].lower() == email:
                key = new_api_key()
                u["api_key_hash"] = hash_api_key(key)
                _write(users)
                return key
    return None


def upgrade_password(email: str, password: str) -> None:
    with _lock:
        users = _read()
        for u in users:
            if u["email"].lower() == email.lower():
                u["password_hash"] = hash_password(password)
                _write(users)
                return


def delete(email: str) -> bool:
    email = email.strip().lower()
    with _lock:
        users = _read()
        kept = [u for u in users if u["email"].lower() != email]
        if len(kept) == len(users):
            return False
        _write(kept)
        return True


def public_view(u: dict) -> dict:
    """What the admin list may show. Never any key or hash."""
    return {"email": u["email"], "name": u.get("name", ""), "role": u.get("role", "member"),
            "has_api_key": bool(u.get("api_key_hash"))}
