"""Prüfung von Eingaben zu Firmendaten: PLZ, IBAN, BIC, E-Mail. Leere Felder gelten als gültig (Pflicht prüft der
Aufrufer). Jede Prüffunktion liefert eine Fehlermeldung oder None."""
from __future__ import annotations

import re

# IBAN-Längen der Länder, die hier realistisch vorkommen; andere Länder: 15–34 Zeichen
IBAN_LENGTH = {"DE": 22, "AT": 20, "CH": 21, "LI": 21, "LU": 20, "NL": 18, "BE": 16, "FR": 27, "IT": 27, "ES": 24,
               "DK": 18, "PL": 28, "CZ": 24, "SE": 24, "NO": 15, "FI": 18, "IE": 22, "PT": 25, "GB": 22}
ZIP_PATTERN = {"DE": (r"\d{5}", "5 Ziffern"), "AT": (r"\d{4}", "4 Ziffern"), "CH": (r"\d{4}", "4 Ziffern"),
               "LI": (r"\d{4}", "4 Ziffern"), "LU": (r"\d{4}", "4 Ziffern"), "NL": (r"\d{4} ?[A-Z]{2}", "z. B. 1234 AB")}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def format_iban(iban: str) -> str:
    """Einheitliche Schreibweise in Vierergruppen (GiroCode und E-Rechnung entfernen die Leerzeichen wieder)."""
    c = compact(iban)
    return " ".join(c[i:i + 4] for i in range(0, len(c), 4))


def iban_error(iban: str) -> str | None:
    c = compact(iban)
    if not c:
        return None
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", c):
        return "IBAN: beginnt mit Ländercode und Prüfziffer (z. B. DE12 …) und enthält nur Buchstaben und Ziffern."
    want = IBAN_LENGTH.get(c[:2])
    if want and len(c) != want:
        return f"IBAN: eine {c[:2]}-IBAN hat {want} Zeichen, eingegeben sind {len(c)}."
    if not want and not 15 <= len(c) <= 34:
        return f"IBAN: {len(c)} Zeichen sind zu {'wenig' if len(c) < 15 else 'viel'}."
    digits = "".join(str(int(ch, 36)) for ch in c[4:] + c[:4])
    if int(digits) % 97 != 1:
        return "IBAN: die Prüfziffer stimmt nicht – bitte auf Tippfehler prüfen."
    return None


def bic_error(bic: str) -> str | None:
    c = compact(bic)
    if not c:
        return None
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", c):
        return "BIC: 8 oder 11 Zeichen, z. B. COBADEFFXXX."
    return None


def zip_error(zip_code: str, country: str = "DE") -> str | None:
    z = (zip_code or "").strip().upper()
    if not z:
        return None
    pattern = ZIP_PATTERN.get((country or "DE").strip().upper())
    if pattern and not re.fullmatch(pattern[0], z):
        return f"PLZ: „{zip_code.strip()}“ – erwartet werden {pattern[1]}."
    return None


def email_error(email: str) -> str | None:
    e = (email or "").strip()
    if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s.]{2,}", e):
        return f"E-Mail: „{e}“ ist keine gültige Adresse."
    return None


def company_problems(values: dict[str, str]) -> list[str]:
    """Alle Fehler in Firmendaten (Schlüssel wie in den Einstellungen, z. B. company.iban)."""
    country = values.get("company.country") or "DE"
    checks = [("company.zip", lambda v: zip_error(v, country)), ("company.iban", iban_error),
              ("company.bic", bic_error), ("company.email", email_error)]
    return [msg for key, fn in checks if key in values and (msg := fn(values[key]))]


def normalize_company(values: dict[str, str]) -> dict[str, str]:
    """IBAN in Vierergruppen, BIC ohne Leerzeichen in Großbuchstaben."""
    out = dict(values)
    if out.get("company.iban", "").strip():
        out["company.iban"] = format_iban(out["company.iban"])
    if out.get("company.bic", "").strip():
        out["company.bic"] = compact(out["company.bic"])
    return out
