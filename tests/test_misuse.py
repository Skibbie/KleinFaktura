"""Regressionstests zur Fehlbedienung (zweite Review-Runde, nur Dummy-Daten)."""
from __future__ import annotations

import os
import sqlite3
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from test_core import _customer, _fake_pdf, _invoice, con  # noqa: F401 (Fixture)
from test_gui import gui  # noqa: F401 (Fixture)


# ------------------------------------------------------------------ Festschreiben: Plausibilität
def test_plausibility_warnings(con):
    from app.services import documents
    doc = {"doc_type": "rechnung", "tax_mode": "kleinunternehmer", "doc_date": "2026-10-13"}
    ok = [{"title": "Beratung", "quantity": "2", "unit_price_cents": 5000}]
    today = date(2026, 10, 13)
    assert documents.plausibility_warnings(doc, ok, today) == []
    zero = [{"title": "Beratung", "quantity": "1", "unit_price_cents": 0}]
    w = documents.plausibility_warnings(doc, zero, today)
    assert any("Gesamtbetrag" in x for x in w) and any("Position 1" in x for x in w)
    assert any("Zukunft" in x for x in documents.plausibility_warnings({**doc, "doc_date": "2062-10-13"}, ok, today))
    assert any("drei Monate" in x for x in documents.plausibility_warnings({**doc, "doc_date": "2025-01-02"}, ok, today))
    storno = {**doc, "doc_type": "storno"}
    assert documents.plausibility_warnings(storno, [{"title": "x", "quantity": "-1", "unit_price_cents": 100}], today) == []


def test_invoices_for_offer(con):
    from app.services import documents
    offer = documents.finalize(con, _invoice(con, _customer(con), doc_type="angebot"), pdf_writer=_fake_pdf)
    assert documents.invoices_for_offer(con, offer["id"]) == []
    documents.copy_as_draft(con, offer["id"], "rechnung")
    assert [i["status"] for i in documents.invoices_for_offer(con, offer["id"])] == ["entwurf"]


# ------------------------------------------------------------------ Jahresabschluss
def test_cannot_lock_current_or_future_year(con):
    from app.services import expenses
    with pytest.raises(ValueError, match="noch nicht vorbei"):
        expenses.lock_year(con, 2026, today=date(2026, 10, 13))
    with pytest.raises(ValueError):
        expenses.lock_year(con, 2027, today=date(2026, 10, 13))
    assert not expenses.is_locked(con, 2026)
    expenses.lock_year(con, 2025, today=date(2026, 10, 13))
    assert expenses.is_locked(con, 2025)


# ------------------------------------------------------------------ Ordner im Archiv
def test_export_and_backup_refuse_archive_folders(con, tmp_path):
    from app import config
    from app.db.database import set_setting
    from app.services import backup, documents, mail
    doc = documents.finalize(con, _invoice(con, _customer(con), on="2026-10-01"), pdf_writer=_fake_pdf)
    set_setting(con, "pdf.export_auto", "1")
    set_setting(con, "pdf.export_dir", str(config.archive_dir()))
    with pytest.raises(ValueError, match="Archiv"):
        mail.export_copy(con, doc)
    assert not os.access(doc["pdf_path"], os.W_OK)             # Schreibschutz unangetastet
    with pytest.raises(ValueError, match="Archiv"):
        backup.create_backup(con, config.archive_dir() / "sicherung")
    assert config.inside_archive(config.archive_dir() / "2026") and not config.inside_archive(tmp_path)


# ------------------------------------------------------------------ Rücksicherung
def test_check_backup_rejects_newer_and_broken_files(con, tmp_path):
    from app.db.database import SCHEMA_VERSION
    from app.services import backup
    good = backup.create_backup(con, tmp_path / "sicherung")
    backup.check_backup(good)
    newer_db = tmp_path / "faktura.sqlite3"
    c = sqlite3.connect(newer_db)
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    c.close()
    newer = tmp_path / "neuer.zip"
    with zipfile.ZipFile(newer, "w") as z:
        z.write(newer_db, "faktura.sqlite3")
    with pytest.raises(ValueError, match="neueren Programmversion"):
        backup.check_backup(newer)
    with pytest.raises(ValueError, match="neueren Programmversion"):
        backup.restore_backup(newer)                             # bricht ab, bevor etwas verschoben wird
    broken = tmp_path / "kaputt.zip"
    broken.write_bytes(b"kein zip")
    with pytest.raises(ValueError, match="keine lesbare Sicherung"):
        backup.check_backup(broken)


# ------------------------------------------------------------------ Kunden / Eingaben
def test_reactivate_customer(con):
    from app.db import repo
    from app.services import documents
    cid = _customer(con)
    _invoice(con, cid)
    repo.deactivate_customer(con, cid)
    assert cid not in [c["id"] for c in repo.list_customers(con)]
    repo.reactivate_customer(con, cid)
    assert cid in [c["id"] for c in repo.list_customers(con)]
    assert documents.list_documents(con)[0]["customer_id"] == cid


