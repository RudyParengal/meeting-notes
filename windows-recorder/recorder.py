r"""
Meeting Recorder — Windows tray app.

Captures system audio via WASAPI loopback, transcribes with faster-whisper,
and sends the transcript to your Meeting Notes server.

Tray icon menu:
  - Start Meeting          — begin capturing system audio
  - Stop Meeting           — end capture, transcribe, send to your server
  - Auto-detect meetings   — toggle process-based auto-detection (Zoom, Teams, etc.)
  - Open Dashboard         — open your server's dashboard in the browser
  - Settings               — edit config.json
  - Quit

Config lives at: %USERPROFILE%\MeetingRecorder\config.json
Logs at:          %USERPROFILE%\MeetingRecorder\recorder.log
"""
import os, sys, json, time, threading, logging, subprocess, uuid, webbrowser, re, struct
from pathlib import Path
import spool
import diarize as diarization
from datetime import datetime, timezone

# Where the recorder keeps its config, logs and in-progress audio.
# Default: C:\Users\<you>\MeetingRecorder. Override with MEETING_RECORDER_HOME.
#
# This is deliberately in the user profile and NOT under %APPDATA%. Some
# launchers - packaged apps such as the Claude desktop app, sandboxes, some
# installers - silently redirect %APPDATA% writes into a private copy. The
# recorder then reads one config when started from there and another when
# Windows starts it at login, which looks exactly like "works until reboot".
# The profile root is shared by everything.
HERE = Path(__file__).resolve().parent
APPDIR = Path(os.environ.get("MEETING_RECORDER_HOME") or (Path.home() / "MeetingRecorder"))
APPDIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APPDIR / "config.json"
LOG_PATH = APPDIR / "recorder.log"

DEFAULT_CONFIG = {
    "api_url": "http://127.0.0.1:8766",   # your Meeting Notes server
    "api_key": "",                       # your server's MEETING_NOTES_API_KEY
    "allow_insecure_http": False,        # true = allow plain http:// to another computer (not recommended)
    "whisper_model": "distil-large-v3",  # options: tiny, base, small, medium, large-v3, distil-large-v3
    "language": "",                      # "" = detect; or "en", "de", ... (distil-large-v3 is English only)
    "whisper_compute_type": "int8",      # int8 is fastest CPU, use "float16" with GPU
    "whisper_device": "cpu",             # "cpu" or "cuda"
    "sample_rate": 16000,
    "chunk_seconds": 30,
    "auto_detect": False,
    "auto_detect_processes": [
        "Zoom.exe", "Teams.exe", "ms-teams.exe", "zoom.exe",
        "webexmta.exe", "CiscoCollabHost.exe", "slack.exe", "discord.exe",
    ],
    "auto_detect_browser_meeting": True,  # checks tab titles via accessibility API
    "auto_detect_debounce_seconds": 15,   # app must be active this long before auto-start
    "meeting_type_default": "auto",
    "notify_on_complete": True,
    # Phase 2 features
    "dual_stream": True,                  # capture mic + system audio separately for speaker diarization
    "custom_vocabulary": [],              # e.g. ["Acme", "Project Falcon", "Kubernetes"]
    "sound_effects": True,                # play beep on start/stop
    "silence_alert_threshold_rms": 0.001, # RMS below this = silence
    "silence_alert_seconds": 10,          # alert after this many seconds of silence
    "audio_source": "default",            # "default" = current default speaker, or specific device name
    # Phase 3 - crash resilience. Off by default: the in-memory path stays the
    # default until the spool has recorded a real meeting end to end.
    "spool_enabled": True,                # write audio to disk as it is captured: a crash loses nothing
    "diarize_others": False,              # split [Others] into Speaker 1/2/3
    "diarize_max_speakers": -1,           # -1 = decide from the audio
    # Tuned on real meetings - see the table in diarize.py. The library default
    # of 0.5 split a two-person call into eight speakers.
    "diarize_threshold": diarization.DEFAULT_THRESHOLD,
    "diarize_min_talk_seconds": diarization.DEFAULT_MIN_TALK,
    "diarize_threads": 2,                 # keep well under the 16 cores available
    # Live transcript window. Needs spool_enabled - it reads the spool behind
    # the write head. Measured at 8% of one core with these settings.
    "live_transcript": False,             # show the always-on-top window
    "live_model": "base",                 # small and fast; the accurate pass is unchanged
    "live_threads": 4,                    # of 16, so a video call keeps the rest
    "live_window_seconds": 8,
    "live_poll_seconds": 1.5,
    "live_min_rms": 0.005,                # below this, skip: quiet windows hallucinate
    "live_language": "en",                # forced; 8s is too short to detect reliably
    "live_window_topmost": True,
    "archive_backups": True,              # move backup WAVs to MeetingArchive after upload
    "archive_retention_days": 30,         # clear archived WAVs older than this
}

# Where crash-resilient spools live, one directory per session.
SPOOL_ROOT = APPDIR / "spool"

# Liveness files. The recorder and the supervisor each touch their own and read
# the other's. A file's timestamp is the cheapest possible "am I alive" signal:
# no process enumeration, no WMI, nothing that can block or hang.
RECORDER_ALIVE = APPDIR / "recorder.alive"
SUPERVISOR_ALIVE = APPDIR / "supervisor.alive"
SUPERVISOR_LAUNCHER = HERE / "start-supervisor.vbs"
# True when the Notewell desktop app runs this recorder in its own process
# (set by main(hooks=...)). The standalone tray app leaves it False.
EMBEDDED = False
ARCHIVE_ROOT = Path.home() / "MeetingArchive"


