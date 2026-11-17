"""Dictation's word list and history: one small SQLite file in Notewell's data folder.

Every row carries an owner ("local"). When Notewell is linked to a Meeting
Notes server (desktop/linked_server.py), the word list and history are synced to
the signed-in account there, so a phone sees the same ones. For that each word
and each dictation has an id made here ("uid"); a removed word stays as a
"removed" marker so the removal reaches the other devices too.

Plain Python, no Windows calls: tested on its own (tests/recorder/test_dictation_store.py).
"""
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

OWNER = "local"
MAX_TERM = 100
MAX_MISHEARING = 300
SYNC_TEXT = 20000      # the server's limit for one dictation's text

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
CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value REAL NOT NULL);
"""
# Added for syncing (Phase 2). Older files get them on first open; nothing is lost.
_ADDED = {"vocabulary_terms": {"uid": "TEXT", "deleted": "INTEGER NOT NULL DEFAULT 0"},
          "dictations": {"uid": "TEXT", "synced": "INTEGER NOT NULL DEFAULT 0"}}
_WORD_COLS = "id, term, mishearing, source, use_count, created_at, updated_at"


def _uid() -> str:
    return uuid.uuid4().hex


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
            for table, cols in _ADDED.items():
                have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                for col, kind in cols.items():
                    if col not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
                for (rowid,) in self._db.execute(f"SELECT id FROM {table} WHERE uid IS NULL").fetchall():
                    self._db.execute(f"UPDATE {table} SET uid=? WHERE id=?", (_uid(), rowid))
                self._db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_uid ON {table} (uid)")

    def close(self):
        with self._lock:
            self._db.close()

    # ---- word list ----
    def words(self) -> list:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_WORD_COLS} FROM vocabulary_terms WHERE owner=? AND deleted=0 "
                "ORDER BY lower(term)", (OWNER,)).fetchall()
        return [dict(r) for r in rows]

    def add_word(self, term: str, mishearing: str = "") -> dict:
        term = _clean(term, MAX_TERM, "The word")
        mishearing = _clean(mishearing, MAX_MISHEARING, "'Misheard as'")
        if not term:
            raise ValueError("Type the word first")
        now = time.time()
        with self._lock, self._db:
            old = self._db.execute("SELECT id, deleted FROM vocabulary_terms WHERE owner=? AND lower(term)=lower(?) "
                                   "ORDER BY deleted LIMIT 1", (OWNER, term)).fetchone()
            if old and not old["deleted"]:
                raise ValueError(f"'{term}' is already in the list")
            if old:                                # removed earlier: bring it back, same id everywhere
                self._db.execute("UPDATE vocabulary_terms SET term=?, mishearing=?, deleted=0, updated_at=? "
                                 "WHERE id=?", (term, mishearing, now, old["id"]))
                return self._word_locked(old["id"])
            cur = self._db.execute(
                "INSERT INTO vocabulary_terms (owner, uid, term, mishearing, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)", (OWNER, _uid(), term, mishearing, now, now))
            return self._word_locked(cur.lastrowid)

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
        """Kept as a 'removed' marker, so a sync removes it on the other devices too."""
        with self._lock, self._db:
            return self._db.execute("UPDATE vocabulary_terms SET deleted=1, updated_at=? "
                                    "WHERE id=? AND owner=? AND deleted=0",
                                    (time.time(), word_id, OWNER)).rowcount > 0

    def count_uses(self, ids: list):
        if ids:
            with self._lock, self._db:
                self._db.executemany("UPDATE vocabulary_terms SET use_count=use_count+1 WHERE id=? AND owner=?",
                                     [(i, OWNER) for i in ids])

    def _word(self, word_id):
        with self._lock:
            return self._word_locked(word_id)

    def _word_locked(self, word_id):
        r = self._db.execute(f"SELECT {_WORD_COLS} FROM vocabulary_terms WHERE id=? AND owner=? AND deleted=0",
                             (word_id, OWNER)).fetchone()
        return dict(r) if r else None

    # ---- history ----
    def add_dictation(self, text, raw_text, app="", seconds=0.0, delay_ms=0, outcome="pasted") -> int:
        with self._lock, self._db:
            return self._db.execute(
                "INSERT INTO dictations (owner, uid, created_at, text, raw_text, app, seconds, delay_ms, outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (OWNER, _uid(), time.time(), text, raw_text, app, round(seconds, 2), int(delay_ms),
                 outcome)).lastrowid

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
        """Delete it all here; the date is kept so a sync clears the other devices too."""
        with self._lock, self._db:
            self._set_meta("history_cleared_at", time.time())
            return self._db.execute("DELETE FROM dictations WHERE owner=?", (OWNER,)).rowcount

    # ---- sync with an account on a server (desktop/linked_server.py) ----
    def _get_meta(self, key) -> float:
        r = self._db.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else 0

    def _set_meta(self, key, value):
        self._db.execute("INSERT INTO sync_meta (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def sync_state(self) -> dict:
        with self._lock:
            return {"since": int(self._get_meta("history_cursor")),
                    "cleared_at": self._get_meta("history_cleared_at")}

    def words_for_sync(self) -> list:
        """Every word, removed ones included, in the server's shape."""
        with self._lock:
            rows = self._db.execute("SELECT uid, term, mishearing, use_count, updated_at, deleted "
                                    "FROM vocabulary_terms WHERE owner=?", (OWNER,)).fetchall()
        return [{**dict(r), "deleted": bool(r["deleted"])} for r in rows]

    def merge_words(self, remote: list) -> int:
        """Take the account's list. Newest edit wins; the use count is the highest seen.
        The same word added on two devices before either synced: both keep the one
        with the smaller id, so they agree without talking to each other."""
        changed = 0
        with self._lock, self._db:
            for w in remote:
                mine = self._db.execute("SELECT id, updated_at, use_count FROM vocabulary_terms WHERE uid=?",
                                        (w["uid"],)).fetchone()
                if mine is None:
                    twin = None if w["deleted"] else self._db.execute(
                        "SELECT id, uid FROM vocabulary_terms WHERE owner=? AND deleted=0 AND lower(term)=lower(?)",
                        (OWNER, w["term"])).fetchone()
                    if twin and twin["uid"] < w["uid"]:
                        # ours wins: keep theirs only as a removed marker, dated now so the removal spreads
                        w = {**w, "deleted": True, "updated_at": time.time()}
                    elif twin:
                        self._db.execute("UPDATE vocabulary_terms SET deleted=1, updated_at=? WHERE id=?",
                                         (time.time(), twin["id"]))
                    self._db.execute(
                        "INSERT INTO vocabulary_terms (owner, uid, term, mishearing, source, use_count, created_at, "
                        "updated_at, deleted) VALUES (?, ?, ?, ?, 'synced', ?, ?, ?, ?)",
                        (OWNER, w["uid"], w["term"], w["mishearing"], w["use_count"], w["updated_at"],
                         w["updated_at"], int(w["deleted"])))
                    changed += 1
                    continue
                if w["updated_at"] > mine["updated_at"]:
                    self._db.execute("UPDATE vocabulary_terms SET term=?, mishearing=?, updated_at=?, deleted=? "
                                     "WHERE id=?", (w["term"], w["mishearing"], w["updated_at"],
                                                    int(w["deleted"]), mine["id"]))
                    changed += 1
                if w["use_count"] > mine["use_count"]:
                    self._db.execute("UPDATE vocabulary_terms SET use_count=? WHERE id=?",
                                     (w["use_count"], mine["id"]))
        return changed

    def unsent_dictations(self, limit: int = 500) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT uid, created_at, text, raw_text, app, seconds, delay_ms, outcome FROM dictations "
                "WHERE owner=? AND synced=0 ORDER BY created_at LIMIT ?", (OWNER, limit)).fetchall()
        return [{**dict(r), "text": r["text"][:SYNC_TEXT], "raw_text": r["raw_text"][:SYNC_TEXT],
                 "app": r["app"][:200], "outcome": r["outcome"][:20]} for r in rows]

    def mark_sent(self, uids: list):
        with self._lock, self._db:
            self._db.executemany("UPDATE dictations SET synced=1 WHERE uid=?", [(u,) for u in uids])

    def merge_dictations(self, items: list, cursor: int, cleared_at: float) -> int:
        """Rows from the account (other devices' too), and how far through them we now are."""
        added = 0
        with self._lock, self._db:
            if cleared_at > self._get_meta("history_cleared_at"):
                self._set_meta("history_cleared_at", cleared_at)
                self._db.execute("DELETE FROM dictations WHERE owner=? AND created_at <= ?", (OWNER, cleared_at))
            cleared = self._get_meta("history_cleared_at")
            for d in items:
                if d["created_at"] > cleared:
                    added += self._db.execute(
                        "INSERT OR IGNORE INTO dictations (owner, uid, created_at, text, raw_text, app, seconds, "
                        "delay_ms, outcome, synced) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        (OWNER, d["uid"], d["created_at"], d["text"], d["raw_text"], d["app"], d["seconds"],
                         d["delay_ms"], d["outcome"])).rowcount
            self._set_meta("history_cursor", cursor)
        return added
