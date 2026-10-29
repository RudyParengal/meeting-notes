r"""Tests for spool.py — the on-disk audio spool.

Run:  python tests/recorder/test_spool.py

No pytest in this venv, so these are plain asserts with a pass/fail tally,
matching test_scenarios.py.

What matters here is the failure modes, not the happy path. The whole point of
the spool is to survive a process that dies mid-write, so most of these tests
are about half-written files.
"""
import os, sys, json, shutil, tempfile
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import spool

SR = 16000

_passed = 0
_failed = []


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def tone(seconds, freq=440, amp=0.5):
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def fresh_dir():
    d = Path(tempfile.mkdtemp(prefix="spooltest_"))
    return d


# ---------------------------------------------------------------
print("\nSpool roundtrip")


def t_roundtrip():
    d = fresh_dir() / "sess1"
    w = spool.SpoolWriter(d, SR)
    a = tone(1.0)
    w.append_system(a)
    w.close()
    back = spool.read_track(d, "sys")
    assert back is not None, "read_track returned None"
    assert len(back) == len(a), f"length {len(back)} != {len(a)}"
    # float32 in, float32 out: this must be exact, not close. Anything less
    # changes the transcript - measured, not assumed.
    assert np.array_equal(back, a), f"max error {np.abs(back-a).max()}"


def t_multiple_appends_concatenate():
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    for _ in range(3):
        w.append_system(tone(0.5))
    w.close()
    back = spool.read_track(d, "sys")
    assert len(back) == int(SR * 1.5), f"got {len(back)} samples, want {int(SR*1.5)}"


def t_two_tracks_independent():
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(1.0, freq=440))
    w.append_mic(tone(2.0, freq=880))
    w.close()
    s = spool.read_track(d, "sys")
    m = spool.read_track(d, "mic")
    assert len(s) == SR, f"sys {len(s)}"
    assert len(m) == 2 * SR, f"mic {len(m)}"


def t_2d_chunk_is_flattened():
    # soundcard hands back shape (n, 1); the spool must cope
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(1.0).reshape(-1, 1))
    w.close()
    assert len(spool.read_track(d, "sys")) == SR


def t_out_of_range_samples_are_preserved():
    # soundcard can hand back values slightly outside [-1, 1]. float32 keeps
    # them exactly; there is no quantisation step to wrap around.
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    original = np.array([2.0, -2.0, 0.0, 1.5], dtype=np.float32)
    w.append_system(original)
    w.close()
    assert np.array_equal(spool.read_track(d, "sys"), original)


check("float32 -> int16 -> float32 within one quantisation step", t_roundtrip)
check("appends concatenate in order", t_multiple_appends_concatenate)
check("system and mic tracks stay independent", t_two_tracks_independent)
check("2-D (n,1) chunk from soundcard is flattened", t_2d_chunk_is_flattened)
check("out-of-range samples are preserved exactly", t_out_of_range_samples_are_preserved)

# ---------------------------------------------------------------
print("\nSurviving a crash mid-write")


def t_unclosed_spool_is_readable():
    # The writer never gets to close() — this is the crash case
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(1.0))
    del w  # no close()
    back = spool.read_track(d, "sys")
    assert back is not None and len(back) == SR, "unclosed spool unreadable"


def t_odd_trailing_byte_reads_to_last_whole_sample():
    # A kill mid-write can leave part of a sample on disk
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(1.0))
    w.close()
    p = spool.track_path(d, "sys")
    with open(p, "ab") as f:
        f.write(b"\x01\x02\x03")  # three stray bytes: a partial float32
    back = spool.read_track(d, "sys")
    assert back is not None, "odd trailing byte made the track unreadable"
    assert len(back) == SR, f"expected {SR} whole samples, got {len(back)}"


def t_truncated_file_reads_what_survived():
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(1.0))
    w.close()
    p = spool.track_path(d, "sys")
    with open(p, "r+b") as f:
        f.truncate(2 * SR)  # half the bytes = half the samples
    back = spool.read_track(d, "sys")
    assert len(back) == SR // 2, f"got {len(back)}, want {SR//2}"


