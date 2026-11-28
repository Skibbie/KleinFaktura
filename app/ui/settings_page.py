"""Einstellungen: Firma, Steuer, Belege, Nummernkreise, Mahnwesen, Backup, Schnittstellen."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QScrollArea, QTabWidget, QSpinBox, QVBoxLayout, QWidget)

from app import config
from app.db.database import PROFILES, apply_profile, get_setting, set_setting, transaction
from app.money import input_decimal
from app.services import audit, backup, kleinunternehmer, mail, numbering, placeholder_catalog, secret
from app.validation import company_problems, normalize_company
from app.ui import placeholders_ui, update_ui
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
    "ku.founding_year": ("Gründungsjahr", "year", 1900),
    "invoice.payment_days": ("Zahlungsziel (Tage)", "int", 0),
    "offer.valid_days": ("Angebote gültig (Tage)", "int", 0),
    "dunning.days": ("Neue Zahlungsfrist (Tage)", "int", 0),
    "dunning.fee_1": ("Gebühr Zahlungserinnerung €", "eur", 0),
    "dunning.fee_2": ("Gebühr 1. Mahnung €", "eur", 0),
    "dunning.fee_3": ("Gebühr 2. Mahnung €", "eur", 0),
    "expense.km_rate": ("Kilometerpauschale € je km", "eur", 0),
    "backup.keep_daily": ("Tägliche Sicherungen behalten", "int", 1),
    "backup.keep_monthly": ("Monatliche Sicherungen behalten", "int", 0),
}


class NumberFormatBox(QGroupBox):
    """Baukasten für einen Nummernkreis mit Vorschau der nächsten Nummer."""

    def __init__(self, ctx: Ctx, doc_type: str, title: str):
        super().__init__(title)
        self.ctx, self.doc_type = ctx, doc_type
        f = QFormLayout(self)
        self.prefix = QLineEdit()
        self.prefix.setMaxLength(10)
        self.prefix.setPlaceholderText("leer oder z.B. RE-")
        self.prefix.setToolTip("Steht vor der Nummer, z.B. RE- oder AN. Erlaubt: Buchstaben, Ziffern, - _ .")
        self.year = QComboBox()
        for label, v in [("4-stellig (2026)", 4), ("2-stellig (26)", 2), ("ohne Jahr", 0)]:
            self.year.addItem(label, v)
        self.month = QCheckBox("mit Monat (Zähler beginnt jeden Monat neu)")
        self.sep = QComboBox()
        for v, label in numbering.SEPARATORS.items():
            self.sep.addItem(label, v)
        self.sep.setToolTip("Steht zwischen Jahr und Monat bzw. Zähler")
        self.digits = QSpinBox()
        self.digits.setRange(numbering.MIN_DIGITS, numbering.MAX_DIGITS)
        self.digits.setToolTip("Mindestanzahl Stellen des Zählers, mit führenden Nullen aufgefüllt")
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.RichText)
        f.addRow("Präfix", self.prefix)
        f.addRow("Jahr", hbox(self.year, self.month, None))
        f.addRow("Trennzeichen", hbox(self.sep, QLabel("Stellen des Zählers"), self.digits, None))
        f.addRow("Nächste Nummer", self.preview)
        self.load()
        self.prefix.textChanged.connect(self._update)
        self.year.currentIndexChanged.connect(self._update)
        self.month.toggled.connect(self._update)
        self.sep.currentIndexChanged.connect(self._update)
        self.digits.valueChanged.connect(self._update)

    def load(self) -> None:
        fmt = numbering.get_format(self.ctx.con, self.doc_type)
        self.prefix.setText(fmt.prefix)
        self.year.setCurrentIndex(self.year.findData(fmt.year))
        self.month.setChecked(fmt.month)
        self.sep.setCurrentIndex(self.sep.findData(fmt.sep))
        self.digits.setValue(fmt.digits)
        self._update()

    def fmt(self) -> numbering.NumberFormat:
        """Eingestelltes Schema; ValueError mit verständlicher Meldung bei ungültigem Präfix."""
        year = self.year.currentData()
        return numbering.NumberFormat(prefix=self.prefix.text().strip(), year=year, month=self.month.isChecked() and year > 0,
                                      sep=self.sep.currentData(), digits=self.digits.value())

    def state(self) -> tuple:
        return (self.prefix.text(), self.year.currentIndex(), self.month.isChecked(), self.sep.currentIndex(),
                self.digits.value())

    def _update(self, *_):
        has_year = self.year.currentData() > 0
        if not has_year:
            self.month.setChecked(False)   # nicht angehakt stehen lassen, was nicht wirkt
        self.month.setEnabled(has_year)    # Monat ohne Jahr ergäbe sich wiederholende Nummern
        self.sep.setEnabled(has_year)
        try:
            fmt = self.fmt()
        except ValueError as e:
            self.preview.setText(f"<span style='color:#B00020'>{e}</span>")
            return
        nxt = numbering.peek_next(self.ctx.con, self.doc_type, fmt=fmt)
        self.preview.setText(f"<b>{nxt}</b> &nbsp;·&nbsp; Schema {fmt.pattern()} &nbsp;·&nbsp; {fmt.describe_reset()}")


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
                           ("ku.limit_curr", "KU-Grenze laufendes Jahr €"), ("ku.warn_percent", "Warnung ab % der Grenze"),
                           ("ku.founding_year", "Gründungsjahr")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        self.edits["ku.founding_year"].setPlaceholderText("leer lassen, wenn vor dem laufenden Jahr gegründet")
        self.edits["ku.founding_year"].setToolTip("Im Jahr der Gründung gilt die 25.000-€-Grenze schon für das "
                                                  "laufende Jahr, nicht erst ab dem Folgejahr.")
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
        hint = QLabel("Aufbau: Präfix + Jahr + Trennzeichen + Monat + Zähler. Stornorechnungen laufen im Rechnungskreis. "
                      "Ein geändertes Schema gilt für neu festgeschriebene Belege; vergebene Nummern bleiben, "
                      "doppelte Nummern sind ausgeschlossen.")
        hint.setWordWrap(True)
        f.addRow(hint)
        self.nr_formats = {"rechnung": NumberFormatBox(ctx, "rechnung", "Rechnungen"),
                           "angebot": NumberFormatBox(ctx, "angebot", "Angebote")}
        for box in self.nr_formats.values():
            f.addRow(box)
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
        self.nr_last.setRange(0, 10 ** numbering.MAX_DIGITS - 1)
        self.nr_year_label, self.nr_month_label = QLabel("Jahr"), QLabel("Monat")
        row = QHBoxLayout()
        for w in (self.nr_type, self.nr_year_label, self.nr_year, self.nr_month_label, self.nr_month,
                  QLabel("zuletzt vergebene Nr."), self.nr_last, button("Startwert setzen", self.set_start)):
            row.addWidget(w)
        row.addStretch(1)
        f.addRow("Startwert", row)
        lay.addWidget(g)
        # erst nach dem Einbau: setVisible an Widgets ohne Eltern öffnete sie kurz als eigene Fenster
        self.nr_type.currentIndexChanged.connect(self._update_start_fields)
        self._update_start_fields()

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
        ph = QLabel("Platzhalter wie {belegnr} oder {betrag} werden ersetzt – im Feld „{“ tippen öffnet eine Auswahl. "
                    "Die Mail wird nur vorbereitet – gesendet wird erst in Thunderbird.")
        ph.setWordWrap(True)
        f.addRow(hbox(ph, button("Platzhalter…", self.show_mail_placeholders,
                                 tooltip="Übersicht der Platzhalter für E-Mails – „Einfügen“ ins zuletzt benutzte Feld")))
        self._mail_target = self.texts["mail.body_rechnung"]
        for w in (self.edits["mail.subject_rechnung"], self.edits["mail.subject_angebot"],
                  self.texts["mail.body_rechnung"], self.texts["mail.body_angebot"]):
            placeholders_ui.attach(w, lambda: placeholder_catalog.for_context("mail"),
                                   on_focus=lambda edit: setattr(self, "_mail_target", edit))
        self.checks["pdf.export_auto"] = QCheckBox("PDF nach dem Festschreiben zusätzlich in diesen Ordner kopieren")
        self.checks["pdf.export_year_dirs"] = QCheckBox("Unterordner je Jahr anlegen")
        f.addRow("", self.checks["pdf.export_auto"])
        self.edits["pdf.export_dir"] = QLineEdit(get_setting(ctx.con, "pdf.export_dir"))
        f.addRow("PDF-Ordner", hbox(self.edits["pdf.export_dir"], button("…", self.pick_export_dir)))
        f.addRow("", self.checks["pdf.export_year_dirs"])
        for key, cb in self.checks.items():
            cb.setChecked(get_setting(ctx.con, key) == "1")
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
        g = QGroupBox("Datensicherung")
        f = QFormLayout(g)
        self.edits["backup.dir"] = QLineEdit(get_setting(ctx.con, "backup.dir"))
        f.addRow("Sicherungsordner", hbox(self.edits["backup.dir"], button("…", self.pick_backup_dir)))
        for key, label in [("backup.keep_daily", "Tägliche Sicherungen behalten"),
                           ("backup.keep_monthly", "Monatliche Sicherungen behalten")]:
            self.edits[key] = QLineEdit(get_setting(ctx.con, key))
            f.addRow(label, self.edits[key])
        self.checks["backup.encrypt"] = QCheckBox("Sicherungen mit Passwort verschlüsseln (AES-256)")
        self.checks["backup.encrypt"].setChecked(get_setting(ctx.con, "backup.encrypt") == "1")
        f.addRow("", self.checks["backup.encrypt"])
        self.edits["backup.password"] = QLineEdit(backup.password(ctx.con))
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
                self.girocode.isChecked(), self.zugferd.isChecked(), self.update_check.isChecked(),
                tuple(b.state() for b in self.nr_formats.values()))

    def _reload_values(self):
        for key, e in self.edits.items():
            e.setText(self._stored(key))
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
        for box in self.nr_formats.values():
            box.load()
        self._clean = self._snapshot()

    def _stored(self, key: str) -> str:
        """Gespeicherter Wert eines Eingabefelds; Geheimnisse liegen geschützt in der Datenbank (secret)."""
        value = get_setting(self.ctx.con, key)
        return secret.unprotect(value) if key in SECRET_KEYS else value

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

    def show_mail_placeholders(self):
        placeholders_ui.PlaceholderDialog(self, placeholder_catalog.for_context("mail"), self._mail_target,
                                          title="Platzhalter für E-Mails").exec()

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
            if kind == "year" and not text:
                out[key] = ""            # freiwillige Angabe
                continue
            try:
                d = input_decimal(text) if text else None
            except ValueError:
                d = None
            if d is None or d < minimum or (kind in ("int", "year") and d != d.to_integral_value()):
                need = "ganze Zahl" if kind in ("int", "year") else "Zahl"
                problems.append(f"{label}: „{text}“ ist keine gültige {need} (mindestens {minimum}).")
                continue
            if kind in ("int", "year"):
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

    def _company_ok(self) -> bool:
        """PLZ, IBAN, BIC und E-Mail prüfen; IBAN/BIC einheitlich schreiben."""
        values = {k: e.text() for k, e in self.edits.items() if k.startswith("company.")}
        problems = company_problems(values)
        if problems:
            QMessageBox.warning(self, "Einstellungen", "Bitte korrigieren:\n\n• " + "\n• ".join(problems))
            return False
        for key, v in normalize_company(values).items():
            if v != values[key]:
                self.edits[key].setText(v)
        return True

    def _number_format_values(self) -> list[tuple[str, str]] | None:
        """Nummernschemata prüfen und ein geändertes Schema bestätigen lassen. Rückgabe: Einstellungswerte oder None."""
        values, problems, changed = [], [], []
        for doc_type, box in self.nr_formats.items():
            try:
                fmt = box.fmt()
            except ValueError as e:
                problems.append(f"Nummernkreis {box.title()}: {e}")
                continue
            keys = numbering.setting_keys(doc_type)
            values += [(keys[k], v) for k, v in numbering.format_values(fmt).items()]
            if fmt != numbering.get_format(self.ctx.con, doc_type):
                changed.append(f"{box.title()}: {numbering.peek_next(self.ctx.con, doc_type)} → "
                               f"{numbering.peek_next(self.ctx.con, doc_type, fmt=fmt)} ({fmt.describe_reset()})")
        if problems:
            QMessageBox.warning(self, "Einstellungen", "Bitte korrigieren:\n\n• " + "\n• ".join(problems))
            return None
        if changed and not confirm(self, "Nummernkreise", "Neues Nummernschema für künftig festgeschriebene Belege:\n\n• "
                                   + "\n• ".join(changed) + "\n\nBereits vergebene Nummern bleiben unverändert. Übernehmen?"):
            return None
        return values

    def _update_start_fields(self, *_):
        """Nur die Felder zeigen, nach denen der Zähler des Kreises neu beginnt (Monat / Jahr / gar nicht)."""
        fmt = numbering.get_format(self.ctx.con, self.nr_type.currentData())
        for w in (self.nr_year_label, self.nr_year):
            w.setVisible(fmt.year > 0)
        for w in (self.nr_month_label, self.nr_month):
            w.setVisible(fmt.month)

    def save(self, quiet: bool = False) -> bool:
        numbers = self._normalized_numbers()
        if numbers is None or not self._folders_ok() or not self._company_ok():
            return False
        number_formats = self._number_format_values()
        if number_formats is None:
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
                if key in SECRET_KEYS:
                    # Passwort nie ins Protokoll und nur geschützt in die Datenbank; unverändert bleibt es stehen
                    if e.text() != self._stored(key) or not secret.is_protected(get_setting(self.ctx.con, key)):
                        if e.text() != self._stored(key):
                            changes[key] = "(geändert)"
                        set_setting(self.ctx.con, key, secret.protect(e.text()))
                    continue
                v = e.text().strip()
                if v != get_setting(self.ctx.con, key):
                    changes[key] = v
                set_setting(self.ctx.con, key, v)
            values = [(k, t.toPlainText().strip()) for k, t in self.texts.items()]
            values += [(k, "1" if c.isChecked() else "0") for k, c in self.checks.items()]
            values += number_formats
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
        for box in self.nr_formats.values():
            box.load()
        self._update_start_fields()
        self._clean = self._snapshot()
        self.ctx.changed()
        if not quiet:
            QMessageBox.information(self, "Einstellungen", "Gespeichert.")
        return True

    def set_start(self):
        t, v = self.nr_type.currentData(), self.nr_last.value()
        try:
            edited = self.nr_formats[t].fmt() != numbering.get_format(self.ctx.con, t)
        except ValueError:
            edited = True
        if edited:
            QMessageBox.information(self, "Startwert", "Das Nummernschema wurde geändert – bitte zuerst die "
                                                       "Einstellungen speichern, dann den Startwert setzen.")
            return
        fmt = numbering.get_format(self.ctx.con, t)
        on = date(self.nr_year.value(), self.nr_month.value() if fmt.month else 1, 1)
        nxt = fmt.render(on, v + 1)
        scope = {"monat": f" für {on.month:02d}/{on.year}", "jahr": f" für {on.year}", "nie": ""}[fmt.reset]
        if v < numbering.last_value(self.ctx.con, t, on):
            QMessageBox.information(self, "Startwert", f"Zuletzt vergeben{scope} ist bereits "
                                                       f"{numbering.last_value(self.ctx.con, t, on)} – der Zähler kann "
                                                       "nicht verringert werden.")
            return
        if not confirm(self, "Startwert", f"Zuletzt vergebene Nummer{scope} auf {v} setzen?\n"
                                          f"Nächste Nummer wird {nxt}.\n\nDer Zähler kann danach nicht verringert werden."):
            return
        try:
            with transaction(self.ctx.con):
                numbering.set_start(self.ctx.con, t, on, v)
                audit.log(self.ctx.con, "number_start", "number_range", None,
                          {"type": t, "period": fmt.period(on), "last": v})
        except Exception as e:  # noqa: BLE001
            error(self, "Startwert", e)
            return
        self.nr_formats[t].load()   # Vorschau „Nächste Nummer“ aktualisieren
        QMessageBox.information(self, "Startwert", f"Gesetzt. Nächste Nummer: {nxt}")
