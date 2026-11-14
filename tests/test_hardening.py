"""Härtung 2.2.3: Sicherung bei Änderungen, Passwortschutz, Schema-Umbau, Jahresabschluss, Protokollabgleich."""
from __future__ import annotations

import sqlite3
import sys
import threading
import zipfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_core import _customer, _fake_pdf, _invoice, con  # noqa: E402, F401


def _finalized(con, cid, **kw):
    from app.services import documents
    return documents.finalize(con, _invoice(con, cid, **kw), pdf_writer=_fake_pdf)


# ------------------------------------------------------------------ Sicherung
def test_backup_needed_after_change_on_same_day(con, tmp_path):
    from app.db.database import get_setting, set_setting
    from app.services import backup
    set_setting(con, "backup.dir", str(tmp_path / "bk"))
    assert backup.needs_backup(con)                      # noch nie gesichert
    backup.create_backup(con)
    assert get_setting(con, "backup.fingerprint") and not backup.needs_backup(con)
    _finalized(con, _customer(con), on="2026-10-01")     # am selben Tag weitergearbeitet
    assert backup.needs_backup(con)
    backup.create_backup(con)
    assert not backup.needs_backup(con)
    con.execute("UPDATE textblock SET body = body || ' ' WHERE id = (SELECT MIN(id) FROM textblock)")
    assert backup.needs_backup(con)                      # auch nicht protokollierte Änderungen zählen
    backup.create_backup(con)
    set_setting(con, "backup.last", "2000-01-01T00:00:00")
    assert backup.needs_backup(con)                      # einmal täglich bleibt


def test_backup_stores_receipts_uncompressed_and_is_verified(con, tmp_path, monkeypatch):
    from app import config
    from app.services import backup
    _finalized(con, _customer(con), on="2026-10-01")
    (config.archive_dir() / "notiz.txt").write_text("x" * 5000, encoding="utf-8")
    z = backup.create_backup(con, tmp_path / "bk")
    infos = {i.filename: i for i in zipfile.ZipFile(z).infolist()}
    pdf = next(i for name, i in infos.items() if name.endswith(".pdf"))
    assert pdf.compress_type == zipfile.ZIP_STORED
    assert infos["archiv/notiz.txt"].compress_type == zipfile.ZIP_DEFLATED
    assert infos["faktura.sqlite3"].compress_type == zipfile.ZIP_DEFLATED

    broken = tmp_path / "kaputt.zip"
    broken.write_bytes(z.read_bytes()[:200])
    with pytest.raises(ValueError, match="nicht gelesen"):
        backup._check_zip(broken)
    # eine Sicherung, die sich nicht zurücklesen lässt, bleibt nicht liegen und gilt nicht als erstellt
    monkeypatch.setattr(backup, "_check_zip", lambda p: (_ for _ in ()).throw(ValueError("fehlerhaft")))
    before = set((tmp_path / "bk").iterdir())
    with pytest.raises(ValueError, match="fehlerhaft"):
        backup.create_backup(con, tmp_path / "bk")
    assert set((tmp_path / "bk").iterdir()) == before


def test_backup_refuses_damaged_database(con, tmp_path, monkeypatch):
    from app.services import backup
    good = tmp_path / "gut.sqlite3"
    db = sqlite3.connect(good)
    db.execute("CREATE TABLE t (x)")
    db.commit()
    db.close()
    backup._check_snapshot(good)
    bad = tmp_path / "schlecht.sqlite3"
    data = bytearray(good.read_bytes())
    data[100:4000] = b"\xff" * 3900                       # Seiteninhalt zerstört, Kopf bleibt
    bad.write_bytes(bytes(data))
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        backup._check_snapshot(bad)


def test_backup_from_background_thread(con, tmp_path):
    from app.db.database import get_setting, set_setting
    from app.services import backup
    set_setting(con, "backup.dir", str(tmp_path / "bk"))
    _finalized(con, _customer(con), on="2026-10-01")
    out = []
    t = threading.Thread(target=lambda: out.append(backup.create_backup_own_connection()))
    t.start()
    t.join(60)
    assert out and out[0].exists()
    assert get_setting(con, "backup.last") and not backup.needs_backup(con)   # im GUI-Thread sichtbar


