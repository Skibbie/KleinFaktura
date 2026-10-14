"""Hauptfenster mit Navigation."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from app import config
from app.db import database
from app.services import backup
from app.ui.common import Ctx
from app.ui.customers import CustomersPage
from app.ui.dashboard import Dashboard
from app.ui.documents_page import DocumentsPage
from app.ui.expenses_page import EuerPage, ExpensesPage
from app.ui.export_page import ExportPage
from app.ui.notes_page import NotesPage
from app.ui.open_items import OpenItemsPage
from app.ui.settings_page import SettingsPage
from app.ui.texts_page import TextsPage
from app.ui.trips_page import TripsPage
from app.ui import setup_wizard, update_ui

PAGES = ["Übersicht", "Kunden", "Notizen", "Angebote", "Rechnungen", "Offene Posten", "Ausgaben", "Fahrten", "EÜR-Werte",
         "Textbausteine & Artikel", "Export & Sicherung", "Einstellungen"]
PAGE_KEYS = ["uebersicht", "kunden", "notizen", "angebote", "rechnungen", "offene_posten", "ausgaben", "fahrten", "euer", "texte",
             "export", "einstellungen"]


class MainWindow(QMainWindow):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self._update_title()
        logo = config.ASSETS_DIR / "icon.ico"
        if logo.exists():
            self.setWindowIcon(QIcon(str(logo)))
        self.resize(1320, 860)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        navcol = QWidget()
        navcol.setObjectName("navCol")
        navcol.setFixedWidth(230)
        nl = QVBoxLayout(navcol)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(0)
        self.brand = QLabel()
        nl.addWidget(self.brand)
        self.brand_sub = QLabel()
        self.brand_sub.setObjectName("brandSub")
        nl.addWidget(self.brand_sub)
        self._update_brand()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(PAGES)
        nl.addWidget(self.nav, 1)
        lay.addWidget(navcol)
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages = [
            Dashboard(ctx, self.navigate),
            CustomersPage(ctx),
            NotesPage(ctx),
            DocumentsPage(ctx, "angebot"),
            DocumentsPage(ctx, "rechnung"),
            OpenItemsPage(ctx),
            ExpensesPage(ctx),
            TripsPage(ctx),
            EuerPage(ctx),
            TextsPage(ctx),
            ExportPage(ctx, self.restart_with),
            SettingsPage(ctx),
        ]
        for p in self.pages:
            w = QWidget()
            l = QHBoxLayout(w)
            l.setContentsMargins(28, 22, 28, 18)
            l.addWidget(p)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QScrollArea.NoFrame)
            sc.setWidget(w)
            self.stack.addWidget(sc)
        self.nav.currentRowChanged.connect(self._nav_changed)
        self._editor = None
        self._editor_box = None
        self._return_row = 0
        ctx.editor_host = self
        self.nav.setCurrentRow(0)
        if setup_wizard.needs_setup(ctx.con):
            # erst nach dem Anzeigen des Fensters, damit der Assistent nicht hinter dem Startfenster liegt
            QTimer.singleShot(0, self.run_setup)
        ctx.listeners.append(self._update_title)
        self._restore_action = None
        self._after_close = None
        update_ui.schedule_start_check(self)

    def _update_title(self):
        title = f"{config.APP_NAME} {config.APP_VERSION} – {database.company(self.ctx.con).get('name', '')}"
        if not config.IS_RELEASE:
            title += f"   [ENTWICKLUNG – Daten: {config.data_dir()}]"
        self.setWindowTitle(title)

    def _update_brand(self):
        pm = QPixmap(database.company(self.ctx.con).get("logo", ""))
        if not pm.isNull():
            self.brand.setObjectName("brand")
            self.brand.setPixmap(pm.scaledToWidth(200, Qt.SmoothTransformation))
            self.brand_sub.setText(f"{config.APP_NAME} {config.APP_VERSION}")
        else:
            self.brand.setObjectName("brandName")
            self.brand.setText(config.APP_NAME)
            self.brand_sub.setText(f"Version {config.APP_VERSION}")
        self.brand.style().unpolish(self.brand)
        self.brand.style().polish(self.brand)

    def run_setup(self):
        """Einrichtungsassistent; bei „Später“ geht es mit den Einstellungen weiter."""
        if setup_wizard.SetupWizard(self, self.ctx).exec():
            self.pages[self.page_index("einstellungen")]._reload_values()
            self._update_brand()
            self.ctx.changed()
        else:
            self.nav.setCurrentRow(self.page_index("einstellungen"))
            QMessageBox.information(self, "Einrichtung", "Bitte die Firmendaten (Name, Anschrift, Steuernummer, IBAN) "
                                                         "unter „Einstellungen“ eintragen. Sie erscheinen auf allen "
                                                         "Belegen. Der Assistent erscheint beim nächsten Start erneut.")

    # ------------------------------------------------------------ eingebettete Bearbeitung
    @staticmethod
    def page_index(key: str) -> int:
        return PAGE_KEYS.index(key)

    def open_editor(self, **kwargs):
        from app.ui.editor import DocumentEditor
        if not self._close_embedded():
            return
        doc_type = kwargs.get("doc_type")
        if kwargs.get("doc_id"):
            from app.services import documents
            d = documents.get_document(self.ctx.con, kwargs["doc_id"])
            doc_type = d["doc_type"] if d else doc_type
        self.open_embedded(DocumentEditor(self, self.ctx, **kwargs),
                           self.page_index("angebote" if doc_type == "angebot" else "rechnungen"))

    def open_embedded(self, dlg, return_row: int):
        """Zeigt einen Bearbeitungsdialog im Inhaltsbereich statt als eigenes Fenster."""
        if not self._close_embedded():
            dlg.deleteLater()
            return
        self._return_row = return_row
        dlg.setWindowFlags(Qt.Widget)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(20, 14, 20, 10)
        bl.addWidget(dlg)
        self._editor, self._editor_box = dlg, box
        dlg.finished.connect(self._editor_closed)
        self.stack.addWidget(box)
        self.stack.setCurrentWidget(box)
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(return_row)
        self.nav.blockSignals(False)
        dlg.show()

    def _close_embedded(self) -> bool:
        """Schließt die eingebettete Bearbeitung (mit Speichern-Rückfrage). False = abgebrochen."""
        if self._editor is None:
            return True
        self._editor.close_dialog()
        return self._editor is None

    def _editor_closed(self, *_):
        box = self._editor_box
        self._editor, self._editor_box = None, None
        if box is not None:
            self.stack.removeWidget(box)
            box.deleteLater()
        self.stack.setCurrentIndex(self.nav.currentRow())
        self.ctx.changed()

    def _leave_current_page(self) -> bool:
        if not self._close_embedded():
            return False
        idx = self.stack.currentIndex()
        page = self.pages[idx] if 0 <= idx < len(self.pages) else None
        guard = getattr(page, "confirm_leave", None)
        return guard() if guard else True

    def _nav_changed(self, row: int):
        if self.stack.currentIndex() == row and self._editor is None:
            return
        if not self._leave_current_page():
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(self._return_row if self._editor is not None else self.stack.currentIndex())
            self.nav.blockSignals(False)
            return
        self.stack.setCurrentIndex(row)

    def navigate(self, target: str):
        if target == "new_invoice":
            self._open_page_action("rechnungen", "new")
        elif target == "new_offer":
            self._open_page_action("angebote", "new")
        elif target == "hottgenroth":
            self._open_page_action("kunden", "import_hottgenroth")
        elif target == "open_items":
            self.nav.setCurrentRow(self.page_index("offene_posten"))

    def show_notes(self, customer_id: int | None):
        row = self.page_index("notizen")
        self.nav.setCurrentRow(row)
        if self.stack.currentIndex() == row:
            self.pages[row].show_customer(customer_id)

    def _open_page_action(self, key: str, action: str):
        row = self.page_index(key)
        self.nav.setCurrentRow(row)
        if self.nav.currentRow() == row and self.stack.currentIndex() == row:
            getattr(self.pages[row], action)()

    def restart_with(self, action):
        """Für die Rücksicherung: DB schließen, Aktion ausführen, Programm beenden."""
        self._restore_action = action
        try:
            self.ctx.con.close()
            action()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Wiederherstellung fehlgeschlagen", str(e))
        else:
            QMessageBox.information(self, "Wiederherstellung", "Die Sicherung wurde wiederhergestellt. "
                                                               "Das Programm wird jetzt beendet – bitte neu starten.")
        self._skip_backup = True
        QApplication.quit()

    def close_for(self, action) -> bool:
        """Programm regulär beenden (inkl. Sicherung) und danach `action` ausführen, z.B. das Update starten.
        False, wenn das Beenden abgebrochen wurde (ungespeicherte Änderungen)."""
        self._after_close = action
        self.close()
        if self.isVisible():
            self._after_close = None
            return False
        return True

    def closeEvent(self, ev):
        if not self._leave_current_page():
            ev.ignore()
            return
        if not getattr(self, "_skip_backup", False):
            try:
                if backup.needs_daily_backup(self.ctx.con):
                    QApplication.setOverrideCursor(Qt.WaitCursor)
                    zpath = backup.create_backup(self.ctx.con)
                    QApplication.restoreOverrideCursor()
                    notice = backup.fallback_notice(self.ctx.con, zpath)
                    if notice:
                        QMessageBox.information(self, "Datensicherung", notice)
            except Exception as e:  # noqa: BLE001
                QApplication.restoreOverrideCursor()
                r = QMessageBox.warning(self, "Datensicherung", f"Automatische Sicherung fehlgeschlagen:\n{e}\n\nTrotzdem beenden?",
                                        QMessageBox.Yes | QMessageBox.No)
                if r == QMessageBox.No:
                    ev.ignore()
                    return
        if self._after_close:
            try:
                self._after_close()
            except Exception as e:  # noqa: BLE001
                self._after_close = None
                QMessageBox.warning(self, config.APP_NAME, f"Beenden für das Update fehlgeschlagen:\n{e}")
                ev.ignore()
                return
        ev.accept()
