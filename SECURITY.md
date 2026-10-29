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
| **The app window** | Notewell's own window, not a browser. It opens only pages from this computer's own server; any other link opens in your normal browser instead. One window per Windows user. |
| **Keys kept on disk** | The app's own key (`app.key`), your AI key (`data\settings.json`) and a linked server's key are locked to your Windows account (Windows DPAPI). Copied to another PC or read by another user, the files are useless. The recorder's `config.json` holds no key at all. |
| **Requests from web pages** | The app answers only requests addressed to `127.0.0.1` or `localhost`, so a website cannot reach it by pointing its own name at your computer. Uploads must say their size and stay under the limit. |
| **Releases** | Built by GitHub from a tagged commit, with every build step pinned. Each release lists the installer's SHA-256 and carries a signed build record (`gh attestation verify`). |

## What is your job

| Area | Why it matters |
|---|---|
| **Your Windows account** | The locked keys open for anything running as you. Malware on your account can read them, and your meetings. Keep Windows updated and do not run programs you do not trust. Use an AI key you can revoke, and revoke it if the computer is lost. |
| **A team server's key** | Kept as plain text in `backend/.env` on the server. Only the account that runs the server should be able to read that file. |
| **Your meetings are not encrypted on disk** | They are ordinary files in `%USERPROFILE%\Notewell\data`. Turn on BitLocker (or Device Encryption) so a lost laptop does not give them away. |
| **The transcript text goes to the AI you choose** | Read that provider's terms. Some free plans may use your data for training unless you switch it off. For confidential meetings use a local model (Ollama). |
| **Telling people you are recording** | Required by law in many places. |
| **A team server on the internet** | Put it behind HTTPS (see [docs/INSTALL.md](docs/INSTALL.md)) and keep the server updated. |
| **The installer is not code-signed** | Windows will warn you. Download it only from this repository's Releases page. |

## Checks on every change

- Secret scan of the whole history (`scripts/check-secrets.py`) on every push.
- Server tests include sign-in, throttling, per-person access and key handling (`tests/server/`).
- Each release checks the built app starts and answers before the installer is published.
