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
]
binaries = []
hiddenimports = [
    # server
    "fastapi", "pydantic", "httpx", "dotenv", "multipart", "openai",
    # recorder
    "numpy", "soundcard", "pystray", "PIL", "PIL.Image", "PIL.ImageDraw", "psutil",
    "requests", "tkinter", "winsound", "wave", "tarfile", "ipaddress", "hmac",
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
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Notewell",
    icon=REPO + "/desktop/notewell.ico",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Notewell")
