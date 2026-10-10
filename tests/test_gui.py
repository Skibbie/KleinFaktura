"""Durchklick-Test der Oberfläche (offscreen, Dummy-Daten, Dialoge gemockt)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_core import _make_ods  # noqa: E402


@pytest.fixture()
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
    app = QApplication.instance() or QApplication([])
    log = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: log.append((_n, a[2] if len(a) > 2 else "")) or QMessageBox.Yes))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda p, t, l, items, cur=0, *a, **k: (items[cur], True)))
    monkeypatch.setattr(os, "startfile", lambda *a, **k: None, raising=False)
    from app.db import database
    from app.ui.common import Ctx
    con = database.connect()
    database.init_db(con)
    from test_core import _dummy_company
    _dummy_company(con)
    ctx = Ctx(con)
    yield app, ctx, log, monkeypatch, tmp_path
    # Fenster und Dialoge ohne Elternfenster jetzt abräumen: bleiben sie bis zum Interpreter-Ende liegen,
    # stürzt PySide6 ab 6.12 beim Beenden ab (Exit-Code ≠ 0 trotz bestandener Tests)
    from PySide6.QtCore import QCoreApplication, QEvent
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    con.close()


def test_click_through(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QFileDialog
    from app.db import repo
    from app.services import documents, payments
    from app.ui import import_dialogs
    from app.ui.customers import CustomerDialog
    from app.ui.documents_page import DocumentsPage, PaymentDialog, make_dunning
    from app.ui.editor import DocumentEditor
    from app.ui.main_window import MainWindow
    from app.ui.settings_page import SettingsPage
    from app.ui.texts_page import ArticleTab, TextblockTab

    win = MainWindow(ctx)
    win._skip_backup = True

    # Kunde manuell anlegen
    cd = CustomerDialog(win, ctx)
    for k, v in {"first_name": "Erika", "last_name": "Mustermann", "street": "Musterweg 12", "zip": "12345",
                 "city": "Musterstadt"}.items():
        cd.fields[k].setText(v)
    cd.salutation.setCurrentText("Frau")
    cd.save()
    cid = cd.customer_id
    assert repo.get_customer(ctx.con, cid)["letter_salutation"] == "Sehr geehrte Frau Mustermann,"

    # Angebot anlegen, festschreiben, in Rechnung umwandeln
    ed = DocumentEditor(win, ctx, doc_type="angebot", customer_id=cid)
    ed.table.item(0, 0).setText("Energieberatung")
    ed.table.item(0, 1).setText("4")
    ed.table.item(0, 3).setText("120,00")
    ed.finalize()
    offer = documents.list_documents(ctx.con, ("angebot",))[0]
    assert offer["number"].startswith("AN") and offer["total_gross_cents"] == 48000

    page = win.pages[win.page_index("angebote")]
    page.reload()
    page.table.selectRow(0)
    page.convert()
    assert win._editor is not None and win.stack.currentWidget() is win._editor_box
    win._editor.finalize()
    assert win._editor is None
    inv = [d for d in documents.list_documents(ctx.con, ("rechnung",)) if d["status"] == "festgeschrieben"][0]
    assert inv["total_gross_cents"] == 48000 and inv["ref_document_id"] == offer["id"]
    assert documents.list_documents(ctx.con, ("angebot",))[0]["status_label"] == "Angenommen"

    # Zahlung + Mahnung
    pd = PaymentDialog(win, ctx, documents.get_document(ctx.con, inv["id"]))
    pd.amount.setText("100,00")
    pd.save()
    assert payments.open_amount(ctx.con, inv["id"]) == 38000
    make_dunning(win, ctx, inv["id"])
    assert payments.dunning_history(ctx.con, inv["id"])[0]["level"] == 1

    # Storno über die Rechnungsseite
    rp = win.pages[win.page_index("rechnungen")]
    rp.reload()
    for r in range(rp.table.rowCount()):
        if rp.table.item(r, 0).text() == inv["number"]:
            rp.table.selectRow(r)
    rp.cancel()
    assert documents.is_cancelled(ctx.con, inv["id"])
    rp.reload()
    # Rechnung + Storno heben sich auf (vorher wurde die stornierte Rechnung doppelt abgezogen)
    assert "1 Rechnungen" in rp.sum_label.text() and "berechnet 0,00 €" in rp.sum_label.text()

    # Einstellungen + Startwert
    sp = SettingsPage(ctx)
    sp.edits["company.bic"].setText("GENODEF1XYZ")
    sp.save()
    from app.db.database import get_setting
    assert get_setting(ctx.con, "company.bic") == "GENODEF1XYZ"
    sp.nr_year.setValue(2030); sp.nr_month.setValue(1); sp.nr_last.setValue(7)
    sp.set_start()
    from datetime import date
    from app.services import numbering
    assert numbering.peek_next(ctx.con, "rechnung", date(2030, 1, 5)) == "2030-0108"

    # Artikel + Textbaustein
    at = ArticleTab(ctx)
    at.new(); at.title.setText("Testleistung"); at.price.setText("99,50"); at.save()
    assert any(a["title"] == "Testleistung" and a["unit_price_cents"] == 9950 for a in repo.list_articles(ctx.con))
    tt = TextblockTab(ctx)
    tt.new(); tt.title.setText("Mein Text"); tt.body.setPlainText("Hallo {kunde}"); tt.save()
    assert any(t["title"] == "Mein Text" for t in repo.list_textblocks(ctx.con))

    # CSV-Import (Dublette -> Kollisionsdialog: Überspringen)
    csvp = tmp / "kunden.csv"
    csvp.write_text("Vorname;Nachname;Straße;PLZ;Ort\nErika;Mustermann;Musterweg 12;12345;Musterstadt\n"
                    "Max;Neu;Weg 1;11111;Ort\n", encoding="utf-8")
    mp.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(csvp), "")))
    mp.setattr(import_dialogs.CsvMappingDialog, "exec", lambda self: 1)

    def coll_exec(self):
        self._done("skip")
        return 1
    mp.setattr(import_dialogs.CustomerCollisionDialog, "exec", coll_exec)
    assert import_dialogs.run_csv_import(win, ctx)
    names = [c["last_name"] for c in repo.list_customers(ctx.con)]
    assert names.count("Mustermann") == 1 and "Neu" in names


def test_legacy_dialog_duplicate_and_format(gui):
    app, ctx, log, mp, tmp = gui
    from app.db.database import company
    from app.importers import legacy_invoices as L
    from app.ui.import_dialogs import LegacyReviewDialog
    from app.services import documents

    f1 = _make_ods(tmp / "a.ods")
    dlg = LegacyReviewDialog(None, ctx, L.parse_file(f1, company(ctx.con)), 1, 1)
    dlg._import()
    assert dlg.result_code == LegacyReviewDialog.IMPORTED

    # gleiche Nummer, andere Datei -> "Mit Vermerk importieren"
    f2 = _make_ods(tmp / "b.ods", total="500,00")
    dlg = LegacyReviewDialog(None, ctx, L.parse_file(f2, company(ctx.con)), 1, 1)
    dlg.number_choice.button(1).setChecked(True)
    dlg._import()
    assert dlg.result_code == LegacyReviewDialog.IMPORTED
    d = [x for x in documents.list_documents(ctx.con, ("rechnung",)) if x["number"] == "2026-0905-D1"]
    assert d and "Dublette" in documents.get_document(ctx.con, d[0]["id"])["import_note"]

    # Nummer außerhalb des Schemas -> nur mit Haken
    f3 = _make_ods(tmp / "c.ods", number="2026-0777", total="10,00")
    dlg = LegacyReviewDialog(None, ctx, L.parse_file(f3, company(ctx.con)), 1, 1)
    dlg.number.setText("R-17")
    dlg.refresh_collisions()
    dlg._import()
    assert dlg.result_code != LegacyReviewDialog.IMPORTED
    dlg.keep_number.setChecked(True)
    dlg._import()
    assert dlg.result_code == LegacyReviewDialog.IMPORTED
    assert any(x["number"] == "R-17" for x in documents.list_documents(ctx.con, ("rechnung",)))


def test_unsaved_changes_guard(gui):
    app, ctx, log, mp, tmp = gui
    from app.db import repo
    from app.ui import common, editor, settings_page, texts_page
    from app.ui.main_window import MainWindow
    answers = []

    def fake_ask(*a, **k):
        return answers.pop(0)
    for mod in (common, editor, settings_page, texts_page):
        mp.setattr(mod, "ask_save", fake_ask)

    win = MainWindow(ctx)
    win._skip_backup = True
    win.nav.setCurrentRow(win.page_index("kunden"))
    # Kunde eingebettet anlegen, Feld ändern, wegnavigieren -> Abbrechen bleibt
    win.pages[win.page_index("kunden")].new()
    dlg = win._editor
    assert dlg is not None and win.stack.currentWidget() is win._editor_box
    dlg.fields["last_name"].setText("Testkunde")
    dlg.fields["street"].setText("Weg 1")
    answers.append(common.CANCEL)
    win.nav.setCurrentRow(win.page_index("rechnungen"))
    assert win._editor is dlg and win.nav.currentRow() == win.page_index("kunden")
    # Speichern -> Kunde angelegt, Seite gewechselt
    answers.append(common.SAVE)
    win.nav.setCurrentRow(win.page_index("rechnungen"))
    assert win._editor is None and win.stack.currentIndex() == win.page_index("rechnungen")
    assert any(c["last_name"] == "Testkunde" for c in repo.list_customers(ctx.con))

    # Beleg-Editor: Änderung verwerfen
    win.pages[win.page_index("rechnungen")].new()
    win._editor.subject.setText("Wird verworfen")
    answers.append(common.DISCARD)
    win.nav.setCurrentRow(0)
    assert win._editor is None and win.stack.currentIndex() == 0
    from app.services import documents
    assert not documents.list_documents(ctx.con, ("rechnung",))

    # Einstellungen: Änderung, Abbrechen hält fest, Verwerfen stellt zurück
    win.nav.setCurrentRow(win.page_index("einstellungen"))
    sp = win.pages[win.page_index("einstellungen")]
    old = sp.edits["company.phone"].text()
    sp.edits["company.phone"].setText("0000")
    answers.append(common.CANCEL)
    win.nav.setCurrentRow(0)
    assert win.stack.currentIndex() == win.page_index("einstellungen")
    answers.append(common.DISCARD)
    win.nav.setCurrentRow(0)
    assert win.stack.currentIndex() == 0 and sp.edits["company.phone"].text() == old

    # Artikel: neue Leistung, beim Navigieren speichern, erneutes Speichern legt keine Dublette an
    win.nav.setCurrentRow(win.page_index("texte"))
    at = win.pages[win.page_index("texte")].art_tab
    at.new()
    at.title.setText("Blower-Door-Test")
    at.price.setText("350")
    answers.append(common.SAVE)
    win.nav.setCurrentRow(0)
    at.save()
    assert [a["title"] for a in repo.list_articles(ctx.con)].count("Blower-Door-Test") == 1
    assert not answers


def test_trips_page(gui):
    app, ctx, log, mp, tmp = gui
    from app.db import repo
    from app.services import trips
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win._skip_backup = True
    cid = repo.save_customer(ctx.con, {"first_name": "Max", "last_name": "Muster", "street": "Weg 1", "zip": "11111",
                                       "city": "Ort"})
    ctx.changed()
    win.nav.setCurrentRow(win.page_index("fahrten"))
    page = win.pages[win.page_index("fahrten")]
    page.new()
    dlg = win._editor
    dlg.customer.setCurrentIndex(dlg.customer.findData(cid))
    assert dlg.destination.text() == "Weg 1, 11111 Ort"
    dlg.purpose.setCurrentText("Kundentermin vor Ort")
    dlg.km_one_way.setText("12,5")
    assert "25 km" in dlg.calc.text() and "7,50 €" in dlg.calc.text()
    dlg.save()
    assert win._editor is None
    assert trips.list_trips(ctx.con)[0]["amount_cents"] == 750
    page.reload()
    assert page.table.rowCount() == 1
    # zweite Fahrt zum selben Kunden: km werden vorgeschlagen
    page.new()
    dlg = win._editor
    dlg.customer.setCurrentIndex(dlg.customer.findData(cid))
    assert dlg.km_one_way.text() == "12,5" and dlg.round_trip.isChecked()
    dlg.done(0)
    euer = win.pages[win.page_index("euer")]
    euer.reload()
    assert any(euer.table.item(r, 2).text() == "7,50 €" for r in range(euer.table.rowCount()))


def test_notes_page(gui):
    app, ctx, log, mp, tmp = gui
    from app.db import repo
    from app.services import notes
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win._skip_backup = True
    cid = repo.save_customer(ctx.con, {"first_name": "Max", "last_name": "Muster", "street": "Weg 1", "zip": "11111",
                                       "city": "Ort"})
    pid = repo.save_project(ctx.con, {"customer_id": cid, "name": "EFH", "object_street": "Objektweg 3"})
    ctx.changed()
    # aus der Kundenliste: Notizen des markierten Kunden
    kp = win.pages[win.page_index("kunden")]
    kp.reload()
    kp.table.selectRow(0)
    kp.show_notes()
    page = win.pages[win.page_index("notizen")]
    assert win.stack.currentIndex() == win.page_index("notizen") and page.customer.currentData() == cid
    page.new()
    dlg = win._editor
    assert dlg.customer.currentData() == cid
    dlg.project.setCurrentIndex(dlg.project.findData(pid))
    dlg.title.setText("Telefonat")
    dlg.body.setPlainText("Unterlagen kommen per Post\nRückruf Freitag")
    dlg.save()
    assert win._editor is None
    assert page.table.rowCount() == 1 and page.table.item(0, 4).text() == "Unterlagen kommen per Post"
    n = notes.list_notes(ctx.con)[0]
    assert (n["customer_id"], n["project_id"], n["title"]) == (cid, pid, "Telefonat")
    page.search.setText("freitag")
    assert page.table.rowCount() == 1
    page.search.setText("nichts")
    assert page.table.rowCount() == 0
    page.search.setText("")
    page.table.selectRow(0)
    page.delete()
    assert not notes.list_notes(ctx.con)


def test_trip_from_invoice_and_remember(gui):
    app, ctx, log, mp, tmp = gui
    from app.db import repo
    from app.services import documents, trips
    from app.ui import trips_page
    from app.ui.editor import DocumentEditor
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win._skip_backup = True
    cid = repo.save_customer(ctx.con, {"first_name": "Max", "last_name": "Muster", "street": "Weg 1", "zip": "11111",
                                       "city": "Ort"})
    ed = DocumentEditor(win, ctx, doc_type="rechnung", customer_id=cid)
    ed.table.item(0, 0).setText("Energieberatung")
    ed.table.item(0, 1).setText("2")
    ed.table.item(0, 3).setText("100,00")
    # neuer Schlusstext und Positionsbeschreibung werden beim Speichern Textbausteine
    ed.footer.setText("Danke für die gute Zusammenarbeit!")
    ed.table.setCurrentCell(0, 0)
    ed.desc.setText("Beratung vor Ort inkl. Bericht")
    ed.notes.remember.setChecked(False)
    ed.notes.setText(ed.notes.text() + "\nEigener Hinweis ohne Übernahme")
    assert ed.save(quiet=True)
    assert "Danke für die gute Zusammenarbeit!" in repo.text_choices(ctx.con, "fuss_rechnung")
    assert "Beratung vor Ort inkl. Bericht" in repo.text_choices(ctx.con, "position")
    assert not any("Eigener Hinweis" in t for t in repo.text_choices(ctx.con, "hinweis"))
    n_blocks = len(repo.list_textblocks(ctx.con))
    assert ed.save(quiet=True) and len(repo.list_textblocks(ctx.con)) == n_blocks   # unverändert: nichts Neues
    ed.finalize()
    inv = documents.list_documents(ctx.con, ("rechnung",))[0]
    assert inv["status"] == "festgeschrieben", log

    # festgeschriebene Rechnung: Fahrt hinzufügen mit Vorbelegung
    ed = DocumentEditor(win, ctx, doc_id=inv["id"])
    seen = {}

    def fake_exec(dlg):
        seen.update(customer=dlg.customer.currentData(), document=dlg.document.currentData(),
                    destination=dlg.destination.text())
        dlg.purpose.setCurrentText("Heizungscheck")
        dlg.km_one_way.setText("8")
        dlg.save()
        return dlg.result()
    mp.setattr(trips_page.TripDialog, "exec", fake_exec)
    ed.add_trip()
    assert seen == {"customer": cid, "document": inv["id"], "destination": "Weg 1, 11111 Ort"}
    t = trips.list_trips(ctx.con)[0]
    assert (t["document_id"], t["km"], t["trip_date"]) == (inv["id"], "16", inv["service_date"] or inv["doc_date"])
    assert "Heizungscheck" in repo.text_choices(ctx.con, trips.PURPOSE_CATEGORY)

    # Einstellung: Haken standardmäßig aus
    from app.db.database import set_setting
    set_setting(ctx.con, "textblock.remember_default", "0")
    dlg = trips_page.TripDialog(win, ctx)
    assert not dlg.remember_purpose.isChecked()
    ed = DocumentEditor(win, ctx, doc_type="rechnung", customer_id=cid)
    assert not ed.footer.remember.isChecked()
    # Entwurf: Fahrt-Button gesperrt
    from PySide6.QtWidgets import QPushButton
    trip_btn = [b for b in ed.findChildren(QPushButton) if b.text() == "Fahrt hinzufügen…"][0]
    assert not trip_btn.isEnabled()


def test_hottgenroth_dialog_shows_only_new(gui):
    app, ctx, log, monkeypatch, tmp_path = gui
    from app.importers import hottgenroth as H
    from app.ui.import_dialogs import HottgenrothImportDialog
    from PySide6.QtCore import Qt
    contacts = [H.map_contact({"uuid": u, "FirstName": n, "LastName": "Test", "PostalCode": z, "City": "X"})
                for u, n, z in [("U1", "Alt", "1"), ("U2", "Weg", "2"), ("U3", "Neu", "3")]]
    H.apply_import(ctx.con, H.plan_import(ctx.con, contacts[:1]))
    dlg = HottgenrothImportDialog(None, ctx, H.plan_import(ctx.con, contacts))
    app.processEvents()
    assert [dlg.table.isRowHidden(r) for r in range(3)] == [True, False, False]
    dlg.table.sortByColumn(2, Qt.DescendingOrder)  # nach Name sortiert: Zuordnung Zeile -> Kontakt bleibt richtig
    app.processEvents()
    rows = {it.incoming["first_name"]: r for r, _, it in dlg._rows()}
    assert [rows[n] for n in ("Weg", "Neu", "Alt")] == [0, 1, 2]
    assert dlg.table.isRowHidden(rows["Alt"]) and not dlg.table.isRowHidden(rows["Weg"])
    dlg.table.item(rows["Weg"], 0).setCheckState(Qt.Unchecked)  # „Weg“ bewusst nicht importieren
    dlg.run()
    assert H.ignored_uuids(ctx.con) == {"u2"}
    assert ctx.con.execute("SELECT 1 FROM customer WHERE hottgenroth_uuid='u3'").fetchone()
    dlg = HottgenrothImportDialog(None, ctx, H.plan_import(ctx.con, contacts))
    assert all(dlg.table.isRowHidden(r) for r in range(3)) and not dlg.empty.isHidden()
    # in Hottgenroth geändert -> trotz Import sichtbar
    changed = dict(contacts[0], city="Y")
    changed["hottgenroth_hash"] = H.contact_hash(changed)
    dlg2 = HottgenrothImportDialog(None, ctx, H.plan_import(ctx.con, [changed, *contacts[1:]]))
    assert [dlg2.table.isRowHidden(r) for r in range(3)] == [False, True, True]
    dlg._toggle_all()
    assert not any(dlg.table.isRowHidden(r) for r in range(3))
    assert dlg.table.item(1, 0).checkState() == Qt.Unchecked
    dlg.table.item(1, 0).setCheckState(Qt.Checked)
    dlg.run()
    assert H.ignored_uuids(ctx.con) == set()
    assert len([c for c in ctx.con.execute("SELECT * FROM customer WHERE hottgenroth_uuid IS NOT NULL")]) == 3


def test_tables_sortable(gui):
    app, ctx, log, monkeypatch, tmp_path = gui
    from PySide6.QtCore import Qt
    from app.ui.common import item, make_table, sort_key
    assert sort_key("1.234,56 €") > sort_key("99,00 €") and sort_key("05.10.2026") > sort_key("28.12.2025")
    assert sort_key("") > sort_key("Zander")  # leere Zellen ans Ende
    t = make_table(["Name", "Betrag"])
    data = [("Bert", "1.200,00 €"), ("Anna", "99,00 €"), ("Carl", "5,00 €")]

    def fill():
        t.setRowCount(0)
        for name, amount in data:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, item(name, name))
            t.setItem(r, 1, item(amount))
        app.processEvents()

    fill()
    assert [t.item(r, 0).text() for r in range(3)] == ["Bert", "Anna", "Carl"]  # Reihenfolge der Abfrage
    t.sortByColumn(1, Qt.AscendingOrder)
    assert [t.item(r, 0).text() for r in range(3)] == ["Carl", "Anna", "Bert"]
    fill()  # neu laden: Sortierung bleibt, Zeilen bleiben zusammen
    assert [(t.item(r, 0).text(), t.item(r, 1).text()) for r in range(3)] == \
        [("Carl", "5,00 €"), ("Anna", "99,00 €"), ("Bert", "1.200,00 €")]
    t.sortByColumn(0, Qt.DescendingOrder)
    assert [t.item(r, 0).data(Qt.UserRole) for r in range(3)] == ["Carl", "Bert", "Anna"]


def test_customer_combo_click_clears_placeholder(gui):
    app, ctx, log, monkeypatch, tmp_path = gui
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox
    from app.ui.common import searchable_combo
    cb = QComboBox()
    cb.setEditable(True)
    cb.setInsertPolicy(QComboBox.NoInsert)
    cb.addItem("(kein Kunde)", None)
    cb.addItem("Erika Muster", 7)
    cb.completer().setFilterMode(Qt.MatchContains)
    searchable_combo(cb)
    cb.show()
    le = cb.lineEdit()
    QTest.mouseClick(le, Qt.LeftButton)
    assert le.text() == "" and cb.completer().popup().isVisible()
    cb.completer().popup().hide()
    le.editingFinished.emit()  # leer verlassen -> wieder „kein Kunde“
    assert cb.currentIndex() == 0 and le.text() == "(kein Kunde)"
    cb.setCurrentIndex(1)
    QTest.mouseClick(le, Qt.LeftButton)
    assert le.selectedText() == "Erika Muster"  # Tippen überschreibt
    cb.setCurrentIndex(0)
    QTest.mouseClick(le, Qt.LeftButton)
    QTest.keyClicks(le, "Mus")
    popup = cb.completer().popup()
    assert popup.isVisible() and popup.model().rowCount() == 1
    popup.setCurrentIndex(popup.model().index(0, 0))
    QTest.keyClick(popup, Qt.Key_Return)
    assert cb.currentData() == 7 and le.text() == "Erika Muster"
    cb.close()


def test_update_flow_pieces(gui):
    app, ctx, log, monkeypatch, tmp_path = gui
    import threading
    from app.ui import update_ui
    from app.ui.main_window import MainWindow
    got = []
    main = threading.get_ident()
    keep = update_ui._in_background(lambda: threading.get_ident(),
                                    lambda res, err: got.append((res, err, threading.get_ident())))
    for _ in range(100):
        app.processEvents()
        if got:
            break
        threading.Event().wait(0.02)
    assert got and got[0][1] is None and got[0][0] != main  # Arbeit lief im Hintergrund …
    assert got[0][2] == main  # … die Rückmeldung (Dialoge!) kommt im GUI-Thread an
    del keep
    update_ui.check_on_start(MainWindow(ctx))  # aus dem Quellcode gestartet: keine Abfrage, kein Fehler
    win = MainWindow(ctx)
    win.show()
    called = []
    assert win.close_for(lambda: called.append(1)) and called == [1]


def test_profile_controls_hottgenroth(gui):
    """Hottgenroth-Knöpfe und -Einstellungen nur im Profil Energieberatung; Wechsel über die Einstellungen."""
    app, ctx, log, mp, tmp = gui
    from app.db import database
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    customers = win.pages[win.page_index("kunden")]
    dashboard = win.pages[win.page_index("uebersicht")]
    settings = win.pages[win.page_index("einstellungen")]
    assert customers.hottgenroth_btn.isHidden() and dashboard.hottgenroth_btn.isHidden()
    assert settings.hottgenroth_box.isHidden()
    called = []
    mp.setattr("app.ui.import_dialogs.run_hottgenroth_import", lambda *a: called.append(a) or False)
    win.navigate("hottgenroth")                                   # ohne Profil: nichts passiert
    assert not called
    settings.profile.setCurrentIndex(settings.profile.findData("energieberatung"))
    assert not settings.hottgenroth_box.isHidden()
    settings.save(quiet=True)
    assert database.get_setting(ctx.con, "profile") == "energieberatung"
    assert not customers.hottgenroth_btn.isHidden() and not dashboard.hottgenroth_btn.isHidden()
    assert ctx.con.execute("SELECT 1 FROM article WHERE title='Hydraulischer Abgleich'").fetchone()
    assert settings.confirm_leave()                               # gespeichert: keine Rückfrage
    win.navigate("hottgenroth")
    assert called
    win.close()


def test_setup_wizard(gui):
    """Erster Start: Assistent übernimmt Firmendaten, Profil, Besteuerung, Logo (in den Datenordner) und Sicherung."""
    app, ctx, log, mp, tmp = gui
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QWizard
    from app.db import database
    from app.ui import setup_wizard
    assert not setup_wizard.needs_setup(ctx.con)                 # Dummy-Firma vorhanden
    database.set_setting(ctx.con, "company.name", "")
    assert setup_wizard.needs_setup(ctx.con)
    logo = tmp / "mein-logo.PNG"
    pm = QPixmap(40, 20)
    pm.fill(QColor("red"))
    assert pm.save(str(logo), "PNG")
    wiz = setup_wizard.SetupWizard(None, ctx)
    wiz.show()
    wiz.next()                                                    # Willkommen -> Firmendaten
    wiz.next()
    assert wiz.currentId() == 1                                   # Pflichtfeld Firmenname leer: kein Weiter
    wiz.edits["company.name"].setText("Muster Handwerk")
    wiz.next()
    assert wiz.currentId() == 2
    wiz.edits["company.iban"].setText("DE02 1203 0000 0000 2020 51")
    wiz.next()
    next(b for b in wiz.profile_group.buttons() if b.property("key") == "energieberatung").setChecked(True)
    wiz.tax_regel.setChecked(True)
    wiz.next()
    wiz.logo.setText(str(tmp / "kaputt.png"))
    assert not wiz.validateCurrentPage()                          # kein lesbares Bild
    wiz.logo.setText(str(logo))
    wiz.backup.setText(str(tmp / "sicherung"))
    wiz.button(QWizard.FinishButton).click()
    c = ctx.con
    assert database.get_setting(c, "company.name") == "Muster Handwerk"
    assert database.get_setting(c, "company.iban") == "DE02 1203 0000 0000 2020 51"
    assert database.get_setting(c, "profile") == "energieberatung"
    assert database.get_setting(c, "tax.mode") == "regel"
    assert database.get_setting(c, "backup.dir") == str(tmp / "sicherung")
    assert database.get_setting(c, "company.logo") == str(tmp / "data" / "logo.png")
    assert (tmp / "data" / "logo.png").read_bytes() == logo.read_bytes()
    assert not setup_wizard.needs_setup(c)
    database.set_setting(c, "company.name", "")                   # durchlaufen: kommt nicht wieder
    assert not setup_wizard.needs_setup(c)


def test_setup_wizard_on_first_start(gui):
    app, ctx, log, mp, tmp = gui
    from app.db import database
    from app.ui import setup_wizard
    from app.ui.main_window import MainWindow
    database.set_setting(ctx.con, "company.name", "")
    mp.setattr(setup_wizard.SetupWizard, "exec", lambda self: 0)  # „Später“
    win = MainWindow(ctx)
    win.run_setup()
    assert win.nav.currentRow() == win.page_index("einstellungen") and log
    win.close()
