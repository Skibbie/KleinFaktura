"""PDF-Ausgabe für Angebote, Rechnungen, Stornos und Mahnungen (ReportLab).

Layout nach der bisherigen Vorlage: Logo links, Absender rechts, Anschriftfeld (DIN 5008 Form B),
Anrede + Datum, Einleitung, Positionstabelle, Summe, Schlusstext, Gruß, Hinweise.
Alle Schriften werden eingebettet (Carlito ≙ Calibri), damit die Datei PDF/A-tauglich ist.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import segno
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether, NextPageTemplate, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from app import config
from app.money import fmt_date, fmt_eur, fmt_qty

PAGE_W, PAGE_H = A4
LEFT = 20 * mm
RIGHT = PAGE_W - 20 * mm
CONTENT_W = RIGHT - LEFT
GREY = colors.Color(0.35, 0.35, 0.35)
GREEN = colors.Color(0.21, 0.49, 0.20)

_FONTS_REGISTERED = False
F_REG, F_BOLD, F_IT = "Faktura", "Faktura-Bold", "Faktura-Italic"


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    paths = config.font_paths()
    if "regular" not in paths:
        raise RuntimeError("Keine einbettbare Schrift gefunden (Carlito/DejaVu/Arial).")
    pdfmetrics.registerFont(TTFont(F_REG, str(paths["regular"])))
    pdfmetrics.registerFont(TTFont(F_BOLD, str(paths.get("bold", paths["regular"]))))
    pdfmetrics.registerFont(TTFont(F_IT, str(paths.get("italic", paths["regular"]))))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily(F_REG, normal=F_REG, bold=F_BOLD, italic=F_IT, boldItalic=F_BOLD)
    _FONTS_REGISTERED = True


def _styles():
    base = ParagraphStyle("base", fontName=F_REG, fontSize=10.5, leading=13.5, alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8, leading=10, textColor=GREY),
        "cell": ParagraphStyle("cell", parent=base, fontSize=10, leading=12),
        "cellr": ParagraphStyle("cellr", parent=base, fontSize=10, leading=12, alignment=TA_RIGHT),
        "head": ParagraphStyle("head", parent=base, fontName=F_BOLD, fontSize=10, leading=12),
        "headr": ParagraphStyle("headr", parent=base, fontName=F_BOLD, fontSize=10, leading=12, alignment=TA_RIGHT),
        "title": ParagraphStyle("title", parent=base, fontName=F_BOLD, fontSize=12.5, leading=15),
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT),
        "note": ParagraphStyle("note", parent=base, fontSize=9.5, leading=12),
    }


def _p(text: str, style) -> Paragraph:
    return Paragraph(escape(text or "").replace("\n", "<br/>"), style)


# ---------------------------------------------------------------- GiroCode
def epc_payload(name: str, iban: str, bic: str, amount_cents: int, reference: str) -> str:
    """EPC069-12 (GiroCode), Version 002 – BIC optional."""
    iban = (iban or "").replace(" ", "").upper()
    amount = f"EUR{amount_cents / 100:.2f}" if amount_cents and amount_cents > 0 else ""
    lines = ["BCD", "002", "1", "SCT", (bic or "").replace(" ", "").upper(), (name or "")[:70], iban, amount,
             "", "", (reference or "")[:140]]
    return "\n".join(lines)


class QRFlowable(Flowable):
    def __init__(self, payload: str, size: float = 24 * mm, caption: str = "GiroCode"):
        super().__init__()
        self.qr = segno.make(payload, error="m", micro=False)
        self.size = size
        self.caption = caption
        self.width = size
        self.height = size + (9 if caption else 0)

    def draw(self):
        c = self.canv
        matrix = list(self.qr.matrix)
        n = len(matrix)
        quiet = 2
        cell = self.size / (n + 2 * quiet)
        y0 = self.height - self.size
        c.saveState()
        c.setFillColor(colors.black)
        for r, row in enumerate(matrix):
            for col, v in enumerate(row):
                if v:
                    c.rect((col + quiet) * cell, y0 + (n - 1 - r + quiet) * cell, cell, cell, stroke=0, fill=1)
        if self.caption:
            c.setFont(F_REG, 7)
            c.setFillColor(GREY)
            c.drawCentredString(self.size / 2, 0, self.caption)
        c.restoreState()


# ---------------------------------------------------------------- Seitenrahmen
class _NumberedCanvas(rl_canvas.Canvas):
    """Ermöglicht 'Seite x von y'."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            if total > 1:
                self.setFont(F_REG, 7.5)
                self.setFillColor(GREY)
                self.drawRightString(RIGHT, 10 * mm, f"Seite {self._pageNumber} von {total}")
            super().showPage()
        super().save()


