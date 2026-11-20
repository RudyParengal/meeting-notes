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
        recorder.note_sent(999, None, "s999", {"api_url": "http://far.test"})   # 2.7.0: only sent meetings clear
        removed = recorder.prune_archive({"archive_retention_days": 30}, has_note=lambda e, c: True,
                                         recycle=lambda ps: [p.unlink() or p for p in ps])
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
print("\nCleaning up the right recording (2.2.1)")


def _manager(**overrides):
    """A real MeetingManager that touches no sound device, model or network."""
    cfg = dict(recorder.DEFAULT_CONFIG, sound_effects=False, auto_detect=False,
               notify_on_complete=False, live_transcript=False)
    cfg.update(overrides)
    notes = []
    mgr = recorder.MeetingManager(cfg, lambda *a: notes.append(a))
    mgr.notes = notes
    return mgr


def _spooled(d, sys_audio, mic_audio=None, close=True):
    w = spool.SpoolWriter(d, SR)
    w.append_system(sys_audio)
    if mic_audio is not None:
        w.append_mic(mic_audio)
    if close:
        w.close()
    else:
        del w
    return d


class _Stubbed:
    """Swap the model and the upload for stand-ins, and put them back after."""

    def __init__(self, deliver=None):
        self.heard = []          # (seconds of call audio, seconds of microphone)
        self.sent = []           # durations handed to the upload
        self._deliver = deliver

    def __enter__(self):
        self._real = (recorder.transcribe_dual, recorder.deliver)

        def transcribe(system_audio, mic_audio, cfg):
            self.heard.append(tuple(0 if a is None else len(a) / SR
                                    for a in (system_audio, mic_audio)))
            return "a real sentence somebody said in the meeting"

        def deliver(transcript, duration, cfg):
            self.sent.append(duration)
            if self._deliver:
                self._deliver()
            return "session-1", "http://localhost"

        recorder.transcribe_dual, recorder.deliver = transcribe, deliver
        return self

    def __exit__(self, *exc):
        recorder.transcribe_dual, recorder.deliver = self._real


def t_finish_removes_its_own_recording_not_the_next_one():
    """The 2.2.0 fault: a new recording started while the last one was being
    written up, and the upload then cleaned up the NEW folder."""
    with _TempAppdir() as tmp:
        old = _spooled(tmp / "spool" / "old", tone(12.0), tone(12.0))
        new = _spooled(tmp / "spool" / "new", tone(12.0), tone(12.0))
        mgr = _manager()
        mgr.capture.session_dir = old

        def next_meeting_starts():
            mgr.capture.session_dir = new

        with _Stubbed(deliver=next_meeting_starts) as s:
            mgr._finish()
        assert s.sent, f"the upload never ran: {mgr.notes}"
        assert not old.exists(), "the finished recording was left behind"
        assert new.exists(), "the NEXT recording's audio was deleted"


check("finishing cleans up its own recording, not the next one",
      t_finish_removes_its_own_recording_not_the_next_one)

# ---------------------------------------------------------------
print("\nStopping by itself after a long silence (2.2.1)")

SPEECH, CLICK, ROOM = 0.05, 0.04, 0.0002     # loudness of one second


def t_one_click_does_not_restart_the_quiet_clock():
    c = recorder.DualAudioCapture(SR)
    c._last_sound = time.monotonic() - 1000
    c._note_level("mic", CLICK)
    c._note_level("mic", ROOM)
    c._note_level("sys", CLICK)
    c._note_level("mic", CLICK)
    assert c.quiet_seconds() >= 1000, "a single loud second restarted the clock"


def t_two_loud_seconds_restart_the_quiet_clock():
    c = recorder.DualAudioCapture(SR)
    c._last_sound = time.monotonic() - 1000
    c._note_level("sys", SPEECH)
    c._note_level("sys", SPEECH)
    assert c.quiet_seconds() < 5, "real sound did not restart the clock"


def _recording(minutes_quiet, **cfg):
    mgr = _manager(**cfg)
    mgr._finish = lambda: None
    mgr.in_meeting = True
    mgr.capture._last_sound = time.monotonic() - minutes_quiet * 60
    return mgr


def t_long_silence_stops_the_recording():
    mgr = _recording(16)
    told = []
    mgr.on_auto_stop = lambda: told.append(1)
    assert mgr.check_auto_stop() is True
    assert mgr.in_meeting is False, "still recording"
    assert told == [1], "the tray icon was never told"
    assert any("stopped" in n[0].lower() for n in mgr.notes), mgr.notes


