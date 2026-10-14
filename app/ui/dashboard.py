"""Übersicht: Forderungen, Kleinunternehmergrenzen als Effizienzskala, Überfälliges, Datensicherung."""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.db.database import get_setting, profile
from app.money import fmt_date, fmt_eur
from app.services import kleinunternehmer, payments
from app.ui import theme
from app.ui.common import Ctx, button, item, make_table, page_title
from app.ui.widgets import EnergyScale

GERMAN_DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
GERMAN_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                 "November", "Dezember"]


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionTitle")
    return lbl


class Dashboard(QWidget):
    def __init__(self, ctx: Ctx, navigate):
        super().__init__()
        self.ctx, self.navigate = ctx, navigate
        lay = QVBoxLayout(self)
        lay.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(page_title("Übersicht"))
        head.addStretch(1)
        t = date.today()
        today = QLabel(f"{GERMAN_DAYS[t.weekday()]}, {t.day}. {GERMAN_MONTHS[t.month - 1]} {t.year}")
        today.setObjectName("muted")
        head.addWidget(today, alignment=Qt.AlignVCenter)
        lay.addLayout(head)

        actions = QHBoxLayout()
        actions.addWidget(button("Rechnung schreiben", lambda: navigate("new_invoice"), primary=True))
        actions.addWidget(button("Angebot schreiben", lambda: navigate("new_offer")))
        self.hottgenroth_btn = button("Kunden aus Hottgenroth holen", lambda: navigate("hottgenroth"))
        actions.addWidget(self.hottgenroth_btn)
        actions.addStretch(1)
        lay.addLayout(actions)

        # Kennzahlen als eine Leiste mit Trennlinien
        strip = QFrame()
        strip.setObjectName("card")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.k_open, self.k_overdue, self.k_rev, self.k_drafts = [QLabel("–") for _ in range(4)]
        labels = ["Offene Forderungen", "Davon überfällig", f"Eingegangen {t.year}", "Entwürfe in Arbeit"]
        for i, (val, lab) in enumerate(zip([self.k_open, self.k_overdue, self.k_rev, self.k_drafts], labels)):
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(18, 14, 18, 14)
            l = QLabel(lab)
            l.setObjectName("kpiLabel")
            val.setObjectName("kpi")
            cl.addWidget(l)
            cl.addWidget(val)
            sl.addWidget(cell, 1)
            if i < 3:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet(f"color: {theme.LINE_SOFT}; background: {theme.LINE_SOFT}; max-width: 1px;")
                sl.addWidget(sep)
        lay.addWidget(strip)

        # Kleinunternehmergrenzen
        ku = QFrame()
        ku.setObjectName("card")
        kl = QGridLayout(ku)
        kl.setContentsMargins(18, 14, 18, 14)
        kl.setHorizontalSpacing(28)
        kl.addWidget(_section("Kleinunternehmergrenze (§ 19 UStG)"), 0, 0, 1, 2)
        self.ku_prev_lbl, self.ku_curr_lbl = QLabel(), QLabel()
        self.ku_prev_lbl.setObjectName("muted")
        self.ku_curr_lbl.setObjectName("muted")
        self.ku_prev, self.ku_curr = EnergyScale(), EnergyScale()
        kl.addWidget(self.ku_prev_lbl, 1, 0)
        kl.addWidget(self.ku_curr_lbl, 1, 1)
        kl.addWidget(self.ku_prev, 2, 0)
        kl.addWidget(self.ku_curr, 2, 1)
        self.ku_warn = QLabel()
        self.ku_warn.setWordWrap(True)
        kl.addWidget(self.ku_warn, 3, 0, 1, 2)
        lay.addWidget(ku)

        row = QHBoxLayout()
        row.addWidget(_section("Überfällige Rechnungen"))
        row.addStretch(1)
        row.addWidget(button("Alle offenen Posten", lambda: navigate("open_items")))
        lay.addLayout(row)
        self.overdue = make_table(["Rechnung", "Kunde", "Fällig seit", "Tage", "Offen", "Mahnstufe"], stretch_col=1)
        self.overdue.setMinimumHeight(170)
        self.overdue.doubleClicked.connect(lambda *_: navigate("open_items"))
        lay.addWidget(self.overdue)
        self.empty = QLabel("Keine überfälligen Rechnungen.")
        self.empty.setObjectName("muted")
        lay.addWidget(self.empty)
        self.credit_lbl = QLabel()
        self.credit_lbl.setObjectName("warn")
        self.credit_lbl.setWordWrap(True)
        lay.addWidget(self.credit_lbl)
        self.backup_lbl = QLabel()
        self.backup_lbl.setWordWrap(True)
        lay.addWidget(self.backup_lbl)
        lay.addStretch(1)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        self.hottgenroth_btn.setVisible(profile(self.ctx.con).hottgenroth)
        con = self.ctx.con
        items = payments.open_items(con)
        od = [d for d in items if d["days_overdue"] > 0]
        # Forderungen und Guthaben (Überzahlung, Erstattung nach Storno) nicht gegeneinander verrechnen
        self.k_open.setText(fmt_eur(sum(d["open_cents"] for d in items if d["open_cents"] > 0)))
        credit = -sum(d["open_cents"] for d in items if d["open_cents"] < 0)
        self.credit_lbl.setText(f"Zu erstatten bzw. Guthaben von Kunden (Überzahlung, Storno): {fmt_eur(credit)} – "
                                "siehe Offene Posten." if credit else "")
        self.credit_lbl.setVisible(bool(credit))
        self.k_overdue.setText(fmt_eur(sum(d["open_cents"] for d in od)))
        self.k_overdue.setStyleSheet(f"color: {theme.SCALE[7]};" if od else "")
        y = date.today().year
        rev = con.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payment WHERE substr(pay_date,1,4)=?", (str(y),)).fetchone()[0]
        self.k_rev.setText(fmt_eur(rev))
        self.k_drafts.setText(str(con.execute("SELECT COUNT(*) FROM document WHERE status='entwurf'").fetchone()[0]))

        st = kleinunternehmer.status(con)
        basis = "Zahlungseingänge" if st.basis == "ist" else "berechnete Umsätze"
        self.ku_prev_lbl.setText(f"Vorjahr {y - 1}, {basis}")
        self.ku_curr_lbl.setText(f"{y} bis heute – entscheidet über {y + 1}")
        self.ku_prev.set_values(st.prev_year_cents, st.limit_prev_cents,
                                "Grenze überschritten" if st.prev_ratio > 1 else "")
        self.ku_curr.set_values(st.curr_year_cents, st.limit_prev_cents,
                                f"Sofortgrenze im laufenden Jahr: {fmt_eur(st.limit_curr_cents).replace(',00 €', ' €')}")
        warns = st.warnings()
        if get_setting(con, "tax.mode") != "kleinunternehmer":
            self.ku_warn.setObjectName("ok")
            self.ku_warn.setText("Regelbesteuerung ist eingestellt (eingeschränkt unterstützt, siehe Einstellungen → "
                                 "Steuer). Die Grenzen sind hier nur zur Information.")
        elif warns:
            self.ku_warn.setObjectName("warn")
            self.ku_warn.setText("<br>".join(warns))
        else:
            self.ku_warn.setObjectName("ok")
            self.ku_warn.setText("Beide Grenzen sind eingehalten. Rechnungen werden ohne Umsatzsteuer gestellt.")
        self.ku_warn.style().unpolish(self.ku_warn)
        self.ku_warn.style().polish(self.ku_warn)

        self.overdue.setRowCount(0)
        for d in od[:20]:
            r = self.overdue.rowCount()
            self.overdue.insertRow(r)
            name = d["c_company"] or " ".join(x for x in [d["first_name"], d["last_name"]] if x)
            days_col = theme.scale_color(min(d["days_overdue"] / 60, 1.0) * 0.55 + 0.45)
            for i, v in enumerate([d["number"], name, fmt_date(d["due_date"]), d["days_overdue"], fmt_eur(d["open_cents"]),
                                   payments.DUNNING_LABEL.get(d["dunning_level"] or 0, "–")]):
                self.overdue.setItem(r, i, item(v, d["id"] if i == 0 else None, align_right=i in (3, 4),
                                                color=days_col if i == 3 else None))
        self.overdue.setVisible(bool(od))
        self.empty.setVisible(not od)

        last = get_setting(con, "backup.last")
        if last:
            dt = datetime.fromisoformat(last)
            age = (datetime.now() - dt).days
            self.backup_lbl.setObjectName("warn" if age > 7 else "muted")
            self.backup_lbl.setText(f"Letzte Datensicherung am {dt:%d.%m.%Y um %H:%M} Uhr."
                                    + (" Bitte neu sichern (Export & Sicherung)." if age > 7 else ""))
        else:
            self.backup_lbl.setObjectName("warn")
            self.backup_lbl.setText("Noch keine Datensicherung. Sie wird beim Beenden automatisch erstellt "
                                    "oder unter Export & Sicherung.")
        self.backup_lbl.style().unpolish(self.backup_lbl)
        self.backup_lbl.style().polish(self.backup_lbl)
