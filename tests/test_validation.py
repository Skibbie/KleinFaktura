"""Prüfung von PLZ, IBAN, BIC und E-Mail (Assistent und Einstellungen)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import validation as v  # noqa: E402

GOOD_IBAN = "DE89 3704 0044 0532 0130 00"   # Beispiel-IBAN der Bundesbank


def test_iban():
    assert v.iban_error(GOOD_IBAN) is None
    assert v.iban_error("de89370400440532013000") is None            # klein, ohne Leerzeichen
    assert v.iban_error("AT61 1904 3002 3457 3201") is None
    assert v.iban_error("") is None                                   # freiwillig
    assert "Prüfziffer" in v.iban_error("DE88 3704 0044 0532 0130 00")
    assert "22 Zeichen" in v.iban_error("DE89 3704 0044 0532 0130")
    assert "Ländercode" in v.iban_error("3704 0044 0532 0130 00")
    assert v.format_iban("de89370400440532013000") == GOOD_IBAN


@pytest.mark.parametrize("bic, ok", [("COBADEFFXXX", True), ("cobadeff", True), ("COBA DE FF", True),
                                     ("COBADEF", False), ("1OBADEFF", False)])
def test_bic(bic, ok):
    assert (v.bic_error(bic) is None) is ok


def test_zip_and_email():
    assert v.zip_error("12345") is None
    assert "5 Ziffern" in v.zip_error("1234")
    assert "5 Ziffern" in v.zip_error("12a45")
    assert v.zip_error("1010", "AT") is None and v.zip_error("ABC", "US") is None   # unbekanntes Land: keine Prüfung
    assert v.email_error("info@example.org") is None
    assert v.email_error("info@example") and v.email_error("info example.org")


def test_company_problems_and_normalize():
    vals = {"company.zip": "1234", "company.iban": "DE00 1234", "company.bic": "x", "company.email": "a@b"}
    assert len(v.company_problems(vals)) == 4
    n = v.normalize_company({"company.iban": "de89370400440532013000", "company.bic": "coba deff xxx"})
    assert n == {"company.iban": GOOD_IBAN, "company.bic": "COBADEFFXXX"}


def test_wizard_blocks_invalid_input(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.db import database
    from app.db.database import get_setting
    from app.ui.common import Ctx
    from app.ui.setup_wizard import SetupWizard
    con = database.connect()
    database.init_db(con)
    w = SetupWizard(None, Ctx(con))
    w.show()
    for key in ("company.name", "company.owner", "company.street", "company.city", "company.tax_number"):
        w.edits[key].setText("x")
    w.edits["company.zip"].setText("1234")
    w.next()                                    # Start → Firmendaten
    w.next()                                    # PLZ falsch: bleibt stehen
    page = w.currentPage()
    assert page.error.isVisible() and "PLZ" in page.error.text()
    w.edits["company.zip"].setText("12345")
    assert not page.error.isVisible()           # Hinweis verschwindet beim Korrigieren
    w.next()                                    # → Bankverbindung
    w.edits["company.iban"].setText("DE88 3704 0044 0532 0130 00")
    w.next()
    assert "Prüfziffer" in w.currentPage().error.text()
    w.edits["company.iban"].setText("de89370400440532013000")
    w.next()
    w.next()
    w.accept()
    assert get_setting(con, "company.iban") == GOOD_IBAN
    w.close()
    con.close()
    app.processEvents()
