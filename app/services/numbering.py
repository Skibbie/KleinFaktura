"""Nummernkreise.

Rechnung (und Storno): JJJJ-MMNN, z.B. 2026-1001 – Zähler startet jeden Monat bei 01.
Angebot:              ANJJJJ-MMNN, z.B. AN2026-1001.
Die Vergabe erfolgt erst beim Festschreiben innerhalb der Festschreibe-Transaktion,
damit keine Lücken durch verworfene Entwürfe entstehen.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from app.db.database import get_setting

RANGE_FOR_TYPE = {"rechnung": "rechnung", "storno": "rechnung", "angebot": "angebot"}


def _prefix(con, rng: str) -> str:
    key = "number.offer_prefix" if rng == "angebot" else "number.invoice_prefix"
    return get_setting(con, key)


def _digits(con) -> int:
    try:
        return max(2, int(get_setting(con, "number.digits", "2")))
    except ValueError:
        return 2


def format_number(prefix: str, year: int, month: int, counter: int, digits: int = 2) -> str:
    return f"{prefix}{year:04d}-{month:02d}{counter:0{digits}d}"


def number_regex(prefix: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(prefix)}(\d{{4}})-(\d{{2}})(\d{{2,}})$")


def parse_number(con, number: str, doc_type: str) -> tuple[int, int, int] | None:
    """Zerlegt eine Nummer im Schema -> (Jahr, Monat, Zähler) oder None."""
    rng = RANGE_FOR_TYPE[doc_type]
    m = number_regex(_prefix(con, rng)).match(number.strip())
    if not m:
        return None
    year, month, counter = int(m[1]), int(m[2]), int(m[3])
    if not 1 <= month <= 12:
        return None
    return year, month, counter


def peek_next(con: sqlite3.Connection, doc_type: str, on: date | None = None) -> str:
    on = on or date.today()
    rng = RANGE_FOR_TYPE[doc_type]
    period = f"{on.year:04d}{on.month:02d}"
    row = con.execute("SELECT last_value FROM number_range WHERE doc_type=? AND period=?", (rng, period)).fetchone()
    last = row[0] if row else 0
    return format_number(_prefix(con, rng), on.year, on.month, last + 1, _digits(con))


def allocate(con: sqlite3.Connection, doc_type: str, on: date) -> str:
    """Muss innerhalb einer Transaktion aufgerufen werden."""
    assert con.in_transaction, "Nummernvergabe nur innerhalb einer Transaktion"
    rng = RANGE_FOR_TYPE[doc_type]
    period = f"{on.year:04d}{on.month:02d}"
    prefix = _prefix(con, rng)
    digits = _digits(con)
    con.execute("INSERT OR IGNORE INTO number_range(doc_type, period, last_value) VALUES (?,?,0)", (rng, period))
    last = con.execute("SELECT last_value FROM number_range WHERE doc_type=? AND period=?", (rng, period)).fetchone()[0]
    nxt = last + 1
    # Sicherheitsnetz: falls die Nummer (z.B. durch Import) schon existiert, weiterzählen
    while con.execute("SELECT 1 FROM document WHERE number=?", (format_number(prefix, on.year, on.month, nxt, digits),)).fetchone():
        nxt += 1
    con.execute("UPDATE number_range SET last_value=? WHERE doc_type=? AND period=?", (nxt, rng, period))
    return format_number(prefix, on.year, on.month, nxt, digits)


def reserve(con: sqlite3.Connection, doc_type: str, number: str) -> bool:
    """Reserviert eine extern vergebene Nummer (Import), damit sie nicht erneut vergeben wird."""
    parsed = parse_number(con, number, doc_type)
    if not parsed:
        return False
    year, month, counter = parsed
    rng = RANGE_FOR_TYPE[doc_type]
    period = f"{year:04d}{month:02d}"
    con.execute("INSERT OR IGNORE INTO number_range(doc_type, period, last_value) VALUES (?,?,0)", (rng, period))
    con.execute(
        "UPDATE number_range SET last_value=? WHERE doc_type=? AND period=? AND last_value < ?",
        (counter, rng, period, counter),
    )
    return True


def set_start(con: sqlite3.Connection, doc_type: str, year: int, month: int, last_value: int) -> None:
    """Startwert setzen (nur erhöhen erlaubt, Trigger verhindert Zurücksetzen)."""
    rng = RANGE_FOR_TYPE[doc_type]
    period = f"{year:04d}{month:02d}"
    con.execute("INSERT OR IGNORE INTO number_range(doc_type, period, last_value) VALUES (?,?,0)", (rng, period))
    con.execute("UPDATE number_range SET last_value=? WHERE doc_type=? AND period=?", (last_value, rng, period))
