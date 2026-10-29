# Troubleshooting

## Start here: run the doctor

```powershell
venv\Scripts\python scripts\doctor.py            # checks the server and the recorder
venv\Scripts\python scripts\doctor.py --e2e      # also pushes a test meeting through your AI provider
```

Each line says `PASS`, `WARN`, `FAIL` or `SKIP` and what it actually saw. The first `FAIL` is almost always the cause. Add `--json` to get the same thing in machine-readable form (useful to paste to an AI assistant). The doctor never prints a key.

> A `SKIP` is not a pass. It means that check could not run (for example, no key was found), so look at why.

## Where the logs are

| What | Where |
|---|---|
| Recorder | `%USERPROFILE%\MeetingRecorder\recorder.log` (tray → **View logs**) |
| Watchdog (starts/restarts the recorder, and why) | `%USERPROFILE%\MeetingRecorder\supervisor.log` |
| Anything the recorder printed as it died | `%USERPROFILE%\MeetingRecorder\recorder-stderr.log` |
| Server | The window you started it in · `docker logs meeting-notes` · `journalctl -u meeting-notes -n 50` |

Read `supervisor.log` first for a recorder that will not stay up: it records the exit code and the reason each time.

---

## The recorder

### I can't find the tray icon

1. Click the **^** arrow at the right of the taskbar. Windows 11 hides new icons there; drag the blue circle out to keep it visible.
2. Wait about 30 seconds after logging in — the first start loads the speech engine.
3. Check it is running: `venv\Scripts\python scripts\doctor.py --recorder`.
4. Start it by hand (this is exactly what Windows does at login):

   ```powershell
   explorer.exe "C:\path\to\meeting-notes\windows-recorder\start-supervisor.vbs"
   ```

5. Still nothing? Read `supervisor.log` and `recorder-stderr.log`.

### "CONFIG PROBLEM" in supervisor.log / the recorder exits straight away

The settings file is wrong, and the recorder says so rather than crashing in a loop. The message names the problem:

| Message | Fix |
|---|---|
| No API key | Put the server's key in `config.json` as `"api_key"` |
| `api_url` must start with http:// or https:// | Fix the address in `config.json` |
| Plain http:// to another computer is refused | Use `https://`, or if you truly accept the risk set `"allow_insecure_http": true` |
| `config.json is not valid JSON` | A typo (often a missing comma or quote). The file was **not** changed — fix it, or delete it to start from defaults |

After fixing, tray → **Quit** (if it is running) or wait: the watchdog retries every 30 minutes after a config problem, or run `start-supervisor.vbs` again.

### It started at login but doesn't any more after a restart

Programs started from some editors, terminals or desktop apps can end up in a sandbox where files are written to a different place than normal, so everything seems fine until you reboot. Always start the recorder with `explorer.exe "…\start-supervisor.vbs"` (as above) or let the Startup shortcut do it. `install.ps1` does this for you.

### The note never appears on the dashboard

| Check | What to look for |
|---|---|
| `recorder.log` | A line like `upload` with a `session_id` = it was sent. An error line says why |
| **"upload returned 200 but not JSON"** | The address in `api_url` is answered by something other than Meeting Notes — typically a login page from a proxy in front of the server. Point `api_url` at the real server; the recorder deliberately refuses redirects so it cannot be fooled |
| **401 / 403** | Wrong or replaced key. Compare `api_key` with the server's key (or ask the admin for a **New key**) |
| Connection refused / timed out | The server is not running or not reachable. `doctor.py --server http://… ` from the laptop |
| Audio is kept | While a meeting is not confirmed by the server, its audio stays in `%USERPROFILE%\MeetingRecorder`. Tray → **Recover unfinished meeting** sends it again |

### Silent, quiet or one-sided recordings

1. Tray → **Test Audio (5s)** while something is playing. It reports OK, faint or silent.
2. Tray → **Audio Source** and choose the device the call actually plays through (headset vs speakers).
3. Bluetooth headsets can switch devices when a call starts; re-run the test after connecting.

### Speaker separation is wrong

- Everyone shows as "Others": it is off, or the models are missing. Re-run `install.ps1 -SpeakerSeparation`, then check `"diarize_others": true`.
- Two people merged into one, or one person split into two: tune `diarize_threshold` in `config.json` (higher = fewer speakers; try 0.8–1.0) or set `diarize_max_speakers`. It is a guess from voice alone — similar voices and people talking over each other are hard for any such system.
- If it fails for any reason the transcript is still sent, labelled "Others". Speaker labels never block a note.

