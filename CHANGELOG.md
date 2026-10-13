# Änderungen

Format: je Version ein Abschnitt `## [x.y.z] – JJJJ-MM-TT`. Der Abschnitt der Version wird beim Release
automatisch als Beschreibung übernommen. Neue Einträge zuerst unter `## [Unveröffentlicht]` sammeln.

## [Unveröffentlicht]

## [2.1.0] – 2026-10-13

- Rechnungen und Angebote per E-Mail: Der Button „Per E-Mail“ sitzt in der Belegliste und im
  festgeschriebenen Beleg, außerdem wird der Versand direkt nach dem Festschreiben angeboten. Er öffnet
  Thunderbird mit Empfänger, Betreff, Text und dem PDF als Anhang. Gesendet wird erst in Thunderbird. Betreff
  und Text sind unter Einstellungen → E-Mail & PDF-Ablage als Vorlagen mit Platzhaltern einstellbar.
  Thunderbird wird automatisch gefunden oder kann dort gewählt werden
- PDF automatisch an einen Wunschort: Nach dem Festschreiben (auch bei Stornos) wird auf Wunsch eine Kopie des
  PDFs in einen frei gewählten Ordner gelegt, optional mit Unterordner je Jahr. Das Archiv bleibt unverändert

## [2.0.0] – 2026-10-10

Erste öffentliche Version (MIT-Lizenz).

- Windows-Installer (ohne Administratorrechte, Startmenü, optionale Desktop-Verknüpfung, Deinstallation
  über die Windows-Einstellungen; die Daten bleiben erhalten). Updates laufen über das neue Setup
- Einrichtungsassistent beim ersten Start: Firmendaten, Bankverbindung, Branche, Besteuerung, Logo und
  Sicherungsordner
- Branchenprofile unter Einstellungen → Branche: „Allgemein“ mit neutralen Startartikeln und Fahrtanlässen,
  „Energieberatung“ mit Fachartikeln, passenden Fahrtanlässen und dem Kundenimport aus Hottgenroth
- Updates direkt aus den öffentlichen GitHub-Releases, ohne Anmeldung und ohne GitHub CLI; der Download wird
  per SHA-256-Prüfsumme geprüft
- Kein eingebautes Logo mehr: Das eigene Logo wird in den Einstellungen gewählt. Startbild und Programmsymbol
  sind neutral
- Standard-Sicherungsordner: `Dokumente\KleinFaktura-Sicherung` (bestehende Einstellungen bleiben)
- Umstieg von der nicht veröffentlichten Vorgängerversion „EEE-Faktura“: Datenordner, Einstellungen und Logo
  werden automatisch übernommen, die Datenbank erhält das Profil „Energieberatung“
