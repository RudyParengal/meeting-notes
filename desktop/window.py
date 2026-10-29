r"""Notewell's own app window: the dashboard in a built-in browser engine (Qt WebEngine).

It needs no browser on the PC and shares nothing with one: its storage is
%USERPROFILE%\Notewell\window-qt. It runs as a second Notewell process,
started by notewell.py with the address in NOTEWELL_WINDOW_URL (not on the
command line, where other programs can read it, because the address carries
the sign-in token).

One window per Windows user: a second start hands its address to the first
window, which loads it and comes to the front.
"""
import ctypes
import getpass
import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEnginePage,
                                     QWebEngineProfile, QWebEngineSettings)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow

HOME = Path(os.environ.get("NOTEWELL_HOME") or (Path.home() / "Notewell"))
ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "desktop" / "notewell.ico"
# Per user, so two people signed in to one PC never reach each other's window.
PIPE = "Notewell.Window." + hashlib.sha256(getpass.getuser().encode()).hexdigest()[:12]


def is_own(url: QUrl) -> bool:
    """The local server is the only place the window itself may show."""
    return url.scheme() in ("http", "https") and url.host() in ("127.0.0.1", "localhost")


def open_outside(url: QUrl) -> None:
    """Web and email links go to the person's own browser or mail program. Nothing
    else: a file:// or search-ms: link in a note would start a program."""
    if url.scheme() in ("http", "https", "mailto"):
        QDesktopServices.openUrl(url)


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main_frame):
        if is_own(url) or url.scheme() in ("blob", "data", "about"):
            return True
        open_outside(url)                          # any other link opens in the person's browser
        return False

    def createWindow(self, kind):
        return Outside(self.profile(), self)       # target=_blank: never a second Notewell window


class Outside(QWebEnginePage):
    """Catches a link meant for a new window and sends it to the default browser."""
    def acceptNavigationRequest(self, url, kind, main_frame):
        open_outside(url)
        self.deleteLater()
        return False


class Window(QMainWindow):
    def __init__(self, url: str):
        super().__init__()
        self.setWindowTitle("Notewell")
        self.resize(1280, 860)
        profile = QWebEngineProfile("Notewell", self)
        profile.setPersistentStoragePath(str(HOME / "window-qt"))
        profile.setCachePath(str(HOME / "window-qt" / "cache"))
        profile.setHttpUserAgent(profile.httpUserAgent() + " Notewell")
        profile.settings().setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, True)
        profile.downloadRequested.connect(self.download)
        self.view = QWebEngineView(self)
        self.view.setPage(Page(profile, self.view))
        self.view.printRequested.connect(self.save_pdf)
        self.view.pdfPrintingFinished.connect(
            lambda path, ok: self.statusBar().showMessage(
                f"Saved {path}" if ok else "Could not save the PDF", 8000))
        self.setCentralWidget(self.view)
        self.statusBar().hide()
        self.view.load(QUrl(url))

    def folder(self) -> str:
        return QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)

    def download(self, item: QWebEngineDownloadRequest):
        item.setDownloadDirectory(self.folder())
        item.accept()
        item.isFinishedChanged.connect(lambda: self.statusBar().showMessage(
            f"Saved to Downloads: {item.downloadFileName()}"
            if item.state() == QWebEngineDownloadRequest.DownloadCompleted
            else "The download did not finish", 8000))
        self.statusBar().show()

    def save_pdf(self):
        """window.print() - the dashboard's PDF button - saves a PDF the person names."""
        name = (self.view.title() or "meeting").strip().replace("/", "-") + ".pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save as PDF", str(Path(self.folder()) / name),
                                              "PDF (*.pdf)")
        if path:
            self.statusBar().show()
            self.view.printToPdf(path)

    def show_url(self, url: str):
        if url:
            self.view.load(QUrl(url))
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()


def main() -> int:
    url = os.environ.pop("NOTEWELL_WINDOW_URL", "")   # not passed on to the engine's helpers
    if url and not is_own(QUrl(url)):
        return 2
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Notewell")
    app.setWindowIcon(QIcon(str(ICON)))

    first = QLocalSocket()
    first.connectToServer(PIPE)
    if first.waitForConnected(500):                # a window is open already: hand it the address
        if os.name == "nt":
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        first.write(url.encode() or b"-")
        first.waitForBytesWritten(2000)
        first.disconnectFromServer()
        return 0

    win = Window(url)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    QLocalServer.removeServer(PIPE)
    server.listen(PIPE)

    def take():
        conn = server.nextPendingConnection()
        def read():
            data = bytes(conn.readAll()).decode(errors="ignore").strip()
            new = "" if data == "-" else data
            if new and not is_own(QUrl(new)):
                return
            win.show_url(new)
        conn.readyRead.connect(read)
    server.newConnection.connect(take)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
