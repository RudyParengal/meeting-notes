r"""Imported audio files are read by faster-whisper through PyAV. PyAV 19 dropped an
argument faster-whisper 1.2.1 passes, so every import failed with a TypeError
while live recording (which hands over samples, not a file) kept working.

    python tests/recorder/test_audio_file.py

No model download, no audio device, no network.
"""
import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

from faster_whisper.audio import decode_audio

_passed, _failed = 0, []


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def one_second_wav(rate):
    path = Path(tempfile.mkdtemp(prefix="nw-audio-")) / "tone.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(rate)))
    return path


print("\nReading an imported audio file")


def t_wav_is_decoded_at_16k():
    audio = decode_audio(str(one_second_wav(16000)), sampling_rate=16000)
    assert abs(len(audio) - 16000) < 400, len(audio)


def t_other_rates_are_resampled():
    audio = decode_audio(str(one_second_wav(44100)), sampling_rate=16000)
    assert abs(len(audio) - 16000) < 400, len(audio)


check("a 16 kHz wav file is read", t_wav_is_decoded_at_16k)
check("a 44.1 kHz wav file is read and resampled", t_other_rates_are_resampled)

print(f"\n{_passed} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)