def t_empty_file_returns_none():
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.close()
    assert spool.read_track(d, "sys") is None, "empty track should be None"


def t_missing_track_returns_none_not_exception():
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(0.5))
    w.close()
    spool.track_path(d, "mic").unlink()
    assert spool.read_track(d, "mic") is None, "missing track should be None"


def t_missing_session_dir_returns_none():
    assert spool.read_track(Path(tempfile.gettempdir()) / "nope_not_here", "sys") is None


check("spool never closed is still readable", t_unclosed_spool_is_readable)
check("odd trailing byte reads to last whole sample", t_odd_trailing_byte_reads_to_last_whole_sample)
check("truncated file returns what survived", t_truncated_file_reads_what_survived)
check("empty track returns None", t_empty_file_returns_none)
check("missing track returns None, not an exception", t_missing_track_returns_none_not_exception)
check("missing session dir returns None", t_missing_session_dir_returns_none)

# ---------------------------------------------------------------
print("\nMetadata and orphan detection")


def t_meta_written_at_open():
    d = fresh_dir() / "sess"
    spool.SpoolWriter(d, SR)
    meta = json.loads(spool.meta_path(d).read_text())
    assert meta["sample_rate"] == SR
    assert "started_utc" in meta and "session_id" in meta


def t_open_spool_is_an_orphan():
    # 11s, not a fraction of a second: list_orphans ignores anything under the
    # same 10s floor the recorder uses to discard meetings that are too short.
    root = fresh_dir()
    spool.SpoolWriter(root / "abc", SR).append_system(tone(11.0))
    orphans = spool.list_orphans(root)
    assert len(orphans) == 1, f"expected 1 orphan, got {len(orphans)}"
    assert orphans[0].name == "abc"


def t_closed_spool_is_not_an_orphan():
    root = fresh_dir()
    w = spool.SpoolWriter(root / "abc", SR)
    w.append_system(tone(0.2))
    w.close()
    assert spool.list_orphans(root) == [], "closed spool still reported as orphan"


def t_orphan_with_no_audio_is_ignored():
    # A session that died before any audio arrived is not worth offering
    root = fresh_dir()
    spool.SpoolWriter(root / "empty", SR)
    assert spool.list_orphans(root) == [], "empty orphan should be ignored"


def t_missing_root_returns_empty_list():
    assert spool.list_orphans(Path(tempfile.gettempdir()) / "nope_not_here") == []


check("meta.json written when the spool opens", t_meta_written_at_open)
check("an unclosed spool with audio is an orphan", t_open_spool_is_an_orphan)
check("a closed spool is not an orphan", t_closed_spool_is_not_an_orphan)
def t_orphan_under_ten_seconds_is_ignored():
    root = fresh_dir()
    spool.SpoolWriter(root / "brief", SR).append_system(tone(3.0))
    assert spool.list_orphans(root) == [], "a 3s orphan should be below the floor"


check("an orphan with no audio is ignored", t_orphan_with_no_audio_is_ignored)
check("an orphan under 10s is below the floor", t_orphan_under_ten_seconds_is_ignored)
check("missing spool root returns []", t_missing_root_returns_empty_list)

# ---------------------------------------------------------------
print("\nDisk accounting")


def t_size_matches_expected_rate():
    d = fresh_dir() / "sess"
    w = spool.SpoolWriter(d, SR)
    w.append_system(tone(60.0))  # one minute, one track
    w.close()
    mb = spool.track_path(d, "sys").stat().st_size / 1e6
    # 16000 samples/s * 4 bytes = 64 kB/s = 3.84 MB/min
    assert 3.7 < mb < 4.0, f"one minute of one track = {mb:.2f} MB, expected ~3.84"


check("one minute of one track is ~3.84 MB", t_size_matches_expected_rate)

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
