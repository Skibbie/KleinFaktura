"""Belegliste für Angebote bzw. Rechnungen (+ Stornos)."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMenu,
                               QMessageBox, QVBoxLayout, QWidget)

from app.db import repo
from app.money import fmt_date, fmt_eur, input_cents
from app.services import documents, payments
from app.ui.common import (STATUS_COLORS, Ctx, button, confirm, count, date_edit, date_value, error, hbox, item, make_table,
                           open_file, page_title, selected_data)
from app.ui.mail_ui import export_after_finalize, send_mail


class PaymentDialog(QDialog):
    def __init__(self, parent, ctx: Ctx, doc: dict):
        super().__init__(parent)
        self.ctx, self.doc = ctx, doc
        self.setWindowTitle(f"Zahlung erfassen – Rechnung {doc['number']}")
        lay = QVBoxLayout(self)
        open_ = payments.open_amount(ctx.con, doc["id"])
        hist = payments.list_payments(ctx.con, doc["id"])
        info = f"Rechnungsbetrag {fmt_eur(doc['total_gross_cents'])} · offen <b>{fmt_eur(open_)}</b>"
        if hist:
            info += "<br>Bisher: " + ", ".join(f"{fmt_date(p['pay_date'])}: {fmt_eur(p['amount_cents'])}" for p in hist)
        lay.addWidget(QLabel(info))
        f = QFormLayout()
        self.date = date_edit()
        self.amount = QLineEdit(fmt_eur(open_, False))
        self.method = QComboBox()
        self.method.setEditable(True)
        self.method.addItems(["Überweisung", "Bar", "PayPal", "Lastschrift", "Verrechnung", "Korrekturbuchung"])
        self.note = QLineEdit()
        f.addRow("Zahlungsdatum", self.date)
        f.addRow("Betrag €", self.amount)
        f.addRow("Zahlungsart", self.method)
        f.addRow("Notiz", self.note)
        lay.addLayout(f)
        hint = QLabel("Zahlungen sind unveränderbar. Fehler bitte mit negativem Betrag (Korrekturbuchung) ausgleichen.")
        hint.setStyleSheet("color:#5A6A6E; font-size:9pt")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _confirm_amount(self, cents: int) -> bool:
        """Rückfrage bei auffälligen Beträgen – Zahlungen sind unveränderbar."""
        con, did = self.ctx.con, self.doc["id"]
        if documents.is_cancelled(con, did):
            if cents < 0:
                return True          # Erstattung nach Storno: der vorgeschlagene Betrag ist bereits negativ
            return confirm(self, "Zahlung", f"Rechnung {self.doc['number']} ist storniert. Trotzdem eine Zahlung buchen?")
        # Höchstens erwartet: Rechnungsbetrag + alle Mahngebühren − bisher gezahlt
        expected = int(self.doc["total_gross_cents"]) + payments.fees_cents(con, did) - payments.paid_cents(con, did)
        if cents > expected:
            return confirm(self, "Zahlung", f"Der Betrag {fmt_eur(cents)} ist höher als der offene Betrag "
                                            f"{fmt_eur(max(expected, 0))} (inkl. Mahngebühren).\n\n"
                                            "Zahlungen lassen sich nicht ändern. Trotzdem buchen?")
        if cents < 0:
            return confirm(self, "Zahlung", f"Negativer Betrag {fmt_eur(cents)} (Rückzahlung/Korrektur) buchen?")
        return True

    def save(self):
        try:
            cents = input_cents(self.amount.text())
            if not self._confirm_amount(cents):
                return
            payments.add_payment(self.ctx.con, self.doc["id"], date_value(self.date), cents,
                                 self.method.currentText(), self.note.text())
        except Exception as e:  # noqa: BLE001
            error(self, "Zahlung", e)
            return
        self.accept()


def record_payment(parent, ctx: Ctx, doc_id: int) -> bool:
    d = documents.get_document(ctx.con, doc_id)
    if not d or d["doc_type"] != "rechnung" or d["status"] != "festgeschrieben":
        QMessageBox.information(parent, "Zahlung", "Zahlungen können nur auf festgeschriebene Rechnungen gebucht werden.")
        return False
    if PaymentDialog(parent, ctx, d).exec():
        ctx.changed()
        return True
    return False


def make_dunning(parent, ctx: Ctx, doc_id: int) -> None:
    d = documents.get_document(ctx.con, doc_id)
    if payments.open_amount(ctx.con, doc_id) <= 0:
        QMessageBox.information(parent, "Mahnung", "Diese Rechnung ist nicht offen.")
        return
    due = d.get("due_date")
    if due and due >= date.today().isoformat() and not confirm(
            parent, "Mahnung", f"Die Rechnung {d['number']} ist erst am {fmt_date(due)} fällig.\n\n"
                               "Mahnungen lassen sich nicht löschen. Trotzdem jetzt mahnen?"):
        return
    lvl = payments.next_dunning_level(ctx.con, doc_id)
    hist = payments.dunning_history(ctx.con, doc_id)
    if hist and hist[-1]["level"] >= 3:
        if not confirm(parent, "Mahnung", "Es wurde bereits die 2. Mahnung versandt. Nochmals eine 2. Mahnung erstellen?"):
            return
    labels = [payments.DUNNING_LABEL[i] for i in (1, 2, 3)]
    from PySide6.QtWidgets import QInputDialog
    choice, ok = QInputDialog.getItem(parent, f"Mahnung zu {d['number']}", "Stufe:", labels, lvl - 1, False)
    if not ok:
        return
    try:
        res = payments.create_dunning(ctx.con, doc_id, level=labels.index(choice) + 1)
    except Exception as e:  # noqa: BLE001
        error(parent, "Mahnung", e)
        return
    ctx.changed()
    open_file(res["path"])


class DocumentsPage(QWidget):
    def __init__(self, ctx: Ctx, kind: str):
        super().__init__()
        self.ctx, self.kind = ctx, kind
        self.types = ("angebot",) if kind == "angebot" else ("rechnung", "storno")
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Angebote" if kind == "angebot" else "Rechnungen"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Nummer, Kunde, Betreff) …")
        self.search.textChanged.connect(self.reload)
        self.year = QComboBox()
        self.year.addItem("Alle Jahre", None)
        for y in range(date.today().year, date.today().year - 11, -1):
            self.year.addItem(str(y), y)
        self.year.setCurrentIndex(1)  # aktuelles Jahr
        self.year.currentIndexChanged.connect(self.reload)
        lay.addLayout(hbox(self.search, self.year))

        new_label = "Neues Angebot" if kind == "angebot" else "Neue Rechnung"
        # Knöpfe, die vom markierten Beleg abhängen: Schlüssel -> Knopf (Freigabe in _update_buttons)
        self.sel_buttons = {"open": button("Öffnen", self.open), "pdf": button("PDF", self.pdf),
                            "mail": button("Per E-Mail", self.mail),
                            "copy": button("Kopieren", self.copy)}
        if kind == "angebot":
            self.sel_buttons["convert"] = button("In Rechnung umwandeln", self.convert)
        else:
            self.sel_buttons.update(payment=button("Zahlung erfassen", self.payment), dunning=button("Mahnen", self.dunning),
                                    cancel=button("Stornieren", self.cancel, danger=True))
        self.sel_buttons["delete"] = button("Entwurf löschen", self.delete, danger=True)
        btns = [button(new_label, self.new, primary=True), *self.sel_buttons.values(), None]
        imp = button("Bestehende importieren ▾", self.import_menu)
        btns.append(imp)
        self.import_btn = imp
        lay.addLayout(hbox(*btns))

        cols = ["Nummer", "Datum", "Kunde", "Betreff", "Betrag", "Offen" if kind != "angebot" else "Gültig bis", "Status", "Quelle"]
        self.table = make_table(cols, stretch_col=2)
        self.table.doubleClicked.connect(self.open)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        lay.addWidget(self.table)
        self._rows: dict[int, dict] = {}
        self.sum_label = QLabel()
        lay.addWidget(self.sum_label)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        rows = documents.list_documents(self.ctx.con, self.types, self.search.text().strip(), self.year.currentData())
        self._rows = {d["id"]: d for d in rows}
        self.table.setRowCount(0)
        total = paid_total = open_total = credit_total = 0
        for d in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            cust = repo.customer_display_name({"first_name": d["first_name"], "last_name": d["last_name"],
                                               "company": d["c_company"]})
            label = d["status_label"]
            color = STATUS_COLORS.get(label.split(" ")[0])
            vals = [d["number"] or "(Entwurf)", fmt_date(d["doc_date"]), cust, d["subject"] or "",
                    fmt_eur(d["total_gross_cents"]),
                    ("" if d["status"] == "entwurf" else fmt_eur(d["open_cents"])) if self.kind != "angebot"
                    else fmt_date(d["valid_until"]),
                    label, "Import" if d["source"] == "import" else ""]
            for i, v in enumerate(vals):
                self.table.setItem(r, i, item(v, d["id"] if i == 0 else None, align_right=i in (4, 5) and self.kind != "angebot" or i == 4,
                                              color=color if i == 6 else None))
            if d["status"] == "festgeschrieben" and d["doc_type"] in ("rechnung", "storno"):
                # Stornos sind negativ und heben die stornierte Rechnung auf
                total += d["total_gross_cents"]
                paid_total += int(d.get("paid_cents") or 0)
                # Forderungen und Guthaben (Überzahlung, Erstattung nach Storno) getrennt, nicht verrechnet
                if d["open_cents"] > 0:
                    open_total += d["open_cents"]
                else:
                    credit_total -= d["open_cents"]
            elif d["status"] == "festgeschrieben" and d["doc_type"] == "angebot":
                total += d["total_gross_cents"]
        period = self.year.currentText() if self.year.currentData() else "alle Jahre"
        drafts = sum(1 for d in rows if d["status"] == "entwurf")
        draft_txt = f", dazu {drafts} Entwurf" + ("e" if drafts != 1 else "") if drafts else ""
        if self.kind == "angebot":
            n = len(rows) - drafts
            self.sum_label.setText(f"{count(n, 'Angebot', 'Angebote')} {period}{draft_txt}: angeboten {fmt_eur(total)}")
        else:
            n = sum(1 for d in rows if d["status"] == "festgeschrieben" and d["doc_type"] == "rechnung")
            self.sum_label.setText(f"{count(n, 'Rechnung', 'Rechnungen')} {period}{draft_txt}: berechnet {fmt_eur(total)}, "
                                   f"bezahlt {fmt_eur(paid_total)}, offen {fmt_eur(open_total)}"
                                   + (f", zu erstatten/Guthaben {fmt_eur(credit_total)}" if credit_total else ""))
        self._update_buttons()

    def _update_buttons(self, *_):
        """Nur passende Aktionen anbieten; der Tooltip sagt, warum ein Knopf gerade nicht geht."""
        d = self._rows.get(selected_data(self.table)) if self.table.selectionModel() else None
        final = bool(d) and d["status"] == "festgeschrieben"
        invoice = final and d["doc_type"] == "rechnung"
        cancelled = invoice and bool(d.get("storno_number"))
        rules = {
            "open": (bool(d), "Bitte einen Beleg markieren."),
            "pdf": (final, "Entwürfe haben noch kein PDF – im Beleg „Vorschau“ nutzen."),
            "mail": (final and bool(d.get("pdf_path")), "Erst nach dem Festschreiben (bei Importen nur mit PDF)."),
            "copy": (bool(d), "Bitte einen Beleg markieren."),
            "convert": (bool(d), "Bitte ein Angebot markieren."),
            "payment": (invoice, "Zahlungen nur auf festgeschriebene Rechnungen."),
            "dunning": (invoice and not cancelled and d["open_cents"] > 0, "Nur offene, festgeschriebene Rechnungen."),
            "cancel": (invoice and not cancelled, "Nur festgeschriebene, noch nicht stornierte Rechnungen."),
            "delete": (bool(d) and d["status"] == "entwurf", "Nur Entwürfe können gelöscht werden."),
        }
        tips = {"mail": "Thunderbird mit vorbereiteter Mail und PDF-Anhang öffnen"}
        for key, btn in self.sel_buttons.items():
            ok, why = rules[key]
            btn.setEnabled(ok)
            btn.setToolTip(tips.get(key, "") if ok else why)

    def _sel(self):
        did = selected_data(self.table)
        if not did:
            QMessageBox.information(self, "Auswahl", "Bitte einen Beleg auswählen.")
        return did

    def new(self):
        from app.ui.editor import open_editor
        open_editor(self, self.ctx, doc_type="angebot" if self.kind == "angebot" else "rechnung")

    def open(self, *_):
        did = self._sel()
        if did:
            from app.ui.editor import open_editor
            open_editor(self, self.ctx, doc_id=did)

    def pdf(self):
        did = self._sel()
        if not did:
            return
        d = documents.get_document(self.ctx.con, did)
        if d["status"] == "festgeschrieben":
            open_file(d.get("pdf_path") or d.get("import_file_path"))
        else:
            QMessageBox.information(self, "PDF", "Entwürfe haben noch kein Archiv-PDF – bitte im Editor „Vorschau“ nutzen.")

    def mail(self):
        did = self._sel()
        if did:
            send_mail(self, self.ctx, documents.get_document(self.ctx.con, did))

    def copy(self):
        did = self._sel()
        if did:
            src = documents.get_document(self.ctx.con, did)
            new_type = "rechnung" if src["doc_type"] == "storno" else src["doc_type"]
            new_id = documents.copy_as_draft(self.ctx.con, did, new_type)
            self.ctx.changed()
            from app.ui.editor import open_editor
            open_editor(self, self.ctx, doc_id=new_id)

    def convert(self):
        did = self._sel()
        if not did:
            return
        src = documents.get_document(self.ctx.con, did)
        if src["status"] != "festgeschrieben" and not confirm(self, "Angebot", "Das Angebot ist noch ein Entwurf. Trotzdem umwandeln?"):
            return
        existing = documents.invoices_for_offer(self.ctx.con, did)
        if existing:
            names = ", ".join(e["number"] or "Entwurf" for e in existing)
            if not confirm(self, "Angebot", f"Aus diesem Angebot wurde bereits eine Rechnung erstellt ({names}).\n\n"
                                            "Trotzdem eine weitere Rechnung anlegen (z.B. Teil- oder Schlussrechnung)?"):
                return
        new_id = documents.copy_as_draft(self.ctx.con, did, "rechnung")
        self.ctx.changed()
        from app.ui.editor import open_editor
        open_editor(self, self.ctx, doc_id=new_id)

    def payment(self):
        did = self._sel()
        if did:
            record_payment(self, self.ctx, did)

    def dunning(self):
        did = self._sel()
        if did:
            make_dunning(self, self.ctx, did)

    def cancel(self):
        did = self._sel()
        if not did:
            return
        d = documents.get_document(self.ctx.con, did)
        if d["doc_type"] != "rechnung" or d["status"] != "festgeschrieben":
            QMessageBox.information(self, "Storno", "Nur festgeschriebene Rechnungen können storniert werden.")
            return
        paid = payments.paid_cents(self.ctx.con, did)
        extra = f"\n\nAchtung: Es sind bereits Zahlungen über {fmt_eur(paid)} gebucht – ggf. Rückzahlung erfassen." if paid else ""
        if not confirm(self, "Rechnung stornieren",
                       f"Zu Rechnung {d['number']} ({fmt_eur(d['total_gross_cents'])}) wird eine Stornorechnung mit "
                       f"eigener Nummer erstellt und festgeschrieben.{extra}\n\nFortfahren?"):
            return
        try:
            s = documents.create_storno(self.ctx.con, did)
        except Exception as e:  # noqa: BLE001
            error(self, "Storno fehlgeschlagen", e)
            return
        self.ctx.changed()
        copied = export_after_finalize(self, self.ctx, s)
        QMessageBox.information(self, "Storniert", f"Stornorechnung {s['number']} wurde erstellt.\n"
                                "Bei Bedarf jetzt eine korrigierte Rechnung über „Kopieren“ anlegen." + copied)
        open_file(s["pdf_path"])

    def delete(self):
        did = self._sel()
        if not did:
            return
        d = documents.get_document(self.ctx.con, did)
        if d["status"] != "entwurf":
            QMessageBox.information(self, "Löschen", "Festgeschriebene Belege können nicht gelöscht werden (GoBD).")
            return
        if confirm(self, "Entwurf löschen", "Entwurf wirklich löschen?"):
            documents.delete_draft(self.ctx.con, did)
            self.ctx.changed()

    def import_menu(self):
        from app.ui.import_dialogs import run_legacy_import
        m = QMenu(self)
        m.addAction("Dateien auswählen (.ods, .xlsx, .pdf) …", lambda: run_legacy_import(self, self.ctx) and self.ctx.changed())
        m.addAction("Ordner auswählen …", lambda: run_legacy_import(self, self.ctx, folder=True) and self.ctx.changed())
        m.exec(self.import_btn.mapToGlobal(self.import_btn.rect().bottomLeft()))
