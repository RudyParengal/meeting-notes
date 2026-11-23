"""Screen recorder: the capture engine (video frames, audio, the MP4 writer).

Moved out of screen_record.py unchanged. screen_record.py re-exports every name.
"""
import logging
import threading
import time
from pathlib import Path

from screen_windows import FPS, capture_source, mask_frame

log = logging.getLogger("screen")


# ============================================================ the recording ===
class Capture:
    """Screen + microphone into one MP4. Runs in the child copy."""

    def __init__(self, path: Path, output_idx: int, source=None, mic=True, monitor=None, region=None):
        self.path, self.idx = Path(path), output_idx
        self.want_mic = bool(mic)
        # A made-up test picture instead of the screen, for tests only.
        self.test = bool(source)
        if source:
            self.source = source
        elif monitor is not None:
            self.source = capture_source(output_idx, monitor, region)
        else:
            self.source = capture_source(output_idx, (0, 0, 0, 0), None)
        self.stop_ev = threading.Event()
        self.lock = threading.Lock()
        self._paused_at = None
        self.muted = False
        self.masks = []
        self.error = None
        self.mic_ok = False
        self.encoder = None
        self.frames = 0

    def add_mask(self, rect):
        rx, ry, rw, rh = (int(v) for v in rect)
        if rw > 0 and rh > 0:
            with self.lock:
                self.masks.append((rx, ry, rw, rh))

    def clear_masks(self):
        with self.lock:
            self.masks.clear()

    def mute(self, on: bool):
        with self.lock:
            self.muted = bool(on)

    def pause(self):
        with self.lock:
            if self._paused_at is None:
                self._paused_at = time.monotonic()

    def resume(self):
        with self.lock:
            if self._paused_at is not None:
                self.t0 += time.monotonic() - self._paused_at
                self._paused_at = None

    @property
    def paused(self):
        return self._paused_at is not None

    def start(self):
        import av
        from fractions import Fraction
        self.src = av.open(self.source, format="lavfi")
        first = next(self.src.decode(video=0))
        w, h = first.width - first.width % 2, first.height - first.height % 2
        enc, fmt, opts = self._pick_encoder(w, h)
        mic = self._open_mic() if self.want_mic else None
        # Fragmented MP4: what is written so far stays playable if this process dies.
        self.out = av.open(str(self.path), "w", format="mp4",
                           options={"movflags": "frag_keyframe+empty_moov+default_base_moof"})
        self.v = self.out.add_stream(enc, rate=FPS)
        self.v.width, self.v.height, self.v.pix_fmt = w, h, fmt
        self.v.time_base = Fraction(1, 1000)
        # The encoder must count in milliseconds too. Left at 1/15 s it rounds two
        # close frames onto the same tick and the file writer refuses the second.
        self.v.codec_context.time_base = Fraction(1, 1000)
        self.v.codec_context.gop_size = FPS * 2
        self.v.options = opts
        self.encoder, self.fmt = enc, fmt
        self.a = None
        if mic is not None:
            self.a = self.out.add_stream("aac", rate=48000)
            self.a.layout = "mono"
            self.a.time_base = Fraction(1, 48000)
        self.t0 = time.monotonic()
        self.threads = [threading.Thread(target=self._video, args=(first,), daemon=True, name="screen-video")]
        if mic is not None:
            self.threads.append(threading.Thread(target=self._audio, args=(mic,), daemon=True, name="screen-mic"))
        for t in self.threads:
            t.start()

    @staticmethod
    def _pick_encoder(w, h):
        """Windows' own H.264 encoder (no licence question); the one inside PyAV if
        that is missing. Tried on its own first, so a refusal leaves no half-made
        track in the file."""
        import av
        from fractions import Fraction
        last = None
        for enc, fmt, opts in (("h264_mf", "nv12", {"rate_control": "quality", "quality": "60"}),
                               ("libx264", "yuv420p", {"preset": "veryfast", "crf": "28"})):
            try:
                c = av.CodecContext.create(enc, "w")
                c.width, c.height, c.pix_fmt = w, h, fmt
                c.time_base, c.framerate = Fraction(1, 1000), Fraction(FPS, 1)
                c.options = dict(opts)
                c.open()
                return enc, fmt, opts
            except Exception as e:
                log.warning("video encoder %s unavailable: %s", enc, e)
                last = e
        raise last

    def _open_mic(self):
        if self.test:                       # tests: a tone, never the real microphone
            self.mic_ok = True
            return _ToneMic()
        try:
            import soundcard as sc
            import soundgaps
            soundgaps.install()
            rec = sc.default_microphone().recorder(samplerate=48000, channels=1, blocksize=soundgaps.blocksize(48000))
            rec.__enter__()
            self.mic_ok = True
            return rec
        except Exception as e:
            log.warning("microphone unavailable, recording without sound: %s", e)
            return None

    def _mux(self, packets):
        with self.lock:
            for p in packets:
                self.out.mux(p)

    def _video(self, first):
        import av
        from fractions import Fraction
        try:
            last = -1
            frame = first
            frames = self.src.decode(video=0)
            while True:
                with self.lock:
                    paused = self._paused_at is not None
                    pts = None if paused else int((time.monotonic() - self.t0) * 1000)
                    masks = list(self.masks)
                if not paused:
                    if pts <= last:
                        pts = last + 1
                    if masks:
                        src_f = frame if frame.format.name == "bgra" else frame.reformat(format="bgra")
                        arr = src_f.to_ndarray()
                        mask_frame(arr, masks)
                        frame_in = av.VideoFrame.from_ndarray(arr, format="bgra")
                    else:
                        frame_in = frame
                    f = frame_in.reformat(width=self.v.width, height=self.v.height, format=self.fmt)
                    f.pts, f.time_base = pts, Fraction(1, 1000)
                    self._mux(self.v.encode(f))
                    last = pts
                    self.frames += 1
                if self.stop_ev.is_set():
                    break
                frame = next(frames)
            self._mux(self.v.encode())
        except Exception as e:
            log.exception("screen video stopped")
            self.error = self.error or f"screen capture stopped: {e}"
            self.stop_ev.set()
        finally:
            try:
                self.src.close()
            except Exception:
                pass

    def _audio(self, mic):
        import av
        from fractions import Fraction
        import numpy as np
        try:
            pts = None
            min_pts = 0
            while not self.stop_ev.is_set():
                block = mic.record(numframes=1024)
                with self.lock:
                    paused = self._paused_at is not None
                    muted = self.muted
                    t0 = self.t0
                if paused:
                    pts = None
                    continue
                if muted:
                    block = np.zeros_like(block)
                if pts is None:     # line the sound up with the picture
                    pts = max(min_pts, int((time.monotonic() - t0) * 48000) - len(block))
                frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(block[:, :1].T, dtype=np.float32),
                                                   format="flt", layout="mono")
                frame.sample_rate, frame.pts, frame.time_base = 48000, pts, Fraction(1, 48000)
                pts += len(block)
                min_pts = pts
                self._mux(self.a.encode(frame))
            self._mux(self.a.encode())
        except Exception as e:
            log.exception("screen microphone stopped")
            self.error = self.error or f"microphone stopped: {e}"
        finally:
            try:
                mic.__exit__(None, None, None)
            except Exception:
                pass

    def video_ms(self):
        """Milliseconds since the first frame's clock started: the events file uses this clock."""
        with self.lock:
            return int(((self._paused_at or time.monotonic()) - self.t0) * 1000)

    def stop(self):
        self.resume()
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=15)
        with self.lock:
            self.out.close()
        return time.monotonic() - self.t0


class _ToneMic:
    """Stands in for the microphone in tests: a quiet 440 Hz tone, in real time."""

    def __init__(self):
        self.n = 0
        self.t0 = time.monotonic()

    def record(self, numframes):
        import numpy as np
        due = self.t0 + (self.n + numframes) / 48000
        time.sleep(max(0.0, due - time.monotonic()))
        t = np.arange(self.n, self.n + numframes) / 48000
        self.n += numframes
        return (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32).reshape(-1, 1)

    def __exit__(self, *a):
        pass


