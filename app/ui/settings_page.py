"""Einstellungen: Firma, Steuer, Belege, Nummernkreise, Mahnwesen, Backup, Schnittstellen."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QScrollArea, QTabWidget, QSpinBox, QVBoxLayout, QWidget)

from app import config
from app.db.database import PROFILES, apply_profile, get_setting, set_setting, transaction
from app.money import input_decimal
from app.services import audit, kleinunternehmer, mail, numbering
from app.ui import update_ui
from app.ui.common import CANCEL, SAVE, Ctx, ask_save, button, confirm, error, hbox, page_title

COMPANY_FIELDS = [
    ("company.name", "Firmenname"), ("company.line1", "Zusatzzeile im Briefkopf (z.B. Tätigkeit)"), ("company.owner", "Inhaber"),
    ("company.street", "Straße"), ("company.zip", "PLZ"), ("company.city", "Ort"), ("company.country", "Land (ISO)"),
    ("company.phone", "Telefon"), ("company.email", "E-Mail"), ("company.web", "Web"),
    ("company.tax_number", "Steuernummer"), ("company.vat_id", "USt-IdNr."), ("company.iban", "IBAN"),
    ("company.bic", "BIC"), ("company.bank", "Bank"), ("company.logo", "Logo-Datei (leer = kein Logo)"),
]

# Zahlenfelder: (Bezeichnung, int/dec/eur, Mindestwert) – werden beim Speichern geprüft und einheitlich geschrieben
NUMERIC_FIELDS = {
    "tax.default_rate": ("Standard-USt-Satz %", "dec", 0),
    "ku.limit_prev": ("KU-Grenze Vorjahr €", "eur", 1),
    "ku.limit_curr": ("KU-Grenze laufendes Jahr €", "eur", 1),
    "ku.warn_percent": ("Warnung ab % der Grenze", "int", 1),
    "invoice.payment_days": ("Zahlungsziel (Tage)", "int", 0),
    "offer.valid_days": ("Angebote gültig (Tage)", "int", 0),
    "dunning.days": ("Neue Zahlungsfrist (Tage)", "int", 0),
    "dunning.fee_1": ("Gebühr Zahlungserinnerung €", "eur", 0),
    "dunning.fee_2": ("Gebühr 1. Mahnung €", "eur", 0),
    "dunning.fee_3": ("Gebühr 2. Mahnung €", "eur", 0),
    "expense.km_rate": ("Kilometerpauschale € je km", "eur", 0),
    "backup.keep_daily": ("Tägliche Sicherungen behalten", "int", 1),
    "backup.keep_monthly": ("Monatliche Sicherungen behalten", "int", 0),
    "retention.years": ("Aufbewahrungsfrist (Jahre)", "int", 1),
}


SECRET_KEYS = {"backup.password"}
CLOUD_FOLDERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "hidrive", "magentacloud", "nextcloud")


class SettingsPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.addWidget(page_title("Einstellungen"))
        # Reiter statt einer langen Seite: jeder Bereich ist mit einem Klick erreichbar
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        self._tab_layouts: list[QVBoxLayout] = []
        self.edits: dict[str, QLineEdit] = {}
        self.texts: dict[str, QPlainTextEdit] = {}
        self.checks: dict[str, QCheckBox] = {}

        lay = self._tab("Firma")
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

        lay = self._tab("Steuer und Belege")
        g = QGroupBox("Steuer")
        f = QFormLayout(g)
        self.tax_mode = QComboBox()
        self.tax_mode.addItem("Kleinunternehmer (§ 19 UStG) – keine Umsatzsteuer", "kleinunternehmer")
        self.tax_mode.addItem(kleinunternehmer.REGEL_LABEL, "regel")
        self.tax_mode.setCurrentIndex(0 if get_setting(ctx.con, "tax.mode") == "kleinunternehmer" else 1)
        f.addRow("Besteuerung (für neue Belege)", self.tax_mode)
        self.regel_hint = QLabel(kleinunternehmer.REGEL_HINWEIS)
        self.regel_hint.setWordWrap(True)
        self.regel_hint.setObjectName("warn")
        f.addRow(self.regel_hint)
        self.tax_mode.currentIndexChanged.connect(lambda *_: self.regel_hint.setVisible(self.tax_mode.currentData() == "regel"))
        self.regel_hint.setVisible(self.tax_mode.currentData() == "regel")
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

        lay = self._tab("E-Mail und PDF")
        g = QGroupBox("E-Mail && PDF-Ablage")   # && = sichtbares &
        f = QFormLayout(g)
        self.edits["mail.thunderbird_path"] = QLineEdit(get_setting(ctx.con, "mail.thunderbird_path"))
        found = mail.find_thunderbird(ctx.con) if not get_setting(ctx.con, "mail.thunderbird_path") else None
        self.edits["mail.thunderbird_path"].setPlaceholderText(f"automatisch: {found}" if found else "nicht gefunden – bitte wählen")
        f.addRow("Thunderbird", hbox(self.edits["mail.thunderbird_path"], button("…", self.pick_thunderbird)))
        for key, label in [("mail.subject_rechnung", "Betreff Rechnung"), ("mail.subject_angebot", "Betreff Angebot")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        for key, label in [("mail.body_rechnung", "Text Rechnung"), ("mail.body_angebot", "Text Angebot")]:
            t = QPlainTextEdit(get_setting(ctx.con, key))
            t.setFixedHeight(150)
            self.texts[key] = t
            f.addRow(label, t)
        ph = QLabel("Platzhalter: {briefanrede} {belegart} {belegnr} {datum} {betrag} {faellig} {gueltig_bis} "
                    "{betreff} {projekt} {objekt_adresse} {kunde} {inhaber} {firma}. Die Mail wird nur vorbereitet – "
                    "gesendet wird erst in Thunderbird.")
        ph.setWordWrap(True)
        f.addRow(ph)
        self.checks["pdf.export_auto"] = QCheckBox("PDF nach dem Festschreiben zusätzlich in diesen Ordner kopieren")
        self.checks["pdf.export_year_dirs"] = QCheckBox("Unterordner je Jahr anlegen")
        f.addRow("", self.checks["pdf.export_auto"])
        self.edits["pdf.export_dir"] = QLineEdit(get_setting(ctx.con, "pdf.export_dir"))
        f.addRow("PDF-Ordner", hbox(self.edits["pdf.export_dir"], button("…", self.pick_export_dir)))
        f.addRow("", self.checks["pdf.export_year_dirs"])
        for key, cb in self.checks.items():
            cb.setChecked(get_setting(ctx.con, key) == "1")
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

        lay = self._tab("Datensicherung")
        g = QGroupBox("Datensicherung && Aufbewahrung")
        f = QFormLayout(g)
        self.edits["backup.dir"] = QLineEdit(get_setting(ctx.con, "backup.dir"))
        f.addRow("Sicherungsordner", hbox(self.edits["backup.dir"], button("…", self.pick_backup_dir)))
        for key, label in [("backup.keep_daily", "Tägliche Sicherungen behalten"),
                           ("backup.keep_monthly", "Monatliche Sicherungen behalten"),
                           ("retention.years", "Aufbewahrungsfrist (Jahre)")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        self.checks["backup.encrypt"] = QCheckBox("Sicherungen mit Passwort verschlüsseln (AES-256)")
        self.checks["backup.encrypt"].setChecked(get_setting(ctx.con, "backup.encrypt") == "1")
        f.addRow("", self.checks["backup.encrypt"])
        self.edits["backup.password"] = QLineEdit(get_setting(ctx.con, "backup.password"))
        self.edits["backup.password"].setEchoMode(QLineEdit.Password)
        self.edits["backup.password"].setPlaceholderText("mindestens 8 Zeichen – bitte zusätzlich sicher notieren")
        f.addRow("Passwort der Sicherung", self.edits["backup.password"])
        enc_hint = QLabel("Ohne dieses Passwort lässt sich eine verschlüsselte Sicherung nicht wiederherstellen – "
                          "etwa nach einem Festplattenschaden. Das Passwort bitte getrennt vom PC aufbewahren.")
        enc_hint.setWordWrap(True)
        enc_hint.setObjectName("muted")
        f.addRow(enc_hint)
        self.cloud_hint = QLabel()
        self.cloud_hint.setWordWrap(True)
        self.cloud_hint.setObjectName("warn")
        f.addRow(self.cloud_hint)
        for w in (self.edits["backup.dir"], self.checks["backup.encrypt"]):
            (w.textChanged if isinstance(w, QLineEdit) else w.toggled).connect(self._update_cloud_hint)
        self._update_cloud_hint()
        lay.addWidget(g)

        lay = self._tab("Schnittstellen und Updates")
        g = QGroupBox("Schnittstellen")
        f = QFormLayout(g)
        for key, label in [("datev.konto_bank", "DATEV Konto Bank"), ("datev.konto_erloes", "DATEV Erlöskonto"),
                           ("datev.konto_sonstige", "DATEV Konto Mahngebühren / Überzahlung"),
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

        lay = self._tab("Hinweise")
        g = QGroupBox("Wichtiger Hinweis")
        f = QVBoxLayout(g)
        self.disclaimer = QLabel(config.DISCLAIMER)
        self.disclaimer.setWordWrap(True)
        f.addWidget(self.disclaimer)
        about = QLabel(f"{config.APP_NAME} {config.APP_VERSION} · MIT-Lizenz · {update_ui.release_page()}")
        about.setObjectName("muted")
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f.addWidget(about)
        lay.addWidget(g)

        for tab_lay in self._tab_layouts:
            tab_lay.addStretch(1)
        outer.addLayout(hbox(None, button("Einstellungen speichern", self.save, primary=True)))
        self._clean = self._snapshot()

    def _snapshot(self):
        return ({k: e.text() for k, e in self.edits.items()}, {k: t.toPlainText() for k, t in self.texts.items()},
                {k: c.isChecked() for k, c in self.checks.items()}, self.profile.currentIndex(), self.tax_mode.currentIndex(), self.ku_basis.currentIndex(),
                self.girocode.isChecked(), self.zugferd.isChecked(), self.update_check.isChecked())

    def _reload_values(self):
        for key, e in self.edits.items():
            e.setText(get_setting(self.ctx.con, key))
        for key, t in self.texts.items():
            t.setPlainText(get_setting(self.ctx.con, key))
        for key, c in self.checks.items():
            c.setChecked(get_setting(self.ctx.con, key) == "1")
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
            if not self.save(quiet=True):
                return False
        else:
            self._reload_values()
        return True

    def _profile_changed(self, *_):
        self.hottgenroth_box.setVisible(PROFILES[self.profile.currentData()].hottgenroth)

    def pick_logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Logo wählen", "", "Bilder (*.png *.jpg *.jpeg)")
        if p:
            try:
                from app.ui.setup_wizard import copy_logo_to_data_dir
                self.edits["company.logo"].setText(str(copy_logo_to_data_dir(p)))
            except OSError as e:
                error(self, "Logo", e)

    def pick_thunderbird(self):
        p, _ = QFileDialog.getOpenFileName(self, "thunderbird.exe wählen", self.edits["mail.thunderbird_path"].text(),
                                           "Programme (*.exe)")
        if p:
            self.edits["mail.thunderbird_path"].setText(p)

    def pick_export_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner für PDF-Kopien wählen", self.edits["pdf.export_dir"].text())
        if d:
            self.edits["pdf.export_dir"].setText(d)

    def pick_backup_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Sicherungsordner wählen", self.edits["backup.dir"].text())
        if d:
            self.edits["backup.dir"].setText(d)

    def _tab(self, title: str) -> QVBoxLayout:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 8, 12, 0)
        scroll.setWidget(body)
        self.tabs.addTab(scroll, title)
        self._tab_layouts.append(lay)
        return lay

    def _update_cloud_hint(self, *_):
        """Hinweis, wenn der Sicherungsordner von einem Cloud-Dienst synchronisiert wird und nicht verschlüsselt ist."""
        path = self.edits["backup.dir"].text().lower()
        cloud = next((n for n in CLOUD_FOLDERS if n in path), None)
        self.cloud_hint.setText(f"Der Sicherungsordner liegt in „{cloud}“ und wird in die Cloud übertragen. Sicherungen "
                                "enthalten alle Kunden-, Bank- und Belegdaten – Verschlüsselung wird empfohlen."
                                if cloud else "")
        self.cloud_hint.setVisible(bool(cloud) and not self.checks["backup.encrypt"].isChecked())

    def _normalized_numbers(self) -> dict[str, str] | None:
        """Prüft die Zahlenfelder; Rückgabe der einheitlich geschriebenen Werte oder None (Meldung gezeigt)."""
        out, problems = {}, []
        for key, (label, kind, minimum) in NUMERIC_FIELDS.items():
            if key not in self.edits:
                continue
            text = self.edits[key].text().strip()
            try:
                d = input_decimal(text) if text else None
            except ValueError:
                d = None
            if d is None or d < minimum or (kind == "int" and d != d.to_integral_value()):
                need = "ganze Zahl" if kind == "int" else "Zahl"
                problems.append(f"{label}: „{text}“ ist keine gültige {need} (mindestens {minimum}).")
                continue
            if kind == "int":
                out[key] = str(int(d))
            elif kind == "eur":
                out[key] = f"{d:.2f}".replace(".", ",")
            else:
                out[key] = format(d.normalize(), "f").replace(".", ",")
        if problems:
            QMessageBox.warning(self, "Einstellungen", "Bitte korrigieren:\n\n• " + "\n• ".join(problems))
            return None
        return out

    def _folders_ok(self) -> bool:
        """PDF-Kopien und Sicherungen dürfen nicht im Beleg-Archiv landen (Schreibschutz, wachsende Sicherungen)."""
        bad = [label for key, label in [("pdf.export_dir", "PDF-Ordner"), ("backup.dir", "Sicherungsordner")]
               if self.edits[key].text().strip() and config.inside_archive(self.edits[key].text().strip())]
        if bad:
            QMessageBox.warning(self, "Einstellungen", f"{' und '.join(bad)} darf nicht im Beleg-Archiv liegen "
                                                       f"({config.archive_dir()}). Bitte einen anderen Ordner wählen.")
        return not bad

    def save(self, quiet: bool = False) -> bool:
        numbers = self._normalized_numbers()
        if numbers is None or not self._folders_ok():
            return False
        if self.checks["backup.encrypt"].isChecked() and len(self.edits["backup.password"].text()) < 8:
            QMessageBox.warning(self, "Einstellungen", "Für verschlüsselte Sicherungen bitte ein Passwort mit "
                                                       "mindestens 8 Zeichen eintragen.")
            return False
        for key, v in numbers.items():
            self.edits[key].setText(v)
        with transaction(self.ctx.con):
            changes = {}
            for key, e in self.edits.items():
                v = e.text() if key in SECRET_KEYS else e.text().strip()
                if v != get_setting(self.ctx.con, key):
                    changes[key] = "(geändert)" if key in SECRET_KEYS else v   # Passwort nie ins Protokoll
                set_setting(self.ctx.con, key, v)
            values = [(k, t.toPlainText().strip()) for k, t in self.texts.items()]
            values += [(k, "1" if c.isChecked() else "0") for k, c in self.checks.items()]
            for key, v in values + [("tax.mode", self.tax_mode.currentData()), ("ku.basis", self.ku_basis.currentData()),
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
        return True

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
