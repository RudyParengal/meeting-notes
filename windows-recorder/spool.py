"""On-disk audio spool for the Meeting Recorder.

Why this exists
---------------
The recorder used to hold a whole meeting in memory as lists of numpy chunks
and write nothing to disk until the meeting stopped. A crash before that point
lost the recording outright, and peak memory grew with meeting length — about
350 MB for a 46-minute meeting, before Whisper loaded on top.

This module writes each captured chunk straight to disk instead. A crash then
costs at most the chunk in flight, and the audio for a dead session is sitting
there in a readable form afterwards.

Format
------
Raw little-endian float32 mono, headerless, one file per track:

    <session_dir>/sys.pcm     system audio  -> the far end
    <session_dir>/mic.pcm     microphone    -> the local user
    <session_dir>/meta.json   sample rate, session id, start time
    <session_dir>/DONE        written by close(); its absence means "died here"

Headerless is the point. There is no length field to keep consistent, so an
append is always valid and a file cut off mid-write is still readable up to its
last whole sample. A WAV header would have to be rewritten on every append and
would be wrong in exactly the case this module exists to survive.

float32, not int16
------------------
The first version stored int16 to halve the disk cost, on the reasoning that
the backup WAV already used that precision. Measured on real meeting audio,
that was wrong: quantising to int16 changes the transcript. Whisper is
deterministic here - the same array transcribed twice is byte-identical - so
the difference was caused by the quantisation and nothing else. A 60-second
clip came back at 0.90 similarity, differing on words like "at it with a
phone" against "at a tone".

The spool is the primary source of the transcript, not a backup of it, so
paying 2x disk to keep the audio bit-identical to what was captured is the
right trade. It costs 3.84 MB per minute per track, deleted once the
transcript uploads.
"""
import io
import json
import os
import time
import uuid
from pathlib import Path

import numpy as np

SAMPLE_WIDTH = 4          # float32
TRACKS = ("sys", "mic")
_DONE = "DONE"
_META = "meta.json"


def track_path(session_dir, track):
    return Path(session_dir) / f"{track}.pcm"


def meta_path(session_dir):
    return Path(session_dir) / _META


def done_path(session_dir):
    return Path(session_dir) / _DONE


def _to_bytes(chunk):
    """Captured audio -> little-endian float32 bytes, unaltered.

    soundcard hands back shape (n, 1); flatten it. Nothing is scaled or
    clamped: the point of float32 is that what comes back out is exactly what
    went in, so the transcript cannot change.
    """
    return np.asarray(chunk, dtype=np.float32).reshape(-1).astype("<f4").tobytes()


class SpoolWriter:
    """Appends captured audio to disk, one file per track.

    Deliberately forgiving: a write that fails sets `self.failed` and is
    swallowed, so a full disk degrades the spool rather than killing the
    capture thread it runs on. The caller checks `failed` and falls back to
    the in-memory path.
    """

    def __init__(self, session_dir, sample_rate=16000, session_id=None):
        self.dir = Path(session_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sample_rate = sample_rate
        self.session_id = session_id or uuid.uuid4().hex
        self.failed = None
        self._files = {}
        for t in TRACKS:
            # Small buffer, flushed per append. The data rate is 64 kB/s per
            # track, so this is not a bottleneck, and it keeps the window of
            # loss to the chunk in flight.
            self._files[t] = open(track_path(self.dir, t), "ab", buffering=io.DEFAULT_BUFFER_SIZE)
        meta_path(self.dir).write_text(json.dumps({
            "session_id": self.session_id,
            "sample_rate": sample_rate,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sample_width": SAMPLE_WIDTH,
            "encoding": "pcm_f32le_mono",
        }, indent=2), encoding="utf-8")

    def _append(self, track, chunk):
        if self.failed is not None:
            return
        try:
            f = self._files[track]
            f.write(_to_bytes(chunk))
            f.flush()
        except Exception as e:                      # disk full, handle closed, ...
            self.failed = f"{type(e).__name__}: {e}"

    def append_system(self, chunk):
        self._append("sys", chunk)

    def append_mic(self, chunk):
        self._append("mic", chunk)

    def close(self):
        for f in self._files.values():
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except Exception:
                pass
        self._files = {}
        try:
            done_path(self.dir).write_text(
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
        except Exception:
            pass

    def __del__(self):
        # A dropped writer must not hold the files open — the recovery path
        # needs to read them.
        for f in list(self._files.values()):
            try:
                f.close()
            except Exception:
                pass


def read_track(session_dir, track):
    """Read one track as float32, or None if there is nothing.

    Tolerates a file cut off mid-sample: a partial trailing sample is dropped
    and everything before it is returned. That is the whole reason for the
    headerless format.
    """
    p = track_path(session_dir, track)
    try:
        raw = p.read_bytes()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    usable = len(raw) - (len(raw) % SAMPLE_WIDTH)
    if usable <= 0:
        return None
    return np.frombuffer(raw[:usable], dtype="<f4").astype(np.float32)


def track_samples(session_dir, track):
    """How many whole samples are on disk right now. Cheap: a stat, no read."""
    try:
        return track_path(session_dir, track).stat().st_size // SAMPLE_WIDTH
    except OSError:
        return 0


def read_track_slice(session_dir, track, start_sample, count):
    """Read `count` samples from `start_sample`, or None if they are not there yet.

    This is what the live transcript reads from, behind the write head. It
    deliberately returns None rather than a short read: a partial window would
    be decoded and then decoded again when the rest arrived, duplicating text
    in the window.
    """
    import numpy as np
    p = track_path(session_dir, track)
    want = count * SAMPLE_WIDTH
    offset = start_sample * SAMPLE_WIDTH
    try:
        if p.stat().st_size < offset + want:
            return None
        with open(p, "rb") as f:
            f.seek(offset)
            raw = f.read(want)
    except OSError:
        return None
    if len(raw) < want:
        return None
    return np.frombuffer(raw, dtype="<f4").astype(np.float32)


def track_seconds(session_dir, track, sample_rate=16000):
    """Length of a track in seconds, read from the file size — no decoding."""
    try:
        return track_path(session_dir, track).stat().st_size // SAMPLE_WIDTH / sample_rate
    except OSError:
        return 0.0


def read_meta(session_dir):
    try:
        return json.loads(meta_path(session_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_orphans(spool_root, min_seconds=10.0):
    """Session directories that were never closed and hold real audio.

    An orphan means the recorder died mid-meeting. Sessions holding less than
    `min_seconds` on both tracks are ignored — the recorder already discards
    meetings under 10 seconds, so offering to recover one would be noise.
    """
    root = Path(spool_root)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or done_path(d).exists():
            continue
        sr = read_meta(d).get("sample_rate", 16000)
        if max(track_seconds(d, t, sr) for t in TRACKS) >= min_seconds:
            out.append(d)
    return out


def discard(session_dir):
    """Delete a spool. Called once the transcript is safely uploaded."""
    import shutil
    try:
        shutil.rmtree(session_dir)
        return True
    except OSError:
        return False
