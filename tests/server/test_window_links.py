"""The app window shows only this computer's own server, and hands web and email
links to the person's browser. Any other kind of link is dropped, because
Windows would start whatever program owns it (file://, search-ms:, ...)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWebEngineCore")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop"))
import window  # noqa: E402
from PySide6.QtCore import QUrl  # noqa: E402


@pytest.fixture()
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(window.QDesktopServices, "openUrl", lambda url: seen.append(url.toString()))
    return seen


def test_only_the_local_server_is_shown_in_the_window():
    assert window.is_own(QUrl("http://127.0.0.1:8767/"))
    assert window.is_own(QUrl("http://localhost:8767/#token=x"))
    assert not window.is_own(QUrl("https://example.com/"))
    assert not window.is_own(QUrl("http://127.0.0.1.example.com/"))
    assert not window.is_own(QUrl("file:///C:/Windows/System32/calc.exe"))


def test_web_and_email_links_go_to_the_browser(opened):
    for u in ("https://example.com/a", "http://example.com/", "mailto:someone@example.com"):
        window.open_outside(QUrl(u))
    assert len(opened) == 3


def test_links_that_would_start_a_program_are_dropped(opened):
    for u in ("file:///C:/Windows/System32/calc.exe", "search-ms:query=x&crumb=location:\\\\evil",
              "ms-msdt:/id PCWDiagnostic", "javascript:alert(1)", "\\\\server\\share\\x.exe"):
        window.open_outside(QUrl(u))
    assert opened == []
