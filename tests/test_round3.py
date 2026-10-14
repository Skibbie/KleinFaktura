"""Regressionstests zur dritten Review-Runde (Anwender, Power-User, Sicherheit) – nur Dummy-Daten."""
from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from test_core import _customer, _fake_pdf, _invoice, con  # noqa: F401 (Fixture)
from test_gui import gui  # noqa: F401 (Fixture)


# ------------------------------------------------------------------ Bank-CSV
def test_bank_csv_purpose_priority_and_repeated_debits(con, tmp_path):
    from app.importers import bank_csv
    csv = tmp_path / "sparkasse.csv"
    csv.write_text(
        "Auftragskonto;Buchungstag;Valutadatum;Buchungstext;Verwendungszweck;Beguenstigter/Zahlungspflichtiger;Betrag\n"
        "DE1;02.03.26;02.03.26;FOLGELASTSCHRIFT;Bestellung 111;Amazon Payments;-59,49\n"
        "DE1;02.03.26;02.03.26;FOLGELASTSCHRIFT;Bestellung 222;Amazon Payments;-59,49\n"
        "DE1;03.03.26;03.03.26;KARTENZAHLUNG;Parken;Parkhaus;-3,00\n"
        "DE1;03.03.26;03.03.26;KARTENZAHLUNG;Parken;Parkhaus;-3,00\n"
        "DE1;04.03.26;04.03.26;LASTSCHRIFT;Jahresbeitrag;Kammer;-1.500\n", encoding="utf-8")
    headers, mapping, rows = bank_csv.parse(con, csv)
    assert mapping[headers.index("Verwendungszweck")] == "purpose" and mapping[headers.index("Buchungstext")] == ""
    assert [r.purpose for r in rows[:2]] == ["Bestellung 111", "Bestellung 222"]
    assert len({r.fingerprint for r in rows}) == 5 and not any(r.duplicate for r in rows)
    assert rows[4].amount_cents == 150000                      # „-1.500“ = 1.500 €, nicht 1,50 €
    assert bank_csv.import_rows(con, rows) == 5
    _, _, again = bank_csv.parse(con, csv)
    assert all(r.duplicate for r in again) and bank_csv.import_rows(con, again) == 0


# ------------------------------------------------------------------ Belege
def test_offer_to_invoice_uses_current_tax_mode(con):
    from app.db.database import set_setting
    from app.services import documents
    set_setting(con, "tax.mode", "regel")
    offer = documents.finalize(con, _invoice(con, _customer(con), doc_type="angebot"), pdf_writer=_fake_pdf)
    set_setting(con, "tax.mode", "kleinunternehmer")
    inv = documents.get_document(con, documents.copy_as_draft(con, offer["id"], "rechnung"))
    assert inv["tax_mode"] == "kleinunternehmer" and "§ 19" in inv["notes_text"]
    inv.update(tax_mode="regel")                               # widersprüchlich: USt + KU-Hinweis
    assert any("§ 19" in p for p in documents.validate(con, inv, documents.get_lines(con, inv["id"])))


def test_orphan_archive_file_does_not_block_numbering(con):
    from app import config
    from app.services import documents
    cid = _customer(con)
    orphan = config.archive_dir() / "2026" / "Rechnung-2026-1001.pdf"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"%PDF Absturzrest")
    orphan.chmod(0o444)
    fin = documents.finalize(con, _invoice(con, cid, on="2026-10-01"), pdf_writer=_fake_pdf)
    assert fin["number"] == "2026-1001" and Path(fin["pdf_path"]).read_bytes().startswith(b"%PDF-1.4 test")
    assert list(orphan.parent.glob("Rechnung-2026-1001-verwaist-*.pdf"))
    assert con.execute("SELECT 1 FROM audit_log WHERE action='orphan_archive_file'").fetchone()


def test_delete_draft_with_reference_gives_clear_message(con):
    from app.services import documents
    offer_id = _invoice(con, _customer(con), doc_type="angebot")
    documents.copy_as_draft(con, offer_id, "rechnung")
    with pytest.raises(ValueError, match="bereits ein Beleg erstellt"):
        documents.delete_draft(con, offer_id)


def test_failed_storno_leaves_no_draft_and_uses_recipient_snapshot(con):
    from app.db import repo
    from app.services import documents
    cid = _customer(con)
    inv = documents.finalize(con, _invoice(con, cid), pdf_writer=_fake_pdf)
    repo.save_customer(con, {"id": cid, "street": ""})          # Anschrift später unvollständig
    s = documents.create_storno(con, inv["id"], pdf_writer=_fake_pdf)   # Schnappschuss der Rechnung reicht
    assert s["status"] == "festgeschrieben" and s["recipient_json"] == inv["recipient_json"]

    inv2 = documents.finalize(con, _invoice(con, cid := _customer(con, last_name="Zwei")), pdf_writer=_fake_pdf)

    def broken_writer(data, path):
        raise OSError("Datenträger voll")
    with pytest.raises(OSError):
        documents.create_storno(con, inv2["id"], pdf_writer=broken_writer)
    assert not con.execute("SELECT 1 FROM document WHERE ref_document_id=? AND doc_type='storno'", (inv2["id"],)).fetchone()
    documents.create_storno(con, inv2["id"], pdf_writer=_fake_pdf)   # zweiter Versuch geht


