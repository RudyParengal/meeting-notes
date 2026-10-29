# Credits & Acknowledgements

Notewell is built on excellent open-source work. Below are the libraries, models and projects it uses, and the ideas it took from other meeting-notes tools.

---

## Recorder (on your computer)

| Library | Purpose | Licence |
|---------|---------|---------|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Speech-to-text on your own computer (CTranslate2) | MIT |
| [Whisper models](https://huggingface.co/Systran) (Systran) | The speech model weights: Tiny, Base, Small and larger | MIT |
| [PyAV](https://github.com/PyAV-Org/PyAV) | Reads imported audio files | BSD-3-Clause |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Speaker separation ("Speaker 1, 2, 3") on the CPU, no PyTorch | Apache-2.0 |
| [pyannote segmentation 3.0](https://huggingface.co/pyannote/segmentation-3.0) (ONNX export by sherpa-onnx) | Finds where each speaker talks | MIT |
| [3D-Speaker CAM++](https://github.com/modelscope/3D-Speaker) (ONNX export by sherpa-onnx) | Tells voices apart | Apache-2.0 |
| [soundcard](https://github.com/bastibe/SoundCard) | Captures the call and the microphone (WASAPI loopback) | BSD-3-Clause |
| [pystray](https://github.com/moses-palmer/pystray) | Tray icon and menu | LGPL-3.0 |
| [Pillow](https://github.com/python-pillow/Pillow) | Draws the tray icon | HPND |
| [psutil](https://github.com/giampaolo/psutil) | Meeting auto-detect, one copy at a time | BSD-3-Clause |
| [numpy](https://numpy.org) | Audio maths, silence detection | BSD-3-Clause |
| [requests](https://github.com/psf/requests) | Sends transcripts to a team server | Apache-2.0 |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | Downloads the speech model | Apache-2.0 |

## Server

| Library | Purpose | Licence |
|---------|---------|---------|
| [FastAPI](https://github.com/fastapi/fastapi) | Web framework | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | Web server | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | Checks incoming requests | MIT |
| [openai](https://github.com/openai/openai-python) | Talks to any OpenAI-compatible AI (Mistral, Groq, Ollama, OpenAI, Anthropic, xAI…) | Apache-2.0 |
| [httpx](https://github.com/encode/httpx) | Webhooks | BSD-3-Clause |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | Reads `.env` settings | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | Audio file uploads | Apache-2.0 |
| [WeasyPrint](https://github.com/Kozea/WeasyPrint) + [Markdown](https://github.com/Python-Markdown/markdown) | Optional server-side PDF export | BSD-3-Clause |

## Dashboard (in the browser)

| Library | Purpose | Licence |
|---------|---------|---------|
| [marked](https://github.com/markedjs/marked) | Shows the notes' Markdown as formatted text | MIT |
| [DOMPurify](https://github.com/cure53/DOMPurify) | Cleans that text before it is shown | Apache-2.0 / MPL-2.0 |

Both are bundled in `dashboard/vendor/` (see its README). The rest is plain JavaScript.

## Windows app and installer

| Tool | Purpose | Licence |
|---------|---------|---------|
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | Packs the app into `Notewell.exe` | GPL-2.0 with an exception that allows packing any program |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Builds `Notewell-Setup.exe` | Inno Setup licence (free) |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/) | The app window, with Qt WebEngine (Chromium) inside | LGPL-3.0; shipped as separate files in the install folder, so they can be replaced |

## AI services you can choose

Notewell does not include any AI service. You pick one on the Settings screen: Mistral (free plan), Groq, Ollama (on your computer), OpenAI, Anthropic, xAI or any OpenAI-compatible service. Only the **text** transcript is sent; audio never leaves your computer.

---

## Ideas taken from other projects

**No code was copied** from any of these; the ideas are credited here.

### [Meetily](https://github.com/Zackriya-Solutions/meetily) — Zackriya Solutions (MIT)
A local-first meeting-notes desktop app. Notewell 2.1 took from it:
- Local-first audio capture — transcription on your computer, not a server
- Meeting type templates — a different summary prompt per kind of meeting
- A desktop app with an installer, rather than scripts to run
- The layout: a sidebar (Home, Record, Meetings, Settings) and transcript beside notes
- A Record screen with timer, sound-level meter, microphone and language choice
- Generate again with another AI model or template, plus a box for extra context
- Notes you can edit by hand
- Importing an audio file recorded elsewhere
- Picking the AI provider on a Settings screen, including local models
- A consent reminder on the Record screen

### [ghost-pepper](https://github.com/matthartman/ghost-pepper) — Matt Hartman
- Meeting auto-detection from running meeting apps (Zoom, Teams, Webex…)
- Microphone and call audio kept apart to label "Me" vs everyone else
- Custom vocabulary passed to Whisper
- Silence detection and start/stop sounds
- Filler-word clean-up

### [OpenOats](https://github.com/yazinsai/OpenOats) — Yazin Alirhayim
- Tagging and filtering meetings
- Markdown export with structured sections
- Webhooks and auto-export to a folder (Obsidian, Dropbox)

## What we did NOT take

- Real-time suggestion engine and knowledge-base search (OpenOats)
- Speaker separation that needs a GPU (pyannote.audio with PyTorch) — Notewell uses the same segmentation model through sherpa-onnx on the CPU instead
- Push-to-talk dictation, on-screen text capture

---

## Special thanks

- **OpenAI** for the original Whisper research and model release
- **SYSTRAN** for faster-whisper
- **k2-fsa** for sherpa-onnx and its ONNX speaker models
- **Anthropic's Claude** (used as a development assistant through Claude Code)

## Reporting an attribution issue

If you maintain a project credited here and the attribution is wrong or not enough, please open an issue and it will be fixed.
