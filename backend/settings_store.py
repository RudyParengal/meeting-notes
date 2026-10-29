"""AI settings chosen in the app, saved to DATA_DIR/settings.json.

A server install sets LLM_* in backend/.env and never touches this file. The
desktop app has no .env: the person picks a provider on the Settings screen
and it is stored here. Environment variables always win, so adding this file
cannot change how an existing server behaves.

On Windows the API key is saved locked to the Windows user (sealed.py), so the
file is useless if copied off this computer. Elsewhere it is plain text,
exactly as backend/.env is. It is never sent anywhere except the provider.
"""
import json
import logging
import os
import tempfile
from pathlib import Path

import config
import sealed

SETTINGS_FILE = config.DATA_DIR / "settings.json"

# What the Settings screen offers. Every one speaks the OpenAI chat format, so
# the pipeline needs no per-vendor code. `model` is a starting suggestion the
# person can change; "" means they must type one.
PRESETS = [
    {"id": "mistral", "name": "Mistral (free plan)", "base_url": "https://api.mistral.ai/v1",
     "model": "ministral-14b-latest", "needs_key": True, "free": True,
     "key_url": "https://console.mistral.ai/api-keys",
     "note": "Free plan, handles long meetings. Mistral may use free-plan requests to "
             "improve its models unless you switch that off in Admin > Privacy."},
    {"id": "groq", "name": "Groq (free, short meetings)", "base_url": "https://api.groq.com/openai/v1",
     "model": "openai/gpt-oss-120b", "needs_key": True, "free": True,
     "key_url": "https://console.groq.com/keys",
     "note": "Very fast and free, but the free plan allows about 8,000 tokens a minute: "
             "meetings over roughly 20 minutes can be refused."},
    {"id": "ollama", "name": "Ollama (on this computer, private)", "base_url": "http://localhost:11434/v1",
     "model": "llama3.1:8b", "needs_key": False, "free": True,
     "key_url": "https://ollama.com/download",
     "note": "Nothing leaves your computer. Install Ollama, run `ollama pull llama3.1:8b`. "
             "Slow without a graphics card."},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o-mini", "needs_key": True, "free": False,
     "key_url": "https://platform.openai.com/api-keys",
     "note": "Pay per use. A ChatGPT Plus subscription does not include API access."},
    {"id": "anthropic", "name": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1/",
     "model": "claude-haiku-5-5", "needs_key": True, "free": False,
     "key_url": "https://console.anthropic.com/settings/keys",
     "note": "Pay per use. A Claude Pro subscription does not include API access."},
    {"id": "xai", "name": "xAI (Grok)", "base_url": "https://api.x.ai/v1",
     "model": "grok-4.7", "needs_key": True, "free": False,
     "key_url": "https://console.x.ai",
     "note": "Pay per use. A SuperGrok subscription does not include API access."},
    {"id": "custom", "name": "Other (any OpenAI-compatible service)", "base_url": "",
     "model": "", "needs_key": True, "free": False, "key_url": "",
     "note": "LM Studio, OpenRouter, Together, DeepSeek, a company gateway..."},
]


def env_controls_ai() -> bool:
    """True when the server's environment already chooses the AI. The app's
    Settings screen then shows the choice but cannot change it."""
    return any(os.environ.get(k, "").strip() for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))


def load() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError:
        # A damaged file must not stop the server; it behaves as "not set up"
        # and the next save replaces it.
        return {}


def ai() -> dict:
    a = load().get("ai")
    if not isinstance(a, dict):
        return {}
    a = dict(a)
    try:
        a["api_key"] = sealed.unseal(a.get("api_key", ""))
    except (OSError, ValueError):
        # Saved by another Windows user or PC: behave as "no key yet", and the
        # Settings screen asks for one.
        logging.getLogger("settings").warning("the saved AI key cannot be unlocked here")
        a["api_key"] = ""
    return a


def save_ai(provider: str, base_url: str, model: str, api_key: str) -> None:
    data = load()
    data["ai"] = {"provider": provider, "base_url": base_url, "model": model,
                  "api_key": sealed.seal(api_key)}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(SETTINGS_FILE.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def key_hint(key: str) -> str:
    """Enough to recognise which key is saved, never enough to use it."""
    key = key or ""
    return ("..." + key[-4:]) if len(key) >= 12 else ("set" if key else "")
