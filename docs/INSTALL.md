# Installing Meeting Notes

Two programs work together:

| Program | What it does | Where it runs |
|---|---|---|
| **Server** (`backend/`) | Receives the text of a meeting, asks the AI for a summary and action items, stores the notes, shows the dashboard | Any computer: Windows, Mac or Linux |
| **Recorder** (`windows-recorder/`) | Sits in the system tray, captures the call, turns speech into text on your computer, sends the text to the server | Windows 10 / 11 only |

Pick the setup that matches you:

| Setup | Best for | Time |
|---|---|---|
| **A. Everything on one Windows laptop** | Just you, getting started | 10 min |
| **B. Server in Docker** | Anyone comfortable with Docker | 10 min |
| **C. Server on a Linux machine with HTTPS** | A team, or notes you want from any device | 30 min |

You do not need a domain name, a VPS or any particular company's service for **A** or **B**.

---

## Before you start

- **Python 3.10 or newer.** Check with `python --version`. Windows: install from [python.org](https://www.python.org/downloads/) and tick **Add python.exe to PATH**.
- **Git** (or download the ZIP from GitHub).
- **An AI provider key** — or choose a local model or the built-in fake AI. See [CONFIGURATION.md](CONFIGURATION.md#choosing-an-ai-provider).

---

## A. Everything on one Windows laptop

### 1. Get the code

```powershell
git clone https://github.com/RudyParengal/meeting-notes.git
cd meeting-notes
```

### 2. Install the server

```powershell
python -m venv venv
venv\Scripts\pip install -r backend\requirements.txt
```

### 3. Create your settings

```powershell
venv\Scripts\python scripts\setup-env.py
```

It asks which AI provider you use and creates `backend\.env`. It generates a strong master key for you. **Neither key is ever printed.** Your AI key is typed with the screen hidden.

Prefer to skip the questions?

```powershell
$env:GEMINI_API_KEY = "paste-your-key"      # typed once in this window only
venv\Scripts\python scripts\setup-env.py --provider gemini --llm-key-env GEMINI_API_KEY
```

Other choices: `--provider openai | groq | deepseek | ollama | mock | custom`. No key at all? Use `mock` (a fake AI) or `ollama` (a model on your own computer).

### 4. Start the server and check it

```powershell
venv\Scripts\python backend\main.py
```

Leave that window open. In a **second** window:

```powershell
venv\Scripts\python scripts\doctor.py --e2e
```

`--e2e` pushes a short test meeting all the way through your AI provider and back, so a `PASS` means it really works — not just that a process started. If you used `mock`, first run `venv\Scripts\python scripts\mock_llm.py` in a third window.

Open **http://127.0.0.1:8766**. Log in with email `admin@localhost` and, as the password, the value of `MEETING_NOTES_API_KEY` in `backend\.env`.

### 5. Start the server automatically (optional)

To keep the server running whenever you are logged in, create a shortcut in `shell:startup` that runs:

```
C:\path\to\meeting-notes\venv\Scripts\pythonw.exe C:\path\to\meeting-notes\backend\main.py
```

(`pythonw` runs without a console window.) Logs go to the window you started it from, so for an always-on setup prefer option B or C.

### 6. Install the recorder

```powershell
powershell -ExecutionPolicy Bypass -File windows-recorder\install.ps1 -SpeakerSeparation
```

What this does:

1. Creates a private Python environment inside `windows-recorder\venv` (nothing is installed system-wide).
2. Installs the dependencies, and with `-SpeakerSeparation` downloads two small models (36 MB, checksum-verified).
3. Writes `%USERPROFILE%\MeetingRecorder\config.json`. **Because the server is on the same computer, the installer reads your key from `backend\.env` itself — you paste nothing.**
4. Adds a Startup shortcut so the recorder starts when you log in.
5. Starts it.

The first meeting downloads the speech model (about 1.5 GB, once).

Look for the **blue circle in the tray** (bottom-right). On Windows 11 new icons hide behind the **^** arrow — drag the circle out to keep it visible.

#### Installer options

| Option | Effect |
|---|---|
| `-SpeakerSeparation` | Split the other people on a call into Speaker 1 / 2 / 3 |
| `-LiveTranscript` | Show a small always-on-top window with the text as people talk |
| `-AutoDetect` | Start recording automatically when Zoom / Teams / Webex opens. **Off by default** — see the recording-law note below |
| `-ApiUrl <url>` | Server address (default `http://127.0.0.1:8766`) |
| `-ApiKey <key>` | Server key. Better: set the `MN_API_KEY` environment variable first, so it never appears in a command line |
| `-NoAutoStart` | Do not start at login |
| `-NoStart` | Install but do not launch |
| `-NonInteractive` | Never ask questions (for scripts and AI agents) |

> **Recording laws.** In many countries and US states everyone on a call must be told it is being recorded. Auto-detect starts recording without you clicking anything, so only enable it if everyone you meet knows.

### 7. Check the recorder

```powershell
venv\Scripts\python scripts\doctor.py --recorder
```

---

## B. Server in Docker

```bash
git clone https://github.com/RudyParengal/meeting-notes.git
cd meeting-notes
python scripts/setup-env.py                 # creates backend/.env (any Python 3.10+; no packages needed)
docker compose up -d --build
python scripts/doctor.py --server-only --e2e
```

- The container runs as a non-root user with a read-only filesystem and no extra privileges.
- Notes live in a Docker volume named `notes-data`. Back it up (see below).
- The port is published on **this computer only** (`127.0.0.1:8766`). To reach it from elsewhere use option C's HTTPS proxy — do not change that line to `0.0.0.0`.
- If `backend/.env` is wrong the container exits with code 78 and a message saying exactly what to fix (`docker logs meeting-notes`). It will not loop.

---

## C. Server on a Linux machine, with HTTPS

Use this when several people share one server, or you want the dashboard on your phone.

### 1. Install

```bash
sudo useradd --system --home /opt/meeting-notes --shell /usr/sbin/nologin meetingnotes
sudo git clone https://github.com/RudyParengal/meeting-notes.git /opt/meeting-notes
cd /opt/meeting-notes
sudo python3 -m venv venv
sudo venv/bin/pip install -r backend/requirements.txt
sudo venv/bin/python scripts/setup-env.py          # creates backend/.env
sudo chown meetingnotes:meetingnotes backend/.env && sudo chmod 600 backend/.env
```

### 2. Run it as a service

```bash
sudo cp deploy/meeting-notes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meeting-notes
sudo venv/bin/python scripts/doctor.py --server-only --e2e
```

The service is sandboxed (it can only write its own data folder, `/var/lib/meeting-notes`) and does not restart in a loop if your configuration is wrong — it stops and the reason is in `journalctl -u meeting-notes -n 30`.

### 3. Put HTTPS in front

**Never expose port 8766 to the internet directly.** Passwords and keys would travel unencrypted. Use a reverse proxy:

- **Caddy** (easiest — gets and renews the certificate for you): edit the hostname in [deploy/Caddyfile.example](../deploy/Caddyfile.example), copy it to `/etc/caddy/Caddyfile`, `sudo systemctl reload caddy`.
- **nginx**: see [deploy/nginx.conf.example](../deploy/nginx.conf.example).

Open only ports 80 and 443 in the firewall. Then in `backend/.env` set:

```
TRUST_PROXY=1
PUBLIC_URL=https://notes.example.com
HOST=127.0.0.1
```

`TRUST_PROXY=1` lets the login throttle see each visitor's real address. Set it **only** when a proxy that you control is in front — otherwise anyone could pretend to be someone else.

### 4. Point the Windows recorder at it

On each laptop:

```powershell
$env:MN_API_KEY = "the-server-key"
powershell -ExecutionPolicy Bypass -File windows-recorder\install.ps1 -ApiUrl https://notes.example.com -NonInteractive -SpeakerSeparation
```

Better still, give each person their own key: log in to the dashboard as admin → **Admin** → add the person → copy the key shown **once**. A personal key can be replaced or revoked without disturbing anyone else.

The recorder **refuses** plain `http://` to anything other than your own computer, so a key can never be sent unencrypted by accident.

---

## Updating

```bash
cd meeting-notes
git pull
venv/Scripts/pip install -r backend/requirements.txt       # Mac/Linux: venv/bin/pip
# restart the server, then:
python scripts/doctor.py --e2e
```

For the recorder, re-run `windows-recorder\install.ps1` — it keeps your existing `config.json`.

## Backing up

Everything the server knows is in its data folder (`./data` by default, `/var/lib/meeting-notes` under systemd, the `notes-data` volume in Docker): the notes and `users.json`. Copy that folder. Your settings are in `backend/.env` — keep a copy somewhere private (it contains your keys).

Recorder data is in `%USERPROFILE%\MeetingRecorder`: its settings, its logs, and the audio of any meeting the server has not yet confirmed. Once a note is safely stored, the backup audio moves to `%USERPROFILE%\MeetingArchive` (not deleted). That audio is a recording of real people's voices — protect and delete it as you would any recording.

## Uninstalling

```powershell
powershell -ExecutionPolicy Bypass -File windows-recorder\uninstall.ps1
```

This removes the start-at-login entry and stops the recorder (it will not interrupt a meeting being recorded). **Your meetings and settings are not deleted.** Remove `%USERPROFILE%\MeetingRecorder` yourself if you want them gone.

Something not working? → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
