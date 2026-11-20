"""Tests zu den Korrekturen der Version 2.2.4 (nur Dummy-Daten)."""
from __future__ import annotations

import csv
import sqlite3
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_core import _customer, _fake_pdf, _invoice, con  # noqa: E402, F401


# ------------------------------------------------------------------ Bank-CSV: Minus am Ende
@pytest.mark.parametrize("text, cents", [("12,50-", -1250), ("1.234,56-", -123456), ("-12,50", -1250),
                                         ("(12,50)", -1250), ("12,50", 1250)])
def test_trailing_minus_is_negative(text, cents):
    from app.money import input_cents
    assert input_cents(text) == cents


def test_bank_csv_with_trailing_minus_finds_debits(con, tmp_path):
    from app.importers import bank_csv
    f = tmp_path / "konto.csv"
    f.write_text("Buchungstag;Empfänger;Verwendungszweck;Betrag\n"
                 "05.03.2026;Telekom;Rechnung März;39,95-\n"
                 "06.03.2026;Kunde;Zahlung;500,00\n", encoding="cp1252")
    _h, _m, rows = bank_csv.parse(con, f)
    assert [(r.name, r.amount_cents) for r in rows] == [("Telekom", 3995)]


# ------------------------------------------------------------------ Jahresabschluss sperrt Rechnungen
def test_closed_year_blocks_finalize(con):
    from app.services import documents, expenses
    did = _invoice(con, _customer(con), on="2025-12-30")
    expenses.lock_year(con, 2025, today=date(2026, 2, 1))
    doc = documents.get_document(con, did)
    assert any("2025 ist abgeschlossen" in p for p in documents.validate(con, doc, documents.get_lines(con, did)))
    with pytest.raises(documents.ValidationError, match="abgeschlossen"):
        documents.finalize(con, did, pdf_writer=_fake_pdf)
    # auch an der Programmlogik vorbei (Datenbank-Trigger)
    with pytest.raises(sqlite3.IntegrityError, match="abgeschlossen"):
        con.execute("UPDATE document SET status='festgeschrieben', number='2025-1299' WHERE id=?", (did,))
    assert documents.get_document(con, did)["status"] == "entwurf"
    # Angebote sind nicht betroffen
    offer = _invoice(con, _customer(con), on="2025-12-30", doc_type="angebot")
    documents.finalize(con, offer, pdf_writer=_fake_pdf)


# Der Import eines Altbelegs (unbezahlt) in ein abgeschlossenes Jahr bleibt erlaubt – siehe
# test_hardening.test_import_as_paid_into_closed_year_is_refused, der das auch am Datenbank-Trigger vorbei prüft.


# ------------------------------------------------------------------ Kleinunternehmer: Gründungsjahr
def test_founding_year_uses_25k_limit_now(con):
    from app.db.database import set_setting
    from app.services import documents, kleinunternehmer, payments
    cid = _customer(con)
    did = _invoice(con, cid, price=2400000, qty="1", on="2026-05-01")
    documents.finalize(con, did, pdf_writer=_fake_pdf)
    payments.add_payment(con, did, "2026-05-10", "24000")
    d = dict(documents.new_draft(con, "rechnung", cid), doc_date="2026-06-01", total_net_cents=200000)

    # ohne Gründungsjahr: nur die 100.000-€-Grenze
    assert kleinunternehmer.check_before_finalize(con, d) == []
    assert kleinunternehmer.status(con, 2026).current_limit_cents == 10_000_000

    set_setting(con, "ku.founding_year", "2026")
    st = kleinunternehmer.status(con, 2026)
    assert st.founded_this_year and st.current_limit_cents == 2_500_000
    msgs = kleinunternehmer.check_before_finalize(con, d)
    assert len(msgs) == 1 and "25.000,00 € (Gründungsjahr)" in msgs[0]
    assert any("Gründungsjahr erreicht" in w for w in st.warnings())

    # im Folgejahr gilt wieder die normale Regel
    assert not kleinunternehmer.status(con, 2027).founded_this_year


def test_founding_year_empty_or_invalid_is_ignored(con):
    from app.db.database import set_setting
    from app.services import kleinunternehmer
    for value in ("", "abc", " "):
        set_setting(con, "ku.founding_year", value)
        assert kleinunternehmer.founding_year(con) is None


# ------------------------------------------------------------------ E-Rechnung: Hinweis bei 0 % (Regel)
def test_zero_rate_warning_for_einvoice_regel():
    from app.services import documents
    doc = {"doc_type": "rechnung", "tax_mode": "regel", "zugferd": 1, "doc_date": date.today().isoformat()}
    lines = [{"title": "PV-Anlage", "quantity": "1", "unit_price_cents": 100000, "tax_rate": "0"}]
    assert any("Nullsatz" in w for w in documents.plausibility_warnings(doc, lines))
    assert not any("Nullsatz" in w for w in documents.plausibility_warnings(dict(doc, zugferd=0), lines))
    lines19 = [dict(lines[0], tax_rate="19")]
    assert not any("Nullsatz" in w for w in documents.plausibility_warnings(doc, lines19))


# ------------------------------------------------------------------ Reste einer Rücksicherung
def test_cleanup_leftovers(con):
    from app import config
    from app.services import backup
    data = config.data_dir()
    (data / "entschluesselt-20260101-120000.zip").write_bytes(b"klartext")
    (data / "ruecksicherung-neu-20260101-120000" / "archiv").mkdir(parents=True)
    keep = data / "vor-ruecksicherung-20260101-120000"
    keep.mkdir()
    backup.cleanup_leftovers()
    assert not list(data.glob("entschluesselt-*")) and not list(data.glob("ruecksicherung-neu-*"))
    assert keep.exists() and config.db_path().exists()


# ------------------------------------------------------------------ Update-ZIP: Laufwerksangaben
@pytest.mark.parametrize("bad", ["KleinFaktura/D:evil.dll", "KleinFaktura/../x.exe", "KleinFaktura/a/C:/x.dll"])
def test_update_zip_rejects_drive_and_parent_paths(tmp_path, bad):
    from app.services import update
    z = tmp_path / "u.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(f"KleinFaktura/{update.EXE_NAME}", b"exe")
        f.writestr(bad, b"x")
    with pytest.raises(update.UpdateError, match="Ungültiger Pfad"):
        update.extract(z, tmp_path / "neu")


# ------------------------------------------------------------------ Transaktion: Fehler nicht verdecken
def test_transaction_keeps_original_error_after_auto_rollback(con):
    from app.db.database import transaction

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction(con):
            con.execute("ROLLBACK")      # wie ein von SQLite selbst beendeter Vorgang
            raise Boom
    assert not con.in_transaction


# ------------------------------------------------------------------ CSV: Standarddialekt bleibt unverändert
def test_csv_fallback_does_not_change_global_dialect(tmp_path):
    from app.importers.csv_import import read_table
    before = csv.excel.delimiter
    f = tmp_path / "eine-spalte.csv"
    f.write_text("Name\nA\nB\n", encoding="utf-8")
    read_table(f)
    assert csv.excel.delimiter == before == ","
