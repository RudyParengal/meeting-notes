# Credits & Acknowledgements

This project stands on the shoulders of excellent open-source work. Below is a full list of the libraries, models, and projects that made it possible, along with the feature-level inspirations we drew from similar meeting-notes tools.

---

## Core dependencies — Windows recorder

| Library | Purpose | License | Link |
|---------|---------|---------|------|
| [**faster-whisper**](https://github.com/SYSTRAN/faster-whisper) | Fast CTranslate2-based Whisper implementation for local transcription | MIT | `github.com/SYSTRAN/faster-whisper` |
| [**Systran/faster-distil-whisper-large-v3**](https://huggingface.co/Systran/faster-distil-whisper-large-v3) | The specific Whisper model weights we use by default (1.5 GB, multilingual) | MIT | `huggingface.co/Systran/faster-distil-whisper-large-v3` |
| [**soundcard**](https://github.com/bastibe/SoundCard) | Cross-platform audio capture (WASAPI loopback on Windows) | BSD-3-Clause | `github.com/bastibe/SoundCard` |
| [**pystray**](https://github.com/moses-palmer/pystray) | System tray icon & menu on Windows | LGPL-3.0 | `github.com/moses-palmer/pystray` |
| [**Pillow**](https://github.com/python-pillow/Pillow) | Tray icon drawing | HPND | `github.com/python-pillow/Pillow` |
| [**psutil**](https://github.com/giampaolo/psutil) | Process inspection (meeting auto-detect, single-instance lock) | BSD-3-Clause | `github.com/giampaolo/psutil` |
| [**numpy**](https://numpy.org) | Audio buffer math, RMS silence detection | BSD-3-Clause | `numpy.org` |
| [**requests**](https://github.com/psf/requests) | HTTP client for backend uploads | Apache-2.0 | `github.com/psf/requests` |
| [**huggingface_hub**](https://github.com/huggingface/huggingface_hub) | Whisper model download | Apache-2.0 | `github.com/huggingface/huggingface_hub` |

## Core dependencies — VPS backend

| Library | Purpose | License | Link |
|---------|---------|---------|------|
| [**FastAPI**](https://github.com/tiangolo/fastapi) | Web framework (API endpoints + auto-generated `/docs`) | MIT | `github.com/tiangolo/fastapi` |
| [**Uvicorn**](https://github.com/encode/uvicorn) | ASGI server | BSD-3-Clause | `github.com/encode/uvicorn` |
| [**Pydantic**](https://github.com/pydantic/pydantic) | Request body validation | MIT | `github.com/pydantic/pydantic` |
| [**google-genai**](https://github.com/googleapis/python-genai) | Google Gemini API client (meeting summaries) | Apache-2.0 | `github.com/googleapis/python-genai` |
| [**openai**](https://github.com/openai/openai-python) | Used with DeepSeek's OpenAI-compatible endpoint for action items | Apache-2.0 | `github.com/openai/openai-python` |
| [**httpx**](https://github.com/encode/httpx) | Async HTTP (webhooks) | BSD-3-Clause | `github.com/encode/httpx` |
| [**python-dotenv**](https://github.com/theskumar/python-dotenv) | Load `.env` config | BSD-3-Clause | `github.com/theskumar/python-dotenv` |
| [**markdown**](https://github.com/Python-Markdown/markdown) | Markdown → HTML for PDF export | BSD-3-Clause | `github.com/Python-Markdown/markdown` |
| [**weasyprint**](https://github.com/Kozea/WeasyPrint) | HTML → PDF rendering | BSD-3-Clause | `github.com/Kozea/WeasyPrint` |
| [**python-multipart**](https://github.com/andrew-d/python-multipart) | Audio file uploads | Apache-2.0 | `github.com/andrew-d/python-multipart` |

## Dashboard (browser)

| Library | Purpose | License | Link |
|---------|---------|---------|------|
| [**marked.js**](https://github.com/markedjs/marked) | Client-side Markdown rendering | MIT | `github.com/markedjs/marked` |

No other frontend dependencies — the dashboard is a single static HTML file with vanilla JavaScript.

## External services (cloud APIs)

| Service | Use | Pricing | Link |
|---------|-----|---------|------|
| **Google Gemini 2.5 Flash** | Meeting summary, meeting-type classification | Very cheap, generous free tier | [`aistudio.google.com`](https://aistudio.google.com) |
| **DeepSeek V3 (`deepseek-chat`)** | Action items with Eisenhower Matrix | ~$0.27/M input, $1.10/M output | [`platform.deepseek.com`](https://platform.deepseek.com) |

Both services only receive the **text transcript** — raw audio never leaves the local laptop.

---

## Feature inspirations

We researched three excellent open-source meeting-notes projects and adopted ideas from each. **No code was copied**; the implementations here are independent, but the concepts are credited below.

### [ghost-pepper](https://github.com/matthartman/ghost-pepper) — by Matt Hartman
A macOS-native voice-to-text dictation and meeting assistant. We took inspiration from:
- **Meeting auto-detection** by scanning for running meeting-app processes (Zoom, Teams, Webex, etc.)
- **Dual-stream audio** (mic + system output) as a zero-ML way to do speaker diarization ("Me" vs "Others")
- **Custom vocabulary / misheard corrections** — pass domain terms as Whisper's `initial_prompt`
- **Silence detection** — alert the user if no audio is being captured
- **Sound effects** — subtle beeps on recording start/stop for confirmation
- **Filler word cleanup** — post-processing pass to remove "um", "uh", "you know"

### [Meetily](https://github.com/Zackriya-Solutions/meetily) — by Zackriya Solutions
A full-stack local meeting-notes app with a Tauri desktop UI. We took inspiration from:
- **Local-first audio capture** — transcription happens on the user's machine, not the server
- **Meeting type templates** — different summary prompts per meeting type

### [OpenOats](https://github.com/yazinsai/OpenOats) — by Yazin Alirhayim
A sophisticated macOS meeting assistant with real-time suggestion engine. We took inspiration from (but did not implement the complex real-time parts):
- **Session tagging** and filtering
- **Markdown export spec** with YAML frontmatter and structured sections
- **Webhook integration** for post-processing hooks
- **Auto-export to folder** for Obsidian / Dropbox sync
- **Scratchpad** concept (on the roadmap, not yet implemented)

## What we explicitly did NOT take

To avoid scope creep, the following features from the above projects are deliberately **not** included:
- Real-time RAG-powered suggestion engine (OpenOats)
- ML-based speaker diarization requiring a GPU (pyannote.audio)
- Knowledge base / vector search
- Push-to-talk dictation
- On-screen OCR context

These are great features but would have required far more complexity than the team-of-six use case this tool is designed for.

---

## Special thanks

- **OpenAI** for the original Whisper research and model release
- **SYSTRAN** for the distil-whisper adaptation
- **Anthropic's Claude** (used as a development assistant via Claude Code during build-out)

---

## Reporting an attribution issue

If you maintain one of the projects credited above and feel the attribution is incorrect or insufficient, please open an issue — I'm happy to fix it.
