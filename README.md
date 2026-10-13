# KleinFaktura

Kostenloses Rechnungsprogramm für Kleinunternehmer und Selbstständige in Deutschland (Windows, EÜR):
Angebote, Rechnungen, Stornos, offene Posten, Mahnwesen, Kundenstamm, Textbausteine, Ausgabenbuch,
Fahrtennachweis, GoBD-Unterstützung, E-Rechnung (ZUGFeRD) und GiroCode. Alle Daten bleiben lokal auf dem eigenen PC.

> **Hinweis:** KleinFaktura ist keine Steuerberatung. Steuerliche Grenzwerte, Aufbewahrungsfristen, Kontenzuordnungen
> und die Verfahrensdokumentation bitte mit dem Steuerberater abstimmen. Die Software wird ohne Gewährleistung
> bereitgestellt (siehe [LICENSE](LICENSE)).

## Installation
1. Unter [Releases](https://github.com/Skibbie/KleinFaktura/releases) das neueste `KleinFaktura-Setup-x.y.z.exe`
   herunterladen und starten. Es sind keine Administratorrechte nötig; installiert wird nach
   `%LOCALAPPDATA%\Programs\KleinFaktura\` mit Eintrag im Startmenü (Desktop-Verknüpfung optional).
   Da der Installer nicht signiert ist, zeigt Windows SmartScreen beim ersten Mal ggf. eine Warnung
   („Weitere Informationen“ → „Trotzdem ausführen“).
2. Beim ersten Start führt ein **Einrichtungsassistent** durch Firmendaten, Bankverbindung, Branche,
   Besteuerung, Logo und Sicherungsordner. Alles lässt sich später unter „Einstellungen“ ändern.

Ohne Installation: `KleinFaktura-x.y.z.zip` entpacken und `KleinFaktura.exe` starten.
Deinstallation über Windows-Einstellungen → Apps; die Daten unter `%LOCALAPPDATA%\KleinFaktura\` bleiben erhalten.

Updates: Das Programm sucht beim Start nach neuen Versionen und bietet sie an (abschaltbar). Die Datenbank wird
beim ersten Start einer neuen Version automatisch angepasst; vorher wird eine Kopie unter
`%LOCALAPPDATA%\KleinFaktura\vor-update\` abgelegt. Eine ältere Version verweigert den Start mit einer neueren
Datenbank (die Daten bleiben unverändert).

## Branchenprofile
| Profil | Inhalt |
|---|---|
| Allgemein | neutrale Startartikel (Arbeitsleistung, Pauschale, Material, Fahrtkosten) und Fahrtanlässe |
| Energieberatung | Artikel wie Heizlastberechnung und hydraulischer Abgleich, passende Fahrtanlässe, Kundenimport aus Hottgenroth |

Beim ersten Wählen eines Profils kommen dessen Startwerte dazu. Ein Wechsel löscht nichts; alles lässt sich unter
„Textbausteine & Artikel“ frei anpassen.

## Funktionen
| Bereich | Inhalt |
|---|---|
| Kunden | manuell anlegen/bearbeiten, Projekte/Objektadressen je Kunde, Import aus CSV/Excel (bei Energieberatung zusätzlich aus Hottgenroth, nur lesend) – mit Kollisionsdialog (Feldvergleich, lokal behalten / übernehmen / zusammenführen / neu / überspringen) |
| Angebote | `ANJJJJ-MMNN`, Gültigkeit, „In Rechnung umwandeln“ |
| Rechnungen | `JJJJ-MMNN` (Zähler je Monat), Nummer erst beim Festschreiben, Pflichtangabenprüfung § 14 UStG, PDF mit eigenem Logo, GiroCode, optional ZUGFeRD (EN 16931, PDF/A-3) |
| Versand & Ablage | „Per E-Mail“ öffnet Thunderbird mit vorbereiteter Mail (Empfänger aus dem Kunden, Betreff und Text als Vorlage) und dem PDF als Anhang – gesendet wird erst in Thunderbird; optional nach dem Festschreiben eine PDF-Kopie in einen Wunschordner (auf Wunsch je Jahr) |
| Storno | Stornorechnung mit eigener Nummer und Verweis; festgeschriebene Belege sind unveränderbar |
| Offene Posten | Teilzahlungen, Überfälligkeit, Zahlungserinnerung / 1. / 2. Mahnung mit Gebühren und GiroCode |
| Altbelege | Import bestehender Rechnungen aus .ods / .xlsx / .pdf, Prüfdialog, Nummernreservierung, Dubletten- und Kollisionsprüfung |
| Kleinunternehmer | Wächter für 25.000 € (Vorjahr) / 100.000 € (lfd. Jahr), Prüfung vor jedem Festschreiben, Umschalten auf Regelbesteuerung |
| GoBD | Trigger gegen Änderungen, Hash-Kette im Protokoll, Archiv mit SHA-256, Integritätsprüfung, Datenexport für Betriebsprüfung, Vorlage Verfahrensdokumentation (`docs/`) |
| Ausgaben | Ausgabenbuch mit Kategorien nach Anlage EÜR, Beleg-Archiv (PDF/Foto, schreibgeschützt mit Prüfsumme), Abschreibung über die Nutzungsdauer (monatsgenau), 70 % Bewirtung, Übernahme aus CSV-Kontoauszug, Jahresabschluss (danach unveränderbar) |
| Fahrten | Fahrtennachweis für den Privat-Pkw (Datum, Kunde/Objekt, Ziel, Anlass, km, verknüpfte Rechnung), Kilometerpauschale je Fahrt gespeichert, km-Vorschlag aus der letzten Fahrt zum selben Ziel, Excel-Export; Summe fließt in die EÜR-Werte |
| EÜR-Werte | Jahressummen je EÜR-Bereich zum Übertragen in die Steuersoftware, Abhakliste, Excel-Export |
| Export | Umsatzliste + Einnahmen (Excel/CSV), DATEV-Buchungsstapel |
| Sicherung | automatisch täglich beim Beenden + manuell, ZIP in einen wählbaren Ordner, Rotation, Rücksicherung |

## Speicherorte
- Datenbank + Archiv: `%LOCALAPPDATA%\KleinFaktura\` (bewusst **nicht** in einem Cloud-Sync-Ordner – SQLite
  verträgt keine Sync-Ordner)
- Sicherungen: standardmäßig `Dokumente\KleinFaktura-Sicherung\`, einstellbar (z.B. ein Cloud-Ordner)

## Platzhalter in Texten
`{briefanrede} {kunde} {faellig} {zahlungsziel} {iban} {bic} {belegnr} {betrag} {gueltig_bis} {datum}
{leistungsdatum} {inhaber} {firma} {objekt_adresse} {projekt} {ref_belegnr} {ref_datum}` –
in E-Mail-Vorlagen zusätzlich `{belegart} {betreff}`, in Mahnungen zusätzlich `{offen} {gebuehr} {gesamt} {neu_faellig} {rechnungsnr} {rechnungsdatum}`.

## Hinweise
- Die Software ist GoBD-*unterstützend*. Die Ordnungsmäßigkeit hängt vom gelebten Verfahren ab
  (Verfahrensdokumentation ausfüllen).
- Kleinunternehmer müssen keine E-Rechnungen ausstellen (aber empfangen können). ZUGFeRD ist pro Rechnung zuschaltbar.

## Entwicklung
- Python 3.12: `python -m pip install -r requirements.txt`, Start mit `python main.py` oder `Start-Entwicklung.bat`
- Aus dem Quellcode gestartet nutzt das Programm **getrennte Testdaten** unter
  `%LOCALAPPDATA%\KleinFaktura-Entwicklung\` (Titelleiste zeigt „ENTWICKLUNG“).
- Tests: `python -m pytest tests` (nur Dummy-Daten)
- Exe lokal bauen: `build_exe.bat`
- Releases: siehe [RELEASING.md](RELEASING.md). Fehler und Wünsche als GitHub-Issue (Vorlagen „Fehler“ / „Wunsch“).

## Lizenz
MIT, siehe [LICENSE](LICENSE).
