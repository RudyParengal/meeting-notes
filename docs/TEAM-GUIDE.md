# Meeting Notes -- Team Setup Guide

> **Version 1.3** | Last updated: April 2026
> Self-hosted AI meeting notetaker for your team

---

## 1. What Is This?

Meeting Notes is a **self-hosted AI meeting notetaker** that replaces expensive commercial tools like Otter.ai, Fireflies, and Fathom.

**Key advantages:**

- **Privacy first** -- Audio never leaves your laptop. Only the text transcript is sent to cloud AI for analysis.
- **Works with everything** -- Zoom, Microsoft Teams, Webex, Slack Huddles, Discord, and any other app that plays audio through your speakers.
- **Dirt cheap** -- Approximately 0.10 GBP/month for 100 meetings, compared to 12+ GBP/month per seat for commercial tools.
- **Smart output** -- Every meeting gets a structured summary (via Gemini 2.5 Flash) and an Eisenhower Matrix action plan (via DeepSeek V3), generated in parallel.
- **No Google Meet** -- Google Meet is excluded because Google's built-in Gemini already handles meeting notes there.

---

## 2. How It Works

```
                         YOUR LAPTOP (Windows)                          VPS (notes.example.com)
 +-------------------------------------------------+      +------------------------------------------+
 |                                                 |      |                                          |
 |  +--------------+    +-----------------------+  |      |  +--------------------------------------+ |
 |  |  Any Meeting  |--->  Windows Recorder     |  |      |  |  FastAPI Backend (port 8766)         | |
 |  |  App (Zoom,   |    |  (system tray app)    |  |      |  |                                      | |
 |  |  Teams, etc.) |    |                       |  |      |  |  Receives text transcript             | |
 |  +--------------+    |  1. Captures audio     |  |      |  |           |                          | |
 |                       |     via WASAPI loopback|  |      |  |     +-----+-----+                    | |
 |  +--------------+    |  2. Dual-stream:       |  |      |  |     |           |                    | |
 |  |  Your Mic     |--->     "Me" + "Others"   |  |      |  |     v           v                    | |
 |  +--------------+    |  3. Whisper transcribes|  |      |  |  Gemini      DeepSeek               | |
 |                       |     locally (on CPU)   |  |      |  |  2.5 Flash   V3                     | |
 |                       |  4. Sends TEXT only ------------>|  |  (summary)   (action items)          | |
 |                       |     to VPS             |  |      |  |     |           |                    | |
 |                       +-----------------------+  |      |  |     +-----+-----+                    | |
 |                                                 |      |  |           |                          | |
 |  Audio stays here. Never uploaded.              |      |  |           v                          | |
 +-------------------------------------------------+      |  |  Stored as .json + .md               | |
                                                          |  |  at /var/meeting-notes/              | |
                                                          |  +--------------------------------------+ |
                                                          |                                          |
                                                          |  +--------------------------------------+ |
                                                          |  |  Dashboard (notes.example.com)       | |
                                                          |  |  - View summaries + action plans      | |
                                                          |  |  - Search, tag, download, re-analyze  | |
                                                          |  |  - Admin panel for user management    | |
                                                          |  +--------------------------------------+ |
                                                          +------------------------------------------+
```

**Processing flow per meeting:**

1. You start/stop a recording from the system tray icon.
2. Whisper AI transcribes the audio on your machine (takes 30s-2min depending on meeting length).
3. The text-only transcript is uploaded to the VPS via HTTPS.
4. Two AI models process the transcript **in parallel**:
   - **Gemini 2.5 Flash** classifies the meeting type and generates the summary.
   - **DeepSeek V3** generates the action plan with Eisenhower Matrix prioritization.
5. Results appear on the dashboard within ~30 seconds.

---

## 3. Getting Started

### Prerequisites

