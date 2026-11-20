"""Artikelimport aus CSV/XLSX (Spaltenzuordnung wie beim Kundenimport), Excel-Vorlage und Export im selben Format."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.db import repo
from app.db.database import get_setting, transaction
from app.money import cents_to_decimal, input_cents, input_decimal

# Spaltenüberschriften der Vorlage und des Exports (in dieser Reihenfolge)
HEADERS = {
    "title": "Bezeichnung",
    "description": "Beschreibung",
    "unit": "Einheit",
    "unit_price_cents": "Preis netto",
    "tax_rate": "USt %",
    "category": "Kategorie",
}

TARGET_FIELDS = {"": "(ignorieren)", **HEADERS}

_GUESS = {
    "bezeichnung": "title", "titel": "title", "artikel": "title", "leistung": "title", "artikelbezeichnung": "title",
    "name": "title", "kurztext": "title",
    "beschreibung": "description", "langtext": "description", "text": "description", "details": "description",
    "einheit": "unit", "me": "unit", "mengeneinheit": "unit",
    "preis": "unit_price_cents", "preis netto": "unit_price_cents", "nettopreis": "unit_price_cents",
    "einzelpreis": "unit_price_cents", "netto": "unit_price_cents", "vk": "unit_price_cents",
    "verkaufspreis": "unit_price_cents", "preis €": "unit_price_cents", "preis (netto)": "unit_price_cents",
    "ust %": "tax_rate", "ust": "tax_rate", "ust-satz": "tax_rate", "mwst": "tax_rate", "mwst %": "tax_rate",
    "steuersatz": "tax_rate", "umsatzsteuer": "tax_rate",
    "kategorie": "category", "gruppe": "category", "warengruppe": "category", "artikelgruppe": "category",
}

# Status in der Vorschau
NEW, EXISTS, DUPLICATE, INVALID = "neu", "vorhanden", "doppelt in Datei", "fehlerhaft"


def guess_mapping(headers: list[str]) -> list[str]:
    out, used = [], set()
    for h in headers:
        key = " ".join(h.strip().lower().rstrip(".:").replace("_", " ").split())
        field = _GUESS.get(key, _GUESS.get(key.replace(" ", ""), ""))
        if field in used:          # zweite Spalte mit gleicher Bedeutung nicht doppelt zuordnen
            field = ""
        used.add(field)
        out.append(field)
    return out


def title_key(title: str) -> str:
    """Vergleichsschlüssel für Dubletten: ohne Groß-/Kleinschreibung und überzählige Leerzeichen."""
    return " ".join((title or "").split()).casefold()


def _tax_rate(value: str) -> str:
    d = input_decimal(value)
    if 0 < d < 1 and "%" not in value:
        d *= 100                    # Excel-Zelle im Prozentformat: „19 %“ steht als 0,19 in der Datei
    if d < 0 or d > 100:
        raise ValueError(f"Ungültiger USt-Satz: {value!r}")
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def _unescape(value: str) -> str:
    """Der CSV-Export stellt Text mit Formelbeginn ein ' voran (Excel-Schutz) – beim Einlesen wieder entfernen."""
    from app.services.export import _FORMULA_START
    return value[1:] if value.startswith("'") and value[1:2] and value[1] in _FORMULA_START else value


@dataclass
class Row:
    """Eine Zeile der Datei mit Ergebnis der Prüfung."""
    line: int                      # Zeilennummer in der Datei (Überschrift = 1)
    data: dict
    status: str = NEW
    existing: dict | None = None   # vorhandener Artikel mit gleicher Bezeichnung
    message: str = ""
    # Felder, die beim Aktualisieren eines vorhandenen Artikels übernommen werden: nur Spalten aus der Datei
    # (leere Einheit/Preis/USt-Zellen lassen den Bestand stehen, leere Beschreibung/Kategorie leeren ihn)
    update_fields: frozenset = frozenset()


def rows_to_articles(rows: list[list[str]], mapping: list[str], default_tax: str = "19") -> list[Row]:
    """Zeilen in Artikel umwandeln. Leere Zeilen ohne Bezeichnung entfallen, unlesbare Preise/Steuersätze werden
    als fehlerhaft markiert (nicht stillschweigend 0)."""
    result = []
    mapped = set(mapping)
    for n, r in enumerate(rows, start=2):
        raw: dict[str, str] = {}
        for i, field in enumerate(mapping):
            if field and i < len(r) and r[i].strip():
                value = _unescape(r[i].strip())
                raw[field] = f"{raw[field]}\n{value}" if field in raw else value
        title = " ".join(raw.get("title", "").split())
        if not title:
            if any(raw.values()):
                result.append(Row(n, raw, INVALID, message="Bezeichnung fehlt"))
            continue
        data = {"title": title, "description": raw.get("description", ""), "unit": raw.get("unit") or "Stunden",
                "category": " ".join(raw.get("category", "").split()), "active": 1}
        row = Row(n, data, update_fields=frozenset(
            {f for f in ("description", "category") if f in mapped} |
            {f for f in ("unit", "unit_price_cents", "tax_rate") if raw.get(f)}))
        try:
            data["unit_price_cents"] = input_cents(raw.get("unit_price_cents") or "0")
        except ValueError:
            row.status, row.message = INVALID, f"Preis nicht lesbar: {raw.get('unit_price_cents')}"
        try:
            data["tax_rate"] = _tax_rate(raw["tax_rate"]) if raw.get("tax_rate") else default_tax
        except ValueError:
            row.status, row.message = INVALID, f"USt-Satz nicht lesbar: {raw.get('tax_rate')}"
        result.append(row)
    return result


