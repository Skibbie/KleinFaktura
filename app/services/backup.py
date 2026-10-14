"""Backup (SQLite-Online-Backup + PDF-Archiv + Logo als ZIP) mit Rotation, und Rücksicherung."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from app import config
from app.db.database import get_int_setting, get_setting, set_setting
from app.services import audit, backup_crypto

PREFIX = "faktura-backup-"


def backup_dir(con: sqlite3.Connection) -> Path:
    """Eingestellter Sicherungsordner; ist er nicht anlegbar (z.B. nach Rücksicherung auf einem anderen PC der
    Ordner eines anderen Benutzers), wird in den Standardordner gesichert statt bei jedem Beenden zu scheitern."""
    p = Path(get_setting(con, "backup.dir") or config.default_backup_dir())
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        p = config.default_backup_dir()
        p.mkdir(parents=True, exist_ok=True)
    return p


def create_backup(con: sqlite3.Connection, target_dir: Path | None = None) -> Path:
    target_dir = Path(target_dir) if target_dir else backup_dir(con)
    if config.inside_archive(target_dir):
        raise ValueError("Der Sicherungsordner liegt im Beleg-Archiv – jede Sicherung würde die vorherigen "
                         "enthalten. Bitte einen anderen Ordner wählen.")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    encrypt = get_setting(con, "backup.encrypt") == "1"
    zpath = target_dir / f"{PREFIX}{stamp}{'.enc' if encrypt else '.zip'}"
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "faktura.sqlite3"
        dst = sqlite3.connect(str(snap))
        con.backup(dst)
        dst.close()
        # unverschlüsselt entsteht das ZIP nur im lokalen Temp-Ordner, nie im (evtl. Cloud-)Sicherungsordner
        tmpzip = Path(tmp) / "sicherung.zip" if encrypt else zpath.with_suffix(".zip.part")
        with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "faktura.sqlite3")
            arch = config.archive_dir()
            for f in arch.rglob("*"):
                if f.is_file():
                    z.write(f, f"archiv/{f.relative_to(arch).as_posix()}")
            logo = config.resolve_stored_path(get_setting(con, "company.logo")) or Path()
            if logo.name and logo.is_file():
                z.write(logo, f"logo/{logo.name}")
            z.writestr("INFO.txt", f"{config.APP_NAME} {config.APP_VERSION}\nBackup vom {datetime.now():%d.%m.%Y %H:%M:%S}\n"
                                   f"{audit.head(con)}\n")
        if encrypt:
            part = zpath.with_suffix(".enc.part")
            backup_crypto.encrypt_file(tmpzip, part, get_setting(con, "backup.password"))
            os.replace(part, zpath)
        else:
            os.replace(tmpzip, zpath)
    set_setting(con, "backup.last", datetime.now().isoformat(timespec="seconds"))
    rotate(con, target_dir)
    return zpath


def fallback_notice(con: sqlite3.Connection, zpath: Path) -> str:
    """Hinweistext, wenn die Sicherung nicht im eingestellten Ordner (z.B. abgezogener USB-Stick) gelandet ist."""
    wanted = (get_setting(con, "backup.dir") or "").strip()
    if not wanted or Path(wanted).resolve() == zpath.parent.resolve():
        return ""
    return (f"Der Sicherungsordner {wanted} war nicht erreichbar (z.B. USB-Stick oder Netzlaufwerk nicht "
            f"angeschlossen). Die Sicherung wurde stattdessen hier abgelegt:\n{zpath}\n\n"
            "Bitte bei Gelegenheit eine Sicherung in den richtigen Ordner machen (Export & Sicherung).")


def list_backups(con: sqlite3.Connection) -> list[Path]:
    d = backup_dir(con)
    return sorted(_backup_files(d), reverse=True)


def _backup_files(d: Path) -> list[Path]:
    return [*d.glob(f"{PREFIX}*.zip"), *d.glob(f"{PREFIX}*.enc")]


def rotate(con: sqlite3.Connection, target_dir: Path) -> None:
    keep_daily = get_int_setting(con, "backup.keep_daily", "30")
    keep_monthly = get_int_setting(con, "backup.keep_monthly", "12")
    files = sorted(_backup_files(target_dir), key=lambda f: f.stem, reverse=True)
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


def decrypt_for_restore(path: Path, password: str) -> Path:
    """Entschlüsselt eine .enc-Sicherung in den lokalen Datenordner (nicht nach %TEMP%); restore_backup löscht die
    Kopie danach wieder (delete_after=True)."""
    out = config.data_dir() / f"entschluesselt-{datetime.now():%Y%m%d-%H%M%S}.zip"
    try:
        backup_crypto.decrypt_file(Path(path), out, password)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return out


def check_backup(zip_path: Path) -> None:
    """Prüft vor der Rücksicherung, ob die Datei eine Sicherung ist, die dieses Programm öffnen kann.
    Eine Sicherung einer neueren Version würde sonst eingespielt und dann beim Start abgelehnt."""
    from app.db.database import SCHEMA_VERSION
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ValueError(f"Die Datei ist keine lesbare Sicherung: {e}") from e
    with z, tempfile.TemporaryDirectory() as tmp:
        if "faktura.sqlite3" not in z.namelist():
            raise ValueError("Keine gültige Sicherung (faktura.sqlite3 fehlt).")
        _restore_members(z)                  # Größen/Anzahl prüfen, bevor etwas entpackt wird
        db = sqlite3.connect(z.extract("faktura.sqlite3", tmp))
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        finally:
            db.close()
    if version > SCHEMA_VERSION:
        raise ValueError(f"Die Sicherung stammt von einer neueren Programmversion (Datenbank-Schema {version}, "
                         f"dieses Programm kennt {SCHEMA_VERSION}). Bitte zuerst {config.APP_NAME} aktualisieren. "
                         "Es wurde nichts verändert.")


MAX_RESTORE_BYTES = 20 * 1024 ** 3      # ausgepackt höchstens 20 GB
MAX_RESTORE_FILES = 200_000
MAX_RATIO = 1000                         # stärker komprimiert ist keine echte Belegdatei (ZIP-Bombe)
_LOGO_NAME = re.compile(r"^[\w .\-]+\.(png|jpe?g)$", re.IGNORECASE)


def _restore_members(z: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    """Prüft die Einträge der Sicherung und gibt (Eintrag, Zielpfad relativ zum Datenordner) zurück.
    Übernommen werden nur Datenbank, Archivdateien (keine Programme/Skripte) und ein Logo mit Bildendung."""
    out, total = [], 0
    infos = z.infolist()
    if len(infos) > MAX_RESTORE_FILES:
        raise ValueError("Die Sicherung enthält ungewöhnlich viele Dateien und wird nicht eingespielt.")
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.endswith("/"):
            continue
        total += info.file_size
        if total > MAX_RESTORE_BYTES or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO
                                         and info.file_size > 50 * 1024 ** 2):
            raise ValueError("Die Sicherung ist ungewöhnlich groß bzw. stark gepackt und wird nicht eingespielt.")
        parts = name.split("/")
        if any(p in ("", ".", "..") or ":" in p for p in parts):
            continue                                   # Pfadtricks (absolut, Laufwerk, ..) nie übernehmen
        if name == "faktura.sqlite3":
            out.append((info, "faktura.sqlite3"))
        elif parts[0] == "archiv" and len(parts) > 1 and not config.is_risky_file(name):
            out.append((info, name))
        elif parts[0] == "logo" and len(parts) == 2 and _LOGO_NAME.match(parts[1]):
            out.append((info, f"logo/{parts[1]}"))
    return out


# Gerätebezogene Einstellungen (Ordner, Programme, Server) gelten für diesen PC und kommen nie aus der Sicherung:
# eine präparierte Sicherung könnte sonst z.B. alle künftigen Sicherungen oder PDF-Kopien auf ein fremdes
# Netzlaufwerk umleiten; bei einem Umzug auf einen neuen PC passen die alten Pfade ohnehin nicht.
LOCAL_SETTINGS = ("backup.dir", "pdf.export_dir", "pdf.export_auto", "pdf.export_year_dirs", "mail.thunderbird_path",
                  "hottgenroth.server", "hottgenroth.driver")


def _keep_local_settings(current_db: Path, restored_db: Path) -> None:
    current = {}
    if current_db.exists():
        c = sqlite3.connect(current_db.as_uri() + "?mode=ro", uri=True)
        try:
            marks = ",".join("?" for _ in LOCAL_SETTINGS)
            current = dict(c.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", LOCAL_SETTINGS).fetchall())
        except sqlite3.Error:
            current = {}
        finally:
            c.close()
    from app.db.database import DEFAULT_SETTINGS
    r = sqlite3.connect(str(restored_db))
    try:
        for key in LOCAL_SETTINGS:
            value = current.get(key, DEFAULT_SETTINGS.get(key, ""))
            r.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        r.commit()
    finally:
        r.close()


def restore_backup(zip_path: Path, delete_after: bool = False) -> None:
    try:
        _restore_backup(Path(zip_path))
    finally:
        if delete_after:
            Path(zip_path).unlink(missing_ok=True)


def _restore_backup(zip_path: Path) -> None:
    """Stellt Datenbank und Archiv aus einem Backup wieder her. Die DB-Verbindung muss geschlossen sein.

    Ablauf ohne halbfertige Zustände: erst alles in einen Zwischenordner entpacken und prüfen, dann den aktuellen
    Stand in „vor-ruecksicherung-…“ verschieben (das Archiv zuerst – eine geöffnete Datei blockiert genau das) und
    den neuen Stand einsetzen. Scheitert ein Schritt, wird alles zurückgeschoben; der alte Stand bleibt nutzbar."""
    zip_path = Path(zip_path)
    check_backup(zip_path)
    data = config.data_dir()
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    staging = data / f"ruecksicherung-neu-{stamp}"
    safety = data / f"vor-ruecksicherung-{stamp}"
    staging.mkdir()
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info, rel in _restore_members(z):
                dest = staging.joinpath(*rel.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        _keep_local_settings(config.db_path(), staging / "faktura.sqlite3")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    db = config.db_path()
    arch = data / "archiv"
    moves: list[tuple[Path, Path]] = []      # erledigte Verschiebungen (von, nach) für die Rücknahme

    def move(src: Path, dst: Path) -> None:
        os.replace(src, dst)
        moves.append((src, dst))

    try:
        safety.mkdir()
        if arch.exists():
            move(arch, safety / "archiv")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                move(p, safety / p.name)
        move(staging / "faktura.sqlite3", db)
        if (staging / "archiv").exists():
            move(staging / "archiv", arch)
        for logo in (staging / "logo").glob("*") if (staging / "logo").exists() else []:
            dest = data / logo.name
            if dest.exists():
                move(dest, safety / dest.name)
            move(logo, dest)
    except OSError as e:
        for src, dst in reversed(moves):
            try:
                os.replace(dst, src)
            except OSError:
                pass
        shutil.rmtree(staging, ignore_errors=True)
        try:
            safety.rmdir()                   # nur wenn leer (alles zurückgeschoben)
        except OSError:
            pass
        if isinstance(e, PermissionError):
            raise ValueError("Die Rücksicherung wurde abgebrochen, weil eine Datei noch geöffnet ist (z.B. ein "
                             "Archiv-PDF im PDF-Betrachter oder eine zweite KleinFaktura-Instanz). Bitte schließen "
                             "und erneut versuchen. Es wurde nichts verändert.") from e
        raise ValueError(f"Die Rücksicherung wurde abgebrochen: {e}. Es wurde nichts verändert.") from e
    shutil.rmtree(staging, ignore_errors=True)
    for f in arch.rglob("*") if arch.exists() else []:
        if f.is_file():
            os.chmod(f, stat.S_IREAD)       # wie im laufenden Betrieb schreibgeschützt
