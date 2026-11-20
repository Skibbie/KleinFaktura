"""Artikel: Import aus Excel/CSV mit Vorschau, Vorlage und Export sowie die Artikelauswahl im Beleg-Editor."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel,
                               QLineEdit, QMessageBox, QRadioButton, QVBoxLayout)

from app.db import repo
from app.importers import article_import as ai
from app.importers.csv_import import read_table
from app.money import fmt_eur
from app.ui import theme
from app.ui.common import Ctx, error, hbox, item, make_table

FILE_FILTER = "Tabellen (*.xlsx *.xlsm *.csv *.txt)"
STATUS_COLOR = {ai.NEW: theme.ACTION, ai.EXISTS: "#A06A00", ai.DUPLICATE: theme.INK_2, ai.INVALID: theme.DANGER}


def category_filter_combo(con, keep=None) -> QComboBox:
    """Auswahl „Alle Kategorien“ / „Ohne Kategorie“ / vorhandene Kategorien."""
    cb = QComboBox()
    fill_category_filter(cb, con, keep)
    return cb


def fill_category_filter(cb: QComboBox, con, keep=None) -> None:
    cb.blockSignals(True)
    cb.clear()
    cb.addItem("Alle Kategorien", None)
    cb.addItem("Ohne Kategorie", "")
    for c in repo.article_categories(con):
        cb.addItem(c, c)
    idx = cb.findData(keep) if keep is not None else 0
    cb.setCurrentIndex(max(idx, 0))
    cb.blockSignals(False)


# ======================================================================= Import
class ArticleImportDialog(QDialog):
    """Vorschau: was neu angelegt wird, was schon vorhanden ist (überspringen oder aktualisieren) und was nicht geht."""

    def __init__(self, parent, rows: list[ai.Row]):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle("Artikel importieren – Vorschau")
        self.resize(1000, 560)
        lay = QVBoxLayout(self)
        counts = {s: sum(r.status == s for r in rows) for s in STATUS_COLOR}
        summary = (f"{counts[ai.NEW]} neu, {counts[ai.EXISTS]} schon vorhanden (gleiche Bezeichnung)")
        if counts[ai.DUPLICATE] or counts[ai.INVALID]:
            summary += f", {counts[ai.DUPLICATE] + counts[ai.INVALID]} werden nicht übernommen"
        lay.addWidget(QLabel(summary + "."))
        self.table = make_table(["Status", "Zeile", "Bezeichnung", "Kategorie", "Einheit", "Preis", "USt %",
                                 "Hinweis"], stretch_col=2)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            d = row.data
            price = d.get("unit_price_cents")
            hint = row.message
            if row.status == ai.EXISTS:
                hint = self._changes(row.existing, d, row.update_fields) or "keine Änderung"
            values = [row.status, row.line, d.get("title", ""), d.get("category", ""), d.get("unit", ""),
                      fmt_eur(price) if isinstance(price, int) else "", d.get("tax_rate", ""), hint]
            for i, v in enumerate(values):
                self.table.setItem(r, i, item(v, color=STATUS_COLOR[row.status] if i == 0 else None,
                                              align_right=i in (1, 5)))
        lay.addWidget(self.table)
        self.skip = QRadioButton("Vorhandene Artikel überspringen (nur neue anlegen)")
        self.update_rb = QRadioButton("Vorhandene Artikel aktualisieren (Beschreibung, Einheit, Preis, USt, Kategorie)")
        self.skip.setChecked(True)
        for rb in (self.skip, self.update_rb):
            rb.setEnabled(counts[ai.EXISTS] > 0)
            lay.addWidget(rb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Importieren")
        bb.button(QDialogButtonBox.Ok).setEnabled(counts[ai.NEW] + counts[ai.EXISTS] > 0)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _changes(old: dict, new: dict, fields) -> str:
        out = []
        for f in (f for f in ("description", "unit", "unit_price_cents", "tax_rate", "category") if f in fields):
            a, b = old.get(f) or "", new.get(f) or ""
            if f == "unit_price_cents":
                a, b = int(a or 0), int(b or 0)
                if a != b:
                    out.append(f"Preis {fmt_eur(a)} → {fmt_eur(b)}")
            elif str(a).strip() != str(b).strip():
                out.append(f"{ai.HEADERS[f]} geändert")
        return ", ".join(out)

    def update_existing(self) -> bool:
        return self.update_rb.isChecked()


def run_article_import(parent, ctx: Ctx) -> bool:
    path, _ = QFileDialog.getOpenFileName(parent, "Artikeldatei wählen", "", FILE_FILTER)
    if not path:
        return False
    try:
        headers, data = read_table(Path(path))
    except Exception as e:  # noqa: BLE001 – beliebige Lesefehler (Format, Zugriff) als Meldung
        error(parent, "Datei konnte nicht gelesen werden", e)
        return False
    if not headers:
        QMessageBox.information(parent, "Import", "Die Datei enthält keine Daten.")
        return False
    from app.ui.import_dialogs import CsvMappingDialog
    dlg = CsvMappingDialog(parent, headers, data, ai.TARGET_FIELDS, ai.guess_mapping(headers))
    if not dlg.exec():
        return False
    mapping = dlg.mapping()
    if "title" not in mapping:
        QMessageBox.warning(parent, "Import", "Bitte einer Spalte das Feld „Bezeichnung“ zuordnen.")
        return False
    from app.db.database import get_setting
    rows = ai.plan_import(ctx.con, ai.rows_to_articles(data, mapping, get_setting(ctx.con, "tax.default_rate", "19")))
    if not rows:
        QMessageBox.information(parent, "Import", "In der Datei wurden keine Artikel gefunden.")
        return False
    preview = ArticleImportDialog(parent, rows)
    if not preview.exec():
        return False
    try:
        n_new, n_upd, n_skip = ai.apply_import(ctx.con, rows, preview.update_existing())
    except Exception as e:  # noqa: BLE001 – Import wird komplett zurückgerollt, Ursache anzeigen
        error(parent, "Import fehlgeschlagen", e)
        return False
    QMessageBox.information(parent, "Import abgeschlossen",
                            f"Neu angelegt: {n_new}\nAktualisiert: {n_upd}\nNicht übernommen: {n_skip}")
    return True


def _save_path(parent, title: str, name: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, str(Path.home() / name),
                                          "Excel-Arbeitsmappe (*.xlsx);;CSV (Semikolon) (*.csv)")
    if not path:
        return None
    path = Path(path)
    return path if path.suffix.lower() in (".xlsx", ".csv") else path.with_suffix(".xlsx")


def save_article_template(parent) -> Path | None:
    path = _save_path(parent, "Vorlage speichern", "Artikel-Vorlage.xlsx")
    if path is None:
        return None
    try:
        ai.write_template(path)
    except Exception as e:  # noqa: BLE001 – z.B. Datei noch in Excel geöffnet
        error(parent, "Vorlage konnte nicht gespeichert werden", e)
        return None
    QMessageBox.information(parent, "Vorlage gespeichert",
                            f"{path}\n\nIn Excel ausfüllen (eine Zeile je Artikel) und über „Importieren…“ einlesen.")
    return path


def export_articles(parent, ctx: Ctx) -> Path | None:
    path = _save_path(parent, "Artikel exportieren", "Artikel.xlsx")
    if path is None:
        return None
    try:
        ai.export_articles(ctx.con, path)
    except Exception as e:  # noqa: BLE001 – z.B. Datei noch in Excel geöffnet
        error(parent, "Export fehlgeschlagen", e)
        return None
    QMessageBox.information(parent, "Export abgeschlossen",
                            f"{path}\n\nNach dem Bearbeiten in Excel über „Importieren…“ wieder einlesen "
                            "(vorhandene Artikel dabei „aktualisieren“).")
    return path


# ======================================================================= Auswahl im Editor
class ArticlePickerDialog(QDialog):
    """Artikel suchen und auswählen: Suchfeld mit Live-Filter, Kategorie, Doppelklick oder Enter übernimmt."""

    def __init__(self, parent, con, articles: list[dict] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Artikel / Leistung einfügen")
        self.resize(760, 480)
        self.articles = repo.list_articles(con) if articles is None else articles
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen in Bezeichnung und Beschreibung …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.search.installEventFilter(self)
        self.category = category_filter_combo(con)
        self.category.currentIndexChanged.connect(self.refresh)
        lay.addLayout(hbox(self.search, self.category))
        self.table = make_table(["Bezeichnung", "Kategorie", "Preis", "Einheit"], stretch_col=0)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.doubleClicked.connect(lambda *_: self.accept())
        self.table.installEventFilter(self)
        lay.addWidget(self.table)
        self.count = QLabel()
        self.count.setObjectName("muted")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Einfügen")
        self.ok = bb.button(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addLayout(hbox(self.count, None, bb))
        self.refresh()
        self.search.setFocus()

    def refresh(self, *_):
        shown = repo.filter_articles(self.articles, self.search.text(), self.category.currentData())
        self.table.setRowCount(0)
        for a in shown:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for i, v in enumerate([a["title"], a.get("category") or "", fmt_eur(a["unit_price_cents"]), a["unit"]]):
                self.table.setItem(r, i, item(v, a if i == 0 else None, align_right=i == 2))
        if shown:
            self.table.selectRow(0)
        self.ok.setEnabled(bool(shown))
        self.count.setText(f"{len(shown)} von {len(self.articles)} Artikeln")

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress:
            if obj is self.search and ev.key() in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
                self.table.setFocus()           # Pfeiltasten im Suchfeld blättern in der Liste
                self.table.keyPressEvent(ev)
                return True
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.accept()
                return True
        return super().eventFilter(obj, ev)

    def selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole)

    def accept(self):
        if self.selected() is not None:
            super().accept()
