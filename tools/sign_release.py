"""Signiert die Release-Dateien (läuft im Release-Workflow).

Erzeugt SHA256SUMS.txt (Prüfsumme und Name je Datei) und SHA256SUMS.txt.sig (Ed25519-Signatur, Base64) über den
exakten Inhalt von SHA256SUMS.txt. Der private Schlüssel kommt Base64-kodiert aus der Umgebungsvariablen
UPDATE_SIGNING_KEY (GitHub-Secret). Fehlt er, bricht das Skript ab – ein Release ohne Signatur würden die
installierten Programme ohnehin ablehnen.

Aufruf: python tools/sign_release.py <Datei> [<Datei> …]
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path


def main(paths: list[str]) -> int:
    key_b64 = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key_b64:
        print("UPDATE_SIGNING_KEY fehlt (GitHub-Secret) – Release wird nicht signiert und abgebrochen.", file=sys.stderr)
        return 1
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_b64))
    lines = []
    for p in map(Path, paths):
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    sums = ("\n".join(lines) + "\n").encode("utf-8")
    Path("SHA256SUMS.txt").write_bytes(sums)
    Path("SHA256SUMS.txt.sig").write_text(base64.b64encode(key.sign(sums)).decode("ascii"), encoding="ascii")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
