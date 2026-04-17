# Meeting Notes — Self-Hosted AI Notetaker

A fully self-hosted meeting notetaker that replaces paid tools like Otter.ai, Fireflies, Granola, and Read.ai. **Audio stays 100% on your laptop**; only text transcripts are sent to cloud LLMs for analysis. Runs on Windows + any Linux VPS.

**Cost:** ~$0.10/month for 100 meetings (vs $10-20/month per seat for commercial tools).
**Works with:** Zoom, Microsoft Teams, Webex, Cisco, Slack Huddles, Discord — any meeting platform that plays audio through Windows.

> ⚠️ **Google Meet is NOT covered** — Google Workspace already has built-in Gemini notes. This tool is for the other platforms.

---

## Architecture

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│   Windows laptop            │        │   Your VPS (Linux)           │
│                             │        │                              │
│  Meeting Recorder (tray)    │        │  FastAPI backend (port 8766) │
│    ├── WASAPI loopback      │ ─────▶ │    ├── Auth (email + pw)    │
│    ├── faster-whisper       │  text  │    ├── Gemini 2.5 Flash     │
│    │   (local, GPU or CPU)  │ upload │    │   (summary, parallel)  │
│    ├── Dual-stream diariz.  │        │    ├── DeepSeek V3          │
│    ├── Custom vocab         │        │    │   (action items)       │
│    └── Auto-meeting detect  │        │    └── /var/meeting-notes/  │
│                             │ ◀───── │           (JSON + Markdown) │
│  Tray icon menu:            │ result │                              │
│    ⏺ Start / ⏹ Stop         │        │  Dashboard (vanilla JS)     │
│    🔊 Test Audio            │        │    ├── Login, multi-user    │
│    Audio Source ▶           │        │    ├── List / search / tag  │
│                             │        │    ├── Download MD/TXT/PDF  │
│                             │        │    ├── Delete / Restore     │
│                             │        │    └── Admin panel          │
└─────────────────────────────┘        └──────────────────────────────┘
```

### Why split like this?

- **Audio never leaves your laptop.** Voice = biometric. Text = much less sensitive.
- **Whisper runs locally**, leveraging your CPU or GPU. No monthly transcription fees.
- **LLM summarization runs on cheap cloud APIs** — Gemini Flash costs fractions of a penny per meeting.
- **VPS only stores text** — small disk footprint, fast search, easy backup.

---

## Features

### Windows Recorder (tray app)

- **Dual-stream audio capture** — simultaneous mic ("Me") + system loopback ("Others") for free speaker diarization
- **Local Whisper transcription** (distil-large-v3 by default, 1.5 GB, multilingual)
- **Auto-meeting detection** — auto-starts when Zoom/Teams/Webex/Slack/Discord launches (with 15s debounce)
- **Audio source picker** — choose which output device to capture (helpful with Bluetooth, HDMI, etc.)
- **Test Audio** — 5-second capture with RMS level report so you verify routing *before* a meeting
- **Custom vocabulary** — give Whisper a list of product/people names it should transcribe correctly
- **Silence detection** — alerts you if no audio is captured for 10+ seconds
- **Sound effects** — subtle beep on start, boop on stop (configurable)
- **Single-instance lock** — prevents accidental duplicate launches
- **Backup WAV + transcript** saved locally as a safety net
- **Per-user API key** — multi-user deployments
- **Windows-native** — tray icon, toast notifications, Startup-folder auto-launch

### Dashboard (browser)

- **Email + password login** (SHA-256 hashed, session tokens)
- **Multi-user** — each user sees only their own meetings; admins see all
- **Admin panel** — add/remove users, view API keys
- **Meeting list** with search and tag filtering
- **8 meeting templates**: `client`, `sales`, `technical`, `investment`, `standup`, `team`, `interview`, `generic`, plus `auto` (AI classification)
- **Re-analyze** any meeting with a different template
- **Session tagging** — color-coded tags (important, follow-up, client, internal, archived, + custom)
- **Download** as Markdown, plain text, or PDF
- **Delete + Restore** — soft-delete to trash, restore or purge
- **Auto-refresh** every 10 seconds for real-time status updates

### Backend (FastAPI)

- **Parallel AI pipeline** — Gemini summary + DeepSeek actions run concurrently (~30s total)
- **Filler-word cleanup** before LLM prompts
- **Audio file import** — upload a `.wav`/`.mp3`/`.m4a` for retrospective processing
- **Webhook on completion** — POST to any URL (Slack, email-relay, etc.)
- **Auto-export to folder** — for syncing to Obsidian vault, Dropbox, etc.
- **Interactive API docs** at `/docs` (Swagger UI)
- **16 documented endpoints** — build your own integrations

### Meeting output (per session)

Each meeting produces:

- **Meeting title** (AI-inferred)
- **Metadata** (participants detected, duration, type)
- **Executive summary** (5 bullets)
- **Key decisions made**
- **Open questions**
- **Action plan with Eisenhower Matrix** (Do Now / Schedule / Delegate / Eliminate)
- **Contextual details** per task: owner, deadline, dependencies, rationale
- **Recommended workflow** — step-by-step execution
- **Risks if delayed** + **suggested follow-up messages**

---

## Setup

Two parts:
1. **VPS backend** (one-time, by an admin)
2. **Windows recorder** (per user)

See [`docs/SETUP.md`](docs/SETUP.md) for the full walkthrough. Quick version:

### VPS backend

```bash
git clone https://github.com/YOUR_USERNAME/meeting-notes.git
cd meeting-notes/backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env              # fill in your Gemini + DeepSeek keys
cp users.json.example users.json  # create your admin user
sudo cp ../systemd/meeting-notes.service /etc/systemd/system/
sudo systemctl enable --now meeting-notes
# optional: nginx reverse-proxy at notes.yourdomain.com
```

### Windows recorder

```powershell
cd windows-recorder
.\install.ps1     # prompts for your API key
# Whisper model (~1.5 GB) auto-downloads on first launch
```

Tray icon appears — right-click → Start Meeting.

---

## Team guide

[`docs/TEAM-GUIDE.md`](docs/TEAM-GUIDE.md) is an 850-line team-onboarding guide covering:

- What the system does and how it works
- Day-to-day usage workflow
- Templates and when to use each
- Configuration options
- Troubleshooting
- Admin tasks (user management, rollback, backups)
- API reference
- Security & privacy guarantees

---

## API

Interactive docs: `https://your-vps.example.com/docs`