def _is_loopback(url: str) -> bool:
    """True only for an address that really is this computer.

    Parsed as an IP address, not matched by prefix: a plain "starts with 127."
    test would accept the ordinary internet hostname 127.example.com.
    """
    import ipaddress
    host = url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    host = host[1:host.index("]")] if host.startswith("[") else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def config_problems(cfg: dict) -> list:
    """Everything that would stop this recorder doing its job, in plain English.

    Empty list = good to go. Checked at start-up so a bad config is a loud,
    explained failure rather than a recorder that quietly does nothing.
    """
    problems = []
    key = (cfg.get("api_key") or "").strip()
    if not key or key.upper().startswith("PASTE") or len(key) < 16:
        problems.append('No API key. Put your server\'s MEETING_NOTES_API_KEY in config.json as "api_key".')
    url = (cfg.get("api_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        problems.append('"api_url" must start with http:// or https://')
    elif url.startswith("http://") and not _is_loopback(url) and not cfg.get("allow_insecure_http"):
        problems.append(
            "api_url uses plain http:// to another computer, which sends your API key and "
            "your meetings unencrypted. Use https:// (see docs/INSTALL.md), or set "
            '"allow_insecure_http": true if this is a private network you trust.')
    return problems


def load_config():
    """Read config.json, adding any settings newer than the file.

    utf-8-sig on purpose: Windows PowerShell 5 writes a byte-order mark at the
    start of "UTF-8" files, which makes a plain json.loads() fail with an error
    that says nothing about the real cause.
    """
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return {**DEFAULT_CONFIG, **KEYS}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        # Never overwrite the user's file with defaults - that would silently
        # erase their API key. Stop, and say exactly what is wrong.
        raise SystemExit(f"{CONFIG_PATH} is not valid JSON ({e}). It has not been changed. "
                         f"Fix it, or delete it to start from the defaults.")
    # Merge in any new defaults
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    cfg.update(KEYS)
    return cfg


# Keys the Notewell app hands over in memory (desktop/notewell.py), so they are
# never written to config.json. Empty for the standalone recorder, which keeps
# its key in the file as it always has.
KEYS = {}


def write_config(cfg):
    CONFIG_PATH.write_text(json.dumps({k: v for k, v in cfg.items() if k not in KEYS}, indent=2),
                           encoding="utf-8")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


log = logging.getLogger("recorder")


# -----------------------------------------------------------------
# Sound effects — programmatic beep/boop via numpy + winsound
# -----------------------------------------------------------------
def _generate_tone(freq_start, freq_end, duration_ms=200, sample_rate=44100, volume=0.3):
    """Generate a sine wave tone (ascending or descending) as PCM16 WAV bytes."""
    import numpy as np
    import io, wave
    n_samples = int(sample_rate * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n_samples, endpoint=False)
    freqs = np.linspace(freq_start, freq_end, n_samples)
    phase = np.cumsum(2 * np.pi * freqs / sample_rate)
    signal = (np.sin(phase) * volume * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(signal.tobytes())
    return buf.getvalue()


def play_start_sound():
    """Ascending beep."""
    try:
        import winsound
        wav_data = _generate_tone(600, 1000, duration_ms=150)
        # Write to temp file since PlaySound needs a file or SND_MEMORY
        tmp = APPDIR / "_start.wav"
        tmp.write_bytes(wav_data)
        winsound.PlaySound(str(tmp), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        log.debug(f"start sound failed: {e}")


def play_stop_sound():
    """Descending boop."""
    try:
        import winsound
        wav_data = _generate_tone(1000, 500, duration_ms=200)
        tmp = APPDIR / "_stop.wav"
        tmp.write_bytes(wav_data)
        winsound.PlaySound(str(tmp), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        log.debug(f"stop sound failed: {e}")


# -----------------------------------------------------------------
# Audio capture via WASAPI loopback (captures system output)
# -----------------------------------------------------------------
class AudioCapture:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.recording = False
        self.frames = []
        self._thread = None
        self._silence_consecutive = 0
        self._silence_alerted = False
        self._audio_source = "default"

    def start(self):
        import soundcard as sc
        import numpy as np
        self.frames = []
        self.recording = True
        self._silence_consecutive = 0
        self._silence_alerted = False

        def _record():
            source = self._audio_source or "default"
            if source == "default" or not source:
                speaker = sc.default_speaker()
            else:
                matches = [s for s in sc.all_speakers() if s.name == source or str(s.name).startswith(source)]
                speaker = matches[0] if matches else sc.default_speaker()
            log.info(f"Loopback source: {speaker.name}")
            # loopback=True captures system output
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with mic.recorder(samplerate=self.sample_rate, channels=1) as rec:
                log.info(f"Recording system audio at {self.sample_rate} Hz")
                while self.recording:
                    data = rec.record(numframes=self.sample_rate)  # 1-second chunks
                    self.frames.append(data)

        self._thread = threading.Thread(target=_record, daemon=True)
        self._thread.start()

    def stop(self):
        import numpy as np
        self.recording = False
        if self._thread:
            self._thread.join(timeout=3)
        if not self.frames:
            return None, 0
        audio = np.concatenate(self.frames, axis=0).flatten().astype(np.float32)
        duration = len(audio) / self.sample_rate
        log.info(f"Captured {duration:.1f}s of audio")
        return audio, duration


# -----------------------------------------------------------------
# Dual-stream audio capture (mic + system = "Me" vs "Others")
# -----------------------------------------------------------------
class DualAudioCapture:
    """Captures system audio AND microphone input in separate streams.
    System audio -> "Others" (remote participants).
    Microphone -> "Me" (local user).
    Falls back to single-stream if mic is unavailable.
    """

    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.recording = False
        self.system_frames = []
        self.mic_frames = []
        self._system_thread = None
        self._mic_thread = None
        self._mic_available = True
        self._system_failed = None  # set if system audio thread crashes
        self._mic_failed = None     # set if mic audio thread crashes
        self._audio_source = "default"  # name of output device to loopback-capture; "default" uses default speaker
        # Loudness of the last 1-second chunk, for the dashboard's level meter.
        self.level_system = 0.0
        self.level_mic = 0.0
        # Silence detection state
        self._silence_consecutive = 0
        self._silence_alerted = False
        self._silence_notify = None  # set by MeetingManager
        # Crash-resilient spool. When enabled, captured audio goes to disk as it
        # arrives, and the in-memory lists hold only what was captured AFTER a
        # spool failure, if one happens. See spool.py.
        self._spool_enabled = False
        self._spool = None
        self.session_dir = None

    def start(self):
        import soundcard as sc
        self.system_frames = []
        self.mic_frames = []
        self.recording = True
        self._spool = None
        self.session_dir = None
        if self._spool_enabled:
            try:
                self.session_dir = SPOOL_ROOT / time.strftime("%Y%m%d-%H%M%S")
                self._spool = spool.SpoolWriter(self.session_dir, self.sample_rate)
                log.info("Spooling audio to %s", self.session_dir)
            except Exception as e:
                # A spool that will not open must not stop the meeting recording.
                log.exception("Could not open spool - recording to memory only: %s", e)
                self._spool = None
                self.session_dir = None
        self._system_failed = None
        self._mic_failed = None
        self._mic_available = True
        self._silence_consecutive = 0
        self._silence_alerted = False

        def _record_system():
            import numpy as np
            try:
                # Resolve which speaker to loopback-capture.
                # cfg["audio_source"] can be "default" or a specific speaker name.
                source = self._audio_source or "default"
                if source == "default" or not source:
                    speaker = sc.default_speaker()
                else:
                    matches = [s for s in sc.all_speakers() if s.name == source or str(s.name).startswith(source)]
                    speaker = matches[0] if matches else sc.default_speaker()
                    if not matches:
                        log.warning(f"Audio source '{source}' not found — falling back to default speaker '{speaker.name}'")
                log.info(f"Loopback source: {speaker.name}")
                loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                with loopback.recorder(samplerate=self.sample_rate, channels=1) as rec:
                    log.info(f"Recording SYSTEM audio at {self.sample_rate} Hz")
                    while self.recording:
                        data = rec.record(numframes=self.sample_rate)  # 1-second chunk
                        self._store("sys", data)
                        # Silence detection on system audio
                        rms = np.sqrt(np.mean(data ** 2))
                        self.level_system = float(rms)
                        self._check_silence(rms)
            except Exception as e:
                log.exception(f"System audio capture failed: {e}")
                # Flag the failure so stop() can report it
                self._system_failed = str(e)
                if self._silence_notify:
                    self._silence_notify("System audio crashed", f"Recording continues on mic only. Error: {type(e).__name__}: {str(e)[:60]}")

        def _record_mic():
            import numpy as np
            try:
                mic = sc.default_microphone()
                with mic.recorder(samplerate=self.sample_rate, channels=1) as rec:
                    log.info(f"Recording MIC audio at {self.sample_rate} Hz")
                    while self.recording:
                        data = rec.record(numframes=self.sample_rate)
                        self._store("mic", data)
                        self.level_mic = float(np.sqrt(np.mean(data ** 2)))
            except Exception as e:
                log.warning(f"Microphone capture failed (falling back to system-only): {e}")
                self._mic_available = False
                self._mic_failed = str(e)
                if self._silence_notify:
                    self._silence_notify("Mic audio crashed", f"Recording continues on system audio only. Error: {type(e).__name__}")

        self._system_thread = threading.Thread(target=_record_system, daemon=True)
        self._mic_thread = threading.Thread(target=_record_mic, daemon=True)
        self._system_thread.start()
        self._mic_thread.start()

    def _store(self, track, data):
        """Put one captured chunk somewhere durable.

        Spool first, while it is working. If a spool write ever fails - a full
        disk, a closed handle - that chunk and every one after it goes to the
        in-memory list instead, so the meeting survives a failing disk rather
        than dying with it. stop() joins the two halves back together.
        """
        if self._spool is not None and self._spool.failed is None:
            if track == "sys":
                self._spool.append_system(data)
            else:
                self._spool.append_mic(data)
            if self._spool.failed is None:
                return
            log.error("Spool write failed (%s) - falling back to memory for the "
                      "rest of this meeting", self._spool.failed)
            if self._silence_notify:
                self._silence_notify(
                    "Disk problem",
                    "Recording continues in memory. Do not restart the app.")
        (self.system_frames if track == "sys" else self.mic_frames).append(data)

    def _check_silence(self, rms):
        """Track consecutive silent chunks and alert once per recording session."""
        threshold = getattr(self, '_silence_threshold', 0.001)
        if rms < threshold:
            self._silence_consecutive += 1
            if self._silence_consecutive >= self._silence_seconds and not self._silence_alerted:
                self._silence_alerted = True
                if self._silence_notify:
                    self._silence_notify(
                        "No audio detected",
                        "Check: is the meeting playing on your default speaker? (Bluetooth/HDMI may route elsewhere) Right-click tray → Audio Source to pick device."
                    )
                log.warning("Silence detected for %ds — alerting once", self._silence_consecutive)
            elif self._silence_consecutive >= self._silence_seconds and self._silence_consecutive % 30 == 0:
                # Re-log every 30s of continued silence (not a new notification)
                log.warning("Still silent at %ds", self._silence_consecutive)
        else:
            # Only reset counter if we got SIGNIFICANT audio (2x threshold = clear signal, not just noise floor)
            # Don't reset _silence_alerted so we don't re-spam notifications for tiny noise blips
            if rms > threshold * 2:
                self._silence_consecutive = 0

    def stop(self):
        import numpy as np
        self.recording = False
        self.level_system = self.level_mic = 0.0
        if self._system_thread:
            self._system_thread.join(timeout=3)
        if self._mic_thread:
            self._mic_thread.join(timeout=3)

        system_audio = None
        mic_audio = None
        system_duration = 0
        mic_duration = 0

        if self._spool is not None:
            try:
                self._spool.close()
            except Exception as e:
                log.warning("spool close failed: %s", e)

        def _assemble(track, frames):
            """Spooled audio first, then anything captured after a spool failure."""
            parts = []
            if self.session_dir is not None:
                on_disk = spool.read_track(self.session_dir, track)
                if on_disk is not None:
                    parts.append(on_disk)
            if frames:
                parts.append(np.concatenate(frames, axis=0).flatten().astype(np.float32))
            if not parts:
                return None
            return parts[0] if len(parts) == 1 else np.concatenate(parts).astype(np.float32)

        system_audio = _assemble("sys", self.system_frames)
        if system_audio is not None:
            system_duration = len(system_audio) / self.sample_rate
            log.info(f"System audio: {system_duration:.1f}s")

        if self._mic_available:
            mic_audio = _assemble("mic", self.mic_frames)
            if mic_audio is not None:
                mic_duration = len(mic_audio) / self.sample_rate
                log.info(f"Mic audio: {mic_duration:.1f}s")

        # Return the MAX duration across streams — if one stream crashes but the other ran,
        # we still have usable audio. (Critical fix: previously duration was only system_duration,
        # so a 1s system-audio crash would discard a valid hour-long mic recording.)
        duration = max(system_duration, mic_duration)
        return system_audio, mic_audio, duration


# -----------------------------------------------------------------
# Whisper transcription via faster-whisper
# -----------------------------------------------------------------
_whisper_model = None


_whisper_name = None


def get_whisper(cfg):
    global _whisper_model, _whisper_name
    # Reload if the dashboard changed the model since it was loaded.
    if _whisper_model is None or _whisper_name != cfg["whisper_model"]:
        from faster_whisper import WhisperModel
        # Prefer the locally-bundled model (no symlink/admin issues on Windows)
        local_path = Path(__file__).parent / "models" / cfg["whisper_model"]
        model_id = str(local_path) if local_path.exists() else cfg["whisper_model"]
        log.info(f"Loading Whisper model: {model_id} on {cfg['whisper_device']}")
        _whisper_model = WhisperModel(
            model_id,
            device=cfg["whisper_device"],
            compute_type=cfg["whisper_compute_type"],
            # The installed app keeps downloaded models in its own data folder.
            download_root=str(APPDIR / "models") if EMBEDDED else None,
        )
        _whisper_name = cfg["whisper_model"]
        log.info("Whisper model loaded successfully")
    return _whisper_model


def _build_initial_prompt(cfg):
    """Build an initial_prompt from custom vocabulary for Whisper."""
    vocab = cfg.get("custom_vocabulary", [])
    if not vocab:
        return None
    # Whisper uses initial_prompt as context — just list the terms naturally
    return "This meeting discusses: " + ", ".join(vocab) + "."


def transcribe(audio, cfg, speaker_label=None, speaker_at=None):
    """Transcribe audio array. If speaker_label is set, prefix each line with it.

    `speaker_at(start, end)` overrides that label per line, which is how
    diarization turns a track of [Others] into Speaker 1 / Speaker 2. It is
    called once per Whisper segment and must never raise.
    """
    model = get_whisper(cfg)
    initial_prompt = _build_initial_prompt(cfg)
    kwargs = dict(beam_size=5, vad_filter=True)
    if cfg.get("language"):
        kwargs["language"] = cfg["language"]
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, info = model.transcribe(audio, **kwargs)
    lines = []
    for seg in segments:
        t = int(seg.start)
        mins, secs = divmod(t, 60)
        label = speaker_label
        if speaker_at is not None:
            try:
                label = speaker_at(seg.start, seg.end) or speaker_label
            except Exception as e:
                # A labelling fault must cost a label, never the line.
                log.debug("speaker_at failed at %.1fs: %s", seg.start, e)
        prefix = f"[{label}] " if label else ""
        lines.append(f"[{mins:02d}:{secs:02d}] {prefix}{seg.text.strip()}")
    return lines


def diarize_others(system_audio, cfg):
    """Work out who is speaking on the far-end track. None if not possible.

    Deliberately swallows everything. Speaker labels are a nicety; the
    transcript is the product, and no labelling problem is worth losing it.
    """
    if not cfg.get("diarize_others", False) or system_audio is None:
        return None
    if not diarization.available():
        log.warning("diarization enabled but models are missing - keeping [Others]")
        return None
    try:
        t0 = time.time()
        spans = diarization.diarize(
            system_audio,
            sample_rate=cfg["sample_rate"],
            num_speakers=cfg.get("diarize_max_speakers", -1),
            threshold=cfg.get("diarize_threshold", diarization.DEFAULT_THRESHOLD),
            threads=cfg.get("diarize_threads", 2),
            min_talk_seconds=cfg.get("diarize_min_talk_seconds",
                                     diarization.DEFAULT_MIN_TALK),
        )
        audio_min = len(system_audio) / cfg["sample_rate"] / 60
        log.info("Diarization found %d speaker(s) in %d spans, %.1fs for %.1f min of audio",
                 diarization.speaker_count(spans), len(spans), time.time() - t0, audio_min)
        return diarization.make_resolver(spans)
    except Exception as e:
        log.exception("diarization failed - keeping [Others]: %s", e)
        return None


def transcribe_dual(system_audio, mic_audio, cfg):
    """Transcribe both streams and merge chronologically. Either may be None if that stream failed."""
    others_lines = []
    me_lines = []
    if system_audio is not None and len(system_audio) > 0:
        resolver = diarize_others(system_audio, cfg)
        others_lines = transcribe(system_audio, cfg, speaker_label="Others",
                                  speaker_at=resolver)
    if mic_audio is not None and len(mic_audio) > 0:
        me_lines = transcribe(mic_audio, cfg, speaker_label="Me")

    # Parse timestamps and merge chronologically
    def _parse_time(line):
        m = re.match(r'\[(\d+):(\d+)\]', line)
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
        return 0

    all_lines = others_lines + me_lines
    all_lines.sort(key=_parse_time)
    return "\n".join(all_lines)


def transcribe_single(audio, cfg):
    """Single-stream transcription (no speaker labels)."""
    lines = transcribe(audio, cfg)
    return "\n".join(lines)


# -----------------------------------------------------------------
# Upload to the server
# -----------------------------------------------------------------
def upload(transcript, duration, cfg, meeting_type="auto"):
    import requests
    session_id = uuid.uuid4().hex
    resp = requests.post(
        cfg["api_url"].rstrip("/") + "/transcribe",
        headers={"X-API-Key": cfg["api_key"], "Content-Type": "application/json"},
        json={
            "session_id": session_id,
            "transcript": transcript,
            "duration_sec": int(duration),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "meeting_type": meeting_type,
        },
        timeout=30,
        # Do NOT follow redirects. A login page or captive portal in front of
        # the server answers with a redirect to a page that returns 200, so
        # following it makes raise_for_status() pass and this function report
        # success for an upload the server never saw.
        allow_redirects=False,
    )
    if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
        raise RuntimeError(
            f"upload was redirected to {resp.headers.get('Location', '?')[:120]} - "
            "something between here and the server (a login page or proxy) intercepted it - the meeting was NOT received")
    resp.raise_for_status()
    # Prove the server actually took it. A 200 of HTML is not an upload.
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(
            f"upload returned {resp.status_code} but not JSON - got "
            f"{resp.text[:80]!r}. The meeting was not stored.")
    if not body.get("session_id"):
        raise RuntimeError(f"upload response had no session_id: {body}")
    return body.get("session_id", session_id)


def deliver(transcript, duration, cfg, meeting_type="auto"):
    """upload(), with a second server to fall back on (fallback_api_url and
    fallback_api_key in config.json - the Notewell app sets them). A meeting
    the fallback took is listed in undelivered.txt so it can be passed on to
    the first server later. Returns (session_id, the config it went to)."""
    try:
        return upload(transcript, duration, cfg, meeting_type), cfg
    except Exception as e:
        if not cfg.get("fallback_api_url"):
            raise
        log.warning(f"server did not take the meeting ({e}) - trying the fallback")
    alt = {**cfg, "api_url": cfg["fallback_api_url"], "api_key": cfg.get("fallback_api_key", "")}
    session_id = upload(transcript, duration, alt, meeting_type)
    with open(APPDIR / "undelivered.txt", "a", encoding="utf-8") as f:
        f.write(session_id + "\n")
    return session_id, alt


def poll_for_completion(session_id, cfg, timeout=300):
    """Poll the server until processing is complete (max timeout seconds)."""
    import requests
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(
                cfg["api_url"].rstrip("/") + f"/results/{session_id}",
                headers={"X-API-Key": cfg["api_key"]},
                timeout=10,
            )
            if r.ok and r.json().get("status") == "complete":
                return r.json()
        except Exception as e:
            log.warning(f"poll error: {e}")
        time.sleep(5)
    return None


# -----------------------------------------------------------------
# Meeting lifecycle (orchestrated by tray icon)
# -----------------------------------------------------------------
def save_backup_wavs(system_audio, mic_audio, sample_rate, stamp=None):
    """Write every track that holds audio, and return the paths.

    Saving only the system (far-end) track is a trap: for an in-person meeting
    the microphone carries the whole conversation and the system track holds
    only room tone, so the "safety net" would be a full-length file of nothing.

    Both tracks are written rather than guessing which one matters. At
    1.9 MB per minute per track that is a cheap way to never be wrong.
    """
    import numpy as np
    import wave

    stamp = stamp or int(time.time())
    written = []
    for name, audio in (("sys", system_audio), ("mic", mic_audio)):
        if audio is None or len(audio) == 0:
            continue
        try:
            path = APPDIR / f"last_meeting_{stamp}_{name}.wav"
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
                wf.writeframes((clipped * 32767).astype(np.int16).tobytes())
            written.append(path)
            log.info("Saved backup audio to %s (%.1f min)",
                     path, len(audio) / sample_rate / 60)
        except Exception as e:
            log.warning("could not save %s backup wav: %s", name, e)
    if not written:
        log.warning("no backup audio written - both tracks were empty")
    return written


def archive_backups(paths, cfg):
    """Move confirmed-uploaded backup WAVs out of the roaming profile.

    Kept, not deleted: a transcript can be regenerated from audio, never the
    other way round. Deliberately not a synced folder - these are unencrypted
    client conversations and OneDrive/Google Drive both run on this machine.
    """
    import shutil
    if not cfg.get("archive_backups", True) or not paths:
        return []
    dest = ARCHIVE_ROOT / f"wav-{time.strftime('%Y-%m-%d')}"
    moved = []
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("could not create archive dir: %s", e)
        return []
    for p in paths:
        try:
            shutil.move(str(p), str(dest / p.name))
            moved.append(dest / p.name)
        except Exception as e:
            log.warning("could not archive %s: %s", p, e)
    if moved:
        log.info("Archived %d backup wav(s) to %s", len(moved), dest)
    return moved


def prune_archive(cfg):
    """Delete archived WAVs older than the retention window."""
    days = int(cfg.get("archive_retention_days", 30))
    if days <= 0 or not ARCHIVE_ROOT.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for wav in ARCHIVE_ROOT.glob("wav-*/*.wav"):
        try:
            if wav.stat().st_mtime < cutoff:
                wav.unlink()
                removed += 1
        except OSError as e:
            log.debug("prune skipped %s: %s", wav, e)
    if removed:
        log.info("Pruned %d archived wav(s) older than %d days", removed, days)
    return removed


class MeetingManager:
    def __init__(self, cfg, notify):
        self.cfg = cfg
        self.notify = notify
        self._use_dual = cfg.get("dual_stream", True)
        if self._use_dual:
            self.capture = DualAudioCapture(cfg["sample_rate"])
            # Wire up silence detection config
            self.capture._silence_threshold = cfg.get("silence_alert_threshold_rms", 0.001)
            self.capture._silence_seconds = cfg.get("silence_alert_seconds", 10)
            self.capture._silence_notify = notify
            self.capture._audio_source = cfg.get("audio_source", "default")
            self.capture._spool_enabled = cfg.get("spool_enabled", False)
        else:
            self.capture = AudioCapture(cfg["sample_rate"])
            self.capture._audio_source = cfg.get("audio_source", "default")
        self.in_meeting = False
        self._live_window = None
        self._live_transcriber = None
        # When the user manually stops a recording while auto-detect is on and a
        # meeting app is still running, set this so the poller doesn't immediately
        # re-start recording. Cleared when the meeting app is no longer detected.
        self._manual_stop_override = False
        # True when the current recording was started by the auto-detect poller
        # (so manual Stop can distinguish auto vs. user-started sessions).
        self._started_by_auto = False
        # Read by the Notewell dashboard: recording timer, "transcribing" state,
        # and whether the speech model has finished downloading.
        self.started_at = None
        self.processing = False
        self.model_ready = False

    def start(self, by_auto=False):
        if self.in_meeting:
            self.notify("Already recording", "A meeting is already in progress.")
            return
        self.in_meeting = True
        self.started_at = time.time()
        self._started_by_auto = by_auto
        try:
            self.capture.start()
            self._start_live_view()
            if self.cfg.get("sound_effects", True):
                play_start_sound()
            stream_mode = "dual-stream (Me + Others)" if self._use_dual else "single-stream"
            self.notify("Meeting started", f"Recording {stream_mode}. Click Stop when done.")
        except Exception as e:
            self.in_meeting = False
            self._started_by_auto = False
            log.exception("start failed")
            self.notify("Error", f"Could not start recording: {e}")

    def stop(self, manual=True):
        if not self.in_meeting:
            return
        # If this was a manual stop while auto-detect is ON and the meeting app
        # is still detected, arm the override so auto_poller doesn't restart.
        if manual and self.cfg.get("auto_detect", False):
            try:
                if is_meeting_active(self.cfg):
                    self._manual_stop_override = True
                    log.info("Manual stop with meeting app still active — auto-restart suppressed until app closes")
            except Exception as e:
                log.debug(f"is_meeting_active check during stop failed: {e}")
        self.in_meeting = False
        self._started_by_auto = False
        if self.cfg.get("sound_effects", True):
            play_stop_sound()
        self.processing = True
        threading.Thread(target=self._finish_tracked, daemon=True).start()

    def _finish_tracked(self):
        try:
            self._finish()
        finally:
            self.processing = False

    def _start_live_view(self):
        """Open the live transcript window, if it is wanted and possible.

        Never fatal. The live view is a convenience; a meeting that records
        without it is a success, a meeting lost to it is not.
        """
        if not self.cfg.get("live_transcript", False):
            return
        session_dir = getattr(self.capture, "session_dir", None)
        if session_dir is None:
            log.warning("live transcript needs spool_enabled - skipping")
            self.notify("Live transcript off",
                        "It needs spool_enabled, which is not on.")
            return
        try:
            from live_window import LiveWindow
            from live_transcribe import LiveTranscriber
            self._live_window = LiveWindow(
                topmost=self.cfg.get("live_window_topmost", True))
            self._live_window.start()
            self._live_transcriber = LiveTranscriber(
                session_dir, self.cfg, self._live_window,
                self.cfg["sample_rate"])
            self._live_transcriber.start()
            log.info("Live transcript started")
        except Exception as e:
            log.exception("live transcript failed to start: %s", e)
            self._stop_live_view()

    def _stop_live_view(self, close_window=True):
        try:
            if self._live_transcriber is not None:
                self._live_transcriber.stop()
                log.info("Live transcript: %d windows decoded, %d quiet skipped, %.0fs CPU",
                         self._live_transcriber.decoded_windows,
                         self._live_transcriber.skipped_quiet,
                         self._live_transcriber.decode_seconds)
        except Exception as e:
            log.debug("stopping live transcriber: %s", e)
        self._live_transcriber = None
        if close_window:
            try:
                if self._live_window is not None:
                    self._live_window.close()
            except Exception as e:
                log.debug("closing live window: %s", e)
            self._live_window = None

    def _retain(self, backup_paths):
        """Clean up after a confirmed upload. Never fatal."""
        try:
            session_dir = getattr(self.capture, "session_dir", None)
            if session_dir is not None and spool.discard(session_dir):
                log.info("Discarded spool %s", session_dir)
        except Exception as e:
            log.warning("spool cleanup failed: %s", e)
        try:
            archive_backups(backup_paths, self.cfg)
            prune_archive(self.cfg)
        except Exception as e:
            log.warning("archive step failed: %s", e)

    def _finish(self):
        backup_paths = []
        try:
            log.info("_finish: stopping capture")
            # Stop decoding immediately - the accurate pass needs the cores.
            # The window stays up so the last lines can still be read.
            self._stop_live_view(close_window=False)

            if self._use_dual:
                system_audio, mic_audio, duration = self.capture.stop()
            else:
                system_audio, duration = self.capture.stop()
                mic_audio = None

            # Proceed if ANY stream has >= 10s of audio. If system crashed but mic has content,
            # we'll transcribe just the mic. If mic crashed but system has content, same.
            if duration < 10 and (system_audio is None or len(system_audio) / self.capture.sample_rate < 10) \
               and (mic_audio is None or len(mic_audio) / self.capture.sample_rate < 10):
                log.warning(f"audio too short: duration={duration}, system={system_audio is not None}, mic={mic_audio is not None}")
                self.notify("Too short", "Meeting under 10s, discarded.")
                return

            # If one stream crashed but the other succeeded, use only the working stream.
            # duration is already max() of both, so transcription will proceed.
            sys_len = (len(system_audio) / self.capture.sample_rate) if system_audio is not None else 0
            mic_len = (len(mic_audio) / self.capture.sample_rate) if mic_audio is not None else 0
            if sys_len < 10 and mic_len >= 10:
                log.warning(f"System audio stream too short ({sys_len:.1f}s) — using mic only ({mic_len:.1f}s)")
                system_audio = None  # skip system in transcription
            elif mic_len < 10 and sys_len >= 10:
                log.warning(f"Mic stream too short ({mic_len:.1f}s) — using system only ({sys_len:.1f}s)")
                mic_audio = None

            # Save raw audio to disk as a safety net in case transcription fails.
            backup_paths = save_backup_wavs(
                system_audio, mic_audio, self.cfg["sample_rate"])

            self.notify("Transcribing...", f"Processing {int(duration//60)} min of audio locally. This may take 30s-2min.")
            t0 = time.time()
            try:
                if self._use_dual:
                    transcript = transcribe_dual(system_audio, mic_audio, self.cfg)
                else:
                    transcript = transcribe_single(system_audio, self.cfg)
            except Exception as e:
                log.exception("transcribe failed")
                self.notify("Transcription failed", f"{type(e).__name__}: {str(e)[:100]}. See log.")
                return
            log.info(f"Transcription done in {time.time()-t0:.1f}s, {len(transcript)} chars")
            if len(transcript.strip()) < 20:
                log.warning(f"transcript too short: '{transcript[:100]}'")
                self.notify("No speech", "No speech detected. Check system audio routing.")
                return

            # Save transcript to disk as a safety net
            try:
                txt_path = APPDIR / f"last_transcript_{int(time.time())}.txt"
                txt_path.write_text(transcript, encoding='utf-8')
                log.info(f"Saved transcript to {txt_path}")
            except Exception as e:
                log.warning(f"could not save transcript: {e}")

            self.notify("Uploading...", "Sending transcript to your server for AI analysis.")
            try:
                session_id, sent_to = deliver(transcript, duration, self.cfg)
                log.info(f"Uploaded session: {session_id}")
                # The transcript is safely on the server, so the raw audio no
                # longer needs to sit in the roaming profile. Order matters:
                # nothing is discarded until the upload has returned an id.
                self._retain(backup_paths)
            except Exception as e:
                log.exception("upload failed")
                self.notify("Upload failed", f"{type(e).__name__}: {str(e)[:100]}. Transcript saved locally.")
                return

            if self._live_window is not None:
                self._live_window.set_status("uploaded - see the dashboard")
                self._live_window.close()
                self._live_window = None
            if self.cfg.get("notify_on_complete", True):
                result = poll_for_completion(session_id, sent_to)
                if result:
                    self.notify("Meeting processed", f"Type: {result.get('meeting_type')} - open dashboard to view")
                else:
                    self.notify("Upload complete", "Processing taking longer than expected - check dashboard.")
        except Exception as e:
            log.exception("finish failed")
            self.notify("Error", f"{type(e).__name__}: {str(e)[:100]}")


# -----------------------------------------------------------------
# Auto-detection of meeting apps (polling every 10s)
# -----------------------------------------------------------------
_MEETING_WINDOW_KEYWORDS = [
    "zoom meeting", "microsoft teams", "google meet",
    "webex meeting", "cisco webex", "slack huddle",
    "discord call", "zoom webinar",
]


def recover_orphan(session_dir, cfg, notify):
    """Transcribe and upload a meeting whose recorder died mid-call.

    The spool is already a complete, readable recording, so recovery is just
    ordinary transcription pointed at a directory instead of at memory.
    """
    sr = spool.read_meta(session_dir).get("sample_rate", cfg["sample_rate"])
    system_audio = spool.read_track(session_dir, "sys")
    mic_audio = spool.read_track(session_dir, "mic")
    lens = [len(a) / sr for a in (system_audio, mic_audio) if a is not None]
    duration = max(lens) if lens else 0
    if duration < 10:
        log.warning("orphan %s is only %.1fs - discarding", session_dir, duration)
        spool.discard(session_dir)
        return None

    log.info("Recovering %s (%.1f min)", session_dir, duration / 60)
    notify("Recovering meeting", f"Transcribing {int(duration // 60)} min left by a crash.")
    save_backup_wavs(system_audio, mic_audio, sr)
    if cfg.get("dual_stream", True) and (system_audio is not None or mic_audio is not None):
        transcript = transcribe_dual(system_audio, mic_audio, cfg)
    else:
        transcript = transcribe_single(system_audio, cfg)
    if len(transcript.strip()) < 20:
        log.warning("recovered transcript too short - keeping the spool")
        notify("Nothing to recover", "No speech found in the unfinished recording.")
        return None
    session_id, _ = deliver(transcript, duration, cfg)
    log.info("Recovered session uploaded: %s", session_id)
    spool.discard(session_dir)
    notify("Meeting recovered", "The unfinished recording is on the dashboard.")
    return session_id


def _touch(path):
    try:
        path.write_text(str(int(time.time())), encoding="utf-8")
        return True
    except OSError:
        return False


def _age_seconds(path):
    """Seconds since a liveness file was last touched. None if it is missing."""
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def heartbeat_loop(notify=None):
    """Prove this recorder is alive, and revive the supervisor if it has died.

    Mutual supervision: the supervisor restarts the recorder, and this restarts
    the supervisor. Both would have to fail at once to leave nothing running.
    It needs no administrator rights (a Windows scheduled task would).
    """
    revive_after = 900          # 15 min of silence means the supervisor is gone
    checked_at = 0
    while True:
        _touch(RECORDER_ALIVE)
        now = time.time()
        # Inside the Notewell app there is no supervisor to revive.
        if not EMBEDDED and now - checked_at >= 300:
            checked_at = now
            try:
                age = _age_seconds(SUPERVISOR_ALIVE)
                if age is None or age > revive_after:
                    if SUPERVISOR_LAUNCHER.is_file():
                        log.warning("supervisor looks dead (%s) - restarting it",
                                    "no heartbeat file" if age is None
                                    else f"{age/60:.0f} min stale")
                        subprocess.Popen(["wscript.exe", str(SUPERVISOR_LAUNCHER)],
                                         creationflags=0x08000000)
                    else:
                        log.warning("supervisor is dead and %s is missing",
                                    SUPERVISOR_LAUNCHER)
            except Exception as e:
                log.debug("supervisor revival check failed: %s", e)
        time.sleep(30)


def _check_window_titles():
    """Check if any window title contains meeting keywords (Windows only)."""
    try:
        import ctypes
        import ctypes.wintypes

        EnumWindows = ctypes.windll.user32.EnumWindows
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible

        titles = []

        def _callback(hwnd, _):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buf, length + 1)
                    titles.append(buf.value.lower())
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        EnumWindows(WNDENUMPROC(_callback), 0)

        for title in titles:
            for keyword in _MEETING_WINDOW_KEYWORDS:
                if keyword in title:
                    log.debug(f"Meeting window found: '{title}' matches '{keyword}'")
                    return True
    except Exception as e:
        log.debug(f"Window title check failed: {e}")
    return False


def is_meeting_active(cfg):
    """Return True if Zoom/Teams/etc. process is running or a meeting window is detected."""
    try:
        import psutil
        names = set(n.lower() for n in cfg.get("auto_detect_processes", []))
        for p in psutil.process_iter(["name"]):
            if p.info["name"] and p.info["name"].lower() in names:
                return True
    except Exception as e:
        log.warning(f"process check failed: {e}")

    # Also check window titles for meeting keywords
    if _check_window_titles():
        return True

    return False


# -----------------------------------------------------------------
# Tray icon (using pystray)
# -----------------------------------------------------------------
def show_toast(title, message):
    """A Windows notification that works without extra packages."""
    def esc(t):
        return str(t).replace("'", "''")
    try:
        subprocess.Popen([
            "powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            f"$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('{esc(title)}')) | Out-Null;"
            f"$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('{esc(message)}')) | Out-Null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Meeting Recorder').Show([Windows.UI.Notifications.ToastNotification]::new($xml));"
        ], creationflags=0x08000000)
    except Exception as e:
        log.warning("toast failed: %s", e)


def _acquire_single_instance_lock():
    """Prevent multiple tray instances. Returns True if we got the lock."""
    lock_file = APPDIR / "recorder.lock"
    import os as _os
    try:
        import psutil
        if lock_file.exists():
            try:
                pid = int(lock_file.read_text().strip())
                # Check if pid exists AND is a pythonw/python running our script
                if psutil.pid_exists(pid):
                    try:
                        proc = psutil.Process(pid)
                        pname = proc.name().lower()
                        cmdline = " ".join(proc.cmdline()).lower()
                        if ("python" in pname) and ("recorder.py" in cmdline):
                            log.warning(f"Meeting Recorder already running (PID {pid}) — exiting.")
                            return False
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                # Stale lock (PID doesn't exist or isn't our process)
                log.info(f"Removing stale lock (PID {pid} not a running recorder)")
                lock_file.unlink(missing_ok=True)
            except (ValueError, OSError):
                lock_file.unlink(missing_ok=True)
        lock_file.write_text(str(_os.getpid()))
        import atexit
        atexit.register(lambda: lock_file.unlink(missing_ok=True))
        return True
    except Exception as e:
        log.warning(f"Could not acquire single-instance lock: {e}")
        return True  # Don't block startup on lock issues


def list_speakers():
    """Output devices the recorder can capture, for the dashboard's picker."""
    import soundcard as sc
    default_name = sc.default_speaker().name
    return ([{"id": "default", "name": f"Default ({default_name})"}]
            + [{"id": s.name, "name": s.name} for s in sc.all_speakers()])


def _controller(mgr, cfg, icon, icon_idle, icon_rec):
    """What the Notewell dashboard drives. Start and stop also recolour the tray
    icon, so it is right whichever side pressed the button."""
    def start():
        mgr.start()
        icon.icon = icon_rec if mgr.in_meeting else icon_idle

    def stop():
        mgr.stop()
        icon.icon = icon_idle

    def save_cfg():
        write_config(cfg)

    return dict(manager=mgr, cfg=cfg, start=start, stop=stop,
                devices=list_speakers, save_cfg=save_cfg)


def main(hooks=None):
    """Run the tray app. `hooks` is given only by the Notewell desktop app:
        on_ready(controller)  called once, just before the tray loop starts
        open_dashboard()      replaces opening api_url in the browser
        open_settings()       replaces opening config.json in Notepad
    """
    global EMBEDDED
    EMBEDDED = hooks is not None
    setup_logging()
    # The first thing written, before anything that can fail. If startup did its
    # work (config, tray icon, audio devices) before logging, a failure in any of
    # it would leave recorder.log empty and nothing to diagnose.
    log.info("startup: process alive, pid=%s", os.getpid())

    # Notewell keeps itself to one copy before it gets here.
    if not EMBEDDED and not _acquire_single_instance_lock():
        # Already running — show a Windows balloon so the user knows it's alive.
        # Users often can't find the tray icon (Win11 hides it) and think the app failed.
        log.info("Meeting Recorder already running — showing notification to user")
        try:
            # Use pywin32 Shell_NotifyIconW via a minimal approach
            import ctypes
            ctypes.windll.user32.MessageBeep(0x00000040)  # MB_ICONASTERISK beep
            # Try to show a balloon via shell — fall back to MessageBox if no tray available
            try:
                # Use BurntToast-style toast via PowerShell (most reliable on Win10/11)
                subprocess.Popen([
                    "powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                    "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                    "$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode('Meeting Recorder is already running')) | Out-Null;"
                    "$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode('Look for the blue circle in the system tray. Click the ^ arrow in the taskbar to find hidden icons.')) | Out-Null;"
                    "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Meeting Recorder').Show($toast);"
                ], creationflags=0x08000000)  # CREATE_NO_WINDOW
            except Exception as e:
                log.warning(f"toast failed: {e}")
        except Exception:
            pass
        return

    log.info("startup: lock acquired")
    cfg = load_config()
    log.info("startup: config loaded")

    problems = config_problems(cfg)
    if problems:
        for pr in problems:
            log.error("config problem: %s", pr)
        log.error("API key not configured — edit %s", CONFIG_PATH)
        # This used to exit silently. It tried a toast through win10toast_click,
        # which was never installed, so the failure was invisible: at every
        # login the recorder read a config with a placeholder key, logged this
        # line, and quit - with no tray icon and no message. Say it on screen.
        show_toast("Meeting Recorder cannot start",
                   problems[0][:200] + f"  (edit {CONFIG_PATH}). Meetings are NOT being recorded.")
        # 78 = configuration error. The supervisor treats it differently from a
        # crash: it reports the problem and stops retrying every five minutes.
        sys.exit(78)

    log.info("startup: importing tray libraries")
    import pystray
    from PIL import Image, ImageDraw

    def make_icon(color):
        img = Image.new("RGB", (64, 64), color=(20, 20, 30))
        d = ImageDraw.Draw(img)
        d.ellipse((16, 16, 48, 48), fill=color)
        return img

    icon_idle = make_icon((88, 166, 255))   # blue
    icon_rec = make_icon((255, 80, 80))      # red

    def notify(title, msg):
        try:
            icon.notify(msg, title)
        except Exception:
            log.info(f"NOTIFY: {title} — {msg}")

    mgr = MeetingManager(cfg, notify)

    def on_start(icon_, item):
        mgr.start()
        icon.icon = icon_rec

    def on_stop(icon_, item):
        mgr.stop()
        icon.icon = icon_idle

    def on_recover(icon_, item):
        def _run():
            for d in spool.list_orphans(SPOOL_ROOT):
                try:
                    recover_orphan(d, cfg, notify)
                except Exception as e:
                    log.exception("recovery failed for %s", d)
                    notify("Recovery failed", f"{type(e).__name__}: {str(e)[:80]}")
        threading.Thread(target=_run, daemon=True).start()

    def on_dashboard(icon_, item):
        if hooks:
            hooks["open_dashboard"]()
        else:
            webbrowser.open(cfg["api_url"])

    def on_settings(icon_, item):
        if hooks:
            hooks["open_settings"]()
        else:
            subprocess.Popen(["notepad", str(CONFIG_PATH)])

    def on_logs(icon_, item):
        subprocess.Popen(["notepad", str(LOG_PATH)])

    def on_quit(icon_, item):
        if mgr.in_meeting:
            mgr.stop()
        icon.stop()

    def auto_detect_toggle(icon_, item):
        cfg["auto_detect"] = not cfg["auto_detect"]
        write_config(cfg)
        notify("Auto-detect", "enabled" if cfg["auto_detect"] else "disabled")

    def live_transcript_toggle(icon_, item):
        cfg["live_transcript"] = not cfg.get("live_transcript", False)
        write_config(cfg)
        if cfg["live_transcript"] and not cfg.get("spool_enabled", False):
            notify("Live transcript",
                   "Turned on, but it also needs spool_enabled to be on.")
        else:
            notify("Live transcript",
                   "enabled - shows from the next meeting" if cfg["live_transcript"]
                   else "disabled")

    def _test_audio(icon_, item):
        """Capture 5 seconds and report RMS level — lets user verify the right device is being captured."""
        import threading as _th
        def _run():
            try:
                import soundcard as sc
                import numpy as np
                source = cfg.get("audio_source", "default")
                if source == "default" or not source:
                    speaker = sc.default_speaker()
                else:
                    matches = [s for s in sc.all_speakers() if s.name == source or str(s.name).startswith(source)]
                    speaker = matches[0] if matches else sc.default_speaker()
                log.info(f"Test audio: capturing 5s from '{speaker.name}'")
                notify("Testing audio...", f"Play something on {speaker.name} now. Listening for 5s.")
                mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                with mic.recorder(samplerate=cfg.get("sample_rate", 16000), channels=1) as rec:
                    data = rec.record(numframes=cfg.get("sample_rate", 16000) * 5)
                audio = data.flatten().astype(np.float32)
                rms = float(np.sqrt(np.mean(audio ** 2)))
                peak = float(np.max(np.abs(audio)))
                log.info(f"Test audio done: RMS={rms:.4f}, peak={peak:.4f}")
                if rms > 0.01:
                    notify("Audio OK ✓", f"'{speaker.name}' captured audio. RMS={rms:.3f}, peak={peak:.2f}")
                elif rms > 0.001:
                    notify("Audio faint", f"'{speaker.name}': very quiet. RMS={rms:.3f}. Turn up volume.")
                else:
                    notify("No audio ✗", f"'{speaker.name}' returned silence. Try another device via Audio Source menu.")
            except Exception as e:
                log.exception("test_audio failed")
                notify("Test failed", f"{type(e).__name__}: {str(e)[:80]}")
        _th.Thread(target=_run, daemon=True).start()

    def _make_source_picker(device_name):
        def _pick(icon_, item):
            cfg["audio_source"] = device_name
            write_config(cfg)
            # Update the live capture so next recording uses new source
            if hasattr(mgr, 'capture'):
                mgr.capture._audio_source = device_name
            notify("Audio source changed", f"Now capturing from: {device_name}")
        return _pick

    def _build_source_menu():
        """Build a submenu of available output devices. Called fresh each time menu opens.

        This enumerates audio hardware, the riskiest thing startup does: a
        half-connected Bluetooth headset or a driver update can make it slow or
        fail. It is timed and logged so that shows up in the log.
        """
        _t0 = time.time()
        try:
            import soundcard as sc
            speakers = sc.all_speakers()
            default_name = sc.default_speaker().name
            _el = time.time() - _t0
            if _el > 2.0:
                log.warning("audio device enumeration took %.1fs - a device is "
                            "slow or half-connected", _el)
            else:
                log.info("startup: %d audio devices, default=%s (%.2fs)",
                         len(speakers), default_name[:40], _el)
            current = cfg.get("audio_source", "default")
            items = [
                pystray.MenuItem(
                    f"Default ({default_name[:30]})",
                    _make_source_picker("default"),
                    checked=lambda i, c=current: c in ("default", "", None),
                    radio=True,
                )
            ]
            for s in speakers:
                items.append(pystray.MenuItem(
                    s.name[:40],
                    _make_source_picker(s.name),
                    checked=lambda i, n=s.name, c=current: c == n,
                    radio=True,
                ))
            return pystray.Menu(*items)
        except Exception as e:
            log.warning(f"Could not build source menu: {e}")
            return pystray.Menu(pystray.MenuItem("(error listing devices)", lambda i, it: None))

    def _primary_label(i):
        if mgr.in_meeting:
            return "Stop Meeting"
        if mgr._manual_stop_override:
            return "Start Meeting (auto-restart suppressed — won't auto-start until meeting app closes)"
        return "Start Meeting"

    log.info("startup: building tray menu")
    menu = pystray.Menu(
        pystray.MenuItem(_primary_label,
                         lambda i, it: on_stop(i, it) if mgr.in_meeting else on_start(i, it)),
        pystray.MenuItem("Auto-detect meetings",
                         auto_detect_toggle,
                         checked=lambda i: cfg.get("auto_detect", False)),
        pystray.MenuItem("Live transcript window",
                         live_transcript_toggle,
                         checked=lambda i: cfg.get("live_transcript", False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔊 Test Audio (5s)", _test_audio),
        pystray.MenuItem("Audio Source", _build_source_menu()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Recover unfinished meeting", on_recover,
                         visible=lambda i: bool(spool.list_orphans(SPOOL_ROOT))),
        pystray.MenuItem("Open Notewell" if hooks else "Open Dashboard", on_dashboard, default=bool(hooks)),
        pystray.MenuItem("Settings" if hooks else "Settings (config.json)", on_settings),
        pystray.MenuItem("View logs", on_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    log.info("startup: tray menu built")
    if hooks:
        icon = pystray.Icon("notewell", icon_idle, "Notewell", menu)
    else:
        icon = pystray.Icon("meeting-recorder", icon_idle, "Meeting Recorder", menu)

    # Auto-detect poller with debounce
    def auto_poller():
        was_active = False
        debounce_start = None  # when we first saw a meeting app
        debounce_secs = cfg.get("auto_detect_debounce_seconds", 15)
        while True:
            time.sleep(10)
            if not cfg.get("auto_detect", False):
                debounce_start = None
                # If auto-detect was turned off, clear any pending override too.
                mgr._manual_stop_override = False
                continue
            active = is_meeting_active(cfg)
            # Reset manual-stop override once the meeting app has actually closed.
            if not active and mgr._manual_stop_override:
                log.info("Meeting app closed — clearing manual-stop override")
                mgr._manual_stop_override = False
            if active and not mgr.in_meeting:
                if mgr._manual_stop_override:
                    # User manually stopped; don't restart until app closes.
                    debounce_start = None
                elif debounce_start is None:
                    debounce_start = time.time()
                    log.info("Meeting app detected, starting %ds debounce", debounce_secs)
                elif time.time() - debounce_start >= debounce_secs:
                    log.info("Debounce passed; auto-starting recording")
                    mgr.start(by_auto=True)
                    icon.icon = icon_rec
                    debounce_start = None
            elif not active:
                debounce_start = None
                if was_active and mgr.in_meeting:
                    log.info("Meeting app closed; stopping recording")
                    # Auto-driven stop — not a manual override.
                    mgr.stop(manual=False)
                    icon.icon = icon_idle
            was_active = active

    try:
        orphans = spool.list_orphans(SPOOL_ROOT)
        if orphans:
            mins = max(spool.track_seconds(d, t) for d in orphans for t in ("sys", "mic")) / 60
            log.warning("%d unfinished recording(s) found in %s", len(orphans), SPOOL_ROOT)
            notify("Unfinished recording found",
                   f"{len(orphans)} meeting(s), longest {int(mins)} min. "
                   f"Right-click the tray icon to recover.")
    except Exception as e:
        log.warning("orphan scan failed: %s", e)

    _touch(RECORDER_ALIVE)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=auto_poller, daemon=True).start()
    log.info("Meeting Recorder started (dual_stream=%s, vocab=%d terms, sound=%s)",
             cfg.get("dual_stream"), len(cfg.get("custom_vocabulary", [])), cfg.get("sound_effects"))
    if hooks:
        hooks["on_ready"](_controller(mgr, cfg, icon, icon_idle, icon_rec))
    log.info("startup: complete, entering tray loop")
    try:
        icon.run()
    except Exception:
        log.exception("tray loop crashed")
        raise
    log.warning("tray loop exited - the recorder is shutting down")


if __name__ == "__main__":
    main()