def _draw_letterhead(c, data: dict, first: bool):
    comp = data["company"]
    if first:
        logo = data.get("logo")
        if logo and Path(logo).exists():
            w = 68 * mm
            h = w * 793 / 2196
            try:
                from reportlab.lib.utils import ImageReader
                img = ImageReader(logo)
                iw, ih = img.getSize()
                h = w * ih / iw
                c.drawImage(img, LEFT - 2 * mm, PAGE_H - 15 * mm - h, width=w, height=h, mask=None)
            except Exception:
                pass
        c.setFont(F_REG, 10)
        c.setFillColor(colors.black)
        y = PAGE_H - 17 * mm
        lines = [comp.get("line1"), comp.get("owner"), comp.get("street"),
                 " ".join(x for x in [comp.get("zip"), comp.get("city")] if x), comp.get("phone"),
                 comp.get("email"), comp.get("web")]
        for ln in [l for l in lines if l]:
            c.drawRightString(RIGHT, y, ln)
            y -= 12.2
        if comp.get("tax_number"):
            c.drawRightString(RIGHT, y, f"Steuernr.: {comp['tax_number']}")
            y -= 12.2
        if comp.get("vat_id"):
            c.drawRightString(RIGHT, y, f"USt-IdNr.: {comp['vat_id']}")
        # Rücksendeangabe + Anschriftfeld (DIN 5008 Form B: 45 mm von oben)
        sender = " · ".join(x for x in [comp.get("owner") or comp.get("name"), comp.get("street"),
                                         " ".join(x for x in [comp.get("zip"), comp.get("city")] if x)] if x)
        c.setFont(F_REG, 7)
        c.setFillColor(GREY)
        c.drawString(LEFT, PAGE_H - 50 * mm, sender)
        c.setStrokeColor(GREY)
        c.setLineWidth(0.3)
        c.line(LEFT, PAGE_H - 50.8 * mm, LEFT + c.stringWidth(sender, F_REG, 7), PAGE_H - 50.8 * mm)
        c.setFillColor(colors.black)
        c.setFont(F_REG, 11)
        y = PAGE_H - 57 * mm
        for ln in data["recipient"].get("lines", [])[:6]:
            c.drawString(LEFT, y, ln)
            y -= 14
        # Falzmarken
        c.setStrokeColor(colors.Color(0.6, 0.6, 0.6))
        c.setLineWidth(0.3)
        for ym in (105, 210):
            c.line(4 * mm, PAGE_H - ym * mm, 8 * mm, PAGE_H - ym * mm)
        c.line(3 * mm, PAGE_H / 2, 9 * mm, PAGE_H / 2)
    # Fußzeile auf jeder Seite
    c.setStrokeColor(GREEN)
    c.setLineWidth(0.6)
    c.line(LEFT, 17 * mm, RIGHT, 17 * mm)
    c.setFont(F_REG, 7.5)
    c.setFillColor(GREY)
    parts1 = [comp.get("name"), comp.get("street"), " ".join(x for x in [comp.get("zip"), comp.get("city")] if x)]
    parts2 = [f"IBAN {comp['iban']}" if comp.get("iban") else "", f"BIC {comp['bic']}" if comp.get("bic") else "",
              comp.get("bank"), f"Steuernr. {comp['tax_number']}" if comp.get("tax_number") else ""]
    c.drawString(LEFT, 13.5 * mm, " · ".join(p for p in parts1 if p))
    c.drawString(LEFT, 10 * mm, " · ".join(p for p in parts2 if p))
    if not data.get("final", True):
        c.saveState()
        c.setFillColor(colors.Color(0.85, 0.2, 0.2, alpha=0.18))
        c.setFont(F_BOLD, 90)
        c.translate(PAGE_W / 2, PAGE_H / 2)
        c.rotate(40)
        c.drawCentredString(0, 0, "ENTWURF")
        c.restoreState()


def _build_doc(path: Path, data: dict, title: str) -> BaseDocTemplate:
    comp = data["company"]
    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=LEFT, rightMargin=20 * mm, topMargin=20 * mm,
                          bottomMargin=22 * mm, title=title, author=comp.get("name", ""),
                          subject=title, creator=config.APP_NAME, lang="de-DE")
    first = Frame(LEFT, 22 * mm, CONTENT_W, PAGE_H - 22 * mm - 98 * mm, id="first", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    later = Frame(LEFT, 22 * mm, CONTENT_W, PAGE_H - 22 * mm - 20 * mm, id="later", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(id="First", frames=[first], onPage=lambda c, d: _draw_letterhead(c, data, True)),
        PageTemplate(id="Later", frames=[later], onPage=lambda c, d: _draw_letterhead(c, data, False)),
    ])
    return doc


