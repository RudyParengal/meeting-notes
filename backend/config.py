"""Settings, read once from the environment (and backend/.env).

The rule for this file: a bad configuration must stop the server at start-up
with a message that says exactly what to fix. Nothing here guesses, and nothing
fails quietly. Two outages in the original deployment came from a service that
started "successfully" with a placeholder or missing credential and then did
nothing useful; refusing to start is the loud version of that failure.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# .env next to this file. Real environment variables win over the file, so a
# container or systemd unit can override anything without editing it.
load_dotenv(HERE / ".env", override=False)

__version__ = "2.2.0"

# Values that mean "the person copied .env.example and never filled it in".
_PLACEHOLDERS = {
    "", "changeme", "change-me", "change_me", "your-key-here", "your_api_key",
    "your-api-key", "xxx", "todo", "paste", "secret", "password", "test",
}


EXIT_CONFIG = 78       # EX_CONFIG: "fix your settings", not "try again"


class ConfigError(SystemExit):
    """A bad configuration, found at start-up. Prints what to fix, then exits 78.

    78 is deliberate. A service manager (systemd, Docker, a supervisor) that
    sees any other failure code restarts the program, which fails the same way,
    forever - a crash loop that hides the one useful message in the log. The
    unit files in deploy/ list 78 as "do not restart".
    """

    def __init__(self, message: str):
        print(f"\nCONFIGURATION ERROR: {message}\n"
              f"See docs/CONFIGURATION.md, or run: python scripts/doctor.py\n", file=sys.stderr)
        super().__init__(EXIT_CONFIG)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}")


def is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return v in _PLACEHOLDERS or v.startswith(("paste", "your-", "your_", "<", "xxxx"))


def is_local_url(url: str) -> bool:
    """True for endpoints on this machine, which normally need no API key."""
    u = url.lower()
    return any(h in u for h in ("://localhost", "://127.", "://[::1]", "://host.docker.internal"))


# ---------------------------------------------------------------- server ---
HOST = env("HOST", "127.0.0.1")        # loopback by default: nothing exposed until you choose
PORT = env_int("PORT", 8766)
PUBLIC_URL = env("PUBLIC_URL").rstrip("/")   # only used in webhook payloads
ADMIN_EMAIL = env("ADMIN_EMAIL", "admin@localhost").lower()

DATA_DIR = Path(env("DATA_DIR", str(REPO / "data"))).expanduser()
NOTES_DIR = Path(env("NOTES_DIR", str(DATA_DIR / "notes"))).expanduser()
USERS_FILE = Path(env("USERS_FILE", str(DATA_DIR / "users.json"))).expanduser()

MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 200)
MAX_TRANSCRIPT_CHARS = env_int("MAX_TRANSCRIPT_CHARS", 1_000_000)
TOKEN_TTL_HOURS = env_int("TOKEN_TTL_HOURS", 12)
LOGIN_MAX_FAILURES = env_int("LOGIN_MAX_FAILURES", 8)
LOGIN_WINDOW_SEC = env_int("LOGIN_WINDOW_SEC", 900)

# Browsers are not allowed to call this API from another site unless you list
# the site here. The bundled dashboard is same-origin and needs nothing.
CORS_ORIGINS = [o.strip() for o in env("CORS_ORIGINS").split(",") if o.strip()]

WEBHOOK_URL = env("WEBHOOK_URL")
EXPORT_FOLDER = env("EXPORT_FOLDER")

# Set to 1 only if a reverse proxy in front of this server already authenticates
# every request AND passes the original client address in X-Forwarded-For.
TRUST_PROXY = env("TRUST_PROXY") == "1"

# The names this server answers to (the Host header), comma-separated; * = any.
# The desktop app sets 127.0.0.1,localhost, so a web page elsewhere that points
# its own name at this computer (DNS rebinding) is refused.
ALLOWED_HOSTS = [h.strip().lower() for h in env("ALLOWED_HOSTS", "*").split(",") if h.strip()]

# Set by the Notewell desktop app. The server then starts with no AI chosen
# yet - the person picks one on the Settings screen - and a meeting that
# arrives before that fails with a message saying so, transcript kept.
# A server install leaves this off and still refuses to start without an AI.
ALLOW_UNCONFIGURED_AI = env("ALLOW_UNCONFIGURED_AI") == "1"


def load_api_key() -> str:
    """The master key. It is also the dashboard password for the admin account."""
    key = env("MEETING_NOTES_API_KEY")
    if is_placeholder(key):
        raise ConfigError(
            "MEETING_NOTES_API_KEY is missing or still a placeholder.\n"
            "  Generate one and put it in backend/.env:\n"
            '    python -c "import secrets; print(\'mn_\' + secrets.token_hex(24))"')
    if len(key) < 24:
        raise ConfigError("MEETING_NOTES_API_KEY is too short to be safe (need 24+ characters).")
    return key


def validate_for_serving() -> None:
    """Checks that must pass before the server accepts a single request."""
    load_api_key()
    if HOST not in ("127.0.0.1", "localhost", "::1") and not TRUST_PROXY:
        # Not an error - a LAN or VPS install legitimately binds wider - but it
        # must never happen by accident, so say so in the log every start.
        print(f"NOTE: listening on {HOST}:{PORT}, not loopback. Put TLS in front of it "
              f"(docs/INSTALL.md) and keep the port closed to the internet.", file=sys.stderr)
