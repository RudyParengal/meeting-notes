# Changelog

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
