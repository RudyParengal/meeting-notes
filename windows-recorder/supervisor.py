"""Keeps the Meeting Recorder running, and records why it stopped.

Why a supervisor that OWNS the recorder
---------------------------------------
The usual way to keep a program running is a watchdog that launches it detached
and then tries to infer, from the outside, whether it worked: is there a process
with the right name? did the log file grow? is a heartbeat file fresh?

Every one of those inferences fails in practice:

  * "Started successfully" was logged for hours while the program died on
    every start. The check was "a process exists".
  * A watchdog hung mid-cycle and logged nothing for days.
  * "Started and is logging" was reported for days while the program wrote zero
    bytes. The check was a file-size delta, and it was simply false.

The common fault is that the watchdog was guessing about a process it did not
own.

This owns it. The recorder runs as a **child** of this process, so:

  * there is no guessing about whether it is alive - we hold the handle;
  * when it dies we get the **exit code**, which no previous version ever had;
  * its stderr is captured to a file we control, so a startup crash cannot
    vanish.

Run with pythonw so there is no console window. One instance only, enforced by
a lock file holding a PID that is verified before it is trusted.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

# Where the recorder keeps its config, logs and in-progress audio.
# Default: C:\Users\<you>\MeetingRecorder. Override with MEETING_RECORDER_HOME.
#
# Deliberately NOT under %APPDATA%. Some launchers (packaged apps, sandboxes,
# some installers) silently redirect %APPDATA% writes into a private copy, so
# the recorder would read one config when started from there and another when
# Windows starts it at login - which looks exactly like "works until reboot".
# The profile root is shared by everything.
HERE = Path(__file__).resolve().parent
APPDIR = Path(os.environ.get("MEETING_RECORDER_HOME") or (Path.home() / "MeetingRecorder"))
APPDIR.mkdir(parents=True, exist_ok=True)

PYTHONW = HERE / "venv" / "Scripts" / "pythonw.exe"
RECORDER = HERE / "recorder.py"

LOG = APPDIR / "supervisor.log"
LOCK = APPDIR / "supervisor.lock"
ALIVE = APPDIR / "supervisor.alive"
CHILD_STDERR = APPDIR / "recorder-stderr.log"

POLL_SECONDS = 5
MIN_BACKOFF = 10
MAX_BACKOFF = 300
CREATE_NO_WINDOW = 0x08000000


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
    except OSError:
        pass


def already_running():
    """True if another supervisor holds the lock and is genuinely alive.

    The PID is verified, not trusted. A lock left behind by a hard kill must
    not stop the recorder coming back - that is how a supervisor turns into
    the outage it exists to prevent.
    """
    try:
        pid = int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        return "supervisor.py" in cmdline
    except Exception:
        return False


def recording_in_progress(window_seconds=120):
    r"""True if a spool file was written very recently - i.e. a meeting is live.

    Captured audio lands in %USERPROFILE%\MeetingRecorder\spool\<session>\ every
    second while recording, so a fresh mtime there means someone is in a call
    right now.

    This exists because a maintenance kill once ended a meeting that was being
    recorded. The audio survived - that is what the spool is for -
    but six minutes in, the recording simply stopped and the rest of that
    meeting was never captured. Nothing automated should ever end a recording.
    """
    spool_root = APPDIR / "spool"
    if not spool_root.is_dir():
        return False
    now = time.time()
    for session in spool_root.iterdir():
        if not session.is_dir() or (session / "DONE").exists():
            continue
        for track in ("sys.pcm", "mic.pcm"):
            try:
                if now - (session / track).stat().st_mtime < window_seconds:
                    return True
            except OSError:
                continue
    return False


def belongs_to_this_install(cmdline: str) -> bool:
    """True only for a recorder started from THIS folder.

    Matching on the bare name "recorder.py" would also hit a second install, a
    copy someone is testing, or an unrelated script of the same name - and
    killing it could end somebody's live recording. The full path is specific.
    """
    norm = cmdline.lower().replace("/", "\\").replace('"', "")
    mine = str(RECORDER).lower().replace("/", "\\")
    return mine in norm and "supervisor.py" not in norm


def kill_stray_recorders():
    """Stop any recorder from THIS folder that we do not own, before starting ours.

    Without this the supervisor fights an orphan. A recorder left over from a
    previous supervisor still holds the single-instance lock, so every child we
    start sees it, logs "already running" and exits **0** - and we restart it
    forever. Seen in practice: two clean exits in twenty seconds.

    Exit code 0 is what made this visible. Every previous supervisor would have
    called those restarts a success.
    """
    killed = []
    if recording_in_progress():
        log("a recording is IN PROGRESS - refusing to stop any recorder")
        return killed
    try:
        import psutil
    except Exception:
        return killed
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            name = (proc.info["name"] or "").lower()
            if "python" not in name:
                continue
            if belongs_to_this_install(" ".join(proc.info["cmdline"] or [])):
                proc.kill()
                killed.append(proc.info["pid"])
        except Exception:
            continue
    return killed


def start_recorder():
    """Launch the recorder as our child, with its stderr captured."""
    err = open(CHILD_STDERR, "a", encoding="utf-8", errors="replace")
    err.write(f"\n===== supervisor starting recorder {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    err.flush()
    proc = subprocess.Popen(
        [str(PYTHONW), str(RECORDER)],
        cwd=str(HERE),
        stdout=subprocess.DEVNULL,
        stderr=err,
        creationflags=CREATE_NO_WINDOW,
    )
    return proc, err


def main():
    if already_running():
        log("another supervisor is already running - exiting")
        return
    try:
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass

    if not PYTHONW.is_file() or not RECORDER.is_file():
        log(f"FATAL: missing {PYTHONW if not PYTHONW.is_file() else RECORDER}")
        return

    log(f"supervisor started (pid {os.getpid()})")
    strays = kill_stray_recorders()
    if strays:
        log(f"stopped {len(strays)} recorder(s) not owned by this supervisor: {strays}")
        time.sleep(2)
    try:
        LOCK_REC = APPDIR / "recorder.lock"
        if LOCK_REC.exists():
            LOCK_REC.unlink()
    except OSError:
        pass
    backoff = MIN_BACKOFF
    proc = None
    err = None
    started_at = 0.0

    while True:
        try:
            ALIVE.write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass

        if proc is None or proc.poll() is not None:
            if proc is not None:
                # The whole point of this file: we know the exit code.
                code = proc.returncode
                if code == 78:
                    # Configuration error, not a crash. Retrying every five
                    # minutes changes nothing - that is what happened for 19
                    # hours, silently, over 200 times. Say what is
                    # wrong and wait for someone to fix it.
                    log(f"CONFIG PROBLEM: recorder has no API key in {APPDIR / 'config.json'} "
                        f"- it cannot record. Retrying in 30 minutes.")
                    if err:
                        try:
                            err.close()
                        except Exception:
                            pass
                    time.sleep(1800)
                    backoff = MIN_BACKOFF
                    proc = None
                    continue
                log(f"recorder exited with code {code} - restarting in {backoff}s")
                if err:
                    try:
                        err.close()
                    except Exception:
                        pass
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
            try:
                proc, err = start_recorder()
                started_at = time.time()
                log(f"recorder started (pid {proc.pid})")
            except Exception as e:
                log(f"failed to start recorder: {type(e).__name__}: {e}")
                proc = None
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue
        elif backoff > MIN_BACKOFF and time.time() - started_at > 60:
            # Surviving a full minute means that start was genuine, so stop
            # widening the gap between attempts.
            backoff = MIN_BACKOFF

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
