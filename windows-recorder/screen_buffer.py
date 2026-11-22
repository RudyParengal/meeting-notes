"""Optional rolling last-2-minutes screen buffer (off by default, decision D9).

Records fixed-length segments into HOME/screen/buffer/ and deletes any segment
older than `keep_seconds`. Checks never_record_reasons before every segment and
before every save so password managers and banking pages are never buffered or
saved. All temporary segments are deleted on stop().
"""
from fractions import Fraction
import logging
import os
from pathlib import Path
import threading
import time

log = logging.getLogger("screen")

DEFAULT_KEEP_SECONDS = 120
DEFAULT_SEGMENT_SECONDS = 10


def concat_segments(segments, dst, encoder=None):
    """Join one or more MP4 segment paths into a single playable MP4 at dst."""
    import av
    import screen_trim

    dst = Path(dst)
    if not segments:
        raise ValueError("no segments to join")
    enc_name, pix_fmt, enc_opts = screen_trim._encoder_spec(encoder or "libx264")
    tmp_dst = dst.with_suffix(".mp4.tmp")
    v_count = 0
    try:
        with av.open(str(segments[0])) as probe:
            vs0 = probe.streams.video[0]
            w = vs0.width - vs0.width % 2
            h = vs0.height - vs0.height % 2
            has_audio = bool(probe.streams.audio)

        with av.open(str(tmp_dst), "w", format="mp4",
                     options={"movflags": "frag_keyframe+empty_moov+default_base_moof"}) as out:
            v_out = out.add_stream(enc_name, rate=screen_trim.FPS)
            v_out.width, v_out.height, v_out.pix_fmt = w, h, pix_fmt
            v_out.time_base = Fraction(1, 1000)
            v_out.codec_context.time_base = Fraction(1, 1000)
            v_out.codec_context.gop_size = screen_trim.FPS * 2
            v_out.options = enc_opts

            a_out = None
            resampler = None
            if has_audio:
                a_out = out.add_stream("aac", rate=48000)
                a_out.layout = "mono"
                a_out.time_base = Fraction(1, 48000)
                resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)

            v_offset_ms = 0
            last_v_pts = -1
            a_pts = 0
            for seg in segments:
                seg_max_ms = 0
                with av.open(str(seg)) as inp:
                    if not inp.streams.video:
                        continue
                    vs = inp.streams.video[0]
                    as_ = inp.streams.audio[0] if (has_audio and inp.streams.audio) else None
                    v_tb = float(vs.time_base)
                    for pkt in inp.demux():
                        if pkt.dts is None and pkt.pts is None:
                            continue
                        if pkt.stream.index == vs.index:
                            for f in pkt.decode():
                                if f.pts is None:
                                    continue
                                rel_ms = max(0, int(round(f.pts * v_tb * 1000)))
                                seg_max_ms = max(seg_max_ms, rel_ms)
                                pts = v_offset_ms + rel_ms
                                if pts <= last_v_pts:
                                    pts = last_v_pts + 1
                                rf = f.reformat(width=w, height=h, format=pix_fmt)
                                rf.pts, rf.time_base = pts, Fraction(1, 1000)
                                for op in v_out.encode(rf):
                                    out.mux(op)
                                last_v_pts = pts
                                v_count += 1
                        elif as_ is not None and pkt.stream.index == as_.index:
                            for af in pkt.decode():
                                for rf in resampler.resample(af):
                                    rf.pts, rf.time_base = a_pts, Fraction(1, 48000)
                                    a_pts += rf.samples
                                    for op in a_out.encode(rf):
                                        out.mux(op)
                v_offset_ms = max(last_v_pts + 1, v_offset_ms + seg_max_ms + 67)

            if v_count == 0:
                raise ValueError("no video frames in buffer segments")
            for op in v_out.encode():
                out.mux(op)
            if a_out is not None:
                for rf in resampler.resample(None):
                    rf.pts, rf.time_base = a_pts, Fraction(1, 48000)
                    a_pts += rf.samples
                    for op in a_out.encode(rf):
                        out.mux(op)
                for op in a_out.encode():
                    out.mux(op)
        os.replace(tmp_dst, dst)
        return round(max(0, last_v_pts) / 1000.0, 1)
    except BaseException:
        if tmp_dst.exists():
            try:
                tmp_dst.unlink()
            except OSError:
                pass
        raise


