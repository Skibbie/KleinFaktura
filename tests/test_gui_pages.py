"""Oberflächentests für Ausgaben, EÜR, Export/Sicherung, offene Posten, Update und E-Mail (offscreen, Dummy-Daten)."""
from __future__ import annotations

import threading
from datetime import date, timedelta

import pytest

from test_core import _customer
from test_gui import gui  # noqa: F401 (Fixture)


def _texts(log, kind=None):
    return [str(t) for n, t in log if kind is None or n == kind]


def _file_dialog(mp, open_name=None, save_names=None):
    """Dateidialoge beantworten: open_name für „Öffnen“, save_names (Liste) der Reihe nach für „Speichern“."""
    from PySide6.QtWidgets import QFileDialog
    saves = list(save_names or [])
    mp.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(open_name or ""), "")))
    mp.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(saves.pop(0)) if saves else "", "")))


def _overdue_invoice(con, email=""):
    from app.services import documents
    cid = _customer(con, email=email)
    start = date.today() - timedelta(days=60)
    d = documents.new_draft(con, "rechnung", cid)
    d.update(doc_date=start.isoformat(), service_date=start.isoformat(),
             due_date=(start + timedelta(days=14)).isoformat())
    did = documents.save_draft(con, d, [{"title": "Beratung", "quantity": "1", "unit": "Stunden",
                                         "unit_price_cents": 10000}])
    return documents.finalize(con, did)


