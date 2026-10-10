"""Kundenverwaltung (manuelle Eingabe + Importe)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QTabWidget,
                               QVBoxLayout, QWidget)

from app.db import repo
from app.db.database import profile
from app.ui.common import (Ctx, GuardedDialog, button, confirm, error, hbox, item, make_table, page_title,
                           searchable_combo, selected_data)


class ProjectDialog(GuardedDialog, QDialog):
    def __init__(self, parent, ctx: Ctx, customer_id: int, project: dict | None = None):
        super().__init__(parent)
        self.ctx, self.customer_id, self.project = ctx, customer_id, project or {}
        self.setWindowTitle("Projekt / Objekt")
        self.setMinimumWidth(460)
        f = QFormLayout(self)
        self.name = QLineEdit(self.project.get("name", ""))
        self.street = QLineEdit(self.project.get("object_street", ""))
        self.zip = QLineEdit(self.project.get("object_zip", ""))
        self.zip.setMaximumWidth(90)
        self.city = QLineEdit(self.project.get("object_city", ""))
        self.notes = QPlainTextEdit(self.project.get("notes", ""))
        self.notes.setMaximumHeight(70)
        f.addRow("Bezeichnung", self.name)
        f.addRow("Objekt Straße", self.street)
        f.addRow("PLZ / Ort", hbox(self.zip, self.city))
        f.addRow("Notiz", self.notes)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.dirty_text = "Das Projekt wurde geändert."
        self.mark_clean()

    def _snapshot(self):
        return (self.name.text(), self.street.text(), self.zip.text(), self.city.text(), self.notes.toPlainText())

    def save(self):
        if not (self.name.text().strip() or self.street.text().strip()):
            QMessageBox.warning(self, "Projekt", "Bitte Bezeichnung oder Objektadresse angeben.")
            return
        data = {"customer_id": self.customer_id, "name": self.name.text().strip(),
                "object_street": self.street.text().strip(), "object_zip": self.zip.text().strip(),
                "object_city": self.city.text().strip(), "notes": self.notes.toPlainText()}
        if self.project.get("id"):
            data["id"] = self.project["id"]
        self.project_id = repo.save_project(self.ctx.con, data)
        self.mark_clean()
        self.accept()


class CustomerDialog(GuardedDialog, QDialog):
    def __init__(self, parent, ctx: Ctx, customer: dict | None = None, prefill: dict | None = None,
                 embedded: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self.customer = customer or {}
        src = dict(prefill or {}) if not customer else self.customer
        self.setWindowTitle("Kunde bearbeiten" if customer else "Neuer Kunde")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel(repo.customer_display_name(self.customer) if customer else "Neuer Kunde")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(button("Zurück zur Liste" if embedded else "Schließen", self.close_dialog))
        lay.addLayout(head)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        w = QWidget()
        g = QGridLayout(w)
        self.fields: dict[str, QLineEdit] = {}

        def le(key, width=None):
            e = QLineEdit(str(src.get(key) or ""))
            if width:
                e.setMaximumWidth(width)
            self.fields[key] = e
            return e

        self.salutation = QComboBox()
        self.salutation.setEditable(True)
        self.salutation.addItems(["", "Frau", "Herr", "Firma", "Familie", "Eheleute"])
        self.salutation.setCurrentText(src.get("salutation", ""))
        r = 0
        g.addWidget(QLabel("Kundennr."), r, 0); g.addWidget(le("customer_no", 120), r, 1)
        if not customer:
            self.fields["customer_no"].setPlaceholderText("automatisch")
        r += 1
        g.addWidget(QLabel("Firma"), r, 0); g.addWidget(le("company"), r, 1, 1, 3)
        r += 1
        g.addWidget(QLabel("Anrede / Titel"), r, 0); g.addWidget(self.salutation, r, 1); g.addWidget(le("title", 120), r, 2)
        r += 1
        g.addWidget(QLabel("Vorname"), r, 0); g.addWidget(le("first_name"), r, 1)
        g.addWidget(QLabel("Nachname"), r, 2); g.addWidget(le("last_name"), r, 3)
        r += 1
        g.addWidget(QLabel("Straße + Nr."), r, 0); g.addWidget(le("street"), r, 1, 1, 3)
        r += 1
        g.addWidget(QLabel("PLZ / Ort"), r, 0); g.addWidget(le("zip", 90), r, 1); g.addWidget(le("city"), r, 2, 1, 2)
        r += 1
        g.addWidget(QLabel("Land"), r, 0); g.addWidget(le("country", 60), r, 1)
        r += 1
        g.addWidget(QLabel("Briefanrede"), r, 0); g.addWidget(le("letter_salutation"), r, 1, 1, 2)
        g.addWidget(button("Vorschlag", self.suggest_salutation), r, 3)
        r += 1
        g.addWidget(QLabel("E-Mail"), r, 0); g.addWidget(le("email"), r, 1, 1, 3)
        r += 1
        g.addWidget(QLabel("Telefon"), r, 0); g.addWidget(le("phone"), r, 1)
        g.addWidget(QLabel("Mobil"), r, 2); g.addWidget(le("mobile"), r, 3)
        r += 1
        g.addWidget(QLabel("USt-IdNr."), r, 0); g.addWidget(le("vat_id"), r, 1)
        g.addWidget(QLabel("Leitweg-ID / Käuferref."), r, 2); g.addWidget(le("buyer_reference"), r, 3)
        r += 1
        self.notes = QPlainTextEdit(src.get("notes") or "")
        self.notes.setMaximumHeight(70)
        g.addWidget(QLabel("Notiz"), r, 0); g.addWidget(self.notes, r, 1, 1, 3)
        if not self.fields["country"].text():
            self.fields["country"].setText("DE")
        if src.get("source") == "hottgenroth" or src.get("hottgenroth_uuid"):
            r += 1
            info = QLabel("Quelle: Hottgenroth" + (" – lokal geändert" if src.get("locally_modified") else ""))
            info.setStyleSheet("color:#5A6A6E")
            g.addWidget(info, r, 1, 1, 3)
        tabs.addTab(w, "Stammdaten")

        # Projekte
        pw = QWidget()
        pl = QVBoxLayout(pw)
        self.ptable = make_table(["Bezeichnung", "Objektadresse"], stretch_col=1)
        self.ptable.doubleClicked.connect(self.edit_project)
        pl.addWidget(self.ptable)
        pl.addLayout(hbox(button("Neues Projekt", self.new_project), button("Bearbeiten", self.edit_project),
                          button("Löschen", self.delete_project, danger=True), None))
        tabs.addTab(pw, "Projekte / Objekte")
        if not customer:
            tabs.setTabEnabled(1, False)
            tabs.setTabToolTip(1, "Erst speichern, dann Projekte anlegen")
        self.load_projects()

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.customer_id = self.customer.get("id")
        self.dirty_text = "Die Kundendaten wurden geändert."
        self.mark_clean()

    def _snapshot(self):
        return self.data()

    def data(self) -> dict:
        d = {k: e.text().strip() for k, e in self.fields.items()}
        d["salutation"] = self.salutation.currentText().strip()
        d["notes"] = self.notes.toPlainText()
        return d

    def suggest_salutation(self):
        self.fields["letter_salutation"].setText(repo.default_letter_salutation(self.data()))

    def save(self):
        d = self.data()
        if not (d.get("last_name") or d.get("company")):
            QMessageBox.warning(self, "Kunde", "Bitte Nachname oder Firma angeben.")
            return
        if not d.get("customer_no"):
            d.pop("customer_no")
        if not d.get("letter_salutation"):
            d["letter_salutation"] = repo.default_letter_salutation(d)
        if not self.customer:
            similar = repo.find_similar_customers(self.ctx.con, d)
            if similar and not confirm(self, "Mögliche Dublette",
                                       "Es gibt bereits ähnliche Kunden:\n\n" +
                                       "\n".join(f"• {repo.customer_display_name(s)}, {s['zip']} {s['city']}" for s in similar[:5]) +
                                       "\n\nTrotzdem neu anlegen?"):
                return
        try:
            if self.customer.get("id"):
                d["id"] = self.customer["id"]
            else:
                d.setdefault("source", "manuell")
            self.customer_id = repo.save_customer(self.ctx.con, d)
        except Exception as e:  # noqa: BLE001
            error(self, "Speichern fehlgeschlagen", e)
            return
        self.mark_clean()
        self.accept()

    def load_projects(self):
        self.ptable.setRowCount(0)
        if not self.customer.get("id"):
            return
        for p in repo.list_projects(self.ctx.con, self.customer["id"]):
            r = self.ptable.rowCount()
            self.ptable.insertRow(r)
            self.ptable.setItem(r, 0, item(p["name"], p["id"]))
            self.ptable.setItem(r, 1, item(repo.project_address(p)))

    def new_project(self):
        if ProjectDialog(self, self.ctx, self.customer["id"]).exec():
            self.load_projects()

    def edit_project(self, *_):
        pid = selected_data(self.ptable)
        if pid and ProjectDialog(self, self.ctx, self.customer["id"], repo.get_project(self.ctx.con, pid)).exec():
            self.load_projects()

    def delete_project(self):
        pid = selected_data(self.ptable)
        if pid and confirm(self, "Projekt löschen", "Projekt wirklich löschen?"):
            try:
                repo.delete_project(self.ctx.con, pid)
            except ValueError as e:
                error(self, "Löschen nicht möglich", e)
            self.load_projects()


class CustomersPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Kunden"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Name, Firma, Ort, Kundennr., E-Mail) …")
        self.search.textChanged.connect(self.reload)
        lay.addLayout(hbox(self.search))
        self.hottgenroth_btn = button("Aus Hottgenroth importieren", self.import_hottgenroth)
        lay.addLayout(hbox(button("Neuer Kunde", self.new, primary=True),
                           button("Bearbeiten", self.edit),
                           button("Entfernen", self.remove, danger=True),
                           button("Notizen", self.show_notes, tooltip="Notizen zum markierten Kunden anzeigen"),
                           None,
                           self.hottgenroth_btn,
                           button("CSV/Excel importieren", self.import_csv)))
        self.table = make_table(["Kundennr.", "Name", "Straße", "PLZ", "Ort", "E-Mail", "Telefon", "Quelle"], stretch_col=1)
        self.table.doubleClicked.connect(self.edit)
        lay.addWidget(self.table)
        self.count = QLabel()
        lay.addWidget(self.count)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        self.hottgenroth_btn.setVisible(profile(self.ctx.con).hottgenroth)
        rows = repo.list_customers(self.ctx.con, self.search.text().strip())
        self.table.setRowCount(0)
        for c in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [c["customer_no"], repo.customer_display_name(c), c["street"], c["zip"], c["city"], c["email"],
                    c["phone"] or c["mobile"], {"hottgenroth": "Hottgenroth", "csv": "CSV", "import": "Rechnungsimport"}.get(c["source"], "manuell")]
            for i, v in enumerate(vals):
                self.table.setItem(r, i, item(v, c["id"] if i == 0 else None))
        self.count.setText(f"{len(rows)} Kunden")

    def _open(self, customer=None):
        host = getattr(self.ctx, "editor_host", None)
        dlg = CustomerDialog(self, self.ctx, customer, embedded=host is not None)
        if host is not None:
            host.open_embedded(dlg, return_row=host.page_index("kunden"))
        elif dlg.exec():
            self.ctx.changed()

    def new(self):
        self._open()

    def edit(self, *_):
        cid = selected_data(self.table)
        if cid:
            self._open(repo.get_customer(self.ctx.con, cid))

    def remove(self):
        cid = selected_data(self.table)
        if not cid:
            return
        c = repo.get_customer(self.ctx.con, cid)
        if confirm(self, "Kunde entfernen", f"{repo.customer_display_name(c)} entfernen?\n\n"
                   "Kunden mit Belegen werden nur deaktiviert (GoBD)."):
            repo.deactivate_customer(self.ctx.con, cid)
            self.ctx.changed()

    def import_hottgenroth(self):
        from app.ui.import_dialogs import run_hottgenroth_import
        if not profile(self.ctx.con).hottgenroth:
            return
        if run_hottgenroth_import(self, self.ctx):
            self.ctx.changed()

    def show_notes(self):
        host = getattr(self.ctx, "editor_host", None)
        if host is not None:
            host.show_notes(selected_data(self.table))

    def import_csv(self):
        from app.ui.import_dialogs import run_csv_import
        if run_csv_import(self, self.ctx):
            self.ctx.changed()


class CustomerPicker(QComboBox):
    """Durchsuchbare Kundenauswahl."""

    def __init__(self, ctx: Ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.completer().setFilterMode(Qt.MatchContains)
        self.completer().setCompletionMode(self.completer().CompletionMode.PopupCompletion)
        searchable_combo(self)
        self.reload()

    def reload(self, select_id: int | None = None):
        cur = select_id if select_id is not None else self.current_id()
        self.blockSignals(True)
        self.clear()
        self.addItem("– Kunde wählen –", None)
        for c in repo.list_customers(self.ctx.con):
            self.addItem(f"{repo.customer_display_name(c)} – {c['zip']} {c['city']} ({c['customer_no']})", c["id"])
        self.blockSignals(False)
        if cur:
            self.select(cur)

    def current_id(self):
        idx = self.currentIndex()
        return self.itemData(idx) if idx >= 0 else None

    def select(self, cid):
        for i in range(self.count()):
            if self.itemData(i) == cid:
                self.setCurrentIndex(i)
                return
        # ggf. deaktivierter Kunde (alter Beleg)
        c = repo.get_customer(self.ctx.con, cid)
        if c:
            self.addItem(f"{repo.customer_display_name(c)} (inaktiv)", c["id"])
            self.setCurrentIndex(self.count() - 1)
