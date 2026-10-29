r"""Tests for the crash-resilience changes in recorder.py.

Run:  python tests/recorder/test_capture.py

Covers the spool wiring in DualAudioCapture, the backup-track fix, and
retention. No audio devices are touched: the capture object is driven directly
through _store() and stop(), which is exactly what the record threads do.
"""
import io, os, sys, time, wave, shutil, tempfile
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import spool
import recorder

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


class _TempAppdir:
    """Point recorder's APPDIR/ARCHIVE_ROOT at a temp dir for one test."""

    def __enter__(self):
        self.d = Path(tempfile.mkdtemp(prefix="reczz_"))
        self._old = (recorder.APPDIR, recorder.ARCHIVE_ROOT, recorder.SPOOL_ROOT)
        recorder.APPDIR = self.d
        recorder.ARCHIVE_ROOT = self.d / "MeetingArchive"
        recorder.SPOOL_ROOT = self.d / "spool"
        return self.d

    def __exit__(self, *a):
        recorder.APPDIR, recorder.ARCHIVE_ROOT, recorder.SPOOL_ROOT = self._old


def wav_seconds(p):
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def wav_peak(p):
    with wave.open(str(p), "rb") as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return float(np.abs(a).max()) / 32768.0


# ---------------------------------------------------------------
print("\nThe backup-track bug (the reason for this work)")


def t_both_tracks_written():
    with _TempAppdir():
        paths = recorder.save_backup_wavs(tone(1.0), tone(2.0), SR, stamp=111)
        assert len(paths) == 2, f"expected 2 files, got {len(paths)}"
        names = sorted(p.name for p in paths)
        assert names == ["last_meeting_111_mic.wav", "last_meeting_111_sys.wav"], names


def t_in_person_meeting_keeps_the_mic():
    """The 25 August case: system track is room tone, mic has the meeting.

    Old behaviour saved the system track and threw the mic away.
    """
    with _TempAppdir():
        room_tone = tone(30.0, amp=0.004)     # what the speakers picked up
        real_speech = tone(30.0, amp=0.5)     # what the microphone heard
        paths = recorder.save_backup_wavs(room_tone, real_speech, SR, stamp=222)
        mic = [p for p in paths if p.name.endswith("_mic.wav")]
        assert mic, "microphone track was not backed up - this is the original bug"
        assert wav_peak(mic[0]) > 0.4, f"mic backup is not the loud track: {wav_peak(mic[0])}"
        assert wav_seconds(mic[0]) == 30.0


def t_single_stream_writes_only_system():
    with _TempAppdir():
        paths = recorder.save_backup_wavs(tone(1.0), None, SR, stamp=333)
        assert len(paths) == 1 and paths[0].name.endswith("_sys.wav"), [p.name for p in paths]


def t_no_audio_writes_nothing_and_does_not_raise():
    with _TempAppdir():
        assert recorder.save_backup_wavs(None, None, SR, stamp=444) == []
        assert recorder.save_backup_wavs(np.array([], dtype=np.float32), None, SR) == []


def t_backup_clamps_instead_of_wrapping():
    with _TempAppdir():
        loud = np.array([2.0, -2.0, 0.5], dtype=np.float32)
        p = recorder.save_backup_wavs(loud, None, SR, stamp=555)[0]
        with wave.open(str(p), "rb") as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        assert a[0] > 32000 and a[1] < -32000, f"clipped sample wrapped: {a[:2]}"


check("both tracks are backed up", t_both_tracks_written)
check("in-person meeting keeps the microphone track", t_in_person_meeting_keeps_the_mic)
check("single-stream writes only the system track", t_single_stream_writes_only_system)
check("no audio writes nothing and does not raise", t_no_audio_writes_nothing_and_does_not_raise)
check("backup clamps loud samples instead of wrapping", t_backup_clamps_instead_of_wrapping)

# ---------------------------------------------------------------
print("\nSpool wiring in DualAudioCapture")


def _capture(spool_enabled, tmp):
    c = recorder.DualAudioCapture(SR)
    c._spool_enabled = spool_enabled
    c.recording = True
    c.system_frames, c.mic_frames = [], []
    if spool_enabled:
        c.session_dir = tmp / "sess"
        c._spool = spool.SpoolWriter(c.session_dir, SR)
    return c


def t_store_goes_to_disk_not_memory():
    with _TempAppdir() as tmp:
        c = _capture(True, tmp)
        for _ in range(3):
            c._store("sys", tone(1.0))
        assert c.system_frames == [], "audio was kept in memory as well as spooled"
        assert spool.track_seconds(c.session_dir, "sys") == 3.0


def t_store_falls_back_to_memory_when_spool_breaks():
    with _TempAppdir() as tmp:
        c = _capture(True, tmp)
        c._store("sys", tone(1.0))
        c._spool.failed = "simulated disk full"     # break it mid-meeting
        c._store("sys", tone(1.0))
        c._store("sys", tone(1.0))
        assert len(c.system_frames) == 2, f"expected 2 chunks in memory, got {len(c.system_frames)}"


