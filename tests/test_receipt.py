"""Quittungen über Barzahlungen (nur Dummy-Daten)."""
from __future__ import annotations

import pytest

from test_core import _customer, _fake_pdf, _invoice, con  # noqa: F401 (Fixture)
from test_gui import gui  # noqa: F401 (Fixture)


@pytest.mark.parametrize("cents, words", [
    (100, "ein Euro"),
    (101, "ein Euro ein Cent"),
    (2100, "einundzwanzig Euro"),
    (12345, "einhundertdreiundzwanzig Euro fünfundvierzig Cent"),
    (120100, "eintausendzweihundertein Euro"),
    (31, "null Euro einunddreißig Cent"),
    (100_000_000, "eine Million Euro"),
    (250_000_000, "zwei Millionen fünfhunderttausend Euro"),
])
def test_betrag_in_worten(cents, words):
    from app.money import betrag_in_worten
    assert betrag_in_worten(cents) == words


def test_int_in_worten_standalone():
    from app.money import int_in_worten
    assert int_in_worten(1) == "eins" and int_in_worten(1201) == "eintausendzweihunderteins"
    assert int_in_worten(1_000_001) == "eine Million eins"
    with pytest.raises(ValueError):
        int_in_worten(-1)


def _paid(con, method="Bar"):
    from app.services import documents, payments
    doc = documents.finalize(con, _invoice(con, _customer(con), price=10000, qty="1", on="2026-11-02"),
                             pdf_writer=_fake_pdf)
    pid = payments.add_payment(con, doc["id"], "2026-11-20", 4000, method)
    return doc, pid


def test_receipt_data_number_and_archive(con):
    from app.services import audit, payments
    doc, pid = _paid(con)
    seen = {}

    def writer(data, path):
        seen.update(data)
        path.write_bytes(b"%PDF-1.4 Quittung")

    path = payments.create_receipt(con, pid, pdf_writer=writer)
    assert seen["number"] == f"{doc['number']}-Q1" and path.name == f"Quittung-{doc['number']}-Q1.pdf".replace("/", "_")
    assert path.parent.name == "2026"
    assert seen["paid_before_cents"] == 0 and seen["open_after_cents"] == 6000 and seen["kleinunternehmer"]
    assert seen["payer"]
    # zweiter Aufruf: dieselbe Datei, kein zweites PDF, kein zweiter Protokolleintrag
    n_logs = con.execute("SELECT COUNT(*) FROM audit_log WHERE action='receipt'").fetchone()[0]
    assert payments.create_receipt(con, pid, pdf_writer=lambda d, p: pytest.fail("neu erzeugt")) == path
    assert con.execute("SELECT COUNT(*) FROM audit_log WHERE action='receipt'").fetchone()[0] == n_logs == 1
    assert audit.verify(con)[0]

    pid2 = payments.add_payment(con, doc["id"], "2026-11-21", 6000, "Bar")
    payments.create_receipt(con, pid2, pdf_writer=writer)
    assert seen["number"].endswith("-Q2") and seen["paid_before_cents"] == 4000 and seen["open_after_cents"] == 0


def test_no_receipt_for_refunds(con):
    from app.services import payments
    doc, pid = _paid(con)
    neg = payments.add_payment(con, doc["id"], "2026-11-21", -4000, "Korrekturbuchung")
    with pytest.raises(ValueError, match="nur für erhaltene"):
        payments.create_receipt(con, neg, pdf_writer=_fake_pdf)
    with pytest.raises(ValueError, match="nicht gefunden"):
        payments.create_receipt(con, 9999, pdf_writer=_fake_pdf)


def test_receipt_real_pdf(con):
    from app.services import payments
    _doc, pid = _paid(con)
    path = payments.create_receipt(con, pid)
    raw = path.read_bytes()
    assert raw.startswith(b"%PDF") and len(raw) > 1000


def test_gui_cash_payment_offers_receipt(gui):
    app, ctx, log, mp, tmp = gui
    from app.services import payments
    from app.ui import documents_page
    from test_gui_pages import _overdue_invoice
    doc = _overdue_invoice(ctx.con)
    opened = []
    mp.setattr(documents_page, "open_file", lambda p: opened.append(p))
    mp.setattr(documents_page.PaymentDialog, "exec", lambda self: (self.save(), self.result())[1])

    # Überweisung: keine Quittung angeboten
    page = documents_page.DocumentsPage(ctx, "rechnung")
    page.year.setCurrentIndex(0)
    page.table.selectRow(0)
    assert not page.sel_buttons["receipt"].isEnabled()
    page.receipt()
    assert any("noch keine Zahlung" in t for n, t in log if n == "information")
    orig_init = documents_page.PaymentDialog.__init__
    mp.setattr(documents_page.PaymentDialog, "__init__", _init_with(orig_init, "30,00", "Überweisung"))
    documents_page.record_payment(None, ctx, doc["id"])
    assert opened == []

    # Barzahlung: Rückfrage (im Test „Ja“) -> Quittung erzeugt und geöffnet
    mp.setattr(documents_page.PaymentDialog, "__init__", _init_with(orig_init, "70,00", "Bar"))
    documents_page.record_payment(None, ctx, doc["id"])
    assert len(opened) == 1 and opened[0].name.startswith("Quittung-") and opened[0].name.endswith("-Q2.pdf")
    assert payments.open_amount(ctx.con, doc["id"]) == 0

    # Knopf in der Rechnungsliste: wählt die Barzahlung vor, öffnet die bereits archivierte Datei erneut
    page.reload()
    page.table.selectRow(0)
    assert page.sel_buttons["receipt"].isEnabled()
    page.receipt()
    assert opened[-1] == opened[0]



def _init_with(orig, amount, method):
    """PaymentDialog mit vorbelegtem Betrag und Zahlungsart."""
    def init(self, *a, **k):
        orig(self, *a, **k)
        self.amount.setText(amount)
        self.method.setCurrentText(method)
    return init