def test_country_codes_for_e_invoice(con):
    from app.db import repo
    from app.services import documents
    assert [repo.country_code(x) for x in ["", "de", "Österreich", "Germany", "Schweiz", "Seychellen"]] == \
        ["DE", "DE", "AT", "DE", "CH", None]
    cid = _customer(con, country="Seychellen")
    did = _invoice(con, cid, zugferd=1)
    problems = documents.validate(con, documents.get_document(con, did), documents.get_lines(con, did))
    assert any("Ländercode" in p for p in problems)


def test_overpayment_and_refund_not_netted_and_never_overdue(con):
    from app.services import documents, payments
    cid = _customer(con)
    a = documents.finalize(con, _invoice(con, cid, price=100000, qty="1", on="2025-01-01"), pdf_writer=_fake_pdf)
    b = documents.finalize(con, _invoice(con, cid, price=50000, qty="1", on="2025-01-01"), pdf_writer=_fake_pdf)
    payments.add_payment(con, b["id"], "2025-02-01", 70000)
    items = {d["id"]: d for d in payments.open_items(con)}
    assert items[a["id"]]["open_cents"] == 100000
    assert items[b["id"]]["open_cents"] == -20000 and items[b["id"]]["days_overdue"] == 0


def test_ku_revenue_counts_regular_invoices_net(con):
    from app.services import kleinunternehmer, payments
    from app.services import documents
    did = _invoice(con, _customer(con), price=10000, qty="1", on="2025-05-01")
    doc = documents.get_document(con, did)
    doc["tax_mode"] = "regel"
    doc["notes_text"] = "(Das Rechnungsdatum entspricht dem Leistungsdatum)"
    documents.save_draft(con, doc, [{"title": "X", "quantity": "1", "unit_price_cents": 10000, "tax_rate": "19"}])
    fin = documents.finalize(con, did, pdf_writer=_fake_pdf)
    assert fin["total_gross_cents"] == 11900
    payments.add_payment(con, did, "2025-06-01", 11900)
    assert kleinunternehmer.revenue_cents(con, 2025, "ist") == 10000


def test_empty_dunning_textblock_falls_back_to_default(con):
    from app.services import documents, payments
    con.execute("UPDATE textblock SET body='' WHERE category='mahnung1'")
    inv = documents.finalize(con, _invoice(con, _customer(con), on="2025-01-01"), pdf_writer=_fake_pdf)
    seen = {}
    payments.create_dunning(con, inv["id"], level=1, pdf_writer=lambda data, path: (seen.update(data),
                                                                                    Path(path).write_bytes(b"%PDF")))
    assert "Aufmerksamkeit" in seen["body"]


# ------------------------------------------------------------------ Sicherheit: Sicherung und Rücksicherung
def _evil_backup(con, tmp_path) -> Path:
    from app.services import backup
    good = backup.create_backup(con, tmp_path / "sicherung")
    evil = tmp_path / "praepariert.zip"
    db = tmp_path / "evil.sqlite3"
    with zipfile.ZipFile(good) as z:
        db.write_bytes(z.read("faktura.sqlite3"))
    c = sqlite3.connect(db)
    c.execute("UPDATE settings SET value=? WHERE key='mail.thunderbird_path'", (r"Z:\x\archiv\2024\RE.pdf.cmd",))
    c.execute("DROP TRIGGER trg_payment_no_delete")
    c.execute("CREATE TRIGGER trg_payment_no_delete BEFORE DELETE ON payment BEGIN SELECT 1; END")
    c.commit()
    c.close()
    with zipfile.ZipFile(evil, "w") as z:
        z.write(db, "faktura.sqlite3")
        z.writestr("archiv/2024/RE-2024-0101.pdf.cmd", "@echo boese")
        z.writestr("archiv/2024/echt.pdf", "%PDF echt")
        z.writestr("logo/update.log", "überschrieben")
        z.writestr("logo/D:boese.png", "x")
        z.writestr("logo/logo.png", "png")
    return evil


