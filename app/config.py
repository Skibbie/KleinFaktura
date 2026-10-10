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
APP_VERSION = "2.0.0"
# Frühere Programmnamen: deren Datenverzeichnis wird weiterverwendet, solange es noch kein eigenes gibt
LEGACY_NAMES = ["EEE-Faktura"]
DB_FILE = "faktura.sqlite3"


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
