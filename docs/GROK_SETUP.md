# Let Grok install Notewell for you

For Notewell 2.7.8 on Windows 10 or 11. Written for someone who has never installed an app before.

**How to use it:** copy everything inside the box below and paste it into Grok (or any AI assistant) as your first message. Nothing to fill in.

- If the assistant can run commands on your computer, it does the installing itself. You only click "Allow", make a free Mistral account, and press record.
- If it is a chat on a website, it guides you click by click.
- It never asks you to type a password or key into the chat.

Prefer a shorter prompt for a technical agent (Claude Code, Cursor)? Use [AGENT_SETUP.md](AGENT_SETUP.md).

````text
Hello. Please install the Notewell app on my Windows computer and set it up for me from start to finish.

ABOUT ME
I am not technical at all. Think of me as a 90-year-old grandparent using a computer. I can click, type a little and read big plain words. I do not know what a "terminal", "browser tab" or "API key" is unless you explain it.

WHAT NOTEWELL IS
Notewell is a free app that records my calls and meetings, writes down what was said, and gives me a short summary and a to-do list. It can also type what I say into any program (dictation). It comes from https://github.com/RudyParengal/meeting-notes

=====================================================================
HOW TO TALK TO ME (follow these every single time)
=====================================================================
1. ONE instruction per message. Never two. Wait until I reply "done" (or tell you what I see) before the next one.
2. Use short sentences and everyday words. No technical words. If you must use one, explain it in the same sentence.
3. Tell me exactly what I will SEE on the screen, where it is (top, bottom, left, right, middle), its colour if you know it, and exactly what to CLICK. Example: "At the bottom right of the screen there is a small blue button that says Next. Click it once."
4. Put the words I must click or look for in CAPITALS so I can find them.
5. Before each part, tell me in one sentence what we are about to do and roughly how long it takes.
6. After each part, tell me what is now done, in one sentence, so I feel the progress. Example: "Part 2 of 7 done: Notewell is now on your computer."
7. If I say something looks different, or I am lost, do not repeat yourself. Ask me to take a photo of the screen with my phone and send it to you, then guide me from what you see.
8. Be patient and kind. Never rush me. If I make a mistake, tell me it is fine and how to undo it.
9. NEVER ask me to type commands, code or anything into a black or blue text window. That is your job, not mine.
10. NEVER ask me to type, paste or say any password, code or key in this chat. If I start to, stop me and tell me not to.

=====================================================================
YOUR SAFETY RULES
=====================================================================
- Never print, show, log or save any key, password or code. Never put one in this chat.
- Do not switch off my antivirus or change any security setting yourself. If something needs allowing, tell me exactly what to click and let me do it.
- Download only from github.com/RudyParengal/meeting-notes. Nothing else.
- Do not delete anything on my computer.
- Explain every warning before it appears, so I am not frightened by it.

=====================================================================
FIRST: WORK OUT WHICH WAY YOU WILL DO THIS
=====================================================================
Check whether YOU can run PowerShell commands directly on MY Windows computer (not on a computer of your own in the cloud). Test it: run `$env:LOCALAPPDATA; [Environment]::OSVersion.VersionString` and check it returns a real Windows folder and "Microsoft Windows".

- If YES: use WAY A. You do every technical step yourself. I only do the few things a person must do (clicking "Allow", signing up for a free account, pressing record).
- If NO (for example you are a chat on a website and cannot touch my computer): use WAY B. You guide me click by click through every step. Then follow every "WAY B" part below, and skip the "WAY A" parts.

Tell me in one plain sentence which way we are using. Do not explain the technical reason.

Then tell me, before anything else, what I need near me:
- my computer plugged in to power and connected to the internet
- my mobile phone (a free account may send me a text message with a code)
- my email address, and the password for my email (I will type it myself, never in this chat)
- about 30 to 45 minutes. I can stop at any time and come back.

=====================================================================
PART 1 OF 7: CHECK THE COMPUTER (about 1 minute)
=====================================================================
WAY A: run these yourself and check:
  [Environment]::Is64BitOperatingSystem          # must be True
  [math]::Round((Get-PSDrive C).Free/1GB,1)       # must be at least 3 (gigabytes free)
  [Environment]::OSVersion.Version.Build          # 19041 or higher = Windows 10 or 11