### Auto-detect didn't start (or started when I didn't want it)

It is off unless you enable it (`-AutoDetect` at install, or the tray checkbox). When on, it needs the meeting app to be active for 15 seconds. Edit `auto_detect_processes` in `config.json` to add or remove programs.

### Antivirus flags it

The recorder captures audio and watches for meeting apps, which some security products treat as suspicious behaviour. The code is all in this repository for you to read. If your product blocks it, add the `windows-recorder\venv` folder (or the program `pythonw.exe` inside it) as a trusted application — a folder-only exclusion is often ignored by behaviour-based scanners.

### It won't install

| Message | Fix |
|---|---|
| Python 3.10+ was not found | Install from python.org and tick **Add python.exe to PATH**; open a new PowerShell window |
| Installing requirements.txt failed | Run the pip command it prints, to see the real error. Usually a network or proxy problem |
| Execution policy error | Run it as `powershell -ExecutionPolicy Bypass -File …\install.ps1` |
| Downloading the speaker models failed | Network blocked. Retry, or install without `-SpeakerSeparation` (you can add it later) |

---

## The server

### It won't start

It prints one clear sentence and exits with code 78 (a configuration problem). Typical ones:

| Message mentions | Fix |
|---|---|
| `MEETING_NOTES_API_KEY` | Missing, too short (under 24 characters) or still a placeholder. Run `python scripts/setup-env.py` or set a real value |
| No API key for the 'summary' stage | Set `LLM_API_KEY`, or point `LLM_BASE_URL` at a local model |
| users.json is damaged | The file is kept untouched. Fix it or restore a backup; the message gives the path |
| Address already in use | Another program has port 8766. Change `PORT`, or stop the other one |

### Notes come back empty, cut off, or "generic"

| Symptom | Cause and fix |
|---|---|
| "Summary generation failed: … insufficient_quota" | Your provider account has run out of credit or hit its plan limit. Top up or switch provider; the server deliberately does not retry this |
| "429" / "503" / "high demand" | The provider is busy. The server already retries 3 times; **Reprocess** the note later |
| "model not found" / 404 | `LLM_MODEL` is wrong or retired for that provider. Check their current model list |
| Unauthorized / 401 from the provider | `LLM_API_KEY` is wrong for `LLM_BASE_URL` (a key from one provider sent to another) |
| Note ends mid-sentence, or empty with a "reasoning model" hint | Raise `LLM_MAX_TOKENS` (try 16384) |
| Every meeting classified "generic" | Check the server log for the `classify` stage; the classifier failed and fell back. Usually the same cause as above |
| Action items say "unavailable for this meeting: …" | That step failed; the reason is printed. The summary and transcript are unaffected. **Reprocess** after fixing |

After fixing the provider, open the note and **Reprocess** it — the transcript is kept, so nothing is lost.

### Login problems

- Admin: email = `ADMIN_EMAIL` (default `admin@localhost`), and the password is whatever `MEETING_NOTES_API_KEY` is set to.
- "Too many attempts": after 8 wrong passwords an address is blocked for 15 minutes. Wait, or restart the server.
- Teammate forgot their password: admin deletes and re-adds them (their old notes stay with the email).
- Logged out every 12 hours: that is `TOKEN_TTL_HOURS`.

### The dashboard is blank or won't load

Open the browser's developer console. The dashboard ships its own scripts and sends a strict Content-Security-Policy; a proxy or browser extension that injects scripts will be blocked. Load it directly at `http://127.0.0.1:8766` to rule out the proxy.

### PDF or audio upload says "501 … not installed"

Those are optional. See [CONFIGURATION.md → Optional features](CONFIGURATION.md#optional-features).

### Docker

```bash
docker compose ps
docker logs meeting-notes --tail 30
```

Status `unhealthy` or `Exited (78)`: read the log — it names the setting to fix.

---

## Still stuck?

Run `python scripts/doctor.py --e2e --json` and open an issue with the output and the last 30 lines of the relevant log. **First read both for anything secret.** The doctor and the logs avoid printing keys, but transcripts can contain private conversations.
