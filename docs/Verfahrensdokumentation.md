# Verfahrensdokumentation – Rechnungsstellung mit KleinFaktura

> Vorlage zum Ausfüllen. Die GoBD verlangen eine Verfahrensdokumentation, die beschreibt, **wie** Belege
> entstehen, verarbeitet, gespeichert und aufbewahrt werden. Die Software unterstützt die GoBD; die
> Ordnungsmäßigkeit entsteht erst durch das tatsächliche Verfahren im Betrieb. Bitte die mit `[…]`
> markierten Stellen ergänzen und Änderungen mit Datum versionieren.
>
> Diese Datei im Repository ist die **Vorlage**. Die ausgefüllte Fassung mit Firmendaten liegt außerhalb
> des Programms (z.B. im eigenen Dokumente- oder Cloud-Ordner).

| | |
|---|---|
| Unternehmen | […Name, Anschrift…] |
| Steuernummer | […] |
| Verantwortlich | […] |
| Stand / Version | […Datum…] / 1.0 |
| Software | KleinFaktura […Version…] (Python/SQLite, lokal unter Windows) |

## 1. Allgemeine Beschreibung
- Tätigkeit: […Art der Leistungen…].
- Besteuerung: Kleinunternehmer nach § 19 UStG (Prüfung der Umsatzgrenzen durch den Kleinunternehmer-Wächter).
- Gewinnermittlung: Einnahmen-Überschuss-Rechnung […durch Steuerberater/selbst…].
- Ausgangsrechnungen werden ausschließlich mit KleinFaktura erstellt (seit […Datum…]). Ältere Rechnungen
  wurden aus der bisherigen Tabellenvorlage importiert (Originaldatei unverändert archiviert, Kennzeichen „Import“).

## 2. Anwenderdokumentation (Ablauf)
1. **Kunde anlegen** – manuell oder per Import (CSV/Excel; im Profil Energieberatung auch aus Hottgenroth, nur lesender Zugriff).
2. **Angebot** (optional) – Nummernkreis `ANJJJJ-MMNN`.
3. **Rechnung erstellen** – Entwurf ohne Nummer; Entwürfe sind änderbar und löschbar.
4. **Festschreiben** – vergibt die fortlaufende Nummer `JJJJ-MMNN` (Zähler je Monat), prüft die
   Pflichtangaben nach § 14 UStG, erzeugt das PDF (optional mit ZUGFeRD-XML) und speichert dessen SHA-256-Prüfsumme.
   Ab diesem Zeitpunkt lässt sich der Beleg im Programm nicht mehr ändern oder löschen (siehe Abschnitt 3 zur
   Schutzwirkung).
5. **Versand** – […per E-Mail als PDF / Post…]. Versanddatum wird […wo?…] dokumentiert.
6. **Zahlungseingang** – Erfassung in „Offene Posten“ (Datum, Betrag, Art). Zahlungen sind unveränderbar;
   Fehler werden mit einer Korrekturbuchung (negativer Betrag) berichtigt.
7. **Mahnwesen** – Zahlungserinnerung, 1. und 2. Mahnung als PDF; archiviert mit Prüfsumme.
8. **Korrektur** – ausschließlich per **Stornorechnung** (eigene Nummer im Rechnungskreis, Verweis auf
   die Ursprungsrechnung) und ggf. neuer Rechnung.

