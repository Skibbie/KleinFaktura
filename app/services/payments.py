"""Zahlungen, offene Posten und Mahnwesen."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from app.db import repo
from app.db.database import get_setting, transaction
from app.money import fmt_date, fmt_eur, to_cents
from app.services import audit, documents

DUNNING_LABEL = {1: "Zahlungserinnerung", 2: "1. Mahnung", 3: "2. Mahnung"}


def paid_cents(con: sqlite3.Connection, doc_id: int) -> int:
    return con.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payment WHERE document_id=?", (doc_id,)).fetchone()[0]


def open_amount(con: sqlite3.Connection, doc_id: int) -> int:
    d = documents.get_document(con, doc_id)
    if not d or d["doc_type"] != "rechnung" or d["status"] != "festgeschrieben" or documents.is_cancelled(con, doc_id):
        return 0
    return int(d["total_gross_cents"]) - paid_cents(con, doc_id)


def list_payments(con: sqlite3.Connection, doc_id: int | None = None) -> list[dict]:
    if doc_id:
        rows = con.execute("SELECT * FROM payment WHERE document_id=? ORDER BY pay_date, id", (doc_id,))
    else:
        rows = con.execute("SELECT p.*, d.number FROM payment p JOIN document d ON d.id=p.document_id ORDER BY pay_date DESC, p.id DESC")
    return [dict(r) for r in rows]


def add_payment(con: sqlite3.Connection, doc_id: int, pay_date: str, amount, method: str = "Überweisung",
                note: str = "") -> int:
    cents = amount if isinstance(amount, int) else to_cents(amount)
    if cents == 0:
        raise ValueError("Betrag darf nicht 0 sein.")
    with transaction(con):
        d = documents.get_document(con, doc_id)
        if not d or d["doc_type"] != "rechnung" or d["status"] != "festgeschrieben":
            raise ValueError("Zahlungen nur auf festgeschriebene Rechnungen.")
        cur = con.execute(
            "INSERT INTO payment(document_id, pay_date, amount_cents, method, note) VALUES (?,?,?,?,?)",
            (doc_id, pay_date, cents, method, note),
        )
        audit.log(con, "payment", "document", doc_id, {"date": pay_date, "cents": cents, "method": method, "note": note})
        return cur.lastrowid


def open_items(con: sqlite3.Connection) -> list[dict]:
    items = [d for d in documents.list_documents(con, ("rechnung",)) if d["status"] == "festgeschrieben" and d["open_cents"] != 0]
    today = date.today()
    for d in items:
        due = d.get("due_date")
        d["days_overdue"] = (today - date.fromisoformat(due)).days if due and due < today.isoformat() else 0
    items.sort(key=lambda x: (x.get("due_date") or "9999"))
    return items


def next_dunning_level(con: sqlite3.Connection, doc_id: int) -> int:
    row = con.execute("SELECT MAX(level) FROM dunning WHERE document_id=?", (doc_id,)).fetchone()
    return min((row[0] or 0) + 1, 3)


def dunning_history(con: sqlite3.Connection, doc_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM dunning WHERE document_id=? ORDER BY level", (doc_id,))]


def create_dunning(con: sqlite3.Connection, doc_id: int, level: int | None = None, fee_cents: int | None = None,
                   days: int | None = None, text: str | None = None, pdf_writer=None) -> dict:
    if pdf_writer is None:
        from app.output.pdf_invoice import write_dunning_pdf as pdf_writer
    inv = documents.get_document(con, doc_id)
    open_ = open_amount(con, doc_id)
    if open_ <= 0:
        raise ValueError("Rechnung ist nicht offen.")
    level = level or next_dunning_level(con, doc_id)
    if fee_cents is None:
        fee_cents = to_cents(get_setting(con, f"dunning.fee_{level}", "0"))
    days = days if days is not None else int(get_setting(con, "dunning.days", "10"))
    today = date.today()
    new_due = today + timedelta(days=days)
    prev_fees = con.execute("SELECT COALESCE(SUM(fee_cents),0) FROM dunning WHERE document_id=?", (doc_id,)).fetchone()[0]
    total = open_ + prev_fees + fee_cents
    values = documents.placeholders(con, inv, extra={
        "offen": fmt_eur(open_),
        "gebuehr": fmt_eur(fee_cents),
        "gesamt": fmt_eur(total),
        "neu_faellig": fmt_date(new_due.isoformat()),
        "rechnungsnr": inv["number"],
        "rechnungsdatum": fmt_date(inv["doc_date"]),
    })
    body = documents.render(text if text is not None else repo.default_text(con, f"mahnung{level}"), values)
    cust = repo.get_customer(con, inv["customer_id"]) or {}
    import json
    recipient = json.loads(inv["recipient_json"]) if inv.get("recipient_json") else {"lines": repo.recipient_lines(cust)}
    comp = repo_company(con)
    data = {
        "label": DUNNING_LABEL[level],
        "level": level,
        "date": today.isoformat(),
        "invoice": inv,
        "company": comp,
        "recipient": recipient,
        "salutation": documents.render(cust.get("letter_salutation") or repo.default_letter_salutation(cust), values),
        "body": body,
        "open_cents": open_,
        "prev_fees_cents": prev_fees,
        "fee_cents": fee_cents,
        "total_cents": total,
        "new_due": new_due.isoformat(),
        "girocode": True,
        "logo": comp.get("logo", ""),
        "closing": documents.render(repo.default_text(con, "gruss"), values),
    }
    path = documents.archive_path_for(f"{inv['number']}-M{level}-{today.strftime('%Y%m%d')}", today.isoformat(), "rechnung")
    path = path.with_name(path.name.replace("Rechnung-", f"{DUNNING_LABEL[level].replace(' ', '').replace('.', '')}-"))
    try:
        pdf_writer(data, path)
        h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    with transaction(con):
        cur = con.execute(
            "INSERT INTO dunning(document_id, level, dunning_date, new_due_date, fee_cents, open_cents, pdf_path, pdf_sha256)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, level, today.isoformat(), new_due.isoformat(), fee_cents, open_, str(path), h),
        )
        audit.log(con, "dunning", "document", doc_id, {"level": level, "fee": fee_cents, "open": open_, "pdf_sha256": h})
    return {"id": cur.lastrowid, "path": path, "level": level}


def repo_company(con):
    from app.db.database import company
    return company(con)
