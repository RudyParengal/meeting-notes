# Changelog

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
