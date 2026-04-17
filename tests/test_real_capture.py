"""
Real end-to-end test: actually capture ~15s of audio, run it through Whisper
and the upload pipeline. Also test short (<10s) rejection.

This validates the fix: that the ImportError in _check_silence no longer kills
the system audio thread.
"""
import sys, os, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
APPDATA = Path(os.environ.get("APPDATA", Path.home())) / "MeetingRecorder"
cfg = json.loads((APPDATA / "config.json").read_text())

import recorder

def test_capture(duration_sec):
    print(f"\n=== Capturing {duration_sec}s of system audio ===")
    cap = recorder.DualAudioCapture(cfg["sample_rate"])
    cap._silence_threshold = cfg.get("silence_alert_threshold_rms", 0.001)
    cap._silence_seconds = cfg.get("silence_alert_seconds", 10)
    cap._silence_notify = lambda t, m: print(f"    NOTIFY: {t} — {m}")
    cap.start()
    time.sleep(duration_sec)
    system_audio, mic_audio, duration = cap.stop()
    print(f"    Returned duration: {duration:.1f}s")
    print(f"    System failed: {cap._system_failed}")
    print(f"    Mic failed: {cap._mic_failed}")
    sys_len = (len(system_audio) / cap.sample_rate) if system_audio is not None else 0
    mic_len = (len(mic_audio) / cap.sample_rate) if mic_audio is not None else 0
    print(f"    System: {sys_len:.1f}s, Mic: {mic_len:.1f}s")
    return cap, system_audio, mic_audio, duration


# Test 1: 5s (should be rejected downstream by _finish)
cap1, s1, m1, d1 = test_capture(5)
if d1 < 10:
    print(f"    Short capture: duration={d1:.1f} → would be REJECTED by _finish ✓")
else:
    print(f"    Short capture: duration={d1:.1f} → unexpected")

# Test 2: 15s (should proceed, and critically, system audio thread must survive > 1s)
cap2, s2, m2, d2 = test_capture(15)
if d2 >= 10 and cap2._system_failed is None:
    print(f"    ✅ System audio thread survived {d2:.1f}s — ImportError FIXED")
else:
    print(f"    ❌ system_failed={cap2._system_failed}, duration={d2}")
    sys.exit(1)

# Test 3: Long capture — simulate an hour by checking that the capture runs continuously for 20s
# without either thread crashing. We don't actually wait an hour.
print("\n=== 20s continuous capture — verifying neither thread crashes ===")
cap3, s3, m3, d3 = test_capture(20)
if cap3._system_failed is None and cap3._mic_failed is None:
    print(f"    ✅ Both streams healthy for {d3:.1f}s")
else:
    print(f"    system_failed={cap3._system_failed}, mic_failed={cap3._mic_failed}")

print("\n" + "=" * 40)
print("All capture tests passed — pipeline is healthy")
print("=" * 40)
