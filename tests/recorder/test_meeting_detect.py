r"""Tests for what starts a meeting recording by itself (chunk 08, step 5).

Run:  python tests/recorder/test_meeting_detect.py

Before 2.7.0 a meeting was "on" when Zoom or Teams was merely running, or any
window title held "google meet", "microsoft teams"... So a Google Meet call was
missed (its tab is "Meet - abc-defg-hij"), an open Teams chat or an email about
a Zoom meeting counted, and bringing another browser tab to the front during a
call stopped the recording. Now: the meeting's window AND that same program
using the microphone, and it stays on while that program keeps the microphone.
"""
import logging, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows-recorder"))
import recorder as R

_passed, _failed = 0, []
CFG = {"auto_detect_processes": list(R.DEFAULT_CONFIG["auto_detect_processes"]),
       "auto_detect_browser_meeting": True}
CHROME, EDGE = " - Google Chrome", " - Personal - Microsoft​ Edge"


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _failed.append((name, e))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def find(windows, mic, running=(), cfg=CFG):
    return R.find_meeting(windows, None if mic is None else set(mic), set(running), cfg)


# ------------------------------------------------------ browser meetings --
def t_google_meet():
    for prog, end in (("chrome", CHROME), ("msedge", EDGE)):
        for title in ("Meet - abc-defg-hij", "Meet – abc-defg-hij", "Meet - abc-defg-hij and 2 more pages"):
            assert find([(prog, title + end)], [prog]) == (prog, "Google Meet in " + prog), (prog, title)


def t_teams_web():
    for prog, end in (("chrome", CHROME), ("msedge", EDGE)):
        for title in ("Meeting with Sam | Microsoft Teams", "Weekly sales | Microsoft Teams",
                      "teams.microsoft.com"):
            assert find([(prog, title + end)], [prog]) == (prog, "Teams in " + prog), (prog, title)


def t_zoom_web():
    for prog, end in (("chrome", CHROME), ("msedge", EDGE)):
        for title in ("Zoom Meeting", "Zoom", "Zoom Workplace", "Zoom Webinar"):
            assert find([(prog, title + end)], [prog]) == (prog, "Zoom in " + prog), (prog, title)


def t_other_browser_tabs_in_front_are_not_a_meeting():
    # the browser is using the microphone (a call in a tab at the back) but no meeting title has been seen
    assert find([("chrome", "Inbox - Gmail" + CHROME)], ["chrome"]) is None


# ------------------------------------------------------------ desktop apps --
def t_desktop_apps_on_a_call():
    for prog, mic in (("Zoom.exe", "zoom"), ("ms-teams.exe", R._prog("MSTeams_8wekyb3d8bbwe")),
                      ("Teams.exe", "teams"), ("webexmta.exe", "webexmta"), ("slack.exe", "slack")):
        assert find([], [mic], [R._prog(prog)]) == (R._prog(prog), R._prog(prog)), prog


def t_names_windows_uses():
    assert [R._prog(n) for n in ("ms-teams.exe", "MSTeams_8wekyb3d8bbwe",
                                 r"C:#Program Files#Google#Chrome#Application#chrome.exe", "Zoom.exe", None)] \
        == ["msteams", "msteams", "chrome", "zoom", ""]


# --------------------------------------------------- what is NOT a meeting --
def t_not_meetings():
    cases = [
        ("Teams open, no call", [("msteams", "Chat | Sam | Microsoft Teams")], [], ["msteams"]),
        ("Zoom open in the tray", [("zoom", "Zoom Workplace")], [], ["zoom"]),
        ("Meet home page", [("chrome", "Google Meet" + CHROME)], [], ["chrome"]),
        ("email about a Zoom meeting", [("outlook", "Zoom meeting tomorrow - Message (HTML)")], ["chrome"], []),
        ("Notewell itself using the mic", [("notewell", "Notewell")], ["notewell", "pythonw"], []),
        ("voice typing in Google Docs", [("chrome", "Notes - Google Docs" + CHROME)], ["chrome"], []),
    ]
    for name, windows, mic, running in cases:
        assert find(windows, mic, running) is None, name


def t_browser_switch_off():
    cfg = dict(CFG, auto_detect_browser_meeting=False)
    assert find([("chrome", "Meet - abc-defg-hij" + CHROME)], ["chrome"], cfg=cfg) is None
    assert find([], ["zoom"], ["zoom"], cfg=cfg) == ("zoom", "zoom"), "desktop apps still count"


# --------------------------------------------------------- start and stop --
def t_tab_to_the_back_keeps_it_on():
    state = {"windows": [("chrome", "Meet - abc-defg-hij" + CHROME)], "mic": {"chrome"}}
    watch = R.MeetingWatch(look=lambda: (state["windows"], {"chrome"}, state["mic"]))
    assert watch(CFG) == "Google Meet in chrome"
    state["windows"] = [("chrome", "Budget 2027 - Google Sheets" + CHROME)]     # another tab in front
    assert watch(CFG), "the call's tab at the back stopped the recording"
    state["mic"] = set()                                                    # left the call
    assert not watch(CFG)
    state["mic"] = {"chrome"}                                               # mic again, no meeting tab
    assert not watch(CFG), "the browser using the mic later is not a meeting"