# ------------------------------------------------------------------ Passwortschutz
def test_secret_roundtrip_and_legacy_values():
    from app.services import secret
    stored = secret.protect("geheim-äöü-123")
    assert secret.is_protected(stored) and "geheim" not in stored
    assert secret.unprotect(stored) == "geheim-äöü-123"
    assert secret.protect("") == "" and secret.unprotect("") == "" and secret.unprotect(None) == ""
    assert secret.unprotect("altes Klartextpasswort") == "altes Klartextpasswort"
    assert secret.unprotect(secret.PREFIX + "kein-base64!!") == ""
    assert secret.unprotect(secret.PREFIX + "QUJDREVGRw==") == ""        # fremder PC/Benutzer: nicht lesbar


def test_plaintext_password_is_protected_on_start(con, tmp_path):
    from app.db import database
    from app.db.database import get_setting, set_setting
    from app.services import backup, backup_crypto, secret
    set_setting(con, "backup.password", "geheim123")       # Stand einer älteren Version
    set_setting(con, "backup.encrypt", "1")
    database.init_db(con)
    stored = get_setting(con, "backup.password")
    assert secret.is_protected(stored) and "geheim123" not in stored
    assert backup.password(con) == "geheim123"
    database.init_db(con)
    assert get_setting(con, "backup.password") == stored   # kein erneutes Umschreiben
    z = backup.create_backup(con, tmp_path / "bk")
    plain = tmp_path / "klar.zip"
    backup_crypto.decrypt_file(z, plain, "geheim123")
    snap = zipfile.ZipFile(plain).extract("faktura.sqlite3", tmp_path / "x")
    db = sqlite3.connect(snap)
    in_backup = db.execute("SELECT value FROM settings WHERE key='backup.password'").fetchone()[0]
    db.close()
    assert "geheim123" not in in_backup                    # auch in der Sicherung steht kein Klartext


def test_restore_keeps_local_encryption_settings(con, tmp_path):
    """Eine (präparierte oder fremde) Sicherung schaltet weder die Verschlüsselung ab noch ersetzt sie das Passwort."""
    from app import config
    from app.db import database
    from app.db.database import get_setting, set_setting
    from app.services import backup, secret
    z = backup.create_backup(con, tmp_path / "bk")          # Sicherung: unverschlüsselt, ohne Passwort
    set_setting(con, "backup.encrypt", "1")
    set_setting(con, "backup.password", secret.protect("lokal-geheim"))
    con.close()
    backup.restore_backup(z)
    c2 = database.connect(config.db_path())
    try:
        assert get_setting(c2, "backup.encrypt") == "1"
        assert backup.password(c2) == "lokal-geheim"
        assert backup.needs_backup(c2)                      # Fingerabdruck der alten Datenbank gilt nicht mehr
    finally:
        c2.close()


# ------------------------------------------------------------------ Schema-Umbau
def _old_database(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database
    c = database.connect()
    database.init_db(c)
    c.execute("INSERT INTO customer(customer_no, last_name) VALUES ('K10001', 'Bestand')")
    return database, c


def test_migration_adds_column_to_existing_database(tmp_path, monkeypatch):
    database, c = _old_database(tmp_path, monkeypatch)
    calls = []

    def step(con):
        calls.append(1)
        database.add_column(con, "customer", "neu_feld", "TEXT DEFAULT 'x'")
        database.add_column(con, "customer", "neu_feld", "TEXT DEFAULT 'x'")   # doppelt schadet nicht
    monkeypatch.setattr(database, "SCHEMA_VERSION", database.SCHEMA_VERSION + 1)
    monkeypatch.setattr(database, "MIGRATIONS", {database.SCHEMA_VERSION: step})
    database.init_db(c)
    assert calls == [1] and database.schema_version(c) == database.SCHEMA_VERSION
    assert c.execute("SELECT last_name, neu_feld FROM customer").fetchone()[:] == ("Bestand", "x")
    assert len(list((tmp_path / "data" / "vor-update").glob("*.sqlite3"))) == 1
    # Schutz-Trigger sind nach dem Umbau wieder da
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] > 10
    database.init_db(c)
    assert calls == [1]                                      # gleiche Version: kein zweiter Lauf
    c.close()


def test_failed_migration_leaves_database_on_old_version(tmp_path, monkeypatch):
    database, c = _old_database(tmp_path, monkeypatch)
    old = database.schema_version(c)

    def step(con):
        database.add_column(con, "customer", "halb_fertig", "TEXT")
        raise RuntimeError("Umbau scheitert")
    monkeypatch.setattr(database, "SCHEMA_VERSION", old + 1)
    monkeypatch.setattr(database, "MIGRATIONS", {old + 1: step})
    with pytest.raises(RuntimeError, match="Umbau scheitert"):
        database.init_db(c)
    assert database.schema_version(c) == old
    assert "halb_fertig" not in {r[1] for r in c.execute("PRAGMA table_info(customer)")}
    assert c.execute("SELECT last_name FROM customer").fetchone()[0] == "Bestand"
    c.close()


