"""Beleg-Editor für Angebote, Rechnungen und Stornorechnungen."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from app import config
from app.db import repo
from app.db.database import get_decimal_setting, get_int_setting, get_setting
from app.money import fmt_eur, fmt_qty, input_cents, input_decimal, line_total_cents
from app.services import documents, kleinunternehmer, numbering
from app.ui.common import CANCEL, SAVE, Ctx, ask_save, button, confirm, date_edit, date_value, error, hbox, open_file, set_date
from app.ui.customers import CustomerDialog, CustomerPicker, ProjectDialog
from app.ui.mail_ui import ask_send_after_finalize, export_after_finalize, send_mail

COL_TITLE, COL_QTY, COL_UNIT, COL_PRICE, COL_TAX, COL_TOTAL = range(6)


def missing_glyph_warning(doc: dict, lines: list[dict]) -> list[str]:
    """Hinweis auf Zeichen (z.B. Emojis), die im archivierten PDF als leere Kästchen erschienen."""
    from app.output.pdf_invoice import missing_glyphs
    texts = [doc.get(k) or "" for k in ("subject", "salutation_text", "header_text", "footer_text", "notes_text")]
    texts += [f"{ln.get('title') or ''} {ln.get('description') or ''} {ln.get('unit') or ''}" for ln in lines]
    try:
        chars = missing_glyphs(" ".join(texts))
    except Exception:  # noqa: BLE001 – keine Schrift gefunden o.ä.: das meldet die PDF-Erzeugung selbst
        return []
    if not chars:
        return []
    return [f"Diese Zeichen kann die Belegschrift nicht darstellen und erscheinen im PDF als leere Kästchen: "
            f"{' '.join(sorted(chars))}"]
UNITS = ["Stunden", "Stück", "pauschal", "km", "Tage", "m²", "Monate"]


class TextPicker(QDialog):
    def __init__(self, parent, ctx: Ctx, categories: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Textbaustein einfügen")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        for cat in categories:
            for tb in repo.list_textblocks(ctx.con, cat):
                it = QListWidgetItem(f"{repo.TEXT_CATEGORIES.get(cat, cat)}: {tb['title']}")
                it.setData(Qt.UserRole, tb["body"])
                self.list.addItem(it)
        self.list.currentItemChanged.connect(lambda cur, _: self.preview.setPlainText(cur.data(Qt.UserRole) if cur else ""))
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        sp = QSplitter()
        sp.addWidget(self.list)
        sp.addWidget(self.preview)
        lay.addWidget(sp)
        self.mode = QComboBox()
        self.mode.addItems(["Ersetzen", "Anhängen"])
        lay.addLayout(hbox(QLabel("Einfügen:"), self.mode, None, button("Abbrechen", self.reject),
                           button("Einfügen", self.accept, primary=True)))
        if self.list.count():
            self.list.setCurrentRow(0)

    def text(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""


class TextField(QWidget):
    """Mehrzeiliges Textfeld mit Textbaustein-Knopf."""

    def __init__(self, ctx: Ctx, categories: list[str], height: int = 60, remember: bool = True):
        super().__init__()
        self.ctx, self.categories = ctx, categories
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        lay = QHBoxLayout()
        self.edit = QPlainTextEdit()
        self.edit.setFixedHeight(height)
        lay.addWidget(self.edit)
        b = button("Baustein", self.pick, tooltip="Textbaustein einfügen")
        lay.addWidget(b, alignment=Qt.AlignTop)
        outer.addLayout(lay)
        self.remember = QCheckBox("In Textbausteine übernehmen")
        self.remember.setToolTip("Geänderter Text wird beim Speichern als Textbaustein angelegt, falls es ihn noch nicht gibt")
        self.remember.setChecked(get_setting(ctx.con, "textblock.remember_default", "1") == "1")
        self.remember.setVisible(remember)
        outer.addWidget(self.remember)

    def pick(self):
        dlg = TextPicker(self, self.ctx, self.categories)
        if dlg.exec() and dlg.text():
            if dlg.mode.currentIndex() == 0:
                self.edit.setPlainText(dlg.text())
            else:
                self.edit.setPlainText((self.edit.toPlainText().rstrip() + "\n" + dlg.text()).strip())

    def text(self) -> str:
        return self.edit.toPlainText()

    def setText(self, t: str):
        self.edit.setPlainText(t or "")

    def setReadOnly(self, ro: bool):
        self.edit.setReadOnly(ro)
        if ro:
            self.remember.setVisible(False)


class DocumentEditor(QDialog):
    def __init__(self, parent, ctx: Ctx, doc_id: int | None = None, doc_type: str = "rechnung",
                 customer_id: int | None = None):
        super().__init__(parent)
        self.ctx = ctx
        con = ctx.con
        if doc_id:
            self.doc = documents.get_document(con, doc_id)
            lines = documents.get_lines(con, doc_id)
        else:
            self.doc = documents.new_draft(con, doc_type, customer_id)
            lines = []
        self.readonly = self.doc.get("status") == "festgeschrieben"
        dt = self.doc["doc_type"]
        label = documents.DOC_TYPE_LABEL[dt]
        self.setWindowTitle(f"{label} {self.doc.get('number') or '(Entwurf)'}")
        self.resize(1180, 760)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        outer = QVBoxLayout(self)
        title_row = QHBoxLayout()
        if self.doc.get("number"):
            title_txt = f"{label} {self.doc['number']}"
        else:
            title_txt = {"rechnung": "Neue Rechnung", "angebot": "Neues Angebot", "storno": "Stornorechnung"}[dt]                 if not doc_id else f"{label} (Entwurf)"
        self.page_title = QLabel(title_txt)
        self.page_title.setObjectName("pageTitle")
        title_row.addWidget(self.page_title)
        title_row.addStretch(1)
        title_row.addWidget(button("Zurück zur Liste", self.close_dialog))
        outer.addLayout(title_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(4, 4, 12, 4)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        if self.readonly:
            ro = QLabel(f"🔒 Festgeschrieben am {self.doc.get('finalized_at') or ''} – unveränderbar (GoBD). "
                        "Korrekturen nur über Stornorechnung.")
            ro.setObjectName("warn")
            root.addWidget(ro)

        # ---- Kopf
        head = QGroupBox(f"{label}")
        g = QGridLayout(head)
        self.customer = CustomerPicker(ctx)
        self.customer.currentIndexChanged.connect(self._customer_changed)
        self.project = QComboBox()
        self.doc_date = date_edit(self.doc.get("doc_date"))
        self.service_date = date_edit(self.doc.get("service_date"), allow_empty=True)
        self.service_to = date_edit(self.doc.get("service_date_to"), allow_empty=True)
        self.due_date = date_edit(self.doc.get("due_date"), allow_empty=True)
        self.valid_until = date_edit(self.doc.get("valid_until"), allow_empty=True)
        self.subject = QLineEdit(self.doc.get("subject") or "")
        self.subject.setPlaceholderText("optional, z.B. Projekt-/Vorgangsbezeichnung")
        self.tax_mode = QComboBox()
        self.tax_mode.addItem("Kleinunternehmer (§ 19 UStG)", "kleinunternehmer")
        self.tax_mode.addItem("Regelbesteuerung (mit USt, eingeschränkt)", "regel")
        self.tax_mode.setItemData(1, kleinunternehmer.REGEL_HINWEIS, Qt.ToolTipRole)
        self.tax_mode.setCurrentIndex(0 if self.doc.get("tax_mode") == "kleinunternehmer" else 1)
        self.tax_mode.currentIndexChanged.connect(self._tax_mode_changed)
        self.zugferd = QCheckBox("ZUGFeRD / E-Rechnung einbetten")
        self.zugferd.setChecked(bool(self.doc.get("zugferd")))
        self.zugferd.setToolTip("Für Geschäftskunden: maschinenlesbare E-Rechnung (EN 16931) im PDF/A-3")
        self.girocode = QCheckBox("GiroCode (QR zum Bezahlen)")
        self.girocode.setChecked(bool(self.doc.get("girocode")))
        self.doc_date.dateChanged.connect(self._doc_date_changed)

        g.addWidget(QLabel("Kunde"), 0, 0)
        g.addLayout(hbox(self.customer, button("Neu…", self.new_customer), button("Bearbeiten…", self.edit_customer)), 0, 1, 1, 3)
        g.addWidget(QLabel("Projekt / Objekt"), 1, 0)
        g.addLayout(hbox(self.project, button("Neu…", self.new_project)), 1, 1, 1, 3)
        g.addWidget(QLabel("Belegdatum"), 2, 0)
        g.addWidget(self.doc_date, 2, 1)
        if dt == "angebot":
            g.addWidget(QLabel("Gültig bis"), 2, 2)
            g.addWidget(self.valid_until, 2, 3)
        else:
            g.addWidget(QLabel("Leistungsdatum / bis"), 3, 0)
            g.addLayout(hbox(self.service_date, QLabel("bis"), self.service_to, None), 3, 1)
            if dt == "rechnung":
                g.addWidget(QLabel("Fällig am"), 2, 2)
                g.addWidget(self.due_date, 2, 3)
        g.addWidget(QLabel("Betreff"), 4, 0)
        g.addWidget(self.subject, 4, 1, 1, 3)
        g.addWidget(QLabel("Besteuerung"), 5, 0)
        g.addWidget(self.tax_mode, 5, 1)
        if dt != "angebot":
            g.addLayout(hbox(self.zugferd, self.girocode if dt == "rechnung" else QWidget(), None), 5, 2, 1, 2)
        if self.doc.get("number"):
            g.addWidget(QLabel("Nummer"), 6, 0)
            g.addWidget(QLabel(f"<b>{self.doc['number']}</b>"), 6, 1)
        else:
            nxt = numbering.peek_next(con, dt)
            g.addWidget(QLabel("Nummer"), 6, 0)
            g.addWidget(QLabel(f"<span style='color:#5A6A6E'>wird beim Festschreiben vergeben (voraussichtlich {nxt})</span>"), 6, 1, 1, 3)
        root.addWidget(head)

        # ---- Texte oben
        texts = QGroupBox("Anschreiben")
        tf = QFormLayout(texts)
        self.salutation = TextField(ctx, ["anrede"], 32)
        self.salutation.edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.header = TextField(ctx, ["kopf_angebot", "kopf_rechnung", "storno"] if dt != "angebot" else ["kopf_angebot", "kopf_rechnung"], 48)
        tf.addRow("Anrede", self.salutation)
        # Platzhalter wie {briefanrede} sagen Einsteigern nichts: darunter steht, was im Brief erscheint
        self.salutation_preview = QLabel()
        self.salutation_preview.setObjectName("muted")
        tf.addRow("", self.salutation_preview)
        self.salutation.edit.textChanged.connect(self._update_salutation_preview)
        tf.addRow("Einleitung", self.header)
        root.addWidget(texts)

        # ---- Positionen
        pos = QGroupBox("Positionen")
        pl = QVBoxLayout(pos)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Bezeichnung (fett)", "Menge", "Einheit", "Einzelpreis €", "USt %", "Gesamt"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        for c, w in [(COL_QTY, 80), (COL_UNIT, 100), (COL_PRICE, 110), (COL_TAX, 60), (COL_TOTAL, 110)]:
            self.table.setColumnWidth(c, w)
        self.table.verticalHeader().setVisible(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(self._row_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.setMinimumHeight(170)
        pl.addWidget(self.table)
        self.desc = TextField(ctx, ["position"], 70)
        self.desc.edit.setPlaceholderText("Beschreibung der markierten Position (mehrzeilig, z.B. Objektadresse, Leistungsumfang) – "
                                          "Platzhalter {objekt_adresse} wird ersetzt")
        self.desc.edit.textChanged.connect(self._desc_changed)
        pl.addWidget(QLabel("Beschreibung der markierten Position:"))
        pl.addWidget(self.desc)
        self.btn_row = hbox(button("+ Position", self.add_line), button("+ Artikel / Leistung…", self.add_article),
                            button("Kopieren", self.copy_line), button("▲", lambda: self.move_line(-1)),
                            button("▼", lambda: self.move_line(1)), button("Entfernen", self.remove_line, danger=True), None)
        pl.addLayout(self.btn_row)
        self.totals = QLabel()
        self.totals.setAlignment(Qt.AlignRight)
        self.totals.setStyleSheet("font-size: 12pt")
        pl.addWidget(self.totals)
        root.addWidget(pos, stretch=1)

        # ---- Texte unten
        foot = QGroupBox("Schluss")
        ff = QFormLayout(foot)
        self.footer = TextField(ctx, ["fuss_rechnung", "fuss_angebot", "gruss"], 80)
        self.notes = TextField(ctx, ["hinweis"], 44)
        ff.addRow("Schlusstext", self.footer)
        ff.addRow("Hinweise", self.notes)
        root.addWidget(foot)
        ph = QLabel("Platzhalter: {briefanrede} {faellig} {zahlungsziel} {iban} {belegnr} {gueltig_bis} {inhaber} "
                    "{objekt_adresse} {projekt} {leistungsdatum} {datum} {betrag}")
        ph.setStyleSheet("color:#5A6A6E; font-size:8.5pt")
        root.addWidget(ph)

        # ---- Aktionen
        self.actions = QHBoxLayout()
        self.actions.addWidget(button("Vorschau (PDF)", self.preview))
        if self.readonly:
            self.actions.addWidget(button("Archiv-PDF öffnen", lambda: open_file(self.doc.get("pdf_path") or self.doc.get("import_file_path"))))
            self.actions.addWidget(button("Per E-Mail senden…", lambda: send_mail(self, self.ctx, self.doc),
                                          tooltip="Thunderbird mit vorbereiteter Mail und PDF-Anhang öffnen"))
        if dt == "rechnung":
            trip_btn = button("Fahrt hinzufügen…", self.add_trip,
                              tooltip="Neue Fahrt mit Bezug zu dieser Rechnung erfassen" if self.readonly
                              else "Erst nach dem Festschreiben möglich (Fahrten werden mit der festen Rechnung verknüpft)")
            trip_btn.setEnabled(self.readonly)
            self.actions.addWidget(trip_btn)
        self.actions.addStretch(1)
        if not self.readonly:
            self.actions.addWidget(button("Speichern", self.save))
            self.actions.addWidget(button("Festschreiben (endgültig)…", self.finalize, primary=True,
                                          tooltip="Nummer vergeben, PDF archivieren – danach unveränderbar"))
        outer.addLayout(self.actions)

        # ---- Werte laden
        if self.doc.get("customer_id"):
            self.customer.select(self.doc["customer_id"])
        self._customer_changed()
        if self.doc.get("project_id"):
            idx = self.project.findData(self.doc["project_id"])
            if idx >= 0:
                self.project.setCurrentIndex(idx)
        self.salutation.setText(self.doc.get("salutation_text"))
        self.header.setText(self.doc.get("header_text"))
        self.footer.setText(self.doc.get("footer_text"))
        self.notes.setText(self.doc.get("notes_text"))
        self._loading = True
        for ln in lines:
            self._append_row(ln)
        self._loading = False
        if not lines and not self.readonly:
            self.add_line()
        self._recalc()
        self.mark_clean()
        self._remember_base()
        if self.readonly:
            self._set_readonly()

    # ------------------------------------------------------------ Kunde / Projekt
    def _customer_changed(self, *_):
        cid = self.customer.current_id()
        self.project.blockSignals(True)
        cur = self.project.currentData()
        self.project.clear()
        self.project.addItem("– kein Projekt –", None)
        if cid:
            for p in repo.list_projects(self.ctx.con, cid):
                addr = repo.project_address(p)
                self.project.addItem(f"{p['name']}" + (f" – {addr}" if addr else ""), p["id"])
        idx = self.project.findData(cur)
        self.project.setCurrentIndex(max(idx, 0))
        self.project.blockSignals(False)
        self._dirty = True
        self._update_salutation_preview()

    def _update_salutation_preview(self, *_):
        text = self.salutation.text()
        if "{" not in text:
            self.salutation_preview.setText("")
            return
        if not self.customer.current_id():
            self.salutation_preview.setText("Im Brief steht hier die Anrede des gewählten Kunden.")
            return
        doc = {**self.doc, "customer_id": self.customer.current_id(), "project_id": self.project.currentData()}
        shown = documents.render(text, documents.placeholders(self.ctx.con, doc)).replace("\n", " ")
        self.salutation_preview.setText(f"Im Brief: {shown}")

    def new_customer(self):
        dlg = CustomerDialog(self, self.ctx)
        if dlg.exec():
            self.customer.reload(dlg.customer_id)
            self.ctx.changed()

    def edit_customer(self):
        cid = self.customer.current_id()
        if cid and CustomerDialog(self, self.ctx, repo.get_customer(self.ctx.con, cid)).exec():
            self.customer.reload(cid)
            self._customer_changed()

    def new_project(self):
        cid = self.customer.current_id()
        if not cid:
            QMessageBox.information(self, "Projekt", "Bitte zuerst einen Kunden wählen.")
            return
        dlg = ProjectDialog(self, self.ctx, cid)
        if dlg.exec():
            self._customer_changed()
            idx = self.project.findData(dlg.project_id)
            self.project.setCurrentIndex(max(idx, 0))

    def _doc_date_changed(self, *_):
        if self.readonly:
            return
        from datetime import timedelta
        d = self.doc_date.date().toPython()
        if self.doc["doc_type"] == "rechnung":
            set_date(self.due_date, (d + timedelta(days=get_int_setting(self.ctx.con, "invoice.payment_days", "14"))).isoformat())
            if date_value(self.service_date) == self.doc.get("service_date"):
                set_date(self.service_date, d.isoformat())
        elif self.doc["doc_type"] == "angebot":
            set_date(self.valid_until, (d + timedelta(days=get_int_setting(self.ctx.con, "offer.valid_days", "30"))).isoformat())

    def _tax_mode_changed(self, *_):
        mode = self.tax_mode.currentData()
        notes = self.notes.text()
        ku_text = repo.default_text(self.ctx.con, "hinweis")
        if mode == "regel" and "§ 19" in notes:
            # nur den Kleinunternehmer-Hinweis entfernen, nicht jede Zeile mit „19“ (z.B. „Zahlbar bis 19.11.“)
            self.notes.setText("\n".join(l for l in notes.splitlines() if "§ 19" not in l and "Kleinunternehmer" not in l))
        elif mode == "kleinunternehmer" and "19" not in notes:
            self.notes.setText(ku_text)
        if mode == "regel":
            default_rate = self._default_rate()
            for r in range(self.table.rowCount()):
                it = self.table.item(r, COL_TAX)
                if it and it.text() in ("", "0"):
                    it.setText(default_rate)
        self.table.setColumnHidden(COL_TAX, mode != "regel")
        self._recalc()

    def _default_rate(self) -> str:
        return format(get_decimal_setting(self.ctx.con, "tax.default_rate", "19").normalize(), "f").replace(".", ",")

    # ------------------------------------------------------------ Positionen
    def _append_row(self, ln: dict):
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        title = QTableWidgetItem(ln.get("title", ""))
        title.setData(Qt.UserRole, ln.get("description", ""))
        self.table.setItem(r, COL_TITLE, title)
        for c, v in [(COL_QTY, fmt_qty(ln.get("quantity", "1"))), (COL_UNIT, ln.get("unit", "")),
                     (COL_PRICE, fmt_eur(ln.get("unit_price_cents", 0), False)),
                     (COL_TAX, str(ln.get("tax_rate") or self._default_rate())),
                     (COL_TOTAL, "")]:
            it = QTableWidgetItem(v)
            if c in (COL_QTY, COL_PRICE, COL_TAX, COL_TOTAL):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c == COL_TOTAL:
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self.table.setCurrentCell(r, COL_TITLE)
        self._recalc()

    def add_line(self):
        self._append_row({"title": "", "quantity": "1", "unit": "Stunden", "unit_price_cents": 0})
        self.table.editItem(self.table.item(self.table.rowCount() - 1, COL_TITLE))

    def add_article(self):
        arts = repo.list_articles(self.ctx.con)
        if not arts:
            QMessageBox.information(self, "Artikel", "Noch keine Artikel angelegt (Textbausteine & Artikel).")
            return
        labels = [f"{a['title']} – {fmt_eur(a['unit_price_cents'])}/{a['unit']}" for a in arts]
        choice, ok = QInputDialog.getItem(self, "Artikel / Leistung", "Auswahl:", labels, 0, False)
        if ok:
            a = arts[labels.index(choice)]
            desc = a["description"] or ""
            if self.project.currentData() and "{objekt_adresse}" not in desc:
                desc = ("{objekt_adresse}\n" + desc).strip()
            self._append_row({"title": a["title"], "description": desc, "quantity": "1", "unit": a["unit"],
                              "unit_price_cents": a["unit_price_cents"], "tax_rate": a["tax_rate"]})
            self.desc.setText(desc)

    def copy_line(self):
        r = self.table.currentRow()
        if r >= 0:
            self._append_row(self._row_dict(r))

    def remove_line(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._recalc()

    def move_line(self, delta: int):
        r = self.table.currentRow()
        t = r + delta
        if r < 0 or t < 0 or t >= self.table.rowCount():
            return
        a, b = self._row_dict(r), self._row_dict(t)
        self.table.blockSignals(True)
        for row, d in ((r, b), (t, a)):
            self.table.item(row, COL_TITLE).setText(d["title"])
            self.table.item(row, COL_TITLE).setData(Qt.UserRole, d["description"])
            self.table.item(row, COL_QTY).setText(fmt_qty(d["quantity"]))
            self.table.item(row, COL_UNIT).setText(d["unit"])
            self.table.item(row, COL_PRICE).setText(fmt_eur(d["unit_price_cents"], False))
            self.table.item(row, COL_TAX).setText(d["tax_rate"])
        self.table.blockSignals(False)
        self.table.setCurrentCell(t, COL_TITLE)
        self._recalc()

    def _context_menu(self, pos):
        if self.readonly:
            return
        m = QMenu(self)
        m.addAction("Position hinzufügen", self.add_line)
        m.addAction("Artikel einfügen…", self.add_article)
        m.addAction("Kopieren", self.copy_line)
        m.addAction("Entfernen", self.remove_line)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _row_dict(self, r: int) -> dict:
        def t(c):
            it = self.table.item(r, c)
            return it.text().strip() if it else ""
        return {"title": t(COL_TITLE), "description": self.table.item(r, COL_TITLE).data(Qt.UserRole) or "",
                "quantity": str(input_decimal(t(COL_QTY) or "0")), "unit": t(COL_UNIT),
                "unit_price_cents": input_cents(t(COL_PRICE) or "0"),
                # „19 %“, „7%“ oder „7,5“ einheitlich speichern; Unlesbares löst hier (je Zeile) den Fehler aus
                "tax_rate": format(input_decimal(t(COL_TAX) or "0").normalize(), "f")}

    def lines(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            try:
                d = self._row_dict(r)
            except ValueError as e:
                raise ValueError(f"Position {r + 1}: {e}") from e
            if d["title"] or d["description"] or d["unit_price_cents"]:
                out.append(d)
        return out

    def _row_changed(self, row, *_):
        self.desc.edit.blockSignals(True)
        it = self.table.item(row, COL_TITLE) if row >= 0 else None
        self.desc.setText(it.data(Qt.UserRole) if it else "")
        self.desc.setReadOnly(self.readonly or it is None)
        self.desc.edit.blockSignals(False)

    def _desc_changed(self):
        r = self.table.currentRow()
        it = self.table.item(r, COL_TITLE) if r >= 0 else None
        if it is not None:
            it.setData(Qt.UserRole, self.desc.text())
            self._dirty = True

    def _item_changed(self, it):
        if getattr(self, "_loading", False):
            return
        self._dirty = True
        if it.column() in (COL_QTY, COL_PRICE, COL_TAX):
            self._recalc()

    def _recalc(self):
        self.table.blockSignals(True)
        mode = self.tax_mode.currentData()
        try:
            lines = []
            for r in range(self.table.rowCount()):
                try:
                    d = self._row_dict(r)
                    t = line_total_cents(d["quantity"], d["unit_price_cents"])
                    self.table.item(r, COL_TOTAL).setText(fmt_eur(t))
                    self.table.item(r, COL_TOTAL).setForeground(Qt.black)
                    lines.append(d)
                except ValueError:
                    self.table.item(r, COL_TOTAL).setText("ungültig")
                    self.table.item(r, COL_TOTAL).setForeground(Qt.red)
            tot = documents.compute_totals(lines, mode)
        finally:
            self.table.blockSignals(False)
        self.table.setColumnHidden(COL_TAX, mode != "regel")
        if mode == "regel":
            parts = [f"Netto {fmt_eur(tot['net'])}"] + [f"USt {b['rate']} %: {fmt_eur(b['tax_cents'])}" for b in tot["breakdown"]]
            self.totals.setText(" · ".join(parts) + f" · <b>Gesamt {fmt_eur(tot['gross'])}</b>")
        else:
            self.totals.setText(f"<b>Summe: {fmt_eur(tot['gross'])}</b>")
        self._totals = tot

    # ------------------------------------------------------------ Speichern / Festschreiben
    def _collect(self) -> dict:
        d = dict(self.doc)
        d.update(customer_id=self.customer.current_id(), project_id=self.project.currentData(),
                 doc_date=date_value(self.doc_date), subject=self.subject.text().strip(),
                 salutation_text=self.salutation.text(), header_text=self.header.text(),
                 footer_text=self.footer.text(), notes_text=self.notes.text(),
                 tax_mode=self.tax_mode.currentData(), zugferd=int(self.zugferd.isChecked()),
                 girocode=int(self.girocode.isChecked()))
        if d["doc_type"] == "angebot":
            d.update(valid_until=date_value(self.valid_until), service_date=None, service_date_to=None, due_date=None)
        else:
            d.update(service_date=date_value(self.service_date), service_date_to=date_value(self.service_to),
                     due_date=date_value(self.due_date) if d["doc_type"] == "rechnung" else None)
        return d

    def save(self, quiet: bool = False) -> bool:
        try:
            lines = self.lines()
            doc = self._collect()
            doc_id = documents.save_draft(self.ctx.con, doc, lines)
            self.doc = documents.get_document(self.ctx.con, doc_id)
        except Exception as e:  # noqa: BLE001
            error(self, "Speichern fehlgeschlagen", e)
            return False
        self._remember_texts(lines)
        self.mark_clean()
        self.ctx.changed()
        if not quiet:
            self.setWindowTitle(f"{documents.DOC_TYPE_LABEL[self.doc['doc_type']]} (Entwurf gespeichert)")
        return True

    def _text_fields(self) -> list[tuple[TextField, str]]:
        """Textfelder mit der Kategorie, in die ein neuer Text als Baustein übernommen wird."""
        dt = self.doc["doc_type"]
        head = {"angebot": "kopf_angebot", "rechnung": "kopf_rechnung", "storno": "storno"}[dt]
        foot = "fuss_angebot" if dt == "angebot" else "fuss_rechnung"
        return [(self.salutation, "anrede"), (self.header, head), (self.footer, foot), (self.notes, "hinweis")]

    def _remember_base(self) -> None:
        """Stand der Texte merken; übernommen wird nur, was danach geändert oder neu eingegeben wurde."""
        self._base_texts = {id(tf): tf.text().strip() for tf, _ in self._text_fields()}
        self._base_descs = {(ln.get("description") or "").strip() for ln in self.lines()}
        self._base_descs |= {(a.get("description") or "").strip() for a in repo.list_articles(self.ctx.con, True)}

    def _remember_texts(self, lines: list[dict]) -> None:
        if self.readonly:
            return
        try:
            for tf, cat in self._text_fields():
                if tf.remember.isChecked() and tf.text().strip() != self._base_texts.get(id(tf)):
                    repo.remember_text(self.ctx.con, cat, tf.text())
            if self.desc.remember.isChecked():
                for ln in lines:
                    if (ln.get("description") or "").strip() not in self._base_descs:
                        repo.remember_text(self.ctx.con, "position", ln["description"])
        except Exception as e:  # noqa: BLE001 – der Beleg ist gespeichert, nur die Übernahme scheiterte
            error(self, "Textbausteine", f"Texte konnten nicht als Textbausteine übernommen werden: {e}")
        self._remember_base()

    def preview(self):
        try:
            if self.readonly:
                doc, lines = self.doc, documents.get_lines(self.ctx.con, self.doc["id"])
            else:
                doc, lines = self._collect(), self.lines()
            # eindeutiger Name: eine noch im PDF-Betrachter geöffnete ältere Vorschau blockiert nichts
            path = config.preview_dir() / f"Vorschau-{doc.get('id') or 'neu'}-{datetime.now():%H%M%S%f}.pdf"
            documents.render_preview(self.ctx.con, doc, lines, path)
            open_file(path)
        except Exception as e:  # noqa: BLE001
            error(self, "Vorschau fehlgeschlagen", e)

    def finalize(self):
        if not self.save(quiet=True):
            return
        lines = documents.get_lines(self.ctx.con, self.doc["id"])
        problems = documents.validate(self.ctx.con, self.doc, lines)
        if problems:
            QMessageBox.warning(self, "Pflichtangaben fehlen", "Bitte ergänzen:\n\n• " + "\n• ".join(problems))
            return
        odd = documents.plausibility_warnings(self.doc, lines, default_tax_mode=get_setting(self.ctx.con, "tax.mode"))
        odd += missing_glyph_warning(self.doc, lines)
        if odd and not confirm(self, "Bitte prüfen", "• " + "\n• ".join(odd) + "\n\nTrotzdem festschreiben?"):
            return
        warns = kleinunternehmer.check_before_finalize(self.ctx.con, self.doc)
        if warns and not confirm(self, "Kleinunternehmer-Grenze", "\n\n".join(warns) + "\n\nTrotzdem festschreiben?"):
            return
        label = documents.DOC_TYPE_LABEL[self.doc["doc_type"]]
        nxt = numbering.peek_next(self.ctx.con, self.doc["doc_type"], self.doc_date.date().toPython())
        if not confirm(self, f"{label} festschreiben",
                       f"{label} jetzt festschreiben?\n\nNummer: {nxt}\n"
                       f"Kunde: {self.customer.currentText()}\n"
                       f"Datum: {self.doc_date.text()}\n"
                       f"Besteuerung: {self.tax_mode.currentText()}\n"
                       f"Betrag: {fmt_eur(self.doc['total_gross_cents'])}\n\n"
                       "Danach ist der Beleg unveränderbar (GoBD). Korrekturen sind nur per Storno möglich."):
            return
        try:
            self.doc = documents.finalize(self.ctx.con, self.doc["id"])
        except documents.ValidationError as e:
            QMessageBox.warning(self, "Pflichtangaben fehlen", "• " + "\n• ".join(e.problems))
            return
        except Exception as e:  # noqa: BLE001
            error(self, "Festschreiben fehlgeschlagen", e)
            return
        self.ctx.changed()
        copied = export_after_finalize(self, self.ctx, self.doc)
        if ask_send_after_finalize(self, f"{label} {self.doc['number']} wurde festgeschrieben und archiviert.{copied}"):
            send_mail(self, self.ctx, self.doc)
        else:
            open_file(self.doc["pdf_path"])
        self.accept()

    def add_trip(self):
        """Fahrt zu dieser (festgeschriebenen) Rechnung erfassen; Kunde, Objekt und Ziel werden vorbelegt."""
        from app.ui.trips_page import TripDialog
        d = self.doc
        prefill = {"customer_id": d.get("customer_id"), "project_id": d.get("project_id"), "document_id": d["id"],
                   "trip_date": d.get("service_date") or d.get("doc_date")}
        dlg = TripDialog(self, self.ctx, prefill)
        if dlg.exec():
            self.ctx.changed()
            QMessageBox.information(self, "Fahrt gespeichert", f"Die Fahrt wurde mit Rechnung {d['number']} verknüpft.")

    def _set_readonly(self):
        for w in (self.customer, self.project, self.doc_date, self.service_date, self.service_to, self.due_date,
                  self.valid_until, self.subject, self.tax_mode, self.zugferd, self.girocode):
            w.setEnabled(False)
        for tf in (self.salutation, self.header, self.footer, self.notes, self.desc):
            tf.setReadOnly(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for i in range(self.btn_row.count()):
            w = self.btn_row.itemAt(i).widget()
            if w:
                w.setEnabled(False)

    def _snapshot(self):
        try:
            d = self._collect()
            return ({k: d.get(k) for k in documents.DOC_EDIT_FIELDS}, self.lines())
        except Exception:  # noqa: BLE001 – ungültige Eingabe zählt als Änderung
            return object()

    def mark_clean(self):
        self._clean = self._snapshot()
        self._dirty = False

    def is_dirty(self) -> bool:
        return not self.readonly and getattr(self, "_clean", None) is not None and self._snapshot() != self._clean

    def close_dialog(self):
        if self.is_dirty():
            r = ask_save(self, "Der Beleg wurde geändert.")
            if r == CANCEL:
                return
            if r == SAVE and not self.save(quiet=True):
                return
        self.accept()

    def reject(self):
        self.close_dialog()


def open_editor(parent, ctx: Ctx, **kwargs) -> None:
    """Öffnet den Beleg-Editor eingebettet im Hauptfenster (Fallback: eigenes Fenster)."""
    host = getattr(ctx, "editor_host", None)
    if host is not None:
        host.open_editor(**kwargs)
        return
    DocumentEditor(parent, ctx, **kwargs).exec()
    ctx.changed()
