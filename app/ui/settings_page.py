"""Einstellungen: Firma, Steuer, Belege, Nummernkreise, Mahnwesen, Backup, Schnittstellen."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from app import config
from app.db.database import PROFILES, apply_profile, get_setting, set_setting, transaction
from app.services import audit, numbering
from app.ui import update_ui
from app.ui.common import CANCEL, SAVE, Ctx, ask_save, button, confirm, error, hbox, page_title

COMPANY_FIELDS = [
    ("company.name", "Firmenname"), ("company.line1", "Absender Zeile 1"), ("company.owner", "Inhaber"),
    ("company.street", "Straße"), ("company.zip", "PLZ"), ("company.city", "Ort"), ("company.country", "Land (ISO)"),
    ("company.phone", "Telefon"), ("company.email", "E-Mail"), ("company.web", "Web"),
    ("company.tax_number", "Steuernummer"), ("company.vat_id", "USt-IdNr."), ("company.iban", "IBAN"),
    ("company.bic", "BIC"), ("company.bank", "Bank"), ("company.logo", "Logo-Datei (leer = Standard)"),
]


class SettingsPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.addWidget(page_title("Einstellungen"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 12, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.edits: dict[str, QLineEdit] = {}

        g = QGroupBox("Firma (Absender, Pflichtangaben § 14 UStG)")
        f = QFormLayout(g)
        for key, label in COMPANY_FIELDS:
            e = QLineEdit(get_setting(ctx.con, key))
            self.edits[key] = e
            if key == "company.logo":
                f.addRow(label, hbox(e, button("…", self.pick_logo)))
            else:
                f.addRow(label, e)
        lay.addWidget(g)

        g = QGroupBox("Branche")
        f = QFormLayout(g)
        self.profile = QComboBox()
        for key, p in PROFILES.items():
            self.profile.addItem(p.label, key)
        self.profile.setCurrentIndex(max(0, self.profile.findData(get_setting(ctx.con, "profile"))))
        self.profile.currentIndexChanged.connect(self._profile_changed)
        f.addRow("Branchenprofil", self.profile)
        hint = QLabel("Bestimmt die Startartikel und Fahrtanlässe (beim ersten Wählen ergänzt, beim Wechsel wird "
                      "nichts gelöscht). „Energieberatung“ schaltet zusätzlich den Kundenimport aus Hottgenroth frei.")
        hint.setWordWrap(True)
        f.addRow(hint)
        lay.addWidget(g)

        g = QGroupBox("Steuer")
        f = QFormLayout(g)
        self.tax_mode = QComboBox()
        self.tax_mode.addItem("Kleinunternehmer (§ 19 UStG) – keine Umsatzsteuer", "kleinunternehmer")
        self.tax_mode.addItem("Regelbesteuerung – Umsatzsteuer ausweisen", "regel")
        self.tax_mode.setCurrentIndex(0 if get_setting(ctx.con, "tax.mode") == "kleinunternehmer" else 1)
        f.addRow("Besteuerung (für neue Belege)", self.tax_mode)
        for key, label in [("tax.default_rate", "Standard-USt-Satz %"), ("ku.limit_prev", "KU-Grenze Vorjahr €"),
                           ("ku.limit_curr", "KU-Grenze laufendes Jahr €"), ("ku.warn_percent", "Warnung ab % der Grenze")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        self.ku_basis = QComboBox()
        self.ku_basis.addItem("Ist – vereinnahmte Entgelte (Zahlungseingänge)", "ist")
        self.ku_basis.addItem("Soll – berechnete Entgelte (Rechnungen)", "soll")
        self.ku_basis.setCurrentIndex(0 if get_setting(ctx.con, "ku.basis") == "ist" else 1)
        f.addRow("Umsatzermittlung", self.ku_basis)
        lay.addWidget(g)

        g = QGroupBox("Belege")
        f = QFormLayout(g)
        for key, label in [("invoice.payment_days", "Zahlungsziel (Tage)"), ("offer.valid_days", "Angebote gültig (Tage)")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        self.girocode = QCheckBox("GiroCode standardmäßig auf Rechnungen")
        self.girocode.setChecked(get_setting(ctx.con, "invoice.girocode") == "1")
        self.zugferd = QCheckBox("ZUGFeRD standardmäßig einbetten")
        self.zugferd.setChecked(get_setting(ctx.con, "invoice.zugferd_default") == "1")
        f.addRow("", self.girocode)
        f.addRow("", self.zugferd)
        lay.addWidget(g)

        g = QGroupBox("Nummernkreise")
        f = QFormLayout(g)
        f.addRow(QLabel("Rechnungen: JJJJ-MMNN (z.B. 2026-1001) · Angebote: ANJJJJ-MMNN (z.B. AN2026-1001) · "
                        "Zähler beginnt jeden Monat neu. Stornorechnungen laufen im Rechnungskreis."))
        f.addRow("Nächste Rechnungsnummer", QLabel(numbering.peek_next(ctx.con, "rechnung")))
        f.addRow("Nächste Angebotsnummer", QLabel(numbering.peek_next(ctx.con, "angebot")))
        self.nr_type = QComboBox()
        self.nr_type.addItem("Rechnungen", "rechnung")
        self.nr_type.addItem("Angebote", "angebot")
        self.nr_year = QSpinBox()
        self.nr_year.setRange(2000, 2100)
        self.nr_year.setValue(date.today().year)
        self.nr_month = QSpinBox()
        self.nr_month.setRange(1, 12)
        self.nr_month.setValue(date.today().month)
        self.nr_last = QSpinBox()
        self.nr_last.setRange(0, 9999)
        row = QHBoxLayout()
        for w in (self.nr_type, QLabel("Jahr"), self.nr_year, QLabel("Monat"), self.nr_month,
                  QLabel("zuletzt vergebene Nr."), self.nr_last, button("Startwert setzen", self.set_start)):
            row.addWidget(w)
        row.addStretch(1)
        f.addRow("Startwert", row)
        lay.addWidget(g)

        g = QGroupBox("Mahnwesen")
        f = QFormLayout(g)
        for key, label in [("dunning.days", "Neue Zahlungsfrist (Tage)"), ("dunning.fee_1", "Gebühr Zahlungserinnerung €"),
                           ("dunning.fee_2", "Gebühr 1. Mahnung €"), ("dunning.fee_3", "Gebühr 2. Mahnung €")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        lay.addWidget(g)

        g = QGroupBox("Fahrten")
        f = QFormLayout(g)
        self.edits["expense.km_rate"] = QLineEdit(get_setting(ctx.con, "expense.km_rate"))
        f.addRow("Kilometerpauschale € je km", self.edits["expense.km_rate"])
        f.addRow(QLabel("Gilt für neu erfasste Fahrten; bereits erfasste behalten ihren Satz."))
        lay.addWidget(g)

        g = QGroupBox("Datensicherung & Aufbewahrung")
        f = QFormLayout(g)
        self.edits["backup.dir"] = QLineEdit(get_setting(ctx.con, "backup.dir"))
        f.addRow("Sicherungsordner", hbox(self.edits["backup.dir"], button("…", self.pick_backup_dir)))
        for key, label in [("backup.keep_daily", "Tägliche Sicherungen behalten"),
                           ("backup.keep_monthly", "Monatliche Sicherungen behalten"),
                           ("retention.years", "Aufbewahrungsfrist (Jahre)")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        lay.addWidget(g)

        g = QGroupBox("Schnittstellen")
        f = QFormLayout(g)
        for key, label in [("datev.konto_bank", "DATEV Konto Bank"), ("datev.konto_erloes", "DATEV Erlöskonto"),
                           ("datev.berater", "DATEV Beraternummer"), ("datev.mandant", "DATEV Mandantennummer")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        lay.addWidget(g)

        self.hottgenroth_box = g = QGroupBox("Hottgenroth (Energieberatung)")
        f = QFormLayout(g)
        for key, label in [("hottgenroth.server", "SQL-Server"), ("hottgenroth.driver", "ODBC-Treiber")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        lay.addWidget(g)
        self._profile_changed()

        g = QGroupBox("Programm-Updates")
        f = QFormLayout(g)
        self.update_check = QCheckBox("Beim Start nach neuen Versionen suchen")
        self.update_check.setChecked(get_setting(ctx.con, update_ui.CHECK_SETTING, "1") == "1")
        f.addRow("", self.update_check)
        info = QLabel(f"Installiert: {config.APP_VERSION}. Neue Versionen kommen von GitHub "
                      f"({update_ui.release_page()}). Die vorherige Version bleibt als "
                      f"„{config.APP_NAME}.vorher“ neben dem Programmordner erhalten.")
        info.setWordWrap(True)
        f.addRow(info)
        f.addRow("", button("Jetzt nach Updates suchen", lambda: update_ui.check_now(self.window())))
        lay.addWidget(g)

        outer.addLayout(hbox(None, button("Einstellungen speichern", self.save, primary=True)))
        self._clean = self._snapshot()

    def _snapshot(self):
        return ({k: e.text() for k, e in self.edits.items()}, self.profile.currentIndex(), self.tax_mode.currentIndex(), self.ku_basis.currentIndex(),
                self.girocode.isChecked(), self.zugferd.isChecked(), self.update_check.isChecked())

    def _reload_values(self):
        for key, e in self.edits.items():
            e.setText(get_setting(self.ctx.con, key))
        self.profile.setCurrentIndex(max(0, self.profile.findData(get_setting(self.ctx.con, "profile"))))
        self.tax_mode.setCurrentIndex(0 if get_setting(self.ctx.con, "tax.mode") == "kleinunternehmer" else 1)
        self.ku_basis.setCurrentIndex(0 if get_setting(self.ctx.con, "ku.basis") == "ist" else 1)
        self.girocode.setChecked(get_setting(self.ctx.con, "invoice.girocode") == "1")
        self.zugferd.setChecked(get_setting(self.ctx.con, "invoice.zugferd_default") == "1")
        self.update_check.setChecked(get_setting(self.ctx.con, update_ui.CHECK_SETTING, "1") == "1")
        self._clean = self._snapshot()

    def confirm_leave(self) -> bool:
        if self._snapshot() == self._clean:
            return True
        r = ask_save(self, "Die Einstellungen wurden geändert.")
        if r == CANCEL:
            return False
        if r == SAVE:
            self.save(quiet=True)
        else:
            self._reload_values()
        return True

    def _profile_changed(self, *_):
        self.hottgenroth_box.setVisible(PROFILES[self.profile.currentData()].hottgenroth)

    def pick_logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Logo wählen", "", "Bilder (*.png *.jpg *.jpeg)")
        if p:
            self.edits["company.logo"].setText(p)

    def pick_backup_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Sicherungsordner wählen", self.edits["backup.dir"].text())
        if d:
            self.edits["backup.dir"].setText(d)

    def save(self, quiet: bool = False):
        with transaction(self.ctx.con):
            changes = {}
            for key, e in self.edits.items():
                v = e.text().strip()
                if v != get_setting(self.ctx.con, key):
                    changes[key] = v
                set_setting(self.ctx.con, key, v)
            for key, v in [("tax.mode", self.tax_mode.currentData()), ("ku.basis", self.ku_basis.currentData()),
                           ("invoice.girocode", "1" if self.girocode.isChecked() else "0"),
                           ("invoice.zugferd_default", "1" if self.zugferd.isChecked() else "0"),
                           (update_ui.CHECK_SETTING, "1" if self.update_check.isChecked() else "0")]:
                if v != get_setting(self.ctx.con, key):
                    changes[key] = v
                set_setting(self.ctx.con, key, v)
            if self.profile.currentData() != get_setting(self.ctx.con, "profile"):
                changes["profile"] = self.profile.currentData()
                apply_profile(self.ctx.con, self.profile.currentData())
            if changes:
                audit.log(self.ctx.con, "settings", "settings", None, changes)
        self._clean = self._snapshot()
        self.ctx.changed()
        if not quiet:
            QMessageBox.information(self, "Einstellungen", "Gespeichert.")

    def set_start(self):
        t, y, m, v = self.nr_type.currentData(), self.nr_year.value(), self.nr_month.value(), self.nr_last.value()
        prefix = get_setting(self.ctx.con, "number.offer_prefix" if t == "angebot" else "number.invoice_prefix")
        nxt = numbering.format_number(prefix, y, m, v + 1)
        if not confirm(self, "Startwert", f"Zuletzt vergebene Nummer für {m:02d}/{y} auf {v} setzen?\n"
                                          f"Nächste Nummer wird {nxt}.\n\nDer Zähler kann danach nicht verringert werden."):
            return
        try:
            with transaction(self.ctx.con):
                numbering.set_start(self.ctx.con, t, y, m, v)
                audit.log(self.ctx.con, "number_start", "number_range", None, {"type": t, "period": f"{y}{m:02d}", "last": v})
        except Exception as e:  # noqa: BLE001
            error(self, "Startwert", e)
            return
        QMessageBox.information(self, "Startwert", f"Gesetzt. Nächste Nummer: {nxt}")
