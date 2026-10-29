"""Speaker separation for the far-end audio track.

Why only the far end
--------------------
The recorder already captures two tracks. The microphone is the user, by
construction, so half of "who said what" is solved before any model runs. Only
the system-audio track needs analysing, and only to split `[Others]` into
numbered speakers. That halves the work and removes the hardest case - telling
the local speaker apart from everyone else - entirely.

Why sherpa-onnx
---------------
It runs segmentation and speaker embedding as ONNX graphs. The recorder's venv
already had onnxruntime, so this added two packages and no PyTorch. pyannote.audio
would have pulled in torch - about 2.5 GB - onto the machine's most fragile
application.

Speakers are numbered, never named. Putting a name to a voice is a guess, and a
confident wrong name in a client meeting record is worse than a number.

Failure here must never cost the transcript. Every entry point returns
something usable or raises, and the caller in recorder.py treats any failure as
"emit [Others] exactly as before".
"""
import logging
import threading
from pathlib import Path

log = logging.getLogger("recorder")

# Tuned on real archived meetings, not taken from the library default of 0.5.
# Raw, at 0.5, a three-minute two-person clip came back as eight speakers.
#
# Measured on four archived meetings, three minutes each, with the talk-time
# floor below applied. Speaker counts:
#
#     threshold   0.5        0.8        0.9
#                 3 4 3 7    2 2 3 4    2 2 3 4
#
# 0.8 and 0.9 agree everywhere; 0.5 over-counts on half of them. The floor does
# most of the work, and 0.9 is picked as the more conservative of the two that
# agree. Diarization cost 0.052-0.077x realtime, so a 20-minute meeting is
# about a minute of CPU.
DEFAULT_THRESHOLD = 0.9
DEFAULT_MIN_TALK = 5.0

MODELS = Path(__file__).parent / "models" / "diarization"
SEGMENTATION = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING = MODELS / "campplus_en_voxceleb_16k.onnx"

_engine = None
_engine_lock = threading.Lock()


def available():
    """True if diarization can run at all. Cheap; safe to call on every meeting."""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return SEGMENTATION.is_file() and EMBEDDING.is_file()


def _build(num_speakers=-1, threshold=0.5, threads=2):
    import sherpa_onnx as so
    cfg = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(SEGMENTATION)),
            num_threads=threads,
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(
            model=str(EMBEDDING), num_threads=threads),
        # num_clusters=-1 means "decide from the audio". A meeting does not
        # announce how many people are on the call, so the threshold decides.
        clustering=so.FastClusteringConfig(
            num_clusters=int(num_speakers), threshold=float(threshold)),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError("sherpa-onnx rejected the diarization config")
    return so.OfflineSpeakerDiarization(cfg)


def get_engine(num_speakers=-1, threshold=0.5, threads=2):
    """Built once and reused. Loading the models costs seconds, not milliseconds."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = _build(num_speakers, threshold, threads)
        return _engine


def diarize(audio, sample_rate=16000, num_speakers=-1, threshold=DEFAULT_THRESHOLD,
            threads=2, min_talk_seconds=DEFAULT_MIN_TALK):
    """Return [(start_sec, end_sec, speaker_index)], sorted by start, cleaned.

    `audio` is float32 mono at `sample_rate`. Raises if the models are missing
    or the engine fails - the caller decides what to do about it.
    """
    import numpy as np

    engine = get_engine(num_speakers, threshold, threads)
    if sample_rate != engine.sample_rate:
        raise ValueError(
            f"diarization needs {engine.sample_rate} Hz, got {sample_rate}")
    a = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
    result = engine.process(a).sort_by_start_time()
    raw = [(float(s.start), float(s.end), int(s.speaker)) for s in result]
    return clean_spans(raw, min_talk_seconds)


def clean_spans(spans, min_talk_seconds=5.0):
    """Drop barely-there speakers, then renumber from 1 by first appearance.

    Raw clustering over-produces: on a real 3-minute clip it returned eight
    speakers, five of whom spoke for under five seconds in total. Those are one
    voice being split, not extra people in the room.

    Dropped spans are not reassigned to a neighbour - a guess about who spoke is
    exactly what this module refuses to make - so those lines fall back to the
    plain [Others] label. Renumbering matters too: unfiltered indices produced
    "Speaker 10" in a call with two people on the far end.
    """
    if not spans:
        return []
    talk = {}
    for start, end, spk in spans:
        talk[spk] = talk.get(spk, 0.0) + (end - start)
    keep = {spk for spk, secs in talk.items() if secs >= min_talk_seconds}
    kept = [(s, e, spk) for s, e, spk in spans if spk in keep]
    if not kept:
        return []
    order, renum = [], {}
    for _, _, spk in kept:
        if spk not in renum:
            renum[spk] = len(order)
            order.append(spk)
    return [(s, e, renum[spk]) for s, e, spk in kept]


def speaker_of(spans, start, end, minimum_overlap=0.0):
    """Which speaker owns a [start, end) window, by most overlap. None if nobody.

    Whisper's segments and the diarizer's spans are cut on different
    boundaries, so a transcript line rarely sits inside exactly one span.
    Picking the largest overlap is what makes them agree.
    """
    best, best_overlap = None, minimum_overlap
    for s_start, s_end, spk in spans:
        overlap = min(end, s_end) - max(start, s_start)
        if overlap > best_overlap:
            best, best_overlap = spk, overlap
    return best


def make_resolver(spans, fallback="Others"):
    """Build the callable transcribe() uses to label each line.

    Returns `fallback` when no span covers the line, so a partial diarization
    degrades line by line instead of failing the meeting.
    """
    if not spans:
        return None

    def resolve(start, end):
        spk = speaker_of(spans, start, end)
        return fallback if spk is None else f"Speaker {spk + 1}"

    return resolve


def speaker_count(spans):
    return len({spk for _, _, spk in spans})
