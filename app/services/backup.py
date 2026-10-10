"""Backup (SQLite-Online-Backup + PDF-Archiv + Logo als ZIP) mit Rotation, und Rücksicherung."""
from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from app import config
from app.db.database import get_setting, set_setting

PREFIX = "faktura-backup-"


def backup_dir(con: sqlite3.Connection) -> Path:
    p = Path(get_setting(con, "backup.dir") or config.default_backup_dir())
    p.mkdir(parents=True, exist_ok=True)
    return p


def create_backup(con: sqlite3.Connection, target_dir: Path | None = None) -> Path:
    target_dir = Path(target_dir) if target_dir else backup_dir(con)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zpath = target_dir / f"{PREFIX}{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "faktura.sqlite3"
        dst = sqlite3.connect(str(snap))
        con.backup(dst)
        dst.close()
        tmpzip = zpath.with_suffix(".zip.part")
        with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "faktura.sqlite3")
            arch = config.archive_dir()
            for f in arch.rglob("*"):
                if f.is_file():
                    z.write(f, f"archiv/{f.relative_to(arch).as_posix()}")
            logo = Path(get_setting(con, "company.logo") or "")
            if logo.name and logo.is_file():
                z.write(logo, f"logo/{logo.name}")
            z.writestr("INFO.txt", f"{config.APP_NAME} {config.APP_VERSION}\nBackup vom {datetime.now():%d.%m.%Y %H:%M:%S}\n")
        os.replace(tmpzip, zpath)
    set_setting(con, "backup.last", datetime.now().isoformat(timespec="seconds"))
    rotate(con, target_dir)
    return zpath


def list_backups(con: sqlite3.Connection) -> list[Path]:
    d = backup_dir(con)
    return sorted(d.glob(f"{PREFIX}*.zip"), reverse=True)


def rotate(con: sqlite3.Connection, target_dir: Path) -> None:
    keep_daily = int(get_setting(con, "backup.keep_daily", "30"))
    keep_monthly = int(get_setting(con, "backup.keep_monthly", "12"))
    files = sorted(target_dir.glob(f"{PREFIX}*.zip"), reverse=True)
    keep: set[Path] = set()
    days, months = [], []
    for f in files:
        stamp = f.stem[len(PREFIX):]
        day, month = stamp[:8], stamp[:6]
        if day not in days and len(days) < keep_daily:
            days.append(day)
            keep.add(f)
        if month not in months and len(months) < keep_monthly:
            months.append(month)
            keep.add(f)
    keep.update(files[:3])  # die letzten drei immer behalten
    for f in files:
        if f not in keep:
            try:
                f.unlink()
            except OSError:
                pass


def needs_daily_backup(con: sqlite3.Connection) -> bool:
    last = get_setting(con, "backup.last")
    return not last or last[:10] < datetime.now().date().isoformat()


def restore_backup(zip_path: Path) -> None:
    """Stellt Datenbank und Archiv aus einem Backup wieder her. Die DB-Verbindung muss geschlossen sein.
    Der aktuelle Stand wird vorher als Sicherheitskopie im Datenverzeichnis abgelegt."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        if "faktura.sqlite3" not in z.namelist():
            raise ValueError("Keine gültige Sicherung (faktura.sqlite3 fehlt).")
        data = config.data_dir()
        safety = data / f"vor-ruecksicherung-{datetime.now():%Y%m%d-%H%M%S}"
        safety.mkdir()
        db = config.db_path()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                os.replace(p, safety / p.name)
        arch = config.archive_dir()
        if arch.exists():
            for f in arch.rglob("*"):
                if f.is_file():
                    f.chmod(stat.S_IWRITE | stat.S_IREAD)
            os.replace(arch, safety / "archiv")
        z.extract("faktura.sqlite3", data)
        for name in z.namelist():
            if name.startswith("archiv/") and not name.endswith("/"):
                z.extract(name, data)
            elif name.startswith("logo/") and name.count("/") == 1 and not name.endswith("/"):
                # Logo zurück in den Datenordner (die Einstellung company.logo verweist dorthin)
                dest = data / name[len("logo/"):]
                if dest.exists():
                    os.replace(dest, safety / dest.name)
                dest.write_bytes(z.read(name))