| Requirement | Details |
|-------------|---------|
| OS | Windows 10 or Windows 11 |
| Python | 3.11 or newer (download from [python.org](https://python.org)) |
| RAM | 4 GB free (for the Whisper model) |
| Disk | ~2 GB free (for Python environment + Whisper model) |
| Internet | Required for uploading transcripts and accessing the dashboard |

### Step 1: Get Your Account

Ask your admin to create your account. He will:
- Create your user on the dashboard
- Send you an email with:
  - Your **login email** and **temporary password**
  - Your **API key** (starts with `mn_`)

### Step 2: Download the Recorder

Get the `windows-recorder` folder from the team shared drive (or USB from the admin). It contains:

```
windows-recorder/
  recorder.py         # The main recorder application
  requirements.txt    # Python dependencies
  install.ps1         # One-click installer script
```

### Step 3: Run the Installer

Open **PowerShell** and run:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer will:
1. Check that Python is installed
2. Create a virtual environment at `%LOCALAPPDATA%\MeetingRecorder`
3. Install all Python dependencies (3-5 minutes)
4. Create a startup shortcut (auto-launches on login)
5. Prompt you for your **API key** -- paste the `mn_...` key from the admin

### Step 4: First Launch

On first launch, the Whisper speech recognition model downloads automatically:
- **Size:** ~1.5 GB (one-time download)
- **Time:** 2-5 minutes depending on your internet speed
- A tray icon (blue dot) will appear once ready

> **Tip:** If the Whisper download fails due to a symlink error, run the recorder once as Administrator, or ask Admin for the bundled model files.

### Step 5: Log Into the Dashboard

1. Open your browser and go to **[notes.example.com](https://notes.example.com)**
2. Enter your email and password
3. You will see the Meeting Notes dashboard (empty at first)

---

## 4. Using the Recorder (Daily Workflow)

### System Tray Icon

The recorder lives in your Windows system tray (bottom-right, near the clock):

| Icon | Meaning |
|------|---------|
| Blue dot | Idle -- not recording |
| Red dot | Recording in progress |

### Starting and Stopping

**Right-click** the tray icon to see the menu:

| Menu Item | Action |
|-----------|--------|
| **Start Meeting** | Begin recording audio |
| **Stop Meeting** | Stop recording, transcribe, and upload |
| **Auto-detect meetings** | Toggle automatic start/stop when Zoom/Teams opens |
| **Open Dashboard** | Opens notes.example.com in your browser |
| **Settings (config.json)** | Opens the configuration file in Notepad |
| **View logs** | Opens the log file for troubleshooting |
| **Quit** | Exit the recorder |

### What Happens When You Record

1. **Start:** You hear an ascending beep (if sound effects are enabled). The icon turns red.
2. **During:** The recorder captures two audio streams:
   - **"Me"** -- your microphone input
   - **"Others"** -- system audio output (what you hear from other participants)
3. **Silence alert:** If no audio is detected for 10+ seconds, you get a notification -- check your audio routing.
4. **Stop:** You hear a descending boop. The icon turns blue.
5. **Processing:** A notification says "Transcribing..." -- Whisper runs locally (30s-2min).
6. **Upload:** The text transcript is sent to the VPS. You get a final notification when processing is complete.

### Auto-Detect Mode

When enabled, the recorder automatically starts when it detects a meeting app running:

- Zoom, Microsoft Teams, Webex, Slack, Discord
- Also checks window titles for meeting-related keywords
- **Debounce:** The meeting app must be active for 15 seconds before auto-start (prevents false triggers)
- Automatically stops when the meeting app closes

### Custom Vocabulary

Add your product names, people names, or technical terms to `config.json` under `custom_vocabulary` to improve transcription accuracy:

```json
{
  "custom_vocabulary": ["your company", "TeamApp", "DeepSeek", "YourProduct", "FastAPI"]
}
```

---

## 5. Using the Dashboard

### Login

Go to **[notes.example.com](https://notes.example.com)** and sign in with your email and password.

### Layout

| Area | Description |
|------|-------------|
| **Left sidebar** | List of all your meetings, sorted by date (newest first) |
| **Main content** | Selected meeting summary and action plan |
| **Top bar** | Search box, user name, logout button |
| **Tag filter bar** | Filter meetings by tag |

> **Privacy:** You can only see **your own** meetings. Admins can see all meetings across users.

### Viewing a Meeting

Click any meeting in the sidebar to view:
- **Metadata:** Meeting type, duration, date/time
- **Summary:** Executive summary, key decisions, open questions
- **Action Plan:** Eisenhower Matrix with priorities, owners, and suggested due dates
- **Raw transcript:** Expandable section at the bottom

### Downloading

Each meeting has download buttons:

| Format | Description |
|--------|-------------|
| **MD** | Markdown file -- full notes with formatting |
| **TXT** | Plain text -- stripped of formatting |
| **PDF** | Formatted PDF export (available via API) |

### Tags

Tag meetings to organize them. Built-in tag suggestions:

| Tag | Color | Use for |
|-----|-------|---------|
| `important` | Red | High-priority meetings |
| `follow-up` | Yellow | Meetings that need follow-up action |
| `client` | Blue | Client-facing meetings |
| `internal` | Grey | Internal meetings |
| `archived` | Dark grey | Old or resolved meetings |

You can also type any custom tag name. Click a tag in the filter bar to filter the sidebar.

### Search

Type in the search box to find meetings by keyword. The search covers meeting titles, summaries, and transcripts.

### Re-Analyze

If the AI chose the wrong meeting type, you can re-analyze with a different template:
1. Open the meeting
2. Use the "Re-analyze as..." dropdown
3. Select the correct template (e.g., change from "generic" to "client")
4. The summary and action plan will be regenerated

---

## 6. Meeting Templates

The AI automatically classifies each meeting, but you can override the template. Each template adjusts what the AI focuses on:

| Template | When to Use | AI Focus |
|----------|-------------|----------|
| **auto** | Default -- let the AI decide | AI classifies from transcript content |
| **client** | Meetings with external clients | Commitments, deliverables, client satisfaction, next steps |
| **sales** | Sales calls and demos | Objections, buying signals, pricing, decision makers, closing steps |
| **technical** | Architecture, code reviews, debugging | Architecture decisions, tech debt, blockers, technical action items |
| **investment** | Investor meetings, fundraising | Metrics discussed, valuation, due diligence, investor concerns |
| **standup** | Daily standups | Per-person format: done yesterday, planned today, blockers |
| **team** | Internal team meetings | Project updates, decisions, ownership, dependencies |
| **interview** | Job interviews | Candidate assessment, strengths, red flags, hiring recommendation |
| **generic** | Everything else | General-purpose comprehensive notes |

---

## 7. Configuration Reference

The recorder configuration lives at:
```
%APPDATA%\MeetingRecorder\config.json
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `api_url` | string | `https://notes.example.com` | VPS API endpoint |
| `api_key` | string | _(your key)_ | Your personal API key (starts with `mn_`) |
| `whisper_model` | string | `distil-large-v3` | Whisper model. Options: `tiny`, `base`, `small`, `medium`, `large-v3`, `distil-large-v3` |
| `whisper_compute_type` | string | `int8` | Compute type. `int8` = fastest CPU. Use `float16` if you have a GPU |
| `whisper_device` | string | `cpu` | Device: `cpu` or `cuda` (NVIDIA GPU) |
| `sample_rate` | int | `16000` | Audio sample rate in Hz |
| `chunk_seconds` | int | `30` | Audio chunk size for processing |
| `auto_detect` | bool | `false` | Auto-start recording when meeting apps are detected |
| `auto_detect_processes` | list | `["Zoom.exe", "Teams.exe", ...]` | Process names to watch for auto-detect |
| `auto_detect_browser_meeting` | bool | `true` | Check browser window titles for meeting keywords |
| `auto_detect_debounce_seconds` | int | `15` | Seconds the app must be active before auto-start |
| `meeting_type_default` | string | `auto` | Default meeting template |
| `notify_on_complete` | bool | `true` | Show a Windows notification when processing finishes |
| `dual_stream` | bool | `true` | Capture mic + system audio separately for "Me" vs "Others" labels |
| `custom_vocabulary` | list | `[]` | Custom words for better transcription (product names, people, etc.) |
| `sound_effects` | bool | `true` | Play beep/boop sounds on start/stop |
| `silence_alert_threshold_rms` | float | `0.001` | RMS level below which audio is considered silence |
| `silence_alert_seconds` | int | `10` | Seconds of silence before showing an alert |

### Changing Models

If you have a lower-spec laptop, use a smaller Whisper model:

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| `tiny` | ~75 MB | Fastest | Basic (for quick tests) |
| `base` | ~150 MB | Fast | Good enough for clear audio |
| `small` | ~500 MB | Moderate | Better accuracy |
| `distil-large-v3` | ~1.5 GB | Moderate | Best accuracy (recommended) |
| `large-v3` | ~3 GB | Slowest | Marginally better than distil |

### GPU Acceleration

If you have an NVIDIA GPU with CUDA installed:

```json
{
  "whisper_device": "cuda",
  "whisper_compute_type": "float16"
}
```

This makes transcription 5-10x faster.

---

## 8. Troubleshooting

### "No speech detected"

- **Cause:** The recorder is not capturing the correct audio output.
- **Fix:** Check Windows Sound Settings -- make sure your meeting app audio is going through the **default speaker** (not headphones or a different device). The recorder captures the default speaker loopback.

### Recording is too quiet or garbled

- Open **Windows Sound Settings** -> **Output** -> increase volume.
- Check that the correct output device is set as default.
- If using Bluetooth headphones, they sometimes switch to a mono "hands-free" profile -- switch back to stereo.

### "Upload failed"

- Check your internet connection.
- Verify your `api_key` in `config.json` is correct (starts with `mn_`).
- Check the log file: right-click tray icon -> **View logs**.
- The transcript is saved locally as a backup at `%APPDATA%\MeetingRecorder\last_transcript_*.txt`.

### Dashboard not loading

- Clear your browser cache (Ctrl+Shift+Delete).
- Try an incognito/private window.
- Check that the VPS is reachable: visit `https://notes.example.com/health` -- you should see `{"status": "ok"}`.

### Whisper model download fails

- This is often a Windows symlink permission error.
- **Fix 1:** Run the recorder once as Administrator (right-click -> Run as administrator).
- **Fix 2:** Ask Admin for the pre-downloaded model folder and place it in `%LOCALAPPDATA%\MeetingRecorder\models\distil-large-v3\`.

### Meeting processed but summary is wrong

- Use the **Re-analyze** dropdown on the dashboard to change the meeting template.
- The AI will reprocess the transcript with the correct template focus.

### Recorder does not start with Windows

- Check that the startup shortcut exists at: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MeetingRecorder.lnk`
- Re-run `install.ps1` to recreate it.

---

## 9. Admin Guide (Admin Only)

### Adding a User

**Via the Dashboard:**
1. Log in as admin at notes.example.com
2. Click the purple **Admin** button (top-left)
3. Fill in Email, Name, Password
4. Click **Add** -- the system generates an API key automatically
5. Copy the API key and send it to the new user along with their password

**Via curl:**

```bash
curl -X POST https://notes.example.com/admin/users \
  -H "X-API-Key: YOUR_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email": "newuser@example.com", "name": "New User", "password": "temppass123"}'
```

The response includes the generated `api_key` for the recorder.

### Removing a User

**Via Dashboard:** Admin panel -> click the red **Delete** button next to the user.

**Via curl:**

```bash
curl -X DELETE https://notes.example.com/admin/users/user@example.com \
  -H "X-API-Key: YOUR_ADMIN_API_KEY"
```

### Listing All Users

```bash
curl https://notes.example.com/admin/users \
  -H "X-API-Key: YOUR_ADMIN_API_KEY"
```

### Viewing All Meetings (Cross-User)

On the dashboard, admin users automatically see all meetings across all users. The API also supports the `all=true` query parameter:

```bash
curl "https://notes.example.com/sessions?all=true" \
  -H "X-API-Key: YOUR_ADMIN_API_KEY"
```

### Changing API Keys

API keys are stored in `/opt/meeting-notes/backend/users.json`. To regenerate a key, delete and re-create the user, or edit the JSON directly and restart the service.

### Restarting the Service

```bash
ssh root@YOUR_VPS_IP
systemctl restart meeting-notes
systemctl status meeting-notes
```

### Checking Logs

```bash
# Live logs
journalctl -u meeting-notes -f

# Last 100 lines
journalctl -u meeting-notes -n 100
```

### Version Management

```bash
cd /opt/meeting-notes

# See all versions
git tag

# Check current version
git describe --tags

# Rollback to a previous version
git checkout v1.2
systemctl restart meeting-notes
```

### Webhook Notifications

To get notified (e.g., in Slack) when a meeting is processed, set `WEBHOOK_URL` in `/opt/meeting-notes/backend/.env`:

```
WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

The webhook sends a JSON POST with:
```json
{
  "session_id": "abc123...",
  "title": "Q2 Planning Meeting",
  "meeting_type": "team",
  "user_email": "user@example.com",
  "dashboard_url": "https://notes.example.com",
  "timestamp": "2026-04-16T10:30:00Z"
}
```

### Auto-Export to Folder

To automatically copy `.md` meeting notes to a folder (e.g., a synced drive), set in `.env`:

```
EXPORT_FOLDER=/path/to/shared/folder
```

---

## 10. API Reference

**Base URL:** `https://notes.example.com`

**Authentication:** All endpoints (except `/health` and `/login`) require one of:
- Header `X-API-Key: mn_your_key_here` (for recorder/scripts)
- Header `Authorization: Bearer <session_token>` (for dashboard/browser)

---

### Health Check

```
GET /health
```

No authentication required. Returns server status.

**Response:**
```json
{"status": "ok", "ts": "2026-04-16T10:30:00+00:00"}
```

---

### Authentication

#### Login

```
POST /login
Content-Type: application/json
```

**Body:**
```json
{"email": "user@example.com", "password": "yourpassword"}
```

**Response:**
```json
{
  "token": "abc123...",
  "email": "user@example.com",
  "name": "User Name",
  "role": "member"
}
```

#### Get Current User

```
GET /me
Authorization: Bearer <token>
```

**Response:**
```json
{"email": "user@example.com", "name": "User Name", "role": "member"}
```

#### Logout

```
POST /logout
Authorization: Bearer <token>
```

---

### Submit Transcript

```
POST /transcribe
Content-Type: application/json
X-API-Key: mn_your_key
```

**Body:**
```json
{
  "transcript": "Full text of the meeting transcript...",
  "duration_sec": 3600,
  "timestamp": "2026-04-16T10:00:00Z",
  "meeting_type": "auto",
  "session_id": "optional_custom_id",
  "hint_title": "Optional title hint"
}
```

**Response (202 Accepted):**
```json
{
  "session_id": "abc123def456",
  "status": "accepted",
  "poll_url": "/results/abc123def456"
}
```

---

### Upload Audio File

```
POST /upload-audio
Content-Type: multipart/form-data
X-API-Key: mn_your_key
```

**Parameters:**
- `file` (required): Audio file (WAV, MP3, M4A, OGG, WEBM, FLAC, AAC)
- `meeting_type` (optional, query): Template to use (default: `auto`)

**Response (202 Accepted):**
```json
{
  "session_id": "abc123def456",
  "status": "transcribing",
  "poll_url": "/results/abc123def456"
}
```

---

### Get Results

```
GET /results/{session_id}
X-API-Key: mn_your_key
```

**Response:**
```json
{
  "session_id": "abc123",
  "timestamp": "2026-04-16T10:00:00Z",
  "duration_sec": 3600,
  "transcript": "...",
  "meeting_type": "team",
  "summary": "## Meeting Title\n...",
  "actions": "## A. Executive Snapshot\n...",
  "timing": {"total_sec": 12.5},
  "status": "complete",
  "user_email": "user@example.com",
  "tags": ["important", "follow-up"]
}
```

---

### Get Markdown

```
GET /results/{session_id}/markdown
X-API-Key: mn_your_key
```

Returns the full meeting notes as plain-text Markdown.

---

### Download Markdown File

```
GET /results/{session_id}/download
X-API-Key: mn_your_key
```

Returns a Markdown file with `Content-Disposition: attachment` header.

---

### Export as PDF

```
GET /results/{session_id}/pdf
X-API-Key: mn_your_key
```

Returns a formatted PDF file.

---

### Re-Analyze with Different Template

```
POST /results/{session_id}/reprocess?meeting_type=client
X-API-Key: mn_your_key
```

**Response (202 Accepted):**
```json
{"session_id": "abc123", "status": "reprocessing"}
```

---

### Update Tags

```
PUT /results/{session_id}/tags
Content-Type: application/json
X-API-Key: mn_your_key
```

**Body:**
```json
{"tags": ["important", "client", "q2-planning"]}
```

**Response:**
```json
{"session_id": "abc123", "tags": ["important", "client", "q2-planning"]}
```

---

### List Sessions

```
GET /sessions?limit=100&search=keyword&tag=important&all=false
X-API-Key: mn_your_key
```

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 100 | Max results (1-500) |
| `search` | string | -- | Full-text search |
| `tag` | string | -- | Filter by tag |
| `all` | bool | false | Admin only: show all users meetings |

**Response:**
```json
{
  "sessions": [
    {
      "session_id": "abc123",
      "title": "Q2 Planning",
      "timestamp": "2026-04-16T10:00:00Z",
      "duration_sec": 3600,
      "meeting_type": "team",
      "status": "complete",
      "tags": ["important"]
    }
  ]
}
```

---

### Get Available Templates

```
GET /templates
X-API-Key: mn_your_key
```

**Response:**
```json
{
  "templates": {
    "client": "Client Meeting -- focus on commitments, deliverables, next steps...",
    "sales": "Sales Call -- focus on objections, buying signals...",
    "technical": "Technical Discussion -- focus on architecture decisions...",
    "investment": "Investment Meeting -- focus on metrics, valuation...",
    "standup": "Standup -- brief per-person format...",
    "team": "Team Meeting -- focus on project updates, decisions...",
    "interview": "Interview -- focus on candidate assessment...",
    "generic": "Generic -- general-purpose meeting notes"
  }
}
```

---

### Admin: Create User

```
POST /admin/users
Content-Type: application/json
X-API-Key: ADMIN_API_KEY
```

**Body:**
```json
{"email": "new@example.com", "name": "New User", "password": "temppass123"}
```

**Response:**
```json
{
  "email": "new@example.com",
  "name": "New User",
  "role": "member",
  "api_key": "mn_generated_key_here"
}
```

---

### Admin: List Users

```
GET /admin/users
X-API-Key: ADMIN_API_KEY
```

---

### Admin: Delete User

```
DELETE /admin/users/{email}
X-API-Key: ADMIN_API_KEY
```

---

## 11. Security and Privacy

### Audio Privacy

- **Audio never leaves your laptop.** The recorder captures and transcribes locally using Whisper AI.
- Only the **text transcript** is sent to the VPS over HTTPS.
- A backup `.wav` file is saved locally at `%APPDATA%\MeetingRecorder\` for safety, but is never uploaded.

### Cloud AI Usage

- **Gemini 2.5 Flash** (Google) receives the text transcript for summary generation and classification.
- **DeepSeek V3** (DeepSeek) receives the text transcript for action item extraction.
- Neither service receives audio, video, or metadata about participants beyond what is in the transcript text.

### Dashboard Security

- The dashboard at notes.example.com is protected by email/password authentication.
- Each session uses a random 256-bit token.
- Users can only see their own meetings. Admin can see all.

### Recorder Authentication

- Each user has a unique API key (starts with `mn_`) stored in their local `config.json`.
- API keys are generated server-side and should not be shared between users.

### Infrastructure

- All data is stored on a **private VPS** -- not on any third-party cloud storage service.
- Transcripts and meeting notes are stored at `/var/meeting-notes/` on the VPS.
- The VPS runs behind HTTPS (TLS encryption in transit).
- No data is shared with third parties beyond the AI API calls described above.

### Best Practices

- Do not share your API key with others.
- Change your dashboard password after first login.
- If you suspect your API key is compromised, ask Admin to regenerate it.
- The recorder logs are stored locally at `%APPDATA%\MeetingRecorder\recorder.log` -- these contain timestamps and metadata but not transcript content.

---

## Questions?

Contact **Admin User** (admin@example.com) -- system administrator.

Dashboard: [notes.example.com](https://notes.example.com)
Health check: [notes.example.com/health](https://notes.example.com/health)