If any check fails, tell me in plain words what is missing (for example "your computer needs a little more free space") and stop.

WAY B: ask me to click the Windows START button (bottom left, or bottom middle on Windows 11), type ABOUT, and click "About your PC". Ask me to read you the lines "System type" and "Edition" (or send a phone photo). It must say "64-bit" and "Windows 10" or "Windows 11".

=====================================================================
PART 2 OF 7: DOWNLOAD AND INSTALL NOTEWELL (about 5 minutes)
=====================================================================
WAY A: do all of this yourself, in Windows PowerShell (powershell.exe):

  $dir = Join-Path $env:USERPROFILE "Downloads\Notewell-install"
  New-Item -ItemType Directory -Force $dir | Out-Null
  $ua  = @{ 'User-Agent' = 'notewell-installer' }
  $rel = Invoke-RestMethod https://api.github.com/repos/RudyParengal/meeting-notes/releases/latest -Headers $ua
  $exe = $rel.assets | Where-Object { $_.name -like 'Notewell-Setup-*.exe' } | Select-Object -First 1
  $sha = $rel.assets | Where-Object { $_.name -like 'Notewell-Setup-*.exe.sha256' } | Select-Object -First 1
  $file = Join-Path $dir $exe.name
  $ProgressPreference = 'SilentlyContinue'
  Invoke-WebRequest $exe.browser_download_url -OutFile $file -Headers $ua -UseBasicParsing
  $shaText = (Invoke-WebRequest $sha.browser_download_url -Headers $ua -UseBasicParsing).Content
  if ($shaText -is [byte[]]) { $shaText = [Text.Encoding]::ASCII.GetString($shaText) }
  $want = ($shaText.Trim() -split '\s+')[0]
  $have = (Get-FileHash $file -Algorithm SHA256).Hash
  $want -ieq $have                                 # must be True

The installer is about 215 MB, so the download can take a few minutes. While it downloads, tell me "I am downloading Notewell now. This can take a few minutes. You do not need to do anything."
If the last line is not True: stop, delete nothing, and tell me "The download did not arrive correctly. Please try again later." Do not install it.

Before installing, make sure Notewell is not already running (close it if it is: `Get-Process Notewell -ErrorAction SilentlyContinue | Stop-Process`), so the quiet install does not stop halfway.
Then install it quietly, for this user only (no administrator password needed), with a desktop icon:

  Start-Process $file -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/TASKS=desktopicon' -Wait
  Test-Path "$env:LOCALAPPDATA\Programs\Notewell\Notewell.exe"     # must be True

If my antivirus (for example Kaspersky, Norton, McAfee or Windows Security) shows a message about Notewell: tell me it is expected because the app is new and not yet "signed" by a big company, and tell me to click ALLOW (or "Allow", "Trust", "Run anyway", whatever button lets it continue). Never tell me to switch the antivirus off.

WAY B: guide me one step at a time:
  1. Open the internet (the Microsoft Edge or Google Chrome icon). Tell me what the icon looks like: Edge is a blue and green swirl, Chrome is a red, yellow and green circle.
  2. Click in the long white bar at the very top, type exactly: github.com/RudyParengal/meeting-notes/releases/latest and press the ENTER key.
  3. Scroll down with the mouse wheel to the word ASSETS. Click the file whose name starts with "Notewell-Setup-" and ends with ".exe" (NOT the one ending in ".sha256", NOT "Source code").
  4. Wait for the download to finish (about 215 MB, a few minutes). If the internet program says the file "isn't commonly downloaded" or "could harm your device", that is expected for a new app: click the three dots (...) next to it, then KEEP, then SHOW MORE, then KEEP ANYWAY.
  5. Click OPEN FILE (or open the DOWNLOADS folder and double-click the Notewell-Setup file).
  6. A blue box may say "Windows protected your PC". That is expected for a new app. Click the small words MORE INFO, then the button RUN ANYWAY.
  7. The installer opens. On each page click NEXT. On the page with tick boxes, leave "Put a Notewell icon on the desktop" ticked. Then click INSTALL, wait, and click FINISH (leave "Open Notewell now" ticked).
  8. If the antivirus asks about Notewell, tell me to click ALLOW.

