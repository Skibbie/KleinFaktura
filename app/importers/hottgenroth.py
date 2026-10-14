"""Kundenimport direkt aus der Hottgenroth-/ETU-Datenbank (SQL Server LocalDB, Instanz HSETU).

Ausschließlich lesender Zugriff (SELECT, ApplicationIntent=ReadOnly, pyodbc readonly=True).
Quelle: HsetuCimData.dbo.CmContact / CmAddress / CmConnect.
Die ProjectUUID der Kontakte verweist nicht eindeutig auf ein Projekt – Objektadressen werden deshalb
in KleinFaktura als Projekt am Kunden gepflegt.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field

from app.db import repo
from app.db.database import get_setting, set_setting, transaction
from app.services import audit

COMPARE_FIELDS = ["salutation", "title", "first_name", "last_name", "company", "letter_salutation",
                  "street", "zip", "city", "country", "email", "phone", "mobile"]
FIELD_LABELS = {
    "salutation": "Anrede", "title": "Titel", "first_name": "Vorname", "last_name": "Nachname", "company": "Firma",
    "letter_salutation": "Briefanrede", "street": "Straße", "zip": "PLZ", "city": "Ort", "country": "Land",
    "email": "E-Mail", "phone": "Telefon", "mobile": "Mobil",
}

QUERY = """
SELECT CAST(c.UUID AS nvarchar(36)) AS uuid, c.ContactType, c.GenderType, c.Salutation, c.Title, c.FirstName,
       c.LastName, c.CompanyName, c.LetterSalutation, c.StampEdit,
       a.Street, a.HouseNumber, a.PostalCode, a.City, a.Country,
       k.TelephoneNumber, k.MobileTelephoneNumber, k.EmailAddress
FROM dbo.CmContact c
OUTER APPLY (SELECT TOP 1 * FROM dbo.CmAddress a WHERE a.ContactUUID = c.UUID ORDER BY a.AddressType, a.SortNum) a
OUTER APPLY (SELECT TOP 1 * FROM dbo.CmConnect k WHERE k.ContactUUID = c.UUID
             ORDER BY CASE WHEN ISNULL(k.EmailAddress,'')<>'' THEN 0 ELSE 1 END, k.SortNum) k
