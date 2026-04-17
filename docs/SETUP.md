# Meeting Notes — Setup Guide

Complete self-hosted meeting notetaker. Audio stays on your laptop, only text goes to the cloud APIs.

## Architecture

```
Your Windows laptop              Your VPS (YOUR_VPS_IP)
─────────────────────            ──────────────────────────
Meeting Recorder (tray)    →     FastAPI backend (port 8766)
  - WASAPI audio capture              ↓
  - faster-whisper transcribe    Gemini 2.5 Flash (summary) ─┐
  - Sends TEXT only              DeepSeek V3 (action items)  ┤→ parallel
                                        ↓                    ─┘
                                 Saved to /var/meeting-notes/
                                        ↓
                                 Dashboard at notes.example.com
```

**Cost:** ~£0.10/month for 100 meetings (vs £12/month now)
**Privacy:** audio never leaves your laptop, only text transcripts go to Gemini/DeepSeek

---

## Part 1 — Windows laptop setup (5 min)

### Prerequisites
- Python 3.11+ installed from [python.org](https://python.org) (tick "Add to PATH" during install)
- Your VPS API key from `/opt/meeting-notes/backend/.env` (the `MEETING_NOTES_API_KEY=...` line, starting with `mn_`)

### Install
1. Copy the `meeting-recorder/` folder to your laptop (anywhere — the installer copies to `%LOCALAPPDATA%\MeetingRecorder`)
2. Open **PowerShell** in that folder
3. Run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
4. Paste your API key when prompted
5. Installer:
   - Creates venv at `%LOCALAPPDATA%\MeetingRecorder\venv`
   - Installs dependencies (faster-whisper, soundcard, pystray, etc. — ~5 min)
   - Creates startup shortcut
   - Saves config to `%APPDATA%\MeetingRecorder\config.json`

### First launch
```powershell
& "$env:LOCALAPPDATA\MeetingRecorder\venv\Scripts\pythonw.exe" "$env:LOCALAPPDATA\MeetingRecorder\recorder.py"
```
- A blue circle icon appears in the system tray (bottom-right)
- **First run** will download the Whisper model (`distil-large-v3`, ~1.5 GB) to `%USERPROFILE%\.cache\huggingface\` — wait 2-3 min

### Using it
- **Right-click tray icon** → "Start Meeting" when your call begins (Zoom/Teams/Meet, doesn't matter which)
- Icon turns red while recording
- Right-click → "Stop Meeting" when done
  - Transcribes locally (about 1 min per 10 min of audio on your Ultra 9)
  - Uploads transcript only
  - Notification when AI analysis complete (~30s after upload)
- **Auto-detect option**: Right-click → "Auto-detect meetings" to auto-start when Zoom/Teams launches

---

## Part 2 — VPS (already set up)

Everything is already running on your VPS. Reference info:

**Service:** `meeting-notes.service` (systemd, auto-restart)
**Port:** 8766
**Public URL:** `https://notes.example.com` (needs DNS: `notes.example.com → YOUR_VPS_IP` in Cloudflare)
**Code:** `/opt/meeting-notes/`
**Notes storage:** `/var/meeting-notes/` (JSON + Markdown per meeting)
**API key:** `/opt/meeting-notes/backend/.env` → `MEETING_NOTES_API_KEY`
**Models used:**
- Summary + classification: Gemini 2.5 Flash (via GOOGLE_API_KEY)
- Action items: DeepSeek V3.2 (via DEEPSEEK_API_KEY)

### Check service status
```bash
systemctl status meeting-notes
journalctl -u meeting-notes -f  # live log
```

### Restart if needed
```bash
systemctl restart meeting-notes
```

### Change API key
```bash
# Edit /opt/meeting-notes/backend/.env
systemctl restart meeting-notes
# Update %APPDATA%\MeetingRecorder\config.json on Windows
```

---

## Part 3 — Test end-to-end (from Windows)

Quick sanity test without a real meeting:
```powershell
# From any PowerShell on Windows
$key = "PASTE_YOUR_API_KEY"
Invoke-RestMethod -Method POST -Uri "https://notes.example.com/transcribe" `
  -Headers @{ "X-API-Key" = $key; "Content-Type" = "application/json" } `
  -Body (@{
    transcript = "Alice: let's ship by Friday. Bob: I'll review the PR tomorrow."
    duration_sec = 60
    meeting_type = "auto"
  } | ConvertTo-Json)
```
You'll get back a `session_id`. Then visit `https://notes.example.com` in a browser, paste the API key, and within 30s you'll see the processed meeting.

---

## Troubleshooting

**Tray icon doesn't appear:** Check `%APPDATA%\MeetingRecorder\recorder.log`. Most common: wrong `api_key`.

**"No speech detected":** Your system audio isn't being routed. Go to Windows Settings → Sound → App volume → check Zoom/Teams is playing through the correct default speaker.

**Transcription very slow:** The Ultra 9 285H should do ~5x realtime. If slower:
- Switch to smaller model: edit `config.json` → `"whisper_model": "base"` (faster, slightly lower quality)
- Or enable GPU (advanced): set `"whisper_device": "cuda"` and install CUDA runtime

**Processing stuck "Processing…":** Check VPS service logs: `ssh root@YOUR_VPS_IP "journalctl -u meeting-notes -n 50"`. Most common: expired API keys (Gemini or DeepSeek).

**Uninstall:**
```powershell
Remove-Item -Recurse "$env:LOCALAPPDATA\MeetingRecorder"
Remove-Item -Recurse "$env:APPDATA\MeetingRecorder"
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\MeetingRecorder.lnk"
Remove-Item "$([Environment]::GetFolderPath('Startup'))\MeetingRecorder.lnk"
```