When done, tell me: "Part 2 of 7 done: Notewell is now on your computer."

=====================================================================
PART 3 OF 7: START NOTEWELL AND LET IT GET READY (about 5 minutes)
=====================================================================
WAY A: start it yourself:
  explorer.exe "$env:LOCALAPPDATA\Programs\Notewell\Notewell.exe"
Then wait up to 5 minutes until it answers:
  $nw = "$env:USERPROFILE\Notewell"; $ok = $false
  foreach ($i in 1..150) { Start-Sleep 2
    if (Test-Path "$nw\port") { $port = (Get-Content "$nw\port").Trim()
      try { if ((Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 3).status -eq 'ok') { $ok = $true; break } } catch {} } }
  $ok                                              # must be True
If it is not True after 5 minutes, read only the last 30 lines of "$nw\notewell.log" and "$nw\recorder\recorder.log" (never the whole files), tell me in plain words what went wrong, and stop.

The first start downloads the speech part (about 500 MB) in the background, so the very first time it can take several minutes before recording works. Tell me: "Notewell is starting. The first time it downloads its listening part, which takes a few minutes. You do not need to do anything."

WAY B: tell me: "Notewell opens in its own window. If it did not, double-click the NOTEWELL icon on your desktop. The first time, it needs a few minutes to get ready. Please wait 5 minutes, then tell me what you see."

Also tell me: there is now a small Notewell icon near the clock at the bottom right of the screen (it may be hidden behind the small up-arrow ^ there). That icon means Notewell is running.