# ---------------------------------------------------------------- Belege
def write_document_pdf(data: dict, path: Path | str) -> Path:
    _register_fonts()
    st = _styles()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = data["doc"]
    number = data.get("number") or "ENTWURF"
    title = f"{data['label']} {number}"
    doc = _build_doc(path, data, title)
    story: list = [NextPageTemplate("Later")]

    # Anrede + Datum
    date_txt = fmt_date(d.get("doc_date"))
    story.append(Table([[_p(data["salutation"], st["base"]), _p(date_txt, st["right"])]],
                       colWidths=[CONTENT_W - 35 * mm, 35 * mm],
                       style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                         ("VALIGN", (0, 0), (-1, -1), "TOP")])))
    story.append(Spacer(1, 4 * mm))
    if data["header"]:
        story.append(_p(data["header"], st["base"]))
        story.append(Spacer(1, 7 * mm))

    # Belegkopf
    story.append(_p(f"{data['label']} {number}", st["title"]))
    info = []
    cust = data["recipient"]
    if cust.get("customer_no"):
        info.append(f"Kundennr. {cust['customer_no']}")
    if data["doc_type"] != "angebot" and d.get("service_date"):
        if d.get("service_date_to") and d["service_date_to"] != d["service_date"]:
            info.append(f"Leistungszeitraum {fmt_date(d['service_date'])} – {fmt_date(d['service_date_to'])}")
        else:
            info.append(f"Leistungsdatum {fmt_date(d['service_date'])}")
    if data["doc_type"] == "angebot" and d.get("valid_until"):
        info.append(f"gültig bis {fmt_date(d['valid_until'])}")
    if data.get("ref") and data["doc_type"] == "storno":
        info.append(f"zu Rechnung {data['ref'].get('number')} vom {fmt_date(data['ref'].get('doc_date'))}")
    if d.get("subject"):
        info.append(d["subject"])
    if info:
        story.append(_p(" · ".join(info), st["small"]))
    story.append(Spacer(1, 1.5 * mm))

    # Positionen
    show_tax = d.get("tax_mode") == "regel"
    rows = [[_p("Pos.", st["head"]), _p("Beschreibung", st["head"]), _p("Anzahl", st["headr"]),
             _p("Einheit", st["head"]), _p("Einzelpreis", st["headr"]), _p("Gesamtpreis", st["headr"])]]
    for i, ln in enumerate(data["lines"], 1):
        desc = ""
        if ln.get("title"):
            desc = f"<b>{escape(ln['title'])}</b>"
        if ln.get("description"):
            desc += ("<br/>" if desc else "") + escape(ln["description"]).replace("\n", "<br/>")
        if show_tax:
            desc += f"<br/><font size=8 color='#666666'>USt {fmt_qty(ln.get('tax_rate') or 0).replace(',0', '')} %</font>"
        rows.append([_p(str(i), st["cellr"]), Paragraph(desc, st["cell"]), _p(fmt_qty(ln.get("quantity")), st["cellr"]),
                     _p(ln.get("unit", ""), st["cell"]), _p(fmt_eur(ln.get("unit_price_cents")), st["cellr"]),
                     _p(fmt_eur(ln.get("total_cents")), st["cellr"])])
    widths = [10 * mm, CONTENT_W - 88 * mm, 16 * mm, 18 * mm, 21 * mm, 23 * mm]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.95, 0.91)),
        ("VALIGN", (0, 0), (-1, 0), "TOP"),
        ("VALIGN", (0, 1), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)

    # Summen
    tot = data["totals"]
    sum_rows = []
    if show_tax:
        sum_rows.append(["Nettobetrag:", fmt_eur(tot["net"])])
        for b in tot["breakdown"]:
            if b["tax_cents"] or b["rate"] != "0":
                sum_rows.append([f"zzgl. USt {b['rate']} % auf {fmt_eur(b['base_cents'])}:", fmt_eur(b["tax_cents"])])
        sum_rows.append(["Gesamtbetrag:", fmt_eur(tot["gross"])])
    else:
        sum_rows.append(["Summe:", fmt_eur(tot["gross"])])
    st_rows = [[_p(a, st["headr"] if i == len(sum_rows) - 1 else st["cellr"]),
                _p(b, st["headr"] if i == len(sum_rows) - 1 else st["cellr"])] for i, (a, b) in enumerate(sum_rows)]
    ts = Table(st_rows, colWidths=[sum(widths[2:5]) + (30 * mm if show_tax else 0), widths[5]], hAlign="RIGHT")
    ts.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("LINEABOVE", (0, -1), (-1, -1), 1.2, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 1.2, colors.black),
        ("INNERGRID", (1, 0), (1, -1), 0.5, colors.black),
        ("LINEBEFORE", (1, 0), (1, -1), 0.5, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ts)
    story.append(Spacer(1, 6 * mm))

    # Schlusstext (+ GiroCode)
    footer_flow = _p(data["footer"], st["base"]) if data["footer"] else Spacer(1, 1)
    comp = data["company"]
    if d.get("girocode") and data["doc_type"] == "rechnung" and comp.get("iban") and tot["gross"] > 0:
        qr = QRFlowable(epc_payload(comp.get("name", ""), comp.get("iban", ""), comp.get("bic", ""), tot["gross"],
                                    f"Rechnung {number}"), caption="Bezahlen per GiroCode")
        story.append(Table([[footer_flow, qr]], colWidths=[CONTENT_W - 30 * mm, 30 * mm],
                           style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                             ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("ALIGN", (1, 0), (1, 0), "RIGHT")])))
    else:
        story.append(footer_flow)
    if data["notes"]:
        story.append(Spacer(1, 6 * mm))
        story.append(KeepTogether([_p(data["notes"], st["note"])]))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return path


