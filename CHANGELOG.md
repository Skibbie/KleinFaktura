# Änderungen

Format: je Version ein Abschnitt `## [x.y.z] – JJJJ-MM-TT`. Der Abschnitt der Version wird beim Release
automatisch als Beschreibung übernommen. Neue Einträge zuerst unter `## [Unveröffentlicht]` sammeln.

## [Unveröffentlicht]

## [2.2.1] – 2026-10-14

- Wichtiger Hinweis zu Haftung und Verantwortung (keine Steuerberatung, ohne Gewährleistung, Datensicherung und
  Aufbewahrung liegen beim Anwender, Regelbesteuerung eingeschränkt) im Einrichtungsassistenten und unter
  Einstellungen → Hinweise; Website und README entsprechend ergänzt

## [2.2.0] – 2026-10-14

- Mahngebühren: Zahlt ein Kunde Rechnung plus Mahngebühr, gilt die Rechnung als bezahlt statt als überzahlt.
  Gebühren zählen nicht mehr zum Umsatz für die Kleinunternehmergrenze und werden im DATEV-Export auf ein
  eigenes Konto gebucht (Einstellungen → Schnittstellen, Standard 2700)
- Einstellungen: Zahlenfelder werden beim Speichern geprüft, ungültige Werte werden abgelehnt. Bereits
  gespeicherte unlesbare Werte führen nicht mehr zu Abstürzen, es gilt dann der Standardwert
- Texte mit einzelnen geschweiften Klammern („{“ oder „}“) brechen Vorschau, Festschreiben, Mahnung und E-Mail
  nicht mehr ab
- Nach einer Rücksicherung auf einem anderen PC oder unter einem anderen Benutzer werden Archiv-PDFs, Mahnungen,
  Ausgabenbelege und das Logo wieder gefunden; die wiederhergestellten Archivdateien sind wieder schreibgeschützt
- Eingaben wie „1.500“ (Preis, Menge, km, Betrag) werden als 1500 gelesen, nicht als 1,5
- Eine zweite Mahnung derselben Stufe am selben Tag überschreibt die erste nicht mehr; Mahnungs-PDFs sind
  schreibgeschützt. Die Integritätsprüfung umfasst jetzt auch Mahnungen und Ausgabenbelege
- „Kopieren“ einer Stornorechnung ergibt eine Rechnung mit positiven Mengen
- „Per E-Mail“ verschickt bei importierten Altbelegen ohne PDF keine Tabellendatei mehr
- Schutz vor Fehlbedienung bei unumkehrbaren Schritten:
  - Festschreiben fragt nach bei 0-€-Beträgen, Positionen ohne Preis und auffälligem Belegdatum (Zukunft oder
    mehr als drei Monate zurück)
  - „In Rechnung umwandeln“ fragt nach, wenn es zum Angebot schon eine Rechnung gibt
  - „Mahnen“ fragt nach, wenn die Rechnung noch nicht fällig ist
  - Zahlungen über dem offenen Betrag (inkl. Mahngebühren), negative Beträge und Zahlungen auf stornierte
    Rechnungen erfordern eine Bestätigung
  - Das laufende Jahr kann nicht mehr abgeschlossen werden
  - PDF-Ordner und Sicherungsordner dürfen nicht im Beleg-Archiv liegen
  - Sicherungen einer neueren Programmversion werden vor der Rücksicherung erkannt und abgelehnt
- USt-Eingaben wie „19 %“ oder „7%“ werden verstanden; bei unlesbaren Werten nennt die Meldung die Position
- Entfernte Kunden lassen sich unter Kunden → „Entfernte (inaktive) anzeigen“ wieder aktivieren
- Kundenauswahl: Ein eingetippter Name ohne Auswahl aus der Liste übernimmt den eindeutigen Treffer oder setzt
  auf „Kunde wählen“ zurück, statt still den vorher gewählten Kunden zu behalten
- Mausrad: Auswahl-, Datums- und Zahlenfelder ändern sich nur noch, wenn sie angeklickt sind – beim Scrollen
  durch den Beleg sprangen sonst Kunde, Besteuerung oder Datum um. Die Rückfrage vor dem Festschreiben zeigt Kunde,
  Datum und Besteuerung und warnt, wenn die Besteuerung von der Einstellung abweicht oder Zeichen (z.B. Emojis)
  im PDF nicht darstellbar sind
- Angebot → Rechnung übernimmt die aktuell eingestellte Besteuerung samt passendem Hinweis; „Regelbesteuerung
  mit § 19-Hinweis“ wird beim Festschreiben abgelehnt
- Regelbesteuerung ist als „eingeschränkt“ gekennzeichnet (Einstellungen, Einrichtung, Beleg, README) mit
  Hinweis, was nicht unterstützt wird (USt-Voranmeldung, USt im DATEV-Export und in den EÜR-Werten, Abschläge in
  Schlussrechnungen, Reverse Charge). Die Kleinunternehmergrenze zählt Zahlungen auf Regel-Rechnungen netto
