"""The helper that writes backend/.env. It handles the most sensitive thing in the
project (the API keys), so it is tested for what it must never do."""
import os
import subprocess
import sys
from pathlib import Path

from conftest import BACKEND

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup-env.py"


def run(tmp_path, *args, env=None):
    out = tmp_path / "x" / ".env"
    r = subprocess.run([sys.executable, str(SCRIPT), "--output", str(out), *args], capture_output=True,
                       text=True, env={**os.environ, **(env or {})}, timeout=60, stdin=subprocess.DEVNULL)
    return r, out


def parse(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_it_writes_a_working_config_without_ever_printing_a_key(tmp_path):
    r, out = run(tmp_path, "--provider", "gemini", "--llm-key-env", "MN_TEST_PROVIDER_KEY",
                 env={"MN_TEST_PROVIDER_KEY": "AIza" + "FAKE" * 9})
    assert r.returncode == 0, r.stderr
    cfg = parse(out)
    assert cfg["MEETING_NOTES_API_KEY"].startswith("mn_") and len(cfg["MEETING_NOTES_API_KEY"]) == 51
    assert cfg["LLM_API_KEY"].startswith("AIzaFAKE")
    assert "generativelanguage.googleapis.com" in cfg["LLM_BASE_URL"] and cfg["HOST"] == "127.0.0.1"
    for secret in (cfg["MEETING_NOTES_API_KEY"], cfg["LLM_API_KEY"]):
        assert secret not in r.stdout + r.stderr, "a key was printed"


def test_the_generated_file_really_starts_the_server(tmp_path):
    r, out = run(tmp_path, "--provider", "ollama")
    assert r.returncode == 0, r.stderr
    env = {**os.environ, **parse(out), "DATA_DIR": str(tmp_path / "data")}
    boot = subprocess.run([sys.executable, "-c", "import main; print('booted')"], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=60)
    assert boot.returncode == 0 and "booted" in boot.stdout, boot.stderr


def test_it_never_overwrites_an_existing_file(tmp_path):
    r, out = run(tmp_path, "--provider", "mock")
    before = out.read_text()
    r2, _ = run(tmp_path, "--provider", "mock")
    assert "already exists" in r2.stdout and out.read_text() == before
    r3, _ = run(tmp_path, "--provider", "mock", "--force")
    assert out.read_text() != before                       # --force makes a new key


def test_a_remote_provider_without_a_key_is_refused(tmp_path):
    r, out = run(tmp_path, "--provider", "openai")
    assert r.returncode != 0 and not out.exists()
    r, out = run(tmp_path, "--provider", "openai", "--llm-key-env", "MN_DOES_NOT_EXIST")
    assert r.returncode != 0 and not out.exists()


def test_local_and_mock_providers_need_no_key(tmp_path):
    for provider in ("ollama", "mock"):
        r, out = run(tmp_path / provider, "--provider", provider)
        assert r.returncode == 0 and parse(out)["LLM_API_KEY"] == ""


def test_every_key_is_different(tmp_path):
    keys = {parse(run(tmp_path / str(i), "--provider", "mock")[1])["MEETING_NOTES_API_KEY"] for i in range(5)}
    assert len(keys) == 5
