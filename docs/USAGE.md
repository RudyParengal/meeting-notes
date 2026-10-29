# Using Meeting Notes

## Recording a meeting

1. Find the **blue circle** in the system tray (bottom-right; on Windows 11 it may be behind the **^** arrow).
2. Right-click → **Start Meeting**. You will hear a short beep.
3. Have your meeting. Your microphone and the other people (whatever plays through your speakers or headset) are captured separately.
4. Right-click → **Stop Meeting**. Another beep, then a notification when the note is ready — the time depends on your AI provider and the length of the call.
5. Open the dashboard (**Open Dashboard** in the same menu) to read it.

You do not need to keep the meeting app in front, and it works with anything that plays sound on Windows: Zoom, Teams, Webex, Slack huddles, Discord, a browser tab, a phone call through your PC.

### Tray menu

| Item | What it does |
|---|---|
| **Start Meeting / Stop Meeting** | Begin or end recording |
| **Auto-detect meetings** | Tick to start automatically when a meeting app opens (off by default — see the recording-law note in the README) |
| **Live transcript window** | Show or hide the always-on-top window with the text as people speak |
| **Test Audio (5s)** | Records 5 seconds and tells you whether sound was heard — use it before an important call |
| **Audio Source** | Pick a specific speaker/headset if the default is wrong |
| **Recover unfinished meeting** | Appears only if a recording was interrupted (crash, power cut). Click to turn the saved audio into a note |
| **Open Dashboard** | Opens your notes in the browser |
| **Settings (config.json)** | Opens the settings file |
| **View logs** | Opens the recorder log |
| **Quit** | Stops the recorder (a meeting in progress is stopped and sent first). The watchdog restarts it about 10 seconds later — handy for reloading settings. To stop it for good, run `windows-recorder\uninstall.ps1` |

### If the computer crashes mid-meeting

Audio is written to disk as it is captured. Start the recorder again (it does so at login) and choose **Recover unfinished meeting**. Everything up to the crash is transcribed and sent.

### Quiet or one-sided recordings

If the recorder hears silence for 10 seconds it warns you — usually the wrong **Audio Source**, or the call is playing through a device that is not selected. Run **Test Audio** and pick the right device.

---

## The dashboard

Open `http://127.0.0.1:8766` (or your server's address) and log in.

- **Admin:** email `admin@localhost` (or whatever `ADMIN_EMAIL` you set) and the server's master key as the password.
- **Teammates:** the email and password the admin gave them.

### Each note contains

| Part | What you get |
|---|---|
| **Title and type** | The AI picks a type (client, sales, team, technical, investment, standup, interview, generic) and tailors the summary to it |
| **Summary** | Key points, decisions, open questions |
| **Action items** | Who has to do what, and by when, where it was said |
| **Transcript** | The full text, with speaker labels |

### Things you can do

| Action | How |
|---|---|
| Search | Type in the search box — it looks through summaries, action items and transcripts |
| Tag | Add tags to a note and filter by them later |
| Download | Markdown or plain text from the dashboard. PDF is available from the API if you install the optional extras ([CONFIGURATION.md](CONFIGURATION.md#optional-features)) |
| Re-run the AI | **Reprocess** a note after changing provider, model or meeting type |
| Delete | Moves a note to **Trash**; you can restore it. Only **Purge** removes it for good |

The dashboard lists only **your own** meetings — the admin's list too. The admin account can open any note by its link and can delete users, so treat it accordingly.

### Speaker labels

| Label | Meaning |
|---|---|
| **Me** | Your microphone |
| **Others** | Everyone else on the call, when speaker separation is off |
| **Speaker 1, 2, 3 …** | Everyone else, separated by voice, when speaker separation is on (`-SpeakerSeparation` at install, or `diarize_others` in `config.json`) |

Speaker separation is a best guess from voice alone. It is good with distinct voices and a clean line, weaker with several similar voices or people talking over each other. Speakers are anonymous: it does not know their names.

---

## Adding teammates (admin)

1. Log in as admin → click **Admin**.
2. Add the person's email, a name and a password (10 characters minimum).
3. The dashboard shows their **API key once**. Copy it now — it is stored only as a one-way hash and cannot be shown again.
4. Give them the key for their recorder, and the email + password for the dashboard.

| Need | Do |
|---|---|
| Lost key | **New key** next to their name. The old key stops working immediately |
| Someone leaves | **Delete** next to their name |

Each person's recorder should use **their own key**. Then you can change one person's access without touching anyone else, and notes are filed under the right person.

---

## Without the tray app

Anything that can make an HTTP request can send a meeting. (The server's built-in interactive API page is switched off on purpose; the endpoints are listed in [ARCHITECTURE.md](ARCHITECTURE.md#the-api).) A minimal example:

```bash
curl -X POST http://127.0.0.1:8766/transcribe \
  -H "X-API-Key: $MN_API_KEY" -H "Content-Type: application/json" \
  -d '{"transcript":"Alex: Let us ship Friday.\nSam: I will send the proposal.","duration_sec":60,"meeting_type":"auto"}'
```

It answers immediately with a `session_id`; the AI works in the background. Fetch the finished note with `GET /results/<session_id>`.

Got a recording but no recorder? If you installed the optional extras (`backend/requirements-optional.txt`), `POST /upload-audio` transcribes an audio file on the server.

Problems? → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