def t_stop_joins_spool_and_memory_remainder():
    """A spool that dies halfway must not cost the second half of the meeting."""
    with _TempAppdir() as tmp:
        c = _capture(True, tmp)
        c._store("sys", tone(1.0))
        c._spool.failed = "simulated disk full"
        c._store("sys", tone(1.0))
        c._mic_available = False
        c.recording = False
        sys_audio, mic_audio, duration = c.stop()
        assert sys_audio is not None, "no audio returned"
        assert abs(len(sys_audio) - 2 * SR) < 2, f"got {len(sys_audio)} samples, want {2*SR}"
        assert abs(duration - 2.0) < 0.01, duration


def t_spool_disabled_keeps_old_behaviour():
    with _TempAppdir() as tmp:
        c = _capture(False, tmp)
        c._store("sys", tone(1.0))
        c._store("mic", tone(1.0))
        assert len(c.system_frames) == 1 and len(c.mic_frames) == 1
        c.recording = False
        sys_audio, mic_audio, duration = c.stop()
        assert sys_audio is not None and mic_audio is not None
        assert abs(duration - 1.0) < 0.01


def t_stop_survives_a_spool_that_never_opened():
    with _TempAppdir() as tmp:
        c = _capture(False, tmp)
        c.session_dir = tmp / "does_not_exist"      # as if the spool failed to open
        c._store("sys", tone(1.0))
        c.recording = False
        sys_audio, _, duration = c.stop()
        assert sys_audio is not None and abs(duration - 1.0) < 0.01


check("captured audio goes to disk, not memory", t_store_goes_to_disk_not_memory)
check("a broken spool falls back to memory", t_store_falls_back_to_memory_when_spool_breaks)
check("stop() joins the spooled and in-memory halves", t_stop_joins_spool_and_memory_remainder)
check("spool disabled keeps the original behaviour", t_spool_disabled_keeps_old_behaviour)
check("stop() survives a spool dir that never existed", t_stop_survives_a_spool_that_never_opened)

# ---------------------------------------------------------------
print("\nRetention")


def t_archive_moves_files_out_of_appdata():
    with _TempAppdir() as tmp:
        paths = recorder.save_backup_wavs(tone(1.0), tone(1.0), SR, stamp=777)
        moved = recorder.archive_backups(paths, {"archive_backups": True})
        assert len(moved) == 2, moved
        assert all(p.exists() for p in moved), "archived files missing"
        assert not any(p.exists() for p in paths), "originals still in APPDIR"
        assert "MeetingArchive" in str(moved[0])


def t_archive_can_be_switched_off():
    with _TempAppdir():
        paths = recorder.save_backup_wavs(tone(1.0), None, SR, stamp=888)
        assert recorder.archive_backups(paths, {"archive_backups": False}) == []
        assert paths[0].exists(), "file was moved despite archiving being off"


def t_prune_removes_old_keeps_new():
    with _TempAppdir():
        paths = recorder.save_backup_wavs(tone(1.0), tone(1.0), SR, stamp=999)
        moved = recorder.archive_backups(paths, {"archive_backups": True})
        old, new = moved[0], moved[1]
        ancient = time.time() - 40 * 86400
        os.utime(old, (ancient, ancient))
        removed = recorder.prune_archive({"archive_retention_days": 30})
        assert removed == 1, f"pruned {removed}, expected 1"
        assert not old.exists() and new.exists()


def t_prune_disabled_by_zero_days():
    with _TempAppdir():
        paths = recorder.save_backup_wavs(tone(1.0), None, SR, stamp=1001)
        moved = recorder.archive_backups(paths, {"archive_backups": True})
        ancient = time.time() - 400 * 86400
        os.utime(moved[0], (ancient, ancient))
        assert recorder.prune_archive({"archive_retention_days": 0}) == 0
        assert moved[0].exists(), "retention 0 should mean keep forever"


def t_spool_discarded_only_after_upload():
    with _TempAppdir() as tmp:
        d = tmp / "spool" / "sess"
        w = spool.SpoolWriter(d, SR)
        w.append_system(tone(1.0))
        w.close()
        assert d.exists()
        assert spool.discard(d) is True
        assert not d.exists()


check("archiving moves backups out of AppData", t_archive_moves_files_out_of_appdata)
check("archiving can be switched off", t_archive_can_be_switched_off)
check("prune removes old and keeps new", t_prune_removes_old_keeps_new)
check("retention of 0 days keeps everything", t_prune_disabled_by_zero_days)
check("a spool can be discarded after upload", t_spool_discarded_only_after_upload)

# ---------------------------------------------------------------
print("\nCrash recovery")


def t_orphan_is_found_and_measured():
    with _TempAppdir() as tmp:
        d = tmp / "spool" / "20260830-120000"
        w = spool.SpoolWriter(d, SR)
        w.append_system(tone(20.0))
        del w                                       # crash: never closed
        orphans = spool.list_orphans(tmp / "spool")
        assert len(orphans) == 1, orphans
        assert abs(spool.track_seconds(orphans[0], "sys") - 20.0) < 0.1


def t_short_orphan_is_discarded_not_offered():
    with _TempAppdir() as tmp:
        d = tmp / "spool" / "tiny"
        w = spool.SpoolWriter(d, SR)
        w.append_system(tone(3.0))
        del w
        assert spool.list_orphans(tmp / "spool") == []


check("a crashed session is found and its length known", t_orphan_is_found_and_measured)
check("an orphan under the floor is not offered", t_short_orphan_is_discarded_not_offered)

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
