"""Lets the dashboard drive the recorder when both run in one app.

On a server install nothing attaches and every recorder endpoint answers 501:
recording happens in the separate tray app on each person's PC. The Notewell
desktop app runs the recorder in the same process and attaches it here, which
is what puts a Record button in the dashboard.
"""
import time
from typing import Callable, Optional

_controller: Optional[dict] = None


def attach(*, manager, cfg: dict, start: Callable[[], None], stop: Callable[[], None],
           devices: Callable[[], list], save_cfg: Callable[[], None]) -> None:
    """Called once by the desktop launcher after the recorder has started.

    start/stop go through the tray's own handlers so the tray icon changes
    colour whichever side pressed the button."""
    global _controller
    _controller = {"manager": manager, "cfg": cfg, "start": start, "stop": stop,
                   "devices": devices, "save_cfg": save_cfg}


def available() -> bool:
    return _controller is not None


# Settings the dashboard may change. Everything else in the recorder's config
# stays file-only, on purpose: those are tuning values, not choices.
EDITABLE = {"audio_source": str, "language": str, "auto_detect": bool,
            "live_transcript": bool, "whisper_model": str}


def status() -> dict:
    if not _controller:
        return {"available": False}
    mgr, cfg = _controller["manager"], _controller["cfg"]
    cap = getattr(mgr, "capture", None)
    started = getattr(mgr, "started_at", None)
    return {
        "available": True,
        "recording": bool(mgr.in_meeting),
        "seconds": int(time.time() - started) if (mgr.in_meeting and started) else 0,
        "level_system": round(float(getattr(cap, "level_system", 0.0)), 4),
        "level_mic": round(float(getattr(cap, "level_mic", 0.0)), 4),
        "processing": bool(getattr(mgr, "processing", False)),
        "model_ready": bool(getattr(mgr, "model_ready", False)),
        "settings": {k: cfg.get(k) for k in EDITABLE},
    }


def start() -> None:
    _controller["start"]()


def stop() -> None:
    _controller["stop"]()


def devices() -> list:
    return _controller["devices"]()


def update(changes: dict) -> dict:
    cfg = _controller["cfg"]
    for k, v in changes.items():
        if k in EDITABLE and isinstance(v, EDITABLE[k]):
            cfg[k] = v.strip()[:200] if isinstance(v, str) else v
    mgr = _controller["manager"]
    if "audio_source" in changes and hasattr(mgr, "capture"):
        mgr.capture._audio_source = cfg["audio_source"]
    _controller["save_cfg"]()
    return {k: cfg.get(k) for k in EDITABLE}
