"""Notizen, optional einem Kunden und Objekt zugeordnet (z.B. Telefonate, Absprachen, Vor-Ort-Eindrücke)."""
from __future__ import annotations

import sqlite3

from app.db import repo
from app.db.database import row_to_dict, transaction

FIELDS = ["note_date", "customer_id", "project_id", "title", "body"]


def list_notes(con: sqlite3.Connection, customer_id: int | None = None, search: str = "") -> list[dict]:
    sql = """SELECT n.*, c.first_name, c.last_name, c.company, c.title AS c_title, p.name AS project_name
             FROM note n LEFT JOIN customer c ON c.id=n.customer_id LEFT JOIN project p ON p.id=n.project_id
             WHERE 1=1"""
    args: list = []
    if customer_id:
        sql += " AND n.customer_id=?"
        args.append(customer_id)
    if search:
        like = f"%{search}%"
        sql += (" AND (n.title LIKE ? OR n.body LIKE ? OR c.last_name LIKE ? OR c.first_name LIKE ? "
                "OR c.company LIKE ? OR p.name LIKE ?)")
        args += [like] * 6
    sql += " ORDER BY n.note_date DESC, n.id DESC"
    out = []
    for r in con.execute(sql, args):
        n = dict(r)
        n["customer_name"] = (repo.customer_display_name({**n, "title": n.pop("c_title")})
                              if n.get("customer_id") else "")
        out.append(n)
    return out


def get_note(con: sqlite3.Connection, nid: int) -> dict | None:
    return row_to_dict(con.execute("SELECT * FROM note WHERE id=?", (nid,)).fetchone())


def save_note(con: sqlite3.Connection, data: dict) -> int:
    d = {k: data.get(k) for k in FIELDS}
    d["title"] = (d.get("title") or "").strip()
    d["body"] = (d.get("body") or "").rstrip()
    if not d.get("note_date"):
        raise ValueError("Datum fehlt.")
    if not (d["title"] or d["body"].strip()):
        raise ValueError("Bitte einen Titel oder Text eingeben.")
    if not d.get("customer_id"):
        d["project_id"] = None
    nid = data.get("id")
    with transaction(con):
        if nid:
            sets = ", ".join(f"{k}=?" for k in d)
            con.execute(f"UPDATE note SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", [*d.values(), nid])
        else:
            cur = con.execute(f"INSERT INTO note({', '.join(d)}) VALUES ({', '.join('?' for _ in d)})", list(d.values()))
            nid = cur.lastrowid
    return nid


def delete_note(con: sqlite3.Connection, nid: int) -> None:
    with transaction(con):
        con.execute("DELETE FROM note WHERE id=?", (nid,))
