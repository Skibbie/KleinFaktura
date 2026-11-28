"""Nummernkreise.

Aufbau je Kreis (Einstellungen → Nummernkreise, „Baukasten“):
    Präfix + Jahr (4-/2-stellig/ohne) + Trennzeichen + Monat (optional) + Zähler (Stellen einstellbar)
Standard Rechnung (und Storno): JJJJ-MMNN, z.B. 2026-1001; Angebot: ANJJJJ-MMNN, z.B. AN2026-1001.
Der Zähler beginnt je Monat neu (mit Monat), je Jahr (nur Jahr) oder läuft durch (ohne Jahr).
Die Vergabe erfolgt erst beim Festschreiben innerhalb der Festschreibe-Transaktion,
damit keine Lücken durch verworfene Entwürfe entstehen.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date

from app.db.database import get_setting

RANGE_FOR_TYPE = {"rechnung": "rechnung", "storno": "rechnung", "angebot": "angebot"}
_KEY = {"rechnung": "invoice", "angebot": "offer"}
SEPARATORS = {"-": "Bindestrich (-)", "_": "Unterstrich (_)", ".": "Punkt (.)", "": "keins"}
PREFIX_RE = re.compile(r"[A-Za-z0-9ÄÖÜäöüß_.-]{0,10}")   # nur mit fullmatch verwenden
MIN_DIGITS, MAX_DIGITS = 1, 8
CONTINUOUS = "*"   # Periode für Kreise ohne Jahr: Zähler läuft immer weiter


@dataclass(frozen=True)
class NumberFormat:
    prefix: str = ""
    year: int = 4          # 4, 2 oder 0 (ohne Jahr)
    month: bool = True     # nur mit Jahr
    sep: str = "-"
    digits: int = 2

    def __post_init__(self):
        if self.year not in (0, 2, 4):
            raise ValueError("Jahr muss 4-stellig, 2-stellig oder ohne sein.")
        if self.month and not self.year:
            raise ValueError("Ein Monat ist nur zusammen mit dem Jahr möglich.")
        if self.sep not in SEPARATORS:
            raise ValueError(f"Unzulässiges Trennzeichen „{self.sep}“.")
        if not isinstance(self.prefix, str) or not PREFIX_RE.fullmatch(self.prefix):
            raise ValueError("Präfix: höchstens 10 Zeichen, nur Buchstaben, Ziffern, - _ und Punkt "
                             "(die Nummer wird auch Dateiname).")
        if not MIN_DIGITS <= self.digits <= MAX_DIGITS:
            raise ValueError(f"Stellen des Zählers: {MIN_DIGITS} bis {MAX_DIGITS}.")

    @property
    def reset(self) -> str:
        return "monat" if self.month else "jahr" if self.year else "nie"

    def period(self, on: date) -> str:
        if self.month:
            return f"{on.year:04d}{on.month:02d}"
        return f"{on.year:04d}" if self.year else CONTINUOUS

    def render(self, on: date, counter: int) -> str:
        y = f"{on.year:04d}" if self.year == 4 else f"{on.year % 100:02d}" if self.year == 2 else ""
        m = f"{on.month:02d}" if self.month else ""
        return f"{self.prefix}{y}{self.sep if y else ''}{m}{counter:0{self.digits}d}"

    def pattern(self) -> str:
        """Schema in Worten, z.B. „RE-JJJJ-NNNN“."""
        y = "J" * self.year
        return f"{self.prefix}{y}{self.sep if y else ''}{'MM' if self.month else ''}{'N' * self.digits}"

    def regex(self) -> re.Pattern:
        y = rf"(\d{{{self.year}}})" if self.year else "()"
        sep = re.escape(self.sep) if self.year else ""
        m = r"(\d{2})" if self.month else "()"
        return re.compile(rf"^{re.escape(self.prefix)}{y}{sep}{m}(\d{{{self.digits},}})$")

    def describe_reset(self) -> str:
        return {"monat": "Zähler beginnt jeden Monat neu", "jahr": "Zähler beginnt jedes Jahr neu",
                "nie": "Zähler läuft fortlaufend weiter (kein Neustart)"}[self.reset]


def setting_keys(rng: str) -> dict[str, str]:
    k = _KEY[rng]
    return {"prefix": f"number.{k}_prefix", "year": f"number.{k}_year", "month": f"number.{k}_month",
            "sep": f"number.{k}_sep", "digits": f"number.{k}_digits"}


def _stored_format(con: sqlite3.Connection, rng: str) -> NumberFormat:
    """Gespeichertes Schema; ValueError, wenn die Einstellungen kein gültiges Schema ergeben."""
    k = setting_keys(rng)
    # Stellen hießen früher gemeinsam „number.digits“; der Wert gilt weiter, bis je Kreis etwas eingestellt ist
    digits = get_setting(con, k["digits"]) or get_setting(con, "number.digits", "2")
    return NumberFormat(prefix=get_setting(con, k["prefix"]), year=int(get_setting(con, k["year"], "4")),
                        month=get_setting(con, k["month"], "1") == "1", sep=get_setting(con, k["sep"], "-"),
                        digits=int(digits))


def format_error(con: sqlite3.Connection, doc_type: str) -> str | None:
    """Fehlerbeschreibung, wenn das gespeicherte Schema ungültig ist (dann gilt das Standardschema), sonst None."""
    try:
        _stored_format(con, RANGE_FOR_TYPE[doc_type])
    except ValueError as e:
        return str(e)
    return None


def get_format(con: sqlite3.Connection, doc_type: str) -> NumberFormat:
    rng = RANGE_FOR_TYPE[doc_type]
    try:
        return _stored_format(con, rng)
    except ValueError:
        # beschädigte Einstellung: lieber das bewährte Standardschema als gar keine Nummer;
        # die Einstellungsseite zeigt den Fehler an (format_error)
        prefix = get_setting(con, setting_keys(rng)["prefix"])
        return NumberFormat(prefix=prefix if PREFIX_RE.fullmatch(prefix) else "")


def format_values(fmt: NumberFormat) -> dict[str, str]:
    """Einstellungswerte (für set_setting) zu einem Format."""
    return {"prefix": fmt.prefix, "year": str(fmt.year), "month": "1" if fmt.month else "0", "sep": fmt.sep,
            "digits": str(fmt.digits)}


def _parse(fmt: NumberFormat, number: str) -> tuple[str, int] | None:
    m = fmt.regex().match(number.strip())
    if not m:
        return None
    if not fmt.year:
        return CONTINUOUS, int(m[3])
    year = int(m[1]) + (2000 if fmt.year == 2 else 0)
    month = int(m[2]) if fmt.month else 1
    if not 1 <= month <= 12:
        return None
    return fmt.period(date(year, month, 1)), int(m[3])


def parse_number(con, number: str, doc_type: str) -> tuple[str, int] | None:
    """Zerlegt eine Nummer im eingestellten Schema -> (Periode, Zähler) oder None."""
    return _parse(get_format(con, doc_type), number)


def _last(con, rng: str, period: str) -> int:
    row = con.execute("SELECT last_value FROM number_range WHERE doc_type=? AND period=?", (rng, period)).fetchone()
    return row[0] if row else 0


def _highest_existing(con, rng: str, fmt: NumberFormat, period: str) -> int:
    """Höchster Zähler vorhandener Belege des Kreises, die im Schema `fmt` in den Zeitraum fallen.
    Nach einem Schemawechsel (z.B. von „je Monat“ auf „je Jahr“) setzt der neue Zähler so oberhalb der bisherigen
    Nummern fort, statt wieder bei 1 zu beginnen und die Folge durcheinanderzubringen."""
    types = [t for t, r in RANGE_FOR_TYPE.items() if r == rng]
    rows = con.execute(f"SELECT number FROM document WHERE number IS NOT NULL AND doc_type IN "
                       f"({','.join('?' * len(types))})", types)
    best = 0
    for (number,) in rows:
        parsed = _parse(fmt, number)
        if parsed and parsed[0] == period:
            best = max(best, parsed[1])
    return best


def _next_free(con, rng: str, fmt: NumberFormat, on: date) -> int:
    """Nächster freier Zähler – gemeinsame Rechnung für Vorschau und Vergabe, damit beide übereinstimmen."""
    period = fmt.period(on)
    nxt = max(_last(con, rng, period), _highest_existing(con, rng, fmt, period)) + 1
    # Sicherheitsnetz: falls die Nummer (z.B. durch Import oder ein früheres Schema) schon existiert, weiterzählen
    while con.execute("SELECT 1 FROM document WHERE number=?", (fmt.render(on, nxt),)).fetchone():
        nxt += 1
    return nxt


def peek_next(con: sqlite3.Connection, doc_type: str, on: date | None = None, fmt: NumberFormat | None = None) -> str:
    """Nächste Nummer, wie allocate() sie vergeben würde; `fmt` für die Vorschau eines noch nicht gespeicherten Schemas."""
    on = on or date.today()
    fmt = fmt or get_format(con, doc_type)
    return fmt.render(on, _next_free(con, RANGE_FOR_TYPE[doc_type], fmt, on))


def allocate(con: sqlite3.Connection, doc_type: str, on: date) -> str:
    """Muss innerhalb einer Transaktion aufgerufen werden."""
    assert con.in_transaction, "Nummernvergabe nur innerhalb einer Transaktion"
    rng = RANGE_FOR_TYPE[doc_type]
    fmt = get_format(con, doc_type)
    period = fmt.period(on)
    con.execute("INSERT OR IGNORE INTO number_range(doc_type, period, last_value) VALUES (?,?,0)", (rng, period))
    nxt = _next_free(con, rng, fmt, on)
    con.execute("UPDATE number_range SET last_value=? WHERE doc_type=? AND period=?", (nxt, rng, period))
    return fmt.render(on, nxt)


def reserve(con: sqlite3.Connection, doc_type: str, number: str) -> bool:
    """Reserviert eine extern vergebene Nummer (Import), damit sie nicht erneut vergeben wird."""
    parsed = parse_number(con, number, doc_type)
    if not parsed:
        return False
    period, counter = parsed
    rng = RANGE_FOR_TYPE[doc_type]
    con.execute("INSERT OR IGNORE INTO number_range(doc_type, period, last_value) VALUES (?,?,0)", (rng, period))
    con.execute(
        "UPDATE number_range SET last_value=? WHERE doc_type=? AND period=? AND last_value < ?",
        (counter, rng, period, counter),
    )
    return True


def last_value(con: sqlite3.Connection, doc_type: str, on: date) -> int:
    """Zuletzt vergebener Zähler im Zeitraum von `on` (nach eingestelltem Schema, inkl. vorhandener Belege)."""
    rng, fmt = RANGE_FOR_TYPE[doc_type], get_format(con, doc_type)
    period = fmt.period(on)
    return max(_last(con, rng, period), _highest_existing(con, rng, fmt, period))


def set_start(con: sqlite3.Connection, doc_type: str, on: date, last_value: int) -> None:
    """Startwert für den Zeitraum von `on` setzen (nur erhöhen erlaubt, Trigger verhindert Zurücksetzen)."""
    rng = RANGE_FOR_TYPE[doc_type]
    period = get_format(con, doc_type).period(on)
    con.execute("INSERT OR IGNORE INTO number_range(doc_type, period, last_value) VALUES (?,?,0)", (rng, period))
    con.execute("UPDATE number_range SET last_value=? WHERE doc_type=? AND period=?", (last_value, rng, period))
