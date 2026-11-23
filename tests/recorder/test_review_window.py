"""Review window tests (windows-recorder/screen_record.py _review_dialog).

    python tests/recorder/test_review_window.py

Drawn far off-screen, so nothing shows on the real screen. Made-up job, no real video.
"""
import sys
import threading
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "windows-recorder"))
import screen_record as sr  # noqa: E402

_passed, _failed = 0, []
OFF_SCREEN = (-20000, -20000, 800, 600)


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


print("\nReview window")


def t_minimised_window_comes_back_and_sends_nothing():
    # 23 Nov 03:41: the window was minimised, nobody could find it, the recording sat unsent for hours.
    root = tk.Tk()
    root.withdraw()
    cmds, seen = [], {}

    def review_win():
        return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]

    def minimise():
        review_win().iconify()
        root.update_idletasks()
        seen["right_after"] = review_win().state()

    def look_then_discard():
        seen["one_second_later"] = review_win().state()
        cmds.append("discard")

    root.after(300, minimise)
    root.after(1300, look_then_discard)
    root.after(6000, lambda: cmds.append("discard"))  # safety net so a broken test cannot hang
    try:
        got = sr._review_dialog(root, tk, {"title": "Test"}, Path("no-such-video.mp4"), 12.0, OFF_SCREEN,
                                threading.Event(), threading.Event(), cmds)
    finally:
        root.destroy()
    assert seen.get("right_after") == "iconic", seen            # the test really did minimise it
    assert seen.get("one_second_later") == "normal", seen       # and it came back by itself
    assert got == ("discard", None, None), got                  # nothing was sent while it was hidden


check("a minimised review window comes back by itself and sends nothing on its own",
      t_minimised_window_comes_back_and_sends_nothing)

print(f"\n{_passed} passed, {len(_failed)} failed")
sys.exit(1 if _failed else 0)
