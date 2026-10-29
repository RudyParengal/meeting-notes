"""Download the speaker-separation models (optional feature, ~36 MB).

    python fetch_models.py

Every file is checked against a pinned SHA-256 before it is kept. A download
that does not match is deleted and the script fails, so a tampered or corrupted
file is never loaded. Safe to run again: files already present and correct are
left alone.

Nothing here is needed for the recorder to work. Without these files the
transcript simply labels the far end "Others" instead of "Speaker 1 / 2 / 3".
Whisper's own models are fetched automatically the first time you record.
"""
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent / "models" / "diarization"

SEGMENTATION_ARCHIVE = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                        "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMBEDDING_FILE = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                  "speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx")

# name -> expected sha256 of the final file on disk
SEGMENTATION = DEST / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING = DEST / "campplus_en_voxceleb_16k.onnx"
EXPECTED = {
    SEGMENTATION: "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    EMBEDDING: "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str) -> bytes:
    print(f"  downloading {url.rsplit('/', 1)[-1]} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "meeting-notes-installer"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def already_good(path: Path) -> bool:
    return path.is_file() and sha256(path.read_bytes()) == EXPECTED[path]


def save_verified(path: Path, data: bytes) -> None:
    got = sha256(data)
    if got != EXPECTED[path]:
        raise SystemExit(f"CHECKSUM MISMATCH for {path.name}\n  expected {EXPECTED[path]}\n  got      {got}\n"
                         f"Not saved. The download may be corrupted or tampered with.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"  ok  {path.name}  ({len(data) / 1e6:.1f} MB, checksum verified)")


def main() -> int:
    if all(already_good(p) for p in EXPECTED):
        print("Speaker-separation models are already installed and verified.")
        return 0

    if not already_good(EMBEDDING):
        save_verified(EMBEDDING, download(EMBEDDING_FILE))

    if not already_good(SEGMENTATION):
        archive = download(SEGMENTATION_ARCHIVE)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as tar:
            member = next((m for m in tar.getmembers()
                           if m.isfile() and m.name.endswith("/model.onnx")), None)
            if member is None:
                raise SystemExit("model.onnx not found in the downloaded archive.")
            # Read ONE named member into memory. Never extractall(): an archive
            # can contain paths that escape the target folder.
            save_verified(SEGMENTATION, tar.extractfile(member).read())

    print("Done. Set \"diarize_others\": true in config.json to use them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
