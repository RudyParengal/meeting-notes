"""Gaps in captured sound: counted, summed up once a minute, and measurable.

Windows marks a piece of captured sound when it does not follow on from the
piece before, usually because the program collected it too late and Windows had
already thrown some away. The soundcard library turns each mark into the warning
"data discontinuity in recording". The app printed one per gap: 11,072 of the
11,076 lines in notewell.log on 19 Nov.

- `blocksize()` gives Windows half a second of room to keep sound waiting for
  us. The library's own default is one device period (10 ms on this laptop), so
  any pause longer than that in Python, such as the speech model running, lost sound.
- `install()` counts the warnings instead of printing them, and writes at most
  one line a minute to the log: "sound card gaps in the last minute: 4 (meeting-mic 4)".
- `tone_check()` measures it for real: a quiet test tone is played and captured
  back, and every break in the tone is counted (`Notewell.exe --sound-check`).
"""
import collections
import logging
import threading
import time
import warnings

import numpy as np

log = logging.getLogger("sound")

BUFFER_SECONDS = 0.5        # how much sound Windows may keep waiting for us
SUMMARY_SECONDS = 60        # at most one summary line this often
MESSAGE = "data discontinuity in recording"

_counts = collections.Counter()      # since the last summary line
totals = collections.Counter()       # since the program started
_lock = threading.Lock()
_installed = False


def blocksize(sample_rate):
    """The `blocksize` for soundcard's recorder(): sets Windows' capture buffer."""
    return int(sample_rate * BUFFER_SECONDS)


def note_gap(where):
    with _lock:
        _counts[where or "sound"] += 1
        totals[where or "sound"] += 1


def flush():
    """Write one line for the gaps counted since the last one. Returns it (or None)."""
    with _lock:
        if not _counts:
            return None
        total = sum(_counts.values())
        parts = ", ".join(f"{k} {v}" for k, v in sorted(_counts.items()))
        _counts.clear()
    line = f"sound card gaps in the last minute: {total} ({parts})"
    log.warning(line)
    return line


def _every_minute(stop=None, seconds=SUMMARY_SECONDS):
    while not (stop.wait(seconds) if stop else time.sleep(seconds)):
        try:
            flush()
        except Exception:
            pass


def install():
    """Count the soundcard warning instead of printing it. Once per program."""
    global _installed
    if _installed:
        return
    _installed = True
    # "always": Python's own once-only rule kept resetting, which is how one
    # warning became 5,536 of them, and a count needs to see every one.
    warnings.filterwarnings("always", message=MESSAGE)
    shown = warnings.showwarning

    def show(message, category, filename, lineno, file=None, line=None):
        if MESSAGE in str(message):
            note_gap(threading.current_thread().name)
            return
        shown(message, category, filename, lineno, file, line)

    warnings.showwarning = show
    threading.Thread(target=_every_minute, daemon=True, name="sound-gaps").start()


# ------------------------------------------------------------ measuring it ---
TONE_HZ = 997               # not a whole number of cycles in 10 ms, so a lost piece always shows
TONE_LEVEL = 0.003          # about -50 dB: faint, and plenty for the check


def find_breaks(audio, sample_rate, freq=TONE_HZ, tolerance=0.05):
    """How many times a steady tone breaks: sound lost, or silence slipped in.

    A pure tone obeys x[n+1] + x[n-1] = 2 cos(w) x[n] at every sample. Where a
    piece is missing the rule fails. Failures closer than 20 ms are one break."""
    x = np.asarray(audio, dtype=np.float64).reshape(-1)
    if len(x) < 3:
        return 0
    level = np.sqrt(2) * np.sqrt(np.mean(x ** 2))
    if level <= 0:
        return 0
    w = 2 * np.pi * freq / sample_rate
    miss = np.abs(x[2:] + x[:-2] - 2 * np.cos(w) * x[1:-1]) > tolerance * level
    where = np.flatnonzero(miss)
    if not len(where):
        return 0
    return 1 + int(np.count_nonzero(np.diff(where) > sample_rate // 50))


def tone_check(seconds=120, sample_rate=16000, say=print):
    """Play a faint tone and record it back the way a meeting is recorded, with
    Windows' old 10 ms buffer and with the new half second, side by side, while
    another thread keeps Python busy (as the speech model does). Returns a dict."""
    import soundcard as sc
    install()
    speaker = sc.default_speaker()
    loop = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    stop = threading.Event()
    got = {}

    def play():
        n, sr = 0, 48000
        with speaker.player(samplerate=sr, channels=1, blocksize=sr // 10) as p:
            while not stop.is_set():
                t = np.arange(n, n + sr // 10) / sr
                p.play((TONE_LEVEL * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.float32))
                n += sr // 10

    def capture(name, size):
        threading.current_thread().name = name
        chunks, t0 = [], time.monotonic()
        with loop.recorder(samplerate=sample_rate, channels=1, blocksize=size) as rec:
            while time.monotonic() - t0 < seconds:
                chunks.append(rec.record(numframes=sample_rate).reshape(-1))
        got[name] = (np.concatenate(chunks), time.monotonic() - t0)

    def busy():
        while not stop.is_set():
            sum(i * i for i in range(20000))

    threads = [threading.Thread(target=f, args=a, daemon=True) for f, a in
               ((play, ()), (busy, ()), (capture, ("old-10ms", None)),
                (capture, ("new-500ms", blocksize(sample_rate))))]
    for t in threads:
        t.start()
    for t in threads[2:]:
        t.join(seconds + 30)
    stop.set()
    with _lock:
        gaps = dict(totals)
    result = {}
    for name, (audio, took) in sorted(got.items()):
        body = audio[sample_rate // 2: -sample_rate // 2]      # skip start-up and the last bit
        result[name] = {"seconds": round(took, 1), "captured_s": round(len(audio) / sample_rate, 1),
                        "level": round(float(np.sqrt(np.mean(body ** 2))), 5),
                        "breaks": find_breaks(body, sample_rate), "gap_marks": gaps.get(name, 0)}
        say(f"{name}: {result[name]}")
    return result
