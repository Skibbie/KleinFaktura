"""Kerntests (nur Dummy-Daten)."""
from __future__ import annotations

import os
import sqlite3
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database
    c = database.connect()
    database.init_db(c)
    _dummy_company(c)
    yield c
    c.close()


DUMMY_COMPANY = {"name": "Energieberatung Max Muster", "line1": "Energieberatung", "owner": "Max Muster",
                 "street": "Beraterweg 1", "zip": "10115", "city": "Berlin", "phone": "030 123456",
                 "email": "info@example.org", "tax_number": "12/345/67890", "iban": "DE02 1203 0000 0000 2020 51"}


def _dummy_company(con):
    from app.db.database import set_setting
    for k, v in DUMMY_COMPANY.items():
        set_setting(con, f"company.{k}", v)


def _customer(con, **kw):
    from app.db import repo
    data = {"salutation": "Frau", "first_name": "Erika", "last_name": "Mustermann", "street": "Musterweg 12",
            "zip": "12345", "city": "Musterstadt"}
    data.update(kw)
    return repo.save_customer(con, data)


def _invoice(con, cid, price=12000, qty="6", on=None, doc_type="rechnung", zugferd=0):
    from app.services import documents
    d = documents.new_draft(con, doc_type, cid)
    if on:
        d["doc_date"] = d["service_date"] = on
    d["zugferd"] = zugferd
    return documents.save_draft(con, d, [{"title": "Heizlastberechnung", "description": "Test", "quantity": qty,
                                          "unit": "Stunden", "unit_price_cents": price}])


def _fake_pdf(data, path):
    Path(path).write_bytes(b"%PDF-1.4 test " + data["number"].encode())


# ------------------------------------------------------------------ Geld
def test_money():
    from app.money import fmt_eur, to_cents, line_total_cents, parse_date
    assert to_cents("1.234,56") == 123456
    assert to_cents("1234.5") == 123450
    assert to_cents("720,00 €") == 72000
    assert fmt_eur(123456) == "1.234,56 €"
    assert line_total_cents("0,333", 10000) == 3330
    assert parse_date("01.10.2026") == "2026-10-01"


# ------------------------------------------------------------------ Nummern
def test_numbering_monthly(con):
    from app.db.database import transaction
    from app.services import numbering
    with transaction(con):
        assert numbering.allocate(con, "rechnung", date(2026, 10, 1)) == "2026-1001"
        assert numbering.allocate(con, "rechnung", date(2026, 10, 5)) == "2026-1002"
        assert numbering.allocate(con, "rechnung", date(2026, 11, 1)) == "2026-1101"
        assert numbering.allocate(con, "angebot", date(2026, 10, 1)) == "AN2026-1001"
        assert numbering.allocate(con, "storno", date(2026, 10, 9)) == "2026-1003"


def test_numbering_reserve_and_no_reset(con):
    from app.db.database import transaction
    from app.services import numbering
    with transaction(con):
        assert numbering.reserve(con, "rechnung", "2026-0902")
        assert numbering.allocate(con, "rechnung", date(2026, 9, 30)) == "2026-0903"
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(con):
            numbering.set_start(con, "rechnung", 2026, 9, 1)


def test_no_number_for_drafts_and_no_gaps(con):
    from app.services import documents
    cid = _customer(con)
    d1 = _invoice(con, cid, on="2026-10-01")
    d2 = _invoice(con, cid, on="2026-10-02")
    documents.delete_draft(con, d1)
    assert documents.get_document(con, d2)["number"] is None
    fin = documents.finalize(con, d2, pdf_writer=_fake_pdf)
    assert fin["number"] == "2026-1001"