# ---------------------------------------------------------------- Mahnungen
def write_dunning_pdf(data: dict, path: Path | str) -> Path:
    _register_fonts()
    st = _styles()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    inv = data["invoice"]
    data = dict(data, final=True)
    doc = _build_doc(path, data, f"{data['label']} zu Rechnung {inv['number']}")
    story: list = [NextPageTemplate("Later")]
    story.append(Table([[_p(data["salutation"], st["base"]), _p(fmt_date(data["date"]), st["right"])]],
                       colWidths=[CONTENT_W - 35 * mm, 35 * mm],
                       style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])))
    story.append(Spacer(1, 4 * mm))
    story.append(_p(f"{data['label']} zu Rechnung {inv['number']}", st["title"]))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(data["body"], st["base"]))
    story.append(Spacer(1, 6 * mm))
    rows = [["Rechnung", "Datum", "Fällig am", "Rechnungsbetrag", "offen"],
            [inv["number"], fmt_date(inv["doc_date"]), fmt_date(inv.get("due_date")), fmt_eur(inv["total_gross_cents"]),
             fmt_eur(data["open_cents"])]]
    if data["prev_fees_cents"]:
        rows.append(["", "", "", "bisherige Mahngebühren", fmt_eur(data["prev_fees_cents"])])
    if data["fee_cents"]:
        rows.append(["", "", "", "Mahngebühr", fmt_eur(data["fee_cents"])])
    rows.append(["", "", "", "Zu zahlen bis " + fmt_date(data["new_due"]), fmt_eur(data["total_cents"])])
    def _style(r, ci):
        bold = r == 0 or r == len(rows) - 1
        if ci >= 3:
            return st["headr"] if bold else st["cellr"]
        return st["head"] if bold else st["cell"]
    prow = [[_p(c, _style(r, ci)) for ci, c in enumerate(row)] for r, row in enumerate(rows)]
    t = Table(prow, colWidths=[28 * mm, 24 * mm, 24 * mm, CONTENT_W - 104 * mm, 28 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                           ("LINEABOVE", (3, -1), (-1, -1), 1, colors.black),
                           ("ALIGN", (3, 0), (-1, -1), "RIGHT")]))
    story.append(t)
    story.append(Spacer(1, 6 * mm))
    comp = data["company"]
    pay = f"Bitte überweisen Sie den Betrag an\nIBAN: {comp.get('iban', '')}" + (f"\nBIC: {comp['bic']}" if comp.get("bic") else "") + \
          f"\nVerwendungszweck: {inv['number']}\n\n{data.get('closing', '')}"
    left = _p(pay, st["base"])
    if comp.get("iban"):
        qr = QRFlowable(epc_payload(comp.get("name", ""), comp["iban"], comp.get("bic", ""), data["total_cents"],
                                    f"Rechnung {inv['number']}"), caption="Bezahlen per GiroCode")
        story.append(Table([[left, qr]], colWidths=[CONTENT_W - 30 * mm, 30 * mm],
                           style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])))
    else:
        story.append(left)
    doc.build(story, canvasmaker=_NumberedCanvas)
    return path