=====================================================================
PART 4 OF 7: LET NOTEWELL HEAR THE MICROPHONE (about 2 minutes)
=====================================================================
WAY A: check the Windows microphone setting yourself (read only, do not change it):
  (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone' -EA SilentlyContinue).Value
  (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged' -EA SilentlyContinue).Value
Both should say "Allow" (an empty answer is also fine). If either says "Deny", guide me through the WAY B steps below.

WAY B (or if WAY A found "Deny"): guide me:
  1. Click START, type MICROPHONE, click "Microphone privacy settings".
  2. Make sure "Microphone access" is ON (the switch is blue or dark).
  3. Make sure "Let apps access your microphone" is ON.
  4. Scroll down: make sure "Let desktop apps access your microphone" is ON.
  5. Close that window with the X at the top right.

If at any time Windows or the antivirus asks "Allow Notewell to use your microphone?", tell me to click YES or ALLOW.

=====================================================================
PART 5 OF 7: GET A FREE "AI KEY" SO NOTEWELL CAN WRITE SUMMARIES (about 15 minutes)
=====================================================================
First explain to me in plain words: "Notewell writes the words down by itself. To write the short summary and the to-do list, it uses a free service called Mistral. Mistral gives you a free 'key', which is like a long password that lets Notewell use it. You will create a free Mistral account. It costs nothing and no card is needed. If it ever asks for a bank card, stop and tell me."

Then guide me ONE step at a time (adapt to what I see; ask for a phone photo if unsure):
  1. Open the internet program and go to: console.mistral.ai/api-keys
  2. Click SIGN UP (or "Continue with Google" if I use Gmail; that is the easiest). I type my email and my own password myself.
  3. If it sends a code to my email: open my email, find the message from Mistral, and type the code into the Mistral page.
  4. If it asks me to choose a plan: choose the FREE one (it may be called "Experiment"). Never choose a paid plan, never type a bank card.
  5. If it asks for my phone number: type it, wait for the text message on my phone, and type the code from the text into the page.
  6. If I see a setting about letting Mistral use my data to train or improve its models, switch it OFF (it may be under ADMIN, then PRIVACY).
  7. On the "API keys" page, click CREATE NEW KEY. For the name, type: Notewell. Leave everything else as it is. Click CREATE.
  8. The long key appears ONCE. Click the COPY button next to it. Tell me: "Do not type or paste the key here in our chat. Just say done."

WAY A: when I say "done", save the key into Notewell yourself, WITHOUT ever showing it. Write this into a file named nw-save-key.ps1 in my TEMP folder and run it with:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\nw-save-key.ps1"
Run it in one go (it needs the key and the app password in the same run). Do not add any line that prints $key or $appkey.

  $ErrorActionPreference = 'Stop'
  Add-Type -AssemblyName System.Security
  $nw = "$env:USERPROFILE\Notewell"; $port = (Get-Content "$nw\port").Trim()
  $key = (Get-Clipboard -Raw); if ($key) { $key = $key.Trim() }
  if (-not $key -or $key.Length -lt 20 -or $key -match '\s') { 'NO-KEY-ON-CLIPBOARD'; exit 1 }
  $s = (Get-Content "$nw\app.key" -Raw).Trim()
  $appkey = if ($s.StartsWith('dpapi:')) { [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String($s.Substring(6)), $null, 'CurrentUser')) } else { $s }
  $h = @{ 'X-API-Key' = $appkey }
  $body = @{ provider='mistral'; base_url='https://api.mistral.ai/v1'; model='ministral-14b-latest'; api_key=$key } | ConvertTo-Json
  Invoke-RestMethod "http://127.0.0.1:$port/settings/ai" -Method Put -Headers $h -ContentType 'application/json' -Body $body | Out-Null
  $body = @{ provider='mistral'; base_url='https://api.mistral.ai/v1'; model='ministral-14b-latest'; api_key='' } | ConvertTo-Json
  $t = Invoke-RestMethod "http://127.0.0.1:$port/settings/ai/test" -Method Post -Headers $h -ContentType 'application/json' -Body $body
  Set-Clipboard -Value ' '
  $key = $null; $appkey = $null; $h = $null
  "TEST-OK: $($t.ok)"; if (-not $t.ok) { "REASON: $($t.error)" }

Then delete that one script file you made (it holds no key, but tidy up): Remove-Item "$env:TEMP\nw-save-key.ps1"
- "TEST-OK: True" means it worked.
- "NO-KEY-ON-CLIPBOARD": ask me to go back to the Mistral page and click COPY again (or make a new key if the old one is no longer shown), then run the script again.
- "TEST-OK: False" and the reason says the key is wrong or not active: wait one minute and run only the test part again once. If it still fails, ask me to create a new key (step 7 and 8) and run the script again.
- Never use the words "401", "endpoint" or "API" with me. Say "the key did not work yet".

WAY B: guide me to paste the key into Notewell myself:
  1. Open the Notewell window (double-click the NOTEWELL icon on the desktop).
  2. On the left side, click SETTINGS.
  3. In the AI part, choose "MISTRAL (FREE PLAN)" from the list.
  4. Click once inside the white box labelled KEY. Hold the CTRL key (bottom left of the keyboard) and press the V key once. Dots or letters appear; that is the key.
  5. Click TEST WITHOUT SAVING. Wait a few seconds. Ask me what message appears. If it says it worked, click SAVE.
  6. If it says it did not work: wait one minute and click TEST WITHOUT SAVING again. If it still fails, go back to Mistral and create a new key.

When done: "Part 5 of 7 done: Notewell can now write summaries."

=====================================================================
PART 6 OF 7: A 30-SECOND TEST RECORDING (about 5 minutes)
=====================================================================
WAY A first: check the listening part is ready, using the same app-password unlock as in Part 5 inside one script run:
  Invoke-RestMethod "http://127.0.0.1:$port/recorder" -Headers $h      # "available" and "model_ready" must be True
  (Invoke-RestMethod "http://127.0.0.1:$port/settings/ai" -Headers $h).ready   # must be True
If model_ready is still False, wait (check every 30 seconds, up to 10 minutes) and tell me it is still getting ready.

Then guide me (WAY A and WAY B the same):
  1. Open the Notewell window (double-click the NOTEWELL icon on the desktop).
  2. Click the big button RECORD A MEETING (or the big round red dot).
  3. Say a few sentences out loud for about 30 seconds. For example: "This is a test. Tomorrow I will call my daughter and buy bread."
  4. Click STOP RECORDING (or the square stop button).
  5. Wait 1 to 3 minutes. A new note appears in the list on the left. Click it to see what you said and the short summary.

WAY A: confirm it yourself as well: a new note file appears under "$nw\data\notes" (check the newest file's time is within the last 10 minutes). Do not open or show its contents.
If no note appears after 5 minutes: read only the last 30 lines of "$nw\recorder\recorder.log", explain in plain words, and help me fix it (often it is the microphone setting from Part 4).

When it works: "Part 6 of 7 done: Notewell recorded you and wrote your first note."

=====================================================================
PART 7 OF 7: OPTIONAL EXTRAS, THEN FINISH (about 5 minutes)
=====================================================================
Ask me ONE question: "Did Rudy give you a Notewell website account (an email or message with a link to set a password for notes.parengal.com)?"
- If NO or "I don't know": skip this. Tell me everything works on this computer by itself.
- If YES: first, I open that link and choose my own password there (I type it myself, never in this chat). Then guide me:
    1. In Notewell, click SETTINGS, then find YOUR ACCOUNT.
    2. In ADDRESS type: https://notes.parengal.com
    3. Type my email, then my Notewell password (myself), then click SIGN IN. It should say "Signed in".
  This lets me see my notes on the website and my phone, and makes share links for screen recordings.
  WAY A: check `(Invoke-RestMethod "http://127.0.0.1:$port/account" -Headers $h).linked` is True.

WAY A only: ask "Would you like Notewell to start by itself every time the computer starts? (yes or no)"
- If YES: create a shortcut to "$env:LOCALAPPDATA\Programs\Notewell\Notewell.exe" in the folder [Environment]::GetFolderPath('Startup'). Tell me: "Done. Notewell will now open by itself when the computer starts."
WAY B: skip this question. I open Notewell from the desktop icon.

WAY A: finally, put a simple help note on my desktop, a text file named "How to use Notewell.txt", with exactly these lines:
  HOW TO USE NOTEWELL
  1. Double-click the Notewell icon on the desktop.
  2. To record a call or meeting: click RECORD A MEETING. To finish: click STOP RECORDING.
  3. A few minutes later your note appears on the left. Click it to read it.
  4. To type by speaking in any program: hold down CTRL and SHIFT together, speak, then let go.
  5. Always tell the other people on a call that you are recording.
  6. Your notes are kept in the Notewell folder in your user folder. Uninstalling the app does not delete them.

FINISH: tell me, in no more than 5 short lines:
- Notewell is installed and working, and my first note is saved.
- To use it: double-click the Notewell icon on the desktop.
- To record: RECORD A MEETING, then STOP RECORDING.
- To dictate anywhere: hold CTRL and SHIFT, speak, let go.
- Please tell people on a call when you are recording them.

=====================================================================
IF SOMETHING GOES WRONG
=====================================================================
| What I see | What you do |
|---|---|
| "Windows protected your PC" | Tell me: click MORE INFO, then RUN ANYWAY. It is expected for a new app. |
| Antivirus warning about Notewell | Tell me: click ALLOW (or Trust / Run anyway). Never switch the antivirus off. |
| Notewell window does not open | WAY A: start it again with explorer.exe as in Part 3. WAY B: double-click the desktop icon once and wait 1 minute. |
| Notewell opens but recording does nothing | Check Part 4 (microphone). Then wait: the first time it needs a few minutes to download its listening part. |
| Note appears but no summary | The Mistral key is missing or not active yet. Repeat Part 5. |
| Installer says Notewell is still running | WAY A: close Notewell (Stop-Process as in Part 2), then install again. WAY B: right-click the Notewell icon near the clock, click QUIT, then run the installer again. |
| Anything else | Ask me for a phone photo of the screen, then explain in one plain sentence and give one next click. |

Please begin now with "FIRST: WORK OUT WHICH WAY YOU WILL DO THIS".
````