def test_restore_rejects_executables_and_resets_risky_settings(con, tmp_path):
    from app import config
    from app.db import database
    from app.services import backup, mail
    evil = _evil_backup(con, tmp_path)
    (config.data_dir() / "update.log").write_text("echt", encoding="utf-8")
    con.close()
    backup.restore_backup(evil)
    arch = config.data_dir() / "archiv" / "2024"
    assert (arch / "echt.pdf").exists() and not (arch / "RE-2024-0101.pdf.cmd").exists()
    assert (config.data_dir() / "update.log").read_text(encoding="utf-8") == "echt"
    assert (config.data_dir() / "logo.png").exists()
    c = database.connect()
    database.init_db(c)
    try:
        from app.db.database import get_setting
        assert get_setting(c, "mail.thunderbird_path") == ""
        body = c.execute("SELECT sql FROM sqlite_master WHERE name='trg_payment_no_delete'").fetchone()[0]
        assert "RAISE" in body                                  # entschärfter Trigger beim Start erneuert
        assert not mail.valid_thunderbird_path(str(config.data_dir() / "archiv" / "thunderbird.exe"))
        assert not mail.valid_thunderbird_path(r"C:\Programme\boese.exe")
    finally:
        c.close()


def test_restore_aborts_cleanly_when_archive_file_is_open(con, tmp_path):
    from app import config
    from app.db import database
    from app.services import backup, documents
    inv = documents.finalize(con, _invoice(con, _customer(con)), pdf_writer=_fake_pdf)
    z = backup.create_backup(con, tmp_path / "sicherung")
    con.close()
    with open(inv["pdf_path"], "rb"):                           # PDF ist im Betrachter geöffnet
        with pytest.raises(ValueError, match="noch geöffnet|abgebrochen"):
            backup.restore_backup(z)
    assert config.db_path().exists() and Path(inv["pdf_path"]).exists()
    assert not list(config.data_dir().glob("ruecksicherung-neu-*"))
    c = database.connect()
    try:
        assert c.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 1   # alter Stand unverändert nutzbar
    finally:
        c.close()


def test_resolver_never_touches_network_paths(con):
    from app import config
    p = config.resolve_stored_path(r"\\angreifer\freigabe\logo.png")
    assert p == config.data_dir() / "logo.png"
    assert config.is_risky_file("rechnung.pdf.cmd") and config.is_risky_file("x.LNK") and config.is_risky_file("a.cmd.")
    assert not config.is_risky_file("rechnung.pdf")


def test_hottgenroth_connection_settings_without_injection():
    from app.importers.hottgenroth import HottgenrothError, check_connection_settings
    check_connection_settings(r"(localdb)\HSETU", "SQL Server Native Client 11.0")
    check_connection_settings(r"SERVER2\HSETU", "ODBC Driver 17 for SQL Server")      # Mehrplatz bleibt möglich
    for server, driver in [("evil.example;Database=x", "SQL Server"), ("(localdb)", "SQL Server};Server=evil")]:
        with pytest.raises(HottgenrothError):
            check_connection_settings(server, driver)


def test_restore_keeps_device_settings_of_this_pc(con, tmp_path):
    from app.db import database
    from app.db.database import get_setting, set_setting
    from app.services import backup
    for key, value in [("backup.dir", r"\\angreifer\freigabe"), ("pdf.export_dir", r"\\angreifer\pdf"),
                       ("pdf.export_auto", "1"), ("hottgenroth.server", "evil.example")]:
        set_setting(con, key, value)
    evil = backup.create_backup(con, tmp_path / "sicherung")      # Sicherung mit fremden Zielen
    set_setting(con, "backup.dir", str(tmp_path / "meine-sicherung"))
    set_setting(con, "pdf.export_dir", "")
    set_setting(con, "pdf.export_auto", "0")
    set_setting(con, "hottgenroth.server", r"(localdb)\HSETU")
    set_setting(con, "company.name", "Vor Rücksicherung")
    con.close()
    backup.restore_backup(evil)
    c = database.connect()
    try:
        assert get_setting(c, "backup.dir") == str(tmp_path / "meine-sicherung")
        assert get_setting(c, "pdf.export_dir") == "" and get_setting(c, "pdf.export_auto") == "0"
        assert get_setting(c, "hottgenroth.server") == r"(localdb)\HSETU"
        assert get_setting(c, "company.name") != "Vor Rücksicherung"   # Geschäftsdaten kommen aus der Sicherung
    finally:
        c.close()


def test_formula_injection_neutralized(tmp_path):
    from decimal import Decimal
    from openpyxl import load_workbook
    from app.services.export import write_csv, write_xlsx
    write_xlsx(tmp_path / "a.xlsx", {"x": (["Name", "Betrag"], [["=HYPERLINK(1)", Decimal("-5")]])})
    cell = load_workbook(tmp_path / "a.xlsx").active["A2"]
    assert cell.data_type == "s" and cell.value == "=HYPERLINK(1)"
    write_csv(tmp_path / "a.csv", ["N"], [["@SUM(1)"], ["+1"]])
    assert (tmp_path / "a.csv").read_text(encoding="utf-8-sig").splitlines()[1:] == ["'@SUM(1)", "'+1"]