def t_short_silence_does_not_stop_the_recording():
    mgr = _recording(14)
    assert mgr.check_auto_stop() is False
    assert mgr.in_meeting is True, "a 14-minute pause ended the recording"


def t_auto_stop_can_be_switched_off():
    mgr = _recording(600, auto_stop_silence_minutes=0)
    assert mgr.check_auto_stop() is False
    assert mgr.in_meeting is True


def t_forgotten_recording_stops_without_anyone_asking():
    real = recorder.AUTO_STOP_CHECK_SECONDS
    recorder.AUTO_STOP_CHECK_SECONDS = 0.05
    try:
        mgr = _recording(16)
        mgr._watch_silence()
        deadline = time.time() + 3
        while mgr.in_meeting and time.time() < deadline:
            time.sleep(0.02)
        assert mgr.in_meeting is False, "nothing was watching the recording"
    finally:
        recorder.AUTO_STOP_CHECK_SECONDS = real


check("one click does not restart the quiet clock", t_one_click_does_not_restart_the_quiet_clock)
check("two loud seconds in a row restart it", t_two_loud_seconds_restart_the_quiet_clock)
check("16 minutes of silence stops the recording", t_long_silence_stops_the_recording)
check("14 minutes of silence does not", t_short_silence_does_not_stop_the_recording)
check("a setting of 0 switches auto-stop off", t_auto_stop_can_be_switched_off)
check("a forgotten recording stops with nobody asking", t_forgotten_recording_stops_without_anyone_asking)

# ---------------------------------------------------------------
print("\nRecovering a recording that ran for hours (2.2.1)")

HALF_MINUTE = dict(auto_stop_silence_minutes=0.5)      # a 30-second limit, for speed


def _quiet(seconds):
    return np.zeros(int(SR * seconds), dtype=np.float32)


def t_recovery_keeps_the_meeting_and_drops_the_empty_room():
    with _TempAppdir() as tmp:
        sys_audio = np.concatenate([tone(20.0), _quiet(40.0), tone(20.0)])
        d = _spooled(tmp / "spool" / "ran-on", sys_audio, _quiet(80.0), close=False)
        cfg = dict(recorder.DEFAULT_CONFIG, **HALF_MINUTE)
        whole_file = spool.read_track

        def refuse(*a):
            raise AssertionError("recovery loaded a whole track into memory")

        spool.read_track = refuse
        try:
            with _Stubbed() as s:
                sid = recorder.recover_orphan(d, cfg, lambda *a: None)
        finally:
            spool.read_track = whole_file
        assert sid == "session-1", sid
        heard = s.heard[0][0]
        assert 40 <= heard <= 52, f"transcribed {heard:.0f}s of an 80s file holding 40s of sound"
        assert abs(s.sent[0] - heard) < 1, f"reported {s.sent[0]}s for {heard:.0f}s of audio"
        assert not d.exists(), "spool kept after a confirmed upload"


def t_recovery_leaves_an_ordinary_recording_whole():
    with _TempAppdir() as tmp:
        sys_audio = np.concatenate([tone(20.0), _quiet(10.0), tone(20.0)])
        d = _spooled(tmp / "spool" / "normal", sys_audio, _quiet(50.0), close=False)
        cfg = dict(recorder.DEFAULT_CONFIG, **HALF_MINUTE)
        with _Stubbed() as s:
            recorder.recover_orphan(d, cfg, lambda *a: None)
        assert abs(s.heard[0][0] - 50) < 0.1, f"a 10s pause was cut: {s.heard[0][0]:.1f}s of 50s"
        assert abs(s.sent[0] - 50) < 0.1, s.sent


def t_recovery_never_throws_away_a_silent_recording():
    with _TempAppdir() as tmp:
        d = _spooled(tmp / "spool" / "silent", _quiet(60.0), _quiet(60.0), close=False)
        cfg = dict(recorder.DEFAULT_CONFIG, **HALF_MINUTE)
        with _Stubbed() as s:
            sid = recorder.recover_orphan(d, cfg, lambda *a: None)
        assert sid is None and not s.sent, "a silent recording was uploaded as a meeting"
        assert d.exists(), "a silent recording was deleted"


check("recovery keeps the meeting and drops the empty room",
      t_recovery_keeps_the_meeting_and_drops_the_empty_room)
check("recovery leaves an ordinary recording whole", t_recovery_leaves_an_ordinary_recording_whole)
check("recovery never throws away a silent recording", t_recovery_never_throws_away_a_silent_recording)

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
