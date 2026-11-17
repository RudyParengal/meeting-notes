r"""Tests for dictation's word list and history.

Run:  python tests/recorder/test_dictation_store.py
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
from dictation_store import Store, apply_words, prompt_hint, mishearings

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


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__}")


tmp = Path(tempfile.mkdtemp())


def new_store():
    return Store(tmp / f"d{len(list(tmp.iterdir()))}.db")


def t_replace_mishearing():
    words = [{"id": 1, "term": "Notewell", "mishearing": "note well, notable"}]
    text, used = apply_words("I opened note well and Notable today", words)
    assert text == "I opened Notewell and Notewell today", text
    assert used == [1]


def t_whole_words_only():
    words = [{"id": 1, "term": "Al", "mishearing": "el"}]
    text, used = apply_words("Also the element, el", words)
    assert text == "Also the element, Al", text


def t_longest_mishearing_first():
    assert mishearings("acme, acme corp , ,acme") == ["acme corp", "acme"]
    words = [{"id": 2, "term": "ACME Corp", "mishearing": "acme, acme corp"}]
    text, _ = apply_words("call acme corp now", words)
    assert text == "call ACME Corp now", text


def t_counts_word_said_right():
    words = [{"id": 3, "term": "Kubernetes", "mishearing": ""}, {"id": 4, "term": "Grafana", "mishearing": ""}]
    _, used = apply_words("Kubernetes sent it", words)
    assert used == [3]


def t_replacement_is_literal():
    words = [{"id": 5, "term": r"C:\temp \1", "mishearing": "see temp"}]
    text, _ = apply_words("open see temp", words)
    assert text == r"open C:\temp \1", text


def t_prompt_hint_limit():
    words = [{"term": "x" * 300}, {"term": "y" * 290}, {"term": "z" * 50}]
    hint = prompt_hint(words)
    assert len(hint) <= 600 and hint.count(",") == 1


def t_word_list_crud():
    s = new_store()
    w = s.add_word("  Notewell ", "note well")
    assert w["term"] == "Notewell" and w["use_count"] == 0
    assert "already" in str(raises(ValueError, s.add_word, "notewell"))
    assert "Type the word" in str(raises(ValueError, s.add_word, "   "))
    raises(ValueError, s.add_word, "x" * 101)
    w2 = s.update_word(w["id"], mishearing="note well, no tell")
    assert w2["mishearing"] == "note well, no tell" and w2["term"] == "Notewell"
    raises(KeyError, s.update_word, 999, term="x")
    s.count_uses([w["id"], w["id"]])
    assert s.words()[0]["use_count"] == 2
    assert s.delete_word(w["id"]) and not s.delete_word(w["id"])
    assert s.words() == []


def t_history():
    s = new_store()
    assert s.last_text() == ""
    s.add_dictation("first one", "first one", app="notepad.exe", seconds=2.0, delay_ms=900)
    s.add_dictation("", "", outcome="failed")
    s.add_dictation("second one here", "second one hear", app="chrome.exe", seconds=3.5, delay_ms=1100,
                    outcome="held")
    h = s.history()
    assert [r["text"] for r in h] == ["second one here", "", "first one"], h
    assert h[0]["outcome"] == "held" and h[0]["raw_text"] == "second one hear"
    assert s.last_text() == "second one here"
    t = s.totals()
    assert t == {"dictations": 3, "seconds": 5.5, "words": 5}, t
    assert len(s.history(limit=1)) == 1
    assert s.clear_history() == 3 and s.history() == [] and s.last_text() == ""


def t_survives_reopen():
    path = tmp / "reopen.db"
    s = Store(path)
    s.add_word("Kharkiv", "harkiv")
    s.add_dictation("hello", "hello")
    s.close()
    s = Store(path)
    assert s.words()[0]["term"] == "Kharkiv" and s.last_text() == "hello"


print("dictation store")
check("a mishearing is replaced by the right word", t_replace_mishearing)
check("only whole words are replaced", t_whole_words_only)
check("the longer mishearing is replaced first", t_longest_mishearing_first)
check("a word said right still counts as used", t_counts_word_said_right)
check("odd characters in a word are typed as they are", t_replacement_is_literal)
check("the spelling hint stays short", t_prompt_hint_limit)
check("words can be added, changed, counted and removed", t_word_list_crud)
check("history keeps every dictation, newest first", t_history)
check("word list and history survive a restart", t_survives_reopen)

total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"   - {n}: {e}")
    sys.exit(1)
print(f"{total}/{total} passed")
