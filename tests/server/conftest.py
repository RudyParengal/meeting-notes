"""Shared fixtures for the server tests.

The environment is set BEFORE the app is imported, because the server reads its
configuration at import time and refuses to start without a valid key. The AI
provider is replaced by a fake, so no test needs a network or a real key.
"""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
MASTER_KEY = "mn_" + "a1" * 24                      # 51 chars, obviously not a real key
_DATA = tempfile.mkdtemp(prefix="mn-test-")

os.environ.update({
    "MEETING_NOTES_API_KEY": MASTER_KEY,
    "LLM_API_KEY": "test-not-a-real-key",
    "LLM_BASE_URL": "https://llm.invalid/v1",
    "CLASSIFY_MODEL": "m-classify",
    "SUMMARY_MODEL": "m-summary",
    "ACTION_MODEL": "m-action",
    "DATA_DIR": _DATA,
    "ADMIN_EMAIL": "admin@example.test",
    "LOGIN_MAX_FAILURES": "4",
})
for leftover in ("HOST", "TRUST_PROXY", "CORS_ORIGINS", "WEBHOOK_URL", "EXPORT_FOLDER"):
    os.environ.pop(leftover, None)

sys.path.insert(0, str(BACKEND))


def reply(text, finish_reason="stop"):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=text), finish_reason=finish_reason)])


class FakeLLM:
    """Stands in for one provider. `fail` makes it raise, like an outage or a spent quota."""

    def __init__(self):
        self.calls = []
        self.fail = set()                    # model names that should raise
        self.empty = set()                   # models that "think" until the token limit, then say nothing
        self.classify_as = "sales"
        self.chat = types.SimpleNamespace(completions=self)

    async def create(self, **kw):
        self.calls.append(kw)
        model = kw["model"]
        if model in self.fail:
            raise RuntimeError(f"429 insufficient_quota ({model})")
        if model in self.empty:
            return reply(None, finish_reason="length")
        if model == "m-classify":
            return reply(self.classify_as)
        if model == "m-summary":
            return reply("## Meeting Title\nQuarterly Planning\n\n## Summary\nThey planned.")
        return reply("- [ ] Send the proposal (Alex, Friday)")


@pytest.fixture(scope="session")
def app_module():
    import main
    return main


@pytest.fixture()
def llm(app_module):
    fake = FakeLLM()
    for stage in app_module.pipeline.stages().values():
        stage.client = fake
    return fake


@pytest.fixture()
def client(app_module, llm):
    from fastapi.testclient import TestClient
    import users
    # A clean slate for every test: no notes, no trash, no users, nobody logged in.
    import shutil
    for d in (app_module.storage.NOTES_DIR, app_module.storage.TRASH_DIR):
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()
    shutil.rmtree(app_module.storage.TRASH_DIR, ignore_errors=True)
    app_module.storage.init()
    if app_module.config.USERS_FILE.exists():
        app_module.config.USERS_FILE.unlink()
    app_module.tokens._tokens.clear()
    app_module.throttle._fails.clear()
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def admin(client):
    return {"X-API-Key": MASTER_KEY}


SAMPLE_PHRASE = "correct-horse-battery"     # test login, not a real credential


def make_user(client, admin, email="alice@example.test", phrase=SAMPLE_PHRASE):
    r = client.post("/admin/users", headers=admin,
                    json={"email": email, "password": phrase, "name": email.split("@")[0]})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def no_waiting_between_retries(app_module, monkeypatch):
    """Retries are real in production (seconds apart); in tests they are instant."""
    monkeypatch.setattr(app_module.pipeline, "RETRY_DELAYS", (0.0, 0.0, 0.0))
