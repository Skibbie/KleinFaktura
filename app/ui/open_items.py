"""Offene Posten mit Zahlungserfassung und Mahnwesen."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from app.db import repo
from app.money import fmt_date, fmt_eur
from app.services import documents, payments
from app.ui.common import Ctx, button, hbox, item, make_table, open_file, page_title, selected_data
from app.ui.documents_page import make_dunning, record_payment


class OpenItemsPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Offene Posten"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Rechnungsnummer, Kunde) …")
        self.search.textChanged.connect(self.reload)
        self.filter = QComboBox()
        self.filter.addItem("Alle offenen", "all")
        self.filter.addItem("Nur überfällige", "overdue")
        self.filter.addItem("Noch nicht fällig", "due")
        self.filter.currentIndexChanged.connect(self.reload)
        lay.addLayout(hbox(self.search, self.filter))
        lay.addLayout(hbox(button("Zahlung erfassen", self.payment, primary=True), button("Mahnen", self.dunning),
                           button("Rechnung öffnen", self.pdf), button("Letzte Mahnung öffnen", self.last_dunning),
                           button("Zahlungen / Mahnungen anzeigen", self.history), None))
        self.table = make_table(["Rechnung", "Datum", "Kunde", "Fällig", "Tage überfällig", "Betrag", "Bezahlt",
                                 "Offen", "Mahnstufe"], stretch_col=2)
        self.table.doubleClicked.connect(self.payment)
        lay.addWidget(self.table)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        items = payments.open_items(self.ctx.con)
        q = self.search.text().strip().lower()
        if q:
            items = [d for d in items if q in " ".join(str(x or "") for x in (
                d["number"], d["first_name"], d["last_name"], d["c_company"], d.get("customer_no"))).lower()]
        mode = self.filter.currentData()
        if mode == "overdue":
            items = [d for d in items if d["days_overdue"] > 0]
        elif mode == "due":
            items = [d for d in items if d["days_overdue"] <= 0]
        self.table.setRowCount(0)
        total = overdue = credit = 0
        for d in items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            cust = repo.customer_display_name({"first_name": d["first_name"], "last_name": d["last_name"],
                                               "company": d["c_company"]})
            od = d["days_overdue"]
            color = "#C9392A" if od > 0 else None
            vals = [d["number"], fmt_date(d["doc_date"]), cust, fmt_date(d["due_date"]), od or "",
                    fmt_eur(d["total_gross_cents"]), fmt_eur(d["paid_cents"]), fmt_eur(d["open_cents"]),
                    payments.DUNNING_LABEL.get(d["dunning_level"] or 0, "")]
            for i, v in enumerate(vals):
                self.table.setItem(r, i, item(v, d["id"] if i == 0 else None, align_right=i in (4, 5, 6, 7),
                                              color=color if i in (3, 4) else None))
            if d["open_cents"] > 0:
                total += d["open_cents"]
            else:
                credit -= d["open_cents"]
            if od > 0:
                overdue += d["open_cents"]
        self.summary.setText(f"{len(items)} offene Posten · offen gesamt {fmt_eur(total)} · davon überfällig "
                             f"{fmt_eur(overdue)}" + (f" · zu erstatten/Guthaben {fmt_eur(credit)}" if credit else ""))

    def _sel(self):
        did = selected_data(self.table)
        if not did:
            QMessageBox.information(self, "Auswahl", "Bitte eine Rechnung auswählen.")
        return did

    def payment(self, *_):
        did = self._sel()
        if did:
            record_payment(self, self.ctx, did)

    def dunning(self):
        did = self._sel()
        if did:
            make_dunning(self, self.ctx, did)

    def pdf(self):
        did = self._sel()
        if did:
            d = documents.get_document(self.ctx.con, did)
            open_file(d.get("pdf_path") or d.get("import_file_path"))

    def last_dunning(self):
        did = self._sel()
        if did:
            hist = payments.dunning_history(self.ctx.con, did)
            if hist:
                open_file(hist[-1]["pdf_path"])
            else:
                QMessageBox.information(self, "Mahnungen", "Zu dieser Rechnung gibt es noch keine Mahnung.")

    def history(self):
        did = self._sel()
        if not did:
            return
        pays = payments.list_payments(self.ctx.con, did)
        duns = payments.dunning_history(self.ctx.con, did)
        txt = "Zahlungen:\n" + ("\n".join(f"  {fmt_date(p['pay_date'])}  {fmt_eur(p['amount_cents']):>14}  {p['method']} {p['note']}" for p in pays) or "  –")
        txt += "\n\nMahnungen:\n" + ("\n".join(f"  {fmt_date(m['dunning_date'])}  {payments.DUNNING_LABEL[m['level']]}  "
                                              f"Gebühr {fmt_eur(m['fee_cents'])}  neu fällig {fmt_date(m['new_due_date'])}" for m in duns) or "  –")
        QMessageBox.information(self, "Verlauf", txt)
