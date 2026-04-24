"""
Meeting Recorder — Windows tray app.

Captures system audio via WASAPI loopback, transcribes with faster-whisper,
and sends the transcript to the Meeting Notes API on the VPS.

Tray icon menu:
  - Start Meeting          — begin capturing system audio
  - Stop Meeting           — end capture, transcribe, send to VPS
  - Auto-detect meetings   — toggle process-based auto-detection (Zoom, Teams, etc.)
  - Open Dashboard         — open notes.example.com in browser
  - Settings               — edit config.json
  - Quit

Config lives at: %APPDATA%\MeetingRecorder\config.json
Logs at:          %APPDATA%\MeetingRecorder\recorder.log
"""
import os, sys, json, time, threading, logging, subprocess, uuid, webbrowser, re, struct
from pathlib import Path
from datetime import datetime, timezone

APPDIR = Path(os.environ.get("APPDATA", Path.home())) / "MeetingRecorder"
APPDIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APPDIR / "config.json"
LOG_PATH = APPDIR / "recorder.log"

DEFAULT_CONFIG = {
    "api_url": "https://notes.example.com",
    "api_key": "PASTE_YOUR_MEETING_NOTES_API_KEY_HERE",
    "whisper_model": "distil-large-v3",  # options: tiny, base, small, medium, large-v3, distil-large-v3
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
    "custom_vocabulary": [],              # e.g. ["CompanyName", "TeamHub", "DeepSeek", "Acme"]
    "sound_effects": True,                # play beep on start/stop
    "silence_alert_threshold_rms": 0.001, # RMS below this = silence
    "silence_alert_seconds": 10,          # alert after this many seconds of silence
    "audio_source": "default",            # "default" = current default speaker, or specific device name
}


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        return DEFAULT_CONFIG.copy()
    cfg = json.loads(CONFIG_PATH.read_text())
    # Merge in any new defaults
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


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
        # Silence detection state
        self._silence_consecutive = 0
        self._silence_alerted = False
        self._silence_notify = None  # set by MeetingManager

    def start(self):
        import soundcard as sc
        self.system_frames = []
        self.mic_frames = []
        self.recording = True
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
                        self.system_frames.append(data)
                        # Silence detection on system audio
                        rms = np.sqrt(np.mean(data ** 2))
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
                        self.mic_frames.append(data)
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
        if self._system_thread:
            self._system_thread.join(timeout=3)
        if self._mic_thread:
            self._mic_thread.join(timeout=3)

        system_audio = None
        mic_audio = None
        system_duration = 0
        mic_duration = 0

        if self.system_frames:
            system_audio = np.concatenate(self.system_frames, axis=0).flatten().astype(np.float32)
            system_duration = len(system_audio) / self.sample_rate
            log.info(f"System audio: {system_duration:.1f}s")

        if self.mic_frames and self._mic_available:
            mic_audio = np.concatenate(self.mic_frames, axis=0).flatten().astype(np.float32)
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


def get_whisper(cfg):
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # Prefer the locally-bundled model (no symlink/admin issues on Windows)
        local_path = Path(__file__).parent / "models" / cfg["whisper_model"]
        model_id = str(local_path) if local_path.exists() else cfg["whisper_model"]
        log.info(f"Loading Whisper model: {model_id} on {cfg['whisper_device']}")
        _whisper_model = WhisperModel(
            model_id,
            device=cfg["whisper_device"],
            compute_type=cfg["whisper_compute_type"],
        )
        log.info("Whisper model loaded successfully")
    return _whisper_model


def _build_initial_prompt(cfg):
    """Build an initial_prompt from custom vocabulary for Whisper."""
    vocab = cfg.get("custom_vocabulary", [])
    if not vocab:
        return None
    # Whisper uses initial_prompt as context — just list the terms naturally
    return "This meeting discusses: " + ", ".join(vocab) + "."


def transcribe(audio, cfg, speaker_label=None):
    """Transcribe audio array. If speaker_label is set, prefix each line with it."""
    model = get_whisper(cfg)
    initial_prompt = _build_initial_prompt(cfg)
    kwargs = dict(beam_size=5, vad_filter=True)
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, info = model.transcribe(audio, **kwargs)
    lines = []
    for seg in segments:
        t = int(seg.start)
        mins, secs = divmod(t, 60)
        prefix = f"[{speaker_label}] " if speaker_label else ""
        lines.append(f"[{mins:02d}:{secs:02d}] {prefix}{seg.text.strip()}")
    return lines


def transcribe_dual(system_audio, mic_audio, cfg):
    """Transcribe both streams and merge chronologically. Either may be None if that stream failed."""
    others_lines = []
    me_lines = []
    if system_audio is not None and len(system_audio) > 0:
        others_lines = transcribe(system_audio, cfg, speaker_label="Others")
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
# Upload to VPS
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
    )
    resp.raise_for_status()
    return session_id