def plan_import(con: sqlite3.Connection, rows: list[Row]) -> list[Row]:
    """Dubletten markieren: Bezeichnung schon vorhanden (auch inaktive Artikel) oder mehrfach in der Datei."""
    have = {}
    for a in repo.list_articles(con, include_inactive=True):
        have.setdefault(title_key(a["title"]), a)
    seen: set[str] = set()
    for row in rows:
        if row.status == INVALID:
            continue
        key = title_key(row.data["title"])
        if key in seen:
            row.status, row.message = DUPLICATE, "Bezeichnung kommt in der Datei mehrfach vor (erste Zeile zählt)"
            continue
        seen.add(key)
        if key in have:
            row.status, row.existing = EXISTS, have[key]
    return rows


def read_articles(con: sqlite3.Connection, path: Path | str, mapping: list[str] | None = None) -> list[Row]:
    """Datei lesen, zuordnen und prüfen (ohne Dialog, z.B. für Tests)."""
    from app.importers.csv_import import read_table
    headers, rows = read_table(path)
    mapping = guess_mapping(headers) if mapping is None else mapping
    default_tax = get_setting(con, "tax.default_rate", "19") or "19"
    return plan_import(con, rows_to_articles(rows, mapping, default_tax))


def apply_import(con: sqlite3.Connection, rows: list[Row], update_existing: bool) -> tuple[int, int, int]:
    """Neue Artikel anlegen, vorhandene je nach Wahl aktualisieren oder überspringen. Ergebnis: (neu, aktualisiert,
    übersprungen). Bei einem Fehler wird nichts übernommen."""
    n_new = n_upd = n_skip = 0
    with transaction(con):
        sort = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM article").fetchone()[0]
        for row in rows:
            if row.status == NEW:
                aid = repo.save_article(con, row.data)
                con.execute("UPDATE article SET sort=? WHERE id=?", (sort, aid))
                sort += 1
                n_new += 1
            elif row.status == EXISTS and update_existing:
                # nur Felder aus der Datei; Bezeichnung (Schreibweise), Aktiv-Kennzeichen und Reihenfolge bleiben
                data = {**row.existing, **{k: row.data[k] for k in row.update_fields}}
                repo.save_article(con, data)
                n_upd += 1
            else:
                n_skip += 1
    return n_new, n_upd, n_skip


# ---------------------------------------------------------------- Vorlage / Export
def _write(path: Path, rows: list[list]) -> Path:
    from app.services.export import write_csv, write_xlsx
    if path.suffix.lower() == ".csv":
        return write_csv(path, list(HEADERS.values()), rows)
    return write_xlsx(path, {"Artikel": (list(HEADERS.values()), rows)})


def _tax_cell(rate):
    """Ganze Steuersätze als Zahl (Excel markiert sie sonst als „als Text gespeicherte Zahl“), andere als Text –
    write_xlsx zeigt Kommazahlen als Eurobetrag an."""
    try:
        d = input_decimal(rate)
    except ValueError:
        return rate or ""
    return int(d) if d == d.to_integral_value() else str(rate).replace(".", ",")


def write_template(path: Path | str) -> Path:
    """Leere Vorlage mit den richtigen Überschriften und einer Beispielzeile."""
    from decimal import Decimal
    return _write(Path(path), [["Beispiel: Beratung", "nach Aufwand (Zeile löschen oder überschreiben)", "Stunden",
                                Decimal("85.00"), 19, "Beratung"]])


def export_articles(con: sqlite3.Connection, path: Path | str) -> Path:
    """Alle Artikel im Format der Vorlage – in Excel bearbeiten und wieder einlesen."""
    rows = [[a["title"], a["description"] or "", a["unit"] or "", cents_to_decimal(a["unit_price_cents"]),
             _tax_cell(a["tax_rate"]), a.get("category") or ""] for a in repo.list_articles(con, include_inactive=True)]
    return _write(Path(path), rows)
