"""Klicktest: bedient jede Seite, jede eingebettete Maske und jeden Dialog (Knöpfe, Auswahlfelder, Reiter).

Läuft unsichtbar (offscreen) mit Wegwerfdaten in einem temporären Ordner. Alles mit Wirkung nach außen ist gesperrt:
Dateien öffnen, Thunderbird/Hottgenroth starten, Netz (Update-Prüfung), Browser, Datei-Dialoge, Programmende.

    python tools/klicktest.py [reject|accept]      (reject: Dialoge abbrechen, accept: Dialoge bestätigen)
    KLICK_V=1 python tools/klicktest.py           (jeden Klick und Dialog protokollieren)

Befunde (Rückgabewert 1, wenn einer davon auftritt):
  FLASH    ein Widget erscheint kurz als eigenes Fenster (z. B. vor dem Einbau sichtbar geschaltet)
  FLICKER  ein Dialog wird angezeigt, versteckt und gleich wieder angezeigt
  EXC      eine Ausnahme in einem Klick
  STUCK    ein Dialog ließ sich nicht schließen
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODE = sys.argv[1] if len(sys.argv) > 1 else "reject"
if MODE not in ("reject", "accept"):
    sys.exit("Aufruf: klicktest.py [reject|accept]")
VERBOSE = bool(os.environ.get("KLICK_V"))
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = Path(tempfile.mkdtemp(prefix="klicktest-"))
os.environ["FAKTURA_DATA_DIR"] = str(TMP / "data")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import subprocess  # noqa: E402
import urllib.request  # noqa: E402


def _blocked(what):
    def f(*a, **k):
        raise OSError(f"{what} im Klicktest gesperrt")
    return f


subprocess.Popen = _blocked("Programmstart")
subprocess.run = _blocked("Programmstart")
urllib.request.urlopen = _blocked("Netzzugriff")
os.startfile = lambda *a, **k: None

from PySide6.QtCore import QEvent, QObject, QTimer, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QDialog, QDialogButtonBox,  # noqa: E402
                               QFileDialog, QMainWindow, QMenu, QMessageBox, QTabBar, QTableWidget, QTabWidget,
                               QWidget)

QDesktopServices.openUrl = staticmethod(lambda *a, **k: True)
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")

app = QApplication([])
QApplication.quit = staticmethod(lambda: None)
from app.ui import theme, update_ui  # noqa: E402

theme.apply(app)
update_ui.schedule_start_check = lambda *a, **k: None

FAIL_KINDS = ("FLASH", "FLICKER", "EXC", "STUCK")
findings: list[str] = []
where = ["Start"]
clicks = [0]


def report(kind: str, msg: str) -> None:
    line = f"{kind:7} [{where[0]}] {msg}"
    if line not in findings:
        findings.append(line)
        print(line, flush=True)


def excepthook(t, v, tb):
    frames = " | ".join(f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(tb)[-3:])
    report("EXC", f"{t.__name__}: {v} @ {frames}")


sys.excepthook = excepthook


def qt_message(_mode, _ctx, msg):
    # Meldungen, die nur vom unsichtbaren Offscreen-Betrieb kommen
    if any(s in msg for s in ("offscreen", "QFontDatabase", "propagateSizeHints", "QFont::",
                              "This plugin does not support")):
        return
    report("WARN", msg)


qInstallMessageHandler(qt_message)

# ------------------------------------------------------------------ Wegwerfdaten
from app.db import database  # noqa: E402
from app.db.database import set_setting  # noqa: E402
from app.services import documents, expenses, notes, payments, trips  # noqa: E402
from app.ui.common import Ctx  # noqa: E402
from test_core import _customer, _dummy_company, _fake_pdf, _invoice  # noqa: E402

con = database.connect()
database.init_db(con)
_dummy_company(con)
set_setting(con, "backup.dir", str(TMP / "sicherung"))
set_setting(con, "pdf.export_dir", str(TMP / "ablage"))
c1 = _customer(con, email="erika@example.org")
c2 = _customer(con, first_name="Max", last_name="Muster", salutation="Herr")
_invoice(con, c1)                                                                       # Rechnungsentwurf
inv = documents.finalize(con, _invoice(con, c1, price=10000, qty="1"), pdf_writer=_fake_pdf)
payments.add_payment(con, inv["id"], inv["doc_date"], 4000, "Bar")                      # teilbezahlt
documents.finalize(con, _invoice(con, c2, price=5000, qty="2", on="2026-09-01"), pdf_writer=_fake_pdf)  # überfällig
documents.finalize(con, _invoice(con, c2, doc_type="angebot"), pdf_writer=_fake_pdf)
_invoice(con, c1, doc_type="angebot")                                                   # Angebotsentwurf
notes.save_note(con, {"customer_id": c1, "note_date": "2026-11-12", "title": "Rückruf", "body": "Termin klären"})
trips.save_trip(con, {"trip_date": "2026-11-10", "customer_id": c1, "destination": "Beispielort", "km_one_way": 21,
                      "round_trip": 1, "km": 42, "purpose": "Kundentermin"})
expenses.save_expense(con, {"exp_date": "2026-11-05", "supplier": "Bürobedarf", "category": "uebrige",
                            "payment_method": "Bar", "amount_cents": 1999, "vat_rate": "19", "description": "Papier"})

from app.ui.main_window import PAGES, MainWindow  # noqa: E402

win = MainWindow(Ctx(con))
win._skip_backup = True
win.restart_with = lambda *a, **k: None
win.show()


# ------------------------------------------------------------------ Beobachter
class Spy(QObject):
    ALLOWED = (QDialog, QMainWindow, QMenu)
    ALLOWED_NAMES = ("QTipLabel", "QComboBoxPrivateContainer", "QCalendarPopup", "QListView", "QCalendarWidget")

    def eventFilter(self, o, e):
        t = e.type()
        if t not in (QEvent.Show, QEvent.Hide) or not isinstance(o, QWidget) or not o.isWindow() or o is win:
            return False
        if t == QEvent.Show:
            if not isinstance(o, self.ALLOWED) and type(o).__name__ not in self.ALLOWED_NAMES:
                text = o.windowTitle() or (o.text() if hasattr(o, "text") else "")
                report("FLASH", f"{type(o).__name__} „{text}“ erscheint als eigenes Fenster")
            hidden = o.property("_klick_hide")
            if isinstance(o, QDialog) and hidden and time.monotonic() - hidden < 0.5:
                report("FLICKER", f"{type(o).__name__} „{o.windowTitle()}“ versteckt und neu angezeigt")
        elif isinstance(o, QDialog):
            o.setProperty("_klick_hide", time.monotonic())
        return False


spy = Spy()
app.installEventFilter(spy)

# ------------------------------------------------------------------ Bedienen
MAX_DEPTH = 2                       # Seite/Maske → Dialog → Dialog im Dialog
LAST = ("Zurück", "Schließen", "Abbrechen", "Festschreiben", "Löschen", "Entfernen", "Stornieren")
exploring: list[QWidget] = []
seen_dialogs: set[str] = set()
current_page: list = [None]          # (Index, Seite), solange eine Seite bedient wird
force_discard = [False]
running = [True]


def label(b) -> str:
    return (b.text() or b.toolTip() or b.objectName() or type(b).__name__).replace("&", "").strip()[:40]


def pump(n: int = 6) -> None:
    for _ in range(n):
        app.processEvents()


def finish(d) -> None:
    """Offenen Dialog schließen: abbrechen bzw. bestätigen; bei Rückfragen notfalls „Verwerfen“."""
    if isinstance(d, QMessageBox):
        btns, btn = d.buttons(), None
        if force_discard[0]:
            for role in (QMessageBox.DestructiveRole, QMessageBox.NoRole, QMessageBox.AcceptRole, QMessageBox.YesRole):
                btn = next((b for b in btns if d.buttonRole(b) == role), None)
                if btn:
                    break
        elif MODE == "accept":
            btn = d.defaultButton() or (btns[0] if btns else None)
        btn.click() if btn else d.reject()
        return
    if isinstance(d, QDialog):
        tries = (d.property("_klick_tries") or 0) + 1
        d.setProperty("_klick_tries", tries)
        force_discard[0] = tries >= 2
        if tries >= 4:
            d.done(0)
            report("STUCK", f"{type(d).__name__} „{d.windowTitle()}“ ließ sich nicht schließen")
        elif MODE == "accept" and tries == 1:
            d.accept()
        else:
            d.reject()
        force_discard[0] = False
    else:
        d.close()


def watchdog() -> None:
    """Bedient modale Dialoge. Je Runde ein neuer Timer – derselbe Timer würde in verschachtelten Dialogen nicht
    erneut feuern."""
    if not running[0]:
        return
    QTimer.singleShot(60, watchdog)
    popup = app.activePopupWidget()
    if popup is not None:
        popup.close()
        return
    m = app.activeModalWidget()
    if m is None or m in exploring:
        return
    key = f"{type(m).__name__}:{m.windowTitle()}"
    if VERBOSE:
        print("  DIALOG", key, flush=True)
    if len(exploring) < MAX_DEPTH and not isinstance(m, (QMessageBox, QFileDialog)) and key not in seen_dialogs:
        seen_dialogs.add(key)
        exploring.append(m)
        outer = where[0]
        where[0] = f"{outer} > {m.windowTitle() or type(m).__name__}"
        try:
            click_all(m, skip_button_box=True)
        finally:
            where[0] = outer
            exploring.remove(m)
    if m.isVisible():
        finish(m)


def guarded(fn) -> None:
    if VERBOSE:
        print("  KLICK", where[0], flush=True)
    try:
        fn()
    except Exception:  # noqa: BLE001 – jede Ausnahme ist ein Befund
        excepthook(*sys.exc_info())
    pump()


def select_first_rows(root) -> None:
    """Viele Knöpfe brauchen eine markierte Zeile."""
    for t in root.findChildren(QTableWidget):
        if t.isVisible() and t.rowCount() > 0:
            t.selectRow(0)


def on_page(root) -> bool:
    return bool(current_page[0]) and root is current_page[0][1]


def show_page() -> None:
    if current_page[0] and win.stack.currentIndex() != current_page[0][0]:
        win.stack.setCurrentIndex(current_page[0][0])
        pump()


def click_all(root, skip_button_box: bool = False) -> None:
    """Reiter, Auswahlfelder und Knöpfe von root bedienen."""
    for tw in [w for w in root.findChildren(QTabWidget) if w.isVisible()]:
        for i in range(tw.count()):
            guarded(lambda tw=tw, i=i: tw.setCurrentIndex(i))
        tw.setCurrentIndex(0)
    for cb in [w for w in root.findChildren(QComboBox) if w.isVisible() and w.isEnabled()]:
        old = cb.currentIndex()
        outer = where[0]
        where[0] = f"{outer} / Auswahl „{cb.currentText()[:20]}“"
        for i in range(min(cb.count(), 8)):
            guarded(lambda cb=cb, i=i: cb.setCurrentIndex(i))
        where[0] = outer
        if cb.count():
            cb.setCurrentIndex(max(old, 0))
    select_first_rows(root)
    done: set[int] = set()
    for _ in range(3):   # Knöpfe können erst durch andere Klicks erscheinen
        if on_page(root):
            show_page()
        buttons = [b for b in root.findChildren(QAbstractButton)
                   if b.isVisible() and b.isEnabled() and id(b) not in done
                   and not isinstance(b.parentWidget(), QTabBar) and b.window() is root.window()]
        if not buttons:
            break
        buttons.sort(key=lambda b: label(b).startswith(LAST))   # Schließende Knöpfe zuletzt
        for b in buttons:
            done.add(id(b))
            if skip_button_box and isinstance(b.parentWidget(), QDialogButtonBox):
                continue   # OK/Abbrechen übernimmt finish()
            if on_page(root):
                show_page()
            if b.window() is not root.window() or not b.isVisible() or not b.isEnabled():
                continue
            outer = where[0]
            where[0] = f"{outer} / „{label(b)}“"
            clicks[0] += 1
            guarded(b.click)
            if on_page(root) and win._editor is not None and win._editor not in exploring:
                explore_editor()
                show_page()
            where[0] = outer
            select_first_rows(root)


def explore_editor() -> None:
    """Eingebettete Maske (Beleg, Kunde, Ausgabe …) durchklicken und schließen."""
    ed = win._editor
    outer = where[0]
    where[0] = f"{outer} > Maske"
    exploring.append(ed)
    try:
        click_all(ed)
    finally:
        exploring.remove(ed)
        where[0] = outer
    if win._editor is not None:
        win._editor.done(0)
        pump()


def main() -> int:
    QTimer.singleShot(60, watchdog)
    for i, name in enumerate(PAGES):
        where[0] = name
        win.nav.blockSignals(True)
        win.nav.setCurrentRow(i)
        win.nav.blockSignals(False)
        current_page[0] = (i, win.pages[i])
        show_page()
        win.stack.setCurrentIndex(i)
        pump()
        click_all(win.pages[i])
        if VERBOSE:
            print(f"Seite {name}: {clicks[0]} Klicks bisher", flush=True)
    running[0] = False
    failed = [f for f in findings if f.startswith(FAIL_KINDS)]
    print(f"Klicktest ({MODE}): {clicks[0]} Klicks, {len(seen_dialogs)} Dialoge erkundet, "
          f"{len(failed)} Befunde, {len(findings) - len(failed)} Hinweise")
    return 1 if failed else 0


if __name__ == "__main__":
    rc = main()
    con.close()
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(rc)