9. **Ausgaben** – Erfassung im Ausgabenbuch (Datum, Lieferant, Kategorie, Bruttobetrag) mit Beleg; der Beleg
   wird unverändert und schreibgeschützt mit SHA-256-Prüfsumme archiviert (`archiv\ausgaben\JJJJ\`).
   Änderungen und Löschungen werden mit altem und neuem Wert protokolliert. Nach Abgabe der Steuererklärung
   wird das Jahr abgeschlossen; danach lassen sich die Ausgaben dieses Jahres im Programm nicht mehr ändern.
   Papierbelege werden […aufbewahrt / gescannt, Scanvorgang beschreiben…].
10. **Fahrten** – Betriebliche Fahrten mit dem privaten Pkw werden zeitnah im Reiter „Fahrten“ erfasst
   (Datum, Kunde/Objekt, Zieladresse, Anlass, gefahrene Kilometer, ggf. zugehöriges Angebot/Rechnung).
   Die einfache Strecke wird mit einem Routenplaner ermittelt (Knopf „Route anzeigen“ öffnet Google Maps im
   Browser; das Programm selbst überträgt keine Adressen) oder aus der letzten Fahrt zum selben Ziel übernommen.
   Angesetzt wird die Kilometerpauschale; der Satz wird je Fahrt gespeichert. Änderungen und Löschungen werden
   protokolliert; mit dem Jahresabschluss werden auch die Fahrten des Jahres unveränderbar. Park- und
   Mautgebühren werden als Ausgabe mit Beleg erfasst.
11. **EÜR** – Die Jahreswerte (Einnahmen nach Zufluss, Ausgaben je Bereich einschließlich Fahrten) werden aus
   KleinFaktura manuell in […Steuersoftware…] (Anlage EÜR) übertragen.

## 3. Technische Systemdokumentation
- Datenbank: SQLite, lokal unter `%LOCALAPPDATA%\KleinFaktura\faktura.sqlite3`.
- Belegarchiv: `%LOCALAPPDATA%\KleinFaktura\archiv\JJJJ\` (PDF/XML, schreibgeschützt), Importe unter `archiv\import\`.
- Unveränderbarkeit: Datenbank-Trigger verhindern Änderung/Löschung festgeschriebener Belege, Positionen,
  Zahlungen, Mahnungen und Protokolleinträge; Nummernkreise können nicht zurückgesetzt werden. Die Trigger
  werden bei jedem Programmstart aus der mitgelieferten Definition neu angelegt.
- Protokoll: jede relevante Aktion wird in einem Änderungsprotokoll mit Hash-Kette (SHA-256) festgehalten;
  „Integrität prüfen“ erkennt nachträgliche Änderungen an Protokoll und Archivdateien. Anzahl der Einträge und
  letzter Hash (Kettenkopf) werden in jeder Datensicherung (`INFO.txt`) und im GoBD-Datenexport festgehalten;
  ein Abgleich mit älteren Sicherungen macht auch das Entfernen der letzten Einträge erkennbar.
- **Grenzen der Schutzwirkung**: Die Sperren wirken gegen Änderungen über das Programm. Wer direkten
  Schreibzugriff auf Datenbank und Archivordner hat, kann sie mit Fachkenntnis umgehen (Schreibschutz ist ein
  Dateiattribut, Prüfsummen und Hash-Kette liegen in derselben Datenbank). Solche Eingriffe sind nur teilweise
  erkennbar. Ergänzende organisatorische Maßnahmen: Zugriff auf das Windows-Benutzerkonto beschränken,
  Datensicherungen getrennt und unveränderbar aufbewahren (z.B. externer Datenträger, Versionierung).
- E-Rechnung: ZUGFeRD/Factur-X Profil EN 16931 (CII), eingebettet in PDF/A-3, XSD-geprüft.

## 4. Betriebsdokumentation
- **Datensicherung**: automatisch täglich beim Beenden sowie manuell; ZIP mit Datenbank und Archiv nach
  `[…Sicherungsordner…]`; Aufbewahrung 30 tägliche + 12 monatliche Sicherungen.
  Zusätzliche Sicherung […externe Festplatte, Rhythmus…].
- **Rücksicherung**: über „Export & Sicherung → Aus Sicherung wiederherstellen“; vorheriger Stand wird aufbewahrt.
  Test der Rücksicherung zuletzt am […Datum…].
- **Zugriffsschutz**: Windows-Benutzerkonto mit Passwort […], Festplattenverschlüsselung […BitLocker ja/nein…].
- **Aufbewahrung**: Rechnungen 8 Jahre (§ 147 AO i. d. F. BEG IV, ab Ende des Kalenderjahres), Handels-/
  Geschäftsbriefe 6 Jahre. Vor Löschung alter Daten Fristen prüfen. […bitte mit Steuerberater abstimmen…]
- **Datenzugriff Finanzverwaltung**: „Datenexport für Betriebsprüfung“ erzeugt CSV-Dateien aller Belege,
  Positionen, Zahlungen, Mahnungen und des Protokolls.

## 5. Internes Kontrollsystem
- Pflichtangabenprüfung vor dem Festschreiben (Anschrift, Steuernummer, Leistungsdatum, § 19-Hinweis).
- Kleinunternehmer-Wächter mit Warnung ab 80 % der Umsatzgrenzen und Prüfung vor jedem Festschreiben.
- Monatlicher Abgleich offene Posten ↔ Kontoauszug […].
- Jährliche Integritätsprüfung und Export für den Steuerberater […].

## 6. Änderungshistorie
| Datum | Änderung | Name |
|---|---|---|
| […] | Ersterstellung | […] |
