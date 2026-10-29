"""Passwords, API keys, session tokens and login throttling.

Everything here uses the standard library only, so there is nothing extra to
install and nothing to keep patched beyond Python itself.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

# Session / meeting ids are uuid4().hex. Anything else is rejected before it
# gets anywhere near a filename or a glob pattern - the original code passed
# the raw id into glob(), so an id of "*" matched every file on disk.
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SESSION_ID_PATTERN = r"^[0-9a-f]{32}$"   # for pydantic / FastAPI validation

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def is_valid_session_id(value: str) -> bool:
    return bool(value) and bool(SESSION_ID_RE.match(value))


# ------------------------------------------------------------ passwords ---
def hash_password(password: str) -> str:
    """Salted scrypt. The original used unsalted SHA-256, which is trivially
    reversible with a precomputed table."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> tuple[bool, bool]:
    """Returns (matches, needs_rehash).

    Still accepts the legacy unsalted SHA-256 hex that older installs wrote to
    users.json, and flags it for upgrade so the file fixes itself on next login.
    """
    if not stored:
        return False, False
    if stored.startswith("scrypt$"):
        try:
            _, n, r, p, salt_b64, hash_b64 = stored.split("$")
            salt, want = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
            got = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p))
        except Exception:
            return False, False
        return hmac.compare_digest(got, want), False
    # legacy: 64 hex chars of sha256(password)
    legacy = hashlib.sha256(password.encode()).hexdigest()
    ok = hmac.compare_digest(legacy, stored.lower())
    return ok, ok


# ------------------------------------------------------------- api keys ---
def new_api_key() -> str:
    return "mn_" + secrets.token_hex(24)


def hash_api_key(key: str) -> str:
    """API keys are 192 random bits, so a fast unsalted hash is sufficient: there
    is nothing to brute-force. What matters is that the file never holds the key."""
    return hashlib.sha256(key.encode()).hexdigest()


def keys_equal(a: str, b: str) -> bool:
    """Constant-time comparison, so response timing cannot leak the key."""
    return hmac.compare_digest(a.encode(), b.encode())


# --------------------------------------------------------- session tokens ---
class TokenStore:
    """In-memory login tokens with an expiry. A restart signs everyone out,
    which is the safe direction to fail."""

    def __init__(self, ttl_hours: int = 12):
        self.ttl = ttl_hours * 3600
        self._tokens: dict[str, tuple[str, float]] = {}

    def issue(self, email: str) -> str:
        self._purge()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (email, time.time() + self.ttl)
        return token

    def lookup(self, token: str) -> str | None:
        item = self._tokens.get(token)
        if not item:
            return None
        email, expires = item
        if expires < time.time():
            self._tokens.pop(token, None)
            return None
        return email

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def revoke_user(self, email: str) -> None:
        for t in [t for t, (e, _) in self._tokens.items() if e == email]:
            self._tokens.pop(t, None)

    def _purge(self) -> None:
        now = time.time()
        for t in [t for t, (_, exp) in self._tokens.items() if exp < now]:
            self._tokens.pop(t, None)


# ----------------------------------------------------------- rate limiting ---
class LoginThrottle:
    """Stops password guessing. Counts failures per (client, account) and per
    client overall, so rotating the account name does not reset the count."""

    def __init__(self, max_failures: int = 8, window_sec: int = 900):
        self.max = max_failures
        self.window = window_sec
        self._fails: dict[str, deque] = defaultdict(deque)

    def _keys(self, client: str, account: str) -> list[str]:
        return [f"c:{client}", f"a:{client}:{account.lower()}"]

    def _trim(self, key: str, now: float) -> deque:
        q = self._fails[key]
        while q and q[0] < now - self.window:
            q.popleft()
        return q

    def blocked(self, client: str, account: str) -> int:
        """Seconds until another attempt is allowed, or 0 if not blocked."""
        now = time.time()
        wait = 0
        for k in self._keys(client, account):
            q = self._trim(k, now)
            limit = self.max * 3 if k.startswith("c:") else self.max
            if len(q) >= limit:
                wait = max(wait, int(q[0] + self.window - now) + 1)
        return wait

    def record_failure(self, client: str, account: str) -> None:
        now = time.time()
        for k in self._keys(client, account):
            self._fails[k].append(now)

    def record_success(self, client: str, account: str) -> None:
        self._fails.pop(f"a:{client}:{account.lower()}", None)
