"""Geld- und Zahlenhelfer (Decimal, deutsche Formatierung)."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def to_decimal(value) -> Decimal:
    """Wandelt '1.234,50', '1234.5', 12, Decimal ... in Decimal."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip().replace("€", "").replace("EUR", "").replace(" ", "").replace(" ", "")
    if not s:
        return Decimal("0")
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"Keine gültige Zahl: {value!r}") from e
    return -d if neg else d


def to_cents(value) -> int:
    return int((to_decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


_THOUSANDS_ONLY = re.compile(r"^-?[1-9]\d{0,2}(\.\d{3})+$")


def input_decimal(value) -> Decimal:
    """Wie to_decimal, aber für Eingaben im Programm (deutsche Schreibweise): '1.500' ist 1500, nicht 1,5.
    Nicht für gespeicherte Werte verwenden – die liegen mit Dezimalpunkt vor (z.B. Menge '2.250' = 2,25)."""
    s = str(value or "").strip().replace("€", "").replace("EUR", "").replace("%", "").replace(" ", "").replace(" ", "")
    if "," not in s and _THOUSANDS_ONLY.match(s):
        s = s.replace(".", "")
    return to_decimal(s)


def input_cents(value) -> int:
    return int((input_decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(int(cents or 0)) / 100).quantize(CENT)


def line_total_cents(quantity, unit_price_cents: int) -> int:
    q = to_decimal(quantity)
    return int((q * Decimal(int(unit_price_cents))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt_eur(cents: int | None, symbol: bool = True) -> str:
    d = cents_to_decimal(cents or 0)
    s = f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €" if symbol else s


def fmt_qty(value) -> str:
    d = to_decimal(value)
    if d == d.to_integral_value():
        s = f"{d:,.1f}"
    else:
        s = f"{d.normalize():,f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_date(value) -> str:
    """ISO 'YYYY-MM-DD' -> 'TT.MM.JJJJ'."""
    if not value:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


_DATE_PATTERNS = [
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "dmy"),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), "dmy2"),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), "ymd"),
]


def parse_date(value) -> str | None:
    """Liefert ISO-Datum oder None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    for rx, kind in _DATE_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        try:
            if kind == "ymd":
                return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
            year = int(m[3]) if kind == "dmy" else 2000 + int(m[3])
            return date(year, int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return None
    return None
