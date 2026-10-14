"""Verschlüsselte Sicherungen (optional): AES-256-GCM in Blöcken, Schlüssel per scrypt aus einem Passwort.

Dateiaufbau: MAGIC | Salt (16) | Nonce-Präfix (8) | Blöcke aus [Länge (4, big endian) | Chiffrat inkl. Tag].
Jeder Block hat die Nonce Präfix + Blockzähler; der letzte Block ist über die Zusatzdaten markiert, sodass
abgeschnittene oder umsortierte Dateien beim Entschlüsseln auffallen.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

MAGIC = b"KLEINFAKTURA-ENC1"
CHUNK = 4 * 1024 * 1024
_LAST, _MORE = b"last", b"more"


class WrongPassword(ValueError):
    pass


def _key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))


def is_encrypted(path: Path | str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_file(src: Path, dst: Path, password: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not password:
        raise ValueError("Für die verschlüsselte Sicherung fehlt das Passwort (Einstellungen → Datensicherung).")
    salt, prefix = os.urandom(16), os.urandom(8)
    aes = AESGCM(_key(password, salt))
    with open(src, "rb") as fin, open(dst, "wb") as out:
        out.write(MAGIC + salt + prefix)
        counter = 0
        block = fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            last = not nxt
            ct = aes.encrypt(prefix + struct.pack(">I", counter), block, _LAST if last else _MORE)
            out.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            block, counter = nxt, counter + 1


def decrypt_file(src: Path, dst: Path, password: str) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(src, "rb") as fin, open(dst, "wb") as out:
        if fin.read(len(MAGIC)) != MAGIC:
            raise ValueError("Die Datei ist keine verschlüsselte Sicherung.")
        salt, prefix = fin.read(16), fin.read(8)
        aes = AESGCM(_key(password or "", salt))
        counter = 0
        while True:
            head = fin.read(4)
            if len(head) < 4:
                raise ValueError("Die verschlüsselte Sicherung ist unvollständig (abgeschnitten).")
            ct = fin.read(struct.unpack(">I", head)[0])
            nonce = prefix + struct.pack(">I", counter)
            try:
                out.write(aes.decrypt(nonce, ct, _LAST))
                break
            except InvalidTag:
                try:
                    out.write(aes.decrypt(nonce, ct, _MORE))
                except InvalidTag as e:
                    if counter == 0:
                        raise WrongPassword("Das Passwort passt nicht zu dieser Sicherung.") from e
                    raise ValueError("Die verschlüsselte Sicherung ist beschädigt.") from e
            counter += 1
        if fin.read(1):
            raise ValueError("Die verschlüsselte Sicherung ist beschädigt (Daten nach dem Ende).")
