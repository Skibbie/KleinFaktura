"""Import-Dialoge: Hottgenroth, CSV/Excel, bestehende Rechnungen – inkl. Kollisionsrückfragen."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from app import config
from app.db import repo
from app.db.database import company, get_int_setting
from app.importers import csv_import, hottgenroth, legacy_invoices
from app.importers.hottgenroth import COMPARE_FIELDS, FIELD_LABELS, ImportItem
from app.money import fmt_eur, fmt_qty, input_cents, input_decimal, line_total_cents
from app.ui.common import (Ctx, button, check_item, date_edit, date_value, error, hbox, item, make_table, open_file,
                           set_date)


# ======================================================================= Kollision Kunde
class CustomerCollisionDialog(QDialog):
    """Feldvergleich lokal | Import mit Auswahl pro Feld oder pro Kunde."""

    def __init__(self, parent, it: ImportItem, source_label: str = "Hottgenroth", remaining: int = 0):
        super().__init__(parent)
        self.it = it
        self.setWindowTitle("Abweichende Kundendaten")
        self.setMinimumWidth(760)
        lay = QVBoxLayout(self)
        inc = it.incoming
        if it.status == "dublette":
            msg = (f"Für <b>{repo.customer_display_name(inc)}</b> aus {source_label} gibt es bereits einen ähnlichen "
                   "Kunden ohne Verknüpfung. Ist das derselbe Kunde?")
        else:
            msg = (f"<b>{repo.customer_display_name(inc)}</b> wurde in {config.APP_NAME} geändert und weicht von "
                   f"{source_label} ab. Welche Daten sollen gelten?")
        lbl = QLabel(msg)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)

        self.candidate = None
        if it.status == "dublette" and len(it.candidates) > 1:
            self.candidate = QComboBox()
            for c in it.candidates:
                self.candidate.addItem(f"{repo.customer_display_name(c)} – {c['street']}, {c['zip']} {c['city']} ({c['customer_no']})", c["id"])
            self.candidate.currentIndexChanged.connect(self._candidate_changed)
            lay.addLayout(hbox(QLabel("Vorhandener Kunde:"), self.candidate, None))

        self.grid_box = QGroupBox("Feldvergleich – abweichende Felder sind markiert")
        self.grid = QGridLayout(self.grid_box)
        lay.addWidget(self.grid_box)
        self.groups: dict[str, QButtonGroup] = {}
        self._build_grid()

        self.apply_all = QCheckBox(f"Entscheidung für alle weiteren Kollisionen übernehmen ({remaining} weitere)")
        self.apply_all.setVisible(remaining > 0)
        lay.addWidget(self.apply_all)

        row = QHBoxLayout()
        row.addWidget(button("Lokal behalten", lambda: self._done("keep_local")))
        row.addWidget(button(f"{source_label} übernehmen", lambda: self._done("take_remote")))
        row.addWidget(button("Zusammenführen (Auswahl)", lambda: self._done("merge"), primary=True))
        row.addWidget(button("Als neuen Kunden anlegen", lambda: self._done("new")))
        row.addWidget(button("Überspringen", lambda: self._done("skip")))
        lay.addLayout(row)

    def _local(self):
        if self.candidate is not None:
            cid = self.candidate.currentData()
            return next(c for c in self.it.candidates if c["id"] == cid)
        return self.it.local or {}

    def _candidate_changed(self):
        self._build_grid()

    def _build_grid(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        local, inc = self._local(), self.it.incoming
        self.grid.addWidget(QLabel("<b>Feld</b>"), 0, 0)
        self.grid.addWidget(QLabel(f"<b>{config.APP_NAME} (lokal)</b>"), 0, 1)
        self.grid.addWidget(QLabel("<b>Import</b>"), 0, 2)
        self.groups = {}
        for r, f in enumerate(COMPARE_FIELDS, start=1):
            lv, iv = str(local.get(f) or ""), str(inc.get(f) or "")
            differs = lv.strip() != iv.strip()
            lab = QLabel(FIELD_LABELS[f])
            rb_l = QRadioButton(lv or "–")
            rb_i = QRadioButton(iv or "–")
            grp = QButtonGroup(self)
            grp.addButton(rb_l, 0)
            grp.addButton(rb_i, 1)
            (rb_l if (lv or not iv) else rb_i).setChecked(True)
            if differs:
                for w in (lab, rb_l, rb_i):
                    w.setStyleSheet("background:#fff4d6; font-weight:600")
            else:
                rb_l.setEnabled(False)
                rb_i.setEnabled(False)
            self.grid.addWidget(lab, r, 0)
            self.grid.addWidget(rb_l, r, 1)
            self.grid.addWidget(rb_i, r, 2)
            self.groups[f] = grp

    def _done(self, decision: str):
        local, inc = self._local(), self.it.incoming
        self.it.decision = decision
        self.it.link_to = local.get("id") if local else None
        if decision == "merge":
            self.it.merged = {f: (inc.get(f) if self.groups[f].checkedId() == 1 else local.get(f)) or "" for f in COMPARE_FIELDS}
        self.accept()


def resolve_collisions(parent, items: list[ImportItem], source_label: str) -> bool:
    """Fragt für alle Kollisionen/Dubletten nach. False = abgebrochen."""
    open_items = [i for i in items if i.status in ("kollision", "dublette") and not i.decision]
    bulk: str | None = None
    for n, it in enumerate(open_items):
        if bulk:
            it.decision = bulk
            it.link_to = (it.local or {}).get("id")
            if bulk == "merge":  # Sammelentscheidung "Zusammenführen" = lokale Werte bevorzugen, leere ergänzen
                loc = it.local or {}
                it.merged = {f: loc.get(f) or it.incoming.get(f) or "" for f in COMPARE_FIELDS}
            continue
        dlg = CustomerCollisionDialog(parent, it, source_label, remaining=len(open_items) - n - 1)
        if not dlg.exec():
            return False
        if dlg.apply_all.isChecked():
            bulk = it.decision
    return True


# ======================================================================= Hottgenroth
STATUS_TEXT = {"neu": "Neu", "unveraendert": "Unverändert", "aktualisiert": "Geändert in Hottgenroth",
               "kollision": "Kollision", "dublette": "Mögliche Dublette"}


class HottgenrothImportDialog(QDialog):
    """Zeigt standardmäßig nur neue und in Hottgenroth geänderte Kontakte; unveränderte bereits importierte und
    früher nicht übernommene sind ausgeblendet."""

    def __init__(self, parent, ctx: Ctx, items: list[ImportItem]):
        super().__init__(parent)
        self.ctx, self.items = ctx, items
        self.setWindowTitle("Kunden aus Hottgenroth importieren")
        self.resize(980, 560)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        lay.addWidget(self.info)
        self.table = make_table(["Übernehmen", "Status", "Name", "Straße", "PLZ / Ort", "Abweichende Felder"], stretch_col=2)
        lay.addWidget(self.table)
        self.empty = QLabel("Keine neuen oder geänderten Kontakte in Hottgenroth. Über „Alle anzeigen“ lassen sich "
                            "bereits importierte und früher nicht übernommene Kontakte erneut auswählen.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:#5A6A6E")
        lay.addWidget(self.empty)
        for n, it in enumerate(items):
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = check_item(it.status in ("neu", "aktualisiert", "kollision", "dublette") and not it.ignored)
            chk.setData(Qt.UserRole, n)  # Index in self.items – die Tabelle ist sortierbar
            self.table.setItem(r, 0, chk)
            if it.ignored:
                self.table.setItem(r, 1, item(f"{STATUS_TEXT[it.status]} – zuvor nicht importiert", color="#5A6A6E"))
            else:
                color = {"kollision": "#A06A00", "dublette": "#A06A00", "neu": "#17604A"}.get(it.status)
                self.table.setItem(r, 1, item(STATUS_TEXT[it.status], color=color))
            c = it.incoming
            self.table.setItem(r, 2, item(repo.customer_display_name(c)))
            self.table.setItem(r, 3, item(c["street"]))
            self.table.setItem(r, 4, item(f"{c['zip']} {c['city']}"))
            self.table.setItem(r, 5, item(", ".join(FIELD_LABELS[f] for f in it.diff)))
        self.toggle = button("Alle anzeigen", self._toggle_all,
                             tooltip="Auch bereits importierte und früher nicht übernommene Kontakte anzeigen")
        lay.addLayout(hbox(button("Alle auswählen", lambda: self._check_all(True)),
                           button("Keine", lambda: self._check_all(False)), None, self.toggle))
        bb = QDialogButtonBox()
        bb.addButton(button("Importieren", self.run, primary=True), QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.stats = None
        self.show_all = False
        self._initial_decisions = [it.decision for it in items]
        self._apply_filter()

    def _visible(self, it: ImportItem) -> bool:
        return self.show_all or (it.is_new and not it.ignored) or it.status in ("aktualisiert", "kollision")

    def _rows(self):
        """(Tabellenzeile, Index, ImportItem) in der aktuellen Sortierung."""
        for r in range(self.table.rowCount()):
            n = self.table.item(r, 0).data(Qt.UserRole)
            yield r, n, self.items[n]

    def _apply_filter(self):
        for r, _, it in self._rows():
            self.table.setRowHidden(r, not self._visible(it))
        shown = sum(1 for it in self.items if self._visible(it))
        self.table.setVisible(shown > 0)
        self.empty.setVisible(shown == 0)
        self.toggle.setText("Nur neue und geänderte" if self.show_all else "Alle anzeigen")
        counts = {s: sum(1 for i in self.items if i.status == s and not i.ignored) for s in STATUS_TEXT}
        parts = [f"{STATUS_TEXT[s]}: {n}" for s, n in counts.items() if n]
        n_ignored = sum(1 for i in self.items if i.ignored)
        if n_ignored:
            parts.append(f"Zuvor nicht importiert: {n_ignored}")
        hint = ""
        if not self.show_all:
            hidden = len(self.items) - shown
            if hidden:
                hint = f"<br>{hidden} unveränderte oder zuvor nicht importierte Kontakte ausgeblendet."
        self.info.setText(" · ".join(parts) + hint +
                          "<br><span style='color:#5A6A6E'>Die Hottgenroth-Datenbank wird nur gelesen.</span>")

    def _toggle_all(self):
        self.show_all = not self.show_all
        self._apply_filter()

    def _check_all(self, on: bool):
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def run(self):
        selected = []
        for r, n, it in self._rows():
            if it.is_new:  # Entscheidungen eines abgebrochenen Durchlaufs verwerfen
                it.decision = self._initial_decisions[n]
            if self.table.isRowHidden(r):
                continue
            if self.table.item(r, 0).checkState() == Qt.Checked:
                if it.status == "unveraendert" and it.decision != "keep_local":
                    continue
                selected.append(it)
            elif it.is_new:
                # bewusst nicht übernommen: wird gemerkt und beim nächsten Import ausgeblendet
                it.decision = "skip"
                selected.append(it)
        if not resolve_collisions(self, selected, "Hottgenroth"):
            return
        try:
            self.stats = hottgenroth.apply_import(self.ctx.con, selected)
        except Exception as e:  # noqa: BLE001
            error(self, "Import fehlgeschlagen", e)
            return
        QMessageBox.information(self, "Import abgeschlossen",
                                f"Neu: {self.stats['neu']}\nAktualisiert: {self.stats['aktualisiert']}\n"
                                f"Verknüpft (lokal behalten): {self.stats['verknuepft']}\n"
                                f"Übersprungen: {self.stats['uebersprungen']}"
                                + ("\n\nÜbersprungene neue Kontakte werden künftig ausgeblendet."
                                   if any(i.is_new and i.decision == "skip" for i in selected) else ""))
        self.accept()


def run_hottgenroth_import(parent, ctx: Ctx) -> bool:
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        contacts = hottgenroth.fetch_contacts(ctx.con)
        items = hottgenroth.plan_import(ctx.con, contacts)
    except Exception as e:  # noqa: BLE001
        QApplication.restoreOverrideCursor()
        error(parent, "Hottgenroth", e)
        return False
    QApplication.restoreOverrideCursor()
    if not items:
        QMessageBox.information(parent, "Hottgenroth", "In Hottgenroth wurden keine Kontakte gefunden.")
        return False
    return bool(HottgenrothImportDialog(parent, ctx, items).exec())


# ======================================================================= CSV / Excel
class CsvMappingDialog(QDialog):
    def __init__(self, parent, headers: list[str], rows: list[list[str]]):
        super().__init__(parent)
        self.setWindowTitle("Spalten zuordnen")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Bitte jeder Spalte ein Feld zuordnen (Vorschlag wurde automatisch erkannt):"))
        self.combos: list[QComboBox] = []
        guess = csv_import.guess_mapping(headers)
        self.table = QTableWidget(min(len(rows), 8) + 1, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        for ci, h in enumerate(headers):
            cb = QComboBox()
            for key, label in csv_import.TARGET_FIELDS.items():
                cb.addItem(label, key)
            idx = cb.findData(guess[ci])
            cb.setCurrentIndex(max(idx, 0))
            self.table.setCellWidget(0, ci, cb)
            self.combos.append(cb)
            for ri, row in enumerate(rows[:8], start=1):
                self.table.setItem(ri, ci, QTableWidgetItem(row[ci] if ci < len(row) else ""))
        self.table.setVerticalHeaderLabels(["Feld"] + [str(i) for i in range(1, min(len(rows), 8) + 1)])
        lay.addWidget(self.table)
        lay.addWidget(QLabel(f"{len(rows)} Datensätze in der Datei."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def mapping(self) -> list[str]:
        return [cb.currentData() for cb in self.combos]


def run_csv_import(parent, ctx: Ctx) -> bool:
    path, _ = QFileDialog.getOpenFileName(parent, "Kundendatei wählen", "", "Tabellen (*.csv *.txt *.xlsx *.xlsm)")
    if not path:
        return False
    try:
        headers, rows = csv_import.read_table(Path(path))
    except Exception as e:  # noqa: BLE001
        error(parent, "Datei konnte nicht gelesen werden", e)
        return False
    if not headers:
        QMessageBox.information(parent, "Import", "Die Datei enthält keine Daten.")
        return False
    dlg = CsvMappingDialog(parent, headers, rows)
    if not dlg.exec():
        return False
    customers = csv_import.rows_to_customers(rows, dlg.mapping())
    items = []
    for c in customers:
        for f in COMPARE_FIELDS:
            c.setdefault(f, "")
        if not c.get("letter_salutation"):
            c["letter_salutation"] = repo.default_letter_salutation(c)
        similar = repo.find_similar_customers(ctx.con, c)
        if similar:
            items.append(ImportItem(c, "dublette", local=similar[0], candidates=similar,
                                    diff=hottgenroth.diff_fields(similar[0], c)))
        else:
            items.append(ImportItem(c, "neu", decision="new"))
    if not resolve_collisions(parent, items, "Datei"):
        return False
    n_new = n_upd = 0
    for it in items:
        c = dict(it.incoming)
        c.pop("hottgenroth_uuid", None)
        c.pop("hottgenroth_hash", None)
        if it.decision == "new":
            repo.save_customer(ctx.con, c)
            n_new += 1
        elif it.decision in ("take_remote", "merge"):
            data = dict(it.merged) if it.decision == "merge" else {f: c.get(f, "") for f in COMPARE_FIELDS}
            data["id"] = it.link_to
            repo.save_customer(ctx.con, data)
            n_upd += 1
    QMessageBox.information(parent, "Import abgeschlossen", f"Neu angelegt: {n_new}\nAktualisiert: {n_upd}")
    return True


# ======================================================================= Bestehende Rechnungen
class LegacyReviewDialog(QDialog):
    """Prüfdialog für eine erkannte Altrechnung inkl. Kollisionsentscheidungen."""

    SKIP, IMPORTED, CANCEL_ALL = 0, 1, 2

    def __init__(self, parent, ctx: Ctx, inv: legacy_invoices.ParsedInvoice, index: int, total: int):
        super().__init__(parent)
        self.ctx, self.inv = ctx, inv
        self.result_code = self.SKIP
        self.setWindowTitle(f"Beleg prüfen ({index}/{total}): {inv.path.name}")
        self.resize(1000, 760)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        lay.addLayout(hbox(QLabel(f"<b>Datei:</b> {inv.path}"), None, button("Original öffnen", lambda: open_file(inv.path))))
        if inv.warnings:
            w = QLabel("⚠ " + "<br>⚠ ".join(inv.warnings))
            w.setObjectName("warn")
            w.setWordWrap(True)
            lay.addWidget(w)

        # Belegdaten
        gb = QGroupBox("Belegdaten")
        f = QFormLayout(gb)
        self.doc_type = QComboBox()
        self.doc_type.addItem("Rechnung", "rechnung")
        self.doc_type.addItem("Angebot", "angebot")
        self.doc_type.setCurrentIndex(0 if inv.doc_type == "rechnung" else 1)
        self.number = QLineEdit(inv.number)
        self.doc_date = date_edit(inv.doc_date)
        self.service_date = date_edit(inv.service_date or inv.doc_date, allow_empty=True)
        days = get_int_setting(ctx.con, "invoice.payment_days", "14")
        from datetime import date as _d, timedelta
        due = (_d.fromisoformat(inv.doc_date) + timedelta(days=days)).isoformat() if inv.doc_date else None
        self.due_date = date_edit(due, allow_empty=True)
        self.tax_mode = QComboBox()
        self.tax_mode.addItem("Kleinunternehmer (§ 19 UStG)", "kleinunternehmer")
        self.tax_mode.addItem("Regelbesteuerung", "regel")
        f.addRow("Belegart", self.doc_type)
        f.addRow("Belegnummer", self.number)
        f.addRow("Belegdatum", self.doc_date)
        f.addRow("Leistungsdatum", self.service_date)
        f.addRow("Fällig am", self.due_date)
        f.addRow("Besteuerung", self.tax_mode)
        lay.addWidget(gb)

        # Kollisionen Nummer
        self.coll_box = QGroupBox("Prüfung")
        self.coll_lay = QVBoxLayout(self.coll_box)
        lay.addWidget(self.coll_box)
        self.number_choice = QButtonGroup(self)
        self._last_checked_number = None
        self.number.editingFinished.connect(self._number_edited)
        self.doc_type.currentIndexChanged.connect(self.refresh_collisions)

        # Kunde
        gbc = QGroupBox("Kunde")
        cl = QVBoxLayout(gbc)
        cl.addWidget(QLabel("Erkannter Empfänger: " + (" · ".join(inv.recipient_lines) or "–")))
        self.cust_mode = QComboBox()
        self.cust_new_idx = 0
        similar = repo.find_similar_customers(ctx.con, inv.customer) if inv.customer else []
        self.cust_mode.addItem("Neuen Kunden aus erkannten Daten anlegen", None)
        for c in similar:
            self.cust_mode.addItem(f"Vorhanden: {repo.customer_display_name(c)} – {c['street']}, {c['zip']} {c['city']} ({c['customer_no']})", c["id"])
        self.cust_mode.addItem("Anderen vorhandenen Kunden wählen …", "pick")
        if len(similar) == 1:
            self.cust_mode.setCurrentIndex(1)
        if len(similar) > 1:
            hint = QLabel(f"⚠ Kunde nicht eindeutig – {len(similar)} passende Kunden gefunden. Bitte auswählen.")
            hint.setObjectName("warn")
            cl.addWidget(hint)
        cl.addWidget(self.cust_mode)
        from app.ui.customers import CustomerPicker
        self.picker = CustomerPicker(ctx)
        self.picker.setVisible(False)
        cl.addWidget(self.picker)
        # editierbare Neukundendaten
        self.new_fields: dict[str, QLineEdit] = {}
        self.new_box = QWidget()
        g = QGridLayout(self.new_box)
        g.setContentsMargins(0, 0, 0, 0)
        for i, (k, lab) in enumerate([("salutation", "Anrede"), ("first_name", "Vorname"), ("last_name", "Nachname"),
                                      ("company", "Firma"), ("street", "Straße"), ("zip", "PLZ"), ("city", "Ort"),
                                      ("letter_salutation", "Briefanrede")]):
            e = QLineEdit(inv.customer.get(k, ""))
            self.new_fields[k] = e
            g.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            g.addWidget(e, i // 2, (i % 2) * 2 + 1)
        cl.addWidget(self.new_box)
        self.cust_mode.currentIndexChanged.connect(self._cust_mode_changed)
        self._cust_mode_changed()
        lay.addWidget(gbc)

        # Positionen
        gbl = QGroupBox("Positionen")
        ll = QVBoxLayout(gbl)
        self.lines = QTableWidget(0, 6)
        self.lines.setHorizontalHeaderLabels(["Bezeichnung", "Beschreibung", "Menge", "Einheit", "Einzelpreis", "Gesamt"])
        self.lines.horizontalHeader().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.lines.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.lines.setColumnWidth(0, 220)
        self.lines.setMinimumHeight(160)
        self.lines.setShowGrid(False)
        self.sum_lbl = QLabel()
        for ln in inv.lines:
            self._add_line(ln.title, ln.description, fmt_qty(ln.quantity), ln.unit, fmt_eur(ln.unit_price_cents, False))
        self.lines.itemChanged.connect(self._recalc)
        ll.addWidget(self.lines)
        ll.addLayout(hbox(button("+ Position", lambda: self._add_line("", "", "1", "", "0,00")),
                          button("Position entfernen", self._del_line), None, self.sum_lbl))
        lay.addWidget(gbl)
        self._recalc()

        # Zahlungsstatus
        gbp = QGroupBox("Zahlungsstatus")
        pl = QHBoxLayout(gbp)
        self.paid = QCheckBox("bereits bezahlt am")
        self.pay_date = date_edit(inv.doc_date)
        self.pay_date.setEnabled(False)
        self.paid.toggled.connect(self.pay_date.setEnabled)
        pl.addWidget(self.paid)
        pl.addWidget(self.pay_date)
        pl.addStretch(1)
        lay.addWidget(gbp)
        self.pay_box = gbp
        self.doc_type.currentIndexChanged.connect(self._doc_type_changed)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Vermerk (optional)")
        lay.addWidget(self.note)

        outer.addLayout(hbox(button("Alle weiteren abbrechen", self._cancel_all), None,
                             button("Überspringen", self.reject),
                             button("Importieren", self._import, primary=True)))
        self.refresh_collisions()
        self._doc_type_changed()

    def _doc_type_changed(self, *_):
        is_invoice = self.doc_type.currentData() == "rechnung"
        self.pay_box.setVisible(is_invoice)
        self.due_date.setEnabled(is_invoice)
        if not is_invoice:
            self.paid.setChecked(False)

    # --- Kunde
    def _cust_mode_changed(self):
        data = self.cust_mode.currentData()
        self.picker.setVisible(data == "pick")
        self.new_box.setVisible(data is None)

    def _customer_id(self) -> int | None:
        data = self.cust_mode.currentData()
        if data == "pick":
            return self.picker.current_id()
        if data is None:
            c = {k: e.text().strip() for k, e in self.new_fields.items()}
            if not (c.get("last_name") or c.get("company")):
                raise ValueError("Für den neuen Kunden fehlt Nachname oder Firma.")
            c["source"] = "import"
            c["country"] = "DE"
            return repo.save_customer(self.ctx.con, c)
        return data

    # --- Positionen
    def _add_line(self, title, desc, qty, unit, price):
        self.lines.blockSignals(True)
        r = self.lines.rowCount()
        self.lines.insertRow(r)
        for i, v in enumerate([title, desc, qty, unit, price, ""]):
            it = QTableWidgetItem(v)
            if i == 5:
                it.setFlags(Qt.ItemIsEnabled)
            if i >= 2 and i != 3:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.lines.setItem(r, i, it)
        self.lines.blockSignals(False)
        self._recalc()

    def _del_line(self):
        r = self.lines.currentRow()
        if r >= 0:
            self.lines.removeRow(r)
            self._recalc()

    def _line_dicts(self) -> list[dict]:
        out = []
        for r in range(self.lines.rowCount()):
            t = lambda c: (self.lines.item(r, c).text() if self.lines.item(r, c) else "").strip()
            out.append({"title": t(0), "description": t(1).replace("\\n", "\n"), "quantity": str(input_decimal(t(2) or "0")),
                        "unit": t(3), "unit_price_cents": input_cents(t(4) or "0"), "tax_rate": "0"})
        return out

    def _recalc(self, *_):
        self.lines.blockSignals(True)
        total = 0
        ok = True
        for r in range(self.lines.rowCount()):
            try:
                q = input_decimal(self.lines.item(r, 2).text() or "0")
                p = input_cents(self.lines.item(r, 4).text() or "0")
                t = line_total_cents(q, p)
                total += t
                self.lines.item(r, 5).setText(fmt_eur(t))
            except (ValueError, AttributeError):
                ok = False
                if self.lines.item(r, 5):
                    self.lines.item(r, 5).setText("?")
        self.lines.blockSignals(False)
        ref = self.inv.total_cents
        txt = f"Summe Positionen: <b>{fmt_eur(total)}</b>"
        if ref is not None:
            txt += f" · im Original: <b>{fmt_eur(ref)}</b>"
            if ref != total:
                txt += " <span style='color:#A06A00'>⚠ abweichend</span>"
        if not ok:
            txt += " <span style='color:#B3261E'>ungültige Zahl</span>"
        self.sum_lbl.setText(txt)

    # --- Kollisionen
    def refresh_collisions(self):
        while self.coll_lay.count():
            w = self.coll_lay.takeAt(0)
            if w.widget():
                w.widget().deleteLater()
            elif w.layout():
                while w.layout().count():
                    x = w.layout().takeAt(0).widget()
                    if x:
                        x.deleteLater()
        for b in self.number_choice.buttons():
            self.number_choice.removeButton(b)
        self._last_checked_number = self.number.text().strip()
        self.colls = legacy_invoices.check_collisions(self.ctx.con, self.inv, self.number.text(), self.doc_type.currentData())
        self.hash_dup = any(c.kind == "hash" for c in self.colls)
        if not self.colls:
            ok = QLabel("✓ Keine Kollisionen – Nummer ist frei und passt ins Schema.")
            ok.setObjectName("ok")
            self.coll_lay.addWidget(ok)
            return
        for c in self.colls:
            lbl = QLabel(("⛔ " if c.kind == "hash" else "⚠ ") + c.message)
            lbl.setObjectName("error" if c.kind == "hash" else "warn")
            lbl.setWordWrap(True)
            self.coll_lay.addWidget(lbl)
            if c.kind == "hash":
                self.coll_lay.addWidget(QLabel("Diese Datei wird übersprungen."))
            elif c.kind == "number":
                ex = c.existing or {}
                cmp = QLabel(f"Vergleich – vorhanden: {ex.get('number')} vom {ex.get('doc_date')}, "
                             f"{fmt_eur(ex.get('total_gross_cents'))} · Import: {self.number.text()} vom "
                             f"{date_value(self.doc_date)}, {fmt_eur(sum(line_total_cents(l['quantity'], l['unit_price_cents']) for l in self._line_dicts()))}")
                cmp.setWordWrap(True)
                self.coll_lay.addWidget(cmp)
                rb_skip = QRadioButton("Überspringen (vorhandener Beleg bleibt unverändert)")
                dup = legacy_invoices.suggest_duplicate_number(self.ctx.con, self.number.text().strip())
                rb_dup = QRadioButton(f"Mit Vermerk importieren als {dup} (als Dublette markiert)")
                rb_dup.setProperty("dup_number", dup)
                self.number_choice.addButton(rb_skip, 0)
                self.number_choice.addButton(rb_dup, 1)
                rb_skip.setChecked(True)
                self.coll_lay.addWidget(rb_skip)
                self.coll_lay.addWidget(rb_dup)
            elif c.kind == "number_format":
                self.keep_number = QCheckBox("Nummer trotzdem so übernehmen (wird nicht im Nummernkreis reserviert)")
                self.coll_lay.addWidget(QLabel("Nummer oben korrigieren oder:"))
                self.coll_lay.addWidget(self.keep_number)

    def _number_edited(self):
        if self.number.text().strip() != self._last_checked_number:
            self.refresh_collisions()

    def _cancel_all(self):
        self.result_code = self.CANCEL_ALL
        self.reject()

    def _import(self):
        # Kollisionen neu prüfen, ohne die Auswahl-Widgets neu aufzubauen (sonst ginge die Auswahl verloren)
        fresh = legacy_invoices.check_collisions(self.ctx.con, self.inv, self.number.text(), self.doc_type.currentData())
        if {c.kind for c in fresh} != {c.kind for c in self.colls}:
            self.refresh_collisions()
            QMessageBox.information(self, "Prüfung", "Die Prüfung hat sich durch Ihre Änderungen geändert – bitte erneut entscheiden.")
            return
        self.colls = fresh
        self.hash_dup = any(c.kind == "hash" for c in fresh)
        if self.hash_dup:
            QMessageBox.information(self, "Bereits importiert", "Diese Datei wurde bereits importiert und wird übersprungen.")
            self.reject()
            return
        number = self.number.text().strip()
        note = self.note.text().strip()
        kinds = {c.kind for c in self.colls}
        if "number" in kinds:
            if self.number_choice.checkedId() != 1:
                self.reject()
                return
            number = self.number_choice.checkedButton().property("dup_number")
            note = (note + " " if note else "") + f"Dublette: Nummer {self.number.text().strip()} war bereits vergeben."
        if "number_format" in kinds and not (getattr(self, "keep_number", None) and self.keep_number.isChecked()):
            if not number:
                QMessageBox.warning(self, "Belegnummer", "Bitte eine Belegnummer eingeben.")
                return
            QMessageBox.warning(self, "Belegnummer", "Die Nummer passt nicht ins Schema. Bitte korrigieren oder "
                                                     "„Nummer trotzdem so übernehmen“ anhaken.")
            return
        lines = self._line_dicts()
        if not lines:
            QMessageBox.warning(self, "Positionen", "Mindestens eine Position ist erforderlich.")
            return
        try:
            cid = self._customer_id()
            if not cid:
                QMessageBox.warning(self, "Kunde", "Bitte einen Kunden wählen.")
                return
            doc_type = self.doc_type.currentData()
            legacy_invoices.import_parsed(
                self.ctx.con, self.inv, number=number, doc_type=doc_type, doc_date=date_value(self.doc_date),
                service_date=date_value(self.service_date), due_date=date_value(self.due_date) if doc_type == "rechnung" else None,
                customer_id=cid, lines=lines, paid=self.paid.isChecked() and doc_type == "rechnung", pay_date=date_value(self.pay_date),
                tax_mode=self.tax_mode.currentData(), note=note)
        except Exception as e:  # noqa: BLE001
            error(self, "Import fehlgeschlagen", e)
            return
        self.result_code = self.IMPORTED
        self.accept()


def run_legacy_import(parent, ctx: Ctx, folder: bool = False) -> int:
    if folder:
        d = QFileDialog.getExistingDirectory(parent, "Ordner mit bestehenden Rechnungen wählen")
        if not d:
            return 0
        files = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in legacy_invoices.SUPPORTED and p.is_file())
        # Gleichnamige .ods/.xlsx und .pdf: die Tabellendatei bevorzugen (genauere Daten)
        stems = {}
        for p in files:
            if p.stem not in stems or p.suffix.lower() != ".pdf":
                stems[p.stem] = p
        files = sorted(stems.values())
    else:
        paths, _ = QFileDialog.getOpenFileNames(parent, "Bestehende Rechnungen wählen", "",
                                                "Belege (*.ods *.xlsx *.xlsm *.pdf)")
        files = [Path(p) for p in paths]
    if not files:
        return 0
    comp = company(ctx.con)
    imported = skipped = 0
    for i, f in enumerate(files, 1):
        try:
            inv = legacy_invoices.parse_file(f, comp)
        except Exception as e:  # noqa: BLE001
            if QMessageBox.warning(parent, "Datei nicht lesbar", f"{f.name}:\n{e}\n\nWeiter mit der nächsten Datei?",
                                   QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
                break
            skipped += 1
            continue
        dlg = LegacyReviewDialog(parent, ctx, inv, i, len(files))
        dlg.exec()
        if dlg.result_code == LegacyReviewDialog.IMPORTED:
            imported += 1
        elif dlg.result_code == LegacyReviewDialog.CANCEL_ALL:
            break
        else:
            skipped += 1
    QMessageBox.information(parent, "Import bestehender Belege", f"Importiert: {imported}\nÜbersprungen: {skipped}")
    return imported