# ------------------------------------------------------------------ Ausgaben
def test_expense_dialog(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import expenses
    from app.ui.expenses_page import ExpenseDialog
    today = date.today().isoformat()

    dlg = ExpenseDialog(None, ctx)
    dlg.save()                                               # leer: Meldung statt Absturz
    assert any("Betrag fehlt" in t for t in _texts(log, "critical"))
    dlg.open_receipt()
    assert any("kein Beleg" in t for t in _texts(log, "information"))

    dlg.supplier.setCurrentText("Messtechnik GmbH")
    dlg.description.setText("Wärmebildkamera")
    dlg.category.setCurrentIndex(dlg.category.findData("afa"))
    dlg.amount.setText("1.190,00")
    assert "Abschreibung" in dlg.calc.text() and not dlg.life.isHidden()
    dlg.amount.setText("500")
    assert "Sofortabzug" in dlg.calc.text()
    dlg.category.setCurrentIndex(dlg.category.findData("gwg"))
    dlg.amount.setText("1.190,00")
    assert "über 800" in dlg.calc.text() and dlg.life.isHidden()
    dlg.category.setCurrentIndex(dlg.category.findData("bewirtung"))
    dlg.amount.setText("100")
    assert "Abziehbar 70 %: 70,00" in dlg.calc.text()
    dlg.amount.setText("abc")
    assert dlg.calc.text() == "Ungültige Zahl."
    dlg.amount.setText("100")
    receipt = tmp / "beleg.pdf"
    receipt.write_bytes(b"%PDF beleg")
    _file_dialog(mp, open_name=receipt)
    dlg.pick_receipt()
    assert "neu, wird beim Speichern archiviert" in dlg.receipt_lbl.text() and dlg.is_dirty()
    dlg.open_receipt()
    dlg.save()
    saved = expenses.get_expense(ctx.con, dlg.exp_id)
    assert saved["category"] == "bewirtung" and saved["amount_cents"] == 10000 and saved["exp_date"] == today
    assert saved["receipt_sha256"] and "beleg.pdf" in saved["receipt_path"]

    # bestehende Ausgabe: Beleg wird angezeigt; neuer Beleg desselben Lieferanten schlägt die Kategorie vor
    edit = ExpenseDialog(None, ctx, saved)
    assert "beleg.pdf" in edit.receipt_lbl.text() and not edit.is_dirty()
    edit.open_receipt()
    new = ExpenseDialog(None, ctx)
    new.supplier.setCurrentText("Messtechnik GmbH")
    new._supplier_done()
    assert new.category.currentData() == "bewirtung"
    new._category_chosen = True                              # selbst gewählt: kein Überschreiben
    new.category.setCurrentIndex(new.category.findData("uebrige"))
    new._supplier_done()
    assert new.category.currentData() == "uebrige"

    # alte km-Ausgabe bleibt bearbeitbar und rechnet mit der Pauschale
    km_id = expenses.save_expense(ctx.con, {"exp_date": today, "description": "Fahrt alt", "category": "fahrt", "km": "10"})
    km = ExpenseDialog(None, ctx, expenses.get_expense(ctx.con, km_id))
    assert "10 km" in km.calc.text() and "3,00 €" in km.calc.text() and km.amount.isHidden()
    km.km.setText("20")
    km.save()
    assert expenses.get_expense(ctx.con, km_id)["amount_cents"] == 600

    # abgeschlossenes Jahr: nur ansehen
    last = date.today().year - 1
    old_id = expenses.save_expense(ctx.con, {"exp_date": f"{last}-05-05", "supplier": "Alt", "category": "uebrige",
                                             "amount_cents": 500})
    expenses.lock_year(ctx.con, last)
    locked = ExpenseDialog(None, ctx, expenses.get_expense(ctx.con, old_id))
    assert not locked.amount.isEnabled() and not locked.supplier.isEnabled()


def test_expenses_page_and_bank_import(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtCore import Qt
    from app.services import expenses
    from app.ui import expenses_page
    from app.ui.expenses_page import BankImportDialog, ExpensesPage
    y = date.today().year
    csv = tmp / "umsaetze.csv"
    csv.write_text(
        '"Kontonummer:";"DE00 1234"\n\n'
        '"Buchungstag";"Valutadatum";"Beguenstigter/Zahlungspflichtiger";"Verwendungszweck";"Betrag (EUR)"\n'
        f'"02.03.{y}";"02.03.{y}";"Vodafone GmbH";"Rechnung 02";"-39,99"\n'
        f'"03.03.{y}";"03.03.{y}";"Erika Mustermann";"Rechnung 1001";"720,00"\n'
        f'"05.03.{y}";"05.03.{y}";"ARAL Station";"Tanken";"-65,10"\n'
        f'"06.03.{y}";"06.03.{y}";"Finanzamt";"Einkommensteuer";"-300,00"\n', encoding="cp1252")

    dlg = BankImportDialog(None, ctx, str(csv))
    assert dlg.table.rowCount() == 3
    assert [dlg.table.item(i, 0).checkState() == Qt.Checked for i in range(3)] == [True, True, False]
    assert "vermutlich privat" in dlg.table.item(2, 3).text() and "2 ausgewählt" in dlg.info.text()
    dlg._check(False)
    assert "0 ausgewählt" in dlg.info.text()
    dlg.run()                                                # nichts gewählt: schließt ohne Übernahme
    assert not expenses.list_expenses(ctx.con, y)
    dlg._check(True)
    dlg.table.item(2, 0).setCheckState(Qt.Unchecked)
    dlg.combos[1].setCurrentIndex(dlg.combos[1].findData("reise"))
    dlg.run()
    assert any("2 Ausgaben übernommen" in t for t in _texts(log, "information"))
    assert sorted(e["category"] for e in expenses.list_expenses(ctx.con, y)) == ["reise", "telekom"]
    again = BankImportDialog(None, ctx, str(csv))
    assert "bereits erfasst" in again.table.item(0, 3).text()
    purpose = next(cb for cb in again.map_boxes if cb.property("target") == "purpose")
    purpose.setCurrentIndex(0)                               # Spalte abwählen: Tabelle wird neu aufgebaut
    assert again.table.rowCount() == 3 and again.table.item(0, 3).text() == ""
    broken = BankImportDialog(None, ctx, str(tmp / "fehlt.csv"))
    assert broken.rows == [] and _texts(log, "critical")

    page = ExpensesPage(ctx)
    assert page.table.rowCount() == 2 and "bei 2 fehlt noch der Beleg" in page.sum_label.text()
    page.open_receipt()                                      # nichts markiert: passiert nichts
    page.delete()
    page.edit()
    opened = []
    mp.setattr(expenses_page.ExpenseDialog, "exec", lambda self: opened.append(self.exp.get("id")) or 1)
    page.new()
    page.table.selectRow(0)
    page.edit()
    assert opened[0] is None and opened[1]
    page.table.selectRow(0)                                  # nach dem Speichern lädt die Liste neu
    page.open_receipt()
    assert any("kein Beleg" in t for t in _texts(log, "information"))
    page.search.setText("vodafone")
    assert page.table.rowCount() == 1
    page.search.setText("")
    page.cat.setCurrentIndex(page.cat.findData("reise"))
    assert page.table.rowCount() == 1
    page.table.selectRow(0)
    page.delete()
    assert [e["category"] for e in expenses.list_expenses(ctx.con, y)] == ["telekom"]
    mp.setattr(expenses_page.BankImportDialog, "exec", lambda self: 1)
    _file_dialog(mp, open_name=csv)
    page.bank_import()


def test_euer_page(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtCore import Qt
    from app.db.database import get_setting, set_setting
    from app.services import expenses
    from app.ui.expenses_page import EuerPage
    y, last = date.today().year, date.today().year - 1
    expenses.save_expense(ctx.con, {"exp_date": f"{last}-04-01", "supplier": "Büro AG", "category": "uebrige",
                                    "amount_cents": 11900})
    expenses.save_expense(ctx.con, {"exp_date": f"{last}-06-01", "supplier": "Gasthaus", "category": "bewirtung",
                                    "amount_cents": 10000})
    page = EuerPage(ctx)
    page.year.setCurrentIndex(page.year.findData(last))
    assert page.table.rowCount() == 3 and page.l_exp.text() == "189,00 €"
    assert "fehlt noch der Beleg" in page.warn.text()
    page.table.item(0, 0).setCheckState(Qt.Checked)
    assert get_setting(ctx.con, f"euer.done.{last}.einnahmen") == "1"
    page.reload()
    assert page.table.item(0, 0).checkState() == Qt.Checked

    out = tmp / "euer.xlsx"
    _file_dialog(mp, save_names=[out])
    page.export()
    assert out.exists()
    page.export()                                            # Dialog abgebrochen
    _file_dialog(mp, save_names=[tmp / "gibt-es-nicht" / "euer.xlsx"])
    page.export()
    assert _texts(log, "critical")

    page.year.setCurrentIndex(page.year.findData(y))
    page.lock()
    assert any("noch nicht vorbei" in t for t in _texts(log, "information")) and not expenses.is_locked(ctx.con, y)
    page.year.setCurrentIndex(page.year.findData(last))
    page.lock()
    assert expenses.is_locked(ctx.con, last)
    assert "ist abgeschlossen" in page.warn.text() and "Zahlungen" in page.warn.text()
    page.lock()
    assert any("bereits abgeschlossen" in t for t in _texts(log, "information"))
    set_setting(ctx.con, "tax.mode", "regel")
    page.reload()
    assert "Regelbesteuerung" in page.intro.text()


# ------------------------------------------------------------------ Offene Posten / Zahlung im abgeschlossenen Jahr
def test_open_items_page(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import payments
    from app.ui import documents_page
    from app.ui.open_items import OpenItemsPage
    doc = _overdue_invoice(ctx.con)
    page = OpenItemsPage(ctx)
    assert page.table.rowCount() == 1 and "davon überfällig 100,00 €" in page.summary.text()
    page.payment()
    assert any("Bitte eine Rechnung auswählen" in t for t in _texts(log, "information"))
    page.filter.setCurrentIndex(page.filter.findData("due"))
    assert page.table.rowCount() == 0
    page.filter.setCurrentIndex(page.filter.findData("overdue"))
    assert page.table.rowCount() == 1
    page.search.setText("gibt es nicht")
    assert page.table.rowCount() == 0
    page.search.setText(doc["number"])
    page.table.selectRow(0)
    page.pdf()
    page.last_dunning()
    assert any("noch keine Mahnung" in t for t in _texts(log, "information"))
    page.dunning()
    assert [m["level"] for m in payments.dunning_history(ctx.con, doc["id"])] == [1]
    page.table.selectRow(0)
    page.last_dunning()
    page.history()
    assert any("Mahnungen:" in t and "Zahlungserinnerung" in t for t in _texts(log, "information"))
    mp.setattr(documents_page.PaymentDialog, "exec", lambda self: (self.save(), self.result())[1])
    page.payment()
    assert payments.open_amount(ctx.con, doc["id"]) == 0 and page.table.rowCount() == 0


def test_payment_dialog_respects_closed_year(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtCore import QDate
    from app.services import expenses, payments
    from app.ui.documents_page import PaymentDialog
    doc = _overdue_invoice(ctx.con)
    last = date.today().year - 1
    expenses.lock_year(ctx.con, last)
    dlg = PaymentDialog(None, ctx, doc)
    dlg.date.setDate(QDate(last, 12, 30))
    dlg.save()
    assert any(f"{last} ist abgeschlossen" in t for t in _texts(log, "critical"))
    assert payments.paid_cents(ctx.con, doc["id"]) == 0
    dlg.date.setDate(QDate.currentDate())
    dlg.save()
    assert payments.paid_cents(ctx.con, doc["id"]) == 10000


# ------------------------------------------------------------------ Export, Integritätsprüfung, Sicherung
def test_export_page_exports_and_verify(gui):
    app, ctx, log, mp, tmp = gui
    from app import config
    from app.services import payments
    from app.ui.export_page import ExportPage
    doc = _overdue_invoice(ctx.con)
    payments.add_payment(ctx.con, doc["id"], date.today().isoformat(), "40")
    calls = []
    page = ExportPage(ctx, calls.append)
    names = [tmp / "u.xlsx", tmp / "u.csv", tmp / "d.csv", tmp / "g.zip"]
    _file_dialog(mp, save_names=names)
    page.revenue("xlsx")
    page.revenue("csv")
    page.datev()
    page.gobd()
    assert all(p.exists() for p in names)
    page.revenue("csv")                                      # Dialog abgebrochen
    page.datev()
    page.gobd()
    missing = tmp / "gibt-es-nicht"
    _file_dialog(mp, save_names=[missing / "u.csv", missing / "d.csv", missing / "g.zip"])
    page.revenue("csv")
    page.datev()
    page.gobd()
    assert len(_texts(log, "critical")) == 3
    page.procdoc()
    page.open_dir()

    page.verify()
    ok = _texts(log, "information")[-1]
    assert ok.count("✓") == 4 and "✗" not in ok
    from app.services import documents
    pdf = config.resolve_stored_path(documents.get_document(ctx.con, doc["id"])["pdf_path"])
    pdf.chmod(0o666)
    pdf.write_bytes(b"manipuliert")
    page.verify()
    bad = _texts(log, "warning")[-1]
    assert "✗ Archiv" in bad and "wurde verändert" in bad and "✓ Belege stimmen" in bad


def test_export_page_backup_and_restore(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QInputDialog
    from app.db.database import set_setting
    from app.services import backup, backup_crypto, secret
    from app.ui.export_page import ExportPage
    set_setting(ctx.con, "backup.dir", str(tmp / "bk"))
    _overdue_invoice(ctx.con)
    calls = []
    page = ExportPage(ctx, calls.append)
    assert "Noch keine Sicherung" in page.backups.item(0).text() and "noch keine" in page.backup_info.text()
    page.do_backup()                                         # im Hintergrund, mit eigener Verbindung
    plain_backup = backup.list_backups(ctx.con)[0]
    assert any(str(plain_backup) in t for t in _texts(log, "information"))
    assert page.backups.item(0).text() == plain_backup.name and not backup.needs_backup(ctx.con)

    _file_dialog(mp)                                         # Auswahl abgebrochen
    page.restore()
    junk = tmp / "keine-sicherung.zip"
    junk.write_text("kein zip", encoding="utf-8")
    _file_dialog(mp, open_name=junk)
    page.restore()
    assert any("keine lesbare Sicherung" in t for t in _texts(log, "warning")) and not calls
    _file_dialog(mp, open_name=plain_backup)
    page.restore()
    assert len(calls) == 1 and callable(calls[0])

    # verschlüsselt: erst das eingestellte Passwort, bei falschem wird nachgefragt
    set_setting(ctx.con, "backup.encrypt", "1")
    set_setting(ctx.con, "backup.password", secret.protect("geheim123"))
    page.do_backup()
    enc = next(p for p in backup.list_backups(ctx.con) if p.suffix == ".enc")
    assert enc.suffix == ".enc" and backup_crypto.is_encrypted(enc)
    _file_dialog(mp, open_name=enc)
    page.restore()
    assert len(calls) == 2
    set_setting(ctx.con, "backup.password", secret.protect("inzwischen-anders"))
    answers = [("falsch", True), ("geheim123", True)]
    mp.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: answers.pop(0)))
    page.restore()
    assert len(calls) == 3 and not answers
    mp.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    page.restore()                                           # Passwortfrage abgebrochen
    assert len(calls) == 3
    from app import config
    for leftover in config.data_dir().glob("entschluesselt-*.zip"):
        leftover.unlink()

    def fail():
        raise OSError("Datenträger voll")
    mp.setattr(backup, "create_backup_own_connection", fail)
    page.do_backup()
    assert any("Datenträger voll" in t for t in _texts(log, "critical"))


def test_settings_store_backup_password_protected(gui):
    app, ctx, log, mp, tmp = gui
    from app.db.database import get_setting
    from app.services import backup, secret
    from app.ui.settings_page import SettingsPage
    sp = SettingsPage(ctx)
    sp.checks["backup.encrypt"].setChecked(True)
    sp.edits["backup.password"].setText("kurz")
    assert not sp.save(quiet=True) and any("mindestens 8 Zeichen" in t for t in _texts(log, "warning"))
    sp.edits["backup.password"].setText("geheim123")
    assert sp.save(quiet=True)
    stored = get_setting(ctx.con, "backup.password")
    assert secret.is_protected(stored) and "geheim123" not in stored and backup.password(ctx.con) == "geheim123"
    protocol = " ".join(r[0] or "" for r in ctx.con.execute("SELECT data FROM audit_log"))
    assert "geheim123" not in protocol and "(geändert)" in protocol
    again = SettingsPage(ctx)
    assert again.edits["backup.password"].text() == "geheim123" and "retention.years" not in again.edits
    again.edits["company.phone"].setText("040 1")
    assert again.save(quiet=True)
    assert get_setting(ctx.con, "backup.password") == stored  # unverändert: nicht neu verschlüsselt


# ------------------------------------------------------------------ Beenden mit Sicherung
def test_close_backs_up_in_background(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QMessageBox
    from app.db.database import set_setting
    from app.services import backup
    from app.ui import main_window
    from app.ui.main_window import MainWindow
    set_setting(ctx.con, "backup.dir", str(tmp / "bk"))
    win = MainWindow(ctx)
    win.show()
    win.close()
    assert not win.isVisible() and len(backup.list_backups(ctx.con)) == 1
    win.show()
    win.close()                                              # nichts geändert: keine zweite Sicherung
    assert len(backup.list_backups(ctx.con)) == 1
    _overdue_invoice(ctx.con)
    threading.Event().wait(1.1)                              # Sicherungen heißen nach Datum und Uhrzeit (Sekunde)
    win.show()
    win.close()                                              # am selben Tag weitergearbeitet: wird gesichert
    assert len(backup.list_backups(ctx.con)) == 2

    def fail():
        raise OSError("Sicherungsordner nicht erreichbar")
    mp.setattr(backup, "create_backup_own_connection", fail)
    mp.setattr(backup, "needs_backup", lambda con: True)
    mp.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.No))
    win.show()
    win.close()
    assert win.isVisible()                                   # „Trotzdem beenden?“ – Nein
    mp.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))

    def boom():
        raise RuntimeError("Update-Start scheitert")
    assert not win.close_for(boom) and win.isVisible()       # Fehler beim Update-Start: Programm bleibt offen
    win.close()
    assert not win.isVisible()

    quits = []
    mp.setattr(main_window.QApplication, "quit", staticmethod(lambda: quits.append(1)))
    win2 = MainWindow(ctx)
    win2.restart_with(lambda: (_ for _ in ()).throw(ValueError("Datei noch geöffnet")))
    assert any("Datei noch geöffnet" in t for t in _texts(log, "critical")) and quits == [1] and win2._skip_backup


# ------------------------------------------------------------------ Update
def test_update_ui(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QMessageBox
    from app.services import update
    from app.ui import update_ui
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win._skip_backup = True
    update_ui.check_now(win)
    assert any("aus dem Quellcode" in t for t in _texts(log, "information"))
    update_ui.install(win, update.Release("99.0.0", ""))     # ohne Programmordner: nichts zu tun

    target = tmp / "KleinFaktura"
    target.mkdir()
    mp.setattr(update, "install_dir", lambda: target)
    main_thread, seen = threading.get_ident(), []

    def latest():
        seen.append(threading.get_ident())
        return update.Release("0.0.1", "")
    mp.setattr(update, "latest_release", latest)
    update_ui.check_now(win)
    assert seen and seen[0] != main_thread                   # die Abfrage blockiert die Oberfläche nicht
    assert any("ist aktuell" in t for t in _texts(log, "information"))

    def offline():
        raise update.UpdateError("GitHub ist nicht erreichbar")
    mp.setattr(update, "latest_release", offline)
    update_ui.check_now(win)
    assert any("nicht erreichbar" in t for t in _texts(log, "warning"))

    newer = update.Release("99.0.0", "Neues")
    offered = []
    real_offer = update_ui.offer
    mp.setattr(update, "latest_release", lambda: newer)
    mp.setattr(update_ui, "offer", lambda w, r: offered.append(r.version))
    update_ui.check_now(win)
    update_ui.check_on_start(win)
    for _ in range(200):
        app.processEvents()
        if len(offered) == 2:
            break
        threading.Event().wait(0.02)
    assert offered == ["99.0.0", "99.0.0"]
    mp.setattr(update_ui, "offer", real_offer)

    mp.setattr(QMessageBox, "exec", lambda self: 0)
    installs = []
    mp.setattr(update_ui, "install", lambda w, r: installs.append(r.version))
    update_ui.offer(win, newer)                              # „Später“
    assert installs == []
    mp.setattr(QMessageBox, "clickedButton",
               lambda self: next(b for b in self.buttons() if b.text() == "Jetzt aktualisieren"))
    update_ui.offer(win, newer)
    assert installs == ["99.0.0"]


def test_update_install(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import update
    from app.ui import update_ui
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win._skip_backup = True
    target = tmp / "KleinFaktura"
    target.mkdir()
    newer = update.Release("99.0.0", "")
    mp.setattr(update, "install_dir", lambda: target)
    quits, launched = [], []
    mp.setattr(update_ui.QApplication, "quit", staticmethod(lambda: quits.append(1)))
    mp.setattr(update, "launch_helper", lambda source, tgt, setup=False: launched.append((source, tgt, setup)))
    mp.setattr(update, "download", lambda release, tgt: tgt.with_name("KleinFaktura.neu"))
    mp.setattr(update, "download_setup", lambda release: tmp / "Setup.exe")

    mp.setattr(win, "close_for", lambda action: False)       # Beenden abgebrochen (ungespeicherte Änderungen)
    update_ui.install(win, newer)
    assert any("Update abgebrochen" in t for t in _texts(log, "information")) and not quits

    mp.setattr(win, "close_for", lambda action: (action(), True)[1])
    update_ui.install(win, newer)                            # ZIP-Installation: Ordnertausch
    (target / "unins000.exe").write_bytes(b"")
    update_ui.install(win, newer)                            # per Setup installiert: Setup laden
    assert launched == [(tmp / "KleinFaktura.neu", target, False), (tmp / "Setup.exe", target, True)]
    assert quits == [1, 1]

    def bad_signature(release):
        raise update.UpdateError("Die Signatur des Updates ist ungültig.")
    mp.setattr(update, "download_setup", bad_signature)
    update_ui.install(win, newer)
    assert any("Signatur des Updates ist ungültig" in t for t in _texts(log, "warning")) and len(launched) == 2


# ------------------------------------------------------------------ E-Mail
def test_mail_ui_messages(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtWidgets import QMessageBox
    from app.services import documents, mail
    from app.ui import mail_ui
    doc = _overdue_invoice(ctx.con)
    draft = documents.new_draft(ctx.con, "rechnung", doc["customer_id"])
    mail_ui.send_mail(None, ctx, draft)
    assert any("Entwürfe können nicht versendet" in t for t in _texts(log, "information"))

    mp.setattr(mail, "find_thunderbird", lambda con: None)
    mail_ui.send_mail(None, ctx, doc)
    assert any("Thunderbird wurde nicht gefunden" in t for t in _texts(log, "warning"))
    exe = tmp / "thunderbird.exe"
    exe.write_bytes(b"")
    mp.setattr(mail, "find_thunderbird", lambda con: exe)
    mail_ui.send_mail(None, ctx, doc)                        # Kunde ohne Adresse: Hinweis, Fenster öffnet trotzdem
    assert any("keine E-Mail-Adresse" in t for t in _texts(log, "information"))
    assert [a for n, a in log if n == "mail"]

    def broken(con, d):
        raise OSError("Zugriff verweigert")
    mp.setattr(mail, "open_compose", broken)
    mail_ui.send_mail(None, ctx, doc)
    assert any("Thunderbird konnte nicht gestartet werden" in t for t in _texts(log, "critical"))

    mp.setattr(mail, "export_copy", broken)
    assert mail_ui.export_after_finalize(None, ctx, doc) == ""
    assert any("trotzdem festgeschrieben" in t for t in _texts(log, "warning"))
    mp.setattr(mail, "export_copy", lambda con, d: tmp / "kopie.pdf")
    assert "kopie.pdf" in mail_ui.export_after_finalize(None, ctx, doc)
    mp.setattr(QMessageBox, "exec", lambda self: 0)
    assert mail_ui.ask_send_after_finalize(None, "Festgeschrieben.") is False


@pytest.mark.parametrize("wait_ms", [0, 30])
def test_run_with_progress_returns_result_and_raises(gui, wait_ms):
    app, ctx, log, mp, tmp = gui
    from app.ui.common import run_with_progress

    def work():
        threading.Event().wait(wait_ms / 1000)
        return threading.get_ident()
    assert run_with_progress(None, "Test", "läuft …", work) != threading.get_ident()

    def fail():
        raise ValueError("ging schief")
    with pytest.raises(ValueError, match="ging schief"):
        run_with_progress(None, "Test", "läuft …", fail)
