# Changelog

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