"""


class HottgenrothError(Exception):
    pass


def _clean(v) -> str:
    return "" if v is None else str(v).strip()


_SERVER_OK = re.compile(r"^[\w.\-()\\]+(,\d+)?$")
_DRIVER_OK = re.compile(r"^[\w .\-]+$")


def check_connection_settings(server: str, driver: str) -> None:
    """Keine Zusatzangaben im Verbindungstext (z.B. „;Database=…“ oder „{…}“). Der Server selbst darf auch ein
    anderer Rechner sein (Mehrplatz-Installation); eine Rücksicherung übernimmt diese Einstellung nie aus der
    Sicherung, sodass sie nur der Anwender selbst setzen kann."""
    if not _SERVER_OK.match(server or "") or not _DRIVER_OK.match(driver or ""):
        raise HottgenrothError("Ungültige Angabe für SQL-Server oder ODBC-Treiber (Einstellungen → Hottgenroth).")


def connection_string(con: sqlite3.Connection | None = None, database: str = "HsetuCimData") -> str:
    server = get_setting(con, "hottgenroth.server") if con else r"(localdb)\HSETU"
    driver = get_setting(con, "hottgenroth.driver") if con else "SQL Server Native Client 11.0"
    check_connection_settings(server, driver)
    return (f"DRIVER={{{driver}}};Server={server};Database={database};Trusted_Connection=yes;"
            "ApplicationIntent=ReadOnly;")


def _start_localdb(server: str) -> None:
    if not server.lower().startswith("(localdb)\\"):
        return
    exe = shutil.which("sqllocaldb")
    if not exe:
        return
    instance = server.split("\\", 1)[1]
    subprocess.run([exe, "start", instance], capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def fetch_contacts(con: sqlite3.Connection | None = None) -> list[dict]:
    try:
        import pyodbc
    except ImportError as e:
        raise HottgenrothError("Das Python-Paket 'pyodbc' ist nicht installiert.") from e
    cs = connection_string(con)
    if "SQL Server Native Client 11.0" not in cs and not any("SQL Server" in d for d in pyodbc.drivers()):
        raise HottgenrothError("Kein SQL-Server-ODBC-Treiber gefunden.")
    try:
        db = pyodbc.connect(cs, timeout=10, readonly=True, autocommit=True)
    except pyodbc.Error:
        _start_localdb(get_setting(con, "hottgenroth.server") if con else r"(localdb)\HSETU")
        try:
            db = pyodbc.connect(cs, timeout=20, readonly=True, autocommit=True)
        except pyodbc.Error as e:
            raise HottgenrothError(
                "Keine Verbindung zur Hottgenroth-Datenbank. Ist die Hottgenroth-Software installiert und "
                f"die LocalDB-Instanz verfügbar?\n\nDetails: {e}") from e
    try:
        cur = db.cursor()
        cur.execute(QUERY)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        db.close()
    return [map_contact(r) for r in rows]


def map_contact(r: dict) -> dict:
    street = " ".join(x for x in [_clean(r.get("Street")), _clean(r.get("HouseNumber"))] if x)
    salutation = _clean(r.get("Salutation"))
    if not salutation:
        salutation = {1: "Herr", 2: "Frau"}.get(r.get("GenderType"), "")
    country = _clean(r.get("Country")) or "DE"
    if country.lower() in ("deutschland", "germany", "d"):
        country = "DE"
    c = {
        "hottgenroth_uuid": _clean(r.get("uuid")).lower(),
        "salutation": salutation,
        "title": _clean(r.get("Title")),
        "first_name": _clean(r.get("FirstName")),
        "last_name": _clean(r.get("LastName")),
        "company": _clean(r.get("CompanyName")),
        "letter_salutation": _clean(r.get("LetterSalutation")),
        "street": street,
        "zip": _clean(r.get("PostalCode")),
        "city": _clean(r.get("City")),
        "country": country,
        "email": _clean(r.get("EmailAddress")),
        "phone": _clean(r.get("TelephoneNumber")),
        "mobile": _clean(r.get("MobileTelephoneNumber")),
    }
    if c["letter_salutation"] and not c["letter_salutation"].endswith(","):
        c["letter_salutation"] += ","
    if not c["letter_salutation"]:
        c["letter_salutation"] = repo.default_letter_salutation(c)
    c["hottgenroth_hash"] = contact_hash(c)
    return c


def contact_hash(c: dict) -> str:
    return hashlib.sha256(json.dumps([c.get(f, "") for f in COMPARE_FIELDS], ensure_ascii=False).encode()).hexdigest()


# ---------------------------------------------------------------- Abgleich
NEW_STATUSES = ("neu", "dublette")   # noch nicht mit einem Kunden verknüpft
IGNORED_KEY = "hottgenroth.ignored"  # JSON-Liste der UUIDs, die bewusst nicht importiert wurden


def ignored_uuids(con: sqlite3.Connection) -> set[str]:
    try:
        return set(json.loads(get_setting(con, IGNORED_KEY) or "[]"))
    except ValueError:
        return set()


def _save_ignored(con: sqlite3.Connection, uuids: set[str]) -> None:
    linked = {r[0] for r in con.execute("SELECT hottgenroth_uuid FROM customer WHERE hottgenroth_uuid IS NOT NULL")}
    set_setting(con, IGNORED_KEY, json.dumps(sorted(uuids - linked)))


@dataclass
class ImportItem:
    incoming: dict
    status: str                      # neu / unveraendert / aktualisiert / kollision / dublette
    local: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    diff: list[str] = field(default_factory=list)
    # Entscheidung (vom Dialog gesetzt): new / take_remote / keep_local / merge / skip / link
    decision: str = ""
    merged: dict | None = None       # bei merge: Feldwerte
    link_to: int | None = None       # bei Dublette: lokale Kunden-ID
    ignored: bool = False            # früher bewusst nicht importiert

    @property
    def is_new(self) -> bool:
        return self.status in NEW_STATUSES


def diff_fields(local: dict, incoming: dict) -> list[str]:
    return [f for f in COMPARE_FIELDS if _clean(local.get(f)) != _clean(incoming.get(f))]


def plan_import(con: sqlite3.Connection, contacts: list[dict]) -> list[ImportItem]:
    ignored = ignored_uuids(con)
    items = []
    for c in contacts:
        local = repo.row_to_dict(con.execute("SELECT * FROM customer WHERE hottgenroth_uuid=?",
                                             (c["hottgenroth_uuid"],)).fetchone()) if c["hottgenroth_uuid"] else None
        if local:
            diff = diff_fields(local, c)
            if not diff:
                items.append(ImportItem(c, "unveraendert", local, decision="skip"))
            elif local.get("hottgenroth_hash") == c["hottgenroth_hash"]:
                # Hottgenroth unverändert, nur lokal geändert -> lokal behalten
                items.append(ImportItem(c, "unveraendert", local, diff=diff, decision="keep_local"))
            elif local.get("locally_modified"):
                items.append(ImportItem(c, "kollision", local, diff=diff))
            else:
                items.append(ImportItem(c, "aktualisiert", local, diff=diff, decision="take_remote"))
            continue
        similar = [s for s in repo.find_similar_customers(con, c) if not s.get("hottgenroth_uuid")]
        if similar:
            items.append(ImportItem(c, "dublette", candidates=similar, local=similar[0],
                                    diff=diff_fields(similar[0], c)))
        else:
            items.append(ImportItem(c, "neu", decision="new"))
        items[-1].ignored = c["hottgenroth_uuid"] in ignored
    return items


def apply_import(con: sqlite3.Connection, items: list[ImportItem]) -> dict:
    """Übernimmt die Entscheidungen. Übersprungene neue Kontakte werden gemerkt und künftig ausgeblendet."""
    stats = {"neu": 0, "aktualisiert": 0, "verknuepft": 0, "uebersprungen": 0}
    ignored = ignored_uuids(con)
    with transaction(con):
        for it in items:
            c = dict(it.incoming)
            dec = it.decision or "skip"
            if dec == "skip" or (dec == "keep_local" and it.local is None):
                if it.is_new and c["hottgenroth_uuid"]:
                    ignored.add(c["hottgenroth_uuid"])
                stats["uebersprungen"] += 1
                continue
            if dec == "new":
                c["source"] = "hottgenroth"
                if it.status == "dublette":
                    # bewusst als eigener Kunde: UUID trotzdem setzen
                    pass
                repo.save_customer(con, c, mark_modified=False)
                stats["neu"] += 1
            elif dec == "take_remote":
                c["id"] = it.link_to or it.local["id"]
                c["locally_modified"] = 0
                repo.save_customer(con, c, mark_modified=False)
                stats["aktualisiert"] += 1
            elif dec == "keep_local":
                # nur Verknüpfung/Hash aktualisieren, damit die Kollision nicht erneut gemeldet wird
                target = it.link_to or it.local["id"]
                con.execute("UPDATE customer SET hottgenroth_uuid=?, hottgenroth_hash=? WHERE id=?",
                            (c["hottgenroth_uuid"], c["hottgenroth_hash"], target))
                audit.log(con, "hottgenroth_keep_local", "customer", target, {"uuid": c["hottgenroth_uuid"]})
                stats["verknuepft"] += 1
            elif dec == "merge":
                target = it.link_to or it.local["id"]
                data = dict(it.merged or {})
                data.update(id=target, hottgenroth_uuid=c["hottgenroth_uuid"], hottgenroth_hash=c["hottgenroth_hash"],
                            locally_modified=1 if any(_clean(data.get(f)) != _clean(c.get(f)) for f in COMPARE_FIELDS) else 0)
                repo.save_customer(con, data, mark_modified=False)
                stats["aktualisiert"] += 1
        _save_ignored(con, ignored)
        audit.log(con, "hottgenroth_import", "customer", None, stats)
    return stats
