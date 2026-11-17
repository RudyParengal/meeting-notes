"""Dictation's word list and history: one small SQLite file in Notewell's data folder.

Every row carries an owner ("local" for now), so a later version can move the
word list and history to an account and share them with a phone.

Plain Python, no Windows calls: tested on its own (tests/recorder/test_dictation_store.py).
"""
import re
import sqlite3
import threading
import time
from pathlib import Path

OWNER = "local"
MAX_TERM = 100
MAX_MISHEARING = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vocabulary_terms (
    id          INTEGER PRIMARY KEY,
    owner       TEXT    NOT NULL DEFAULT 'local',
    term        TEXT    NOT NULL,              -- the right word
    mishearing  TEXT    NOT NULL DEFAULT '',   -- what the model writes instead; commas between several
    source      TEXT    NOT NULL DEFAULT 'user',
    use_count   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS dictations (
    id          INTEGER PRIMARY KEY,
    owner       TEXT    NOT NULL DEFAULT 'local',
    created_at  REAL    NOT NULL,
    text        TEXT    NOT NULL,              -- what was typed (after the word list and tidy-up)
    raw_text    TEXT    NOT NULL,              -- what the speech model heard
    app         TEXT    NOT NULL DEFAULT '',   -- the program it went into, e.g. notepad.exe
    seconds     REAL    NOT NULL DEFAULT 0,    -- length of the speech
    delay_ms    INTEGER NOT NULL DEFAULT 0,    -- key released -> text pasted
    outcome     TEXT    NOT NULL DEFAULT 'pasted'  -- pasted | held (password box) | failed
);
CREATE INDEX IF NOT EXISTS dictations_by_time ON dictations (owner, created_at);
"""


def _clean(value, limit, name):
    value = " ".join(str(value or "").split())
    if len(value) > limit:
        raise ValueError(f"{name} is too long (at most {limit} characters)")
    return value


def mishearings(entry: str) -> list:
    """'acme corp, ackme' -> ['acme corp', 'ackme'], longest first so the longer
    phrase is replaced before a shorter one inside it."""
    parts = {p.strip() for p in (entry or "").split(",") if p.strip()}
    return sorted(parts, key=len, reverse=True)


def _word_pattern(phrase: str):
    # Whole words only: "Al" must not turn "Also" into something else.
    return re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)


def apply_words(text: str, words: list):
    """Put the right words in. Returns (new text, ids of the words that appear in it).

    A word with no 'misheard as' still counts when the model got it right: the
    count says how often the word is used, which is what decides if it is worth keeping.
    """
    # One pass over the text, so a word just put in is never itself replaced again.
    fix = {}
    for w in words:
        for wrong in mishearings(w.get("mishearing", "")):
            fix.setdefault(wrong.lower(), w["term"])
    if fix:
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(fix, key=len, reverse=True))
                             + r")(?!\w)", re.IGNORECASE)
        text = pattern.sub(lambda m: fix[m.group(0).lower()], text)
    used = [w["id"] for w in words if w["term"] and _word_pattern(w["term"]).search(text)]
    return text, used


def prompt_hint(words: list, limit: int = 600) -> str:
    """The words, as a hint for the speech model to spell them this way."""
    hint = ""
    for w in words:
        nxt = (hint + ", " if hint else "") + w["term"]
        if len(nxt) > limit:
            break
        hint = nxt
    return hint


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()

    # ---- word list ----
    def words(self) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, term, mishearing, source, use_count, created_at, updated_at "
                "FROM vocabulary_terms WHERE owner=? ORDER BY lower(term)", (OWNER,)).fetchall()
        return [dict(r) for r in rows]

    def add_word(self, term: str, mishearing: str = "") -> dict:
        term = _clean(term, MAX_TERM, "The word")
        mishearing = _clean(mishearing, MAX_MISHEARING, "'Misheard as'")
        if not term:
            raise ValueError("Type the word first")
        now = time.time()
        with self._lock, self._db:
            if self._db.execute("SELECT 1 FROM vocabulary_terms WHERE owner=? AND lower(term)=lower(?)",
                                (OWNER, term)).fetchone():
                raise ValueError(f"'{term}' is already in the list")
            cur = self._db.execute(
                "INSERT INTO vocabulary_terms (owner, term, mishearing, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)", (OWNER, term, mishearing, now, now))
        return self._word(cur.lastrowid)

    def update_word(self, word_id: int, term: str = None, mishearing: str = None) -> dict:
        old = self._word(word_id)
        if old is None:
            raise KeyError(word_id)
        term = old["term"] if term is None else _clean(term, MAX_TERM, "The word")
        mishearing = old["mishearing"] if mishearing is None else _clean(mishearing, MAX_MISHEARING, "'Misheard as'")
        if not term:
            raise ValueError("The word cannot be empty")
        with self._lock, self._db:
            self._db.execute("UPDATE vocabulary_terms SET term=?, mishearing=?, updated_at=? WHERE id=? AND owner=?",
                             (term, mishearing, time.time(), word_id, OWNER))
        return self._word(word_id)

    def delete_word(self, word_id: int) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM vocabulary_terms WHERE id=? AND owner=?",
                                    (word_id, OWNER)).rowcount > 0

    def count_uses(self, ids: list):
        if ids:
            with self._lock, self._db:
                self._db.executemany("UPDATE vocabulary_terms SET use_count=use_count+1 WHERE id=? AND owner=?",
                                     [(i, OWNER) for i in ids])

    def _word(self, word_id):
        with self._lock:
            r = self._db.execute("SELECT id, term, mishearing, source, use_count, created_at, updated_at "
                                 "FROM vocabulary_terms WHERE id=? AND owner=?", (word_id, OWNER)).fetchone()
        return dict(r) if r else None

    # ---- history ----
    def add_dictation(self, text, raw_text, app="", seconds=0.0, delay_ms=0, outcome="pasted") -> int:
        with self._lock, self._db:
            return self._db.execute(
                "INSERT INTO dictations (owner, created_at, text, raw_text, app, seconds, delay_ms, outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (OWNER, time.time(), text, raw_text, app, round(seconds, 2), int(delay_ms), outcome)).lastrowid

    def history(self, limit: int = 100, before: float = None) -> list:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, text, raw_text, app, seconds, delay_ms, outcome FROM dictations "
                "WHERE owner=? AND created_at < ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (OWNER, before if before is not None else 1e12, limit)).fetchall()
        return [dict(r) for r in rows]

    def last_text(self) -> str:
        """The latest dictation that has words in it, for 'paste the last one again'."""
        with self._lock:
            r = self._db.execute("SELECT text FROM dictations WHERE owner=? AND text != '' "
                                 "ORDER BY created_at DESC, id DESC LIMIT 1", (OWNER,)).fetchone()
        return r["text"] if r else ""

    def totals(self) -> dict:
        with self._lock:
            r = self._db.execute("SELECT count(*) AS n, coalesce(sum(seconds), 0) AS s FROM dictations "
                                 "WHERE owner=?", (OWNER,)).fetchone()
            texts = self._db.execute("SELECT text FROM dictations WHERE owner=?", (OWNER,)).fetchall()
        return {"dictations": r["n"], "seconds": round(r["s"], 1),
                "words": sum(len(t["text"].split()) for t in texts)}

    def clear_history(self) -> int:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM dictations WHERE owner=?", (OWNER,)).rowcount
