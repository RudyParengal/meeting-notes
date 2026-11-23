# Changelog

## 2.7.8: Documents in the side menu (23 Nov 2026)

### Added
- **Documents page in the side menu, under Screen recordings.** Add a PDF (even a phone or Drive scan) and Notewell reads every page on your server so an AI can use it. Each document shows how far the reading has got; when it is ready you can download it as a .md file, or all ready documents as one .md. Remove puts it in the server's bin. The same list is on the website and the phone page.

## 2.7.7: Page titles no longer show address values (23 Nov 2026)

### Fixed
- **A page title no longer shows the values in its address.** A tab that has not loaded yet uses its address as its title, so a title could show `?code=...` in full while the address beside it showed `?code=*`. The laptop app now masks titles too; the Chrome helper already does since its reload.

## 2.7.6: Chrome helper lines reach recordings (23 Nov 2026)

### Fixed
- **Page, console and network lines from the Chrome helper now reach the recording.** Before, the app turned away every request from the helper, so a recording made in Chrome had no Steps, Console or Network on its share page. After installing, click reload on the helper's card in chrome://extensions once.
- **A minimised "Review recording" window comes back by itself.** Before, if it was minimised the recording waited unsent with nothing on screen to say so. It still sends nothing until you click Send.
- **Web pages can no longer make up console lines** that look like they came from the helper.

### Changed
- The screen recorder's code is split into smaller files. Nothing it does has changed.

## 2.7.5: Screen recordings that show what happened (22 Nov 2026)

### Added
- **A screen recording now notes what you did, with times.** Which window was open, where you clicked and, with the Chrome helper, which page, console errors and failed or slow web requests. The website turns this into a summary and steps to reproduce.
- **Recorder controls:** pause and resume, mute the microphone, record a whole screen, one window or a dragged area, draw marks that fade, hide part of the screen, take a screenshot with a hotkey, an optional camera bubble and an optional "keep the last 2 minutes" buffer.
- **Check before it uploads:** review, rename, trim or discard a recording first.
- **Chrome helper** (in the `browser-extension` folder, loaded by hand): sends page, console and network lines to the recorder only while a recording runs. It never reads cookies, form fields or request contents.

### Fixed
- **An upload that makes no progress now stops** instead of retrying for ever.

## 2.7.4: Sharper text in the app window (22 Nov 2026)

### Fixed
- **Text in the app window no longer looks jagged on a 100% screen.** The window now smooths text edges the same way Chrome does, so it looks like the website.

## 2.7.3: One Share box for screen recordings (22 Nov 2026)

### Changed
- **Screen recordings use the same Share box as the website and phone.** One box holds the link switch, a password and an end date, with one Save. Before, the app only had a button to switch the link on or off.
- **If a password is too short or a date is wrong, the box says why.** Before, it would have said the server could not be reached.

## 2.7.2 — Plainer boxes, messages and empty lists (20 Nov 2026)

### Changed
- **No more browser pop-ups.** Moving a meeting to the trash, deleting it forever and renaming now use Notewell's own boxes, and the buttons say what they do ("Move to trash", "Keep", "Delete forever").
- **When something does not load, it says so and offers Try again.** Before, a failed list just stayed empty or showed an error number.
- **Empty lists explain themselves.** "No meetings yet", "Nothing matches" (with Clear search) and "The trash is empty" each say what to do next.
- **The trash button is always visible** on a meeting, not only when the mouse is over it.
- **No text smaller than 12 px.**

## 2.7.0 — New look, and laptop fixes (20 Nov 2026)

What changed since 2.6.1: the app now looks like the website, plus the laptop fixes below.

