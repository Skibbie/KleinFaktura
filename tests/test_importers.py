"""Importer: Altbelege aus Excel/PDF, Sonderfälle des Parsers, Hottgenroth-Abruf (nachgestellt), Kunden aus Excel."""
from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_core import DUMMY_COMPANY, _customer, con  # noqa: E402, F401

ROWS = [
    ["", "", "", "", "", "Energieberatung"], ["", "", "", "", "", "Beraterweg 1"], ["", "", "", "", "", "10115 Berlin"],
    [], ["Bau GmbH"], ["Herrn Max Beispiel"], ["Testallee 3"], ["54321 Probestadt"], [],
    ["Sehr geehrter Herr Beispiel,", "", "", "", "", datetime(2026, 9, 15)],
    ["Rechnung 2026-0907"],
    ["Pos.", "Beschreibung", "Anzahl", "Einheit", "Einzelpreis", "Gesamtpreis"],
    [1, "Hydraulischer Abgleich\nObjekt Testallee 3", 4, "Stunden", 120.0, 480.0],
    [2, "Fahrtkosten", 30, "km", 0.3, 9.0],
    ["", "", "", "", "Summe:", 489.0],
    ["Leistungsdatum: 01.09.2026"],
]


def _xlsx(path: Path, rows=ROWS) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def _pdf(path: Path, lines: list[str]) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 18
    c.save()
    return path


# ------------------------------------------------------------------ Altbelege
def test_legacy_import_from_xlsx(tmp_path):
    from app.importers import legacy_invoices as li
    inv = li.parse_file(_xlsx(tmp_path / "Rechnung.xlsx"), DUMMY_COMPANY)
    assert (inv.doc_type, inv.number, inv.doc_date, inv.service_date) == ("rechnung", "2026-0907", "2026-09-15", "2026-09-01")
    assert inv.total_cents == 48900 and inv.lines_total == 48900 and not inv.warnings
    first, second = inv.lines
    assert (first.title, first.description, first.quantity, first.unit, first.unit_price_cents) == \
        ("Hydraulischer Abgleich", "Objekt Testallee 3", "4", "Stunden", 12000)
    assert (second.title, second.unit_price_cents, second.total_cents) == ("Fahrtkosten", 30, 900)
    assert inv.recipient_lines == ["Bau GmbH", "Herrn Max Beispiel", "Testallee 3", "54321 Probestadt"]
    c = inv.customer
    assert (c["company"], c["salutation"], c["first_name"], c["last_name"], c["zip"], c["city"]) == \
        ("Bau GmbH", "Herr", "Max", "Beispiel", "54321", "Probestadt")
    assert c["letter_salutation"] == "Sehr geehrter Herr Beispiel,"


def test_legacy_import_reports_inconsistent_sheet(tmp_path):
    """Menge × Preis ≠ Gesamtpreis, abweichende Summe und fehlender Einzelpreis werden gemeldet statt verschluckt."""
    from app.importers import legacy_invoices as li
    rows = [list(r) for r in ROWS]
    rows[12] = [1, "Hydraulischer Abgleich", 4, "Stunden", 120.0, 500.0]      # gerechnet wären es 480
    rows[13] = [2, "Pauschale", "viel", "pauschal", "", 9.0]                 # Menge unlesbar, kein Einzelpreis
    rows[14] = ["", "", "", "", "Summe:", 600.0]
    inv = li.parse_file(_xlsx(tmp_path / "krumm.xlsx", rows), DUMMY_COMPANY)
    assert [ln.total_cents for ln in inv.lines] == [50000, 900] and inv.lines[1].unit_price_cents == 900
    assert any("Menge × Preis ≠ Gesamtpreis" in w for w in inv.warnings)
    assert any("weicht von der Gesamtsumme ab" in w for w in inv.warnings)


