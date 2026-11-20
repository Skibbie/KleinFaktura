"""Prüft, ob ein Schlüssel der private Update-Signaturschlüssel ist, und setzt ihn auf Wunsch als Secret.

Privater und öffentlicher Schlüssel sehen gleich aus (44 Zeichen Base64). Das Skript fragt den Schlüssel verdeckt ab,
berechnet daraus den öffentlichen Schlüssel und vergleicht ihn mit UPDATE_PUBLIC_KEY in app/services/update.py.
Der Schlüssel wird weder angezeigt noch gespeichert.

Aufruf:
  python tools/check_update_key.py            nur prüfen
  python tools/check_update_key.py --setzen   prüfen und bei Erfolg als Secret UPDATE_SIGNING_KEY im
                                              Environment „release“ hinterlegen (über gh)
"""
from __future__ import annotations

import base64
import binascii
import getpass
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPDATE_PY = ROOT / "app" / "services" / "update.py"


def expected_public_key() -> str:
    match = re.search(r'^UPDATE_PUBLIC_KEY = "(.*)"', UPDATE_PY.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit("UPDATE_PUBLIC_KEY in app/services/update.py nicht gefunden.")
    return match.group(1)


def main(args: list[str]) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    expected = expected_public_key()
    key_b64 = getpass.getpass("Schlüssel einfügen (Eingabe bleibt unsichtbar), dann Enter: ").strip()
    if key_b64 == expected:
        print("NEIN: Das ist der ÖFFENTLICHE Schlüssel (steht auch in update.py). Zum Signieren taugt er nicht.")
        return 1
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError):
        print("NEIN: Kein gültiges Base64.")
        return 1
    if len(raw) != 32:
        print(f"NEIN: Falsche Länge ({len(raw)} Byte statt 32).")
        return 1
    public = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if base64.b64encode(public).decode("ascii") != expected:
        print("NEIN: Gültiger Schlüssel, passt aber nicht zum öffentlichen Schlüssel in update.py.")
        return 1
    print("JA: Das ist der private Update-Signaturschlüssel.")
    if "--setzen" in args:
        result = subprocess.run(["gh", "secret", "set", "UPDATE_SIGNING_KEY", "--env", "release"],
                                check=False, input=key_b64, text=True, cwd=ROOT)
        if result.returncode != 0:
            print("Setzen fehlgeschlagen (gh angemeldet?).")
            return 1
        print("Als Secret UPDATE_SIGNING_KEY im Environment „release“ hinterlegt.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