16 endpoints in total, covering:
- Auth: `/login`, `/logout`, `/me`
- Core: `/transcribe`, `/upload-audio`, `/sessions`, `/results/{id}`
- Export: `/results/{id}/download` (MD), `/results/{id}/pdf`, `/results/{id}/markdown`
- Tags: `PUT /results/{id}/tags`
- Re-analyze: `/results/{id}/reprocess`
- Trash: `DELETE /results/{id}`, `POST /results/{id}/restore`, `DELETE /results/{id}/purge`, `GET /trash`
- Admin: `GET|POST /admin/users`, `DELETE /admin/users/{email}`
- Meta: `/templates`, `/health`

All protected endpoints accept **either** `X-API-Key: mn_...` (for machine clients like the recorder) **or** `Authorization: Bearer <session_token>` (for browser dashboard).

---

## Security & privacy

- **Audio is processed locally** on the user's laptop. It never leaves the machine.
- **Only plain text transcripts** are sent to Google Gemini and DeepSeek.
- **Dashboard is behind email + password auth.** Session tokens expire on logout.
- **Per-user API keys** for the recorder — no shared credentials.
- **Password hashes** are SHA-256; no plaintext passwords are ever stored.
- **All meeting data** lives on your own VPS in `/var/meeting-notes/`. Fully under your control.
- **Secrets** (`.env`, `users.json`) are in `.gitignore`. Never commit them.

---

## Contributing

Issues and PRs welcome. This is a personal project actively used by a small team, so breaking changes may not land quickly — open an issue to discuss before a big PR.

---

## License

MIT. See [LICENSE](LICENSE).

## Credits

Major open-source libraries: faster-whisper, FastAPI, pystray, soundcard, google-genai, and many more. Full list including feature inspirations from similar projects (ghost-pepper, Meetily, OpenOats) in [CREDITS.md](CREDITS.md).
