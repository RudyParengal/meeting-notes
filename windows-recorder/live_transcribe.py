r"""Live transcript: decode the spool a few seconds behind the write head.

Two tiers, the same shape ulpaso uses. A small model decodes short windows
while the meeting runs, purely so there is something to read on screen. The
accurate pass at the end is untouched: distil-large-v3 over the whole meeting,
and that is still the only transcript saved or uploaded.

Nothing here is allowed to matter. If the live tier falls behind, it skips
ahead rather than queueing. If it fails, it stops and the meeting carries on.
The window is a convenience, the recording is the product.

The real risk is CPU. This runs *during* a video call, competing with Zoom or
Teams, so the model is small, the thread count is capped well below the 16
cores available, and there is a flag to turn the whole thing off.
"""
import logging
import threading
import time

import spool

log = logging.getLogger("recorder")

_model = None
_model_lock = threading.Lock()


def get_live_model(cfg):
    """Small, fast model, separate from the accurate one. Loaded once."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            name = cfg.get("live_model", "base")
            _model = WhisperModel(
                name,
                device="cpu",
                compute_type=cfg.get("whisper_compute_type", "int8"),
                cpu_threads=int(cfg.get("live_threads", 4)),
            )
            log.info("Live transcript model loaded: %s", name)
        return _model


class LiveTranscriber:
    """Polls the spool and pushes decoded text into a LiveWindow."""

    def __init__(self, session_dir, cfg, window, sample_rate=16000):
        self.session_dir = session_dir
        self.cfg = cfg
        self.window = window
        self.sample_rate = sample_rate
        self.window_seconds = float(cfg.get("live_window_seconds", 8))
        self.poll_seconds = float(cfg.get("live_poll_seconds", 1.5))
        self._stop = threading.Event()
        self._thread = None
        self._pos = {"sys": 0, "mic": 0}
        self.decoded_windows = 0
        self.decode_seconds = 0.0
        self.skipped_quiet = 0

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="live-transcribe")
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    # ----------------------------------------------------------------
    def _run(self):
        try:
            get_live_model(self.cfg)
            if self.window:
                self.window.set_status("listening...")
        except Exception as e:
            log.warning("live transcript could not start: %s", e)
            if self.window:
                self.window.set_status(f"live transcript unavailable: {type(e).__name__}")
            return

        chunk = int(self.window_seconds * self.sample_rate)
        while not self._stop.is_set():
            did_work = False
            for track, label in (("mic", "Me"), ("sys", "Others")):
                if self._stop.is_set():
                    break
                try:
                    did_work |= self._maybe_decode(track, label, chunk)
                except Exception as e:
                    log.debug("live decode on %s: %s", track, e)
            if not did_work:
                self._stop.wait(self.poll_seconds)
        if self.window:
            self.window.set_status(
                f"meeting ended - {self.decoded_windows} windows, "
                f"{self.decode_seconds:.0f}s of CPU. Transcribing properly now.")

    def _maybe_decode(self, track, label, chunk):
        available = spool.track_samples(self.session_dir, track)
        behind = available - self._pos[track]
        if behind < chunk:
            return False

        # If we have fallen a long way behind, skip forward. A live view that
        # is three minutes stale is worse than one with a gap in it.
        max_behind = chunk * 3
        if behind > max_behind:
            skipped = behind - chunk
            self._pos[track] = available - chunk
            log.debug("live transcript skipped %.0fs of %s to catch up",
                      skipped / self.sample_rate, track)

        audio = spool.read_track_slice(self.session_dir, track,
                                       self._pos[track], chunk)
        if audio is None:
            return False
        self._pos[track] += chunk

        # Skip near-silence rather than decoding it. This is not only about
        # saving CPU: on a quiet eight-second window the small model invents
        # text, and it does not confine itself to English while doing so - a
        # silent window produced Arabic during testing. A gap in a disposable
        # view is much better than confident nonsense in it.
        import numpy as np
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        if rms < float(self.cfg.get("live_min_rms", 0.005)):
            self.skipped_quiet += 1
            return True

        t0 = time.time()
        model = get_live_model(self.cfg)
        segments, _ = model.transcribe(
            audio,
            beam_size=1,                    # greedy: this tier is for speed
            vad_filter=True,
            condition_on_previous_text=False,   # windows are independent
            # Forced, not detected. Eight seconds is not enough for reliable
            # language detection, and a wrong guess turns the window to noise.
            language=self.cfg.get("live_language", "en"),
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        self.decode_seconds += time.time() - t0
        self.decoded_windows += 1
        if text and self.window:
            self.window.append(text, speaker=label)
        return True
