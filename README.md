# Notewell

**An AI note-taker that runs on your own computer.** It records your calls on Windows, turns the audio into text on your own machine, and writes a summary and a list of action items. No subscription, no sign-up, no meeting bot joining your calls.

- **Your audio never leaves your laptop.** Only the written transcript goes to the AI that writes the summary — and with a local model (Ollama), not even that.
- **Free to run.** Pick the free Mistral plan on the Settings screen, paste the key once, done. Or use OpenAI, Claude, Grok, Groq, Ollama or any OpenAI-compatible service.
- **Knows who said what.** Your voice is labelled "Me"; everyone else is split into Speaker 1, 2, 3 — on your computer.
- **Survives crashes.** Audio is saved to disk while you talk. If the laptop dies mid-call, the recording is recovered on the next start.
- **Edit, tag, search and export** every note — Markdown, plain text or PDF.

> **Recording laws.** In many places you must tell people a call is being recorded. Notewell shows a reminder on the Record screen. Telling people is your responsibility.

---

## Install on Windows (about 5 minutes)

1. Download **`Notewell-Setup-x.y.z.exe`** from the [latest release](https://github.com/RudyParengal/meeting-notes/releases/latest). Only from there: the release notes list the file's SHA-256, so you can check it with `Get-FileHash` in PowerShell.
2. Double-click it. Windows may say **"Windows protected your PC"** — the installer is not code-signed. Click **More info → Run anyway**.
3. Click **Next** until it finishes. Leave **"Open Notewell now"** ticked.
4. Notewell opens in its own window and signs you in. The first start downloads the speech model (about 500 MB) — give it a few minutes on a slow connection.
5. Go to **Settings**, choose **Mistral (free plan)**, click **Get a free key from Mistral**, paste the key, press **Test without saving**, then **Save**.
6. Go to **Record**, press the round record button, talk, press the square stop button. The note appears in a minute or two.

| You need | |
|---|---|
| Windows 10 or 11, 64-bit | |
| About 2 GB of free disk | the app plus the speech model |
| 8 GB of memory | recommended; on a slower PC pick the "Base" speech model in Settings |
| Internet | once for the speech model, then only to reach your AI |

**Where things live:** your meetings are in `%USERPROFILE%\Notewell`. Uninstalling (Settings → Apps → Notewell) leaves them there.

**Already running?** Look for the blue Notewell icon by the clock. Double-clicking the desktop icon again just reopens the window.

### About "use my ChatGPT / Claude / Grok subscription"

A ChatGPT Plus, Claude Pro or SuperGrok subscription **does not include the API key** an app like this needs — those are sold separately, pay per use. The free Mistral plan is the easiest start. If you already pay for OpenAI, Anthropic or xAI developer access, pick it on the Settings screen and paste that key instead.

---

## Let an AI assistant install it for you

Paste the prompt in **[docs/AGENT_SETUP.md](docs/AGENT_SETUP.md)** into Claude Code, Cursor, ChatGPT or any agent that can run commands on your computer. It installs Notewell, checks it, and tells you the one thing only you can do: paste your AI key.

---

## Run it for a team (server + recorders)

Notewell can also run as one shared server — on a VPS or in Docker — with each person's laptop recording into it. Everyone gets their own login and sees only their own meetings.

```bash
git clone https://github.com/RudyParengal/meeting-notes.git
cd meeting-notes
python -m venv venv
venv/Scripts/pip install -r backend/requirements.txt      # Mac / Linux: venv/bin/pip
python scripts/setup-env.py                               # asks which AI; creates backend/.env
venv/Scripts/python backend/main.py
```

Full instructions, including HTTPS and pointing each laptop's recorder at the server: **[docs/INSTALL.md](docs/INSTALL.md)**.

---

## Documentation

| Read this | When |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Running a shared server — laptop, Docker, or Linux with HTTPS |
| [docs/USAGE.md](docs/USAGE.md) | Recording, the dashboard, adding teammates, searching, exports |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Changing AI provider or model, every setting |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Something is not working |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it fits together, and the API |
| [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) | Prompt for an AI agent to install it for you |
| [SECURITY.md](SECURITY.md) | What is protected, what is your job, how to report a problem |
| [CHANGELOG.md](CHANGELOG.md) | What changed between versions |

## What it costs

| Part | Cost |
|---|---|
| Software | Free (MIT licence) |
| Transcription | Free — runs on your own computer |
| Summaries | Free on the Mistral free plan or a local model; a fraction of a cent per meeting on paid providers |

## Tests

```bash
venv/Scripts/pip install pytest
venv/Scripts/python -m pytest tests/server -q       # server: no network or AI key needed
```

The recorder has its own tests in `tests/recorder/` (plain scripts, Windows). Every push runs both on GitHub Actions, with a secret scan of the whole history and a real end-to-end run. Every release builds the installer and checks the built app starts and answers before publishing it.

## Licence and credits

MIT — see [LICENSE](LICENSE). The desktop design borrows ideas from [Meetily](https://github.com/Zackriya-Solutions/meetily) (MIT). Everything it is built on is listed in [CREDITS.md](CREDITS.md).
