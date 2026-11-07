"""Randfälle bei Geldbeträgen, Datumsangaben und verschlüsselten Sicherungen – nur Dummy-Daten."""
from __future__ import annotations

import struct
from datetime import date, datetime
from decimal import Decimal

import pytest


# ------------------------------------------------------------------ Geld und Datum
@pytest.mark.parametrize(("text", "expected"), [
    (None, "0"), ("", "0"), ("  €  ", "0"),
    ("1.234,56", "1234.56"),        # deutsch
    ("1,234.56", "1234.56"),        # englisch mit Tausenderkomma
    ("1.234.567", "1234567"),       # nur Tausenderpunkte
    ("-12,50", "-12.50"), ("(12,50)", "-12.50"),  # Minus und Buchhaltungsklammern
    ("12,50 EUR", "12.50"), (7, "7"), (0.1, "0.1"), (Decimal("3.30"), "3.30"),
])
def test_to_decimal_formats(text, expected):
    from app.money import to_decimal
    assert to_decimal(text) == Decimal(expected)


def test_to_decimal_rejects_garbage():
    from app.money import to_decimal
    with pytest.raises(ValueError):
        to_decimal("zwölf Euro")


def test_input_decimal_reads_german_thousands():
    from app.money import input_cents, input_decimal, to_decimal
    assert input_decimal("1.500") == 1500 and to_decimal("1.500") == Decimal("1.5")   # Eingabe vs. gespeicherter Wert
    assert input_cents("1.500,25 €") == 150025 and input_cents("19 %") == 1900


def test_cent_rounding_is_commercial():
    from app.money import line_total_cents, to_cents
    assert to_cents("0,005") == 1 and to_cents("-0,005") == -1                       # kaufmännisch, nicht „Banker's“
    assert line_total_cents("2,5", 333) == 833 and line_total_cents("-1", 72000) == -72000


def test_formatting():
    from app.money import fmt_date, fmt_eur, fmt_qty
    assert fmt_eur(-123456789) == "-1.234.567,89 €" and fmt_eur(None, symbol=False) == "0,00"
    assert fmt_qty("2") == "2,0" and fmt_qty("1234.25") == "1.234,25" and fmt_qty("0.500") == "0,5"
    assert fmt_date("2026-11-07") == "07.11.2026" and fmt_date(date(2026, 1, 2)) == "02.01.2026"
    assert fmt_date("") == "" and fmt_date("unbekannt") == "unbekannt"


@pytest.mark.parametrize(("text", "expected"), [
    ("7.11.2026", "2026-11-07"), ("07.11.26", "2026-11-07"), ("2026-11-07T10:00", "2026-11-07"),
    (datetime(2026, 11, 7, 9), "2026-11-07"), (date(2026, 11, 7), "2026-11-07"),
    ("31.02.2026", None), ("2026-13-01", None), ("11/07/2026", None), ("", None), (None, None),
])
def test_parse_date(text, expected):
    from app.money import parse_date
    assert parse_date(text) == expected


# ------------------------------------------------------------------ Verschlüsselte Sicherung
@pytest.fixture()
def small_chunks(monkeypatch):
    """Kleine Blöcke, damit mehrteilige Dateien ohne Megabyte-Testdaten geprüft werden."""
    from app.services import backup_crypto
    monkeypatch.setattr(backup_crypto, "CHUNK", 64)
    return backup_crypto


def _blocks(data: bytes, header: int) -> list[tuple[int, int]]:
    """(Start, Ende) jedes Blocks inkl. Längenfeld."""
    out, pos = [], header
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        out.append((pos, pos + 4 + n))
        pos += 4 + n
    return out


@pytest.mark.parametrize("size", [0, 1, 64, 128, 200])   # leer, ein Block, genau Blockgrenzen, mehrere Blöcke
def test_encrypted_roundtrip_block_sizes(small_chunks, tmp_path, size):
    src, enc, out = tmp_path / "a.zip", tmp_path / "a.enc", tmp_path / "b.zip"
    payload = bytes(range(256)) * 2
    src.write_bytes(payload[:size])
    small_chunks.encrypt_file(src, enc, "geheim")
    assert small_chunks.is_encrypted(enc) and not small_chunks.is_encrypted(src)
    small_chunks.decrypt_file(enc, out, "geheim")
    assert out.read_bytes() == payload[:size]


def test_encrypted_backup_detects_tampering(small_chunks, tmp_path):
    src, enc, out = tmp_path / "a.zip", tmp_path / "a.enc", tmp_path / "b.zip"
    src.write_bytes(b"x" * 200)                                      # 4 Blöcke
    small_chunks.encrypt_file(src, enc, "geheim")
    data = enc.read_bytes()
    header = len(small_chunks.MAGIC) + 16 + 8
    blocks = _blocks(data, header)
    assert len(blocks) == 4

    def fails(content: bytes, message: str, pw: str = "geheim"):
        enc.write_bytes(content)
        with pytest.raises(ValueError, match=message):
            small_chunks.decrypt_file(enc, out, pw)

    fails(data[:blocks[-1][0]], "unvollständig")                   # letzter Block fehlt
    fails(data[:blocks[1][0]] + data[blocks[2][0]:], "beschädigt")  # Block entfernt
    b1, b2 = data[blocks[1][0]:blocks[1][1]], data[blocks[2][0]:blocks[2][1]]
    fails(data[:blocks[1][0]] + b2 + b1 + data[blocks[2][1]:], "beschädigt")   # Blöcke vertauscht
    flipped = bytearray(data)
    flipped[blocks[2][0] + 10] ^= 1
    fails(bytes(flipped), "beschädigt")                              # ein Bit verändert
    fails(data + b"\0", "Daten nach dem Ende")
    fails(data, "Passwort passt nicht", pw="falsch")
    fails(b"PK\x03\x04 kein Chiffrat", "keine verschlüsselte Sicherung")


def test_encrypt_requires_password(small_chunks, tmp_path):
    src = tmp_path / "a.zip"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="Passwort"):
        small_chunks.encrypt_file(src, tmp_path / "a.enc", "")
    assert not small_chunks.is_encrypted(tmp_path / "fehlt.enc")
