"""Secrets kept on disk by the desktop app, locked to the Windows user.

On Windows, seal() encrypts with DPAPI (the same lock Windows uses for saved
Wi-Fi and browser passwords): only this Windows account on this computer can
unseal it, so a copied file, a backup or a stolen disk image is useless on its
own. Elsewhere (a Linux server keeps its key in backend/.env) text passes
through unchanged.

A value written before sealing existed has no "dpapi:" prefix and is returned
as it is, so an upgrade never loses a key; the caller saves it sealed next time.
"""
import base64
import ctypes
import os

PREFIX = "dpapi:"

if os.name == "nt":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _UI_FORBIDDEN = 0x1          # never show a prompt; fail instead

    def _run(fn, data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not fn(ctypes.byref(blob_in), None, None, None, None, _UI_FORBIDDEN,
                  ctypes.byref(blob_out)):
            raise OSError(ctypes.get_last_error(), "Windows could not unlock this value")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)


def available() -> bool:
    return os.name == "nt"


def seal(text: str) -> str:
    if not text or not available() or text.startswith(PREFIX):
        return text
    return PREFIX + base64.b64encode(_run(_crypt32.CryptProtectData, text.encode())).decode()


def unseal(text: str) -> str:
    """Raises OSError when the value was sealed by another Windows user or PC."""
    if not text or not text.startswith(PREFIX):
        return text
    if not available():
        raise OSError("this value was locked on Windows and can only be read there")
    return _run(_crypt32.CryptUnprotectData, base64.b64decode(text[len(PREFIX):])).decode()
