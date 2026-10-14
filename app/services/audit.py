"""Append-only-Protokoll mit Hash-Kette (Manipulationen werden erkennbar)."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from app.db.database import dumps

GENESIS = "0" * 64


def _hash(prev_hash: str, ts: str, action: str, entity: str, entity_id, data: str) -> str:
    payload = "|".join([prev_hash, ts, action, entity, str(entity_id if entity_id is not None else ""), data])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log(con: sqlite3.Connection, action: str, entity: str, entity_id: int | None, data=None) -> None:
    row = con.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = row[0] if row else GENESIS
    ts = datetime.now().isoformat(timespec="seconds")
    data_s = dumps(data or {})
    h = _hash(prev, ts, action, entity, entity_id, data_s)
    con.execute(
        "INSERT INTO audit_log(ts, action, entity, entity_id, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
        (ts, action, entity, entity_id, data_s, prev, h),
    )


def head(con: sqlite3.Connection) -> str:
    """Kettenkopf (Anzahl Einträge und letzter Hash) zum Festhalten außerhalb der Datenbank (Sicherung, Export):
    ein späterer Abgleich macht auch das Entfernen der letzten Einträge erkennbar, das verify() allein nicht sieht."""
    n, last = con.execute("SELECT COUNT(*), (SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1) FROM audit_log").fetchone()
    return f"Protokoll: {n} Einträge, letzter Hash {last or GENESIS}"


def verify(con: sqlite3.Connection) -> tuple[bool, str]:
    """Prüft die komplette Kette. Rückgabe (ok, Meldung)."""
    prev = GENESIS
    n = 0
    for r in con.execute("SELECT * FROM audit_log ORDER BY id"):
        n += 1
        if r["prev_hash"] != prev:
            return False, f"Kette unterbrochen bei Eintrag {r['id']} ({r['ts']})"
        expected = _hash(r["prev_hash"], r["ts"], r["action"], r["entity"], r["entity_id"], r["data"])
        if expected != r["hash"]:
            return False, f"Eintrag {r['id']} ({r['ts']}, {r['action']}) wurde verändert"
        prev = r["hash"]
    return True, f"Protokoll in Ordnung ({n} Einträge)"