class RollingBuffer:
    """Keeps only the last `keep_seconds` of screen recording in short segments."""

    def __init__(self, root: Path, output_idx=0, keep_seconds=DEFAULT_KEEP_SECONDS,
                 segment_seconds=DEFAULT_SEGMENT_SECONDS, source=None, check_private=None):
        self.dir = Path(root) / "buffer"
        self.output_idx = output_idx
        self.keep_seconds = float(keep_seconds)
        self.segment_seconds = float(segment_seconds)
        self.source = source
        self.check_private = check_private
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._segments = []  # [(Path, duration_s, encoder)]
        self._thread = None
        self._seq = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _private_reasons(self):
        if self.check_private is not None:
            return list(self.check_private())
        if self.source:
            return []
        import screen_record
        screens = screen_record.monitors()
        area = screens[self.output_idx] if self.output_idx < len(screens) else screens[0]
        return screen_record.never_record_reasons(screen_record.visible_windows(area))

    def start(self):
        if self.running:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="screen-buffer")
        self._thread.start()

    def _prune(self):
        total = 0.0
        keep = []
        for item in reversed(self._segments):
            if total < self.keep_seconds or not keep:
                keep.append(item)
                total += item[1]
            else:
                try:
                    item[0].unlink()
                except OSError:
                    pass
        self._segments = list(reversed(keep))

    def _loop(self):
        import screen_record
        while not self._stop.is_set():
            if self._private_reasons():
                self._stop.wait(1.0)
                continue
            self._seq += 1
            seg_path = self.dir / f"seg-{self._seq:06d}.mp4"
            cap = screen_record.Capture(seg_path, self.output_idx, source=self.source)
            try:
                cap.start()
            except Exception as e:
                log.warning("rolling buffer could not start segment: %s", e)
                self._stop.wait(2.0)
                continue
            self._stop.wait(self.segment_seconds)
            try:
                dur = cap.stop()
            except Exception as e:
                log.warning("rolling buffer segment stop failed: %s", e)
                try:
                    seg_path.unlink()
                except OSError:
                    pass
                continue
            if seg_path.exists() and seg_path.stat().st_size > 0 and cap.frames > 0:
                with self._lock:
                    self._segments.append((seg_path, max(0.1, dur), cap.encoder))
                    self._prune()
            else:
                try:
                    seg_path.unlink()
                except OSError:
                    pass

    def segment_paths(self):
        with self._lock:
            return [p for p, _, _ in self._segments if p.exists()]

    def save_last(self, box, title=None):
        """Save the buffered segments into `box` as a ready job after a private check."""
        reasons = self._private_reasons()
        if reasons:
            return "blocked", reasons
        with self._lock:
            snap = [(p, d, enc) for p, d, enc in self._segments if p.exists()]
        if not snap:
            return "empty", None
        paths = [p for p, _, _ in snap]
        encoder = snap[-1][2] or "libx264"
        label = title or time.strftime("Last 2 minutes %d %b %Y %H:%M").replace(" 0", " ", 1)
        job = box.new_job(label)
        try:
            seconds = concat_segments(paths, box.video(job["client_id"]), encoder=encoder)
        except Exception as e:
            log.exception("could not save rolling buffer")
            box.move_aside(job, "failed")
            return "error", str(e)
        job.update(state="ready", seconds=seconds, stopped_at=time.time(), encoder=encoder,
                   from_buffer=True)
        box.save(job)
        return "saved", job

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
            self._thread = None
        with self._lock:
            for p, _, _ in self._segments:
                try:
                    p.unlink()
                except OSError:
                    pass
            self._segments.clear()
        if self.dir.exists():
            for p in self.dir.glob("seg-*.mp4"):
                try:
                    p.unlink()
                except OSError:
                    pass
