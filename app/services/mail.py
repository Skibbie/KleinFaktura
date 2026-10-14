"""Belege per E-Mail (Thunderbird-Verfassen-Fenster mit PDF-Anhang) und PDF-Kopie in einen Wunschordner.

Gesendet wird nie automatisch: Thunderbird öffnet nur das vorausgefüllte Fenster, der Nutzer prüft und sendet.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from app import config
from app.db import repo
from app.db.database import get_setting, transaction
from app.services import audit
from app.services.documents import DOC_TYPE_LABEL, placeholders, render

TB_DEFAULT_PATHS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Mozilla Thunderbird" / "thunderbird.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Mozilla Thunderbird" / "thunderbird.exe",
]


class MailError(Exception):
    pass


def document_pdf(doc: dict) -> Path | None:
    """Das Archiv-PDF eines festgeschriebenen Belegs. Importierte Altbelege haben nur dann eins, wenn die
    Originaldatei ein PDF war (sonst ist pdf_path leer und die .ods/.xlsx-Datei wird nicht als PDF versendet)."""
    if doc.get("status") != "festgeschrieben":
        return None
    return config.resolve_stored_path(doc.get("pdf_path"))


def _template_kind(doc: dict) -> str:
    return "angebot" if doc["doc_type"] == "angebot" else "rechnung"


def valid_thunderbird_path(path: str) -> bool:
    """Nur eine Datei namens thunderbird.exe außerhalb des Datenordners, kein Netzwerkpfad – ein manipulierter Pfad
    (z.B. aus einer präparierten Sicherung) soll kein beliebiges Programm starten können."""
    p = Path(path)
    if p.name.lower() != "thunderbird.exe" or config.is_unc(path):
        return False
    try:
        p.resolve().relative_to(config.data_dir().resolve())
        return False
    except ValueError:
        return True


def find_thunderbird(con: sqlite3.Connection) -> Path | None:
    """Einstellung > Registry (App Paths) > Standard-Installationsordner."""
    own = get_setting(con, "mail.thunderbird_path").strip().strip('"')
    if own:
        return Path(own) if valid_thunderbird_path(own) and Path(own).is_file() else None
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\thunderbird.exe") as k:
                    p = Path(winreg.QueryValue(k, None).strip('"'))
                    if p.is_file():
                        return p
            except OSError:
                pass
    except ImportError:  # nicht Windows
        pass
    return next((p for p in TB_DEFAULT_PATHS if p.is_file()), None)


def _quote(value: str) -> str:
    # Thunderbird trennt die Felder an Kommas außerhalb von '…'; ein ' im Wert würde das Feld beenden.
    return "'" + (value or "").replace("'", "\u2019") + "'"


def compose_arg(to: str, subject: str, body: str, attachment: Path | None) -> str:
    parts = [f"to={_quote(to)}", f"subject={_quote(subject)}", f"body={_quote(body)}"]
    if attachment:
        parts.append(f"attachment={_quote(Path(attachment).resolve().as_uri())}")
    return ",".join(parts)


def mail_texts(con: sqlite3.Connection, doc: dict) -> tuple[str, str, str]:
    """Empfänger, Betreff und Text (Platzhalter ersetzt) für einen festgeschriebenen Beleg."""
    kind = _template_kind(doc)
    values = placeholders(con, doc, extra={"belegart": DOC_TYPE_LABEL[doc["doc_type"]], "betreff": doc.get("subject") or ""})
    cust = repo.get_customer(con, doc["customer_id"]) if doc.get("customer_id") else {}
    to = ((cust or {}).get("email") or "").strip()
    return to, render(get_setting(con, f"mail.subject_{kind}"), values), render(get_setting(con, f"mail.body_{kind}"), values)


def _launch(args: list[str]) -> None:
    subprocess.Popen(args)  # noqa: S603 – Programmpfad und Argumente als Liste, keine Shell


def open_compose(con: sqlite3.Connection, doc: dict) -> str:
    """Öffnet Thunderbird mit vorausgefüllter Mail und PDF-Anhang. Gibt den Empfänger zurück (ggf. leer)."""
    pdf = document_pdf(doc)
    if not pdf and doc.get("status") == "festgeschrieben":
        raise MailError("Zu diesem importierten Beleg gibt es kein PDF (die Originaldatei ist eine Tabelle). "
                        "Bitte die Rechnung als PDF exportieren und selbst anhängen.")
    if not pdf:
        raise MailError("Nur festgeschriebene Belege können versendet werden.")
    if not pdf.exists():
        raise MailError(f"Das Archiv-PDF wurde nicht gefunden:\n{pdf}")
    exe = find_thunderbird(con)
    if not exe:
        raise MailError("Thunderbird wurde nicht gefunden. Bitte unter Einstellungen → E-Mail & PDF-Ablage den Pfad "
                        "zu thunderbird.exe angeben.")
    to, subject, body = mail_texts(con, doc)
    _launch([str(exe), "-compose", compose_arg(to, subject, body, pdf)])
    with transaction(con):
        audit.log(con, "mail", "document", doc["id"], {"to": to, "number": doc.get("number")})
    return to


def export_copy(con: sqlite3.Connection, doc: dict) -> Path | None:
    """Kopiert das Archiv-PDF in den Wunschordner (falls eingeschaltet). Das Archiv selbst bleibt unberührt."""
    target_dir = get_setting(con, "pdf.export_dir").strip()
    pdf = document_pdf(doc)
    if get_setting(con, "pdf.export_auto") != "1" or not target_dir or not pdf:
        return None
    target_dir = Path(target_dir)
    if config.inside_archive(target_dir):
        raise ValueError("Der PDF-Ordner liegt im Beleg-Archiv. Bitte unter Einstellungen → E-Mail & PDF-Ablage "
                         "einen Ordner außerhalb wählen – das Archiv bleibt unverändert.")
    if get_setting(con, "pdf.export_year_dirs") == "1":
        target_dir = target_dir / (doc.get("doc_date") or "")[:4]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / pdf.name
    if target.exists():
        target.chmod(0o666)
    # copyfile statt copy2: das Archiv-PDF ist schreibgeschützt, die Kopie soll es nicht sein
    shutil.copyfile(pdf, target)
    return target
