"""Geheimnisse (Sicherungspasswort) geschützt in der Datenbank ablegen: Windows-DPAPI, gebunden an das Benutzerkonto.

Der gespeicherte Wert ist nur unter demselben Windows-Benutzer auf demselben PC lesbar. In Kopien der Datenbank
(Sicherung, „vor-update“, weitergegebene Datei) ist er damit wertlos. Nach einem PC- oder Benutzerwechsel muss das
Passwort neu eingetragen werden.
"""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), buf), Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: nie ein Fenster zeigen
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError("DPAPI-Aufruf fehlgeschlagen")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def is_protected(stored: str | None) -> bool:
    return (stored or "").startswith(PREFIX)


def protect(text: str) -> str:
    """Klartext → Wert für die Datenbank. Leer bleibt leer; außerhalb von Windows bleibt der Text unverändert."""
    if not text or sys.platform != "win32":
        return text or ""
    return PREFIX + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(stored: str | None) -> str:
    """Wert aus der Datenbank → Klartext. Ein noch ungeschützter Altwert wird unverändert geliefert; ein Wert eines
    anderen Benutzers/PCs (z.B. aus einer fremden Datenbank) ergibt leer."""
    stored = stored or ""
    if not is_protected(stored):
        return stored
    try:
        return _dpapi(base64.b64decode(stored[len(PREFIX):]), False).decode("utf-8")
    except (OSError, ValueError, AttributeError):
        return ""
