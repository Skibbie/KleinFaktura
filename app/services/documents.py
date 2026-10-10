"""Beleg-Logik: Entwurf -> Festschreiben -> (Storno). Angebote, Rechnungen, Stornorechnungen."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app import config
from app.db import repo
from app.db.database import company, dumps, get_setting, row_to_dict, transaction
from app.money import fmt_date, fmt_eur, line_total_cents, to_decimal
from app.services import audit, numbering

DOC_TYPE_LABEL = {"angebot": "Angebot", "rechnung": "Rechnung", "storno": "Stornorechnung"}
LINE_FIELDS = ["pos", "title", "description", "quantity", "unit", "unit_price_cents", "tax_rate"]
DOC_EDIT_FIELDS = [
    "customer_id", "project_id", "doc_date", "service_date", "service_date_to", "due_date", "valid_until",
    "subject", "salutation_text", "header_text", "footer_text", "notes_text", "tax_mode", "zugferd", "girocode",
    "ref_document_id",
]


class ValidationError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


# ---------------------------------------------------------------- Lesen
def get_document(con: sqlite3.Connection, doc_id: int) -> dict | None:
    return row_to_dict(con.execute("SELECT * FROM document WHERE id=?", (doc_id,)).fetchone())


def get_lines(con: sqlite3.Connection, doc_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM document_line WHERE document_id=? ORDER BY pos, id", (doc_id,))]


def list_documents(con: sqlite3.Connection, doc_types: tuple[str, ...] | None = None, search: str = "",
                   year: int | None = None) -> list[dict]:
    sql = """SELECT d.*, c.last_name, c.first_name, c.company AS c_company, c.customer_no,
                    (SELECT COALESCE(SUM(amount_cents),0) FROM payment p WHERE p.document_id=d.id) AS paid_cents,
                    (SELECT s.number FROM document s WHERE s.ref_document_id=d.id AND s.doc_type='storno'
                        AND s.status='festgeschrieben' LIMIT 1) AS storno_number,
                    (SELECT MAX(level) FROM dunning m WHERE m.document_id=d.id) AS dunning_level,
                    (SELECT r.number FROM document r WHERE r.ref_document_id=d.id AND r.doc_type='rechnung'
                        LIMIT 1) AS converted
             FROM document d LEFT JOIN customer c ON c.id=d.customer_id WHERE 1=1"""
    args: list = []
    if doc_types:
        sql += f" AND d.doc_type IN ({','.join('?' for _ in doc_types)})"
        args += list(doc_types)
    if year:
        sql += " AND substr(d.doc_date,1,4)=?"
        args.append(f"{year:04d}")
    if search:
        like = f"%{search}%"
        sql += " AND (d.number LIKE ? OR c.last_name LIKE ? OR c.first_name LIKE ? OR c.company LIKE ? OR d.subject LIKE ?)"
        args += [like] * 5
    sql += " ORDER BY COALESCE(d.doc_date,'9999') DESC, d.id DESC"
    out = []
    for r in con.execute(sql, args):
        d = dict(r)
        d["status_label"] = status_label(d)
        d["open_cents"] = open_cents(d)
        out.append(d)
    return out


def open_cents(d: dict) -> int:
    if d["doc_type"] != "rechnung" or d["status"] != "festgeschrieben" or d.get("storno_number"):
        return 0
    return int(d.get("total_gross_cents") or 0) - int(d.get("paid_cents") or 0)


def status_label(d: dict) -> str:
    if d["status"] == "entwurf":
        return "Entwurf"
    if d["doc_type"] == "angebot":
        if d.get("converted"):
            return "Angenommen"
        if d.get("valid_until") and d["valid_until"] < date.today().isoformat():
            return "Abgelaufen"
        return "Offen"
    if d["doc_type"] == "storno":
        return "Storno"
    if d.get("storno_number"):
        return "Storniert"
    open_ = open_cents(d)
    if open_ <= 0:
        return "Bezahlt"
    paid = int(d.get("paid_cents") or 0)
    overdue = d.get("due_date") and d["due_date"] < date.today().isoformat()
    if overdue:
        return "Überfällig" + (f" (M{d['dunning_level']})" if d.get("dunning_level") else "")
    return "Teilbezahlt" if paid > 0 else "Offen"


# ---------------------------------------------------------------- Summen
def compute_totals(lines: list[dict], tax_mode: str) -> dict:
    net = 0
    by_rate: dict[str, int] = defaultdict(int)
    for ln in lines:
        t = line_total_cents(ln.get("quantity") or 0, int(ln.get("unit_price_cents") or 0))
        ln["total_cents"] = t
        net += t
        rate = "0" if tax_mode == "kleinunternehmer" else str(to_decimal(ln.get("tax_rate") or 0).normalize())
        by_rate[rate] += t
    tax = 0
    tax_breakdown = []
    for rate, base in sorted(by_rate.items()):
        r = to_decimal(rate)
        amount = int((Decimal(base) * r / 100).quantize(Decimal("1"), rounding="ROUND_HALF_UP")) if r else 0
        tax += amount
        tax_breakdown.append({"rate": rate, "base_cents": base, "tax_cents": amount})
    return {"net": net, "tax": tax, "gross": net + tax, "breakdown": tax_breakdown}


# ---------------------------------------------------------------- Platzhalter
class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def placeholders(con: sqlite3.Connection, doc: dict, number: str | None = None, extra: dict | None = None) -> dict:
    comp = company(con)
    cust = repo.get_customer(con, doc["customer_id"]) if doc.get("customer_id") else {}
    proj = repo.get_project(con, doc["project_id"]) if doc.get("project_id") else None
    ref = get_document(con, doc["ref_document_id"]) if doc.get("ref_document_id") else None
    totals_gross = doc.get("total_gross_cents") or 0
    d = {
        "briefanrede": (cust or {}).get("letter_salutation") or repo.default_letter_salutation(cust or {}),
        "kunde": repo.customer_display_name(cust or {}),
        "zahlungsziel": get_setting(con, "invoice.payment_days"),
        "faellig": fmt_date(doc.get("due_date")),
        "gueltig_bis": fmt_date(doc.get("valid_until")),
        "datum": fmt_date(doc.get("doc_date")),
        "leistungsdatum": fmt_date(doc.get("service_date")),
        "iban": comp.get("iban", ""),
        "bic": comp.get("bic", ""),
        "inhaber": comp.get("owner", ""),
        "firma": comp.get("name", ""),
        "belegnr": number or doc.get("number") or "(wird beim Festschreiben vergeben)",
        "betrag": fmt_eur(totals_gross),
        "objekt_adresse": repo.project_address(proj),
        "projekt": (proj or {}).get("name", ""),
        "ref_belegnr": (ref or {}).get("number", ""),
        "ref_datum": fmt_date((ref or {}).get("doc_date")),
    }
    if extra:
        d.update(extra)
    return d


def render(text: str, values: dict) -> str:
    return (text or "").format_map(_SafeDict(values)) if "{" in (text or "") else (text or "")


# ---------------------------------------------------------------- Entwürfe
def new_draft(con: sqlite3.Connection, doc_type: str, customer_id: int | None = None,
              project_id: int | None = None) -> dict:
    today = date.today()
    tax_mode = get_setting(con, "tax.mode", "kleinunternehmer")
    doc = {
        "doc_type": doc_type,
        "customer_id": customer_id,
        "project_id": project_id,
        "doc_date": today.isoformat(),
        "service_date": today.isoformat() if doc_type != "angebot" else None,
        "service_date_to": None,
        "due_date": (today + timedelta(days=int(get_setting(con, "invoice.payment_days", "14")))).isoformat()
        if doc_type == "rechnung" else None,
        "valid_until": (today + timedelta(days=int(get_setting(con, "offer.valid_days", "30")))).isoformat()
        if doc_type == "angebot" else None,
        "subject": "",
        "salutation_text": repo.default_text(con, "anrede"),
        "header_text": repo.default_text(con, "kopf_angebot" if doc_type == "angebot" else
                                         "storno" if doc_type == "storno" else "kopf_rechnung"),
        "footer_text": repo.default_text(con, "fuss_angebot" if doc_type == "angebot" else "fuss_rechnung")
        if doc_type != "storno" else "",
        "notes_text": _default_notes(con, tax_mode, doc_type),
        "tax_mode": tax_mode,
        "zugferd": int(get_setting(con, "invoice.zugferd_default", "0")) if doc_type != "angebot" else 0,
        "girocode": int(get_setting(con, "invoice.girocode", "1")) if doc_type == "rechnung" else 0,
        "ref_document_id": None,
    }
    gruss = repo.default_text(con, "gruss")
    if gruss:
        doc["footer_text"] = (doc["footer_text"] + "\n\n" + gruss).strip()
    return doc


def _default_notes(con, tax_mode: str, doc_type: str) -> str:
    if tax_mode == "kleinunternehmer":
        txt = repo.default_text(con, "hinweis")
        if doc_type == "angebot":
            # Leistungsdatum-Hinweis ist bei Angeboten nicht sinnvoll
            txt = "\n".join(l for l in txt.splitlines() if "Leistungsdatum" not in l)
        return txt
    return "(Das Rechnungsdatum entspricht dem Leistungsdatum)" if doc_type != "angebot" else ""


def save_draft(con: sqlite3.Connection, doc: dict, lines: list[dict]) -> int:
    """Speichert einen Entwurf (neu oder bestehend). Gibt die ID zurück."""
    totals = compute_totals(lines, doc.get("tax_mode", "kleinunternehmer"))
    with transaction(con):
        values = {k: doc.get(k) for k in DOC_EDIT_FIELDS}
        values.update(total_net_cents=totals["net"], total_tax_cents=totals["tax"], total_gross_cents=totals["gross"])
        doc_id = doc.get("id")
        if doc_id:
            cur = get_document(con, doc_id)
            if cur["status"] != "entwurf":
                raise ValueError("Festgeschriebene Belege können nicht geändert werden.")
            sets = ", ".join(f"{k}=?" for k in values)
            con.execute(f"UPDATE document SET {sets}, updated_at=datetime('now','localtime') WHERE id=?",
                        [*values.values(), doc_id])
            con.execute("DELETE FROM document_line WHERE document_id=?", (doc_id,))
            action = "update_draft"
        else:
            values["doc_type"] = doc["doc_type"]
            cols = ", ".join(values)
            cur = con.execute(f"INSERT INTO document({cols}) VALUES ({', '.join('?' for _ in values)})",
                              list(values.values()))
            doc_id = cur.lastrowid
            action = "create_draft"
        for i, ln in enumerate(lines, start=1):
            con.execute(
                "INSERT INTO document_line(document_id, pos, title, description, quantity, unit, unit_price_cents, tax_rate, total_cents)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, i, ln.get("title", ""), ln.get("description", ""), str(to_decimal(ln.get("quantity") or 0)),
                 ln.get("unit", ""), int(ln.get("unit_price_cents") or 0), str(ln.get("tax_rate") or "0"),
                 ln["total_cents"]),
            )
        audit.log(con, action, "document", doc_id, {"type": doc["doc_type"], "gross": totals["gross"]})
    return doc_id


def delete_draft(con: sqlite3.Connection, doc_id: int) -> None:
    with transaction(con):
        d = get_document(con, doc_id)
        if not d or d["status"] != "entwurf":
            raise ValueError("Nur Entwürfe können gelöscht werden.")
        con.execute("DELETE FROM document_line WHERE document_id=?", (doc_id,))
        con.execute("DELETE FROM document WHERE id=?", (doc_id,))
        audit.log(con, "delete_draft", "document", doc_id)


def copy_as_draft(con: sqlite3.Connection, src_id: int, doc_type: str | None = None) -> int:
    """Kopie eines Belegs als neuen Entwurf (z.B. Angebot -> Rechnung)."""
    src = get_document(con, src_id)
    target_type = doc_type or src["doc_type"]
    doc = new_draft(con, target_type, src["customer_id"], src["project_id"])
    doc["subject"] = src["subject"]
    doc["tax_mode"] = src["tax_mode"]
    if target_type == "rechnung" and src["doc_type"] == "angebot":
        doc["ref_document_id"] = src_id
    lines = [{k: ln[k] for k in LINE_FIELDS} for ln in get_lines(con, src_id)]
    return save_draft(con, doc, lines)


# ---------------------------------------------------------------- Prüfung §14 UStG
def validate(con: sqlite3.Connection, doc: dict, lines: list[dict]) -> list[str]:
    problems = []
    comp = company(con)
    for key, label in [("name", "Firmenname"), ("street", "Firmenanschrift"), ("zip", "Firmen-PLZ"), ("city", "Firmenort")]:
        if not comp.get(key):
            problems.append(f"{label} fehlt (Einstellungen → Firma).")
    if doc["doc_type"] != "angebot" and not (comp.get("tax_number") or comp.get("vat_id")):
        problems.append("Steuernummer oder USt-IdNr. fehlt (Einstellungen → Firma).")
    if not doc.get("customer_id"):
        problems.append("Kein Kunde gewählt.")
    else:
        c = repo.get_customer(con, doc["customer_id"]) or {}
        if not (c.get("last_name") or c.get("company")):
            problems.append("Kundenname fehlt.")
        if not (c.get("street") and c.get("zip") and c.get("city")):
            problems.append("Kundenanschrift unvollständig (Straße, PLZ, Ort).")
    if not doc.get("doc_date"):
        problems.append("Belegdatum fehlt.")
    if doc["doc_type"] in ("rechnung", "storno") and not doc.get("service_date"):
        problems.append("Leistungsdatum fehlt (§14 Abs. 4 Nr. 6 UStG).")
    if not lines:
        problems.append("Keine Positionen.")
    for i, ln in enumerate(lines, 1):
        if not (ln.get("title") or ln.get("description")):
            problems.append(f"Position {i}: Bezeichnung fehlt (Menge und Art der Leistung).")
    if doc.get("tax_mode") == "kleinunternehmer" and doc["doc_type"] != "angebot" and "19" not in (doc.get("notes_text") or ""):
        problems.append("Hinweis auf die Steuerbefreiung nach § 19 UStG fehlt im Hinweistext.")
    if doc.get("service_date_to") and doc.get("service_date") and doc["service_date_to"] < doc["service_date"]:
        problems.append("Leistungszeitraum: Ende liegt vor Beginn.")
    return problems


# ---------------------------------------------------------------- Festschreiben
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path | str) -> str:
    return _sha256(Path(path))


def archive_path_for(number: str, doc_date: str, doc_type: str, ext: str = ".pdf") -> Path:
    year = (doc_date or str(date.today()))[:4]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", number)
    p = config.archive_dir() / year / f"{DOC_TYPE_LABEL[doc_type]}-{safe}{ext}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def build_render_data(con: sqlite3.Connection, doc: dict, lines: list[dict], number: str | None,
                      final: bool) -> dict:
    """Alle Daten, die PDF und ZUGFeRD benötigen (Schnappschuss)."""
    cust = repo.get_customer(con, doc["customer_id"]) if doc.get("customer_id") else {}
    if doc.get("recipient_json"):
        recipient = json.loads(doc["recipient_json"])
    else:
        recipient = dict(cust or {})
        recipient["lines"] = repo.recipient_lines(cust or {})
    comp = json.loads(doc["company_json"]) if doc.get("company_json") else company(con)
    values = placeholders(con, doc, number)
    totals = compute_totals([dict(l) for l in lines], doc.get("tax_mode", "kleinunternehmer"))
    ref = get_document(con, doc["ref_document_id"]) if doc.get("ref_document_id") else None
    rendered_lines = []
    for ln in lines:
        l2 = dict(ln)
        l2["title"] = render(ln.get("title", ""), values)
        l2["description"] = render(ln.get("description", ""), values)
        l2["total_cents"] = line_total_cents(ln.get("quantity") or 0, int(ln.get("unit_price_cents") or 0))
        rendered_lines.append(l2)
    return {
        "doc": dict(doc),
        "doc_type": doc["doc_type"],
        "label": DOC_TYPE_LABEL[doc["doc_type"]],
        "number": number,
        "final": final,
        "company": comp,
        "recipient": recipient,
        "salutation": render(doc.get("salutation_text", ""), values),
        "header": render(doc.get("header_text", ""), values),
        "footer": render(doc.get("footer_text", ""), values),
        "notes": render(doc.get("notes_text", ""), values),
        "lines": rendered_lines,
        "totals": totals,
        "ref": ref,
        "logo": comp.get("logo", ""),
    }


def finalize(con: sqlite3.Connection, doc_id: int, *, pdf_writer=None, zugferd_writer=None) -> dict:
    """Vergibt die Nummer, erzeugt das Archiv-PDF, speichert Hashes und schreibt fest – alles atomar.

    pdf_writer(render_data, path) und zugferd_writer(render_data, pdf_path) -> xml_bytes sind injizierbar (Tests).
    """
    if pdf_writer is None:
        from app.output.pdf_invoice import write_document_pdf as pdf_writer
    if zugferd_writer is None:
        from app.output.zugferd import embed_zugferd as zugferd_writer

    written: list[Path] = []
    try:
        with transaction(con):
            doc = get_document(con, doc_id)
            if not doc or doc["status"] != "entwurf":
                raise ValueError("Beleg ist bereits festgeschrieben.")
            lines = get_lines(con, doc_id)
            problems = validate(con, doc, lines)
            if problems:
                raise ValidationError(problems)
            on = datetime.strptime(doc["doc_date"], "%Y-%m-%d").date()
            number = numbering.allocate(con, doc["doc_type"], on)

            cust = repo.get_customer(con, doc["customer_id"])
            recipient = dict(cust)
            recipient["lines"] = repo.recipient_lines(cust)
            doc["recipient_json"] = dumps(recipient)
            doc["company_json"] = dumps(company(con))
            data = build_render_data(con, doc, lines, number, final=True)

            pdf_path = archive_path_for(number, doc["doc_date"], doc["doc_type"])
            if pdf_path.exists():
                raise FileExistsError(f"Archivdatei existiert bereits: {pdf_path}")
            written.append(pdf_path)
            pdf_writer(data, pdf_path)
            xml_hash = None
            if doc.get("zugferd") and doc["doc_type"] in ("rechnung", "storno"):
                xml_bytes = zugferd_writer(data, pdf_path)
                xml_hash = hashlib.sha256(xml_bytes).hexdigest()
                xml_path = pdf_path.with_suffix(".xml")
                written.append(xml_path)
                xml_path.write_bytes(xml_bytes)
            pdf_hash = _sha256(pdf_path)
            try:
                pdf_path.chmod(0o444)  # schreibgeschützt
            except OSError:
                pass

            # Platzhalter in Positionen fest übernehmen (solange der Beleg noch Entwurf ist)
            for ln in data["lines"]:
                con.execute("UPDATE document_line SET title=?, description=?, total_cents=? WHERE id=?",
                            (ln["title"], ln["description"], ln["total_cents"], ln["id"]))
            con.execute(
                """UPDATE document SET number=?, status='festgeschrieben', salutation_text=?, header_text=?,
                   footer_text=?, notes_text=?, recipient_json=?, company_json=?, total_net_cents=?,
                   total_tax_cents=?, total_gross_cents=?, pdf_path=?, pdf_sha256=?, xml_sha256=?,
                   finalized_at=datetime('now','localtime'), updated_at=datetime('now','localtime')
                   WHERE id=? AND status='entwurf'""",
                (number, data["salutation"], data["header"], data["footer"], data["notes"], doc["recipient_json"],
                 doc["company_json"], data["totals"]["net"], data["totals"]["tax"], data["totals"]["gross"],
                 str(pdf_path), pdf_hash, xml_hash, doc_id),
            )
            audit.log(con, "finalize", "document", doc_id,
                      {"number": number, "type": doc["doc_type"], "gross": data["totals"]["gross"],
                       "pdf_sha256": pdf_hash, "xml_sha256": xml_hash})
    except BaseException:
        for p in written:
            try:
                if p.exists():
                    p.chmod(0o666)
                    p.unlink()
            except OSError:
                pass
        raise
    return get_document(con, doc_id)


def render_preview(con: sqlite3.Connection, doc: dict, lines: list[dict], path: Path) -> Path:
    from app.output.pdf_invoice import write_document_pdf
    data = build_render_data(con, doc, lines, doc.get("number"), final=doc.get("status") == "festgeschrieben")
    write_document_pdf(data, path)
    return path


# ---------------------------------------------------------------- Storno
def create_storno(con: sqlite3.Connection, invoice_id: int, *, finalize_now: bool = True, **writers) -> dict:
    inv = get_document(con, invoice_id)
    if not inv or inv["doc_type"] != "rechnung" or inv["status"] != "festgeschrieben":
        raise ValueError("Nur festgeschriebene Rechnungen können storniert werden.")
    if con.execute("SELECT 1 FROM document WHERE ref_document_id=? AND doc_type='storno'", (invoice_id,)).fetchone():
        raise ValueError("Zu dieser Rechnung existiert bereits eine Stornorechnung (ggf. als Entwurf).")
    doc = new_draft(con, "storno", inv["customer_id"], inv["project_id"])
    doc.update(ref_document_id=invoice_id, tax_mode=inv["tax_mode"], service_date=inv["service_date"],
               service_date_to=inv["service_date_to"], subject="",
               notes_text=inv["notes_text"], zugferd=inv["zugferd"], girocode=0)
    lines = []
    for ln in get_lines(con, invoice_id):
        l2 = {k: ln[k] for k in LINE_FIELDS}
        l2["quantity"] = str(-to_decimal(ln["quantity"]))
        lines.append(l2)
    draft_id = save_draft(con, doc, lines)
    if finalize_now:
        return finalize(con, draft_id, **writers)
    return get_document(con, draft_id)


def is_cancelled(con: sqlite3.Connection, invoice_id: int) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM document WHERE ref_document_id=? AND doc_type='storno' AND status='festgeschrieben'",
        (invoice_id,)).fetchone())


def verify_archive(con: sqlite3.Connection) -> list[str]:
    """Prüft, ob alle Archiv-PDFs vorhanden und unverändert sind."""
    problems = []
    for d in con.execute("SELECT id, number, pdf_path, pdf_sha256, import_file_path, import_sha256 FROM document WHERE status='festgeschrieben'"):
        for path, h in [(d["pdf_path"], d["pdf_sha256"]), (d["import_file_path"], d["import_sha256"])]:
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                problems.append(f"{d['number']}: Archivdatei fehlt ({p.name})")
            elif h and _sha256(p) != h:
                problems.append(f"{d['number']}: Archivdatei wurde verändert ({p.name})")
    return problems
