"""Clearing the laptop's spare copies without losing anything by mistake (2.7.0).

recycle(paths)       sends files to the Windows Recycle Bin, where they can be put back from
trim_backups(home)   keeps the newest 2 backup copies of dictation.db, config.json and
                     linked-server.json and sends older ones to the Recycle Bin

Every install copies those three files first (<file>.bak-<date>-<number>) and nothing ever
removed the copies: 9 of dictation.db had piled up by 20 Nov. The app runs trim_backups each
time it starts, so each install ends with 2.
"""
import ctypes
import logging
import sys
from pathlib import Path

log = logging.getLogger("tidy")
KEEP = 2
BACKED_UP = ("data/dictation.db", "recorder/config.json", "linked-server.json")


def recycle(paths):
    """Send each file to the Recycle Bin and return the ones that went.
    Anything that could not be sent stays where it is. Off Windows nothing is removed."""
    gone = []
    if sys.platform != "win32":
        return gone
    from ctypes import wintypes

    class FileOp(ctypes.Structure):                      # SHFILEOPSTRUCTW
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    delete, undo, quiet = 3, 0x0040, 0x0004 | 0x0010 | 0x0400   # allow undo = Recycle Bin; no windows
    for p in paths:
        p = Path(p)
        name = ctypes.create_unicode_buffer(str(p.resolve()) + "\0")   # the list ends with two zeros
        op = FileOp(None, delete, ctypes.addressof(name), None, undo | quiet, False, None, None)
        try:
            code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except OSError as e:
            code = e
        if code == 0 and not op.fAnyOperationsAborted and not p.exists():
            gone.append(p)
        else:
            log.warning("could not send %s to the Recycle Bin (%s) - kept", p.name, code)
    return gone


def _age(p):
    try:
        return p.stat().st_mtime
    except OSError:
        return 0


def trim_backups(home, keep=KEEP, send=None):
    """Keep the newest `keep` copies of each file in BACKED_UP, send older ones to the Recycle Bin."""
    send = send or recycle
    sent = []
    for rel in BACKED_UP:
        live = Path(home) / rel
        copies = sorted(live.parent.glob(live.name + ".bak-*"), key=lambda p: (_age(p), p.name), reverse=True)
        if len(copies) > keep:
            sent += send(copies[keep:])
    if sent:
        log.info("Sent %d old backup copies to the Recycle Bin, kept the newest %d of each", len(sent), keep)
    return sent
