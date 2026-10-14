"""Erzeugt einmalig das Schlüsselpaar für die Update-Signatur.

- Der PRIVATE Schlüssel wird in eine Datei geschrieben (Standard: Dokumente\\KleinFaktura-Update-Signaturschluessel.txt).
  Er gehört als GitHub-Secret UPDATE_SIGNING_KEY ins Repository und zusätzlich offline gesichert (z.B. USB-Stick,
  Passwortmanager). Geht er verloren, können installierte Programme nicht mehr automatisch aktualisiert werden.
- Der ÖFFENTLICHE Schlüssel wird in app/services/update.py eingetragen (UPDATE_PUBLIC_KEY) und ausgegeben.

Eine vorhandene Schlüsseldatei wird nie überschrieben.

Aufruf: python tools/make_update_key.py [Zieldatei]
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPDATE_PY = ROOT / "app" / "services" / "update.py"


def main(args: list[str]) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    target = Path(args[0]) if args else Path.home() / "Documents" / "KleinFaktura-Update-Signaturschluessel.txt"
    if target.exists():
        print(f"{target} existiert bereits – es wird kein neuer Schlüssel erzeugt.", file=sys.stderr)
        return 1
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base64.b64encode(private).decode("ascii"), encoding="ascii")
    pub_b64 = base64.b64encode(public).decode("ascii")
    text = UPDATE_PY.read_bytes().decode("utf-8")          # Zeilenenden unverändert lassen
    new, n = re.subn(r'^UPDATE_PUBLIC_KEY = ".*"(\r?)$', f'UPDATE_PUBLIC_KEY = "{pub_b64}"\\1', text, flags=re.MULTILINE)
    if n != 1:
        print("UPDATE_PUBLIC_KEY in app/services/update.py nicht gefunden – bitte von Hand eintragen.", file=sys.stderr)
    else:
        UPDATE_PY.write_bytes(new.encode("utf-8"))
    print(f"Privater Schlüssel: {target}")
    print(f"Öffentlicher Schlüssel (in update.py eingetragen): {pub_b64}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
