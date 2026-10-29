r"""Tests for diarize.py - the span and labelling logic.

Run:  python tests/recorder/test_diarize.py

The model itself is measured in test_diarize_real.py against archived meetings.
What is tested here is everything around it: which speaker owns a transcript
line, how tiny speakers are dropped, and that a failure degrades to [Others]
rather than losing the line.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import diarize

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


# ---------------------------------------------------------------
print("\nWhich speaker owns a line")


def t_line_inside_one_span():
    spans = [(0.0, 10.0, 0), (10.0, 20.0, 1)]
    assert diarize.speaker_of(spans, 2.0, 4.0) == 0
    assert diarize.speaker_of(spans, 12.0, 14.0) == 1


def t_line_straddling_two_spans_goes_to_the_bigger_overlap():
    # Whisper and the diarizer cut on different boundaries, so this is the
    # normal case, not an edge case.
    spans = [(0.0, 10.0, 0), (10.0, 20.0, 1)]
    assert diarize.speaker_of(spans, 8.0, 16.0) == 1, "6s of speaker 1 vs 2s of speaker 0"
    assert diarize.speaker_of(spans, 4.0, 12.0) == 0, "6s of speaker 0 vs 2s of speaker 1"


def t_line_in_silence_has_no_speaker():
    spans = [(0.0, 5.0, 0), (20.0, 25.0, 1)]
    assert diarize.speaker_of(spans, 10.0, 15.0) is None


def t_touching_but_not_overlapping_is_not_a_match():
    spans = [(0.0, 10.0, 0)]
    assert diarize.speaker_of(spans, 10.0, 12.0) is None


def t_no_spans_means_no_speaker():
    assert diarize.speaker_of([], 1.0, 2.0) is None


check("a line inside one span gets that speaker", t_line_inside_one_span)
check("a straddling line goes to the larger overlap", t_line_straddling_two_spans_goes_to_the_bigger_overlap)
check("a line in silence has no speaker", t_line_in_silence_has_no_speaker)
check("spans that merely touch do not match", t_touching_but_not_overlapping_is_not_a_match)
check("no spans means no speaker", t_no_spans_means_no_speaker)

# ---------------------------------------------------------------
print("\nDropping speakers who barely spoke")


def t_tiny_speakers_are_dropped():
    spans = [(0.0, 30.0, 0), (30.0, 60.0, 1), (60.0, 61.0, 2)]  # 2 spoke for 1s
    out = diarize.clean_spans(spans, min_talk_seconds=5.0)
    assert diarize.speaker_count(out) == 2, out
    assert all(spk != 2 for _, _, spk in out)


def t_dropped_lines_fall_back_not_reassigned():
    """A dropped span must leave a hole, not be handed to a neighbour."""
    spans = [(0.0, 30.0, 0), (60.0, 61.0, 2)]
    out = diarize.clean_spans(spans, min_talk_seconds=5.0)
    assert diarize.speaker_of(out, 60.2, 60.8) is None, "the hole was filled by a guess"


def t_talk_time_is_summed_across_spans():
    # Three 2s spans is 6s of talking - above the floor, even though no single
    # span is.
    spans = [(0.0, 2.0, 0), (10.0, 12.0, 0), (20.0, 22.0, 0), (30.0, 40.0, 1)]
    out = diarize.clean_spans(spans, min_talk_seconds=5.0)
    assert diarize.speaker_count(out) == 2, out


def t_speakers_are_renumbered_from_one():
    # Raw clustering produced "Speaker 10" in a two-person call.
    spans = [(0.0, 30.0, 7), (30.0, 60.0, 9)]
    out = diarize.clean_spans(spans, min_talk_seconds=5.0)
    assert sorted({spk for _, _, spk in out}) == [0, 1], out


def t_renumbering_follows_first_appearance():
    spans = [(0.0, 30.0, 5), (30.0, 60.0, 2)]
    out = diarize.clean_spans(spans, min_talk_seconds=5.0)
    assert out[0][2] == 0 and out[1][2] == 1


def t_everyone_below_the_floor_yields_nothing():
    spans = [(0.0, 1.0, 0), (5.0, 6.0, 1)]
    assert diarize.clean_spans(spans, min_talk_seconds=5.0) == []


def t_empty_in_empty_out():
    assert diarize.clean_spans([], 5.0) == []


check("speakers under the floor are dropped", t_tiny_speakers_are_dropped)
check("a dropped span leaves a hole, not a guess", t_dropped_lines_fall_back_not_reassigned)
check("talk time is summed across spans", t_talk_time_is_summed_across_spans)
check("speakers are renumbered from one", t_speakers_are_renumbered_from_one)
check("renumbering follows first appearance", t_renumbering_follows_first_appearance)
check("everyone below the floor yields nothing", t_everyone_below_the_floor_yields_nothing)
check("empty in, empty out", t_empty_in_empty_out)

# ---------------------------------------------------------------
print("\nLabels handed to the transcript")


def t_resolver_labels_from_one():
    r = diarize.make_resolver([(0.0, 10.0, 0), (10.0, 20.0, 1)])
    assert r(1.0, 2.0) == "Speaker 1"
    assert r(11.0, 12.0) == "Speaker 2"


def t_resolver_falls_back_outside_any_span():
    r = diarize.make_resolver([(0.0, 10.0, 0)])
    assert r(50.0, 51.0) == "Others", "an unlabelled line must stay [Others]"


def t_no_spans_means_no_resolver():
    # None tells transcribe() to keep its fixed label - no wrapper, no cost.
    assert diarize.make_resolver([]) is None


def t_resolver_fallback_is_configurable():
    r = diarize.make_resolver([(0.0, 10.0, 0)], fallback="Unknown")
    assert r(50.0, 51.0) == "Unknown"


check("labels start at Speaker 1", t_resolver_labels_from_one)
check("a line outside every span stays [Others]", t_resolver_falls_back_outside_any_span)
check("no spans means no resolver at all", t_no_spans_means_no_resolver)
check("the fallback label is configurable", t_resolver_fallback_is_configurable)

# ---------------------------------------------------------------
print("\nAvailability")


def t_available_is_honest():
    # Models were downloaded during the build; if they vanish this must say so
    # rather than raising at meeting time.
    assert diarize.available() is True, "models missing from models/diarization"


def t_missing_models_report_unavailable(  ):
    real = diarize.EMBEDDING
    try:
        diarize.EMBEDDING = Path("no_such_model.onnx")
        assert diarize.available() is False
    finally:
        diarize.EMBEDDING = real


check("available() is True with the models present", t_available_is_honest)
check("available() is False when a model is missing", t_missing_models_report_unavailable)

# ---------------------------------------------------------------
print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