### Changed
- **The app looks like the website.** It uses the same design file (`notewell-ui.css`): the purple Notewell button, the website's sidebar, cards, labels, messages and dark mode. Nothing moved and nothing was removed; every button does what it did.
- **Easier with the keyboard.** Meetings in the sidebar and the tag filters can be reached with Tab and opened with Enter. Messages that say something did not work now show a red dot.
- **The dictation bubble, the screen recording flashes and the live transcript window use the website's colours.** Red while the mic is on, amber for "1 minute left", violet while writing, green when a link is copied.
- **Plainer tray menu.** "Record a meeting", "Stop recording the meeting", "Start recording by itself", "Test the sound (5 seconds)", "Listen to", "Open Notewell", "Quit Notewell". The same words as in the app.
- **Hands-free dictation can run for 20 minutes** (was 5). At 19 minutes the badge says "1 minute left"; at 20 it types what it heard by itself, so nothing is lost. Holding the keys still stops after 5 minutes, in case a key is stuck down.
- **A stray key no longer throws a dictation away.** Before, any other key pressed while holding Ctrl+Shift cancelled it, so a slip or Ctrl+Shift+V lost everything said. Now a key within the first second is still a shortcut (Ctrl+Shift+T works as before); after that the dictation carries on and types when you let go. Esc still throws it away. The log names the key that did it.
- **Auto-detect now catches Google Meet, Teams and Zoom in Chrome or Edge, and no longer records when Teams or Zoom is merely open.** A meeting is now the meeting app or browser tab *and* that program using the microphone. Before, a Google Meet call was missed, an open Teams chat or an email about a Zoom meeting counted, and bringing another tab to the front mid-call stopped the recording; now it keeps recording until the call lets go of the microphone. The log says what it found, never the window title.
- **The logs no longer fill up with sound card warnings.** Sound is now collected in half-second pieces instead of hundredths of a second, so a busy moment no longer drops sound, and any gaps are counted in one line a minute instead of one line each.
- **Meeting recordings and transcript copies on the laptop are kept 7 days (was 30), and only cleared once the server has the finished note.** If the server cannot be reached, or does not have the note, nothing is cleared. Before, recordings went after 30 days whether or not the note had arrived, and transcript copies were never cleared. Anything cleared goes to the Recycle Bin, so it can be put back. A list of sent meetings (`sent-meetings.jsonl`) records which note each recording belongs to; recordings from before 2.7.0 are not on it and are left alone.
- **Old backup copies are trimmed.** Each install copies `dictation.db`, `config.json` and `linked-server.json` first, and nothing removed the copies (9 of `dictation.db` by 20 Nov). Notewell now keeps the newest 2 of each every time it starts and sends the rest to the Recycle Bin.
- **A meeting rescued after a crash is now archived like any other**, instead of its recording staying in the Notewell folder for good.
- **Logs stay small.** notewell.log, recorder.log and screen.log each start a fresh file at 5 MB and keep the 3 newest old ones.

### Added
- **The version is shown and sent.** The bottom of Settings and the tray tooltip say "Notewell 2.7.0". Every request to the linked server, the sign-in and each meeting sent carry the version (`X-Notewell-Version`, and `User-Agent: Notewell/2.7.0`), so the server's log shows which version each laptop runs.
- `Notewell.exe --sound-check` records 2 minutes of a test tone and writes how many sound gaps it found to `sound-check.txt` in the Notewell folder.

## 2.6.1 — Updated add-on software (unreleased)

### Changed
- **Add-on software updated** to current versions (21 packages, small updates only). One of them, a tracing library the app carries, had a published fault that let a crafted request slow the app down; now fixed. No change to how Notewell looks or works.
- Held back on purpose: the window toolkit (PySide6 stays 6.9.1), the video reader (av stays 18; 19 breaks transcription) and the speaker-separation library (sherpa-onnx stays 1.x; 2.0 is a big change). Each needs its own test run.

## 2.6.0 — Ctrl+Shift+R, and the screen tells you what is happening (unreleased)

### Changed
- **Record your screen with Ctrl+Shift+R** instead of Ctrl+Alt+R. Anyone who chose their own keys (`screen_hotkey`) keeps them. While Notewell runs, Ctrl+Shift+R no longer hard-reloads a page in Chrome or Edge; Ctrl+F5 still does.

### Added
- **A moment of colour on the screen**: red **Recording** when it starts, blue **Stopped** when you stop, green **Link copied — ready to paste** when the link is on the clipboard, orange **Saved on this PC** when it has to wait for the internet. None of it appears in the recording, a screenshot or a screen share, and clicks go straight through it.
- **Dictation and Screen recordings have their own pages in the laptop app.** Screen recordings lists every recording on your account, with Copy link, Watch and a switch to turn each link off or on. Dictation holds its settings, your words and the history.
- **Everything stays up to date by itself, on the laptop, the website and the phone.** Each page checks every 5 seconds and redraws only when something changed: a new meeting, a new dictation, a new recording, a link switched off.

## 2.5.0 — Record your screen and get a share link (2026-11-18)

### Added
- **Press Ctrl+Alt+R to record your screen and your voice. Press it again to stop.** A few seconds later a link is on the clipboard and a notice says so. Anyone with the link can watch it on your server's website without an account. Also in the tray menu ("Record screen") and on the dashboard.
- **Two screens:** a small window asks which one. One screen: it records that one.
- **No internet?** The recording waits on the laptop and goes up when the connection is back; the link is copied then.
- **Notewell's own windows never appear in a recording** (this also hides the dictation badge from screen shares and screenshots).
- **A warning before recording a password manager or a banking page.** You can still record.
- Recording the screen runs on its own, so it cannot stop a meeting recording or dictation running at the same time.

## 2.4.5 — Hands-free dictation, and no more flashing badge (2026-11-17)

### Added
- **Tap Ctrl+Shift once to dictate hands-free.** No need to hold the keys: tap them, speak, tap them again and the words are typed. Esc throws the recording away. Holding the keys works as before. Shortcuts such as Ctrl+Shift+T used while it listens do not stop it.

