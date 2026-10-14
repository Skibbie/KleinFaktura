"""Export, Backup, Rücksicherung und Integritätsprüfung."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGroupBox, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QVBoxLayout, QWidget)

from app import config
from app.db.database import get_setting
from app.services import audit, backup, backup_crypto, documents, export
from app.ui.common import Ctx, button, confirm, error, hbox, open_file, page_title


class ExportPage(QWidget):
    def __init__(self, ctx: Ctx, restart_cb):
        super().__init__()
        self.ctx, self.restart_cb = ctx, restart_cb
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Export & Datensicherung"))

        g1 = QGroupBox("Export für Buchhaltung / Steuerberater")
        l1 = QVBoxLayout(g1)
        self.year = QComboBox()
        for y in range(date.today().year, date.today().year - 11, -1):
            self.year.addItem(str(y), y)
        self.year.addItem("Alle Jahre", None)
        l1.addLayout(hbox(QLabel("Jahr:"), self.year, None))
        l1.addLayout(hbox(button("Umsatzliste + Einnahmen (Excel)", lambda: self.revenue("xlsx")),
                          button("Umsatzliste (CSV)", lambda: self.revenue("csv")),
                          button("DATEV-Buchungsstapel (Zahlungseingänge)", self.datev), None))
        l1.addWidget(QLabel("<span style='color:#5A6A6E'>DATEV-Konten in den Einstellungen (Standard SKR03: Bank 1200 an "
                            "8195 Erlöse Kleinunternehmer). Bitte mit dem Steuerberater abstimmen.</span>"))
        lay.addWidget(g1)

        g2 = QGroupBox("GoBD")
        l2 = QVBoxLayout(g2)
        l2.addLayout(hbox(button("Integrität prüfen (Protokoll + Archiv)", self.verify),
                          button("Datenexport für Betriebsprüfung (ZIP)", self.gobd),
                          button("Verfahrensdokumentation öffnen", self.procdoc), None))
        lay.addWidget(g2)

        g3 = QGroupBox("Datensicherung")
        l3 = QVBoxLayout(g3)
        self.backup_info = QLabel()
        l3.addWidget(self.backup_info)
        l3.addLayout(hbox(button("Jetzt sichern", self.do_backup, primary=True), button("Sicherungsordner öffnen", self.open_dir),
                          button("Aus Sicherung wiederherstellen …", self.restore, danger=True), None))
        self.backups = QListWidget()
        self.backups.setMaximumHeight(160)
        l3.addWidget(self.backups)
        lay.addWidget(g3)

        g4 = QGroupBox("Speicherorte")
        l4 = QVBoxLayout(g4)
        l4.addWidget(QLabel(f"Datenbank (lokal): {config.db_path()}"))
        l4.addWidget(QLabel(f"Belegarchiv: {config.archive_dir()}"))
        l4.addLayout(hbox(button("Archivordner öffnen", lambda: open_file(config.archive_dir())), None))
        lay.addWidget(g4)
        lay.addStretch(1)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        last = get_setting(self.ctx.con, "backup.last")
        self.backup_info.setText(f"Sicherungsordner: {get_setting(self.ctx.con, 'backup.dir')}<br>"
                                 f"Letzte Sicherung: {datetime.fromisoformat(last).strftime('%d.%m.%Y %H:%M') if last else 'noch keine'}")
        self.backups.clear()
        try:
            for p in backup.list_backups(self.ctx.con)[:30]:
                self.backups.addItem(p.name)
        except OSError:
            pass
        if not self.backups.count():
            self.backups.addItem("Noch keine Sicherung in diesem Ordner. Sie entsteht automatisch beim Beenden des "
                                 "Programms (einmal täglich) oder mit „Jetzt sichern“.")
            self.backups.item(0).setFlags(Qt.NoItemFlags)

    def _ask_path(self, title, default, filt):
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path.home() / "Documents" / default), filt)
        return Path(path) if path else None

    def revenue(self, fmt):
        y = self.year.currentData()
        p = self._ask_path("Umsatzliste speichern", f"Umsatzliste-{y or 'alle'}.{fmt}",
                           "Excel (*.xlsx)" if fmt == "xlsx" else "CSV (*.csv)")
        if p:
            try:
                export.export_revenue(self.ctx.con, p, y)
                open_file(p)
            except Exception as e:  # noqa: BLE001
                error(self, "Export", e)

    def datev(self):
        y = self.year.currentData() or date.today().year
        p = self._ask_path("DATEV-Export speichern", f"EXTF_Buchungsstapel_{y}.csv", "CSV (*.csv)")
        if p:
            try:
                export.export_datev(self.ctx.con, p, y)
                QMessageBox.information(self, "DATEV", f"Gespeichert:\n{p}")
            except Exception as e:  # noqa: BLE001
                error(self, "Export", e)

    def gobd(self):
        p = self._ask_path("GoBD-Export speichern", f"GoBD-Export-{date.today():%Y%m%d}.zip", "ZIP (*.zip)")
        if p:
            try:
                export.export_gobd(self.ctx.con, p)
                QMessageBox.information(self, "GoBD-Export", f"Gespeichert:\n{p}")
            except Exception as e:  # noqa: BLE001
                error(self, "Export", e)

    def verify(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        ok, msg = audit.verify(self.ctx.con)
        problems = documents.verify_archive(self.ctx.con)
        QApplication.restoreOverrideCursor()
        text = ("✓ " if ok else "✗ ") + msg + "\n"
        text += "✓ Alle Archivdateien vorhanden und unverändert." if not problems else "✗ Archiv:\n" + "\n".join(problems)
        (QMessageBox.information if ok and not problems else QMessageBox.warning)(self, "Integritätsprüfung", text)

    def procdoc(self):
        open_file(config.BASE_DIR / "docs" / "Verfahrensdokumentation.md")

    def do_backup(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            p = backup.create_backup(self.ctx.con)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            error(self, "Sicherung fehlgeschlagen", e)
            return
        QApplication.restoreOverrideCursor()
        self.reload()
        QMessageBox.information(self, "Sicherung", backup.fallback_notice(self.ctx.con, p) or f"Sicherung erstellt:\n{p}")

    def open_dir(self):
        open_file(backup.backup_dir(self.ctx.con))

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Sicherung wählen", str(backup.backup_dir(self.ctx.con)),
                                              "Sicherung (*.zip *.enc)")
        if not path:
            return
        plain, temporary = Path(path), False
        try:
            if backup_crypto.is_encrypted(path):
                plain, temporary = self._decrypt(Path(path)), True
                if plain is None:
                    return
            backup.check_backup(plain)
        except ValueError as e:
            if temporary:
                plain.unlink(missing_ok=True)
            QMessageBox.warning(self, "Wiederherstellen", str(e))
            return
        if not confirm(self, "Wiederherstellen",
                       "Der aktuelle Datenstand wird durch die Sicherung ersetzt (eine Kopie des aktuellen Stands "
                       "wird im Datenverzeichnis aufbewahrt).\n\nFortfahren?"):
            if temporary:
                plain.unlink(missing_ok=True)
            return
        self.restart_cb(lambda: backup.restore_backup(plain, delete_after=temporary))

    def _decrypt(self, path: Path) -> Path | None:
        """Verschlüsselte Sicherung: erst das eingestellte Passwort versuchen, sonst danach fragen."""
        password = get_setting(self.ctx.con, "backup.password")
        while True:
            try:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    return backup.decrypt_for_restore(path, password)
                finally:
                    QApplication.restoreOverrideCursor()
            except backup_crypto.WrongPassword:
                password, ok = QInputDialog.getText(self, "Verschlüsselte Sicherung",
                                                    "Passwort dieser Sicherung:", QLineEdit.Password)
                if not ok:
                    return None
