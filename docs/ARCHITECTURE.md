# How Notewell fits together

## The two ways to run it

| | Windows app | Team server |
|---|---|---|
| **Who** | One person | A team; each person has their own login |
| **Install** | `Notewell-Setup.exe` | `git clone`, then [INSTALL.md](INSTALL.md) |
| **Recorder** | Built in | Each laptop runs `windows-recorder/` and sends transcripts to the server |
| **Server** | Built in, on `127.0.0.1` only | `backend/main.py` on a VPS or in Docker, behind HTTPS |
| **Settings** | Settings screen → `%USERPROFILE%\Notewell` | `backend/.env` + each recorder's `config.json` |

Both use the same code. The Windows app is `desktop/notewell.py`, which starts the server and the recorder in one process.

## What happens to a meeting

```
  microphone + call audio
          │   (recorded on your computer; saved to disk as it goes)
          ▼
  speech-to-text  (faster-whisper, on your computer)
          │
  who said what   ("Me" = your microphone; others split into Speaker 1, 2, 3 by sherpa-onnx)
          │
          ▼  only the TEXT goes on from here
  server: POST /transcribe
          │
  AI you chose:   1. what kind of meeting  2. summary  3. action items
          │
          ▼
  saved as JSON in the data folder → shown in the dashboard
```

If the AI step fails (no key, provider down), the transcript is still saved and **Generate** in the dashboard runs the AI step again.

## Linked server, accounts and the phone (2.7.0)

The Windows app can be linked to a Notewell server of your own. Meetings do not need it, but dictation history, screen recordings, the website and the phone all do.

| Part | What it does |
|---|---|
| **Account** | You sign in once in Settings. Passwords are set through a one-time link. Five wrong passwords in a row lock the account for 15 minutes |
| **Device pass** | Each laptop or phone gets its own pass when you sign in. Switching a device off on the website's Devices page stops it at once |
| **Meetings** | Audio is turned into text on the laptop. Only the transcript goes to the server, which writes the summary and action items |
| **Dictation** | Runs on the laptop (`windows-recorder/dictation.py`, a small speech model). The history syncs to the server later. Words are not sent anywhere else unless you switch tidy-up on |
| **Screen recording** | `windows-recorder/screen_record.py` records the screen and microphone, waits in `screen\outbox` if there is no internet, uploads, and copies a share link. Share links work without an account and can be switched off |
| **Sync** | Every device asks the server for changes every 5 seconds and redraws only when something changed |
| **Phone** | A web page on the same server (`/m/`) with the same look and filters as the website, plus an Android app with a floating bubble that types your dictation into any app |

What leaves the laptop, in plain words: meeting transcripts, screen recordings and phone memos go to your own server. Meeting audio does not.

The app sends its version with every request, so the server can tell which version each laptop runs.

## The folders

| Folder | What is in it |
|---|---|
| `backend/` | The server (FastAPI). `main.py` routes, `pipeline.py` the AI steps, `prompts.py` the meeting templates, `storage.py` saving notes, `security.py` passwords/keys/tokens, `settings_store.py` the AI chosen on the Settings screen, `recorder_bridge.py` the link to the built-in recorder |
| `windows-recorder/` | The recorder. `recorder.py` capture, tray and transcription; `spool.py` crash-safe audio on disk; `diarize.py` speaker separation; `live_transcribe.py` live text |
| `dashboard/` | The web page: `index.html`, `app.js`, `style.css`. Plain JavaScript, no build step. Libraries in `vendor/` |
| `desktop/` | The Windows app: `notewell.py`, the PyInstaller recipe `notewell.spec`, the Inno Setup recipe `installer.iss`, the icon |
| `scripts/` | `setup-env.py` (creates the server's settings), `doctor.py` (checks every link), `mock_llm.py` (a fake AI for testing), `check-secrets.py` (secret scan) |
| `tests/server/` | Server tests (pytest, no network or AI key needed) |
| `tests/recorder/` | Recorder tests (plain scripts, Windows) |

## Where the Windows app keeps things

Everything is under `%USERPROFILE%\Notewell`:

| Path | What |
|---|---|
| `data\` | Your meetings (one JSON file each), trash, `settings.json` (the AI you chose, including its key) |
| `recorder\` | Recorder `config.json` and `recorder.log` |
| `archive\` | Audio kept while it is being processed, and recovered recordings |
| `window-qt\` | The app window's own storage (sign-in, cache), separate from any browser |
| `app.key` | The app's own key (the server and recorder use it to talk to each other), locked to this Windows user |
| `linked-server.json` | Only if you link a server of your own: its address and key (the key locked on first read) |
| `linked-state.json` | What the last sync with that server saw, so the next one knows which side changed |
| `port` | Which local port the app is on (usually 8767) |
| `notewell.log` | Start-up errors |

## The API

Every request except `/health` needs either `X-API-Key: <key>` or `Authorization: Bearer <token from /login>`. There is deliberately no public `/docs` page; this table is the list.

| Route | What it does |
|---|---|
| `GET /health` | Is the server up (no sign-in needed) |
| `GET /health/deep` | Can notes be saved, which AI each step uses, can it read audio files / make PDFs |
| `POST /login`, `POST /logout`, `GET /me` | Sign in / out, who am I |
| `POST /transcribe` | Send a transcript; notes are made in the background (202) |
| `POST /upload-audio` | Send an audio file; it is transcribed, then notes are made (202) |
| `GET /results/{id}` | One meeting: status, summary, action items, transcript |
| `GET /results/{id}/markdown`, `/download`, `/pdf` | Exports |
| `POST /results/{id}/reprocess?meeting_type=auto` | Run the AI step again (optionally another model or template) |
| `PUT /results/{id}/notes`, `PUT /results/{id}/tags` | Edit notes, set tags |
| `GET /sessions` | List meetings |
| `DELETE /results/{id}`, `POST /results/{id}/restore`, `DELETE /results/{id}/purge`, `GET /trash` | Trash, restore, delete for good |
| `GET /templates` | Meeting templates |
| `GET/PUT /settings/ai`, `POST /settings/ai/test` | Which AI writes the notes; test a key without saving |
| `GET /recorder`, `POST /recorder/start`, `POST /recorder/stop`, `GET /recorder/devices`, `PUT /recorder/settings` | The built-in recorder (Windows app only) |
| `POST/GET /admin/users`, `POST /admin/users/{email}/rotate-key`, `DELETE /admin/users/{email}` | Team accounts (admin only) |
