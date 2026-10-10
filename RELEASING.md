# Release-Ablauf

## Versionen
`MAJOR.MINOR.PATCH` – z.B. `1.0.1` nur Fehlerkorrekturen, `1.1.0` neue Funktionen, `2.0.0` größere Umbauten.

## Branches
- `main` – immer freigegeben und lauffähig. Jede Version ist ein Tag `vX.Y.Z`.
- Arbeit auf eigenen Branches (`feature/…`, `fix/…`), Zusammenführung per Pull Request nach bestandenen Tests.
- Dringender Fix für die genutzte Version, während `main` schon Neues enthält: Branch `release/1.0` vom Tag
  `v1.0.0` anlegen, dort fixen und `v1.0.1` taggen; den Fix anschließend nach `main` übernehmen.

## Fehler und Wünsche
GitHub → Issues → „Neues Issue“ → Vorlage **Fehler** oder **Wunsch**. Keine Kundendaten, Rechnungen oder
Screenshots mit personenbezogenen Daten anhängen. Geplante Punkte werden einem Meilenstein (z.B. `v1.1.0`) zugeordnet.

## Neue Version veröffentlichen
1. `CHANGELOG.md`: Einträge aus „Unveröffentlicht“ in einen neuen Abschnitt `## [X.Y.Z] – Datum` verschieben.
2. `app/config.py`: `APP_VERSION = "X.Y.Z"`.
3. Bei Änderungen an `schema.sql`: `SCHEMA_VERSION` in `app/db/database.py` erhöhen, Umbau so schreiben,
   dass bestehende Daten erhalten bleiben (nur `CREATE … IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN`).
4. Commit auf `main`, dann `git tag vX.Y.Z` und `git push --follow-tags`.
5. GitHub Actions testet, baut `KleinFaktura-Setup-X.Y.Z.exe` (Inno Setup, `installer/KleinFaktura.iss`) und
   `KleinFaktura-X.Y.Z.zip` und legt das Release mit dem Changelog-Abschnitt an.
   Der Workflow bricht ab, wenn Tag und `APP_VERSION` nicht übereinstimmen.

## Installation / Update beim Anwender
Beim Start fragt das Programm die öffentliche GitHub-API nach einer neueren Version und bietet das Update an
(abschaltbar unter Einstellungen → Programm-Updates, dort auch „Jetzt nach Updates suchen“). Bei „Jetzt
aktualisieren“ wird bei einer Installation per Setup das neue Setup geladen (SHA-256-geprüft) und nach dem
Beenden still ausgeführt. Bei einer ZIP-Installation wird das ZIP geladen, geprüft und nach `KleinFaktura.neu` entpackt, das Programm regulär
beendet (mit Sicherung), ein Hilfsskript tauscht den Programmordner und startet die neue Version. Der alte Stand
bleibt als `KleinFaktura.vorher` liegen; Protokoll in `update.log` im Datenordner.

Von Hand:
1. Programm schließen (dabei läuft die tägliche Sicherung).
2. ZIP aus dem Release herunterladen, Inhalt nach `%LOCALAPPDATA%\Programs\KleinFaktura\` entpacken
   (vorhandenen Ordner ersetzen). Oder: `tools\install_release.ps1` ausführen.
3. Starten. Ändert die neue Version die Datenbank, liegt vorher eine Kopie unter
   `%LOCALAPPDATA%\KleinFaktura\vor-update\`.

## Zurück auf die Vorversion
Ältere Exe aus dem vorherigen Release entpacken. Wurde die Datenbank inzwischen umgebaut, startet die alte
Version nicht; dann die Kopie aus `vor-update\` zurückspielen (alles nach dem Update Erfasste fehlt danach)
oder einen Fix-Release abwarten.