def test_new_database_skips_migrations(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database
    monkeypatch.setattr(database, "MIGRATIONS", {v: lambda con: 1 / 0 for v in range(1, database.SCHEMA_VERSION + 1)})
    c = database.connect()
    database.init_db(c)                                      # neue Datenbank entsteht direkt aus schema.sql
    assert database.schema_version(c) == database.SCHEMA_VERSION
    c.close()


def test_every_schema_version_from_3_has_a_migration():
    from app.db import database
    missing = [v for v in range(3, database.SCHEMA_VERSION + 1) if v not in database.MIGRATIONS]
    assert not missing, f"Für Schema-Version {missing} fehlt ein Umbauschritt in database.MIGRATIONS"
    assert all(v <= database.SCHEMA_VERSION for v in database.MIGRATIONS)


# ------------------------------------------------------------------ Jahresabschluss sperrt Zahlungen
def test_closed_year_rejects_payments(con):
    from app.services import expenses, payments
    doc = _finalized(con, _customer(con), on="2025-11-03")
    payments.add_payment(con, doc["id"], "2025-11-20", "100")
    expenses.lock_year(con, 2025, today=date(2026, 3, 1))
    before = expenses.euer_values(con, 2025)["income_cents"]
    with pytest.raises(ValueError, match="2025 ist abgeschlossen"):
        payments.add_payment(con, doc["id"], "2025-12-30", "50")
    with pytest.raises(ValueError, match="abgeschlossen"):
        payments.add_payment(con, doc["id"], "2025-12-30", -100)          # auch keine Korrekturbuchung
    with pytest.raises(sqlite3.IntegrityError, match="abgeschlossen"):     # an der Programmlogik vorbei
        con.execute("INSERT INTO payment(document_id, pay_date, amount_cents) VALUES (?, '2025-12-31', 1)", (doc["id"],))
    assert expenses.euer_values(con, 2025)["income_cents"] == before == 10000
    payments.add_payment(con, doc["id"], "2026-01-05", "620")              # späterer Eingang: mit echtem Datum
    assert payments.open_amount(con, doc["id"]) == 0


def test_import_as_paid_into_closed_year_is_refused(con, tmp_path):
    from test_core import _make_ods
    from app import config
    from app.db.database import company
    from app.importers import legacy_invoices
    from app.services import expenses
    expenses.lock_year(con, 2025, today=date(2026, 3, 1))
    inv = legacy_invoices.parse_file(_make_ods(tmp_path / "Rechnung-2025-0905.ods"), company(con))
    lines = [{"title": "Alt", "description": "", "quantity": "1", "unit": "pauschal", "unit_price_cents": 5000}]
    kw = {"number": "2025-0905", "doc_type": "rechnung", "doc_date": "2025-09-05", "service_date": "2025-09-05",
          "due_date": None, "customer_id": _customer(con), "lines": lines}
    with pytest.raises(ValueError, match="abgeschlossen"):
        legacy_invoices.import_parsed(con, inv, paid=True, **kw)
    assert not list((config.archive_dir() / "import").rglob("*.ods"))      # nichts halb Importiertes im Archiv
    assert legacy_invoices.import_parsed(con, inv, paid=False, **kw)       # unbezahlt geht weiterhin


# ------------------------------------------------------------------ Rundung
def test_net_share_of_partial_payment_rounds_half_up(con):
    """14 Cent auf eine Rechnung über 1,00 netto / 1,12 brutto sind 12,5 Cent netto → 13 (nicht 12 wie round())."""
    from app.services import documents, payments
    d = documents.new_draft(con, "rechnung", _customer(con))
    d.update(doc_date="2026-10-01", service_date="2026-10-01", tax_mode="regel", notes_text="")
    did = documents.save_draft(con, d, [{"title": "X", "quantity": "1", "unit": "Stück", "unit_price_cents": 100,
                                         "tax_rate": "12"}])
    doc = documents.finalize(con, did, pdf_writer=_fake_pdf)
    assert (doc["total_net_cents"], doc["total_gross_cents"]) == (100, 112)
    payments.add_payment(con, did, "2026-10-02", 14)
    part = payments.payment_parts(con, 2026)[0]
    assert part["revenue_cents"] == 14 and part["revenue_net_cents"] == 13


# ------------------------------------------------------------------ Abgleich Belege ↔ Protokoll
def test_documents_are_checked_against_log(con, tmp_path):
    from test_core import _make_ods
    from app.db.database import company
    from app.importers import legacy_invoices
    from app.services import documents
    cid = _customer(con)
    doc = _finalized(con, cid, on="2026-10-01")
    documents.create_storno(con, doc["id"], pdf_writer=_fake_pdf)
    inv = legacy_invoices.parse_file(_make_ods(tmp_path / "Rechnung-2026-0905.ods"), company(con))
    legacy_invoices.import_parsed(con, inv, number="2026-0905", doc_type="rechnung", doc_date="2026-09-05",
                                  service_date="2026-09-05", due_date=None, customer_id=cid,
                                  lines=[{"title": "Alt", "quantity": "1", "unit": "pauschal", "unit_price_cents": 5000}])
    _invoice(con, cid)                                                      # Entwurf: zählt nicht
    assert documents.verify_against_log(con) == []

    # Eingriff an den Sperren vorbei: Trigger entfernen, Betrag und Prüfsumme ändern
    con.execute("DROP TRIGGER trg_document_no_update_final")
    con.execute("DROP TRIGGER trg_document_no_delete_final")
    con.execute("UPDATE document SET total_gross_cents = 1, pdf_sha256 = 'x' WHERE id = ?", (doc["id"],))
    con.execute("UPDATE document SET import_sha256 = 'y' WHERE number = '2026-0905'")
    problems = documents.verify_against_log(con)
    assert any("2026-1001: Betrag" in p for p in problems) and any("2026-1001: PDF-Prüfsumme" in p for p in problems)
    assert any("2026-0905: Prüfsumme der Originaldatei" in p for p in problems)
    # Festschreibung zurückgenommen bzw. Beleg ohne Protokolleintrag eingeschleust
    con.execute("UPDATE document SET status = 'entwurf' WHERE doc_type = 'storno'")
    con.execute("INSERT INTO document(doc_type, number, status, finalized_at) "
                "VALUES ('rechnung', '2026-1099', 'festgeschrieben', datetime('now','localtime'))")
    problems = documents.verify_against_log(con)
    assert any("laut Protokoll festgeschrieben" in p for p in problems)
    assert any("2026-1099: kein Festschreibe-Eintrag" in p for p in problems)


def test_documents_older_than_the_log_are_not_reported(con):
    from app.services import documents
    con.execute("DROP TRIGGER trg_line_no_insert_final")
    con.execute("INSERT INTO document(doc_type, number, status, finalized_at) "
                "VALUES ('rechnung', '2019-0101', 'festgeschrieben', '2019-01-05 10:00:00')")
    _finalized(con, _customer(con), on="2026-10-01")
    assert documents.verify_against_log(con) == []


# ------------------------------------------------------------------ E-Rechnung
def test_seller_identifier_without_vat_id(con):
    """EN 16931 BR-CO-26: ohne USt-IdNr. wird die Steuernummer zusätzlich als Verkäuferkennung ausgegeben."""
    from app.db.database import set_setting
    from app.output.zugferd import build_cii_xml
    from app.services import documents
    cid = _customer(con)

    def seller_xml():
        did = _invoice(con, cid, on="2026-10-01", zugferd=1)
        doc = documents.get_document(con, did)
        data = documents.build_render_data(con, doc, documents.get_lines(con, did), "2026-1001", final=True)
        xml = build_cii_xml(data).decode("utf-8")
        return xml[xml.index("<ram:SellerTradeParty>"):xml.index("</ram:SellerTradeParty>")]
    assert seller_xml().startswith("<ram:SellerTradeParty><ram:ID>12/345/67890</ram:ID><ram:Name>")
    set_setting(con, "company.vat_id", "DE123456789")
    xml = seller_xml()
    assert "<ram:SellerTradeParty><ram:Name>" in xml and 'schemeID="VA">DE123456789' in xml


def test_einvoice_samples_tool(tmp_path, monkeypatch):
    """Das CI-Werkzeug erzeugt drei XSD-gültige Belege und lässt echte Daten in Ruhe."""
    import facturx
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import make_einvoice_samples
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "nicht-benutzt"))
    assert make_einvoice_samples.main([]) == 2
    assert make_einvoice_samples.main([str(tmp_path / "out")]) == 0
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == ["kleinunternehmer.pdf", "regelbesteuerung.pdf", "storno.pdf"]
    for name in files:
        _, xml = facturx.get_xml_from_pdf((tmp_path / "out" / name).read_bytes(), check_xsd=True)
        assert (b"<ram:TypeCode>381<" in xml) == (name == "storno.pdf")
    assert not (tmp_path / "nicht-benutzt" / "faktura.sqlite3").exists()
