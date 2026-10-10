"""Ausgabenbuch: Betriebsausgaben erfassen, Belege archivieren, Jahreswerte für die Anlage EÜR.

Als Kleinunternehmer ist die Vorsteuer nicht abziehbar – Ausgaben zählen mit dem Bruttobetrag.
Für die 800-€-Grenze geringwertiger Wirtschaftsgüter gilt dagegen stets der Nettobetrag.
Hinweis: keine Steuerberatung – Zuordnung im Zweifel mit dem Steuerberater klären.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from app import config
from app.db.database import get_setting, row_to_dict, transaction
from app.money import to_decimal
from app.services import audit


@dataclass(frozen=True)
class Category:
    key: str
    label: str        # Anzeige in der Erfassung
    area: str         # Bereich in der Anlage EÜR (Bezeichnung wie im Formular, ohne Zeilennummer)
    hint: str = ""
    kind: str = ""    # km / afa / bewirtung / gwg


CATEGORIES: list[Category] = [
    Category("uebrige", "Software, Versicherung, Beiträge, Büro, Kontogebühren", "Übrige unbeschränkt abziehbare Betriebsausgaben",
             "z.B. Software-Wartung, Berufshaftpflicht, Kammer/Verband, Porto, Büromaterial, Website"),
    Category("telekom", "Telefon, Handy, Internet", "Aufwendungen für Telekommunikation", "nur betrieblicher Anteil"),
    Category("fortbildung", "Fortbildung, Seminare", "Fortbildungskosten", "z.B. Fachseminar, Pflichtfortbildung"),
    Category("fremdleistung", "Fremdleistungen, Subunternehmer", "Bezogene Fremdleistungen"),
    Category("raum", "Miete Büro / Raumkosten", "Raumkosten und sonstige Grundstücksaufwendungen", "für ein eigenes Büro außerhalb der Wohnung"),
    Category("homeoffice", "Homeoffice-Pauschale / Arbeitszimmer", "Arbeitszimmer / Homeoffice-Pauschale",
             "Pauschale 6 € je Tag, höchstens 1.260 € im Jahr"),
    Category("fahrt", "Fahrten mit Privat-Pkw (km)", "Fahrtkosten mit privatem Fahrzeug (Kilometerpauschale)",
             "Neue Fahrten bitte im Reiter „Fahrten“ erfassen", kind="km"),
    Category("kfz", "Kosten betriebliches Fahrzeug", "Kfz-Kosten (betriebliches Fahrzeug)", "Treibstoff, Versicherung, Reparatur"),
    Category("reise", "Reisekosten (Bahn, Hotel, Verpflegung)", "Reisekosten (Übernachtung, Reisenebenkosten, ÖPNV)"),
    Category("gwg", "Anschaffung bis 800 € netto", "Geringwertige Wirtschaftsgüter (Sofortabzug)",
             "z.B. Messgerät, Software-Kauf, Bürostuhl", kind="gwg"),
    Category("afa", "Anschaffung über 800 € netto", "Abschreibung (AfA) auf bewegliche Wirtschaftsgüter",
             "z.B. Laptop, Wärmebildkamera, Blower-Door – wird über die Nutzungsdauer verteilt", kind="afa"),
    Category("bewirtung", "Bewirtung von Geschäftspartnern", "Bewirtungsaufwendungen", "nur 70 % abziehbar", kind="bewirtung"),
    Category("geschenke", "Geschenke an Geschäftspartner", "Geschenke", "abziehbar bis 50 € je Empfänger und Jahr"),
]
CAT = {c.key: c for c in CATEGORIES}


def selectable_categories(current: str | None = None) -> list[Category]:
    """Kategorien zur Auswahl. Fahrten werden im Reiter „Fahrten“ erfasst; alte km-Ausgaben bleiben bearbeitbar."""
    return [c for c in CATEGORIES if c.kind != "km" or c.key == current]

# Nutzungsdauer nach AfA-Tabelle (Vorschläge)
USEFUL_LIFE_HINTS = {"Computer / Laptop / Software": 1, "Messgeräte": 5, "Büromöbel": 13, "Pkw": 6, "Kamera / Technik": 7}

GWG_LIMIT_NET_CENTS = 80000


# ---------------------------------------------------------------- Berechnungen
def km_amount_cents(con: sqlite3.Connection, km) -> int:
    rate = to_decimal(get_setting(con, "expense.km_rate", "0,30"))
    return int((to_decimal(km) * rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def net_cents(amount_cents: int, vat_rate) -> int:
    r = to_decimal(vat_rate or 0)
    return int((Decimal(amount_cents) * 100 / (100 + r)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def afa_schedule(amount_cents: int, purchase_date: str, years: int) -> dict[int, int]:
    """Lineare AfA, im Anschaffungsjahr monatsgenau (§ 7 Abs. 1 EStG). Rückgabe {Jahr: Cent}."""
    years = max(int(years or 1), 1)
    d = date.fromisoformat(purchase_date)
    total_months = years * 12
    monthly = Decimal(amount_cents) / total_months
    schedule: dict[int, int] = {}
    remaining = amount_cents
    months_left = total_months
    y = d.year
    first = 13 - d.month
    while remaining > 0 and months_left > 0:
        m = min(first if y == d.year else 12, months_left)
        part = int((monthly * m).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if months_left - m <= 0:
            part = remaining  # Rundungsrest im letzten Jahr
        part = min(part, remaining)
        schedule[y] = part
        remaining -= part
        months_left -= m
        y += 1
    return schedule


# ---------------------------------------------------------------- Lesen / Schreiben
def list_expenses(con: sqlite3.Connection, year: int | None = None, search: str = "", category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM expense WHERE 1=1"
    args: list = []
    if year:
        sql += " AND substr(exp_date,1,4)=?"
        args.append(f"{year:04d}")
    if category:
        sql += " AND category=?"
        args.append(category)
    if search:
        like = f"%{search}%"
        sql += " AND (supplier LIKE ? OR description LIKE ? OR note LIKE ?)"
        args += [like] * 3
    sql += " ORDER BY exp_date DESC, id DESC"
    return [dict(r) for r in con.execute(sql, args)]


def get_expense(con: sqlite3.Connection, eid: int) -> dict | None:
    return row_to_dict(con.execute("SELECT * FROM expense WHERE id=?", (eid,)).fetchone())


def suppliers(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT supplier FROM expense WHERE supplier<>'' GROUP BY supplier ORDER BY COUNT(*) DESC, supplier LIMIT 200")]


def last_category_for(con: sqlite3.Connection, supplier: str) -> str | None:
    row = con.execute("SELECT category FROM expense WHERE LOWER(supplier)=LOWER(?) ORDER BY exp_date DESC LIMIT 1",
                      (supplier,)).fetchone()
    return row[0] if row else None


def is_locked(con: sqlite3.Connection, year: int | str) -> bool:
    return bool(con.execute("SELECT 1 FROM expense_lock WHERE year=?", (str(year),)).fetchone())


def lock_year(con: sqlite3.Connection, year: int) -> None:
    with transaction(con):
        con.execute("INSERT OR IGNORE INTO expense_lock(year) VALUES (?)", (str(year),))
        audit.log(con, "lock_year", "expense", None, {"year": year})


def archive_receipt(src: Path | str, exp_date: str) -> tuple[str, str]:
    """Kopiert den Beleg unverändert ins Archiv (schreibgeschützt). Rückgabe (Pfad, SHA-256)."""
    src = Path(src)
    data = src.read_bytes()
    h = hashlib.sha256(data).hexdigest()
    target_dir = config.archive_dir() / "ausgaben" / (exp_date or "0000")[:4]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{exp_date}_{h[:8]}_{src.name}"
    if not target.exists():
        shutil.copy2(src, target)
        try:
            target.chmod(0o444)
        except OSError:
            pass
    return str(target), h


FIELDS = ["exp_date", "supplier", "description", "category", "amount_cents", "vat_rate", "km", "useful_life",
          "payment_method", "receipt_path", "receipt_sha256", "note", "source", "bank_ref"]


def validate(con: sqlite3.Connection, d: dict) -> list[str]:
    problems = []
    if not d.get("exp_date"):
        problems.append("Datum fehlt.")
    if d.get("category") not in CAT:
        problems.append("Bitte eine Kategorie wählen.")
    cat = CAT.get(d.get("category"))
    if cat and cat.kind == "km":
        if not d.get("km") or to_decimal(d["km"]) <= 0:
            problems.append("Bitte die gefahrenen Kilometer angeben.")
    elif not d.get("amount_cents"):
        problems.append("Betrag fehlt.")
    if cat and cat.kind == "afa" and not d.get("useful_life"):
        problems.append("Bitte die Nutzungsdauer in Jahren angeben.")
    if not (d.get("supplier") or d.get("description")):
        problems.append("Bitte Lieferant oder Beschreibung angeben.")
    return problems


def save_expense(con: sqlite3.Connection, data: dict) -> int:
    d = {k: data.get(k) for k in FIELDS if k in data}
    cat = CAT.get(d.get("category"))
    if cat and cat.kind == "km":
        d["amount_cents"] = km_amount_cents(con, d.get("km") or 0)
        d["vat_rate"] = "0"
    problems = validate(con, d)
    if problems:
        raise ValueError("\n".join(problems))
    with transaction(con):
        eid = data.get("id")
        if eid:
            old = get_expense(con, eid)
            sets = ", ".join(f"{k}=?" for k in d)
            con.execute(f"UPDATE expense SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", [*d.values(), eid])
            audit.log(con, "update", "expense", eid, {k: {"alt": old.get(k), "neu": v} for k, v in d.items() if old.get(k) != v})
        else:
            cols = ", ".join(d)
            cur = con.execute(f"INSERT INTO expense({cols}) VALUES ({', '.join('?' for _ in d)})", list(d.values()))
            eid = cur.lastrowid
            audit.log(con, "create", "expense", eid, d)
    return eid


def delete_expense(con: sqlite3.Connection, eid: int) -> None:
    with transaction(con):
        old = get_expense(con, eid)
        con.execute("DELETE FROM expense WHERE id=?", (eid,))
        audit.log(con, "delete", "expense", eid, old)


# ---------------------------------------------------------------- EÜR-Jahreswerte
@dataclass
class EuerArea:
    key: str
    area: str
    amount_cents: int
    count: int
    note: str = ""


def euer_values(con: sqlite3.Connection, year: int) -> dict:
    y = f"{year:04d}"
    income = con.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payment WHERE substr(pay_date,1,4)=?", (y,)).fetchone()[0]
    areas: list[EuerArea] = []
    for c in CATEGORIES:
        if c.kind == "afa":
            total, n = 0, 0
            for e in con.execute("SELECT * FROM expense WHERE category='afa'"):
                part = afa_schedule(e["amount_cents"], e["exp_date"], e["useful_life"] or 1).get(year, 0)
                if part:
                    total += part
                    n += 1
            if n:
                areas.append(EuerArea(c.key, c.area, total, n, "Jahresanteil der Abschreibungen"))
            continue
        row = con.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*), COALESCE(SUM(CAST(REPLACE(km, ',', '.') AS REAL)),0) "
                          "FROM expense WHERE category=? AND substr(exp_date,1,4)=?", (c.key, y)).fetchone()
        total, n, km = row
        if c.kind == "km":
            from app.services import trips
            t_total, t_n, t_km = trips.year_totals(con, year)
            total, n, km = total + t_total, n + t_n, km + t_km
        if not n:
            continue
        note = ""
        if c.kind == "bewirtung":
            deductible = int((Decimal(total) * Decimal("0.7")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            note = f"davon abziehbar 70 %: {deductible / 100:.2f} €, nicht abziehbar 30 %: {(total - deductible) / 100:.2f} €".replace(".", ",")
            areas.append(EuerArea(c.key, c.area, total, n, note))
            continue
        if c.kind == "km":
            note = f"{km:,.0f}".replace(",", ".") + " km, Nachweis im Reiter „Fahrten“"
        areas.append(EuerArea(c.key, c.area, total, n, note))
    deductible_total = 0
    for a in areas:
        if a.key == "bewirtung":
            deductible_total += int((Decimal(a.amount_cents) * Decimal("0.7")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        else:
            deductible_total += a.amount_cents
    missing_receipts = con.execute(
        "SELECT COUNT(*) FROM expense WHERE substr(exp_date,1,4)=? AND (receipt_path IS NULL OR receipt_path='') "
        "AND category NOT IN ('fahrt','homeoffice')", (y,)).fetchone()[0]
    return {"year": year, "income_cents": income, "areas": areas, "expense_cents": deductible_total,
            "profit_cents": income - deductible_total, "missing_receipts": missing_receipts,
            "locked": is_locked(con, year)}