# ------------------------------------------------------------------ GoBD
def test_finalized_is_immutable(con):
    from app.services import documents
    cid = _customer(con)
    did = _invoice(con, cid, on="2026-10-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    for sql in ["UPDATE document SET subject='x' WHERE id=?", "DELETE FROM document WHERE id=?",
                "UPDATE document_line SET unit_price_cents=1 WHERE document_id=?",
                "DELETE FROM document_line WHERE document_id=?"]:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql, (did,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO document_line(document_id, pos) VALUES (?, 9)", (did,))
    with pytest.raises(ValueError):
        documents.save_draft(con, documents.get_document(con, did), [])


def test_finalize_failure_rolls_back(con):
    from app.services import documents
    cid = _customer(con)
    did = _invoice(con, cid, on="2026-10-01")

    def boom(data, path):
        Path(path).write_bytes(b"x")
        raise RuntimeError("PDF kaputt")
    with pytest.raises(RuntimeError):
        documents.finalize(con, did, pdf_writer=boom)
    d = documents.get_document(con, did)
    assert d["status"] == "entwurf" and d["number"] is None
    assert documents.finalize(con, did, pdf_writer=_fake_pdf)["number"] == "2026-1001"


def test_validation_blocks_missing_address(con):
    from app.services import documents
    cid = _customer(con, street="")
    did = _invoice(con, cid)
    with pytest.raises(documents.ValidationError):
        documents.finalize(con, did, pdf_writer=_fake_pdf)


def test_audit_chain_detects_tampering(con):
    from app.services import audit, documents
    cid = _customer(con)
    documents.finalize(con, _invoice(con, cid, on="2026-10-01"), pdf_writer=_fake_pdf)
    assert audit.verify(con)[0]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE audit_log SET data='{}' WHERE id=1")
    # Manipulation an der Datei vorbei (Trigger entfernt) wird erkannt
    con.execute("DROP TRIGGER trg_audit_no_update")
    con.execute("UPDATE audit_log SET data='{\"x\":1}' WHERE id=2")
    ok, msg = audit.verify(con)
    assert not ok and "verändert" in msg


def test_storno_and_open_items(con):
    from app.services import documents, payments
    cid = _customer(con)
    did = _invoice(con, cid, on="2026-10-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    assert payments.open_amount(con, did) == 72000
    payments.add_payment(con, did, "2026-10-05", "200,00")
    assert payments.open_amount(con, did) == 52000
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM payment")
    s = documents.create_storno(con, did, pdf_writer=_fake_pdf)
    assert s["number"] == "2026-1002" and s["total_gross_cents"] == -72000
    assert payments.open_amount(con, did) == 0
    assert documents.list_documents(con, ("rechnung",))[0]["status_label"] == "Storniert"
    with pytest.raises(ValueError):
        documents.create_storno(con, did, pdf_writer=_fake_pdf)


def test_placeholders_rendered_on_finalize(con):
    from app.db import repo
    from app.services import documents
    cid = _customer(con)
    pid = repo.save_project(con, {"customer_id": cid, "name": "EFH", "object_street": "Beispielweg 1",
                                  "object_zip": "23730", "object_city": "Neustadt"})
    d = documents.new_draft(con, "rechnung", cid, pid)
    did = documents.save_draft(con, d, [{"title": "X", "description": "{objekt_adresse}", "quantity": "1",
                                         "unit_price_cents": 100}])
    fin = documents.finalize(con, did, pdf_writer=_fake_pdf)
    assert documents.get_lines(con, did)[0]["description"] == "Beispielweg 1, 23730 Neustadt"
    assert fin["number"] in fin["footer_text"]
    assert "{" not in fin["salutation_text"]


def test_regel_tax(con):
    from app.services import documents
    tot = documents.compute_totals([{"quantity": "1", "unit_price_cents": 10000, "tax_rate": "19"},
                                    {"quantity": "2", "unit_price_cents": 1000, "tax_rate": "7"}], "regel")
    assert tot["net"] == 12000 and tot["tax"] == 1900 + 140 and tot["gross"] == 14040
    assert documents.compute_totals([{"quantity": "1", "unit_price_cents": 10000, "tax_rate": "19"}],
                                    "kleinunternehmer")["tax"] == 0


# ------------------------------------------------------------------ Kleinunternehmer
def test_kleinunternehmer_limits(con):
    from app.services import documents, kleinunternehmer, payments
    cid = _customer(con)
    did = _invoice(con, cid, price=2600000, qty="1", on="2025-06-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    payments.add_payment(con, did, "2025-06-10", "26000")
    st = kleinunternehmer.status(con, 2026)
    assert not st.eligible_this_year
    d = documents.new_draft(con, "rechnung", cid)
    d.update(doc_date="2026-03-01", total_net_cents=100)
    assert kleinunternehmer.check_before_finalize(con, d)
    big = dict(d, total_net_cents=10_000_001)
    con.execute("UPDATE settings SET value='99999999' WHERE key='ku.limit_prev'")
    msgs = kleinunternehmer.check_before_finalize(con, big)
    assert any("100.000,00 €" in m for m in msgs)


# ------------------------------------------------------------------ PDF / ZUGFeRD / GiroCode
def test_real_pdf_and_zugferd(con):
    import facturx
    from app.services import documents
    cid = _customer(con, email="kunde@example.org")
    did = _invoice(con, cid, on="2026-10-01", zugferd=1)
    fin = documents.finalize(con, did)
    pdf = Path(fin["pdf_path"]).read_bytes()
    name, xml = facturx.get_xml_from_pdf(pdf, check_xsd=True)
    assert name == "factur-x.xml"
    assert b"<ram:CategoryCode>E</ram:CategoryCode>" in xml and b"2026-1001" in xml
    assert b"GrandTotalAmount>720.00<" in xml
    s = documents.create_storno(con, did)
    _, sx = facturx.get_xml_from_pdf(Path(s["pdf_path"]).read_bytes(), check_xsd=True)
    assert b"<ram:TypeCode>381</ram:TypeCode>" in sx and b"GrandTotalAmount>720.00<" in sx
    assert not documents.verify_archive(con)


def test_epc_payload():
    from app.output.pdf_invoice import epc_payload
    p = epc_payload("Energieberatung", "DE02 1203 0000 0000 2020 51", "", 72000, "Rechnung 2026-1001").split("\n")
    assert p[:4] == ["BCD", "002", "1", "SCT"] and p[6] == "DE02120300000000202051" and p[7] == "EUR720.00"


# ------------------------------------------------------------------ Importe
def _make_ods(path: Path, number="2026-0905", total="480,00"):
    """Erzeugt eine Dummy-ODS im Vorlagenformat."""
    T = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    X = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    O = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"

    def row(*cells):
        out = "<table:table-row>"
        for c in cells:
            paras = "".join(f"<text:p>{p}</text:p>" for p in str(c).split("\n")) if c else ""
            out += f"<table:table-cell>{paras}</table:table-cell>"
        return out + "</table:table-row>"
    rows = [row("", "", "", "", "", "Energieberatung"), row("", "", "", "", "", "Beraterweg 1"),
            row("", "", "", "", "", "10115 Berlin"), row(), row("Max Beispiel"), row("Testallee 3"),
            row("54321 Probestadt"), row(), row("Sehr geehrter Herr Beispiel,", "", "", "", "", "15.09.2026"),
            row("für die Erledigung …"), row(f"Rechnung {number}"),
            row("Pos.", "Beschreibung", "Anzahl", "Einheit", "Einzelpreis", "Gesamtpreis"),
            row("1", "Hydraulischer Abgleich\nObjekt Testallee 3", "4,0", "Stunden", "120,00 €", f"{total} €"),
            row("", "", "", "", "Summe:", f"{total} €")]
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="{O}" '
               f'xmlns:table="{T}" xmlns:text="{X}"><office:body><office:spreadsheet><table:table table:name="R">'
               + "".join(rows) + "</table:table></office:spreadsheet></office:body></office:document-content>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content)
    return path


def test_legacy_ods_import_and_collisions(con, tmp_path):
    from app.db.database import company
    from app.importers import legacy_invoices as L
    from app.services import documents, numbering, payments
    f = _make_ods(tmp_path / "Rechnung-2026-0905.ods")
    inv = L.parse_file(f, company(con))
    assert inv.number == "2026-0905" and inv.doc_date == "2026-09-15"
    assert inv.customer["last_name"] == "Beispiel" and inv.customer["zip"] == "54321"
    assert len(inv.lines) == 1 and inv.lines[0].total_cents == 48000 and inv.total_cents == 48000
    assert not L.check_collisions(con, inv)
    cid = _customer(con, first_name="Max", last_name="Beispiel", street="Testallee 3", zip="54321", city="Probestadt")
    lines = [{"title": l.title, "description": l.description, "quantity": l.quantity, "unit": l.unit,
              "unit_price_cents": l.unit_price_cents} for l in inv.lines]
    did = L.import_parsed(con, inv, number=inv.number, doc_type="rechnung", doc_date=inv.doc_date,
                          service_date=inv.service_date, due_date="2026-09-29", customer_id=cid, lines=lines)
    d = documents.get_document(con, did)
    assert d["status"] == "festgeschrieben" and d["source"] == "import" and d["total_gross_cents"] == 48000
    assert payments.open_amount(con, did) == 48000
    # Nummer reserviert: nächste Septembernummer ist 0906
    assert numbering.peek_next(con, "rechnung", date(2026, 9, 20)) == "2026-0906"
    # gleiche Datei erneut -> Hash-Kollision
    kinds = {c.kind for c in L.check_collisions(con, L.parse_file(f, company(con)))}
    assert "hash" in kinds and "number" in kinds
    with pytest.raises(ValueError):
        L.import_parsed(con, inv, number="2026-0999", doc_type="rechnung", doc_date=inv.doc_date, service_date=None,
                        due_date=None, customer_id=cid, lines=lines)
    # andere Datei, gleiche Nummer -> Dublette mit Suffix
    f2 = _make_ods(tmp_path / "andere.ods", total="500,00")
    inv2 = L.parse_file(f2, company(con))
    assert {c.kind for c in L.check_collisions(con, inv2)} == {"number"}
    dup = L.suggest_duplicate_number(con, inv2.number)
    assert dup == "2026-0905-D1"
    L.import_parsed(con, inv2, number=dup, doc_type="rechnung", doc_date=inv2.doc_date, service_date=None,
                    due_date=None, customer_id=cid, lines=lines, paid=True, pay_date="2026-09-20", note="Dublette")
    assert not documents.verify_archive(con)


def test_customer_import_planning(con):
    from app.importers import hottgenroth as H
    c = H.map_contact({"uuid": "ABC", "Salutation": "Frau", "FirstName": "Erika", "LastName": "Mustermann",
                       "Street": "Musterweg", "HouseNumber": "12", "PostalCode": "12345", "City": "Musterstadt"})
    assert c["street"] == "Musterweg 12" and c["letter_salutation"] == "Sehr geehrte Frau Mustermann,"
    items = H.plan_import(con, [c])
    assert items[0].status == "neu"
    H.apply_import(con, items)
    assert H.plan_import(con, [c])[0].status == "unveraendert"
    # lokal geändert + Hottgenroth geändert -> Kollision
    from app.db import repo
    cust = repo.list_customers(con)[0]
    repo.save_customer(con, {"id": cust["id"], "phone": "0123"})
    c2 = dict(c, city="Neustadt")
    c2["hottgenroth_hash"] = H.contact_hash(c2)
    it = H.plan_import(con, [c2])[0]
    assert it.status == "kollision" and "city" in it.diff
    it.decision = "merge"
    it.merged = {f: (c2[f] if f == "city" else cust.get(f) or "") for f in H.COMPARE_FIELDS}
    it.merged["phone"] = "0123"
    H.apply_import(con, [it])
    after = repo.get_customer(con, cust["id"])
    assert after["city"] == "Neustadt" and after["phone"] == "0123"
    # manueller Kunde ohne UUID -> Dublette
    repo.save_customer(con, {"first_name": "Hans", "last_name": "Meier", "zip": "11111", "city": "X"})
    d = H.map_contact({"uuid": "DEF", "FirstName": "Hans", "LastName": "Meier", "PostalCode": "11111", "City": "X"})
    assert H.plan_import(con, [d])[0].status == "dublette"


def test_customer_import_remembers_skipped(con):
    from app.importers import hottgenroth as H
    a = H.map_contact({"uuid": "AAA", "FirstName": "Anna", "LastName": "Alt", "PostalCode": "1", "City": "A"})
    b = H.map_contact({"uuid": "BBB", "FirstName": "Bert", "LastName": "Neu", "PostalCode": "2", "City": "B"})
    items = H.plan_import(con, [a, b])
    assert all(i.is_new and not i.ignored for i in items)
    items[0].decision = "skip"
    H.apply_import(con, items)
    again = H.plan_import(con, [a, b])
    assert again[0].ignored and again[0].is_new
    assert not again[1].is_new  # bereits importiert
    # später doch importiert -> nicht mehr gemerkt
    again[0].decision = "new"
    H.apply_import(con, [again[0]])
    assert H.ignored_uuids(con) == set()
    assert not H.plan_import(con, [a])[0].is_new


def test_csv_import(tmp_path):
    from app.importers import csv_import
    p = tmp_path / "k.csv"
    p.write_text("Vorname;Nachname;Straße;Hausnummer;PLZ;Ort\nErika;Muster;Weg;1;12345;Stadt\n", encoding="utf-8")
    headers, rows = csv_import.read_table(p)
    m = csv_import.guess_mapping(headers)
    cs = csv_import.rows_to_customers(rows, m)
    assert cs[0]["street"] == "Weg 1" and cs[0]["zip"] == "12345"


# ------------------------------------------------------------------ Backup / Export
def test_backup_restore(con, tmp_path):
    from app import config
    from app.db.database import set_setting
    from app.services import backup, documents
    set_setting(con, "backup.dir", str(tmp_path / "bk"))
    cid = _customer(con)
    documents.finalize(con, _invoice(con, cid, on="2026-10-01"), pdf_writer=_fake_pdf)
    logo = config.data_dir() / "logo.png"
    logo.write_bytes(b"logo")
    set_setting(con, "company.logo", str(logo))
    z = backup.create_backup(con)
    assert z.exists() and {"faktura.sqlite3", "logo/logo.png"} <= set(zipfile.ZipFile(z).namelist())
    con.close()
    os.remove(config.db_path())
    logo.write_bytes(b"anderes")
    backup.restore_backup(z)
    from app.db import database
    c2 = database.connect()
    assert c2.execute("SELECT number FROM document").fetchone()[0] == "2026-1001"
    assert logo.read_bytes() == b"logo"
    c2.close()


def test_exports(con, tmp_path):
    from app.services import documents, export, payments
    cid = _customer(con)
    did = _invoice(con, cid, on="2026-10-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    payments.add_payment(con, did, "2026-10-03", "720")
    export.export_revenue(con, tmp_path / "u.xlsx", 2026)
    export.export_revenue(con, tmp_path / "u.csv", 2026)
    export.export_datev(con, tmp_path / "d.csv", 2026)
    assert "720,00;S;EUR" in (tmp_path / "d.csv").read_text(encoding="cp1252")
    export.export_gobd(con, tmp_path / "g.zip")
    assert "belege.csv" in zipfile.ZipFile(tmp_path / "g.zip").namelist()


# ------------------------------------------------------------------ Schema-Version
def test_schema_version_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database
    c = database.connect(tmp_path / "alt.sqlite3")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")   # Datenbank vor Einführung der Version
    database.init_db(c)
    assert database.schema_version(c) == database.SCHEMA_VERSION
    assert len(list((tmp_path / "data" / "vor-update").glob("faktura-schema0-*.sqlite3"))) == 1
    database.init_db(c)                                                       # gleiche Version: keine weitere Kopie
    assert len(list((tmp_path / "data" / "vor-update").iterdir())) == 1
    c.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
    with pytest.raises(database.NewerDatabaseError):
        database.init_db(c)
    c.close()


def test_upgrade_1_0_database(tmp_path, monkeypatch):
    """Datenbank im Stand 1.0.0 (Schema 1): Notizen-Tabelle und Fahrt-Anlässe kommen dazu, Daten bleiben."""
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database, repo
    c = database.connect(tmp_path / "v1.sqlite3")
    database.init_db(c)
    c.executescript("DROP TABLE note; DELETE FROM settings WHERE key='seed.fahrt_anlass'; "
                    "DELETE FROM textblock WHERE category='fahrt_anlass'; PRAGMA user_version = 1;")
    blocks_before = len(repo.list_textblocks(c))
    cid = _customer(c)
    database.init_db(c)
    assert database.schema_version(c) == 2
    assert len(list((tmp_path / "data" / "vor-update").glob("faktura-schema1-*.sqlite3"))) == 1
    assert c.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    assert repo.get_customer(c, cid)["last_name"] == "Mustermann"
    anlass = repo.text_choices(c, "fahrt_anlass")
    assert anlass == list(database.PROFILES["allgemein"].trip_purposes)
    assert len(repo.list_textblocks(c)) == blocks_before + len(anlass)
    # gelöschte Anlässe kommen beim nächsten Start nicht wieder
    repo.delete_textblock(c, repo.list_textblocks(c, "fahrt_anlass")[0]["id"])
    database.init_db(c)
    assert len(repo.text_choices(c, "fahrt_anlass")) == len(anlass) - 1
    c.close()


def test_new_database_seeds(con):
    from app.db import database, repo
    assert repo.default_text(con, "kopf_rechnung")            # Standard-Textbausteine trotz Anlass-Startwerten
    assert repo.text_choices(con, "fahrt_anlass") == list(database.PROFILES["allgemein"].trip_purposes)
    assert database.get_setting(con, "profile") == "allgemein" and not database.profile(con).hottgenroth
    titles = [a["title"] for a in con.execute("SELECT title FROM article ORDER BY sort")]
    assert titles == [a[0] for a in database.PROFILES["allgemein"].articles]


def test_profile_switch_adds_once(con):
    """Wechsel auf Energieberatung ergänzt deren Startwerte genau einmal; zurück wird nichts gelöscht."""
    from app.db import database, repo
    n_articles = lambda: con.execute("SELECT COUNT(*) FROM article").fetchone()[0]  # noqa: E731
    before = n_articles()
    database.apply_profile(con, "energieberatung")
    assert database.profile(con).hottgenroth
    assert n_articles() == before + 4                      # „Fahrtkosten“ gibt es schon
    assert "Vor-Ort-Termin iSFP" in repo.text_choices(con, "fahrt_anlass")
    con.execute("DELETE FROM article WHERE title='Hydraulischer Abgleich'")
    database.apply_profile(con, "allgemein")
    database.apply_profile(con, "energieberatung")
    database.init_db(con)
    assert n_articles() == before + 3                      # Gelöschtes kommt nicht wieder
    with pytest.raises(ValueError):
        database.apply_profile(con, "gibtsnicht")


def test_legacy_database_becomes_energieberatung(tmp_path, monkeypatch):
    """Datenbank aus EEE-Faktura (ohne Profil): wird Energieberatung, ohne doppelte Artikel oder Anlässe."""
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database, repo
    c = database.connect(tmp_path / "alt.sqlite3")
    database.init_db(c)
    database.apply_profile(c, "energieberatung")           # Startwerte wie in der alten Version …
    c.execute("DELETE FROM settings WHERE key IN ('profile', 'seed.profile.allgemein', 'seed.profile.energieberatung')")
    c.execute("DELETE FROM article WHERE title IN ('Arbeitsleistung', 'Pauschale', 'Material')")
    articles = c.execute("SELECT COUNT(*) FROM article").fetchone()[0]
    anlass = repo.text_choices(c, "fahrt_anlass")
    old_logo = tmp_path / "alt" / "logo.jpg"
    old_logo.parent.mkdir()
    old_logo.write_bytes(b"jpg")
    monkeypatch.setattr(database, "legacy_logo_candidates", lambda: [tmp_path / "fehlt.jpg", old_logo])
    database.init_db(c)
    assert database.get_setting(c, "profile") == "energieberatung"
    logo = Path(database.get_setting(c, "company.logo"))      # eingebautes Logo in den Datenordner übernommen
    assert logo == tmp_path / "data" / "logo.jpg" and logo.read_bytes() == b"jpg"
    assert c.execute("SELECT COUNT(*) FROM article").fetchone()[0] == articles
    assert repo.text_choices(c, "fahrt_anlass") == anlass
    database.apply_profile(c, "energieberatung")
    assert c.execute("SELECT COUNT(*) FROM article").fetchone()[0] == articles
    c.close()


def test_data_dir_uses_legacy_folder(tmp_path, monkeypatch):
    """Nach der Umbenennung wird der Ordner mit der vorhandenen Datenbank weiterbenutzt (nicht verschoben)."""
    from app import config
    monkeypatch.delenv("FAKTURA_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for release, suffix in [(True, ""), (False, "-Entwicklung")]:
        monkeypatch.setattr(config, "IS_RELEASE", release)
        own, old = tmp_path / f"KleinFaktura{suffix}", tmp_path / f"EEE-Faktura{suffix}"
        assert config.data_dir() == own                    # nichts vorhanden: neuer Ordner
        own.rmdir()
        old.mkdir()
        assert config.data_dir() == own                    # alter Ordner ohne Datenbank zählt nicht
        own.rmdir()
        (old / config.DB_FILE).write_bytes(b"")
        assert config.data_dir() == old and not own.exists()
        own.mkdir(exist_ok=True)
        (own / config.DB_FILE).write_bytes(b"")
        assert config.data_dir() == own                    # eigene Datenbank hat Vorrang


def test_remember_text(con):
    from app.db import repo
    assert repo.remember_text(con, "fahrt_anlass", "  Heizungscheck vor Ort ")
    assert not repo.remember_text(con, "fahrt_anlass", "heizungscheck VOR ORT")
    assert not repo.remember_text(con, "fahrt_anlass", "   ")
    assert repo.text_choices(con, "fahrt_anlass").count("Heizungscheck vor Ort") == 1
    long = "x" * 80
    repo.remember_text(con, "fahrt_anlass", long)
    assert [t["title"] for t in repo.list_textblocks(con, "fahrt_anlass") if t["body"] == long] == ["x" * 60 + "…"]


def test_notes(con):
    from app.db import repo
    from app.services import notes
    cid = _customer(con)
    other = _customer(con, first_name="Hans", last_name="Beispiel")
    pid = repo.save_project(con, {"customer_id": cid, "name": "EFH", "object_street": "Objektweg 3"})
    with pytest.raises(ValueError):
        notes.save_note(con, {"note_date": "2026-10-01", "title": " ", "body": "\n"})
    n1 = notes.save_note(con, {"note_date": "2026-10-01", "customer_id": cid, "project_id": pid,
                               "title": "Telefonat", "body": "Unterlagen kommen per Post"})
    notes.save_note(con, {"note_date": "2026-10-03", "title": "", "body": "Allgemein"})
    notes.save_note(con, {"note_date": "2026-10-02", "customer_id": other, "project_id": pid, "body": "Rückruf"})
    assert [n["body"] for n in notes.list_notes(con)] == ["Allgemein", "Rückruf", "Unterlagen kommen per Post"]
    mine = notes.list_notes(con, cid)
    assert len(mine) == 1 and mine[0]["customer_name"] == "Erika Mustermann" and mine[0]["project_name"] == "EFH"
    assert mine[0]["title"] == "Telefonat"
    assert [n["id"] for n in notes.list_notes(con, search="post")] == [n1]
    notes.save_note(con, {"id": n1, **{k: mine[0][k] for k in notes.FIELDS}, "body": "geändert"})
    assert notes.get_note(con, n1)["body"] == "geändert"
    # Objekt löschen: Notiz bleibt beim Kunden
    repo.delete_project(con, pid)
    assert notes.get_note(con, n1)["project_id"] is None and notes.get_note(con, n1)["customer_id"] == cid
    # Kunde mit Notizen wird nur deaktiviert
    repo.deactivate_customer(con, cid)
    assert repo.get_customer(con, cid)["active"] == 0
    notes.delete_note(con, n1)
    assert notes.get_note(con, n1) is None


# ------------------------------------------------------------------ E-Mail / PDF-Ablage
def test_mail_compose_and_export(con, tmp_path, monkeypatch):
    from app.db.database import set_setting
    from app.services import documents, mail
    cid = _customer(con, email="erika@example.org", last_name="D'Angelo")
    did = _invoice(con, cid, on="2026-10-01")
    draft = documents.get_document(con, did)
    with pytest.raises(mail.MailError):
        mail.open_compose(con, draft)
    assert mail.export_copy(con, draft) is None
    doc = documents.finalize(con, did, pdf_writer=_fake_pdf)

    # Argument: Kommas und Zeilenumbrüche bleiben, ' wird typografisch, Anhang als file-URL (Leerzeichen kodiert)
    arg = mail.compose_arg("a@b.de", "Rechnung 1, Test", "Hallo D'Angelo,\nZeile 2", Path(tmp_path / "mit leer.pdf"))
    assert "body='Hallo D’Angelo,\nZeile 2'" in arg and "subject='Rechnung 1, Test'" in arg
    assert arg.endswith("mit%20leer.pdf'") and "attachment='file:///" in arg

    to, subject, body = mail.mail_texts(con, doc)
    assert to == "erika@example.org" and subject == "Rechnung 2026-1001 vom 01.10.2026"
    assert "Rechnung 2026-1001" in body and "D'Angelo" in body and "Max Muster" in body

    exe = tmp_path / "thunderbird.exe"
    exe.write_bytes(b"")
    set_setting(con, "mail.thunderbird_path", str(exe))
    launched = []
    monkeypatch.setattr(mail, "_launch", launched.append)
    assert mail.open_compose(con, doc) == "erika@example.org"
    args = launched[0]
    assert args[0] == str(exe) and args[1] == "-compose" and Path(doc["pdf_path"]).name.replace(" ", "%20") in args[2]
    assert "D’Angelo" in args[2] and "D'Angelo" not in args[2]
    assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='mail'").fetchone()[0] == 1
    set_setting(con, "mail.thunderbird_path", str(tmp_path / "fehlt.exe"))
    with pytest.raises(mail.MailError):
        mail.open_compose(con, doc)

    # PDF-Kopie: nur wenn eingeschaltet; Kopie ist beschreibbar und darf überschrieben werden
    out = tmp_path / "Rechnungen"
    set_setting(con, "pdf.export_dir", str(out))
    assert mail.export_copy(con, doc) is None
    set_setting(con, "pdf.export_auto", "1")
    target = mail.export_copy(con, doc)
    assert target == out / "2026" / Path(doc["pdf_path"]).name
    assert target.read_bytes() == Path(doc["pdf_path"]).read_bytes()
    assert os.access(target, os.W_OK)
    assert mail.export_copy(con, doc) == target
    set_setting(con, "pdf.export_year_dirs", "0")
    assert mail.export_copy(con, doc) == out / Path(doc["pdf_path"]).name
