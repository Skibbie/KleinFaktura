"""Ausgaben aus dem CSV-Kontoauszug der Bank übernehmen (reines Python).

Unterstützt die üblichen deutschen Exportformate (Sparkasse, Volksbank, DKB, ING, Postbank …):
Spalten werden über die Überschriften erkannt und können im Dialog angepasst werden.
Nur Abbuchungen (negative Beträge) werden als Ausgaben vorgeschlagen.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.importers.csv_import import read_table
from app.money import parse_date, to_cents
from app.services import expenses

TARGETS = {"": "(ignorieren)", "date": "Buchungsdatum", "amount": "Betrag", "name": "Empfänger / Auftraggeber",
           "purpose": "Verwendungszweck", "debit": "Soll (Abbuchung)", "credit": "Haben (Gutschrift)"}

_GUESS = [
    ("date", r"^(buchungs(tag|datum)|datum|buchung|valuta.*|wertstellung)$"),
    ("amount", r"^(betrag.*|umsatz.*|amount)$"),
    ("debit", r"^(soll|lastschrift|ausgang)$"),
    ("credit", r"^(haben|gutschrift|eingang)$"),
    ("name", r"^(beg(ü|ue)nstigter.*|empf(ä|ae)nger.*|zahlungsempf(ä|ae)nger.*|auftraggeber.*|name.*|"
             r"zahlungspflichtige.*|gegenkonto.*name|partner.*)$"),
    ("purpose", r"^(verwendungszweck.*|buchungstext|vorgang.*|beschreibung|zweck)$"),
]

# einfache Zuordnungsregeln (Lieferant/Zweck -> Kategorie)
RULES = [
    ("telekom", r"telekom|vodafone|o2 |telefonica|1&1|congstar|freenet|unitymedia|pyur|mobilfunk"),
    ("kfz", r"aral|shell|esso|jet |total|tankstelle|star tank|avia|kfz-steuer|huk|kfz"),
    ("reise", r"deutsche bahn|db vertrieb|db fernverkehr|flixbus|hotel|booking\.com|hrs"),
    ("fortbildung", r"seminar|akademie|fortbildung|schulung|weiterbildung|geg-|dena|bafa.*schulung"),
    ("uebrige", r"microsoft|adobe|google|ionos|strato|versicherung|haftpflicht|"
                r"kammer|verband|gebühr|kontoführung|porto|deutsche post|dhl|amazon|büro"),
]

SKIP_PATTERNS = r"miete privat|lohn|gehalt|finanzamt|einkommensteuer|krankenkasse|rundfunk|privat"


@dataclass
class BankRow:
    date: str
    amount_cents: int          # positiv = Ausgabe
    name: str
    purpose: str
    fingerprint: str
    category: str = "uebrige"
    duplicate: bool = False
    likely_private: bool = False


def guess_mapping(headers: list[str]) -> list[str]:
    out = []
    used = set()
    for h in headers:
        key = re.sub(r"\s+", " ", h.strip().lower().replace('"', ""))
        hit = ""
        for target, rx in _GUESS:
            if target not in used and re.match(rx, key):
                hit = target
                used.add(target)
                break
        out.append(hit)
    return out


def guess_category(name: str, purpose: str, con: sqlite3.Connection | None = None) -> str:
    if con is not None and name:
        prev = expenses.last_category_for(con, name)
        if prev:
            return prev
    text = f"{name} {purpose}".lower()
    for cat, rx in RULES:
        if re.search(rx, text):
            return cat
    return "uebrige"


def parse(con: sqlite3.Connection, path: Path | str, mapping: list[str] | None = None) -> tuple[list[str], list[str], list[BankRow]]:
    headers, rows = read_table(Path(path))
    # Bank-CSVs haben oft Vorspann-Zeilen: Kopfzeile = erste Zeile mit erkanntem Datum- und Betragsfeld
    all_rows = [headers] + rows
    for i, r in enumerate(all_rows[:30]):
        m = guess_mapping(r)
        if "date" in m and ("amount" in m or "debit" in m):
            headers, rows = r, all_rows[i + 1:]
            break
    mapping = mapping or guess_mapping(headers)
    out: list[BankRow] = []
    for r in rows:
        def col(name):
            if name not in mapping:
                return ""
            i = mapping.index(name)
            return r[i].strip() if i < len(r) else ""
        d = parse_date(col("date"))
        if not d:
            continue
        try:
            if "amount" in mapping:
                amount = to_cents(col("amount") or "0")
            else:
                amount = to_cents(col("credit") or "0") - abs(to_cents(col("debit") or "0"))
        except ValueError:
            continue
        if amount >= 0:
            continue  # nur Abbuchungen
        name, purpose = col("name"), col("purpose")
        fp = hashlib.sha256(f"{d}|{amount}|{name}|{purpose}".encode("utf-8")).hexdigest()[:32]
        row = BankRow(d, -amount, name, purpose, fp, guess_category(name, purpose, con))
        row.duplicate = bool(con.execute("SELECT 1 FROM expense WHERE bank_ref=?", (fp,)).fetchone()) or bool(
            con.execute("SELECT 1 FROM expense WHERE exp_date=? AND amount_cents=? AND LOWER(supplier)=LOWER(?)",
                        (d, -amount, name)).fetchone())
        row.likely_private = bool(re.search(SKIP_PATTERNS, f"{name} {purpose}".lower()))
        out.append(row)
    return headers, mapping, out


def import_rows(con: sqlite3.Connection, rows: list[BankRow]) -> int:
    n = 0
    for r in rows:
        if con.execute("SELECT 1 FROM expense WHERE bank_ref=?", (r.fingerprint,)).fetchone():
            continue
        expenses.save_expense(con, {"exp_date": r.date, "supplier": r.name[:120], "description": r.purpose[:300],
                                    "category": r.category, "amount_cents": r.amount_cents, "vat_rate": "19",
                                    "useful_life": 3 if r.category == "afa" else None,
                                    "payment_method": "Überweisung/Lastschrift", "source": "bank",
                                    "bank_ref": r.fingerprint, "note": "aus Kontoauszug übernommen"})
        n += 1
    return n
