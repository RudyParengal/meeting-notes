# Meeting Notes

**A self-hosted AI note-taker.** It records your calls on Windows, turns them into text on your own computer, and writes a summary and a list of action items. You own the server, the data and the AI account. No subscription, no third-party app, no sign-up.

- **Your audio never leaves your laptop.** Only the written transcript goes to the AI that writes the summary — and if you run a local model (Ollama), not even that.
- **Works with any AI provider** that speaks the OpenAI format: OpenAI, Google Gemini, Groq, DeepSeek, Ollama (local), LM Studio and more. Change it with one line.
- **Survives crashes.** Audio is saved to disk while you talk. If the laptop dies mid-call, the recording is recovered on the next start.
- **Knows who said what.** Your own voice is labelled "Me"; everyone else on the call can be split into Speaker 1, 2, 3 — all on your computer.
- **Optional live transcript** window while the call is happening.
- **Safe by default.** Login is always required, the server only listens on your own computer until you decide otherwise, and no secret is ever stored in plain text.

> **Recording laws.** In many places you must tell people the call is being recorded. Auto-recording of meetings is **off** by default. Turning it on is your responsibility.

---

## Quick start (about 10 minutes)

You need **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/) — tick "Add python.exe to PATH" on Windows). The server runs on Windows, Mac or Linux. The recorder is Windows-only.

### 1. Get the code

```bash
git clone https://github.com/RudyParengal/meeting-notes.git
cd meeting-notes
```

### 2. Set up the server

```bash
python -m venv venv
venv/Scripts/pip install -r backend/requirements.txt      # Windows
# venv/bin/pip install -r backend/requirements.txt        # Mac / Linux

python scripts/setup-env.py        # asks which AI provider; creates backend/.env
```

No AI key yet? Choose **mock** — a built-in fake AI, so you can try everything first.

### 3. Start it and check it

```bash
venv/Scripts/python backend/main.py         # leave this window open
```

In a second window:

```bash
venv/Scripts/python scripts/doctor.py --e2e
```

You want every line to say `PASS`. Then open **http://127.0.0.1:8766** and log in with:

- **Email:** `admin@localhost`
- **Password:** the `MEETING_NOTES_API_KEY` value inside `backend/.env`

### 4. Install the recorder (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File windows-recorder\install.ps1 -SpeakerSeparation
```

Look for the **blue circle in the system tray**. Right-click it → **Start Meeting**. Choose **Stop Meeting** to finish; the note appears on the dashboard in a minute or two.

**Full instructions, including running the server on a separate machine:** [docs/INSTALL.md](docs/INSTALL.md)

---

## Let an AI assistant do it for you

Paste the prompt in **[docs/AGENT_SETUP.md](docs/AGENT_SETUP.md)** into Claude, ChatGPT, Cursor or any agent that can run commands on your computer. It installs and checks everything, and tells you the one or two things only you can do (such as pasting your AI key).

---

## Documentation

| Read this | When |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Installing — laptop only, Docker, or a separate server with HTTPS |
| [docs/USAGE.md](docs/USAGE.md) | Recording, the dashboard, adding teammates, searching, exports |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Changing AI provider or model, adding API keys, every setting |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Something is not working |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it fits together and why |
| [SECURITY.md](SECURITY.md) | What is protected, what is your job, how to report a problem |
| [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) | Prompt for an AI agent to install it for you |
| [CHANGELOG.md](CHANGELOG.md) | What changed between versions |

## What it costs

| Part | Cost |
|---|---|
| Software | Free (MIT licence) |
| Transcription | Free — runs on your own computer |
| Summaries | A fraction of a cent per meeting on Gemini Flash / GPT-4o-mini; free with a local model |
| Server | Your own computer, or any small VPS |

## Requirements

| | Server | Recorder |
|---|---|---|
| System | Windows, Mac or Linux | Windows 10 / 11 |
| Python | 3.10+ | 3.10+ |
| Disk | tiny (notes are text) | about 2 GB for the speech model (downloaded once) |
| Memory | modest | a few GB free while transcribing (less with a smaller speech model) |
| Internet | to reach your AI provider (not needed with Ollama) | first-time model download only |

## Tests

```bash
venv/Scripts/pip install pytest
venv/Scripts/python -m pytest tests/server -q       # server: no network or AI key needed
```

The recorder has its own tests in `tests/recorder/` (plain scripts, Windows). Every push runs both on GitHub Actions, together with a secret scan of the whole history and a real end-to-end run.

## Licence and credits

MIT — see [LICENSE](LICENSE). Built on open-source work listed in [CREDITS.md](CREDITS.md).