def test_tax_rate_with_percent_sign():
    from app.money import input_decimal
    assert input_decimal("19 %") == Decimal("19") and input_decimal("7%") == Decimal("7")
    assert input_decimal("7,5 %") == Decimal("7.5")


# ------------------------------------------------------------------ GUI
def _questions(mp, answer):
    """QMessageBox.question aufzeichnen und mit `answer` beantworten."""
    from PySide6.QtWidgets import QMessageBox
    asked = []
    mp.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, *a, **k: asked.append((title, text)) or (QMessageBox.Yes if answer else QMessageBox.No)))
    return asked


def _editor(ctx, cid, price="100,00", tax=None):
    from app.ui.editor import DocumentEditor
    ed = DocumentEditor(None, ctx, doc_type="rechnung", customer_id=cid)
    ed.table.item(0, 0).setText("Beratung")
    ed.table.item(0, 1).setText("1")
    ed.table.item(0, 3).setText(price)
    if tax is not None:
        ed.tax_mode.setCurrentIndex(1)
        ed.table.item(0, 4).setText(tax)
    return ed


def test_editor_tax_input_and_zero_invoice(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import documents
    cid = _customer(ctx.con)
    ed = _editor(ctx, cid, tax="7%")
    assert ed.save(quiet=True)
    assert documents.get_lines(ctx.con, ed.doc["id"])[0]["tax_rate"] == "7"
    assert ed.doc["total_gross_cents"] == 10700
    ed.table.item(0, 4).setText("sieben")
    assert not ed.save(quiet=True)
    assert log[-1][0] == "critical" and "Position 1" in log[-1][1]

    asked = _questions(mp, False)                                # Rückfrage mit „Nein“ beantworten
    ed = _editor(ctx, cid, price="0,00")
    ed.finalize()
    assert asked and asked[0][0] == "Bitte prüfen" and "Gesamtbetrag" in asked[0][1]
    assert documents.get_document(ctx.con, ed.doc["id"])["status"] == "entwurf"


def test_list_actions_ask_before_irreversible_steps(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import documents, payments
    from app.ui.documents_page import DocumentsPage, PaymentDialog, make_dunning
    cid = _customer(ctx.con)
    offer = documents.finalize(ctx.con, _invoice(ctx.con, cid, doc_type="angebot"), pdf_writer=_fake_pdf)
    documents.copy_as_draft(ctx.con, offer["id"], "rechnung")
    asked = _questions(mp, False)
    page = DocumentsPage(ctx, "angebot")
    page.table.selectRow(0)
    page.convert()
    assert "bereits eine Rechnung" in asked[-1][1]
    assert len(documents.invoices_for_offer(ctx.con, offer["id"])) == 1

    inv = documents.finalize(ctx.con, _invoice(ctx.con, cid, price=10000, qty="1"), pdf_writer=_fake_pdf)
    assert inv["due_date"] >= date.today().isoformat()
    make_dunning(None, ctx, inv["id"])
    assert "erst am" in asked[-1][1] and payments.dunning_history(ctx.con, inv["id"]) == []

    dlg = PaymentDialog(None, ctx, inv)
    dlg.amount.setText("1.000,00")                               # Tippfehler: 1000 statt 100
    dlg.save()
    assert "höher als der offene Betrag" in asked[-1][1] and payments.paid_cents(ctx.con, inv["id"]) == 0
    dlg.amount.setText("100,00")
    n = len(asked)
    dlg.save()
    assert len(asked) == n and payments.paid_cents(ctx.con, inv["id"]) == 10000


def test_settings_reject_archive_folder_and_lock_current_year(gui):
    app, ctx, log, mp, tmp = gui
    from app import config
    from app.services import expenses
    from app.ui.expenses_page import EuerPage
    from app.ui.settings_page import SettingsPage
    sp = SettingsPage(ctx)
    sp.edits["pdf.export_dir"].setText(str(config.archive_dir() / "kopien"))
    assert sp.save(quiet=True) is False and "Beleg-Archiv" in log[-1][1]
    tab = EuerPage(ctx)
    tab.year.setCurrentIndex(tab.year.findData(date.today().year))
    tab.lock()
    assert "noch nicht vorbei" in log[-1][1] and not expenses.is_locked(ctx.con, date.today().year)


def test_customer_page_reactivate(gui):
    app, ctx, log, mp, tmp = gui
    from app.db import repo
    from app.ui.customers import CustomersPage
    cid = _customer(ctx.con)
    _invoice(ctx.con, cid)
    repo.deactivate_customer(ctx.con, cid)
    page = CustomersPage(ctx)
    assert page.table.rowCount() == 0
    page.show_inactive.setChecked(True)
    assert page.table.rowCount() == 1 and "inaktiv" in page.table.item(0, 7).text()
    page.table.selectRow(0)
    page.reactivate()
    assert repo.get_customer(ctx.con, cid)["active"] == 1