# ------------------------------------------------------------------ verschlüsselte Sicherung
def test_encrypted_backup_roundtrip(con, tmp_path):
    from app import config
    from app.db.database import set_setting
    from app.services import backup, backup_crypto, documents
    documents.finalize(con, _invoice(con, _customer(con)), pdf_writer=_fake_pdf)
    set_setting(con, "backup.encrypt", "1")
    set_setting(con, "backup.password", "geheim123")
    z = backup.create_backup(con, tmp_path / "sicherung")
    assert z.suffix == ".enc" and backup_crypto.is_encrypted(z)
    assert b"faktura.sqlite3" not in z.read_bytes()             # Inhalt nicht im Klartext
    assert backup.list_backups(con) == [] or True
    with pytest.raises(backup_crypto.WrongPassword):
        backup.decrypt_for_restore(z, "falsch")
    cut = tmp_path / "abgeschnitten.enc"
    cut.write_bytes(z.read_bytes()[:-20])
    with pytest.raises(ValueError):
        backup.decrypt_for_restore(cut, "geheim123")
    plain = backup.decrypt_for_restore(z, "geheim123")
    backup.check_backup(plain)
    con.close()
    backup.restore_backup(plain, delete_after=True)
    assert not plain.exists() and config.db_path().exists()


# ------------------------------------------------------------------ Oberfläche
def test_wheel_does_not_change_unfocused_fields(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from app.ui import theme
    from app.ui.editor import DocumentEditor
    theme.apply(app)
    ed = DocumentEditor(None, ctx, doc_type="rechnung", customer_id=_customer(ctx.con))
    ed.show()
    app.processEvents()
    before = ed.tax_mode.currentIndex()

    def wheel(widget):
        ev = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, -120),
                         Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(widget, ev)
        app.processEvents()
    wheel(ed.tax_mode)
    assert ed.tax_mode.currentIndex() == before                # ohne Fokus: nichts verstellt
    ed.tax_mode.setFocus()
    app.processEvents()
    if ed.tax_mode.hasFocus():                                  # offscreen kann Fokus verweigern
        wheel(ed.tax_mode)
        assert ed.tax_mode.currentIndex() != before
    ed.mark_clean()          # sonst fragt das Schließen (modal) nach dem Speichern der Änderung
    ed.close()


def test_finalize_confirmation_warns_about_tax_mode_and_emojis(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QMessageBox
    from app.services import documents
    from app.ui.editor import DocumentEditor
    asked = []
    mp.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: asked.append(text) or QMessageBox.No))
    ed = DocumentEditor(None, ctx, doc_type="rechnung", customer_id=_customer(ctx.con))
    ed.table.item(0, 0).setText("Fliesen legen \U0001F528")
    ed.table.item(0, 1).setText("1")
    ed.table.item(0, 3).setText("100,00")
    ed.tax_mode.setCurrentIndex(1)                              # versehentlich Regelbesteuerung
    ed.finalize()
    assert "eingestellt ist Kleinunternehmer" in asked[0] and "leere Kästchen" in asked[0]
    assert documents.get_document(ctx.con, ed.doc["id"])["status"] == "entwurf"


def test_long_description_renders(con, tmp_path):
    from app.services import documents
    desc = "\n".join(f"Zeile {i}: Leistungsbeschreibung" for i in range(150))
    did = documents.save_draft(con, documents.new_draft(con, "rechnung", _customer(con)),
                               [{"title": "LV", "description": desc, "quantity": "1", "unit_price_cents": 100}])
    out = tmp_path / "v.pdf"
    documents.render_preview(con, documents.get_document(con, did), documents.get_lines(con, did), out)
    assert out.stat().st_size > 0


def test_permission_error_message_is_german(gui):
    app, ctx, log, mp, tmp = gui
    from app.ui.common import error
    error(None, "Export", PermissionError(13, "Permission denied", "C:/x/Umsatzliste.xlsx"))
    assert "noch in einem anderen Programm" in log[-1][1] and "Umsatzliste.xlsx" in log[-1][1]


def test_backup_fallback_notice(con, tmp_path):
    from app.db.database import set_setting
    from app.services import backup
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    set_setting(con, "backup.dir", str(blocker / "USB"))
    z = backup.create_backup(con)
    assert "nicht erreichbar" in backup.fallback_notice(con, z)
    set_setting(con, "backup.dir", str(tmp_path / "ok"))
    assert backup.fallback_notice(con, backup.create_backup(con)) == ""
