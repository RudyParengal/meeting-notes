# PyInstaller recipe for the Windows app.   pyinstaller desktop/notewell.spec
#
# backend/, dashboard/ and windows-recorder/ ship as plain source folders, not
# frozen modules: they find their own files (the dashboard, the speaker models)
# relative to __file__, and notewell.py puts them on sys.path at start-up.
# Because PyInstaller never reads them, the libraries they use are listed here.
from PyInstaller.utils.hooks import collect_all, collect_submodules

REPO = SPECPATH + "/.."

datas = [
    (REPO + "/backend", "backend"),
    (REPO + "/dashboard", "dashboard"),
    (REPO + "/windows-recorder", "windows-recorder"),
    (REPO + "/desktop/notewell.ico", "desktop"),     # the app window's icon
]
binaries = []
hiddenimports = [
    # server
    "fastapi", "pydantic", "httpx", "dotenv", "multipart", "openai",
    # recorder
    "numpy", "soundcard", "pystray", "PIL", "PIL.Image", "PIL.ImageDraw", "psutil",
    "requests", "tkinter", "winsound", "wave", "tarfile", "ipaddress", "hmac",
    # the app window (desktop/window.py) and the link to a server of your own
    "window", "linked_server", "PySide6.QtWebEngineWidgets", "PySide6.QtNetwork",
]
for pkg in ("uvicorn", "faster_whisper", "ctranslate2", "av", "sherpa_onnx",
            "soundcard", "pystray", "tokenizers", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("fastapi") + collect_submodules("pydantic")

a = Analysis(
    [REPO + "/desktop/notewell.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["weasyprint", "matplotlib", "pytest"],  # PDF falls back to the print window
)

# Qt brings far more than one web window needs (about 100 MB): 3D, charts, maps,
# media, the QML controls, and the web engine's 52 other page languages.
QT_UNUSED = ("Qt63D", "Qt6Quick3D", "Qt6Charts", "Qt6Graphs", "Qt6DataVisualization",
             "Qt6Location", "Qt6Multimedia", "Qt6SpatialAudio", "Qt6Sensors", "Qt6TextToSpeech",
             "Qt6VirtualKeyboard", "Qt6RemoteObjects", "Qt6Scxml", "Qt6StateMachine", "Qt6Test",
             "Qt6QuickTest", "Qt6SerialPort", "Qt6Sql", "Qt6WebView", "Qt6QuickControls2",
             "Qt6QuickDialogs2", "Qt6Labs", "Qt6QuickTimeline", "Qt6QuickParticles",
             "Qt6QuickEffects", "Qt6QuickVectorImage", "Qt6PdfQuick", "Qt6WebEngineQuick")


def wanted(entry):
    path = entry[0].replace("\\", "/")
    if not path.startswith("PySide6/"):
        return True
    name = path.rsplit("/", 1)[-1]
    if "/qml/" in path or (path.startswith("PySide6/translations/") and name != "en-US.pak"):
        return False
    return not name.startswith(QT_UNUSED)


a.binaries = [x for x in a.binaries if wanted(x)]
a.datas = [x for x in a.datas if wanted(x)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Notewell",
    icon=REPO + "/desktop/notewell.ico",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Notewell")
