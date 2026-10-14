"""ZUGFeRD 2.x / Factur-X (Profil EN 16931) – CII-XML erzeugen und als PDF/A-3 einbetten.

Kleinunternehmer (§ 19 UStG): Steuerkategorie 'E' (steuerbefreit), 0 %, Befreiungsgrund als Text.
Stornorechnung: TypeCode 381 (Gutschrift/Korrektur) mit positiven Beträgen und Verweis auf die Ursprungsrechnung.
"""
from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from app import config
from app.db import repo
from app.money import cents_to_decimal, to_decimal

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)

KU_REASON = "Kleinunternehmer gemäß § 19 UStG – Umsatzsteuer wird nicht berechnet"

UNIT_CODES = {
    "stunden": "HUR", "stunde": "HUR", "std": "HUR", "std.": "HUR", "h": "HUR",
    "stück": "H87", "stk": "H87", "stk.": "H87",
    "km": "KMT", "tage": "DAY", "tag": "DAY", "pauschal": "LS", "psch": "LS", "psch.": "LS",
    "m²": "MTK", "qm": "MTK", "m2": "MTK", "m": "MTR", "monat": "MON", "monate": "MON",
}


def _q(tag: str) -> str:
    pfx, name = tag.split(":")
    return f"{{{NS[pfx]}}}{name}"


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, _q(tag), {k: v for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _amt(cents: int) -> str:
    return f"{cents_to_decimal(cents):.2f}"


def _date(parent, tag, iso: str):
    wrap = _sub(parent, tag)
    _sub(wrap, "udt:DateTimeString", iso.replace("-", ""), format="102")
    return wrap


def unit_code(unit: str) -> str:
    return UNIT_CODES.get((unit or "").strip().lower(), "C62")


def _party(parent, tag, name, street, zip_, city, country, email=None, tax_number=None, vat_id=None,
           contact=None, phone=None):
    p = _sub(parent, tag)
    _sub(p, "ram:Name", name)
    if contact or phone or (email and tag.endswith("SellerTradeParty")):
        c = _sub(p, "ram:DefinedTradeContact")
        if contact:
            _sub(c, "ram:PersonName", contact)
        if phone:
            _sub(_sub(c, "ram:TelephoneUniversalCommunication"), "ram:CompleteNumber", phone)
        if email:
            _sub(_sub(c, "ram:EmailURIUniversalCommunication"), "ram:URIID", email)
    addr = _sub(p, "ram:PostalTradeAddress")
    if zip_:
        _sub(addr, "ram:PostcodeCode", zip_)
    if street:
        _sub(addr, "ram:LineOne", street)
    if city:
        _sub(addr, "ram:CityName", city)
    code = repo.country_code(country)
    if code is None:
        # nie abschneiden/raten („Germany“ → GE = Georgien); validate() meldet das schon vor dem Festschreiben
        raise ValueError(f"Unbekanntes Land „{country}“ – bitte den Ländercode (z.B. DE, AT, CH) eintragen.")
    _sub(addr, "ram:CountryID", code)
    if email:
        _sub(_sub(p, "ram:URIUniversalCommunication"), "ram:URIID", email, schemeID="EM")
    if vat_id:
        _sub(_sub(p, "ram:SpecifiedTaxRegistration"), "ram:ID", vat_id, schemeID="VA")
    if tax_number:
        _sub(_sub(p, "ram:SpecifiedTaxRegistration"), "ram:ID", tax_number, schemeID="FC")
    return p


def build_cii_xml(data: dict) -> bytes:
    doc = data["doc"]
    comp = data["company"]
    rec = data["recipient"]
    is_storno = data["doc_type"] == "storno"
    sign = -1 if is_storno else 1
    ku = doc.get("tax_mode") == "kleinunternehmer"

    root = ET.Element(_q("rsm:CrossIndustryInvoice"))
    ctx = _sub(root, "rsm:ExchangedDocumentContext")
    _sub(_sub(ctx, "ram:GuidelineSpecifiedDocumentContextParameter"), "ram:ID", "urn:cen.eu:en16931:2017")

    ed = _sub(root, "rsm:ExchangedDocument")
    _sub(ed, "ram:ID", data["number"])
    _sub(ed, "ram:TypeCode", "381" if is_storno else "380")
    _date(ed, "ram:IssueDateTime", doc["doc_date"])
    if data.get("notes"):
        n = _sub(ed, "ram:IncludedNote")
        _sub(n, "ram:Content", data["notes"])
    if comp.get("name"):
        n = _sub(ed, "ram:IncludedNote")
        _sub(n, "ram:Content", f"{comp.get('name')}, {comp.get('street')}, {comp.get('zip')} {comp.get('city')}")
        _sub(n, "ram:SubjectCode", "REG")

    tx = _sub(root, "rsm:SupplyChainTradeTransaction")
    line_sum = 0
    for i, ln in enumerate(data["lines"], 1):
        qty = to_decimal(ln.get("quantity")) * sign
        total = int(ln["total_cents"]) * sign
        line_sum += total
        li = _sub(tx, "ram:IncludedSupplyChainTradeLineItem")
        _sub(_sub(li, "ram:AssociatedDocumentLineDocument"), "ram:LineID", str(i))
        prod = _sub(li, "ram:SpecifiedTradeProduct")
        _sub(prod, "ram:Name", (ln.get("title") or ln.get("description") or "Leistung").split("\n")[0][:200])
        if ln.get("description") and ln.get("title"):
            _sub(prod, "ram:Description", ln["description"])
        agr = _sub(li, "ram:SpecifiedLineTradeAgreement")
        _sub(_sub(agr, "ram:NetPriceProductTradePrice"), "ram:ChargeAmount", _amt(int(ln.get("unit_price_cents") or 0)))
        dl = _sub(li, "ram:SpecifiedLineTradeDelivery")
        _sub(dl, "ram:BilledQuantity", f"{qty.normalize():f}" if qty != qty.to_integral_value() else f"{qty:.0f}",
             unitCode=unit_code(ln.get("unit")))
        st = _sub(li, "ram:SpecifiedLineTradeSettlement")
        tax = _sub(st, "ram:ApplicableTradeTax")
        _sub(tax, "ram:TypeCode", "VAT")
        rate = Decimal(0) if ku else to_decimal(ln.get("tax_rate") or 0)
        _sub(tax, "ram:CategoryCode", "E" if ku else ("S" if rate > 0 else "Z"))
        _sub(tax, "ram:RateApplicablePercent", f"{rate:.2f}")
        _sub(_sub(st, "ram:SpecifiedTradeSettlementLineMonetarySummation"), "ram:LineTotalAmount", _amt(total))

    ag = _sub(tx, "ram:ApplicableHeaderTradeAgreement")
    if rec.get("buyer_reference"):
        _sub(ag, "ram:BuyerReference", rec["buyer_reference"])
    _party(ag, "ram:SellerTradeParty", comp.get("name"), comp.get("street"), comp.get("zip"), comp.get("city"),
           comp.get("country"), email=comp.get("email"), tax_number=comp.get("tax_number"), vat_id=comp.get("vat_id"),
           contact=comp.get("owner"), phone=comp.get("phone"))
    buyer_name = rec.get("company") or " ".join(x for x in [rec.get("first_name"), rec.get("last_name")] if x) \
        or (rec.get("lines") or ["Kunde"])[0]
    _party(ag, "ram:BuyerTradeParty", buyer_name, rec.get("street"), rec.get("zip"), rec.get("city"), rec.get("country"),
           email=rec.get("email") or None, vat_id=rec.get("vat_id") or None)

    dv = _sub(tx, "ram:ApplicableHeaderTradeDelivery")
    if doc.get("service_date"):
        ev = _sub(dv, "ram:ActualDeliverySupplyChainEvent")
        _date(ev, "ram:OccurrenceDateTime", doc["service_date"])

    se = _sub(tx, "ram:ApplicableHeaderTradeSettlement")
    _sub(se, "ram:PaymentReference", data["number"])
    _sub(se, "ram:InvoiceCurrencyCode", "EUR")
    if comp.get("iban") and not is_storno:
        pm = _sub(se, "ram:SpecifiedTradeSettlementPaymentMeans")
        _sub(pm, "ram:TypeCode", "58")
        acc = _sub(pm, "ram:PayeePartyCreditorFinancialAccount")
        _sub(acc, "ram:IBANID", comp["iban"].replace(" ", ""))
        _sub(acc, "ram:AccountName", comp.get("name", ""))
        if comp.get("bic"):
            _sub(_sub(pm, "ram:PayeeSpecifiedCreditorFinancialInstitution"), "ram:BICID", comp["bic"].replace(" ", ""))

    tax_total = 0
    for b in data["totals"]["breakdown"]:
        t = _sub(se, "ram:ApplicableTradeTax")
        tax_cents = int(b["tax_cents"]) * sign
        tax_total += tax_cents
        _sub(t, "ram:CalculatedAmount", _amt(tax_cents))
        _sub(t, "ram:TypeCode", "VAT")
        rate = to_decimal(b["rate"])
        if ku:
            _sub(t, "ram:ExemptionReason", KU_REASON)
        _sub(t, "ram:BasisAmount", _amt(int(b["base_cents"]) * sign))
        _sub(t, "ram:CategoryCode", "E" if ku else ("S" if rate > 0 else "Z"))
        _sub(t, "ram:RateApplicablePercent", f"{rate:.2f}")

    if doc.get("service_date") and doc.get("service_date_to") and doc["service_date_to"] != doc["service_date"]:
        per = _sub(se, "ram:BillingSpecifiedPeriod")
        _date(per, "ram:StartDateTime", doc["service_date"])
        _date(per, "ram:EndDateTime", doc["service_date_to"])

    if is_storno:
        pt = _sub(se, "ram:SpecifiedTradePaymentTerms")
        _sub(pt, "ram:Description", "Der Betrag wird erstattet bzw. mit offenen Forderungen verrechnet.")
    else:
        pt = _sub(se, "ram:SpecifiedTradePaymentTerms")
        if doc.get("due_date"):
            from app.money import fmt_date
            _sub(pt, "ram:Description", f"Zahlbar ohne Abzug bis {fmt_date(doc['due_date'])}")
            _date(pt, "ram:DueDateDateTime", doc["due_date"])
        else:
            _sub(pt, "ram:Description", "Zahlbar sofort ohne Abzug")

    ms = _sub(se, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _sub(ms, "ram:LineTotalAmount", _amt(line_sum))
    _sub(ms, "ram:TaxBasisTotalAmount", _amt(line_sum))
    _sub(ms, "ram:TaxTotalAmount", _amt(tax_total), currencyID="EUR")
    _sub(ms, "ram:GrandTotalAmount", _amt(line_sum + tax_total))
    _sub(ms, "ram:DuePayableAmount", _amt(line_sum + tax_total))

    if is_storno and data.get("ref"):
        ref = _sub(se, "ram:InvoiceReferencedDocument")
        _sub(ref, "ram:IssuerAssignedID", data["ref"]["number"])
        _date(ref, "ram:FormattedIssueDateTime", data["ref"]["doc_date"]).find(_q("udt:DateTimeString")).tag = \
            _q("qdt:DateTimeString")

    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue()


def _make_pdfa_ready(pdf_path: Path) -> bytes:
    """Ergänzt OutputIntent (sRGB) und Sprache, damit das PDF die PDF/A-3-Anforderungen erfüllt.
    Die XMP-Metadaten (pdfaid:part=3, conformance=B) setzt anschließend factur-x."""
    import pikepdf
    with pikepdf.open(str(pdf_path)) as pdf:
        icc = config.srgb_icc_path()
        if icc and "/OutputIntents" not in pdf.Root:
            stream = pikepdf.Stream(pdf, icc.read_bytes())
            stream["/N"] = 3
            intent = pikepdf.Dictionary(
                Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFA1,
                OutputConditionIdentifier=pikepdf.String("sRGB IEC61966-2.1"),
                Info=pikepdf.String("sRGB IEC61966-2.1"), DestOutputProfile=stream,
            )
            pdf.Root.OutputIntents = pikepdf.Array([intent])
        pdf.Root.Lang = pikepdf.String("de-DE")
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


def embed_zugferd(data: dict, pdf_path: Path | str) -> bytes:
    """Erzeugt das XML, prüft es gegen das XSD und bettet es in das PDF ein (überschreibt pdf_path)."""
    import facturx
    pdf_path = Path(pdf_path)
    xml = build_cii_xml(data)
    facturx.xml_check_xsd(xml, flavor="factur-x", level="en16931")
    pdf_bytes = _make_pdfa_ready(pdf_path)
    comp = data["company"]
    meta = {
        "author": comp.get("name", ""),
        "keywords": "Factur-X, ZUGFeRD, Rechnung",
        "title": f"{data['label']} {data['number']}",
        "subject": f"{data['label']} {data['number']} von {comp.get('name', '')}",
    }
    result = facturx.generate_from_binary(pdf_bytes, xml, flavor="factur-x", level="en16931", check_xsd=True,
                                          pdf_metadata=meta, lang="de-DE")
    pdf_path.write_bytes(result)
    return xml


def build_xrechnung_xml(data: dict) -> bytes:
    """XRechnung (CII-Syntax) – gleiche Struktur, anderer Spezifikationsbezeichner."""
    xml = build_cii_xml(data).decode("utf-8")
    xml = xml.replace("urn:cen.eu:en16931:2017</ram:ID>",
                      "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</ram:ID>", 1)
    return xml.encode("utf-8")
