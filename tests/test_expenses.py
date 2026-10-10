"""Tests Ausgabenbuch (Dummy-Daten)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_core import con  # noqa: E402,F401  (Fixture)


def test_afa_schedule_monthly_pro_rata():
    from app.services.expenses import afa_schedule
    s = afa_schedule(120000, "2026-10-15", 3)          # 1.200 € ab Oktober, 3 Jahre
    assert s == {2026: 10000, 2027: 40000, 2028: 40000, 2029: 30000}
    assert sum(afa_schedule(99999, "2025-01-01", 7).values()) == 99999


def test_km_and_net():
    from app.services import expenses
    from app.services.expenses import net_cents
    assert net_cents(11900, "19") == 10000

    class C:  # Minimal-Ersatz für get_setting
        pass
    from app.db import database
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(database.SCHEMA_FILE.read_text(encoding="utf-8"))
    assert expenses.km_amount_cents(c, "123,5") == 3705


def test_euer_values_and_lock(con, tmp_path):
    from app.services import documents, expenses, payments
    from test_core import _customer, _fake_pdf, _invoice
    cid = _customer(con)
    did = _invoice(con, cid, on="2026-03-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    payments.add_payment(con, did, "2026-03-10", "720")
    receipt = tmp_path / "rechnung-software.pdf"
    receipt.write_bytes(b"%PDF dummy")
    path, h = expenses.archive_receipt(receipt, "2026-02-01")
    expenses.save_expense(con, {"exp_date": "2026-02-01", "supplier": "Software GmbH", "category": "uebrige",
                                "amount_cents": 59500, "receipt_path": path, "receipt_sha256": h})
    expenses.save_expense(con, {"exp_date": "2026-04-02", "supplier": "Mobilfunk", "category": "telekom", "amount_cents": 2999})
    expenses.save_expense(con, {"exp_date": "2026-05-05", "description": "Vor-Ort-Termin", "category": "fahrt", "km": "100"})
    expenses.save_expense(con, {"exp_date": "2026-06-01", "supplier": "Café", "category": "bewirtung", "amount_cents": 10000})
    expenses.save_expense(con, {"exp_date": "2026-10-15", "supplier": "Technik", "category": "afa",
                                "amount_cents": 120000, "useful_life": 3})
    with pytest.raises(ValueError):
        expenses.save_expense(con, {"exp_date": "2026-01-01", "supplier": "X", "category": "afa", "amount_cents": 1})
    v = expenses.euer_values(con, 2026)
    areas = {a.key: a.amount_cents for a in v["areas"]}
    assert v["income_cents"] == 72000
    assert areas == {"uebrige": 59500, "telekom": 2999, "fahrt": 3000, "bewirtung": 10000, "afa": 10000}
    assert v["expense_cents"] == 59500 + 2999 + 3000 + 7000 + 10000
    assert v["missing_receipts"] == 3   # telekom, bewirtung, afa (Fahrt braucht keinen Beleg)
    assert expenses.euer_values(con, 2027)["areas"][0].amount_cents == 40000   # AfA-Folgejahr
    assert Path(path).exists()
    expenses.lock_year(con, 2026)
    eid = expenses.list_expenses(con, 2026)[0]["id"]
    with pytest.raises(sqlite3.IntegrityError):
        expenses.delete_expense(con, eid)
    with pytest.raises(sqlite3.IntegrityError):
        expenses.save_expense(con, {"exp_date": "2026-12-01", "supplier": "Y", "category": "uebrige", "amount_cents": 100})
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM expense_lock")


def test_bank_csv_import(con, tmp_path):
    from app.importers import bank_csv
    from app.services import expenses
    csv = tmp_path / "umsaetze.csv"
    csv.write_text(
        '"Kontonummer:";"DE00 1234"\n\n'
        '"Buchungstag";"Valutadatum";"Beguenstigter/Zahlungspflichtiger";"Verwendungszweck";"Betrag (EUR)"\n'
        '"02.10.2026";"02.10.2026";"Vodafone GmbH";"Rechnung 09/2026";"-39,99"\n'
        '"03.10.2026";"03.10.2026";"Erika Mustermann";"Rechnung 2026-1001";"720,00"\n'
        '"05.10.2026";"05.10.2026";"ARAL Station";"Tanken";"-65,10"\n'
        '"06.10.2026";"06.10.2026";"Finanzamt";"Einkommensteuer";"-300,00"\n', encoding="cp1252")
    headers, mapping, rows = bank_csv.parse(con, csv)
    assert [r.amount_cents for r in rows] == [3999, 6510, 30000]
    assert [r.category for r in rows][:2] == ["telekom", "kfz"]
    assert rows[2].likely_private
    assert bank_csv.import_rows(con, rows[:2]) == 2
    _, _, again = bank_csv.parse(con, csv)
    assert again[0].duplicate and again[1].duplicate and not again[2].duplicate
    assert bank_csv.import_rows(con, again[:2]) == 0
    assert len(expenses.list_expenses(con, 2026)) == 2


def test_trips(con):
    from app.db import repo
    from app.db.database import set_setting
    from app.services import documents, expenses, trips
    from test_core import _customer, _fake_pdf, _invoice
    cid = _customer(con)
    pid = repo.save_project(con, {"customer_id": cid, "name": "EFH", "object_street": "Objektweg 3",
                                  "object_zip": "12345", "object_city": "Musterstadt"})
    did = _invoice(con, cid, on="2026-03-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    other = _invoice(con, cid, on="2026-04-01")
    documents.finalize(con, other, pdf_writer=_fake_pdf)

    assert trips.default_destination(con, cid, pid) == "Objektweg 3, 12345 Musterstadt"
    assert trips.default_destination(con, cid, None) == "Musterweg 12, 12345 Musterstadt"
    assert [d["id"] for d in trips.documents_for(con, cid)] == [other, did]
    with pytest.raises(ValueError):
        trips.save_trip(con, {"trip_date": "2026-02-20", "destination": "X", "purpose": "", "km_one_way": "10"})

    t1 = trips.save_trip(con, {"trip_date": "2026-02-20", "customer_id": cid, "project_id": pid, "document_id": did,
                               "destination": "Objektweg 3, 12345 Musterstadt", "purpose": "Vor-Ort-Termin iSFP",
                               "km_one_way": "42,5", "round_trip": True})
    t = trips.get_trip(con, t1)
    assert (t["km"], t["rate"], t["amount_cents"]) == ("85", "0.3", 2550)
    assert trips.remembered_km(con, "objektweg 3, 12345 musterstadt") == ("42.5", True)
    assert trips.remembered_km(con, "", customer_id=cid) == ("42.5", True)
    from app.db.database import set_setting
    for k, v in {"company.street": "Hauptstr. 1", "company.zip": "12345", "company.city": "Musterstadt"}.items():
        set_setting(con, k, v)
    assert trips.own_address(con) == "Hauptstr. 1, 12345 Musterstadt"
    assert trips.route_url(trips.own_address(con), "Objektweg 3, 12345 Musterstadt") == (
        "https://www.google.com/maps/dir/?api=1&travelmode=driving"
        "&origin=Hauptstr.+1%2C+12345+Musterstadt&destination=Objektweg+3%2C+12345+Musterstadt")

    # Satzänderung wirkt nur auf neue Fahrten
    set_setting(con, "expense.km_rate", "0,35")
    trips.save_trip(con, {"id": t1, **{k: t[k] for k in trips.FIELDS}, "note": "geändert"})
    assert trips.get_trip(con, t1)["amount_cents"] == 2550
    trips.save_trip(con, {"trip_date": "2026-05-02", "destination": "Kammer, Schwerin", "purpose": "Fortbildung",
                          "km_one_way": "30", "round_trip": False})
    assert trips.year_totals(con, 2026) == (2550 + 1050, 2, 115.0)

    # EÜR: Fahrten + alte km-Ausgabe im selben Bereich
    set_setting(con, "expense.km_rate", "0,30")
    expenses.save_expense(con, {"exp_date": "2026-01-10", "description": "Altbestand", "category": "fahrt", "km": "10"})
    area = {a.key: a for a in expenses.euer_values(con, 2026)["areas"]}["fahrt"]
    assert (area.amount_cents, area.count) == (2550 + 1050 + 300, 3)
    assert area.note.startswith("125 km")
    assert "fahrt" not in [c.key for c in expenses.selectable_categories()]
    assert "fahrt" in [c.key for c in expenses.selectable_categories("fahrt")]

    assert [m["id"] for m in trips.invoices_without_trip(con, 2026)] == [other]
    assert len(trips.rows_for_export(con, 2026)) == 2
    with pytest.raises(ValueError):
        repo.delete_project(con, pid)

    log = [r["action"] for r in con.execute("SELECT action FROM audit_log WHERE entity='trip'")]
    assert log == ["create", "update", "create"]

    # Jahresabschluss sperrt auch Fahrten
    expenses.lock_year(con, 2026)
    with pytest.raises(sqlite3.IntegrityError):
        trips.delete_trip(con, t1)
    with pytest.raises(sqlite3.IntegrityError):
        trips.save_trip(con, {"trip_date": "2026-12-01", "destination": "X", "purpose": "Y", "km_one_way": "1"})
    trips.save_trip(con, {"trip_date": "2027-01-02", "destination": "X", "purpose": "Y", "km_one_way": "1"})
