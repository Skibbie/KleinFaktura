"""Erzeugt Beispiel-E-Rechnungen (ZUGFeRD/Factur-X im PDF/A-3) mit Dummy-Daten für die Prüfung in der CI.

Geschrieben werden drei Belege über den normalen Weg des Programms (Entwurf → Festschreiben):
Kleinunternehmer-Rechnung (Absender ohne USt-IdNr., der häufigste Fall), Rechnung mit Regelbesteuerung (7 % und 19 % gemischt) und eine Stornorechnung.
Gearbeitet wird in einem eigenen temporären Datenordner – echte Daten werden nie berührt.

Aufruf: python tools/make_einvoice_samples.py <Zielordner>
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COMPANY = {"name": "Musterbüro Max Muster", "line1": "Planungsbüro", "owner": "Max Muster", "street": "Beraterweg 1",
           "zip": "10115", "city": "Berlin", "country": "DE", "phone": "030 123456", "email": "info@example.org",
           "tax_number": "12/345/67890", "iban": "DE02 1203 0000 0000 2020 51",
           "bic": "BYLADEM1001", "bank": "Musterbank"}
CUSTOMER = {"company": "Beispiel GmbH", "salutation": "Frau", "first_name": "Erika", "last_name": "Mustermann",
            "street": "Musterweg 12", "zip": "12345", "city": "Musterstadt", "country": "DE",
            "email": "rechnung@example.com", "vat_id": "DE987654321", "buyer_reference": "04011000-12345-06"}


def build(target: Path) -> list[Path]:
    from app.db import database, repo
    from app.services import documents

    con = database.connect()
    try:
        database.init_db(con)
        for key, value in COMPANY.items():
            database.set_setting(con, f"company.{key}", value)
        customer_id = repo.save_customer(con, dict(CUSTOMER))

        def invoice(tax_mode: str, lines: list[dict]) -> dict:
            doc = documents.new_draft(con, "rechnung", customer_id)
            doc.update(doc_date="2026-10-01", service_date="2026-09-01", service_date_to="2026-09-30",
                       due_date="2026-10-15", tax_mode=tax_mode, zugferd=1)
            if tax_mode == "regel":
                doc["notes_text"] = ""   # der Standardhinweis nennt § 19 UStG und passt nur für Kleinunternehmer
            return documents.finalize(con, documents.save_draft(con, doc, lines))

        ku = invoice("kleinunternehmer", [
            {"title": "Beratung", "description": "nach Aufwand", "quantity": "6.5", "unit": "Stunden",
             "unit_price_cents": 12000, "tax_rate": "0"},
            {"title": "Fahrtkosten", "description": "", "quantity": "84", "unit": "km", "unit_price_cents": 30,
             "tax_rate": "0"}])
        storno = documents.create_storno(con, ku["id"])
        database.set_setting(con, "company.vat_id", "DE123456789")   # zweiter Fall: Absender mit USt-IdNr.
        regel = invoice("regel", [
            {"title": "Planungsleistung", "description": "Zeile 1\nZeile 2", "quantity": "3", "unit": "Stunden",
             "unit_price_cents": 9950, "tax_rate": "19"},
            {"title": "Fachbuch", "description": "", "quantity": "2", "unit": "Stück", "unit_price_cents": 2495,
             "tax_rate": "7"}])
        target.mkdir(parents=True, exist_ok=True)
        out = []
        for name, doc in [("kleinunternehmer", ku), ("regelbesteuerung", regel), ("storno", storno)]:
            dest = target / f"{name}.pdf"
            shutil.copyfile(doc["pdf_path"], dest)
            out.append(dest)
        return out
    finally:
        con.close()


def main(args: list[str]) -> int:
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    target = Path(args[0]).resolve()
    with tempfile.TemporaryDirectory(prefix="kleinfaktura-beispiele-", ignore_cleanup_errors=True) as tmp:
        os.environ["FAKTURA_DATA_DIR"] = tmp
        for path in build(target):
            print(path)
        for f in Path(tmp).rglob("*"):   # Archivdateien sind schreibgeschützt und ließen sich sonst nicht aufräumen
            if f.is_file():
                f.chmod(0o666)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
