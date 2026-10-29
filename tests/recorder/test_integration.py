r"""Integration test: real speech audio through the spool, against Whisper.

Run:  python tests/recorder/test_integration.py

The unit tests prove the spool returns the same samples. This proves the thing
that actually matters: that the transcript is unchanged.

This test is why the spool stores float32. The first version stored int16,
which is lossless enough on paper - and measurably was not. Whisper is
deterministic (the control below: the same array twice gives byte-identical
text), so the transcripts diverging at 0.90 similarity was down to the
quantisation alone. "Lossless enough on paper" is how this system has been
wrong before.

Needs real speech, so it is OPT-IN. Point it at a folder of .wav recordings
(16-bit PCM, any length) and it picks one:

    set MN_TEST_WAV_DIR=C:\path\to\some\wavs
    python tests/recorder/test_integration.py

With nothing set it prints SKIPPED and exits 0, so CI stays green without
anyone's private recordings. Loads the Whisper model, so it takes a few minutes.
"""
import glob, json, os, sys, time, wave, tempfile
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import spool
import recorder

import os as _os
ARCHIVE = Path(_os.environ["MN_TEST_WAV_DIR"]) if _os.environ.get("MN_TEST_WAV_DIR") else None
if ARCHIVE is None or not ARCHIVE.is_dir():
    print("SKIPPED: set MN_TEST_WAV_DIR to a folder of .wav files to run this test.")
    raise SystemExit(0)
SECONDS = 90            # enough speech to be a real comparison, short enough to run
_failed = []
_passed = 0


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def load_wav(p, seconds=None, skip=0):
    with wave.open(str(p), "rb") as w:
        sr = w.getframerate()
        w.setpos(min(skip * sr, w.getnframes() - 1))
        n = w.getnframes() - skip * sr if seconds is None else int(seconds * sr)
        raw = w.readframes(max(0, min(n, w.getnframes() - skip * sr)))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, sr


def pick_loudest_window(audio, sr, seconds):
    """Find a stretch with actual speech - archived meetings start with silence."""
    win = int(seconds * sr)
    if len(audio) <= win:
        return audio
    step = win // 2
    best, best_rms = 0, -1.0
    for i in range(0, len(audio) - win, step):
        r = float(np.sqrt(np.mean(audio[i:i + win] ** 2)))
        if r > best_rms:
            best_rms, best = r, i
    return audio[best:best + win]


cfg = json.loads(recorder.CONFIG_PATH.read_text())

wavs = sorted(glob.glob(str(ARCHIVE / "*.wav")), key=os.path.getsize)
assert wavs, f"no archived meetings in {ARCHIVE}"
src = wavs[len(wavs) // 3]
print(f"source: {os.path.basename(src)}")

full, SR = load_wav(src)
clip = pick_loudest_window(full, SR, SECONDS)
print(f"clip:   {len(clip)/SR:.0f}s at {SR} Hz, RMS {np.sqrt(np.mean(clip**2)):.4f}")

# ---------------------------------------------------------------
print("\nSpool roundtrip on real meeting audio")

tmp = Path(tempfile.mkdtemp(prefix="integ_"))
d = tmp / "sess"
w = spool.SpoolWriter(d, SR)
for i in range(0, len(clip), SR):          # 1-second chunks, as the recorder does
    w.append_system(clip[i:i + SR])
w.close()
via_spool = spool.read_track(d, "sys")


def t_samples_identical():
    assert len(via_spool) == len(clip), f"{len(via_spool)} != {len(clip)}"
    assert np.array_equal(via_spool, clip), "spool is not bit-identical"


check("sample-for-sample identical, not merely close", t_samples_identical)

# ---------------------------------------------------------------
print("\nTranscribing both paths (loading distil-large-v3, please wait)")

def transcript_of(audio):
    # transcribe() returns a list of timestamped lines, not a string
    return "\n".join(recorder.transcribe(audio, cfg, speaker_label="Others"))


t0 = time.time()
direct = transcript_of(clip)
t_direct = time.time() - t0
t0 = time.time()
spooled = transcript_of(via_spool)
t_spooled = time.time() - t0

print(f"  direct : {len(direct):5d} chars in {t_direct:5.1f}s")
print(f"  spooled: {len(spooled):5d} chars in {t_spooled:5.1f}s")


def t_transcript_not_empty():
    assert len(direct.strip()) > 50, f"direct transcript too short: {direct[:120]!r}"


def t_transcripts_identical():
    if direct == spooled:
        return
    # Not identical: report how far apart, so a near-miss is not read as a pass
    import difflib
    ratio = difflib.SequenceMatcher(None, direct, spooled).ratio()
    raise AssertionError(f"transcripts differ, similarity {ratio:.4f}\n"
                         f"  direct : {direct[:200]!r}\n"
                         f"  spooled: {spooled[:200]!r}")


def t_whisper_is_deterministic():
    """Control. Without this, 'identical transcripts' could pass for the wrong
    reason - a model that varies run to run would make the comparison above
    meaningless, and a model that is deterministic makes any difference the
    spool's fault."""
    again = transcript_of(clip)
    assert again == direct, (
        "the same array transcribed twice gave different text, so the "
        "comparison above cannot attribute a difference to the spool")


check("the clip produces a real transcript", t_transcript_not_empty)
check("Whisper is deterministic on identical input (control)", t_whisper_is_deterministic)
check("spooled audio transcribes identically to direct", t_transcripts_identical)

# ---------------------------------------------------------------
print("\nCrash and recover, end to end")


def t_crash_leaves_a_recoverable_meeting():
    root = tmp / "spoolroot"
    sess = root / "20260830-140000"
    writer = spool.SpoolWriter(sess, SR)
    for i in range(0, len(clip), SR):
        writer.append_system(clip[i:i + SR])
    del writer                                    # the crash: never closed

    orphans = spool.list_orphans(root)
    assert len(orphans) == 1, f"crashed session not found: {orphans}"
    recovered = spool.read_track(orphans[0], "sys")
    assert recovered is not None
    assert abs(len(recovered) - len(clip)) < SR, "recovered audio is the wrong length"
    assert np.array_equal(recovered, clip), "recovered audio differs"


def t_half_written_chunk_still_recovers():
    root = tmp / "spoolroot2"
    sess = root / "20260830-150000"
    writer = spool.SpoolWriter(sess, SR)
    for i in range(0, len(clip), SR):
        writer.append_system(clip[i:i + SR])
    del writer
    with open(spool.track_path(sess, "sys"), "ab") as f:
        f.write(b"\x7f")                          # killed mid-sample
    recovered = spool.read_track(sess, "sys")
    assert recovered is not None and len(recovered) == len(clip)


check("a crashed session is fully recoverable", t_crash_leaves_a_recoverable_meeting)
check("a kill mid-sample still recovers the meeting", t_half_written_chunk_still_recovers)

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