- Rücksicherung: läuft über einen Zwischenordner und schiebt bei einem Fehler (z.B. noch geöffnetes Archiv-PDF)
  alles zurück – das Programm startet danach nicht mehr leer. Programme/Skripte aus einer Sicherung werden nicht
  übernommen, Größe und Anzahl sind begrenzt. Gerätebezogene Einstellungen (Sicherungs- und PDF-Ordner,
  Thunderbird, Hottgenroth-Server) bleiben die dieses PCs und werden nicht aus der Sicherung übernommen
- Sicherheit: Das Programm öffnet keine ausführbaren Dateien (auch nicht über manipulierte Belegpfade), startet als
  Thunderbird nur eine thunderbird.exe außerhalb des Datenordners, greift beim Logo und bei Archivpfaden nicht auf
  Netzwerkpfade zu und lässt im Hottgenroth-Verbindungstext keine Zusatzangaben zu. Text, der mit =, +, -, @ beginnt,
  wird in Excel-/CSV-Exporten nicht mehr als Formel ausgeführt. Update-Dateien liegen in Ordnern mit Zufallsnamen
- Sicherungen lassen sich optional mit Passwort verschlüsseln (AES-256); liegt der Sicherungsordner in einem
  Cloud-Ordner (OneDrive, Dropbox …), weist das Programm darauf hin. Ist der Sicherungsordner nicht erreichbar,
  wird gemeldet, wo die Sicherung stattdessen liegt
- GoBD: Schutz-Trigger werden bei jedem Start neu angelegt; Anzahl und letzter Hash des Protokolls stehen in jeder
  Sicherung und im GoBD-Export. Verfahrensdokumentation, README und Website beschreiben ehrlich, dass die Sperren
  im Programm wirken und mit direktem Dateizugriff umgangen werden können
- Updates sind signiert: Das Programm installiert ein Update nur noch, wenn die Release-Dateien mit dem Schlüssel
  des Herstellers signiert sind und zur signierten Prüfsumme passen. Ein manipuliertes Release wird abgelehnt.
  Für Anwender ändert sich nichts; die Prüfung läuft automatisch (ab dieser Version für alle folgenden Updates)
- Nur eine Programminstanz: ein zweiter Start meldet, dass KleinFaktura bereits geöffnet ist
- Verwaiste Archivdatei nach einem Absturz blockiert nicht mehr alle Rechnungen des Monats (wird umbenannt und
  protokolliert)
- Bank-CSV (z.B. Sparkasse): Verwendungszweck statt Buchungstext; mehrere gleiche Abbuchungen werden alle
  übernommen; Beträge wie „-1.500“ werden als 1.500 € gelesen
- Storno: geht an den Empfänger der Ursprungsrechnung und hinterlässt bei einem Fehler keinen Entwurf. Nach einem
  Storno mit Teilzahlung erscheint der zu erstattende Betrag in den offenen Posten; Übersicht und offene Posten
  verrechnen Guthaben nicht mit Forderungen
- E-Rechnung: Länderangaben wie „Österreich“ werden in ISO-Codes umgesetzt; Unbekanntes wird vor dem
  Festschreiben gemeldet statt abgeschnitten („Germany“ wurde zu GE = Georgien)
- Sehr lange Positionsbeschreibungen laufen über mehrere Seiten statt das PDF abzubrechen
- Verständlichere Meldungen: Entwurf löschen, der schon weiterverwendet wurde; Datei noch in Excel geöffnet;
  kein PDF-Programm installiert
- Bedienung:
  - In der Beleg-Liste sind nur die Knöpfe aktiv, die zum markierten Beleg passen (z.B. „Stornieren“ nur bei
    festgeschriebenen Rechnungen); der Tooltip erklärt, warum ein Knopf gerade nicht geht. Entwürfe zeigen keinen
    offenen Betrag mehr
  - Im Beleg steht unter der Anrede, was im Brief erscheint (z.B. „Im Brief: Sehr geehrte Frau Mustermann,“)
  - Der Knopf heißt jetzt „Festschreiben (endgültig)…“
  - Einstellungen in Reitern (Firma, Steuer und Belege, E-Mail und PDF, Datensicherung, Schnittstellen und Updates);
    „Absender Zeile 1“ heißt „Zusatzzeile im Briefkopf“
  - Einheitlich „Sie“ statt teils „du“, richtige Einzahl („1 Ausgabe“), EÜR-Hinweis passend zur Besteuerung,
    Hinweis bei noch leerer Sicherungsliste
- Kleinere Korrekturen: Ausgaben-Lieferant überschreibt keine selbst gewählte Kategorie mehr; leerer Mahntext
  fällt auf den Standard zurück; Umschalten auf Regelbesteuerung löscht nur noch den § 19-Hinweis; Logo aus den
  Einstellungen wird in den Datenordner kopiert; Vorschau-PDFs werden aufgeräumt; Fehlerprotokoll ist begrenzt;
  schnellere Belegliste bei vielen Mahnungen

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
