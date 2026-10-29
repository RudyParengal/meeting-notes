#!/usr/bin/env python3
"""Creates backend/.env for you: a fresh API key, and your choice of AI provider.

    python scripts/setup-env.py                       # asks you
    python scripts/setup-env.py --provider gemini --llm-key-env GEMINI_API_KEY
    python scripts/setup-env.py --provider ollama --model llama3.1      # fully local, no key
    python scripts/setup-env.py --provider mock       # try everything with no key and no cost

Your provider key is read with the typing hidden, or from an environment variable
you name (--llm-key-env) - never from the command line, where it would end up in
your shell history. Neither key is ever printed. The file is only created if it
does not exist (use --force to replace it).

Standard library only.
"""
import argparse
import getpass
import os
import secrets
import stat
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / "backend" / ".env"

# name -> (base url, default model, needs a key, note). Model names change; if one
# is rejected, check your provider's current list and edit LLM_MODEL in backend/.env.
PROVIDERS = {
    "gemini":   ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash", True,
                 "Google Gemini. Cheap and fast. Key: https://aistudio.google.com/apikey"),
    "openai":   ("https://api.openai.com/v1", "gpt-4o-mini", True,
                 "OpenAI. Key: https://platform.openai.com/api-keys"),
    "groq":     ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", True,
                 "Groq. Very fast. Key: https://console.groq.com/keys"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", True,
                 "DeepSeek. Key: https://platform.deepseek.com/api_keys"),
    "ollama":   ("http://localhost:11434/v1", "llama3.1", False,
                 "Ollama, running on this computer. Nothing leaves your machine. https://ollama.com"),
    "mock":     ("http://127.0.0.1:9099/v1", "mock", False,
                 "A fake AI for trying the system with no key. Run: python scripts/mock_llm.py"),
    "custom":   ("", "", True, "Any other OpenAI-compatible service."),
}


def has_a_person_typing() -> bool:
    """True only if a real keyboard is attached. On Windows the empty "NUL" device
    claims to be a terminal, so a script run with no input would wait forever."""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    if os.name == "nt":
        import ctypes
        import msvcrt
        mode = ctypes.c_uint32()
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(sys.stdin.fileno()))
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    return input(f"{prompt}{shown}: ").strip() or default


def main() -> int:
    ap = argparse.ArgumentParser(description="Create backend/.env")
    ap.add_argument("--provider", choices=sorted(PROVIDERS))
    ap.add_argument("--model", help="override the provider's default model")
    ap.add_argument("--base-url", help="for --provider custom")
    ap.add_argument("--llm-key-env", metavar="VAR", help="read the provider key from this environment variable")
    ap.add_argument("--admin-email", default="admin@localhost")
    ap.add_argument("--force", action="store_true", help="replace an existing backend/.env")
    ap.add_argument("--output", type=Path, default=ENV_PATH, help="write somewhere else (default: backend/.env)")
    args = ap.parse_args()
    out = args.output

    if out.exists() and not args.force:
        print(f"{out} already exists - not touching it.\n"
              f"Edit it by hand, or run again with --force to replace it (the old API key will stop working).")
        return 0

    provider = args.provider
    if not provider:
        print("Which AI provider will write your summaries?\n")
        names = list(PROVIDERS)
        for i, n in enumerate(names, 1):
            print(f"  {i}. {n:9} {PROVIDERS[n][3]}")
        choice = ask("\nNumber or name", "1")
        provider = names[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(names) else choice
        if provider not in PROVIDERS:
            print(f"Unknown provider {provider!r}.")
            return 2

    base, default_model, needs_key, _ = PROVIDERS[provider]
    base = args.base_url or base
    model = args.model or default_model
    if provider == "custom":
        base = base or ask("Base URL (ends in /v1)")
        model = model or ask("Model name")
    if not base or not model:
        print("A base URL and a model are required.")
        return 2

    llm_key = ""
    if needs_key:
        if args.llm_key_env:
            llm_key: str = os.environ.get(args.llm_key_env, "").strip()
            if not llm_key:
                print(f"Environment variable {args.llm_key_env} is empty or not set.")
                return 2
        elif has_a_person_typing():
            llm_key: str = getpass.getpass(f"Paste your {provider} API key (hidden): ").strip()
        if not llm_key:
            print("No provider key given. Re-run with a key, or choose 'ollama' (local) or 'mock' (no key).")
            return 2

    master = "mn_" + secrets.token_hex(24)
    text = f"""# Created by scripts/setup-env.py. Never commit or share this file.
# Every setting is explained in docs/CONFIGURATION.md and backend/.env.example.

# The key the recorder uses, and the admin's dashboard password.
MEETING_NOTES_API_KEY={master}
ADMIN_EMAIL={args.admin_email}

# AI provider: {provider}
LLM_API_KEY={llm_key}
LLM_BASE_URL={base}
LLM_MODEL={model}

# Listen on this computer only. See docs/INSTALL.md before changing this.
HOST=127.0.0.1
PORT=8766
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    # Create with owner-only permissions from the first byte (no moment where it is world-readable).
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    try:
        os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print(f"\nCreated {out}")
    print(f"  provider : {provider}  ({model})")
    print(f"  API key  : generated and saved in that file. It is NOT shown here; the installer reads it for you.")
    print("\nNext:  python backend/main.py     then     python scripts/doctor.py --e2e")
    return 0


if __name__ == "__main__":
    sys.exit(main())
