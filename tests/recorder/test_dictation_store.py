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


# ---- syncing with an account (Phase 2): two stores stand in for laptop and phone,
# a small copy of the server's rules (backend/dictation_sync.py) stands in for the server.
class FakeServer:
    def __init__(self):
        self.words, self.rows, self.cleared, self.seq = {}, [], 0.0, 0

    def sync_words(self, incoming):
        for w in incoming:
            old = self.words.get(w["uid"])
            if old is None:
                self.words[w["uid"]] = dict(w)
                continue
            if w["updated_at"] > old["updated_at"]:
                old.update(term=w["term"], mishearing=w["mishearing"], updated_at=w["updated_at"],
                           deleted=w["deleted"])
            old["use_count"] = max(old["use_count"], w["use_count"])
        return [dict(w) for w in self.words.values()]

    def sync_history(self, incoming, since, cleared_at):
        if cleared_at > self.cleared:
            self.cleared = cleared_at
            self.rows = [r for r in self.rows if r["created_at"] > cleared_at]
        have = {r["uid"] for r in self.rows}
        for d in incoming:
            if d["created_at"] > self.cleared and d["uid"] not in have:
                self.seq += 1
                self.rows.append({**d, "seq": self.seq})
        items = [r for r in self.rows if r["seq"] > since]
        return {"items": items, "cursor": items[-1]["seq"] if items else since, "cleared_at": self.cleared}


def sync(store, server):
    store.merge_words(server.sync_words(store.words_for_sync()))
    st = store.sync_state()
    sent = store.unsent_dictations()
    r = server.sync_history(sent, st["since"], st["cleared_at"])
    store.mark_sent([d["uid"] for d in sent])
    store.merge_dictations(r["items"], r["cursor"], r["cleared_at"])


def terms(store):
    return sorted(w["term"] for w in store.words())


def t_old_file_gets_ids():
    import sqlite3
    path = tmp / "old-version.db"
    db = sqlite3.connect(str(path))
    db.executescript("""CREATE TABLE vocabulary_terms (id INTEGER PRIMARY KEY, owner TEXT NOT NULL DEFAULT 'local',
        term TEXT NOT NULL, mishearing TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'user',
        use_count INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE dictations (id INTEGER PRIMARY KEY, owner TEXT NOT NULL DEFAULT 'local', created_at REAL NOT NULL,
        text TEXT NOT NULL, raw_text TEXT NOT NULL, app TEXT NOT NULL DEFAULT '', seconds REAL NOT NULL DEFAULT 0,
        delay_ms INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'pasted');
        INSERT INTO vocabulary_terms (term, created_at, updated_at) VALUES ('Kharkiv', 1, 1), ('Notewell', 1, 1);
        INSERT INTO dictations (created_at, text, raw_text) VALUES (5, 'from before', 'from before');""")
    db.commit()
    db.close()
    s = Store(path)
    words = s.words_for_sync()
    assert terms(s) == ["Kharkiv", "Notewell"], terms(s)
    assert len({w["uid"] for w in words}) == 2 and all(len(w["uid"]) >= 8 for w in words), words
    assert [d["text"] for d in s.unsent_dictations()] == ["from before"]


def t_words_reach_other_device():
    server, laptop, phone = FakeServer(), new_store(), new_store()
    laptop.add_word("Parengal", "para angle")
    sync(laptop, server)
    sync(phone, server)
    assert terms(phone) == ["Parengal"] and phone.words()[0]["mishearing"] == "para angle", phone.words()


def t_removal_reaches_other_device():
    server, laptop, phone = FakeServer(), new_store(), new_store()
    w = laptop.add_word("Notewell")
    sync(laptop, server)
    sync(phone, server)
    laptop.delete_word(w["id"])
    sync(laptop, server)
    sync(phone, server)
    assert terms(phone) == [] and terms(laptop) == [], (terms(phone), terms(laptop))


def t_newest_edit_wins():
    server, laptop, phone = FakeServer(), new_store(), new_store()
    w = laptop.add_word("Parengal")
    sync(laptop, server)
    sync(phone, server)
    phone.update_word(phone.words()[0]["id"], term="Parengal Ltd")
    sync(phone, server)
    sync(laptop, server)
    assert terms(laptop) == ["Parengal Ltd"], terms(laptop)
    laptop.count_uses([w["id"]] * 4)
    sync(laptop, server)
    sync(phone, server)
    assert phone.words()[0]["use_count"] == 4, phone.words()


def t_same_word_added_twice_settles():
    server, laptop, phone = FakeServer(), new_store(), new_store()
    laptop.add_word("Kharkiv")
    phone.add_word("kharkiv", "harkiv")
    for _ in range(2):
        sync(laptop, server)
        sync(phone, server)
    assert len(laptop.words()) == 1 and len(phone.words()) == 1, (laptop.words(), phone.words())
    live = lambda s: {w["uid"] for w in s.words_for_sync() if not w["deleted"]}
    assert live(laptop) == live(phone), (live(laptop), live(phone))


def t_readding_a_removed_word():
    s = new_store()
    w = s.add_word("Grafana")
    s.delete_word(w["id"])
    s.add_word("Grafana", "graph ana")
    assert terms(s) == ["Grafana"] and len(s.words_for_sync()) == 1, s.words_for_sync()


def t_history_reaches_other_device_once():
    server, laptop, phone = FakeServer(), new_store(), new_store()
    laptop.add_dictation("hello from the laptop", "hello from the laptop", app="notepad.exe", seconds=2.0)
    for _ in range(2):
        sync(laptop, server)
        sync(phone, server)
    assert [r["text"] for r in phone.history()] == ["hello from the laptop"], phone.history()
    assert len(laptop.history()) == 1 and len(server.rows) == 1, (laptop.history(), server.rows)
    assert laptop.unsent_dictations() == []


def t_clear_reaches_other_device():
    server, laptop, phone = FakeServer(), new_store(), new_store()
    laptop.add_dictation("old one", "old one")
    sync(laptop, server)
    sync(phone, server)
    phone.clear_history()
    sync(phone, server)
    sync(laptop, server)
    assert laptop.history() == [] and server.rows == [], (laptop.history(), server.rows)
    laptop.add_dictation("new one", "new one")
    sync(laptop, server)
    sync(phone, server)
    assert [r["text"] for r in phone.history()] == ["new one"], phone.history()


def t_long_dictation_clipped_for_server():
    s = new_store()
    s.add_dictation("x" * 25000, "y" * 25000, app="a" * 300)
    d = s.unsent_dictations()[0]
    assert len(d["text"]) == 20000 and len(d["raw_text"]) == 20000 and len(d["app"]) == 200
    assert len(s.history()[0]["text"]) == 25000     # the copy on this computer is kept whole


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
check("sync: a file from the old version gets ids, nothing lost", t_old_file_gets_ids)
check("sync: a word added on the laptop reaches the phone", t_words_reach_other_device)
check("sync: a word removed on the laptop goes from the phone", t_removal_reaches_other_device)
check("sync: the newest edit wins and use counts travel", t_newest_edit_wins)
check("sync: the same word added on both settles to one", t_same_word_added_twice_settles)
check("sync: re-adding a removed word brings it back, no duplicate", t_readding_a_removed_word)
check("sync: a dictation reaches the phone once, not twice", t_history_reaches_other_device_once)
check("sync: clearing history on the phone clears the laptop", t_clear_reaches_other_device)
check("sync: a very long dictation is cut to the server's limit", t_long_dictation_clipped_for_server)

total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"   - {n}: {e}")
    sys.exit(1)
print(f"{total}/{total} passed")
