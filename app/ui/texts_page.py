"""Textbausteine und Artikel/Leistungen pflegen – mit Rückfrage bei ungespeicherten Änderungen."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from app.db import repo
from app.db.database import get_setting, set_setting
from app.money import fmt_eur, to_cents
from app.ui.common import (CANCEL, SAVE, Ctx, ask_save, button, confirm, hbox, item, make_table, page_title,
                           selected_data)


class _FormGuard:
    """Merkt sich den gespeicherten Formularstand und fragt vor dem Verlassen nach."""

    dirty_text = "Es gibt ungespeicherte Änderungen."
    _clean = None

    def mark_clean(self):
        self._clean = self._snapshot()

    def is_dirty(self) -> bool:
        return self._clean is not None and self._snapshot() != self._clean

    def confirm_leave(self) -> bool:
        if not self.is_dirty():
            return True
        r = ask_save(self, self.dirty_text)
        if r == CANCEL:
            return False
        if r == SAVE:
            return self.save()
        return True


class TextblockTab(_FormGuard, QWidget):
    dirty_text = "Der Textbaustein wurde geändert."

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.current_id = None
        lay = QVBoxLayout(self)
        sp = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        self.cat_filter = QComboBox()
        self.cat_filter.addItem("Alle Kategorien", None)
        for k, v in repo.TEXT_CATEGORIES.items():
            self.cat_filter.addItem(v, k)
        self.cat_filter.currentIndexChanged.connect(lambda *_: self._reload_keep())
        ll.addWidget(self.cat_filter)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._item_changed)
        ll.addWidget(self.list)
        ll.addLayout(hbox(button("Neu", self.new), button("Löschen", self.delete, danger=True), None))
        sp.addWidget(left)
        right = QWidget()
        f = QFormLayout(right)
        self.category = QComboBox()
        for k, v in repo.TEXT_CATEGORIES.items():
            self.category.addItem(v, k)
        self.title = QLineEdit()
        self.body = QPlainTextEdit()
        self.is_default = QCheckBox("Standard für diese Kategorie (wird bei neuen Belegen eingesetzt)")
        f.addRow("Kategorie", self.category)
        f.addRow("Titel", self.title)
        f.addRow("Text", self.body)
        f.addRow("", self.is_default)
        ph = QLabel("Platzhalter: {briefanrede} {kunde} {faellig} {zahlungsziel} {iban} {bic} {belegnr} {betrag} "
                    "{gueltig_bis} {datum} {leistungsdatum} {inhaber} {firma} {objekt_adresse} {projekt} "
                    "{ref_belegnr} {ref_datum}. In Mahnungen zusätzlich: {offen} {gebuehr} {gesamt} {neu_faellig} "
                    "{rechnungsnr} {rechnungsdatum}")
        ph.setWordWrap(True)
        ph.setObjectName("muted")
        f.addRow("", ph)
        f.addRow("", hbox(None, button("Speichern", self.save, primary=True)))
        sp.addWidget(right)
        sp.setSizes([320, 700])
        lay.addWidget(sp)
        self.remember_default = QCheckBox("„In Textbausteine übernehmen“ unter Textfeldern (Belegtexte, Positionsbeschreibung, "
                                          "Anlass einer Fahrt) standardmäßig anhaken")
        self.remember_default.setChecked(get_setting(ctx.con, "textblock.remember_default", "1") == "1")
        self.remember_default.toggled.connect(
            lambda on: set_setting(ctx.con, "textblock.remember_default", "1" if on else "0"))
        lay.addWidget(self.remember_default)
        ctx.listeners.append(self._reload_keep)  # z.B. neuer Anlass aus einer Fahrt
        self.reload()
        self.mark_clean()

    def _snapshot(self):
        return (self.current_id, self.category.currentIndex(), self.title.text(), self.body.toPlainText(),
                self.is_default.isChecked())

    def reload(self, *_):
        self.list.clear()
        for tb in repo.list_textblocks(self.ctx.con, self.cat_filter.currentData()):
            it = QListWidgetItem(f"{'★ ' if tb['is_default'] else ''}{repo.TEXT_CATEGORIES.get(tb['category'], tb['category'])}: {tb['title']}")
            it.setData(Qt.UserRole, tb)
            self.list.addItem(it)

    def _reload_keep(self):
        self.list.blockSignals(True)
        self.reload()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole)["id"] == self.current_id:
                self.list.setCurrentRow(i)
        self.list.blockSignals(False)

    def _item_changed(self, cur, prev):
        if not self.confirm_leave():
            self.list.blockSignals(True)
            self.list.setCurrentItem(prev)
            self.list.blockSignals(False)
            return
        self.load(cur)

    def load(self, cur, _prev=None):
        tb = cur.data(Qt.UserRole) if cur else None
        self.current_id = tb["id"] if tb else None
        if tb:
            self.category.setCurrentIndex(self.category.findData(tb["category"]))
            self.title.setText(tb["title"])
            self.body.setPlainText(tb["body"])
            self.is_default.setChecked(bool(tb["is_default"]))
        self.mark_clean()

    def new(self):
        if not self.confirm_leave():
            return
        self.current_id = None
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self.title.clear()
        self.body.clear()
        self.is_default.setChecked(False)
        if self.cat_filter.currentData():
            self.category.setCurrentIndex(self.category.findData(self.cat_filter.currentData()))
        self.mark_clean()
        self.title.setFocus()

    def save(self) -> bool:
        if not self.title.text().strip():
            QMessageBox.warning(self, "Textbaustein", "Bitte einen Titel angeben.")
            return False
        self.current_id = repo.save_textblock(self.ctx.con, {
            "id": self.current_id, "category": self.category.currentData(), "title": self.title.text().strip(),
            "body": self.body.toPlainText(), "is_default": self.is_default.isChecked()})
        self.mark_clean()
        self._reload_keep()
        return True

    def delete(self):
        if self.current_id and confirm(self, "Löschen", "Textbaustein löschen?"):
            repo.delete_textblock(self.ctx.con, self.current_id)
            self.current_id = None
            self.title.clear()
            self.body.clear()
            self.mark_clean()
            self._reload_keep()


class ArticleTab(_FormGuard, QWidget):
    dirty_text = "Der Artikel wurde geändert."

    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        self.current_id = None
        self._sel_id = None  # Artikel-ID der markierten Zeile (Tabelle ist sortierbar, Zeilennummern wandern)
        lay = QVBoxLayout(self)
        self.table = make_table(["Bezeichnung", "Beschreibung", "Einheit", "Preis", "USt %", "Aktiv"], stretch_col=1)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        lay.addWidget(self.table)
        f = QFormLayout()
        self.title = QLineEdit()
        self.desc = QPlainTextEdit()
        self.desc.setFixedHeight(60)
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(["Stunden", "Stück", "pauschal", "km", "Tage", "m²"])
        self.price = QLineEdit()
        self.tax = QLineEdit("19")
        self.active = QCheckBox("aktiv")
        self.active.setChecked(True)
        f.addRow("Bezeichnung", self.title)
        f.addRow("Beschreibung", self.desc)
        f.addRow("Einheit", self.unit)
        f.addRow("Preis € (netto)", self.price)
        f.addRow("USt % (bei Regelbesteuerung)", self.tax)
        f.addRow("", self.active)
        lay.addLayout(f)
        lay.addLayout(hbox(button("Neu", self.new), button("Löschen", self.delete, danger=True), None,
                           button("Speichern", self.save, primary=True)))
        self.reload()
        self.mark_clean()

    def _snapshot(self):
        return (self.current_id, self.title.text(), self.desc.toPlainText(), self.unit.currentText(), self.price.text(),
                self.tax.text(), self.active.isChecked())

    def reload(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._sel_id = None
        for a in repo.list_articles(self.ctx.con, include_inactive=True):
            r = self.table.rowCount()
            self.table.insertRow(r)
            for i, v in enumerate([a["title"], a["description"], a["unit"], fmt_eur(a["unit_price_cents"]),
                                   a["tax_rate"], "ja" if a["active"] else "nein"]):
                self.table.setItem(r, i, item(v, a if i == 0 else None, align_right=i == 3))
            if a["id"] == self.current_id:
                self.table.selectRow(r)
                self._sel_id = a["id"]
        self.table.blockSignals(False)

    def _selection_changed(self):
        a = selected_data(self.table)
        sel_id = a["id"] if a else None
        if sel_id == self._sel_id:
            return
        if not self.confirm_leave():
            self.table.blockSignals(True)
            self.table.clearSelection()
            for r in range(self.table.rowCount()):
                if (self.table.item(r, 0).data(Qt.UserRole) or {}).get("id") == self._sel_id:
                    self.table.selectRow(r)
            self.table.blockSignals(False)
            return
        self._sel_id = sel_id
        self.load()

    def load(self):
        a = selected_data(self.table)
        if not a:
            return
        self.current_id = a["id"]
        self.title.setText(a["title"])
        self.desc.setPlainText(a["description"])
        self.unit.setCurrentText(a["unit"])
        self.price.setText(fmt_eur(a["unit_price_cents"], False))
        self.tax.setText(a["tax_rate"])
        self.active.setChecked(bool(a["active"]))
        self.mark_clean()

    def new(self):
        if not self.confirm_leave():
            return
        self._clear_form()
        self.title.setFocus()

    def _clear_form(self):
        self.current_id = None
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.blockSignals(False)
        self._sel_id = None
        self.title.clear()
        self.price.clear()
        self.desc.clear()
        self.unit.setCurrentText("Stunden")
        self.tax.setText("19")
        self.active.setChecked(True)
        self.mark_clean()

    def save(self) -> bool:
        if not self.title.text().strip():
            QMessageBox.warning(self, "Artikel", "Bitte eine Bezeichnung angeben.")
            return False
        try:
            price = to_cents(self.price.text() or "0")
        except ValueError as e:
            QMessageBox.warning(self, "Artikel", str(e))
            return False
        self.current_id = repo.save_article(self.ctx.con, {
            "id": self.current_id, "title": self.title.text().strip(), "description": self.desc.toPlainText(),
            "unit": self.unit.currentText(), "unit_price_cents": price, "tax_rate": self.tax.text().strip() or "0",
            "active": int(self.active.isChecked())})
        self.reload()
        self.mark_clean()
        return True

    def delete(self):
        if self.current_id and confirm(self, "Löschen", "Artikel löschen? (Bestehende Belege bleiben unverändert.)"):
            repo.delete_article(self.ctx.con, self.current_id)
            self._clear_form()
            self.reload()


class TextsPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Textbausteine & Artikel"))
        tabs = QTabWidget()
        self.tb_tab, self.art_tab = TextblockTab(ctx), ArticleTab(ctx)
        tabs.addTab(self.tb_tab, "Textbausteine")
        tabs.addTab(self.art_tab, "Artikel / Leistungen")
        lay.addWidget(tabs)

    def confirm_leave(self) -> bool:
        return self.tb_tab.confirm_leave() and self.art_tab.confirm_leave()