def t_log_says_what_not_the_title():
    lines = []
    h = logging.Handler()
    h.emit = lambda r: lines.append(r.getMessage())
    R.log.addHandler(h)
    R.log.setLevel(logging.INFO)
    try:
        watch = R.MeetingWatch(look=lambda: ([("msedge", "Salary review | Microsoft Teams" + EDGE)], set(), {"msedge"}))
        watch(CFG)
    finally:
        R.log.removeHandler(h)
    assert lines == ["Meeting found: Teams in msedge"], lines


def t_old_rule_if_windows_record_unreadable():
    assert find([], None, ["zoom"]) == ("zoom", "zoom")
    assert find([("chrome", "Meeting | Microsoft Teams" + CHROME)], None) == ("chrome", "microsoft teams")
    assert find([("chrome", "Inbox" + CHROME)], None) is None
    watch = R.MeetingWatch(look=lambda: ([], {"zoom"}, None))
    assert watch(CFG) and watch(CFG), "still works when the record cannot be read"


# --------------------------------------------- Windows' microphone record --
class FakeReg:
    """Stands in for winreg: Windows' list of programs and when each last used the mic."""
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, tree):
        self.tree = tree                         # {path: {app: (start, stop)}}

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, parent, name):
        path = name if parent == "HKCU" else parent.path + "\\" + name
        known = path in self.tree or any(path.rsplit("\\", 1)[0] == p and path.rsplit("\\", 1)[1] in a
                                         for p, a in self.tree.items())
        if not known:
            raise OSError("no such key")
        return self.Key(path)

    def EnumKey(self, key, i):
        apps = list(self.tree.get(key.path, {}))
        if i >= len(apps):
            raise OSError("no more")
        return apps[i]

    def QueryValueEx(self, key, value):
        parent, app = key.path.rsplit("\\", 1)
        times = self.tree.get(parent, {}).get(app)
        if times is None:
            raise OSError("no value")
        return (times[0] if value == "LastUsedTimeStart" else times[1], 4)


def t_mic_record():
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    reg = FakeReg({
        base: {"MSTeams_8wekyb3d8bbwe": (134353440031838638, 0),            # on a call now
               "Microsoft.WindowsSoundRecorder_8wekyb3d8bbwe": (1343137, 1343137 + 9)},
        base + r"\NonPackaged": {r"C:#Program Files#Google#Chrome#Application#chrome.exe": (134353440031838638, 0),
                                 r"C:#Program Files#Notewell#Notewell.exe": (1343, 1350),
                                 r"C:#Never#used.exe": (0, 0)},
    })
    assert R.mic_users(reg) == {"msteams", "chrome"}, R.mic_users(reg)
    assert R.mic_users(FakeReg({})) is None, "unreadable must say so, not 'nobody'"


def t_real_windows_calls_work():
    # read-only, on this PC: the window list and Windows' microphone record can be read
    windows, running = R.list_windows()
    assert running and all(isinstance(p, str) and isinstance(t, str) for p, t in windows)
    assert R._prog(Path(sys.executable).name) in running
    assert isinstance(R.mic_users(), set)
    assert R.is_meeting_active(CFG) in (True, False)


check("Google Meet in Chrome and Edge (tab 'Meet - abc-defg-hij')", t_google_meet)
check("Teams on the web in Chrome and Edge", t_teams_web)
check("Zoom on the web in Chrome and Edge", t_zoom_web)
check("a browser using the mic with no meeting tab seen is not a meeting", t_other_browser_tabs_in_front_are_not_a_meeting)
check("desktop Zoom / Teams (new and classic) / Webex / Slack on a call", t_desktop_apps_on_a_call)
check("program names, however Windows spells them", t_names_windows_uses)
check("not meetings: Teams or Zoom just open, Meet home page, an email, Notewell, Docs", t_not_meetings)
check("browser meetings can be switched off; desktop apps still count", t_browser_switch_off)
check("another tab in front mid-call keeps recording; leaving the call stops it", t_tab_to_the_back_keeps_it_on)
check("the log names the meeting app, never the window title", t_log_says_what_not_the_title)
check("if Windows' mic record cannot be read: the old rule", t_old_rule_if_windows_record_unreadable)
check("Windows' microphone record: in use now = started, not stopped", t_mic_record)
check("the real window list and mic record can be read on this PC", t_real_windows_calls_work)

print()
total = _passed + len(_failed)
if _failed:
    print(f"{_passed}/{total} passed, {len(_failed)} FAILED:")
    for n, e in _failed:
        print(f"  - {n}: {e}")
    sys.exit(1)
print(f"{_passed}/{total} passed")
