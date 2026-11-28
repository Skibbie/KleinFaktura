"""Verzeichnis der Platzhalter für Textbausteine, Belegtexte und Mailvorlagen (Vorschlagliste und Übersicht).

Die Werte selbst liefern documents.placeholders() bzw. die Mahn- und Mailfunktionen; ein Test stellt sicher,
dass beide Seiten zusammenpassen.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Placeholder:
    name: str
    description: str
    context: str = "beleg"   # beleg = überall; mahnung / mail = nur dort zusätzlich

    @property
    def token(self) -> str:
        return "{" + self.name + "}"


CONTEXT_LABEL = {"beleg": "alle Texte", "mahnung": "nur Mahnungen", "mail": "nur E-Mail-Vorlagen"}

PLACEHOLDERS: tuple[Placeholder, ...] = (
    Placeholder("briefanrede", "Briefanrede des Kunden, z.B. „Sehr geehrte Frau Mustermann,“"),
    Placeholder("kunde", "Name des Kunden"),
    Placeholder("belegnr", "Nummer des Belegs (vor dem Festschreiben ein Hinweis)"),
    Placeholder("datum", "Belegdatum"),
    Placeholder("leistungsdatum", "Leistungsdatum"),
    Placeholder("faellig", "Fälligkeitsdatum der Rechnung"),
    Placeholder("zahlungsziel", "Zahlungsziel in Tagen (Einstellungen)"),
    Placeholder("gueltig_bis", "Gültig bis (Angebot)"),
    Placeholder("betrag", "Gesamtbetrag des Belegs"),
    Placeholder("projekt", "Name des Projekts / Objekts"),
    Placeholder("objekt_adresse", "Adresse des Projekts / Objekts"),
    Placeholder("ref_belegnr", "Nummer der stornierten Rechnung (Storno)"),
    Placeholder("ref_datum", "Datum der stornierten Rechnung (Storno)"),
    Placeholder("firma", "Eigener Firmenname"),
    Placeholder("inhaber", "Eigener Name (Inhaber)"),
    Placeholder("iban", "Eigene IBAN"),
    Placeholder("bic", "Eigene BIC"),
    Placeholder("rechnungsnr", "Nummer der angemahnten Rechnung", "mahnung"),
    Placeholder("rechnungsdatum", "Datum der angemahnten Rechnung", "mahnung"),
    Placeholder("offen", "Offener Rechnungsbetrag", "mahnung"),
    Placeholder("gebuehr", "Mahngebühr dieser Mahnung", "mahnung"),
    Placeholder("gesamt", "Zu zahlen insgesamt (offen + Gebühren)", "mahnung"),
    Placeholder("neu_faellig", "Neue Zahlungsfrist", "mahnung"),
    Placeholder("belegart", "Art des Belegs, z.B. „Rechnung“", "mail"),
    Placeholder("betreff", "Betreff des Belegs", "mail"),
)

# Textbaustein-Kategorien ohne Platzhalter-Ersetzung (z.B. Anlass einer Fahrt)
NO_PLACEHOLDERS = {"fahrt_anlass"}


def for_context(context: str | None) -> list[Placeholder]:
    """Platzhalter, die in einem Text der Art `context` (beleg/mahnung/mail) ersetzt werden; None = alle."""
    if context is None:
        return list(PLACEHOLDERS)
    return [p for p in PLACEHOLDERS if p.context in ("beleg", context)]


def for_category(category: str | None) -> list[Placeholder]:
    """Platzhalter für eine Textbaustein-Kategorie; None (Kategorie unbekannt) = alle."""
    if category is None:
        return for_context(None)
    if category in NO_PLACEHOLDERS:
        return []
    return for_context("mahnung" if category.startswith("mahnung") else "beleg")
