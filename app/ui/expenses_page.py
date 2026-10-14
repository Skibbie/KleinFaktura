"""Ausgabenbuch und EÜR-Jahreswerte."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.db.database import get_setting, set_setting
from app.importers import bank_csv
from app.importers.csv_import import read_table
from app.money import fmt_date, fmt_eur, input_cents, input_decimal, to_decimal
from app.services import expenses
from app.services.expenses import CAT, CATEGORIES
from app.ui import theme
from app.ui.common import (Ctx, GuardedDialog, button, check_item, confirm, count, date_edit, date_value, error, hbox, item,
                           make_table, open_file, page_title, selected_data)


def _year_combo(with_all: bool = True) -> QComboBox:
    cb = QComboBox()
    if with_all:
        cb.addItem("Alle Jahre", None)
    for y in range(date.today().year, date.today().year - 11, -1):
        cb.addItem(str(y), y)
    cb.setCurrentIndex(1 if with_all else 0)
    return cb


# ======================================================================= Erfassung
class ExpenseDialog(GuardedDialog, QDialog):
    dirty_text = "Die Ausgabe wurde geändert."

    def __init__(self, parent, ctx: Ctx, expense: dict | None = None, embedded: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self.exp = dict(expense or {})
        self.new_receipt: str | None = None
        self.setWindowTitle("Ausgabe")
        self.setMinimumWidth(640)
        locked = bool(self.exp.get("exp_date")) and expenses.is_locked(ctx.con, self.exp["exp_date"][:4])
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Ausgabe bearbeiten" if self.exp.get("id") else "Neue Ausgabe")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(button("Zurück zur Liste" if embedded else "Schließen", self.close_dialog))
        lay.addLayout(head)
        if locked:
            w = QLabel(f"Das Jahr {self.exp['exp_date'][:4]} ist abgeschlossen. Die Ausgabe kann nur angesehen werden.")
            w.setObjectName("warn")
            lay.addWidget(w)

        box = QFrame()
        box.setObjectName("card")
        f = QFormLayout(box)
        f.setContentsMargins(18, 16, 18, 16)
        f.setVerticalSpacing(10)
        self.date = date_edit(self.exp.get("exp_date"))
        self.supplier = QComboBox()
        self.supplier.setEditable(True)
        self.supplier.addItems([""] + expenses.suppliers(ctx.con))
        self.supplier.setCurrentText(self.exp.get("supplier", ""))
        self.supplier.completer().setFilterMode(Qt.MatchContains)
        self.supplier.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.supplier.lineEdit().editingFinished.connect(self._supplier_done)
        self.description = QLineEdit(self.exp.get("description", ""))
        self.description.setPlaceholderText("z.B. Jahreswartung Fachsoftware")
        self.category = QComboBox()
        for c in expenses.selectable_categories(self.exp.get("category")):
            self.category.addItem(c.label, c.key)
        self.category.setCurrentIndex(max(self.category.findData(self.exp.get("category", "uebrige")), 0))
        self.category.currentIndexChanged.connect(self._category_changed)
        self.category.activated.connect(lambda *_: setattr(self, "_category_chosen", True))  # nur echte Auswahl
        self.cat_hint = QLabel()
        self.cat_hint.setObjectName("muted")
        self.cat_hint.setWordWrap(True)
        self.amount = QLineEdit(fmt_eur(self.exp["amount_cents"], False) if self.exp.get("amount_cents") else "")
        self.amount.setPlaceholderText("Bruttobetrag laut Beleg")
        self.amount.textChanged.connect(self._update_hints)
        self.vat = QComboBox()
        for v in ("19", "7", "0"):
            self.vat.addItem(f"{v} %", v)
        self.vat.setCurrentIndex(max(self.vat.findData(str(self.exp.get("vat_rate") or "19")), 0))
        self.vat.currentIndexChanged.connect(self._update_hints)
        self.km = QLineEdit(str(self.exp.get("km") or "").replace(".", ","))
        self.km.setPlaceholderText("gefahrene Kilometer (Hin- und Rückweg)")
        self.km.textChanged.connect(self._update_hints)
        self.life = QSpinBox()
        self.life.setRange(1, 50)
        self.life.setValue(int(self.exp.get("useful_life") or 3))
        self.life.setSuffix(" Jahre")
        self.method = QComboBox()
        self.method.setEditable(True)
        self.method.addItems(["Überweisung", "Lastschrift", "EC-Karte", "Kreditkarte", "Bar", "PayPal"])
        self.method.setCurrentText(self.exp.get("payment_method") or "Überweisung")
        self.receipt_lbl = QLabel()
        self.receipt_lbl.setWordWrap(True)
        self.note = QPlainTextEdit(self.exp.get("note", ""))
        self.note.setFixedHeight(60)
        self.calc = QLabel()
        self.calc.setObjectName("muted")
        self.calc.setWordWrap(True)

        f.addRow("Datum", self.date)
        f.addRow("Lieferant", self.supplier)
        f.addRow("Beschreibung", self.description)
        f.addRow("Kategorie", self.category)
        f.addRow("", self.cat_hint)
        self.row_amount = (QLabel("Betrag brutto €"), self.amount)
        f.addRow(*self.row_amount)
        self.row_vat = (QLabel("USt auf dem Beleg"), self.vat)
        f.addRow(*self.row_vat)
        self.row_km = (QLabel("Kilometer"), self.km)
        f.addRow(*self.row_km)
        self.row_life = (QLabel("Nutzungsdauer"), self.life)
        f.addRow(*self.row_life)
        f.addRow("", self.calc)
        f.addRow("Bezahlt per", self.method)
        rec_btns = hbox(button("Beleg wählen …", self.pick_receipt), button("Beleg öffnen", self.open_receipt), None)
        f.addRow("Beleg", self.receipt_lbl)
        f.addRow("", rec_btns)
        f.addRow("Notiz", self.note)
        lay.addWidget(box)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setProperty("primary", True)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if locked:
            for w in (self.date, self.supplier, self.description, self.category, self.amount, self.vat, self.km,
                      self.life, self.method, self.note):
                w.setEnabled(False)
            bb.button(QDialogButtonBox.Save).setEnabled(False)
        self._category_changed()
        self._show_receipt()
        self.mark_clean()

    def _snapshot(self):
        return (date_value(self.date), self.supplier.currentText(), self.description.text(), self.category.currentData(),
                self.amount.text(), self.vat.currentData(), self.km.text(), self.life.value(), self.method.currentText(),
                self.note.toPlainText(), self.new_receipt)

    def _supplier_done(self):
        """Kategorie des letzten Belegs dieses Lieferanten vorschlagen – nur bei neuen Ausgaben, nur wenn der
        Anwender die Kategorie nicht schon selbst gewählt hat und nur mit einer hier wählbaren Kategorie."""
        name = self.supplier.currentText().strip()
        if self.exp.get("id") or not name or getattr(self, "_category_chosen", False):
            return
        idx = self.category.findData(expenses.last_category_for(self.ctx.con, name))
        if idx >= 0:
            self.category.setCurrentIndex(idx)

    def _category_changed(self, *_):
        c = CAT[self.category.currentData()]
        self.cat_hint.setText(f"EÜR: {c.area}" + (f". {c.hint}" if c.hint else ""))
        is_km = c.kind == "km"
        for w in self.row_amount + self.row_vat:
            w.setVisible(not is_km)
        for w in self.row_km:
            w.setVisible(is_km)
        for w in self.row_life:
            w.setVisible(c.kind == "afa")
        self._update_hints()

    def _update_hints(self, *_):
        c = CAT[self.category.currentData()]
        txt = ""
        try:
            if c.kind == "km":
                km = input_decimal(self.km.text() or "0")
                rate = get_setting(self.ctx.con, "expense.km_rate", "0,30")
                txt = f"{str(km).replace('.', ',')} km × {rate} € = {fmt_eur(expenses.km_amount_cents(self.ctx.con, km))}"
            else:
                cents = input_cents(self.amount.text() or "0")
                net = expenses.net_cents(cents, self.vat.currentData())
                if c.kind == "gwg" and net > expenses.GWG_LIMIT_NET_CENTS:
                    txt = (f"Netto {fmt_eur(net)} liegt über 800 €. Bitte „Anschaffung über 800 € netto“ wählen, "
                           "dann wird über die Nutzungsdauer abgeschrieben.")
                elif c.kind == "afa":
                    if net <= expenses.GWG_LIMIT_NET_CENTS and cents:
                        txt = f"Netto {fmt_eur(net)} – bis 800 € ist der Sofortabzug als geringwertiges Wirtschaftsgut möglich. "
                    if cents:
                        sched = expenses.afa_schedule(cents, date_value(self.date), self.life.value())
                        txt += "Abschreibung: " + ", ".join(f"{y}: {fmt_eur(v)}" for y, v in list(sched.items())[:6])
                        if len(sched) > 6:
                            txt += " …"
                elif c.kind == "bewirtung" and cents:
                    txt = f"Abziehbar 70 %: {fmt_eur(round(cents * 0.7))}. Bewirtungsbeleg mit Anlass und Teilnehmern aufbewahren."
        except ValueError:
            txt = "Ungültige Zahl."
        self.calc.setText(txt)
        self.calc.setVisible(bool(txt))

    def _show_receipt(self):
        p = self.new_receipt or self.exp.get("receipt_path")
        if p:
            self.receipt_lbl.setText(Path(p).name + (" (neu, wird beim Speichern archiviert)" if self.new_receipt else ""))
        else:
            self.receipt_lbl.setText("kein Beleg hinterlegt")

    def pick_receipt(self):
        p, _ = QFileDialog.getOpenFileName(self, "Beleg wählen", str(Path.home() / "Downloads"),
                                           "Belege (*.pdf *.jpg *.jpeg *.png *.heic *.xml);;Alle Dateien (*.*)")
        if p:
            self.new_receipt = p
            self._show_receipt()

    def open_receipt(self):
        p = self.new_receipt or self.exp.get("receipt_path")
        if p:
            open_file(p)
        else:
            QMessageBox.information(self, "Beleg", "Zu dieser Ausgabe ist kein Beleg hinterlegt.")

    def save(self):
        d = {"id": self.exp.get("id"), "exp_date": date_value(self.date), "supplier": self.supplier.currentText().strip(),
             "description": self.description.text().strip(), "category": self.category.currentData(),
             "vat_rate": self.vat.currentData(), "payment_method": self.method.currentText(),
             "note": self.note.toPlainText(), "useful_life": self.life.value() if CAT[self.category.currentData()].kind == "afa" else None,
             "receipt_path": self.exp.get("receipt_path"), "receipt_sha256": self.exp.get("receipt_sha256"),
             "source": self.exp.get("source") or "manuell", "bank_ref": self.exp.get("bank_ref")}
        try:
            if CAT[d["category"]].kind == "km":
                d["km"] = str(input_decimal(self.km.text() or "0"))
                d["amount_cents"] = 0
            else:
                d["km"] = None
                d["amount_cents"] = input_cents(self.amount.text() or "0")
            if self.new_receipt:
                d["receipt_path"], d["receipt_sha256"] = expenses.archive_receipt(self.new_receipt, d["exp_date"])
            self.exp_id = expenses.save_expense(self.ctx.con, d)
        except Exception as e:  # noqa: BLE001
            error(self, "Ausgabe nicht gespeichert", e)
            return
        self.mark_clean()
        self.accept()


# ======================================================================= Kontoauszug
class BankImportDialog(QDialog):
    def __init__(self, parent, ctx: Ctx, path: str):
        super().__init__(parent)
        self.ctx, self.path = ctx, path
        self.setWindowTitle("Ausgaben aus Kontoauszug übernehmen")
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Datei: {Path(path).name}. Vorgeschlagen werden nur Abbuchungen. Bitte private Buchungen "
                             "abwählen und die Kategorie prüfen. Belege können Sie danach an jede Ausgabe anhängen."))
        self.map_row = QHBoxLayout()
        lay.addLayout(self.map_row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Übernehmen", "Datum", "Empfänger", "Verwendungszweck", "Betrag", "Kategorie"])
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        from PySide6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.info = QLabel()
        self.info.setObjectName("muted")
        lay.addWidget(self.info)
        lay.addLayout(hbox(button("Alle", lambda: self._check(True)), button("Keine", lambda: self._check(False)), None,
                           button("Abbrechen", self.reject), button("Übernehmen", self.run, primary=True)))
        self.combos: list[QComboBox] = []
        self.mapping = None
        self.load()

    def load(self):
        try:
            headers, mapping, rows = bank_csv.parse(self.ctx.con, self.path, self.mapping)
        except Exception as e:  # noqa: BLE001
            error(self, "Kontoauszug", e)
            self.rows = []
            return
        self.headers, self.mapping, self.rows = headers, mapping, rows
        # Spaltenzuordnung (änderbar)
        while self.map_row.count():
            w = self.map_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.map_row.addWidget(QLabel("Spalten:"))
        self.map_boxes = []
        for target in ("date", "amount", "name", "purpose"):
            cb = QComboBox()
            cb.addItem(f"{bank_csv.TARGETS[target]}: –", -1)
            for i, h in enumerate(headers):
                cb.addItem(f"{bank_csv.TARGETS[target]}: {h}", i)
            if target in mapping:
                cb.setCurrentIndex(mapping.index(target) + 1)
            cb.setProperty("target", target)
            cb.currentIndexChanged.connect(self._remap)
            self.map_row.addWidget(cb)
            self.map_boxes.append(cb)
        self.map_row.addStretch(1)
        self.table.setRowCount(0)
        self.combos = []
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked if (r.duplicate or r.likely_private) else Qt.Checked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, item(fmt_date(r.date)))
            self.table.setItem(i, 2, item(r.name))
            note = " (bereits erfasst)" if r.duplicate else " (vermutlich privat)" if r.likely_private else ""
            self.table.setItem(i, 3, item(r.purpose + note, color=theme.INK_2 if note else None))
            self.table.setItem(i, 4, item(fmt_eur(r.amount_cents), align_right=True))
            cb = QComboBox()
            for c in expenses.selectable_categories():
                cb.addItem(c.label, c.key)
            cb.setCurrentIndex(max(cb.findData(r.category), 0))
            self.table.setCellWidget(i, 5, cb)
            self.combos.append(cb)
        self.table.resizeColumnToContents(2)
        self.table.setColumnWidth(5, 300)
        self._update_info()
        self.table.itemChanged.connect(self._update_info)

    def _remap(self):
        m = [""] * len(self.headers)
        for cb in self.map_boxes:
            idx = cb.currentData()
            if idx is not None and idx >= 0:
                m[idx] = cb.property("target")
        self.mapping = m
        self.table.itemChanged.disconnect(self._update_info)
        self.load()

    def _check(self, on: bool):
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _update_info(self, *_):
        sel = [r for i, r in enumerate(self.rows) if self.table.item(i, 0).checkState() == Qt.Checked]
        self.info.setText(f"{len(self.rows)} Abbuchungen gefunden, {len(sel)} ausgewählt, Summe {fmt_eur(sum(r.amount_cents for r in sel))}")

    def run(self):
        sel = []
        for i, r in enumerate(self.rows):
            if self.table.item(i, 0).checkState() == Qt.Checked:
                r.category = self.combos[i].currentData()
                sel.append(r)
        if not sel:
            self.reject()
            return
        try:
            n = bank_csv.import_rows(self.ctx.con, sel)
        except Exception as e:  # noqa: BLE001
            error(self, "Übernahme fehlgeschlagen", e)
            return
        QMessageBox.information(self, "Kontoauszug", f"{count(n, 'Ausgabe', 'Ausgaben')} übernommen.")
        self.accept()


# ======================================================================= Liste
class ExpensesPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Ausgaben"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Lieferant, Beschreibung, Notiz) …")
        self.search.textChanged.connect(self.reload)
        self.cat = QComboBox()
        self.cat.addItem("Alle Kategorien", None)
        for c in CATEGORIES:
            self.cat.addItem(c.label, c.key)
        self.cat.currentIndexChanged.connect(self.reload)
        self.year = _year_combo()
        self.year.currentIndexChanged.connect(self.reload)
        lay.addLayout(hbox(self.search, self.cat, self.year))
        lay.addLayout(hbox(button("Neue Ausgabe", self.new, primary=True), button("Bearbeiten", self.edit),
                           button("Beleg öffnen", self.open_receipt), button("Löschen", self.delete, danger=True), None,
                           button("Aus Kontoauszug übernehmen …", self.bank_import)))
        self.table = make_table(["Datum", "Lieferant", "Beschreibung", "Kategorie", "Betrag", "Beleg"], stretch_col=2)
        self.table.doubleClicked.connect(self.edit)
        lay.addWidget(self.table)
        self.sum_label = QLabel()
        lay.addWidget(self.sum_label)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        rows = expenses.list_expenses(self.ctx.con, self.year.currentData(), self.search.text().strip(), self.cat.currentData())
        self.table.setRowCount(0)
        total = missing = 0
        for e in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            c = CAT.get(e["category"])
            desc = e["description"]
            if c and c.kind == "km" and e.get("km"):
                desc = (desc + " " if desc else "") + f"({str(e['km']).replace('.', ',')} km)"
            has_receipt = bool(e.get("receipt_path"))
            needs = c is not None and c.key not in ("fahrt", "homeoffice")
            if needs and not has_receipt:
                missing += 1
            vals = [fmt_date(e["exp_date"]), e["supplier"], desc, c.label if c else e["category"], fmt_eur(e["amount_cents"]),
                    "vorhanden" if has_receipt else ("fehlt" if needs else "–")]
            for i, v in enumerate(vals):
                self.table.setItem(r, i, item(v, e["id"] if i == 0 else None, align_right=i == 4,
                                              color=theme.SCALE[6] if (i == 5 and v == "fehlt") else None))
            total += e["amount_cents"]
        period = self.year.currentText() if self.year.currentData() else "alle Jahre"
        txt = f"{count(len(rows), 'Ausgabe', 'Ausgaben')} {period}, gezahlt {fmt_eur(total)}"
        if missing:
            txt += f", bei {missing} fehlt noch der Beleg"
        self.sum_label.setText(txt)

    def _open(self, expense=None):
        host = getattr(self.ctx, "editor_host", None)
        dlg = ExpenseDialog(self, self.ctx, expense, embedded=host is not None)
        if host is not None:
            host.open_embedded(dlg, return_row=host.page_index("ausgaben"))
        elif dlg.exec():
            self.ctx.changed()

    def new(self):
        self._open()

    def edit(self, *_):
        eid = selected_data(self.table)
        if eid:
            self._open(expenses.get_expense(self.ctx.con, eid))

    def open_receipt(self):
        eid = selected_data(self.table)
        if not eid:
            return
        e = expenses.get_expense(self.ctx.con, eid)
        if e.get("receipt_path"):
            open_file(e["receipt_path"])
        else:
            QMessageBox.information(self, "Beleg", "Zu dieser Ausgabe ist kein Beleg hinterlegt.")

    def delete(self):
        eid = selected_data(self.table)
        if not eid:
            return
        e = expenses.get_expense(self.ctx.con, eid)
        if confirm(self, "Ausgabe löschen", f"Ausgabe „{e['supplier'] or e['description']}“ über {fmt_eur(e['amount_cents'])} löschen?\n\n"
                   "Die Löschung wird protokolliert, ein archivierter Beleg bleibt erhalten."):
            try:
                expenses.delete_expense(self.ctx.con, eid)
            except Exception as ex:  # noqa: BLE001
                error(self, "Löschen nicht möglich", ex)
            self.ctx.changed()

    def bank_import(self):
        p, _ = QFileDialog.getOpenFileName(self, "Kontoauszug (CSV) wählen", str(Path.home() / "Downloads"),
                                           "Kontoauszug (*.csv *.txt *.xlsx)")
        if p and BankImportDialog(self, self.ctx, p).exec():
            self.ctx.changed()


# ======================================================================= EÜR-Werte
class EuerPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(page_title("EÜR-Werte"))
        head.addStretch(1)
        self.year = _year_combo(with_all=False)
        if date.today().month <= 6 and self.year.count() > 1:
            self.year.setCurrentIndex(1)  # im ersten Halbjahr meist das Vorjahr gefragt
        self.year.currentIndexChanged.connect(self.reload)
        head.addWidget(QLabel("Jahr"))
        head.addWidget(self.year)
        lay.addLayout(head)
        self.intro = intro = QLabel()
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.table = make_table(["Übertragen", "Bereich in der Anlage EÜR", "Betrag", "Belege", "Hinweis"], stretch_col=4)
        self.table.itemChanged.connect(self._done_changed)
        lay.addWidget(self.table)

        res = QFrame()
        res.setObjectName("card")
        g = QGridLayout(res)
        g.setContentsMargins(18, 12, 18, 12)
        self.l_inc, self.l_exp, self.l_profit = QLabel(), QLabel(), QLabel()
        for i, (lab, val) in enumerate([("Betriebseinnahmen", self.l_inc), ("Betriebsausgaben (abziehbar)", self.l_exp),
                                        ("Gewinn", self.l_profit)]):
            l = QLabel(lab)
            l.setObjectName("kpiLabel")
            val.setObjectName("kpi")
            g.addWidget(l, 0, i)
            g.addWidget(val, 1, i)
        lay.addWidget(res)
        self.warn = QLabel()
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        lay.addLayout(hbox(button("Als Excel speichern", self.export), None,
                           button("Jahr abschließen …", self.lock, danger=True)))
        lay.addStretch(1)
        ctx.listeners.append(self.reload)
        self.reload()

    def _done_key(self, key):
        return f"euer.done.{self.year.currentData()}.{key}"

    def reload(self, *_):
        if get_setting(self.ctx.con, "tax.mode") == "kleinunternehmer":
            self.intro.setText("Diese Summen übertragen Sie in Ihrer Steuersoftware in die Anlage EÜR. Haken Sie ab, "
                               "was übertragen ist. Ausgaben sind Bruttobeträge, weil Kleinunternehmer keine Vorsteuer "
                               "abziehen.")
        else:
            self.intro.setText("Diese Summen übertragen Sie in Ihrer Steuersoftware in die Anlage EÜR. Achtung: Bei "
                               "Regelbesteuerung sind Umsatzsteuer und Vorsteuer hier nicht getrennt ausgewiesen, "
                               "Ausgaben und Abschreibungen sind Bruttobeträge (eingeschränkte Unterstützung) – bitte mit "
                               "dem Steuerberater abstimmen.")
        y = self.year.currentData()
        v = expenses.euer_values(self.ctx.con, y)
        self.values = v
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        rows = [("einnahmen", "Betriebseinnahmen als umsatzsteuerlicher Kleinunternehmer", v["income_cents"], "",
                 "Zahlungseingänge des Jahres (Zufluss)")]
        rows += [(a.key, a.area, a.amount_cents, str(a.count), a.note) for a in v["areas"]]
        for key, area, cents, n, note in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            chk = check_item(get_setting(self.ctx.con, self._done_key(key)) == "1")
            chk.setData(Qt.UserRole, key)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, item(area))
            self.table.setItem(r, 2, item(fmt_eur(cents), align_right=True))
            self.table.setItem(r, 3, item(n, align_right=True))
            self.table.setItem(r, 4, item(note, color=theme.INK_2))
        self.table.blockSignals(False)
        self.l_inc.setText(fmt_eur(v["income_cents"]))
        self.l_exp.setText(fmt_eur(v["expense_cents"]))
        self.l_profit.setText(fmt_eur(v["profit_cents"]))
        warns = []
        if v["missing_receipts"]:
            warns.append(f"Bei {count(v['missing_receipts'], 'Ausgabe', 'Ausgaben')} in {y} fehlt noch der Beleg.")
        if v["locked"]:
            warns.append(f"Das Jahr {y} ist abgeschlossen, Ausgaben und Fahrten sind unveränderbar.")
        self.warn.setText(" ".join(warns))
        self.warn.setVisible(bool(warns))

    def _done_changed(self, it):
        if it.column() == 0:
            set_setting(self.ctx.con, self._done_key(it.data(Qt.UserRole)), "1" if it.checkState() == Qt.Checked else "0")

    def export(self):
        y = self.year.currentData()
        p, _ = QFileDialog.getSaveFileName(self, "EÜR-Werte speichern", str(Path.home() / "Documents" / f"EUeR-Werte-{y}.xlsx"),
                                           "Excel (*.xlsx)")
        if not p:
            return
        from app.services import export
        v = self.values
        summary = [["Betriebseinnahmen (Kleinunternehmer)", to_decimal(v["income_cents"]) / 100]]
        summary += [[a.area, to_decimal(a.amount_cents) / 100, a.note] for a in v["areas"]]
        summary += [["Betriebsausgaben abziehbar", to_decimal(v["expense_cents"]) / 100], ["Gewinn", to_decimal(v["profit_cents"]) / 100]]
        detail = [[fmt_date(e["exp_date"]), e["supplier"], e["description"], CAT[e["category"]].area if e["category"] in CAT else e["category"],
                   to_decimal(e["amount_cents"]) / 100, "ja" if e.get("receipt_path") else "nein"]
                  for e in reversed(expenses.list_expenses(self.ctx.con, y))]
        from app.services import trips
        try:
            export.write_xlsx(Path(p), {"EÜR-Werte": (["Bereich", "Betrag", "Hinweis"], summary),
                                        "Ausgaben": (["Datum", "Lieferant", "Beschreibung", "Bereich", "Betrag", "Beleg"], detail),
                                        "Fahrten": (trips.EXPORT_HEADER, trips.rows_for_export(self.ctx.con, y))})
            open_file(p)
        except Exception as e:  # noqa: BLE001
            error(self, "Export", e)

    def lock(self):
        y = self.year.currentData()
        if expenses.is_locked(self.ctx.con, y):
            QMessageBox.information(self, "Jahr abschließen", f"{y} ist bereits abgeschlossen.")
            return
        if y >= date.today().year:
            QMessageBox.information(self, "Jahr abschließen", f"{y} ist noch nicht vorbei und kann erst nach dem "
                                                              "Jahreswechsel abgeschlossen werden. Für das Vorjahr "
                                                              "bitte oben das Jahr umstellen.")
            return
        if confirm(self, "Jahr abschließen", f"Ausgaben und Fahrten {y} abschließen?\n\nDanach können Ausgaben und Fahrten dieses Jahres nicht mehr "
                   "geändert, gelöscht oder nachgetragen werden (GoBD). Das lässt sich nicht rückgängig machen. "
                   "Am besten erst, wenn die Steuererklärung abgegeben ist."):
            expenses.lock_year(self.ctx.con, y)
            self.ctx.changed()