def test_legacy_import_offer_without_table(tmp_path):
    """Angebot ohne erkennbare Positionstabelle: Sammelposition mit dem Gesamtbetrag, Nummer aus dem Dateinamen."""
    from app.importers import legacy_invoices as li
    rows = [["Frau Erika Muster"], ["Weg 1"], ["12345 Ort"], [], ["Guten Tag Frau Muster"], ["Angebot vom 03.02.26"],
            ["Gesamtbetrag", "", 250.0]]
    inv = li.parse_file(_xlsx(tmp_path / "AN2026-0203.xlsx", rows), DUMMY_COMPANY)
    assert (inv.doc_type, inv.number, inv.doc_date, inv.total_cents) == ("angebot", "AN2026-0203", "2026-02-03", 25000)
    assert len(inv.lines) == 1 and inv.lines[0].title == "Leistung laut Originalbeleg"
    assert any("Sammelposition" in w for w in inv.warnings)
    assert inv.customer["salutation"] == "Frau" and inv.customer["letter_salutation"] == "Guten Tag Frau Muster,"


def test_legacy_import_from_pdf(tmp_path):
    from app.importers import legacy_invoices as li
    pdf = _pdf(tmp_path / "Rechnung.pdf", [
        "Energieberatung Max Muster", "Max Beispiel", "Testallee 3", "54321 Probestadt",
        "Sehr geehrter Herr Beispiel, 15.09.2026", "Rechnung Nr. 2026-0908",
        "1 Hydraulischer Abgleich 4,5 Stunden 120,00 540,00", "2 Fahrtkosten 30 km 0,30 9,00", "Summe: 549,00"])
    inv = li.parse_file(pdf, DUMMY_COMPANY)
    assert (inv.number, inv.doc_date, inv.total_cents) == ("2026-0908", "2026-09-15", 54900)
    assert [(ln.title, ln.quantity, ln.unit, ln.unit_price_cents, ln.total_cents) for ln in inv.lines] == \
        [("Hydraulischer Abgleich", "4.5", "Stunden", 12000, 54000), ("Fahrtkosten", "30", "km", 30, 900)]
    assert inv.customer["last_name"] == "Beispiel" and inv.customer["zip"] == "54321"


def test_legacy_import_unreadable_content(tmp_path):
    from app.importers import legacy_invoices as li
    with pytest.raises(ValueError, match="Nicht unterstütztes Format"):
        li.read_rows(tmp_path / "rechnung.docx")
    inv = li.parse_file(_xlsx(tmp_path / "leer.xlsx", [["nur Text ohne Angaben"]]), DUMMY_COMPANY)
    assert inv.number == "" and inv.lines == [] and inv.total_cents == 0
    for expected in ("Keine Belegnummer", "Kein Datum", "Empfängeradresse nicht erkannt", "Gesamtsumme nicht erkannt"):
        assert any(expected in w for w in inv.warnings)


def test_legacy_collisions_and_duplicate_numbers(con, tmp_path):  # noqa: F811
    from app.importers import legacy_invoices as li
    inv = li.parse_file(_xlsx(tmp_path / "Rechnung.xlsx"), DUMMY_COMPANY)
    assert li.check_collisions(con, inv) == []
    assert [c.kind for c in li.check_collisions(con, inv, number="")] == ["number_format"]
    assert [c.kind for c in li.check_collisions(con, inv, number="R-17")] == ["number_format"]
    lines = [{"title": ln.title, "description": ln.description, "quantity": ln.quantity, "unit": ln.unit,
              "unit_price_cents": ln.unit_price_cents} for ln in inv.lines]
    kw = {"doc_type": "rechnung", "doc_date": inv.doc_date, "service_date": inv.service_date, "due_date": None,
          "customer_id": _customer(con), "lines": lines}
    li.import_parsed(con, inv, number="2026-0907", paid=True, **kw)
    assert {c.kind for c in li.check_collisions(con, inv)} == {"hash", "number"}
    with pytest.raises(ValueError, match="bereits importiert"):
        li.import_parsed(con, inv, number="2026-0999", **kw)
    other = li.parse_file(_xlsx(tmp_path / "andere.xlsx", [*ROWS, ["Nachtrag"]]), DUMMY_COMPANY)
    with pytest.raises(ValueError, match="bereits vergeben"):
        li.import_parsed(con, other, number="2026-0907", **kw)
    assert li.suggest_duplicate_number(con, "2026-0907") == "2026-0907-D1"
    li.import_parsed(con, other, number="2026-0907-D1", **kw)
    assert li.suggest_duplicate_number(con, "2026-0907") == "2026-0907-D2"
    from app.services import numbering
    assert numbering.peek_next(con, "rechnung", datetime(2026, 9, 1).date()) == "2026-0908"


