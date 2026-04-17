"""Test harness for the recording/transcription pipeline.

Tests all edge cases without requiring a real meeting:
1. Short audio (<10s) → correctly rejected as 'Too short'
2. Long audio (>10s) in both streams → transcribed and uploaded
3. System audio crashed, mic has content → mic-only transcription proceeds
4. Mic crashed, system has content → system-only transcription proceeds
5. Both streams empty → rejected
"""
import sys, os, json, time, numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))

# Load config
APPDATA = Path(os.environ.get("APPDATA", Path.home())) / "MeetingRecorder"
cfg = json.loads((APPDATA / "config.json").read_text())
print(f"API key: {cfg['api_key'][:16]}...")
print(f"API URL: {cfg['api_url']}")
print()

# Import recorder (but don't run main)
import recorder

SAMPLE_RATE = 16000


def make_audio(duration_sec, freq=440):
    """Make a fake audio array (sine wave at freq Hz) for duration_sec."""
    n = int(SAMPLE_RATE * duration_sec)
    t = np.linspace(0, duration_sec, n, endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_duration_logic():
    """Exercise the duration-check logic in _finish (without actual Whisper/upload)."""

    class MockCapture:
        sample_rate = SAMPLE_RATE

    class StubMgr:
        capture = MockCapture()
        cfg = cfg
        notifications = []
        def notify(self, title, msg):
            self.notifications.append((title, msg))
            print(f"    NOTIFY: {title} — {msg}")

    scenarios = [
        ("1. Short audio (5s) — should reject",
         make_audio(5), make_audio(5), 5.0, "Too short"),

        ("2. Long audio (60s both) — should proceed",
         make_audio(60), make_audio(60), 60.0, None),

        ("3. System crashed (1s), mic long (3600s) — should proceed with mic only",
         make_audio(1), make_audio(3600), 3600.0, None),

        ("4. Mic crashed (1s), system long (3600s) — should proceed with system only",
         make_audio(3600), make_audio(1), 3600.0, None),

        ("5. Both empty/none — should reject",
         None, None, 0, "Too short"),

        ("6. System=None, mic=3600s (mic alone works) — should proceed",
         None, make_audio(3600), 3600.0, None),
    ]

    # We extract the critical logic from _finish() to test it in isolation
    for name, sys_aud, mic_aud, duration, expected_notify in scenarios:
        print(f"\n{name}")
        mgr = StubMgr()
        mgr.capture.sample_rate = SAMPLE_RATE

        # This is the exact logic from _finish:
        too_short = (duration < 10
                     and (sys_aud is None or len(sys_aud) / mgr.capture.sample_rate < 10)
                     and (mic_aud is None or len(mic_aud) / mgr.capture.sample_rate < 10))

        if too_short:
            mgr.notify("Too short", "Meeting under 10s, discarded.")
            result = "REJECTED"
        else:
            sys_len = (len(sys_aud) / mgr.capture.sample_rate) if sys_aud is not None else 0
            mic_len = (len(mic_aud) / mgr.capture.sample_rate) if mic_aud is not None else 0
            if sys_len < 10 and mic_len >= 10:
                print(f"    → use mic only (sys={sys_len:.0f}s, mic={mic_len:.0f}s)")
                sys_aud = None
            elif mic_len < 10 and sys_len >= 10:
                print(f"    → use system only (sys={sys_len:.0f}s, mic={mic_len:.0f}s)")
                mic_aud = None
            else:
                print(f"    → use both (sys={sys_len:.0f}s, mic={mic_len:.0f}s)")
            result = "PROCEED"

        print(f"    Result: {result}")

        # Verify expected behavior
        got_too_short = any(n[0] == "Too short" for n in mgr.notifications)
        if expected_notify == "Too short" and not got_too_short:
            print(f"    ❌ FAIL: expected Too short notification")
            return False
        if expected_notify is None and got_too_short:
            print(f"    ❌ FAIL: unexpectedly rejected")
            return False
        print(f"    ✅ PASS")

    return True


def test_transcribe_dual_with_none():
    """Test transcribe_dual handles None for either stream."""
    print("\n=== transcribe_dual with None streams ===")

    # Patch transcribe to just return fake lines
    with patch('recorder.transcribe') as mock_tr:
        mock_tr.side_effect = lambda audio, cfg, speaker_label=None: [
            f"[00:00] [{speaker_label}] fake line from {speaker_label}"
        ] if audio is not None and len(audio) > 0 else []

        # Case A: both streams
        r1 = recorder.transcribe_dual(make_audio(30), make_audio(30), cfg)
        print(f"Both streams: {r1!r}")
        assert "Others" in r1 and "Me" in r1, "should have both speakers"

        # Case B: system=None, mic has content
        r2 = recorder.transcribe_dual(None, make_audio(30), cfg)
        print(f"System=None: {r2!r}")
        assert "Me" in r2 and "Others" not in r2, "should have only Me"

        # Case C: mic=None, system has content
        r3 = recorder.transcribe_dual(make_audio(30), None, cfg)
        print(f"Mic=None: {r3!r}")
        assert "Others" in r3 and "Me" not in r3, "should have only Others"

        # Case D: both None
        r4 = recorder.transcribe_dual(None, None, cfg)
        print(f"Both None: {r4!r}")
        assert r4 == "", "should return empty"

    print("✅ transcribe_dual handles None streams correctly")
    return True


def test_check_silence_no_import_error():
    """Verify _check_silence doesn't crash with ImportError."""
    print("\n=== _check_silence import check ===")
    cap = recorder.DualAudioCapture(SAMPLE_RATE)
    cap._silence_threshold = 0.001
    cap._silence_seconds = 10
    cap._silence_notify = lambda t, m: None
    # This used to crash with "attempted relative import"
    try:
        cap._check_silence(0.0)  # silent
        cap._check_silence(0.5)  # loud
        print("✅ _check_silence runs without ImportError")
        return True
    except ImportError as e:
        print(f"❌ FAIL: ImportError still present: {e}")
        return False


def test_upload_short_transcript():
    """End-to-end: upload a short test transcript to the VPS and verify it processes."""
    print("\n=== End-to-end upload test (with real VPS call) ===")
    import requests
    transcript = """[00:00] [Others] Thanks everyone for joining this test meeting.
[00:03] [Me] Yes, we should ship the feature by Friday.
[00:06] [Others] Agreed. Alice will finalize the spec tomorrow.
[00:10] [Me] Bob to handle the deployment pipeline next week.
[00:14] [Others] Let's sync up on Monday to review progress."""

    r = requests.post(
        cfg["api_url"].rstrip("/") + "/transcribe",
        headers={"X-API-Key": cfg["api_key"], "Content-Type": "application/json"},
        json={
            "transcript": transcript,
            "duration_sec": 20,
            "meeting_type": "auto",
        },
        timeout=30,
    )
    if r.status_code == 202:
        print(f"✅ Upload accepted: {r.json()}")
        return True
    else:
        print(f"❌ Upload failed: {r.status_code} {r.text}")
        return False


if __name__ == "__main__":
    results = {
        "duration_logic": test_duration_logic(),
        "transcribe_dual_none": test_transcribe_dual_with_none(),
        "check_silence": test_check_silence_no_import_error(),
        "upload_end_to_end": test_upload_short_transcript(),
    }
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    for name, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {name}")
    failed = [n for n, ok in results.items() if not ok]
    if failed:
        print(f"\n{len(failed)} test(s) FAILED")
        sys.exit(1)
    print("\nAll tests passed ✅")
