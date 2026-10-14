"""Notizen mit Zuordnung zu Kunden und Objekten."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget)

from app.db import repo
from app.money import fmt_date
from app.services import notes
from app.ui.common import (Ctx, GuardedDialog, button, confirm, count, date_edit, date_value, error, hbox, item, make_table,
                           page_title, searchable_combo, selected_data)


def _customer_combo(ctx: Ctx, first: str, include_inactive: bool = False) -> QComboBox:
    cb = QComboBox()
    cb.setEditable(True)
    cb.setInsertPolicy(QComboBox.NoInsert)
    cb.addItem(first, None)
    for c in repo.list_customers(ctx.con, include_inactive=include_inactive):
        cb.addItem(repo.customer_display_name(c) + (f", {c['city']}" if c.get("city") else ""), c["id"])
    cb.completer().setFilterMode(Qt.MatchContains)
    cb.completer().setCompletionMode(QCompleter.PopupCompletion)
    return searchable_combo(cb)


# ======================================================================= Bearbeitung
class NoteDialog(GuardedDialog, QDialog):
    dirty_text = "Die Notiz wurde geändert."

    def __init__(self, parent, ctx: Ctx, note: dict | None = None, embedded: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self.note = dict(note or {})
        self.setWindowTitle("Notiz")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Notiz bearbeiten" if self.note.get("id") else "Neue Notiz")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(button("Zurück zur Liste" if embedded else "Schließen", self.close_dialog))
        lay.addLayout(head)

        box = QFrame()
        box.setObjectName("card")
        f = QFormLayout(box)
        f.setContentsMargins(18, 16, 18, 16)
        f.setVerticalSpacing(10)
        self.date = date_edit(self.note.get("note_date"))
        self.customer = _customer_combo(ctx, "(kein Kunde)", include_inactive=bool(self.note.get("customer_id")))
        self.customer.setCurrentIndex(max(self.customer.findData(self.note.get("customer_id")), 0))
        self.project = QComboBox()
        self.title = QLineEdit(self.note.get("title", ""))
        self.title.setPlaceholderText("z.B. Telefonat, Absprache Termin, Unterlagen angefordert")
        self.body = QPlainTextEdit(self.note.get("body", ""))
        self.body.setMinimumHeight(220)
        f.addRow("Datum", self.date)
        f.addRow("Kunde", self.customer)
        f.addRow("Objekt", self.project)
        f.addRow("Titel", self.title)
        f.addRow("Notiz", self.body)
        lay.addWidget(box)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setProperty("primary", True)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill_projects(self.note.get("project_id"))
        self.customer.currentIndexChanged.connect(lambda *_: self._fill_projects())
        self.mark_clean()

    def _fill_projects(self, project_id=None):
        cid = self.customer.currentData()
        self.project.clear()
        self.project.addItem("–", None)
        if cid:
            for p in repo.list_projects(self.ctx.con, cid):
                self.project.addItem(" – ".join(x for x in [p.get("name"), repo.project_address(p)] if x)
                                     or f"Objekt {p['id']}", p["id"])
        self.project.setCurrentIndex(max(self.project.findData(project_id), 0))
        self.project.setEnabled(bool(cid))

    def _snapshot(self):
        return (date_value(self.date), self.customer.currentData(), self.project.currentData(), self.title.text(),
                self.body.toPlainText())

    def save(self):
        d = {"id": self.note.get("id"), "note_date": date_value(self.date), "customer_id": self.customer.currentData(),
             "project_id": self.project.currentData(), "title": self.title.text(), "body": self.body.toPlainText()}
        try:
            self.note_id = notes.save_note(self.ctx.con, d)
        except Exception as e:  # noqa: BLE001
            error(self, "Notiz nicht gespeichert", e)
            return
        self.mark_clean()
        self.accept()


# ======================================================================= Liste
class NotesPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Notizen"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Titel, Text, Kunde, Objekt) …")
        self.search.textChanged.connect(self.reload)
        self.customer = QComboBox()
        self.customer.setMinimumWidth(260)
        self.customer.currentIndexChanged.connect(self.reload)
        lay.addLayout(hbox(self.search, self.customer))
        lay.addLayout(hbox(button("Neue Notiz", self.new, primary=True), button("Bearbeiten", self.edit),
                           button("Löschen", self.delete, danger=True), None))
        self.table = make_table(["Datum", "Kunde", "Objekt", "Titel", "Text"], stretch_col=4)
        self.table.doubleClicked.connect(self.edit)
        lay.addWidget(self.table)
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        lay.addWidget(self.count_label)
        ctx.listeners.append(self.refresh)
        self.refresh()

    def refresh(self):
        """Nach Änderungen an anderer Stelle: Kundenliste und Notizen neu laden."""
        self._fill_customers()
        self.reload()

    def _fill_customers(self):
        cur = self.customer.currentData()
        self.customer.blockSignals(True)
        self.customer.clear()
        self.customer.addItem("Alle Kunden", None)
        for c in repo.list_customers(self.ctx.con, include_inactive=True):
            self.customer.addItem(repo.customer_display_name(c) + (f", {c['city']}" if c.get("city") else ""), c["id"])
        self.customer.setCurrentIndex(max(self.customer.findData(cur), 0))
        self.customer.blockSignals(False)

    def reload(self, *_):
        rows = notes.list_notes(self.ctx.con, self.customer.currentData(), self.search.text().strip())
        self.table.setRowCount(0)
        for n in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            first_line = (n.get("body") or "").strip().splitlines()[0] if (n.get("body") or "").strip() else ""
            vals = [fmt_date(n["note_date"]), n["customer_name"], n.get("project_name") or "", n.get("title") or "",
                    first_line]
            for i, v in enumerate(vals):
                self.table.setItem(r, i, item(v, n["id"] if i == 0 else None))
        self.count_label.setText(count(len(rows), "Notiz", "Notizen"))

    def show_customer(self, customer_id: int | None):
        """Filtert die Liste auf einen Kunden (z.B. aus der Kundenansicht)."""
        self._fill_customers()
        self.customer.setCurrentIndex(max(self.customer.findData(customer_id), 0))

    def _open(self, note=None):
        host = getattr(self.ctx, "editor_host", None)
        dlg = NoteDialog(self, self.ctx, note, embedded=host is not None)
        if host is not None:
            host.open_embedded(dlg, return_row=host.page_index("notizen"))
        elif dlg.exec():
            self.ctx.changed()

    def new(self):
        cid = self.customer.currentData()
        self._open({"customer_id": cid} if cid else None)

    def edit(self, *_):
        nid = selected_data(self.table)
        if nid:
            self._open(notes.get_note(self.ctx.con, nid))

    def delete(self):
        nid = selected_data(self.table)
        if not nid:
            return
        n = notes.get_note(self.ctx.con, nid)
        label = n.get("title") or (n.get("body") or "").strip().splitlines()[0][:60]
        if confirm(self, "Notiz löschen", f"Notiz vom {fmt_date(n['note_date'])} „{label}“ löschen?"):
            notes.delete_note(self.ctx.con, nid)
            self.ctx.changed()