### Fixed
- **The listening badge no longer flashes when you use a Ctrl+Shift shortcut** (Ctrl+Shift+T, Ctrl+Shift+Arrow and so on). The badge now waits a third of a second before showing; the microphone still starts straight away, so no words are lost.

## 2.4.4 — Dictation keys are now Ctrl+Shift (2026-11-17)

### Changed
- **Hold Ctrl+Shift to dictate, instead of Ctrl+Win.** The Windows key could open Windows Search, which then caught the words instead of the program you were typing in. The new keys do not use the Windows key at all.
- Anyone who chose their own keys in Settings keeps them.
- Shortcuts that start with Ctrl+Shift (for example Ctrl+Shift+Arrow to select a word) still work; the listening badge may flash for a moment and close without typing anything.

## 2.4.3 — Dictation hears a normal speaking voice (2026-11-17)

### Fixed
- **A normal voice on a laptop's built-in microphone is no longer "Didn't hear anything".** 2.4.2 still asked for a loudness that this kind of microphone only reaches when you speak up. The line is now just "is the microphone picking anything up at all". Telling your voice apart from a fan or keyboard noise is done by the speech model's own speech detector, which is now switched on, so it still does not invent words from noise.

## 2.4.2 — Dictation hears a quiet voice (2026-11-17)

### Fixed
- **Quiet speech is no longer thrown away as "Didn't hear anything".** Notewell used to judge the average loudness of the whole time you held the keys, so the half-second the microphone takes to start, and the pauses between your words, could pull a quiet voice under the line. It now judges your loudest moments. A bump of the desk in an otherwise silent room still counts as silence.

## 2.4.1 — Fewer "server not reached" messages (2026-11-17)

### Fixed
- **A connection cut on the way to your server is now tried again.** On some PCs a burst of new connections is sometimes dropped before it reaches the server, which showed "Your server did not answer just now (server not reached)" even though everything had worked. Reads are tried up to twice more; an upload is only sent again if it never left the PC, so a meeting is never sent twice.

## 2.4.0 — Sign in to your notes server; dictation follows your account (2026-11-17)

### Added
- **Settings → Your account.** Sign in once with your email and password; this PC gets its own pass, which the server keeps scrambled and can switch off on its own. Your password is not kept on the PC. The old settings file is kept as a backup.
- **Your dictation words and history follow your account.** Words you add or remove, and what you dictate, reach your other signed-in devices. A server without accounts is skipped quietly.

## 2.3.2 — Dictation shows when it is really listening (2026-11-17)

### Fixed
- **Notewell no longer opens the microphone when it starts.** 2.3.1 did this to make the first dictation quicker; on some laptops it froze the built-in microphone until it recovered by itself a few minutes later. That warm-up is gone.

### Changed
- **The bubble says "Wait, mic starting..." until sound is really coming in, then "Listening".** Windows can take 4 to 7 seconds to start a microphone that has been idle; start speaking when the bubble turns to "Listening" and nothing is lost.
- The log now says how long the microphone took to start for each dictation.

## 2.3.1 — The first dictation keeps its first words (2026-11-17)

### Fixed
- **The first dictation after Notewell starts no longer loses its opening words.** On some laptops Windows takes 4 to 7 seconds to open the microphone the first time a program asks for it, and well under a second after that. Notewell now opens the microphone once, briefly, when dictation switches on, so every dictation starts listening straight away.

### Good to know
- You will see the Windows microphone icon for a few seconds when Notewell starts. That is this warm-up; nothing is recorded or kept.

## 2.3.0 — Dictation (2026-11-17)

### New
- **Talk instead of type, anywhere in Windows.** Hold **Ctrl+Win**, speak, let go: the words appear where your cursor is — in Notepad, a browser, an email. Measured on a laptop: the text arrives about half a second after you let go (0.5 s in Notepad, 0.6 s in a browser text box).
- **It stays on your computer.** The speech is turned into text on the laptop itself, with the small "base" speech model. Nothing is sent anywhere.
- **Paste the last one again** with **Alt+Shift+V** — handy when the text went to the wrong window.
- **Your own word list.** Add the right spelling of a name or term, and what it tends to be heard as; Notewell swaps it in and counts how often it helped. Edit it in **Settings → Dictation**.
- **A history of what you said**, kept in Notewell's data folder and shown in Settings.
- **An optional tidy-up** (removes "um", fixes punctuation). **Off unless you turn it on.** It only ever uses AI services that do not train on what you send; when it is off, dictation works with no internet at all.

### Security
- **It never types into a password box or a password manager.** The text is kept in the history instead.
- **The small "listening" badge is hidden from screen shares and recordings**, so the people on your call do not see it.