def poll_for_completion(session_id, cfg, timeout=300):
    """Poll VPS until processing is complete (max timeout seconds)."""
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
        else:
            self.capture = AudioCapture(cfg["sample_rate"])
            self.capture._audio_source = cfg.get("audio_source", "default")
        self.in_meeting = False
        # When the user manually stops a recording while auto-detect is on and a
        # meeting app is still running, set this so the poller doesn't immediately
        # re-start recording. Cleared when the meeting app is no longer detected.
        self._manual_stop_override = False
        # True when the current recording was started by the auto-detect poller
        # (so manual Stop can distinguish auto vs. user-started sessions).
        self._started_by_auto = False

    def start(self, by_auto=False):
        if self.in_meeting:
            self.notify("Already recording", "A meeting is already in progress.")
            return
        self.in_meeting = True
        self._started_by_auto = by_auto
        try:
            self.capture.start()
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
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self):
        try:
            log.info("_finish: stopping capture")

            if self._use_dual:
                system_audio, mic_audio, duration = self.capture.stop()
                audio_for_backup = system_audio  # back up system audio
            else:
                audio_for_backup, duration = self.capture.stop()
                system_audio = audio_for_backup
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

            # Save raw audio to disk as a safety net in case transcription fails
            try:
                import numpy as np
                import wave
                wav_path = APPDIR / f"last_meeting_{int(time.time())}.wav"
                with wave.open(str(wav_path), 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.cfg['sample_rate'])
                    wf.writeframes((audio_for_backup * 32767).astype(np.int16).tobytes())
                log.info(f"Saved backup audio to {wav_path}")
            except Exception as e:
                log.warning(f"could not save backup wav: {e}")

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

            self.notify("Uploading...", "Sending transcript to VPS for AI analysis.")
            try:
                session_id = upload(transcript, duration, self.cfg)
                log.info(f"Uploaded session: {session_id}")
            except Exception as e:
                log.exception("upload failed")
                self.notify("Upload failed", f"{type(e).__name__}: {str(e)[:100]}. Transcript saved locally.")
                return

            if self.cfg.get("notify_on_complete", True):
                result = poll_for_completion(session_id, self.cfg)
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


def main():
    setup_logging()

    if not _acquire_single_instance_lock():
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

    cfg = load_config()

    if cfg["api_key"].startswith("PASTE") or not cfg.get("api_key", "").startswith("mn_"):
        log.error("API key not configured — edit %s", CONFIG_PATH)
        # Do NOT open notepad (user found it confusing). Just log and exit silently.
        # The user can right-click the tray icon → Settings (once icon shows).
        # If no icon shows up after login, they can find the log at %APPDATA%\MeetingRecorder\recorder.log
        try:
            # Try to show a Windows toast notification as a last-ditch signal
            from win10toast_click import ToastNotifier  # optional dep, may not be installed
            ToastNotifier().show_toast(
                "Meeting Recorder",
                "API key missing — edit %APPDATA%\\MeetingRecorder\\config.json",
                duration=10, threaded=True,
            )
        except Exception:
            pass
        return

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

    def on_dashboard(icon_, item):
        webbrowser.open(cfg["api_url"])

    def on_settings(icon_, item):
        subprocess.Popen(["notepad", str(CONFIG_PATH)])

    def on_logs(icon_, item):
        subprocess.Popen(["notepad", str(LOG_PATH)])

    def on_quit(icon_, item):
        if mgr.in_meeting:
            mgr.stop()
        icon.stop()

    def auto_detect_toggle(icon_, item):
        cfg["auto_detect"] = not cfg["auto_detect"]
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        notify("Auto-detect", "enabled" if cfg["auto_detect"] else "disabled")

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
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
            # Update the live capture so next recording uses new source
            if hasattr(mgr, 'capture'):
                mgr.capture._audio_source = device_name
            notify("Audio source changed", f"Now capturing from: {device_name}")
        return _pick

    def _build_source_menu():
        """Build a submenu of available output devices. Called fresh each time menu opens."""
        try:
            import soundcard as sc
            speakers = sc.all_speakers()
            default_name = sc.default_speaker().name
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

    menu = pystray.Menu(
        pystray.MenuItem(_primary_label,
                         lambda i, it: on_stop(i, it) if mgr.in_meeting else on_start(i, it)),
        pystray.MenuItem("Auto-detect meetings",
                         auto_detect_toggle,
                         checked=lambda i: cfg.get("auto_detect", False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🔊 Test Audio (5s)", _test_audio),
        pystray.MenuItem("Audio Source", _build_source_menu()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Dashboard", on_dashboard),
        pystray.MenuItem("Settings (config.json)", on_settings),
        pystray.MenuItem("View logs", on_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

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

    threading.Thread(target=auto_poller, daemon=True).start()
    log.info("Meeting Recorder started (dual_stream=%s, vocab=%d terms, sound=%s)",
             cfg.get("dual_stream"), len(cfg.get("custom_vocabulary", [])), cfg.get("sound_effects"))
    icon.run()


if __name__ == "__main__":
    main()
