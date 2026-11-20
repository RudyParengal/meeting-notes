r"""Tests for logfiles.py: logs roll over at 5 MB and keep the 3 newest old copies.

Run:  python tests/recorder/test_logfiles.py

notewell.log, recorder.log and screen.log had no limit. These use a small
limit so the rollover happens in a second, then check the real settings.
"""
import logging, os, re, shutil, sys, tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "windows-recorder"))
import logfiles

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


def folder():
    return Path(tempfile.mkdtemp(prefix="nw-logs-"))


def names(d):
    return sorted(p.name for p in d.iterdir())


def t_real_limits():
    assert logfiles.MAX_BYTES == 5 * 1024 * 1024
    assert logfiles.KEEP == 3


def t_handler_rolls_and_keeps_three():
    d = folder()
    h = logfiles.handler(d / "recorder.log", max_bytes=2000)
    log = logging.getLogger("t-roll")
    log.propagate = False
    log.addHandler(h)
    for i in range(500):
        log.warning("line %04d %s", i, "x" * 40)
    h.close()
    log.removeHandler(h)
    assert names(d) == ["recorder.log", "recorder.log.1", "recorder.log.2", "recorder.log.3"], names(d)
    for p in d.iterdir():
        assert p.stat().st_size <= 2100, (p.name, p.stat().st_size)
    assert "line 0499" in (d / "recorder.log").read_text(encoding="utf-8")
    shutil.rmtree(d)


def t_stream_rolls_and_keeps_three():
    d = folder()
    s = logfiles.Stream(d / "notewell.log", max_bytes=2000)
    for i in range(500):
        print(f"line {i:04d} " + "y" * 40, file=s)
    s.close()
    assert names(d) == ["notewell.log", "notewell.log.1", "notewell.log.2", "notewell.log.3"], names(d)
    for p in d.iterdir():
        assert p.stat().st_size <= 2100, (p.name, p.stat().st_size)
    newest = (d / "notewell.log").read_text(encoding="utf-8")
    older = (d / "notewell.log.1").read_text(encoding="utf-8")
    assert "line 0499" in newest and "line 0499" not in older
    shutil.rmtree(d)


def t_stream_carries_on_when_file_is_held():
    d = folder()
    s = logfiles.Stream(d / "notewell.log", max_bytes=500)
    held = open(d / "notewell.log", "a", encoding="utf-8")  # Windows will not rename it now
    try:
        for i in range(50):
            print(f"held {i:02d} " + "z" * 30, file=s)
    finally:
        held.close()
    assert names(d) == ["notewell.log"], names(d)
    text = (d / "notewell.log").read_text(encoding="utf-8")
    assert "held 49" in text, "nothing written while the file was held"
    s._next_try = 0                       # skip the minute's wait
    print("after", file=s)
    s.close()
    assert "notewell.log.1" in names(d), names(d)
    shutil.rmtree(d)


def t_handler_carries_on_when_file_is_held():
    d = folder()
    h = logfiles.handler(d / "screen.log", max_bytes=500)
    errors = []
    h.handleError = lambda record: errors.append(record)
    held = open(d / "screen.log", "a", encoding="utf-8")
    try:
        for i in range(50):
            h.emit(logging.makeLogRecord({"msg": f"held {i:02d} " + "z" * 30}))
    finally:
        held.close()
    h.close()
    assert not errors, f"{len(errors)} log lines failed"
    assert "held 49" in (d / "screen.log").read_text(encoding="utf-8")
    shutil.rmtree(d)


def t_app_uses_it_for_all_three_logs():
    src = (APP / "desktop" / "notewell.py").read_text(encoding="utf-8")
    assert "logfiles.Stream(LOG_FILE)" in src, "notewell.log"
    assert 'logfiles.handler(HOME / "recorder" / "recorder.log")' in src, "recorder.log"
    assert 'logfiles.handler(HOME / "recorder" / "screen.log")' in src, "screen.log"
    assert not re.search(r"logging\.FileHandler|filename=str\(HOME", src), "an unlimited log is left"
    rec = (APP / "windows-recorder" / "recorder.py").read_text(encoding="utf-8")
    assert "logging.FileHandler" not in rec, "recorder.py still opens an unlimited log"


check("the real limits are 5 MB and 3 old copies", t_real_limits)
check("recorder.log / screen.log roll over and keep 3 old copies", t_handler_rolls_and_keeps_three)
check("notewell.log (the app's printed output) rolls over and keeps 3", t_stream_rolls_and_keeps_three)
check("notewell.log keeps writing if another program holds it", t_stream_carries_on_when_file_is_held)
check("recorder.log keeps writing if another program holds it", t_handler_carries_on_when_file_is_held)
check("the app sends all three logs through it", t_app_uses_it_for_all_three_logs)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
