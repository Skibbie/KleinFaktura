"""Einrichtungsassistent beim ersten Start: Firmendaten, Branche, Besteuerung, Logo und Sicherungsordner."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFormLayout, QLabel, QLineEdit, QRadioButton,
                               QVBoxLayout, QWizard, QWizardPage)

from app import config
from app.db.database import PROFILES, apply_profile, get_setting, set_setting, transaction
from app.services import audit
from app.ui.common import button, hbox

DONE_SETTING = "setup.done"

# Pflichtfelder für Belege nach § 14 UStG (Name, Anschrift, Steuernummer); IBAN für Zahlungshinweis und GiroCode
COMPANY_PAGE = [
    ("company.name", "Firmenname *", True), ("company.owner", "Inhaber *", True),
    ("company.street", "Straße *", True), ("company.zip", "PLZ *", True), ("company.city", "Ort *", True),
    ("company.tax_number", "Steuernummer *", True), ("company.vat_id", "USt-IdNr.", False),
    ("company.email", "E-Mail", False), ("company.phone", "Telefon", False), ("company.web", "Web", False),
]
BANK_PAGE = [("company.iban", "IBAN", False), ("company.bic", "BIC", False), ("company.bank", "Bank", False)]


def needs_setup(con) -> bool:
    """Assistent nur für neue Datenbanken: noch keine Firmendaten und nicht schon einmal durchlaufen."""
    return not get_setting(con, "company.name") and get_setting(con, DONE_SETTING) != "1"


def apply_setup(con, values: dict[str, str], profile: str, tax_mode: str, logo: str, backup_dir: str) -> None:
    """Übernimmt die Angaben des Assistenten. Das Logo wird in den Datenordner kopiert, damit es unabhängig vom
    ursprünglichen Speicherort erhalten bleibt (und in Sicherungen landet)."""
    with transaction(con):
        for key, v in values.items():
            set_setting(con, key, v.strip())
        set_setting(con, "tax.mode", tax_mode)
        if logo:
            src = Path(logo)
            dest = config.data_dir() / f"logo{src.suffix.lower()}"
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            set_setting(con, "company.logo", str(dest))
        if backup_dir:
            set_setting(con, "backup.dir", backup_dir)
        apply_profile(con, profile)
        set_setting(con, DONE_SETTING, "1")
        audit.log(con, "settings", "settings", None, {"setup": "Einrichtungsassistent", "profile": profile,
                                                      "tax.mode": tax_mode})


class _FormPage(QWizardPage):
    """Formularseite; Weiter erst, wenn alle Pflichtfelder ausgefüllt sind. (Qt-Pflichtfelder per registerField
    gelten nur als ausgefüllt, wenn sie sich geändert haben – vorbelegte Werte würden blockieren.)"""

    def __init__(self, title: str, subtitle: str, fields, edits: dict[str, QLineEdit], con):
        super().__init__()
        self.setTitle(title)
        self.setSubTitle(subtitle)
        self.required: list[QLineEdit] = []
        f = QFormLayout(self)
        for key, label, required in fields:
            e = QLineEdit(get_setting(con, key))
            edits[key] = e
            f.addRow(label, e)
            if required:
                self.required.append(e)
                e.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:
        return all(e.text().strip() for e in self.required)


class SetupWizard(QWizard):
    def __init__(self, parent, ctx):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(f"{config.APP_NAME} einrichten")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage)
        self.setButtonText(QWizard.NextButton, "Weiter >")
        self.setButtonText(QWizard.BackButton, "< Zurück")
        self.setButtonText(QWizard.FinishButton, "Fertig")
        self.setButtonText(QWizard.CancelButton, "Später")
        self.setMinimumSize(640, 520)
        self.edits: dict[str, QLineEdit] = {}

        start = QWizardPage()
        start.setTitle(f"Willkommen bei {config.APP_NAME}")
        lay = QVBoxLayout(start)
        intro = QLabel("In wenigen Schritten wird das Programm eingerichtet: Firmendaten für die Belege, Branche, "
                       "Besteuerung, Logo und Sicherungsordner.\n\nAlle Angaben lassen sich später unter "
                       "„Einstellungen“ ändern. Die Daten bleiben auf diesem PC.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.addPage(start)

        self.addPage(_FormPage("Firmendaten", "Erscheinen als Absender auf allen Belegen (Pflichtangaben mit *).",
                                COMPANY_PAGE, self.edits, ctx.con))
        self.addPage(_FormPage("Bankverbindung", "Für den Zahlungshinweis und den GiroCode auf Rechnungen.",
                                BANK_PAGE, self.edits, ctx.con))

        page = QWizardPage()
        page.setTitle("Branche und Besteuerung")
        page.setSubTitle("Die Branche bestimmt die Startartikel und Fahrtanlässe; alles bleibt frei anpassbar.")
        lay = QVBoxLayout(page)
        self.profile_group = QButtonGroup(page)
        current = get_setting(ctx.con, "profile")
        for key, p in PROFILES.items():
            rb = QRadioButton(p.label + (" – mit Kundenimport aus Hottgenroth" if p.hottgenroth else ""))
            rb.setProperty("key", key)
            rb.setChecked(key == current)
            self.profile_group.addButton(rb)
            lay.addWidget(rb)
        lay.addSpacing(16)
        self.tax_group = QButtonGroup(page)
        self.tax_ku = QRadioButton("Kleinunternehmer (§ 19 UStG) – keine Umsatzsteuer auf Rechnungen")
        self.tax_regel = QRadioButton("Regelbesteuerung – Umsatzsteuer ausweisen")
        (self.tax_ku if get_setting(ctx.con, "tax.mode") == "kleinunternehmer" else self.tax_regel).setChecked(True)
        for rb in (self.tax_ku, self.tax_regel):
            self.tax_group.addButton(rb)
            lay.addWidget(rb)
        lay.addStretch(1)
        self.addPage(page)

        page = QWizardPage()
        page.setTitle("Logo und Datensicherung")
        page.setSubTitle("Beides ist optional und lässt sich später ändern.")
        f = QFormLayout(page)
        self.logo = QLineEdit()
        self.logo.setPlaceholderText("kein Logo")
        self.logo.textChanged.connect(self._show_logo)
        f.addRow("Logo (PNG/JPG)", hbox(self.logo, button("…", self.pick_logo)))
        self.logo_preview = QLabel()
        self.logo_preview.setMinimumHeight(90)
        self.logo_preview.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        f.addRow("", self.logo_preview)
        self.backup = QLineEdit(get_setting(ctx.con, "backup.dir"))
        f.addRow("Sicherungsordner", hbox(self.backup, button("…", self.pick_backup)))
        hint = QLabel("Beim Beenden wird täglich eine Sicherung angelegt. Am besten einen Ordner wählen, der zusätzlich "
                      "extern gesichert wird (z.B. Cloud-Ordner oder externe Festplatte). Die Datenbank selbst bleibt "
                      "bewusst lokal.")
        hint.setWordWrap(True)
        f.addRow(hint)
        self.addPage(page)

    def profile_key(self) -> str:
        b = self.profile_group.checkedButton()
        return b.property("key") if b else "allgemein"

    def _show_logo(self, path: str):
        pm = QPixmap(path) if path else QPixmap()
        self.logo_preview.setPixmap(pm.scaled(240, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                    if not pm.isNull() else QPixmap())

    def pick_logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Logo wählen", "", "Bilder (*.png *.jpg *.jpeg)")
        if p:
            self.logo.setText(p)

    def pick_backup(self):
        d = QFileDialog.getExistingDirectory(self, "Sicherungsordner wählen", self.backup.text())
        if d:
            self.backup.setText(d)

    def validateCurrentPage(self) -> bool:
        if not self.currentPage().isComplete():  # Pflichtfelder (auch bei Weiter per Tastatur/Code)
            return False
        if self.currentPage() is self.page(self.pageIds()[-1]):
            logo = self.logo.text().strip()
            if logo and QPixmap(logo).isNull():
                self.logo_preview.setText("Die Datei ist kein lesbares Bild.")
                return False
        return super().validateCurrentPage()

    def accept(self):
        apply_setup(self.ctx.con, {k: e.text() for k, e in self.edits.items()}, self.profile_key(),
                    "kleinunternehmer" if self.tax_ku.isChecked() else "regel",
                    self.logo.text().strip(), self.backup.text().strip())
        super().accept()
