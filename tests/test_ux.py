"""UX-Feinschliff: Knopf-Freigabe, Anrede-Vorschau, Reiter, Texte (nur Dummy-Daten)."""
from __future__ import annotations

from test_core import _customer, _fake_pdf, _invoice
from test_gui import gui  # noqa: F401 (Fixture)


def _select(page, number_text):
    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == number_text:
            page.table.selectRow(r)
            return
    raise AssertionError(number_text)


def test_invoice_buttons_follow_selection(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import documents
    from app.ui.documents_page import DocumentsPage
    cid = _customer(ctx.con)
    _invoice(ctx.con, cid)                                             # Entwurf
    inv = documents.finalize(ctx.con, _invoice(ctx.con, cid), pdf_writer=_fake_pdf)
    page = DocumentsPage(ctx, "rechnung")
    b = page.sel_buttons
    assert not any(btn.isEnabled() for btn in b.values())            # nichts markiert
    _select(page, "(Entwurf)")
    assert b["open"].isEnabled() and b["delete"].isEnabled()
    assert not b["cancel"].isEnabled() and not b["payment"].isEnabled() and not b["pdf"].isEnabled()
    assert "Entwürfe" in b["pdf"].toolTip()
    row = next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).text() == "(Entwurf)")
    assert page.table.item(row, 5).text() == ""                       # Entwurf: kein „Offen 0,00 €“
    _select(page, inv["number"])
    assert b["cancel"].isEnabled() and b["payment"].isEnabled() and b["dunning"].isEnabled()
    assert not b["delete"].isEnabled()


def test_salutation_preview_and_finalize_label(gui):
    app, ctx, log, mp, tmp = gui
    from app.ui.editor import DocumentEditor
    ed = DocumentEditor(None, ctx, doc_type="rechnung")
    assert "gewählten Kunden" in ed.salutation_preview.text()
    ed.customer.select(_customer(ctx.con))
    assert ed.salutation_preview.text() == "Im Brief: Sehr geehrte Frau Mustermann,"
    ed.salutation.setText("Hallo zusammen,")
    assert ed.salutation_preview.text() == ""
    labels = [ed.actions.itemAt(i).widget().text() for i in range(ed.actions.count()) if ed.actions.itemAt(i).widget()]
    assert "Festschreiben (endgültig)…" in labels
    ed.mark_clean()


def test_settings_tabs_and_texts(gui):
    app, ctx, log, mp, tmp = gui
    from app.db.database import set_setting
    from app.ui.expenses_page import EuerPage
    from app.ui.export_page import ExportPage
    from app.ui.settings_page import SettingsPage
    sp = SettingsPage(ctx)
    assert [sp.tabs.tabText(i) for i in range(sp.tabs.count())] == \
        ["Firma", "Steuer und Belege", "E-Mail und PDF", "Datensicherung", "Schnittstellen und Updates", "Hinweise"]
    euer = EuerPage(ctx)
    assert "Kleinunternehmer" in euer.intro.text() and " du " not in euer.intro.text()
    set_setting(ctx.con, "tax.mode", "regel")
    euer.reload()
    assert "eingeschränkte Unterstützung" in euer.intro.text()
    set_setting(ctx.con, "backup.dir", str(tmp / "leer"))           # eigener, leerer Sicherungsordner
    page = ExportPage(ctx, lambda action: None)
    assert page.backups.count() == 1 and "Noch keine Sicherung" in page.backups.item(0).text()


def test_count_helper():
    from app.ui.common import count
    assert count(1, "Ausgabe", "Ausgaben") == "1 Ausgabe" and count(0, "Ausgabe", "Ausgaben") == "0 Ausgaben"


def test_disclaimer_in_settings_and_wizard(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QLabel
    from app import config
    from app.ui.settings_page import SettingsPage
    from app.ui.setup_wizard import SetupWizard
    sp = SettingsPage(ctx)
    assert sp.disclaimer.text() == config.DISCLAIMER
    wiz = SetupWizard(None, ctx)
    first = wiz.page(wiz.pageIds()[0])
    assert any(config.DISCLAIMER in lbl.text() for lbl in first.findChildren(QLabel))
