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
    # Minus vorn, hinten (manche Bank-Exporte: „12,50-“) oder Klammern
    neg = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
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


_ONES = ["null", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwölf",
         "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_TENS = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]


def _words_below_1000(n: int) -> str:
    out = ""
    if n >= 100:
        out += _ONES[n // 100] + "hundert"
        n %= 100
    if n >= 20:
        out += ("" if n % 10 == 0 else _ONES[n % 10] + "und") + _TENS[n // 10]
    elif n:
        out += _ONES[n]
    return out


def int_in_worten(n: int, before_noun: bool = False) -> str:
    """Ganze Zahl (0 … 999.999.999) in deutschen Worten, z.B. 1201 -> „eintausendzweihunderteins“.
    Vor einem Hauptwort (before_noun) heißt es „ein Euro“ statt „eins Euro“."""
    if n < 0 or n >= 1_000_000_000:
        raise ValueError("Zahl außerhalb des Bereichs für die Wortdarstellung")
    if n == 0:
        return "null"
    millions, rest = divmod(n, 1_000_000)
    thousands, ones = divmod(rest, 1000)
    parts = []
    if millions:
        parts.append("eine Million " if millions == 1 else _words_below_1000(millions) + " Millionen ")
    if thousands:
        parts.append(_words_below_1000(thousands) + "tausend")
    if ones:
        w = _words_below_1000(ones)
        parts.append(w + "s" if w.endswith("ein") and not before_noun else w)   # „eins“ am Ende, „einhundert“ in der Mitte
    return "".join(parts).strip()


def betrag_in_worten(cents: int) -> str:
    """Betrag für Quittungen, z.B. 12345 -> „einhundertdreiundzwanzig Euro fünfundvierzig Cent“."""
    if cents < 0:
        raise ValueError("Negativer Betrag")
    euro, ct = divmod(int(cents), 100)
    txt = f"{int_in_worten(euro, True)} Euro"
    if ct:
        txt += f" {int_in_worten(ct, True)} Cent"
    return txt
