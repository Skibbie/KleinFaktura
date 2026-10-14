"""Regressionstests zu den Funden aus dem Code-Review (nur Dummy-Daten)."""
from __future__ import annotations

import os
import shutil
import stat
from decimal import Decimal
from pathlib import Path

import pytest

from test_core import _customer, _fake_pdf, _invoice, con  # noqa: F401 (Fixture)


def _fake_dunning_pdf(data, path):
    Path(path).write_bytes(b"%PDF-1.4 Mahnung " + str(data["level"]).encode())


def _paid_invoice_setup(con, price=10000):
    from app.db.database import set_setting
    from app.services import documents, payments
    set_setting(con, "dunning.fee_2", "5")
    doc = documents.finalize(con, _invoice(con, _customer(con), price=price, qty="1", on="2025-11-03"),
                             pdf_writer=_fake_pdf)
    payments.create_dunning(con, doc["id"], level=2, pdf_writer=_fake_dunning_pdf)
    return doc


# ------------------------------------------------------------------ Mahngebühren
def test_dunning_fee_payment_is_not_overpayment(con):
    from app.services import documents, payments
    doc = _paid_invoice_setup(con)
    payments.add_payment(con, doc["id"], "2025-12-01", 10500)     # Rechnung + 5 € Mahngebühr
    assert payments.open_amount(con, doc["id"]) == 0
    row = documents.list_documents(con, ("rechnung",))[0]
    assert row["open_cents"] == 0 and row["status_label"] == "Bezahlt"
    assert payments.open_items(con) == []
    payments.add_payment(con, doc["id"], "2025-12-02", 500)       # echte Überzahlung
    assert payments.open_amount(con, doc["id"]) == -500


def test_fees_do_not_count_as_ku_revenue_across_years(con):
    from app.services import kleinunternehmer, payments
    doc = _paid_invoice_setup(con)
    payments.add_payment(con, doc["id"], "2025-12-20", 10000)     # Rechnungsbetrag im alten Jahr
    payments.add_payment(con, doc["id"], "2026-01-10", 500)       # Gebühr erst im neuen Jahr
    assert kleinunternehmer.revenue_cents(con, 2025, "ist") == 10000
    assert kleinunternehmer.revenue_cents(con, 2026, "ist") == 0
    parts = payments.payment_parts(con, 2026)
    assert [(p["revenue_cents"], p["other_cents"]) for p in parts] == [(0, 500)]
    # Rückzahlung der Überzahlung läuft durch dieselbe Rechnung
    payments.add_payment(con, doc["id"], "2026-02-01", -500)
    assert [(p["revenue_cents"], p["other_cents"]) for p in payments.payment_parts(con, 2026)][-1] == (0, -500)


def test_datev_books_fees_to_separate_account(con, tmp_path):
    from app.services import export, payments
    doc = _paid_invoice_setup(con)
    payments.add_payment(con, doc["id"], "2025-12-01", 10500)
    text = export.export_datev(con, tmp_path / "datev.csv", 2025).read_bytes().decode("cp1252")
    rows = [r.split(";") for r in text.splitlines()[2:]]
    assert [(r[0], r[7]) for r in rows] == [("100,00", "8195"), ("5,00", "2700")]


def test_repeated_dunning_same_day_keeps_archived_pdf(con):
    from app.services import documents, payments
    doc = documents.finalize(con, _invoice(con, _customer(con), on="2026-10-01"), pdf_writer=_fake_pdf)
    first = payments.create_dunning(con, doc["id"], level=3, pdf_writer=_fake_dunning_pdf)
    second = payments.create_dunning(con, doc["id"], level=3, pdf_writer=_fake_dunning_pdf)
    assert first["path"] != second["path"] and first["path"].exists() and second["path"].exists()
    assert not os.access(first["path"], os.W_OK)
    assert documents.verify_archive(con) == []


# ------------------------------------------------------------------ Einstellungen
def test_invalid_numeric_settings_fall_back_to_defaults(con, tmp_path):
    from app.db.database import set_setting
    from app.services import backup, documents, kleinunternehmer
    for key, bad in [("invoice.payment_days", ""), ("offer.valid_days", "30 Tage"), ("ku.warn_percent", "80 %"),
                     ("ku.limit_prev", "viel"), ("backup.keep_daily", "x"), ("dunning.days", "")]:
        set_setting(con, key, bad)
    draft = documents.new_draft(con, "rechnung")
    assert draft["due_date"] > draft["doc_date"]
    documents.new_draft(con, "angebot")
    st = kleinunternehmer.status(con)
    assert st.warn_percent == 80 and st.limit_prev_cents == 2500000
    st.warnings()
    backup.create_backup(con, tmp_path / "sicherung")
    set_setting(con, "ku.limit_prev", "25.000")                   # deutsche Schreibweise
    assert kleinunternehmer.status(con).limit_prev_cents == 2500000


# ------------------------------------------------------------------ Platzhalter
def test_render_survives_stray_braces():
    from app.services.documents import render
    text = "Betrag {betrag} } { offen {unbekannt} {kunde.x} {0} {{betrag}}"
    assert render(text, {"betrag": "12,00 €"}) == "Betrag 12,00 € } { offen {unbekannt} {kunde.x} {0} {12,00 €}"
    assert render("ohne Platzhalter }", {}) == "ohne Platzhalter }"


