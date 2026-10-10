"""Kleinunternehmer-Wächter (§ 19 UStG, Fassung ab 01.01.2025).

- Vorjahresumsatz max. 25.000 € (netto) und
- Umsatz im laufenden Jahr max. 100.000 € – wird diese Grenze im laufenden Jahr überschritten,
  entfällt die Steuerbefreiung SOFORT ab dem Umsatz, mit dem die Grenze überschritten wird.
Maßgeblich ist der Gesamtumsatz nach vereinnahmten Entgelten (Ist), umschaltbar auf berechnete (Soll).
Hinweis: keine Steuerberatung – im Zweifel Steuerberater fragen.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from app.db.database import get_setting
from app.money import fmt_eur, to_cents


@dataclass
class KUStatus:
    year: int
    basis: str
    prev_year_cents: int
    curr_year_cents: int
    limit_prev_cents: int
    limit_curr_cents: int
    warn_percent: int

    @property
    def prev_ratio(self) -> float:
        return self.prev_year_cents / self.limit_prev_cents if self.limit_prev_cents else 0

    @property
    def curr_ratio(self) -> float:
        return self.curr_year_cents / self.limit_curr_cents if self.limit_curr_cents else 0

    @property
    def eligible_this_year(self) -> bool:
        return self.prev_year_cents <= self.limit_prev_cents

    def warnings(self) -> list[str]:
        w = []
        if not self.eligible_this_year:
            w.append(f"Vorjahresumsatz {self.year - 1} über {self.limit_prev_cents // 100:,} € – "
                     f"Kleinunternehmerregelung ist {self.year} nicht anwendbar!".replace(",", "."))
        if self.curr_ratio >= 1:
            w.append(f"Umsatzgrenze {self.limit_curr_cents // 100:,} € im laufenden Jahr überschritten – "
                     "ab sofort Regelbesteuerung!".replace(",", "."))
        elif self.curr_ratio * 100 >= self.warn_percent:
            w.append(f"Umsatz {self.year} hat {self.curr_ratio:.0%} der Grenze von "
                     f"{self.limit_curr_cents // 100:,} € erreicht.".replace(",", "."))
        # Prognose für das Folgejahr: Umsatz dieses Jahres ist der Vorjahresumsatz von nächstem Jahr
        if self.curr_year_cents > self.limit_prev_cents:
            w.append(f"Umsatz {self.year} liegt über {self.limit_prev_cents // 100:,} € – "
                     f"ab {self.year + 1} ist die Kleinunternehmerregelung nicht mehr möglich.".replace(",", "."))
        elif self.curr_year_cents * 100 >= self.limit_prev_cents * self.warn_percent:
            w.append(f"Umsatz {self.year} hat {self.curr_year_cents / self.limit_prev_cents:.0%} der "
                     f"25.000-€-Grenze erreicht (relevant für {self.year + 1}).")
        return w


def revenue_cents(con: sqlite3.Connection, year: int, basis: str) -> int:
    y = f"{year:04d}"
    if basis == "soll":
        # berechnete Entgelte: Rechnungen + Stornos (negativ), netto
        row = con.execute(
            "SELECT COALESCE(SUM(total_net_cents),0) FROM document WHERE status='festgeschrieben' "
            "AND doc_type IN ('rechnung','storno') AND substr(doc_date,1,4)=?", (y,)).fetchone()
        return int(row[0])
    # vereinnahmte Entgelte: Zahlungseingänge im Jahr (bei Kleinunternehmer brutto = netto)
    row = con.execute(
        "SELECT COALESCE(SUM(p.amount_cents),0) FROM payment p JOIN document d ON d.id=p.document_id "
        "WHERE substr(p.pay_date,1,4)=?", (y,)).fetchone()
    return int(row[0])


def status(con: sqlite3.Connection, year: int | None = None) -> KUStatus:
    year = year or date.today().year
    basis = get_setting(con, "ku.basis", "ist")
    return KUStatus(
        year=year,
        basis=basis,
        prev_year_cents=revenue_cents(con, year - 1, basis),
        curr_year_cents=revenue_cents(con, year, basis),
        limit_prev_cents=to_cents(get_setting(con, "ku.limit_prev", "25000")),
        limit_curr_cents=to_cents(get_setting(con, "ku.limit_curr", "100000")),
        warn_percent=int(get_setting(con, "ku.warn_percent", "80")),
    )


def check_before_finalize(con: sqlite3.Connection, doc: dict) -> list[str]:
    """Warnungen, bevor eine Rechnung als Kleinunternehmer festgeschrieben wird."""
    if doc.get("tax_mode") != "kleinunternehmer" or doc.get("doc_type") != "rechnung":
        return []
    year = int((doc.get("doc_date") or date.today().isoformat())[:4])
    st = status(con, year)
    msgs = []
    if not st.eligible_this_year:
        msgs.append(f"Der Vorjahresumsatz ({fmt_eur(st.prev_year_cents)}) liegt über der Grenze von "
                    f"{fmt_eur(st.limit_prev_cents)}. Für {year} darf die Kleinunternehmerregelung "
                    "nicht angewendet werden.")
    # Für die Grenze im laufenden Jahr zählen wir konservativ alle berechneten Umsätze (Soll) + diese Rechnung,
    # da offene Rechnungen im Laufe des Jahres vereinnahmt werden.
    soll = revenue_cents(con, year, "soll")
    projected = max(soll, st.curr_year_cents) + int(doc.get("total_net_cents") or 0)
    if projected > st.limit_curr_cents:
        msgs.append(f"Mit dieser Rechnung würde der Umsatz {year} ({fmt_eur(projected)}) die Grenze von "
                    f"{fmt_eur(st.limit_curr_cents)} überschreiten. Die Steuerbefreiung entfällt ab diesem "
                    "Umsatz – Rechnung mit Umsatzsteuer ausstellen (Einstellungen → Steuer).")
    return msgs