# ------------------------------------------------------------------ Kunden aus Excel
def test_customer_import_from_xlsx_and_odd_csv(tmp_path):
    from app.importers import csv_import
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["Nachname", "Vorname", "PLZ", "Ort"])
    wb.active.append(["Muster", "Erika", 12345, "Musterstadt"])
    wb.active.append([None, None, None, None])
    wb.save(tmp_path / "kunden.xlsx")
    headers, rows = csv_import.read_table(tmp_path / "kunden.xlsx")
    assert headers == ["Nachname", "Vorname", "PLZ", "Ort"] and rows == [["Muster", "Erika", "12345", "Musterstadt"]]
    (tmp_path / "leer.csv").write_text("\n\n", encoding="utf-8")
    assert csv_import.read_table(tmp_path / "leer.csv") == ([], [])
    (tmp_path / "eine-spalte.csv").write_bytes("Nachname\nMüller\n".encode("cp1252"))   # kein Trennzeichen, kein UTF-8
    assert csv_import.read_table(tmp_path / "eine-spalte.csv") == (["Nachname"], [["Müller"]])


# ------------------------------------------------------------------ Hottgenroth (Datenbankzugriff nachgestellt)
_COLUMNS = ("uuid", "GenderType", "Salutation", "Title", "FirstName", "LastName", "CompanyName", "LetterSalutation",
            "Street", "HouseNumber", "PostalCode", "City", "Country", "TelephoneNumber", "MobileTelephoneNumber",
            "EmailAddress")


class _FakeCursor:
    def __init__(self):
        self.description = [(c,) for c in _COLUMNS]

    def execute(self, sql):
        assert sql.lstrip().upper().startswith("SELECT")       # nur lesender Zugriff

    def fetchall(self):
        return [("ABC-1", 2, "", "Dr.", "Erika", "Muster", "", "Sehr geehrte Frau Dr. Muster", "Weg", "1", "12345",
                 "Ort", "Deutschland", "030 1", None, "erika@example.org"),
                ("ABC-2", 1, None, None, "Max", "Beispiel", "Bau GmbH", None, None, None, None, None, None, None, None, None)]


def _fake_pyodbc(monkeypatch, fail_first=False, always_fail=False, drivers=("ODBC Driver 18 for SQL Server",)):
    calls = []

    class Error(Exception):
        pass

    class Db:
        closed = False

        def cursor(self):
            return _FakeCursor()

        def close(self):
            Db.closed = True

    def connect(cs, **kw):
        calls.append((cs, kw))
        if always_fail or (fail_first and len(calls) == 1):
            raise Error("Instanz nicht gestartet")
        return Db()
    module = types.SimpleNamespace(connect=connect, Error=Error, drivers=lambda: list(drivers))
    monkeypatch.setitem(sys.modules, "pyodbc", module)
    return calls, Db


