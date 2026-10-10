"""Fahrten mit dem Privat-Pkw: Nachweis je Fahrt (Datum, Ziel, Anlass, km) und Kilometerpauschale.

Die Summe fließt in den EÜR-Bereich „Fahrtkosten mit privatem Fahrzeug“ (siehe expenses.euer_values).
Der Satz je km wird bei der Erfassung mitgespeichert, damit spätere Änderungen der Einstellung alte Fahrten
nicht verändern. Hinweis: keine Steuerberatung.
"""
from __future__ import annotations

import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote_plus

from app.db import repo
from app.db.database import get_setting, row_to_dict, transaction
from app.money import fmt_date, to_decimal
from app.services import audit

FIELDS = ["trip_date", "customer_id", "project_id", "document_id", "destination", "purpose", "km_one_way",
          "round_trip", "km", "rate", "amount_cents", "note"]

PURPOSE_CATEGORY = "fahrt_anlass"  # Anlässe werden als Textbausteine gepflegt


def _num(v) -> str:
    """Dezimalzahl als Text mit Punkt (Speicherformat wie expense.km)."""
    d = to_decimal(v or 0).normalize()
    return format(d, "f")


def fmt_km(v) -> str:
    """Anzeige mit Komma, z.B. 123,5."""
    return _num(v).replace(".", ",")


def fmt_rate(v) -> str:
    return f"{to_decimal(v):.2f}".replace(".", ",")


def total_km(km_one_way, round_trip: bool) -> Decimal:
    return to_decimal(km_one_way or 0) * (2 if round_trip else 1)