### Good to know
- What was on your clipboard before is put back afterwards.
- Dictation does not interrupt a meeting recording: both can use the microphone at the same time.
- Change the keys, or switch dictation off completely, in **Settings → Dictation**.

## 2.2.1 — A forgotten recording stops by itself (2026-11-16)

### Fixed
- **A recording you forget to stop now stops by itself** after 15 minutes with no sound on either the call or the microphone, and tells you so. Before, it ran until you stopped it — one ran for 23 hours and filled 10 GB. A single click or bump does not count as sound; two seconds in a row do. Change the wait with `auto_stop_silence_minutes` in the recorder's `config.json` (`0` = never stop by itself).
- **Recovering a very long unfinished recording works.** Recovery used to load the whole recording into memory and fail on anything many hours long. It now reads a minute at a time, keeps the parts with sound and leaves out any silence longer than the wait above. A recording with no sound at all is kept on disk, not sent as an empty meeting.
- **Finished recordings are no longer left behind.** If you started a new recording while the last one was still being written up, the old one's working files (0.2–0.8 GB each) stayed on disk for good. Each recording now clears its own files.

### Good to know
- Music or a video playing for hours counts as sound, so that recording will not stop by itself.
- If the computer sleeps for longer than the wait while recording, the recording stops when it wakes.

## 2.2.0 — Its own window, keys locked away (2026-10-29)

**Notewell no longer needs Microsoft Edge.** It has its own window, with its own icon on the taskbar.

### New
- **Notewell's own window** (Qt), not a browser app. The taskbar shows Notewell, and opening it again brings the same window back.
- **Link a server of your own** (optional). Put its address and key in `%USERPROFILE%\Notewell\linked-server.json` and every note on that server is copied to this PC once a minute, with tags and trash kept in step both ways. Meetings are sent to that server first; if it does not answer, they are kept here and sent later. Nothing on this PC is ever deleted by a sync.

### Security
- **Keys on disk are locked to your Windows account** (DPAPI): the app's own key, your AI key and a linked server's key. Keys saved by 2.1 are locked the first time 2.2 starts. The recorder's `config.json` no longer holds any key.
- **The app answers only requests addressed to `127.0.0.1` or `localhost`**, so a web page cannot reach it by pointing its own name at your computer. Uploads with no stated size, or over the limit, are refused before they are read.
- **Links in notes open only if they are web or email links.** A `file://` or similar link could otherwise start a program.
- **Releases**: every build step is pinned to an exact version, the build has read-only access, and each release lists the installer's SHA-256 with a signed build record (`gh attestation verify`).

### Changed
- Parts of Qt the app never uses (about 100 MB) are left out of the install.

## 2.1.0 — Notewell (2026-10-29)

**Now a Windows app with an installer.** Download `Notewell-Setup-2.1.0.exe`, double-click, and it runs — no Python, no command line.

### New
- **One app, one window.** Recorder, server and dashboard in a single program, opened in its own window. A tray icon by the clock; opening it a second time just brings the window back.
- **Choose your AI on the Settings screen.** Mistral (free plan) first, then Groq, Ollama (on your computer), OpenAI, Anthropic (Claude), xAI (Grok) or any OpenAI-compatible service. "Get a free key" link and a "Test without saving" button.
- **New layout** (ideas taken from [Meetily](https://github.com/Zackriya-Solutions/meetily)): Home, Record, Meetings and Settings in a sidebar; transcript and notes side by side.
- **Record screen** with a timer, sound-level meter, microphone and language choice, and a reminder to tell people they are being recorded.
- **Generate again** with a different AI model or meeting template, and a box for extra context for the AI.
- **Edit notes** by hand, add tags, and filter by tag.
- **Import an audio file** (a recording made elsewhere) and get notes from it.
- **Downloads** as Markdown, plain text or PDF (PDF uses the print window when the server cannot make one itself).
- **Installer** for Windows: no admin rights needed, optional desktop icon and start-with-Windows. Uninstalling keeps your meetings.
- **Guide for AI agents** to install it for you: [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md).

### Fixed
- Importing an audio file failed with PyAV 19 — now pinned below 19, with a test that fails on 19.
- An imported file lost its transcript when no AI was set up — the transcript is now kept so you can press Generate later.
- The "Action plan" heading appeared twice in some notes.
- Tags you typed did not appear in the tag filter.
- Downloads were all named `meeting.md` — they now use the meeting's title.

### Changed
- `scripts/setup-env.py` (team server) lists Mistral first and uses Groq's current model.

## 2.0.0

- Any OpenAI-compatible AI provider; sign-in always on; audio saved to disk while recording so a crash loses nothing; speaker separation on your own computer; live transcript.

## 1.3.6

- Recorder starts reliably at sign-in, and says so if it is already running.

## 1.3.5

- First public release.
