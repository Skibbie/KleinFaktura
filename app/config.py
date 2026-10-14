"""Pfade und Grundkonfiguration.

Die Live-Datenbank liegt bewusst lokal (%LOCALAPPDATA%), nicht in einem Cloud-Sync-Ordner:
SQLite in synchronisierten Ordnern kann beschädigt werden oder Konfliktkopien erzeugen.
Backups werden in den konfigurierbaren Backup-Ordner (Standard: Dokumente/KleinFaktura-Sicherung) geschrieben.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "KleinFaktura"
APP_VERSION = "2.2.1"
# Frühere Programmnamen: deren Datenverzeichnis wird weiterverwendet, solange es noch kein eigenes gibt
LEGACY_NAMES = ["EEE-Faktura"]
DB_FILE = "faktura.sqlite3"

# Hinweis zu Haftung und Verantwortung (Einrichtungsassistent, Einstellungen; gleicher Inhalt auf der Website)
DISCLAIMER = (
    "KleinFaktura ist keine Steuer- oder Rechtsberatung und wird kostenlos und ohne Gewährleistung bereitgestellt "
    "(MIT-Lizenz). Die Software unterstützt die GoBD; ordnungsgemäß wird die Buchführung erst durch das gelebte "
    "Verfahren. Für die Richtigkeit der Belege, die Datensicherung und die Aufbewahrung über die gesetzlichen Fristen "
    "(für Rechnungen derzeit 8 Jahre) sind Sie selbst verantwortlich. Die Regelbesteuerung wird nur eingeschränkt "
    "unterstützt. Steuerliche Fragen klären Sie bitte mit Ihrer Steuerberatung.")


def _base_dir() -> Path:
    # PyInstaller entpackt nach sys._MEIPASS
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
ASSETS_DIR = BASE_DIR / "assets"

# Nur die installierte Exe arbeitet mit den echten Daten. Aus dem Quellcode gestartet (Entwicklung)
# wird ein getrenntes Datenverzeichnis verwendet, damit Testläufe nie die Live-Datenbank berühren.
IS_RELEASE = bool(getattr(sys, "frozen", False))


def _choose_data_dir() -> Path:
    """Eigener Ordner, wenn dort schon eine Datenbank liegt; sonst der Ordner eines früheren Programmnamens mit
    Datenbank (wird unverändert weiterbenutzt, nicht verschoben); sonst neu."""
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    suffix = "" if IS_RELEASE else "-Entwicklung"
    own = root / f"{APP_NAME}{suffix}"
    if (own / DB_FILE).exists():
        return own
    for old in LEGACY_NAMES:
        if (root / f"{old}{suffix}" / DB_FILE).exists():
            return root / f"{old}{suffix}"
    return own


def data_dir() -> Path:
    """Datenverzeichnis; per FAKTURA_DATA_DIR überschreibbar (Tests)."""
    override = os.environ.get("FAKTURA_DATA_DIR")
    p = Path(override) if override else _choose_data_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / DB_FILE


def archive_dir() -> Path:
    p = data_dir() / "archiv"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_stored_path(stored) -> Path | None:
    """Gespeicherter (absoluter) Pfad einer Archivdatei oder des Logos. Liegt die Datei dort nicht mehr – etwa nach
    einer Rücksicherung auf einem anderen PC oder unter einem anderen Benutzer –, wird sie im aktuellen
    Datenverzeichnis gesucht: Archivdateien ab dem letzten Ordner „archiv“, sonst (Logo) unter ihrem Namen."""
    if not stored:
        return None
    path = Path(stored)
    if is_unc(stored):
        # Netzwerkpfade nie anfassen (z.B. aus einer präparierten Sicherung): nur lokal unter dem Namen suchen
        return data_dir() / path.name
    if path.exists():
        return path
    lowered = [part.lower() for part in path.parts]
    if "archiv" in lowered:
        i = len(lowered) - 1 - lowered[::-1].index("archiv")
        candidate = archive_dir().joinpath(*path.parts[i + 1:])
        if candidate.exists():
            return candidate
    if path.name and (data_dir() / path.name).exists():
        return data_dir() / path.name
    return path


# Dateitypen, die Windows beim Öffnen ausführt statt anzuzeigen. Solche Dateien gehören nie ins Beleg-Archiv;
# das Programm öffnet, archiviert und stellt sie nicht wieder her (Schutz vor präparierten Sicherungen).
RISKY_SUFFIXES = {
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi",
    ".msp", ".mst", ".scr", ".pif", ".lnk", ".url", ".hta", ".cpl", ".jar", ".reg", ".dll", ".sys", ".ocx",
    ".application", ".appref-ms", ".gadget", ".msc", ".scf", ".inf", ".chm", ".py", ".pyw", ".pyc", ".pyz",
    ".settingcontent-ms", ".library-ms", ".search-ms", ".iso", ".img", ".vhd", ".vhdx", ".appx", ".msix",
    ".appinstaller", ".diagcab", ".xll", ".xlam", ".ppam", ".website",
}


def is_risky_file(path) -> bool:
    """Ausführbare bzw. beim Öffnen ausgeführte Datei (auch mit Doppelendung wie „rechnung.pdf.cmd“)."""
    return Path(str(path).strip().rstrip(". ")).suffix.lower() in RISKY_SUFFIXES


def is_unc(path) -> bool:
    """Netzwerkpfad (\\\\server\\freigabe) – der bloße Zugriff kann die Windows-Anmeldung an den Server senden."""
    s = str(path or "")
    return s.startswith("\\\\") or s.startswith("//")


def inside_archive(path) -> bool:
    """Liegt der Ordner im Beleg-Archiv? Dort darf das Programm nichts ablegen (Kopien, Sicherungen)."""
    try:
        Path(path).resolve().relative_to(archive_dir().resolve())
        return True
    except ValueError:
        return False


def preview_dir(cleanup_hours: float = 12) -> Path:
    """Eigener Temp-Ordner für Vorschau-PDFs (enthalten Kundendaten). Ältere Vorschauen werden dabei gelöscht;
    noch geöffnete (gesperrte) Dateien bleiben bis zum nächsten Mal liegen."""
    import tempfile
    import time
    d = Path(tempfile.gettempdir()) / f"{APP_NAME}-Vorschau"
    d.mkdir(exist_ok=True)
    limit = time.time() - cleanup_hours * 3600
    for f in d.glob("*.pdf"):
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass
    return d


def default_backup_dir() -> Path:
    if not IS_RELEASE or os.environ.get("FAKTURA_DATA_DIR"):
        return data_dir() / "backup"
    return Path.home() / "Documents" / f"{APP_NAME}-Sicherung"


def font_paths() -> dict[str, Path]:
    """Carlito ist metrisch kompatibel zu Calibri und frei einbettbar (PDF/A)."""
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [ASSETS_DIR / "fonts", win]
    names = {
        "regular": ["Carlito-Regular.ttf", "DejaVuSans.ttf", "arial.ttf"],
        "bold": ["Carlito-Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"],
        "italic": ["Carlito-Italic.ttf", "DejaVuSans-Oblique.ttf", "ariali.ttf"],
    }
    result = {}
    for style, files in names.items():
        for f in files:
            hit = next((d / f for d in candidates if (d / f).exists()), None)
            if hit:
                result[style] = hit
                break
    return result


def srgb_icc_path() -> Path | None:
    for p in [
        ASSETS_DIR / "sRGB.icc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "spool" / "drivers" / "color" / "sRGB Color Space Profile.icm",
    ]:
        if p.exists():
            return p
    return None
