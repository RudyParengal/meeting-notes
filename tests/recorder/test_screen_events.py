"""Screen events watcher tests (windows-recorder/screen_events.py).

    python tests/recorder/test_screen_events.py

Uses made-up values only; never reads real windows on this laptop.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "windows-recorder"))
import screen_events as se  # noqa: E402

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


def tmp():
    return Path(tempfile.mkdtemp(prefix="nw-events-"))


# ------------------------------------------------------------------------ clip
print("\nClip")


def t_clip_controls_spaces_and_limit():
    raw = "  hello\x00\x07\x1f   world\x7f\t\n  again  "
    assert se.clip(raw, 300) == "hello world again", repr(se.clip(raw, 300))
    long_text = "a" * 500
    out = se.clip(long_text, se.TITLE_MAX)
    assert len(out) == 300 and out == "a" * 300, len(out)
    assert se.clip(None, 10) == ""


check("clip removes control characters, collapses spaces, and cuts at 300", t_clip_controls_spaces_and_limit)

# ---------------------------------------------------------------------- typing
print("\nTyping")


def t_typing_single_burst():
    tp = se.Typing()
    tp.tick(0, True, False)
    tp.tick(100, True, False)
    tp.tick(200, True, False)
    assert tp.due(1699) is None
    got = tp.due(1800)
    assert got == {"t": 0, "k": "typing", "ms": 200}, got
    assert tp.due(2000) is None


def t_typing_two_bursts():
    tp = se.Typing()
    tp.tick(100, True, False)
    tp.tick(300, True, False)
    b1 = tp.due(1900)
    assert b1 == {"t": 100, "k": "typing", "ms": 200}, b1
    tp.tick(2000, True, False)
    tp.tick(2450, True, False)
    b2 = tp.flush(2600)
    assert b2 == {"t": 2000, "k": "typing", "ms": 450}, b2


def t_typing_private_dropped():
    tp = se.Typing()
    tp.tick(0, True, False)
    tp.tick(100, True, False)
    tp.tick(200, False, True)
    assert tp.due(1800) is None
    tp2 = se.Typing()
    tp2.tick(0, True, False)
    tp2.tick(100, True, True)
    tp2.tick(200, True, False)
    assert tp2.flush(300) is None


def t_typing_disabled_constant():
    old = se.TYPING_EVENTS
    se.TYPING_EVENTS = False
    try:
        tp = se.Typing()
        tp.tick(0, True, False)
        tp.tick(200, True, False)
        assert tp.flush(300) is None
    finally:
        se.TYPING_EVENTS = old


check("typing burst at 0, 100, 200 ms finishes at 1800 ms with ms == 200", t_typing_single_burst)
check("typing, pause over 1.5 s, typing gives two bursts", t_typing_two_bursts)
check("a burst that touched a private window is dropped by due and flush", t_typing_private_dropped)
check("TYPING_EVENTS = False suppresses typing bursts", t_typing_disabled_constant)

# ---------------------------------------------------------------------- clicks
print("\nClicks")


def t_click_once_per_press():
    cl = se.Clicks()
    first = cl.tick(100, True, False, 50, 60, True, False, False)
    held = cl.tick(130, True, False, 50, 60, True, False, False)
    up = cl.tick(160, False, False, 50, 60, True, False, False)
    second = cl.tick(200, False, True, 70, 80, True, False, False)
    assert first == {"t": 100, "k": "click", "b": "left", "x": 50, "y": 60}, first
    assert held is None and up is None
    assert second == {"t": 200, "k": "click", "b": "right", "x": 70, "y": 80}, second


def t_click_filters():
    cl = se.Clicks()
    assert cl.tick(100, True, False, 10, 10, False, False, False) is None
    assert cl.tick(130, True, False, 10, 10, True, False, False) is None  # still held from outside
    cl.tick(160, False, False, 10, 10, True, False, False)
    assert cl.tick(200, True, False, 10, 10, True, True, False) is None
    cl.tick(230, False, False, 10, 10, True, False, False)
    assert cl.tick(300, True, False, 10, 10, True, False, True) is None


check("click is reported once per press, not while held", t_click_once_per_press)
check("clicks outside area, in private window, or on our window are dropped", t_click_filters)

# --------------------------------------------------------------------- windows
print("\nWindows")


def t_windows_title_stability():
    win = se.Windows()
    first = win.tick(0, 101, "chrome.exe", "Start", False, False)
    assert first == {"t": 0, "k": "window", "app": "chrome.exe", "title": "Start"}, first
    for i, ts in enumerate((100, 200, 300, 450, 600), start=1):
        assert win.tick(ts, 101, "chrome.exe", f"Tab {i}", False, False) is None
    assert win.tick(1500, 101, "chrome.exe", "Tab 5", False, False) is None
    settled = win.tick(1600, 101, "chrome.exe", "Tab 5", False, False)
    assert settled == {"t": 600, "k": "window", "app": "chrome.exe", "title": "Tab 5"}, settled
    assert win.tick(1850, 101, "chrome.exe", "Tab 5", False, False) is None


def t_windows_private_and_ours():
    win = se.Windows()
    assert win.tick(0, 101, "notepad.exe", "notes.txt", False, False) == {
        "t": 0, "k": "window", "app": "notepad.exe", "title": "notes.txt"}
    assert win.tick(100, 999, "notewell.exe", "Badge", False, True) is None
    assert win.tick(200, 101, "notepad.exe", "notes.txt", False, False) is None
    priv = win.tick(300, 202, "keepass.exe", "Secret Vault", True, False)
    assert priv == {"t": 300, "k": "private"}, priv
    assert set(priv.keys()) == {"t", "k"}
    assert win.tick(400, 202, "keepass.exe", "Other Entry", True, False) is None
    back = win.tick(500, 101, "notepad.exe", "notes.txt", False, False)
    assert back == {"t": 500, "k": "window", "app": "notepad.exe", "title": "notes.txt"}, back


def t_windows_no_duplicate_and_basename():
    win = se.Windows()
    a = win.tick(0, 101, r"C:\Example\notepad.exe", "Doc", False, False)
    assert a == {"t": 0, "k": "window", "app": "notepad.exe", "title": "Doc"}, a
    assert win.tick(100, 102, "notepad.exe", "Doc", False, False) is None


check("title changing 5 times in 600 ms then holding writes one line with last title", t_windows_title_stability)
check("private window writes bare private line once; our window is ignored", t_windows_private_and_ours)
check("never writes two identical window lines in a row and strips folder paths", t_windows_no_duplicate_and_basename)

# ---------------------------------------------------------------------- writer
print("\nWriter")


def t_writer_line_cap_and_end():
    p = tmp() / "a.events.jsonl"
    w = se.Writer(p, max_lines=3)
    assert w.line({"v": 1})
    assert w.line({"t": 10, "k": "private"})
    assert w.line({"t": 20, "k": "private"})
    assert not w.line({"t": 30, "k": "private"})
    assert w.cut is True
    w.end(40)
    w.end(50)  # calling twice is safe
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4, rows
    assert rows[-1] == {"t": 40, "k": "end", "lines": 3, "cut": True}, rows[-1]


def t_writer_byte_cap():
    p = tmp() / "b.events.jsonl"
    w = se.Writer(p, max_bytes=45)
    assert w.line({"v": 1, "os": "Windows 10"})
    assert not w.line({"t": 10, "k": "window", "app": "a.exe", "title": "too long for cap"})
    assert not w.line({"t": 20, "k": "private"})  # stays cut even for a short line
    assert w.cut is True
    w.end(30)
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[-1] == {"t": 30, "k": "end", "lines": 1, "cut": True}, rows


check("Writer stops at line cap, sets cut, and still writes end line", t_writer_line_cap_and_end)
check("Writer stops at byte cap, stays cut, and still writes end line", t_writer_byte_cap)

print(f"\n{_passed} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)