def amount_cents(km, rate) -> int:
    return int((to_decimal(km) * to_decimal(rate) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def current_rate(con: sqlite3.Connection) -> str:
    return _num(get_setting(con, "expense.km_rate", "0,30"))


# ---------------------------------------------------------------- Lesen
def list_trips(con: sqlite3.Connection, year: int | None = None, search: str = "") -> list[dict]:
    sql = """SELECT t.*, c.first_name, c.last_name, c.company, c.title, d.number AS doc_number
             FROM trip t LEFT JOIN customer c ON c.id=t.customer_id LEFT JOIN document d ON d.id=t.document_id
             WHERE 1=1"""
    args: list = []
    if year:
        sql += " AND substr(t.trip_date,1,4)=?"
        args.append(f"{year:04d}")
    if search:
        like = f"%{search}%"
        sql += (" AND (t.destination LIKE ? OR t.purpose LIKE ? OR t.note LIKE ? OR c.last_name LIKE ? "
                "OR c.first_name LIKE ? OR c.company LIKE ? OR d.number LIKE ?)")
        args += [like] * 7
    sql += " ORDER BY t.trip_date DESC, t.id DESC"
    out = []
    for r in con.execute(sql, args):
        t = dict(r)
        t["customer_name"] = repo.customer_display_name(t) if t.get("customer_id") else ""
        out.append(t)
    return out


def get_trip(con: sqlite3.Connection, tid: int) -> dict | None:
    return row_to_dict(con.execute("SELECT * FROM trip WHERE id=?", (tid,)).fetchone())


def default_destination(con: sqlite3.Connection, customer_id: int | None, project_id: int | None) -> str:
    if project_id:
        addr = repo.project_address(repo.get_project(con, project_id))
        if addr:
            return addr
    c = repo.get_customer(con, customer_id) if customer_id else None
    if not c:
        return ""
    zc = " ".join(x for x in [c.get("zip"), c.get("city")] if x)
    return ", ".join(x for x in [c.get("street"), zc] if x)


def own_address(con: sqlite3.Connection) -> str:
    """Eigene Anschrift aus den Einstellungen (Startpunkt der Fahrten)."""
    zc = " ".join(x for x in [get_setting(con, "company.zip", ""), get_setting(con, "company.city", "")] if x)
    return ", ".join(x for x in [get_setting(con, "company.street", ""), zc] if x)


def route_url(start: str, destination: str) -> str:
    """Routenplaner-Link (Google Maps, Auto) – wird nur auf Klick im Browser geöffnet, das Programm selbst
    überträgt keine Adressen."""
    return ("https://www.google.com/maps/dir/?api=1&travelmode=driving"
            f"&origin={quote_plus(start)}&destination={quote_plus(destination)}")


def remembered_km(con: sqlite3.Connection, destination: str = "", customer_id: int | None = None,
                  project_id: int | None = None) -> tuple[str, bool] | None:
    """Einfache Strecke und Hin/Rück der letzten Fahrt zum selben Ziel (sonst zum selben Kunden/Projekt)."""
    queries = []
    if destination:
        queries.append(("LOWER(TRIM(destination))=LOWER(TRIM(?))", destination))
    if project_id:
        queries.append(("project_id=?", project_id))
    if customer_id:
        queries.append(("customer_id=?", customer_id))
    for cond, arg in queries:
        r = con.execute(f"SELECT km_one_way, round_trip, km FROM trip WHERE {cond} ORDER BY trip_date DESC, id DESC LIMIT 1",
                        (arg,)).fetchone()
        if r:
            return (r["km_one_way"] or r["km"], bool(r["round_trip"]) if r["km_one_way"] else False)
    return None


def documents_for(con: sqlite3.Connection, customer_id: int) -> list[dict]:
    """Festgeschriebene Angebote/Rechnungen des Kunden zur Verknüpfung."""
    return [dict(r) for r in con.execute(
        "SELECT id, doc_type, number, doc_date, subject, project_id FROM document WHERE customer_id=? "
        "AND status='festgeschrieben' AND doc_type IN ('angebot','rechnung') ORDER BY doc_date DESC, id DESC",
        (customer_id,))]


# ---------------------------------------------------------------- Schreiben
def validate(d: dict) -> list[str]:
    problems = []
    if not d.get("trip_date"):
        problems.append("Datum fehlt.")
    if not (d.get("destination") or "").strip():
        problems.append("Bitte das Ziel angeben.")
    if not (d.get("purpose") or "").strip():
        problems.append("Bitte den Anlass der Fahrt angeben.")
    try:
        if to_decimal(d.get("km") or 0) <= 0:
            problems.append("Bitte die Kilometer angeben.")
    except ValueError:
        problems.append("Ungültige Kilometerangabe.")
    return problems


def save_trip(con: sqlite3.Connection, data: dict) -> int:
    d = {k: data.get(k) for k in FIELDS if k in data}
    d["round_trip"] = 1 if d.get("round_trip", 1) else 0
    if d.get("km_one_way") not in (None, ""):
        d["km_one_way"] = _num(d["km_one_way"])
        d["km"] = _num(total_km(d["km_one_way"], d["round_trip"]))
    elif d.get("km") not in (None, ""):
        d["km"] = _num(d["km"])
    problems = validate(d)
    if problems:
        raise ValueError("\n".join(problems))
    tid = data.get("id")
    old = get_trip(con, tid) if tid else None
    # Satz der Erfassung beibehalten; neue Fahrten bekommen den aktuellen Satz aus den Einstellungen
    d["rate"] = _num(d.get("rate") or (old or {}).get("rate") or current_rate(con))
    d["amount_cents"] = amount_cents(d["km"], d["rate"])
    d["destination"] = d["destination"].strip()
    d["purpose"] = d["purpose"].strip()
    with transaction(con):
        if tid:
            sets = ", ".join(f"{k}=?" for k in d)
            con.execute(f"UPDATE trip SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", [*d.values(), tid])
            audit.log(con, "update", "trip", tid, {k: {"alt": old.get(k), "neu": v} for k, v in d.items() if old.get(k) != v})
        else:
            cols = ", ".join(d)
            cur = con.execute(f"INSERT INTO trip({cols}) VALUES ({', '.join('?' for _ in d)})", list(d.values()))
            tid = cur.lastrowid
            audit.log(con, "create", "trip", tid, d)
    return tid


def delete_trip(con: sqlite3.Connection, tid: int) -> None:
    with transaction(con):
        old = get_trip(con, tid)
        con.execute("DELETE FROM trip WHERE id=?", (tid,))
        audit.log(con, "delete", "trip", tid, old)


# ---------------------------------------------------------------- Jahreswerte
def year_totals(con: sqlite3.Connection, year: int) -> tuple[int, int, float]:
    """(Betrag in Cent, Anzahl Fahrten, km) eines Jahres."""
    r = con.execute("SELECT COALESCE(SUM(amount_cents),0), COUNT(*), COALESCE(SUM(CAST(km AS REAL)),0) FROM trip "
                    "WHERE substr(trip_date,1,4)=?", (f"{year:04d}",)).fetchone()
    return r[0], r[1], r[2]


def invoices_without_trip(con: sqlite3.Connection, year: int) -> list[dict]:
    """Rechnungen des Jahres, zu denen keine Fahrt verknüpft ist (Hinweis auf vergessene Fahrten)."""
    return [dict(r) for r in con.execute(
        """SELECT d.id, d.number, d.doc_date, d.customer_id FROM document d
           WHERE d.doc_type='rechnung' AND d.status='festgeschrieben' AND substr(d.doc_date,1,4)=?
             AND NOT EXISTS (SELECT 1 FROM document s WHERE s.ref_document_id=d.id AND s.doc_type='storno'
                             AND s.status='festgeschrieben')
             AND NOT EXISTS (SELECT 1 FROM trip t WHERE t.document_id IN (d.id, d.ref_document_id))
           ORDER BY d.doc_date""", (f"{year:04d}",))]


EXPORT_HEADER = ["Datum", "Kunde", "Ziel", "Anlass", "Beleg", "km", "€ je km", "Betrag", "Notiz"]


def rows_for_export(con: sqlite3.Connection, year: int) -> list[list]:
    out = []
    for t in reversed(list_trips(con, year)):
        out.append([fmt_date(t["trip_date"]), t["customer_name"], t["destination"], t["purpose"], t.get("doc_number") or "",
                    fmt_km(t["km"]), fmt_rate(t["rate"]), to_decimal(t["amount_cents"]) / 100, t.get("note") or ""])
    return out
