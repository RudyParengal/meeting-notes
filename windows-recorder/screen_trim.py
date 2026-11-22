"""Trim a screen recording on the laptop before it uploads.

Does not import screen_record: the encoder name is passed in so this module
stays standalone and easy to test.
"""
from fractions import Fraction
import os
from pathlib import Path
import re

FPS = 15
ENCODER_SPECS = {
    "h264_mf": ("nv12", {"rate_control": "quality", "quality": "60"}),
    "libx264": ("yuv420p", {"preset": "veryfast", "crf": "28"}),
}


def _parse_clock(text: str) -> float:
    """Parse 'm:ss', 'h:mm:ss', or plain seconds into a non-negative float."""
    s = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Use minutes and seconds, like 0:05.")
    try:
        nums = [int(p) for p in parts[:-1]] + [float(parts[-1])]
    except ValueError:
        raise ValueError("Use minutes and seconds, like 0:05.")
    if any(n < 0 for n in nums) or nums[-1] >= 60 or (len(nums) == 3 and nums[1] >= 60):
        raise ValueError("Use minutes and seconds, like 0:05.")
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def parse_trim(start_text, end_text, seconds):
    """Parse trim fields (m:ss) against a recording of `seconds` length.

    Returns None when both fields are empty (no cut), or (start_s, end_s).
    Raises ValueError with a short plain-English message if the range is invalid.
    """
    st = (start_text or "").strip()
    en = (end_text or "").strip()
    if not st and not en:
        return None
    dur = max(0.0, float(seconds))
    start_s = _parse_clock(st) if st else 0.0
    end_s = _parse_clock(en) if en else dur
    start_s = min(start_s, dur)
    end_s = min(end_s, dur)
    if start_s >= end_s:
        raise ValueError("Start time must be before end time.")
    if end_s - start_s < 1.0:
        raise ValueError("Keep at least 1 second of video.")
    return (start_s, end_s)


def _encoder_spec(encoder):
    name = encoder or "libx264"
    if name not in ENCODER_SPECS:
        raise ValueError(f"unknown encoder: {name}")
    fmt, opts = ENCODER_SPECS[name]
    return name, fmt, dict(opts)


def trim_video(src, dst, start_s, end_s, encoder):
    """Re-encode the kept [start_s, end_s] range of src into dst with pts from 0."""
    import av

    src, dst = Path(src), Path(dst)
    start_s, end_s = float(start_s), float(end_s)
    if start_s < 0 or end_s <= start_s:
        raise ValueError("invalid trim range")
    enc_name, pix_fmt, enc_opts = _encoder_spec(encoder)
    tmp_dst = dst.with_suffix(".mp4.tmp")
    v_count = 0
    try:
        with av.open(str(src)) as inp:
            if not inp.streams.video:
                raise ValueError("source file has no video stream")
            vs = inp.streams.video[0]
            as_ = inp.streams.audio[0] if inp.streams.audio else None
            w = vs.width - vs.width % 2
            h = vs.height - vs.height % 2
            with av.open(str(tmp_dst), "w", format="mp4",
                         options={"movflags": "frag_keyframe+empty_moov+default_base_moof"}) as out:
                v_out = out.add_stream(enc_name, rate=FPS)
                v_out.width, v_out.height, v_out.pix_fmt = w, h, pix_fmt
                v_out.time_base = Fraction(1, 1000)
                v_out.codec_context.time_base = Fraction(1, 1000)
                v_out.codec_context.gop_size = FPS * 2
                v_out.options = enc_opts

                a_out = None
                resampler = None
                if as_ is not None:
                    a_out = out.add_stream("aac", rate=48000)
                    a_out.layout = "mono"
                    a_out.time_base = Fraction(1, 48000)
                    resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)

                last_v_pts = -1
                a_pts = 0
                v_tb = float(vs.time_base)
                a_tb = float(as_.time_base) if as_ is not None else 0.0

                for pkt in inp.demux():
                    if pkt.dts is None and pkt.pts is None:
                        continue
                    if pkt.stream.index == vs.index:
                        for f in pkt.decode():
                            if f.pts is None:
                                continue
                            t = f.pts * v_tb
                            if t < start_s or t > end_s:
                                continue
                            pts = max(0, int(round((t - start_s) * 1000)))
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
                            if af.pts is None:
                                continue
                            t = af.pts * a_tb
                            if t < start_s or t > end_s:
                                continue
                            for rf in resampler.resample(af):
                                rf.pts, rf.time_base = a_pts, Fraction(1, 48000)
                                a_pts += rf.samples
                                for op in a_out.encode(rf):
                                    out.mux(op)

                if v_count == 0:
                    raise ValueError("no video frames in the chosen range")
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
    except BaseException:
        if tmp_dst.exists():
            try:
                tmp_dst.unlink()
            except OSError:
                pass
        raise