def test_hottgenroth_fetch_contacts(con, monkeypatch):  # noqa: F811
    from app.db.database import set_setting
    from app.importers import hottgenroth as hg
    started = []
    monkeypatch.setattr(hg, "_start_localdb", started.append)
    calls, db = _fake_pyodbc(monkeypatch)
    contacts = hg.fetch_contacts(con)
    cs, kw = calls[0]
    assert "ApplicationIntent=ReadOnly" in cs and kw["readonly"] is True and db.closed and not started
    first, second = contacts
    assert (first["hottgenroth_uuid"], first["salutation"], first["street"], first["country"]) == ("abc-1", "Frau", "Weg 1", "DE")
    assert first["letter_salutation"] == "Sehr geehrte Frau Dr. Muster,"
    assert (second["salutation"], second["company"], second["letter_salutation"]) == \
        ("Herr", "Bau GmbH", "Sehr geehrter Herr Beispiel,")
    assert first["hottgenroth_hash"] != second["hottgenroth_hash"]

    calls, _ = _fake_pyodbc(monkeypatch, fail_first=True)       # erster Versuch scheitert: LocalDB starten, nochmal
    assert len(hg.fetch_contacts(con)) == 2 and len(calls) == 2 and started == [r"(localdb)\HSETU"]
    _fake_pyodbc(monkeypatch, always_fail=True)
    with pytest.raises(hg.HottgenrothError, match="Keine Verbindung"):
        hg.fetch_contacts(con)

    set_setting(con, "hottgenroth.driver", "ODBC Driver 18 for SQL Server")
    _fake_pyodbc(monkeypatch, drivers=("SQLite3 ODBC Driver",))
    with pytest.raises(hg.HottgenrothError, match="Kein SQL-Server-ODBC-Treiber"):
        hg.fetch_contacts(con)
    set_setting(con, "hottgenroth.server", r"(localdb)\HSETU;Database=master")   # eingeschleuste Zusatzangabe
    with pytest.raises(hg.HottgenrothError, match="Ungültige Angabe"):
        hg.fetch_contacts(con)
    monkeypatch.setitem(sys.modules, "pyodbc", None)            # Paket fehlt
    with pytest.raises(hg.HottgenrothError, match="pyodbc"):
        hg.fetch_contacts(con)


def test_hottgenroth_localdb_start(monkeypatch):
    from app.importers import hottgenroth as hg
    runs = []
    monkeypatch.setattr(hg.subprocess, "run", lambda args, **kw: runs.append(args))
    monkeypatch.setattr(hg.shutil, "which", lambda name: None)
    hg._start_localdb(r"(localdb)\HSETU")                       # sqllocaldb nicht installiert
    monkeypatch.setattr(hg.shutil, "which", lambda name: r"C:\sql\sqllocaldb.exe")
    hg._start_localdb("server.example")                         # anderer Rechner: nichts zu starten
    assert runs == []
    hg._start_localdb(r"(localdb)\HSETU")
    assert runs == [[r"C:\sql\sqllocaldb.exe", "start", "HSETU"]]


def test_hottgenroth_keep_local_and_merge(con):  # noqa: F811
    from app.db import repo
    from app.importers import hottgenroth as hg
    cid = _customer(con, hottgenroth_uuid="abc-1", hottgenroth_hash="alt", locally_modified=1)
    incoming = hg.map_contact({"uuid": "ABC-1", "GenderType": 2, "FirstName": "Erika", "LastName": "Mustermann",
                               "Street": "Neue Straße", "HouseNumber": "5", "PostalCode": "12345", "City": "Musterstadt"})
    item = hg.plan_import(con, [incoming])[0]
    assert item.status == "kollision" and "street" in item.diff
    item.decision = "keep_local"
    assert hg.apply_import(con, [item])["verknuepft"] == 1
    kept = repo.get_customer(con, cid)
    assert kept["street"] == "Musterweg 12" and kept["hottgenroth_hash"] == incoming["hottgenroth_hash"]
    assert hg.plan_import(con, [incoming])[0].status == "unveraendert"          # Kollision wird nicht erneut gemeldet

    changed = dict(incoming, city="Neustadt")
    changed["hottgenroth_hash"] = hg.contact_hash(changed)
    item = hg.plan_import(con, [changed])[0]
    item.decision, item.merged = "merge", {**changed, "street": "Musterweg 12"}
    assert hg.apply_import(con, [item])["aktualisiert"] == 1
    merged = repo.get_customer(con, cid)
    assert (merged["city"], merged["street"], merged["locally_modified"]) == ("Neustadt", "Musterweg 12", 1)

    stranger = hg.map_contact({"uuid": "ZZZ-9", "LastName": "Unbekannt"})
    skip = hg.plan_import(con, [stranger])[0]
    skip.decision = "skip"
    assert hg.apply_import(con, [skip])["uebersprungen"] == 1 and hg.ignored_uuids(con) == {"zzz-9"}
    assert hg.plan_import(con, [stranger])[0].ignored