def test_finalize_with_brace_in_text(con):
    from app.services import documents
    did = _invoice(con, _customer(con))
    doc = documents.get_document(con, did)
    doc["footer_text"] = "Zahlbar bis {faellig (siehe AGB}"
    documents.save_draft(con, doc, documents.get_lines(con, did))
    fin = documents.finalize(con, did, pdf_writer=_fake_pdf)
    assert fin["footer_text"] == "Zahlbar bis {faellig (siehe AGB}"


# ------------------------------------------------------------------ Pfade nach Rücksicherung
def test_archive_paths_resolve_after_moving_data_dir(con, tmp_path, monkeypatch):
    from app import config
    from app.services import documents, mail
    doc = documents.finalize(con, _invoice(con, _customer(con), on="2026-10-01"), pdf_writer=_fake_pdf)
    old_pdf = Path(doc["pdf_path"])
    new_data = tmp_path / "anderer-pc"
    shutil.copytree(config.data_dir() / "archiv", new_data / "archiv")
    for f in (config.data_dir() / "archiv").rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IWRITE | stat.S_IREAD)
    shutil.rmtree(config.data_dir() / "archiv")
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(new_data))
    assert not old_pdf.exists()
    resolved = config.resolve_stored_path(old_pdf)
    assert resolved == new_data / "archiv" / "2026" / old_pdf.name and resolved.exists()
    assert documents.verify_archive(con) == []
    assert mail.document_pdf(doc) == resolved
    (new_data / "logo.png").write_bytes(b"png")
    assert config.resolve_stored_path(tmp_path / "alter-ordner" / "logo.png") == new_data / "logo.png"


def test_logo_found_even_if_old_path_contains_archiv(con, tmp_path):
    from app import config
    (config.data_dir() / "logo.png").write_bytes(b"png")
    assert config.resolve_stored_path(tmp_path / "Dokumente" / "Archiv" / "logo.png") == config.data_dir() / "logo.png"


def test_backup_falls_back_when_backup_dir_unusable(con, tmp_path):
    from app import config
    from app.db.database import set_setting
    from app.services import backup
    blocker = tmp_path / "keine-ordner"
    blocker.write_text("Datei statt Ordner")
    set_setting(con, "backup.dir", str(blocker / "Sicherung"))     # nicht anlegbar
    z = backup.create_backup(con)
    assert z.exists() and z.parent == config.default_backup_dir()


def test_restore_makes_archive_read_only(con, tmp_path):
    from app import config
    from app.services import backup, documents
    doc = documents.finalize(con, _invoice(con, _customer(con)), pdf_writer=_fake_pdf)
    z = backup.create_backup(con, tmp_path / "sicherung")
    con.close()
    backup.restore_backup(z)
    restored = config.resolve_stored_path(doc["pdf_path"])
    assert restored.exists() and not os.access(restored, os.W_OK)


# ------------------------------------------------------------------ Eingaben
@pytest.mark.parametrize("text, expected", [
    ("1.500", Decimal("1500")), ("1.500,50", Decimal("1500.50")), ("12.345.678", Decimal("12345678")),
    ("1.5", Decimal("1.5")), ("0.300", Decimal("0.300")), ("2,250", Decimal("2.250")), ("-1.000", Decimal("-1000")),
    ("1.500 €", Decimal("1500")), ("", Decimal("0")),
])
def test_input_decimal_german(text, expected):
    from app.money import input_decimal
    assert input_decimal(text) == expected


def test_stored_quantities_keep_decimal_point(con):
    """Gespeicherte Mengen ('2.250' = 2,25) dürfen nicht als Tausender gelesen werden."""
    from app.money import input_decimal, line_total_cents, to_decimal
    from app.services import documents
    did = documents.save_draft(con, documents.new_draft(con, "rechnung", _customer(con)),
                               [{"title": "Beratung", "quantity": str(input_decimal("2,250")), "unit": "Std.",
                                 "unit_price_cents": 10000}])
    q = documents.get_lines(con, did)[0]["quantity"]
    assert to_decimal(q) == Decimal("2.25")
    assert documents.get_document(con, did)["total_gross_cents"] == 22500 == line_total_cents(q, 10000)


# ------------------------------------------------------------------ Storno kopieren / Mail-Anhang
def test_copy_of_storno_has_positive_quantities(con):
    from app.money import to_decimal
    from app.services import documents
    inv = documents.finalize(con, _invoice(con, _customer(con)), pdf_writer=_fake_pdf)
    storno = documents.create_storno(con, inv["id"], pdf_writer=_fake_pdf)
    copy_id = documents.copy_as_draft(con, storno["id"], "rechnung")
    copy = documents.get_document(con, copy_id)
    assert copy["doc_type"] == "rechnung" and copy["total_gross_cents"] == inv["total_gross_cents"] > 0
    assert all(to_decimal(ln["quantity"]) > 0 for ln in documents.get_lines(con, copy_id))


def test_mail_refuses_imported_spreadsheet(con, tmp_path):
    from app.services import mail
    sheet = tmp_path / "Rechnung 2024-0501.xlsx"
    sheet.write_bytes(b"PK")
    doc = {"id": 1, "status": "festgeschrieben", "doc_type": "rechnung", "pdf_path": None,
           "import_file_path": str(sheet)}
    assert mail.document_pdf(doc) is None
    with pytest.raises(mail.MailError, match="kein PDF"):
        mail.open_compose(con, doc)
