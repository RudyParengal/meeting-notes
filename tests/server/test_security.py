"""Security properties. Each test tries what an attacker would try and checks it fails.

If one of these goes red, do not "fix the test": it is describing a way in.
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import BACKEND, MASTER_KEY, make_user

SID = "0123456789abcdef0123456789abcdef"


# ------------------------------------------------------------ nothing open ---
PROTECTED = [
    ("get", "/sessions"), ("get", "/templates"), ("get", "/trash"), ("get", "/me"),
    ("get", "/health/deep"), ("get", "/admin/users"), ("get", f"/results/{SID}"),
    ("delete", f"/results/{SID}"), ("post", "/transcribe"), ("post", "/admin/users"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_no_credentials_is_refused(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code in (401, 422), f"{method.upper()} {path} answered {r.status_code} without credentials"
    # 422 is acceptable only for a body-less POST that fails validation; it must not reveal data
    assert "transcript" not in r.text or r.status_code == 422


def test_wrong_api_key_is_refused(client):
    assert client.get("/sessions", headers={"X-API-Key": "mn_" + "00" * 24}).status_code == 401


def test_there_is_no_trust_everyone_switch(client, monkeypatch):
    """The original shipped SINGLE_SIGNON=1 by default, which skipped auth entirely."""
    monkeypatch.setenv("SINGLE_SIGNON", "1")
    assert client.get("/sessions").status_code == 401


def test_health_is_public_but_says_nothing_sensitive(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "ts"}


def test_openapi_schema_is_not_published(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# ----------------------------------------------------- refuse to start unsafe ---
@pytest.mark.parametrize("bad", ["", "changeme", "PASTE_YOUR_KEY", "short", "your-api-key-here"])
def test_server_will_not_start_with_a_missing_or_weak_key(bad):
    env = {**os.environ, "MEETING_NOTES_API_KEY": bad}
    out = subprocess.run([sys.executable, "-c", "import main"], cwd=BACKEND, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode != 0
    assert "MEETING_NOTES_API_KEY" in out.stderr + out.stdout


def test_server_will_not_start_without_an_llm_key_for_a_remote_provider():
    env = {**os.environ, "LLM_API_KEY": "", "LLM_BASE_URL": "https://api.openai.com/v1"}
    out = subprocess.run([sys.executable, "-c", "import main"], cwd=BACKEND, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode != 0
    assert "LLM_API_KEY" in out.stderr + out.stdout


def test_a_local_model_needs_no_key():
    env = {**os.environ, "LLM_API_KEY": "", "LLM_BASE_URL": "http://localhost:11434/v1"}
    out = subprocess.run([sys.executable, "-c", "import main; print('started')"], cwd=BACKEND, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "started" in out.stdout


# --------------------------------------------------------------- passwords ---
def test_admin_logs_in_with_the_master_key_and_nothing_else(client):
    assert client.post("/login", json={"email": "admin@example.test", "password": MASTER_KEY}).status_code == 200
    assert client.post("/login", json={"email": "admin@example.test", "password": "wrong"}).status_code == 401


def test_login_does_not_reveal_whether_an_account_exists(client, admin):
    make_user(client, admin)
    a = client.post("/login", json={"email": "alice@example.test", "password": "wrong-password"})
    b = client.post("/login", json={"email": "nobody@example.test", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_password_guessing_is_throttled(client, admin):
    make_user(client, admin)
    codes = [client.post("/login", json={"email": "alice@example.test", "password": f"guess-{i}"}).status_code
             for i in range(8)]
    assert 429 in codes, f"never throttled: {codes}"
    # ...and the real password is also refused while locked out
    ok = client.post("/login", json={"email": "alice@example.test", "password": "correct-horse-battery"})
    assert ok.status_code == 429
    assert "Retry-After" in ok.headers


def test_stored_passwords_are_salted_scrypt_not_plain_sha256(client, admin):
    make_user(client, admin)
    import config
    stored = json.loads(config.USERS_FILE.read_text())["users"][0]["password_hash"]
    assert stored.startswith("scrypt$")
    import hashlib
    assert stored != hashlib.sha256(b"correct-horse-battery").hexdigest()


# ---------------------------------------------------------------- api keys ---
def test_no_api_key_is_ever_written_to_disk_or_listed(client, admin):
    created = make_user(client, admin)
    key = created["api_key"]
    import config
    on_disk = config.USERS_FILE.read_text()
    assert key not in on_disk, "the user's API key is stored in the clear"
    listing = client.get("/admin/users", headers=admin)
    assert key not in listing.text
    for u in listing.json()["users"]:
        assert "api_key" not in u and "api_key_hash" not in u and "password_hash" not in u
        assert u["has_api_key"] is True          # a yes/no flag is fine; the key itself is not
    assert client.get("/sessions", headers={"X-API-Key": key}).status_code == 200


def test_rotating_a_key_kills_the_old_one(client, admin):
    created = make_user(client, admin)
    old = created["api_key"]
    new = client.post(f"/admin/users/{created['email']}/rotate-key", headers=admin).json()["api_key"]
    assert client.get("/sessions", headers={"X-API-Key": old}).status_code == 401
    assert client.get("/sessions", headers={"X-API-Key": new}).status_code == 200


def test_old_installs_are_upgraded_in_place(client, app_module):
    """users.json from v1 held the key itself and an unsalted SHA-256 password."""
    import hashlib, config, users
    legacy = {"users": [{"email": "old@example.test", "name": "Old", "role": "member",
                         "password_hash": hashlib.sha256(b"old-password-123").hexdigest(),
                         "api_key": "mn_legacyplaintextkey0123456789abcdef0123456789ab"}]}  # secret-scan: allow (fake, for the migration test)
    config.USERS_FILE.write_text(json.dumps(legacy))
    assert users.migrate() == 1
    text = config.USERS_FILE.read_text()
    assert "mn_legacyplaintextkey" not in text, "plaintext key survived the upgrade"
    # the old key still works (it is now compared by hash)...
    assert client.get("/sessions", headers={"X-API-Key": legacy["users"][0]["api_key"]}).status_code == 200
    # ...and the old password still logs in, then is re-saved as scrypt
    assert client.post("/login", json={"email": "old@example.test", "password": "old-password-123"}).status_code == 200
    assert json.loads(config.USERS_FILE.read_text())["users"][0]["password_hash"].startswith("scrypt$")


def test_a_corrupt_users_file_is_not_treated_as_empty(client):
    """Otherwise the next 'create user' would overwrite every account with a blank list."""
    import config
    config.USERS_FILE.write_text("{not json")
    import users
    with pytest.raises(users.UsersFileCorrupt):
        users.all_users()
    assert config.USERS_FILE.read_text() == "{not json"          # untouched


def test_a_damaged_users_file_does_not_crash_a_running_server(client, admin):
    import config
    config.USERS_FILE.write_text("{not json")
    # the master key still works for everything that does not need the list...
    assert client.get("/sessions", headers=admin).status_code == 200
    # ...and anything that does returns a clear 503 instead of killing the process
    r = client.get("/admin/users", headers=admin)
    assert r.status_code == 503 and "damaged" in r.json()["detail"]
    assert client.get("/health").status_code == 200


def test_config_errors_exit_with_78_so_service_managers_do_not_crash_loop():
    out = subprocess.run([sys.executable, "-c", "import main"], cwd=BACKEND, capture_output=True, text=True,
                         env={**os.environ, "MEETING_NOTES_API_KEY": ""}, timeout=60)
    assert out.returncode == 78, f"exit code {out.returncode}"


# ------------------------------------------------------------- id handling ---
@pytest.mark.parametrize("bad", ["*", "????????????????????????????????", "../../etc/passwd",
                                 "0123456789abcdef", "ZZZZ456789abcdef0123456789abcdef", "%2A"])
def test_session_ids_must_be_exactly_32_hex_characters(client, admin, bad):
    """The original passed the raw id into glob(), so an id of * matched every file."""
    for method in ("get", "delete"):
        r = getattr(client, method)(f"/results/{bad}", headers=admin)
        assert r.status_code in (404, 422), f"{method} /results/{bad} -> {r.status_code}"


def test_a_wildcard_id_cannot_trash_other_peoples_notes(client, admin, llm):
    alice = make_user(client, admin, "alice@example.test")
    client.post("/transcribe", headers={"X-API-Key": alice["api_key"]},
                json={"transcript": "x" * 50, "duration_sec": 60})
    r = client.delete("/results/*", headers=admin)
    assert r.status_code in (404, 422)
    assert len(client.get("/sessions?all=true", headers=admin).json()["sessions"]) == 1


def test_a_bad_session_id_in_the_body_is_refused(client, admin):
    r = client.post("/transcribe", headers=admin,
                    json={"transcript": "x" * 50, "duration_sec": 1, "session_id": "../evil"})
    assert r.status_code == 422


# --------------------------------------------------------------- isolation ---
def test_people_cannot_read_or_delete_each_others_meetings(client, admin, llm):
    alice = make_user(client, admin, "alice@example.test")
    bob = make_user(client, admin, "bob@example.test")
    a, b = {"X-API-Key": alice["api_key"]}, {"X-API-Key": bob["api_key"]}
    sid = client.post("/transcribe", headers=a, json={"transcript": "secret " * 20, "duration_sec": 60}).json()["session_id"]
    assert client.get(f"/results/{sid}", headers=a).status_code == 200
    for method, path in [("get", f"/results/{sid}"), ("get", f"/results/{sid}/markdown"),
                         ("get", f"/results/{sid}/download"), ("delete", f"/results/{sid}"),
                         ("post", f"/results/{sid}/reprocess?meeting_type=sales")]:
        assert getattr(client, method)(path, headers=b).status_code == 403, f"{method} {path} leaked"
    assert client.get("/sessions", headers=b).json()["sessions"] == []
    assert len(client.get("/sessions?all=true", headers=admin).json()["sessions"]) == 1
    assert client.get("/sessions?all=true", headers=b).json()["sessions"] == []   # ?all is admin-only


def test_a_member_cannot_use_admin_endpoints(client, admin):
    bob = make_user(client, admin, "bob@example.test")
    b = {"X-API-Key": bob["api_key"]}
    assert client.get("/admin/users", headers=b).status_code == 403
    assert client.post("/admin/users", headers=b, json={"email": "x@example.test", "password": "long-enough-pw"}).status_code == 403


def test_a_user_cannot_overwrite_someone_elses_session_by_reusing_the_id(client, admin, llm):
    alice = make_user(client, admin, "alice@example.test")
    bob = make_user(client, admin, "bob@example.test")
    sid = client.post("/transcribe", headers={"X-API-Key": alice["api_key"]},
                      json={"transcript": "alice " * 20, "duration_sec": 60}).json()["session_id"]
    r = client.post("/transcribe", headers={"X-API-Key": bob["api_key"]},
                    json={"transcript": "bob " * 20, "duration_sec": 60, "session_id": sid})
    assert r.status_code == 403


def test_deleting_a_user_signs_them_out(client, admin):
    make_user(client, admin, "carol@example.test")
    token = client.post("/login", json={"email": "carol@example.test", "password": "correct-horse-battery"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/me", headers=h).status_code == 200
    client.delete("/admin/users/carol@example.test", headers=admin)
    assert client.get("/me", headers=h).status_code == 401


# ------------------------------------------------------------ browser side ---
def test_dashboard_has_a_strict_content_security_policy(client):
    r = client.get("/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-eval" not in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp, "inline scripts must stay blocked"
    assert "frame-ancestors 'none'" in csp


def test_dashboard_loads_nothing_from_the_internet(client):
    """No CDN: it works offline, and a compromised CDN cannot inject code."""
    import re
    html = client.get("/").text
    external = re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', html)
    assert external == [], f"dashboard pulls from the internet: {external}"
    for name in ("app.js", "style.css"):
        assert client.get(f"/static/{name}").status_code == 200


def test_responses_are_not_cacheable_and_not_sniffable(client, admin):
    r = client.get("/sessions", headers=admin)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_no_cors_by_default(client, admin):
    r = client.get("/sessions", headers={**admin, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# -------------------------------------------------------------- input limits ---
def test_oversized_transcripts_are_refused(client, admin, app_module):
    big = "x" * (app_module.config.MAX_TRANSCRIPT_CHARS + 1)
    assert client.post("/transcribe", headers=admin, json={"transcript": big, "duration_sec": 1}).status_code == 422


def test_too_short_transcripts_are_refused(client, admin):
    assert client.post("/transcribe", headers=admin, json={"transcript": "hi", "duration_sec": 1}).status_code == 422


def test_unsupported_audio_types_are_refused(client, admin):
    r = client.post("/upload-audio", headers=admin, files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code in (400, 501)


def test_an_oversized_upload_is_refused_before_it_is_read(client, admin, app_module, monkeypatch):
    monkeypatch.setattr(app_module.config, "MAX_UPLOAD_MB", 0)      # limit + margin = 1 MB
    r = client.post("/upload-audio", files={"file": ("call.wav", b"\0" * (2 * 1024 * 1024), "audio/wav")})
    assert r.status_code == 413                                   # even before the key is checked
    monkeypatch.setattr(app_module.config, "MAX_UPLOAD_MB", 200)
    r = client.post("/upload-audio", headers=admin, files={"file": ("call.wav", b"RIFF", "audio/wav")})
    assert r.status_code != 413


def test_only_the_named_hosts_are_answered(client, admin, app_module, monkeypatch):
    """DNS rebinding: a web page whose own name now points at this computer."""
    monkeypatch.setattr(app_module.config, "ALLOWED_HOSTS", ["127.0.0.1", "localhost"])
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/sessions", headers={**admin, "Host": "evil.example:8767"}).status_code == 400
    assert client.get("/health", headers={"Host": "127.0.0.1:8767"}).status_code == 200
    assert client.get("/health", headers={"Host": "localhost"}).status_code == 200


def test_pdf_engine_cannot_be_made_to_fetch_urls(app_module):
    """A note is built from what people said on a call; a link in it must never be fetched."""
    with pytest.raises(ValueError):
        app_module._deny_fetch("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError):
        app_module._deny_fetch("file:///etc/passwd")
