# Security

## Reporting a problem

Please do **not** open a public issue for a security problem. Use GitHub's **Report a vulnerability** button on the repository's *Security* tab. You will get an answer within a week.

## What Notewell protects

| Area | How |
|---|---|
| **Audio** | Recorded and transcribed on your own computer. Audio files are never uploaded anywhere. |
| **Who can reach the app** | The Windows app listens only on `127.0.0.1` — other computers on your network cannot connect to it. A team server also binds to `127.0.0.1` unless you change `HOST`. |
| **Sign-in** | Always required; there is no "trusted network" switch. Every request needs a login token or an API key. |
| **Passwords** | Stored as salted scrypt hashes, never as text. |
| **Guessing passwords** | Repeated failed sign-ins are blocked for a while. |
| **Keys compared safely** | Constant-time comparison, so a key cannot be guessed one character at a time. |
| **Each person's meetings** | On a team server, a person sees only their own meetings. Only the admin sees everyone's. |
| **Notes shown in the page** | AI output is cleaned (DOMPurify) before it is displayed, so text in a transcript cannot run code in your browser. |
| **Browser libraries** | Bundled in `dashboard/vendor/` — nothing loaded from a CDN. |
| **The app window** | Opens in its own Edge profile (`%USERPROFILE%\Notewell\window`), separate from your normal browsing. |

## What is your job

| Area | Why it matters |
|---|---|
| **Your AI key is stored as plain text** | In `%USERPROFILE%\Notewell\data\settings.json` (Windows app) or `backend/.env` (server). Anyone who can read your user folder can read it. Use a key you can revoke, and revoke it if the computer is lost. |
| **The app's own key is stored as plain text** | In `%USERPROFILE%\Notewell\app.key` and the recorder's `config.json`. It opens every meeting in the app. Do not share those files. |
| **The transcript text goes to the AI you choose** | Read that provider's terms. Some free plans may use your data for training unless you switch it off. For confidential meetings use a local model (Ollama). |
| **Telling people you are recording** | Required by law in many places. |
| **A team server on the internet** | Put it behind HTTPS (see [docs/INSTALL.md](docs/INSTALL.md)) and keep the server updated. |
| **The installer is not code-signed** | Windows will warn you. Download it only from this repository's Releases page. |

## Checks on every change

- Secret scan of the whole history (`scripts/check-secrets.py`) on every push.
- Server tests include sign-in, throttling, per-person access and key handling (`tests/server/`).
- Each release checks the built app starts and answers before the installer is published.
