# Configuration

**Using the Windows app (Notewell-Setup.exe)?** Everything is on its **Settings** screen — AI provider, key, model, microphone, speech model, language. It saves to `%USERPROFILE%\Notewell`. You do not need this page.

For a shared team server, there are two places to configure:

| Where | What it controls | Edit with |
|---|---|---|
| `backend/.env` | The **server**: your AI provider and keys, who can log in, where notes are kept | Any text editor, then restart the server |
| `%USERPROFILE%\MeetingRecorder\config.json` | The **recorder**: server address, speech model, speaker separation, auto-detect | Tray menu → **Settings (config.json)**, then restart the recorder |

Both files contain secrets. They are git-ignored; never commit, email or screenshot them.

> **Changed something and it will not start?** The server and recorder print exactly what is wrong and exit with code 78 instead of looping. Run `python scripts/doctor.py` — it checks every link and says which one failed.

---

## Choosing an AI provider

The server talks to any service that offers the **OpenAI chat format**. You set three things:

| Setting | Meaning |
|---|---|
| `LLM_API_KEY` | The key your provider gave you |
| `LLM_BASE_URL` | The provider's address |
| `LLM_MODEL` | Which model to use |

| Provider | `LLM_BASE_URL` | Example `LLM_MODEL` | Notes |
|---|---|---|---|
| **Mistral** | `https://api.mistral.ai/v1` | `ministral-14b-latest` | **Free plan.** Key: [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys). It may train on your data unless you switch that off in Admin → Privacy |
| **Google Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` | Cheap and fast. Key: [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o-mini` | The default if you set nothing |
| **Anthropic (Claude)** | `https://api.anthropic.com/v1/` | `claude-haiku-5-5` | Paid API key from [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| **xAI (Grok)** | `https://api.x.ai/v1` | `grok-4.7` | Paid API key from [console.x.ai](https://console.x.ai) |
| **Groq** | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | Very fast. Free limit (about 8,000 tokens, roughly 6,000 words, a minute) suits short meetings |
| **DeepSeek** | `https://api.deepseek.com/v1` | `deepseek-chat` | |
| **Ollama** (on your computer) | `http://localhost:11434/v1` | `llama3.1` | **Nothing leaves your machine.** No key needed. Install from [ollama.com](https://ollama.com), then `ollama pull llama3.1` |
| **LM Studio / vLLM** | their local address | the model you loaded | No key needed for local addresses |
| **Mock (testing)** | `http://127.0.0.1:9099/v1` | `mock` | A fake AI for trying the system. Run `python scripts/mock_llm.py` |

Model names change as providers release and retire models. If you see "model not found", check your provider's current model list and update `LLM_MODEL`.

The quickest way to set this up is `python scripts/setup-env.py`, or edit `backend/.env` by hand (a commented example is in [backend/.env.example](../backend/.env.example)), then restart the server and run `python scripts/doctor.py --e2e`.

### Privacy: where does the transcript go?

The **text** of your meeting is sent to whichever provider you choose. The audio never is. For confidential meetings:

- Use a local model (Ollama) for nothing to leave your machine, or
- Use a provider whose terms you have read and accept. Avoid anonymous routers or free proxies that keep prompts — transcripts can contain client and personal information.

### Different provider for each step

Three steps run per meeting: **CLASSIFY** (what kind of meeting), **SUMMARY** and **ACTION** (action items). Each can use a different provider or model; anything you leave empty falls back to the `LLM_*` setting:

```
# cheap fast model for everything, a stronger one for action items
LLM_API_KEY=...
LLM_MODEL=gemini-2.5-flash
ACTION_MODEL=gemini-2.5-pro
```

Each step also has `_API_KEY` and `_BASE_URL` (`SUMMARY_API_KEY`, `ACTION_BASE_URL`, …), so you can put action items on a completely different service.

### "Thinking" models and long meetings

Some models spend part of their output budget "thinking" before they answer. If notes come back cut off or empty, raise `LLM_MAX_TOKENS` (default 8192). The server retries temporary provider errors (busy, rate-limited) automatically after 3, 8 and 20 seconds (`LLM_RETRY_DELAYS`). It does **not** retry a used-up quota or a wrong key, because waiting cannot fix those — the note says why it failed instead.

---

## Adding and changing API keys

| I want to… | Do this |
|---|---|
| Change my AI provider's key | Edit `LLM_API_KEY` in `backend/.env`, restart the server, run `doctor.py --e2e` |
| Use a second provider for one step | Set `SUMMARY_API_KEY` / `ACTION_API_KEY` etc. |
| Rotate the server's master key | Put a new value in `MEETING_NOTES_API_KEY` (make one with `python -c "import secrets; print('mn_' + secrets.token_hex(24))"`), restart the server, then update each recorder's `api_key` |
| Give a teammate a key | Dashboard → **Admin** → add user. The key is shown once |
| Replace one person's key | Dashboard → **Admin** → **New key**. The old one stops immediately |
| Put a key in the recorder | `install.ps1 -ApiKey …` (or the `MN_API_KEY` environment variable), or edit `api_key` in `config.json` |

Keys the server generates look like `mn_` followed by 48 characters. The server stores teammates' keys only as one-way hashes, so it cannot show them again — that is deliberate.

Never put a key on a command line you will keep in shell history, in a screenshot, or in a chat. If a key was ever exposed, replace it at once.

---

## Server settings (`backend/.env`)

Only the first two are required.

| Setting | Default | Meaning |
|---|---|---|
| `MEETING_NOTES_API_KEY` | — **required** | Master key: the recorder's key and the admin's dashboard password. At least 24 characters; placeholders are rejected |
| `LLM_API_KEY` | — **required** unless the provider is local | Your AI provider's key |
| `LLM_BASE_URL` / `LLM_MODEL` | OpenAI / `gpt-4o-mini` | See above |
| `ADMIN_EMAIL` | `admin@localhost` | The admin's login name |
| `HOST` | `127.0.0.1` | `127.0.0.1` = this computer only. `0.0.0.0` = every network; only with HTTPS in front |
| `PORT` | `8766` | |
| `DATA_DIR` | `./data` | Where notes and the user list are kept |
| `PUBLIC_URL` | empty | Your dashboard's public address; included in webhook messages |
| `WEBHOOK_URL` | empty | The server POSTs a small JSON message here when a note is ready (Slack/Discord/Zapier/your own). It contains the session id, title, type, owner's email and your `PUBLIC_URL` — never the transcript |
| `EXPORT_FOLDER` | empty | Also saves each finished note as a `.md` file here (for example an Obsidian vault) |
| `MAX_UPLOAD_MB` | `200` | Largest audio upload |
| `MAX_TRANSCRIPT_CHARS` | `1000000` | Largest transcript |
| `TOKEN_TTL_HOURS` | `12` | How long a dashboard login lasts |
| `LOGIN_MAX_FAILURES` / `LOGIN_WINDOW_SEC` | `8` / `900` | After this many wrong passwords from one address, logins from it are blocked for the window |
| `CORS_ORIGINS` | empty | Other websites allowed to call the API from a browser. The bundled dashboard needs none |
| `TRUST_PROXY` | off | Set to `1` **only** behind your own HTTPS proxy, so the throttle sees visitors' real addresses |
| `SERVER_WHISPER_MODEL` | `base` | Speech model used by the optional `/upload-audio` |
| `LLM_MAX_TOKENS` / `LLM_RETRY_DELAYS` | `8192` / `3,8,20` | See above |

---

## Recorder settings (`config.json`)

The installer writes this file. After editing, right-click the tray icon → **Quit**. A small watchdog (the "supervisor") starts the recorder again about 10 seconds later with the new settings — that is also how it recovers from crashes. To stop it for good, run `windows-recorder\uninstall.ps1`.

| Setting | Default | Meaning |
|---|---|---|
| `api_url` | `http://127.0.0.1:8766` | Your server. Plain `http://` is refused for anything but your own computer |
| `api_key` | — | Your server key |
| `allow_insecure_http` | `false` | Overrides that refusal. Not recommended: the key would cross the network readable |
| `whisper_model` | `distil-large-v3` | Speech model. Smaller = faster and lighter, less accurate: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `whisper_device` | `cpu` | `cuda` if you have an NVIDIA GPU |
| `whisper_compute_type` | `int8` | `float16` on a GPU |
| `dual_stream` | `true` | Capture your microphone and the call separately, so "Me" and "Others" can be told apart |
| `diarize_others` | `false` | Split "Others" into Speaker 1, 2, 3 (needs the models from `-SpeakerSeparation`) |
| `diarize_max_speakers` | `-1` | `-1` = decide from the audio; or a fixed number |
| `diarize_threshold` / `diarize_min_talk_seconds` | `0.9` / `5` | Tuning: higher threshold = fewer, merged speakers. The library default split a two-person call into eight |
| `live_transcript` | `false` | Show the always-on-top live window |
| `live_model` | `base` | Small and quick model for the live text (the final transcript is unaffected) |
| `auto_detect` | `false` | Start when a meeting app opens. **Off by default — see the recording-law note** |
| `auto_detect_processes` | Zoom, Teams, Webex, Slack, Discord | Programs that count as "a meeting" |
| `auto_detect_browser_meeting` | `true` | Also recognise meetings in browser tab titles (only used if `auto_detect` is on) |
| `custom_vocabulary` | `[]` | Names and jargon to help the speech model, e.g. `["Acme", "Kubernetes"]` |
| `meeting_type_default` | `auto` | `auto`, or force `client`, `sales`, `team`, `technical`, `investment`, `standup`, `interview`, `generic` |
| `spool_enabled` | `true` | Write audio to disk as it is captured, so a crash loses nothing. Leave on |
| `audio_source` | `default` | A specific speaker/headset name (or use the tray's **Audio Source**) |
| `sound_effects` | `true` | Beep on start/stop |
| `silence_alert_seconds` | `10` | Warn if the call has been silent this long |
| `archive_backups` | `true` | After a note is safely stored, move the backup audio to `%USERPROFILE%\MeetingArchive` |
| `archive_retention_days` | `30` | Archived audio older than this is cleared automatically |

Everything not listed keeps a safe default. Unknown settings are ignored, and a damaged file is never overwritten — the recorder says what is wrong and waits.

---

## Optional features

| Feature | Install | Then |
|---|---|---|
| Transcribe an uploaded audio file on the server (`POST /upload-audio`) | `pip install -r backend/requirements-optional.txt` | Uses a small CPU model by default (`SERVER_WHISPER_MODEL`) |
| PDF export (`GET /results/<id>/pdf`) | same file | WeasyPrint also needs system libraries (Pango/Cairo) — see its documentation. Without it the endpoint says so |

Both are off unless you install them; the core server needs neither.
