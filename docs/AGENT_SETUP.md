# Installing Notewell with an AI agent

Copy everything in the box below into Claude Code, Cursor, ChatGPT (with a terminal) or any agent that can run commands on your Windows computer.

The agent installs and checks everything. The only thing it will ask you to do is paste your AI key into the Settings screen — **never paste a key into the chat**.

---

````text
Install Notewell, the meeting note-taker from https://github.com/RudyParengal/meeting-notes,
on this Windows computer, and prove it works. Use PowerShell.

Rules:
- Never ask me for an API key in chat and never print one. I paste my key into
  Notewell's Settings screen myself.
- Do not change system or security settings. Do not turn off antivirus.
- After each step, say in one plain sentence what you checked and what it returned.

1. Check: Windows 10/11 64-bit, at least 2 GB free on C:.
     [Environment]::Is64BitOperatingSystem
     (Get-PSDrive C).Free / 1GB

2. Download the newest installer to a new, empty folder:
     $dir = Join-Path $env:TEMP "notewell-download"; New-Item -ItemType Directory -Force $dir | Out-Null
     $rel = Invoke-RestMethod https://api.github.com/repos/RudyParengal/meeting-notes/releases/latest
     $asset = $rel.assets | Where-Object name -like "Notewell-Setup-*.exe" | Select-Object -First 1
     Invoke-WebRequest $asset.browser_download_url -OutFile (Join-Path $dir $asset.name)
   Tell me the file name and size before running it.

3. Install quietly for the current user (no admin prompt), with a desktop icon:
     Start-Process (Join-Path $dir $asset.name) -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/TASKS=desktopicon' -Wait
   It installs to %LOCALAPPDATA%\Programs\Notewell. A quiet install does not start the app.

4. Start it:
     Start-Process "$env:LOCALAPPDATA\Programs\Notewell\Notewell.exe"

5. Wait up to 3 minutes for it to answer. The first start downloads the speech model.
     $nw = "$env:USERPROFILE\Notewell"; $health = $null
     foreach ($i in 1..180) {
       Start-Sleep 1
       if (Test-Path "$nw\port") {
         $port = (Get-Content "$nw\port").Trim()
         try { $health = Invoke-RestMethod "http://127.0.0.1:$port/health"; break } catch {}
       }
     }
     $health                                                 # expect status = ok
   If it does not answer, show me the last 30 lines of $nw\notewell.log and of
   $nw\recorder\recorder.log, and stop.

6. Check the recorder can hear this computer. Use the app's own key file. It is
   locked to this Windows user; unlock it into a variable in Windows PowerShell
   (powershell.exe) and never print it:
     Add-Type -AssemblyName System.Security
     $saved = (Get-Content "$nw\app.key").Trim()
     $key = if ($saved -like 'dpapi:*') { [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect(
              [Convert]::FromBase64String($saved.Substring(6)), $null, 'CurrentUser')) } else { $saved }
     $h = @{'X-API-Key' = $key}
     Invoke-RestMethod "http://127.0.0.1:$port/recorder" -Headers $h           # expect available = true
     Invoke-RestMethod "http://127.0.0.1:$port/recorder/devices" -Headers $h   # list the devices
     Invoke-RestMethod "http://127.0.0.1:$port/settings/ai" -Headers $h        # ready = false until I save a key

7. Tell me: "Notewell is installed and running. Open its window, go to Settings, choose
   Mistral (free plan), click 'Get a free key from Mistral', paste the key, press
   'Test without saving', then Save." Wait until I say I have done it.

8. Check the AI is ready:
     (Invoke-RestMethod "http://127.0.0.1:$port/settings/ai" -Headers $h).ready   # expect True
   Then tell me to record a 30-second test on the Record screen and check that the note
   appears on the Home screen.

Finish with a short table: each step, what you checked, what it returned.
````

---

## If you would rather not use an agent

Follow the five steps in the [README](../README.md#install-on-windows-about-5-minutes).

## For a shared team server instead

An agent can also follow [INSTALL.md](INSTALL.md) section C (Linux server with HTTPS). Give it the same rules: no keys in chat, report each check. The server's own check is:

```bash
venv/bin/python scripts/doctor.py --e2e      # every line should say PASS
```

## Silent install options (for IT teams)

| Option | Effect |
|---|---|
| `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` | No windows at all |
| `/TASKS=desktopicon` | Desktop icon |
| `/TASKS=desktopicon,startup` | Desktop icon, and start with Windows |
| `/DIR="D:\Apps\Notewell"` | Install somewhere else |

Uninstall quietly: `"%LOCALAPPDATA%\Programs\Notewell\unins000.exe" /VERYSILENT`. Meetings in `%USERPROFILE%\Notewell` are kept.
