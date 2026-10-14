"""Datenzugriff für Stammdaten: Kunden, Projekte, Textbausteine, Artikel."""
from __future__ import annotations

import sqlite3

from app.db.database import row_to_dict, transaction
from app.services import audit

CUSTOMER_FIELDS = [
    "customer_no", "salutation", "title", "first_name", "last_name", "company", "letter_salutation",
    "street", "zip", "city", "country", "email", "phone", "mobile", "vat_id", "buyer_reference", "notes",
    "source", "hottgenroth_uuid", "hottgenroth_hash", "locally_modified", "active",
]
PROJECT_FIELDS = ["customer_id", "name", "object_street", "object_zip", "object_city", "hottgenroth_uuid", "notes"]


# ---------------------------------------------------------------- Kunden
def customer_display_name(c: dict) -> str:
    if not c:
        return ""
    person = " ".join(x for x in [c.get("title"), c.get("first_name"), c.get("last_name")] if x)
    if c.get("company"):
        return f"{c['company']}" + (f" ({person})" if person else "")
    return person or "(ohne Namen)"


def default_letter_salutation(c: dict) -> str:
    sal = (c.get("salutation") or "").strip().lower()
    name = " ".join(x for x in [c.get("title"), c.get("last_name")] if x)
    if sal.startswith("frau") and name:
        return f"Sehr geehrte Frau {name},"
    if sal.startswith("herr") and name:
        return f"Sehr geehrter Herr {name},"
    return "Sehr geehrte Damen und Herren,"


def recipient_lines(c: dict) -> list[str]:
    lines = []
    if c.get("company"):
        lines.append(c["company"])
    person = " ".join(x for x in [c.get("salutation") if c.get("company") else "", c.get("title"), c.get("first_name"), c.get("last_name")] if x)
    if person:
        lines.append(person)
    if c.get("street"):
        lines.append(c["street"])
    zc = " ".join(x for x in [c.get("zip"), c.get("city")] if x)
    if zc:
        lines.append(zc)
    if c.get("country") and c["country"].upper() not in ("DE", "DEUTSCHLAND", ""):
        lines.append(c["country"])
    return lines


def next_customer_no(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT MAX(CAST(SUBSTR(customer_no, 2) AS INTEGER)) FROM customer WHERE customer_no GLOB 'K[0-9]*'"
    ).fetchone()
    return f"K{(row[0] or 10000) + 1}"


def list_customers(con: sqlite3.Connection, search: str = "", include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM customer WHERE 1=1"
    args: list = []
    if not include_inactive:
        sql += " AND active=1"
    if search:
        like = f"%{search}%"
        sql += " AND (last_name LIKE ? OR first_name LIKE ? OR company LIKE ? OR city LIKE ? OR customer_no LIKE ? OR email LIKE ?)"
        args += [like] * 6
    sql += " ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE, company COLLATE NOCASE"
    return [dict(r) for r in con.execute(sql, args)]


def get_customer(con: sqlite3.Connection, cid: int) -> dict | None:
    return row_to_dict(con.execute("SELECT * FROM customer WHERE id=?", (cid,)).fetchone())


def save_customer(con: sqlite3.Connection, data: dict, *, mark_modified: bool = True) -> int:
    data = {k: v for k, v in data.items() if k in CUSTOMER_FIELDS or k == "id"}
    with transaction(con):
        cid = data.pop("id", None)
        if cid:
            old = get_customer(con, cid)
            if mark_modified and old and old.get("hottgenroth_uuid"):
                data.setdefault("locally_modified", 1)
            sets = ", ".join(f"{k}=?" for k in data)
            con.execute(f"UPDATE customer SET {sets}, updated_at=datetime('now','localtime') WHERE id=?", [*data.values(), cid])
            audit.log(con, "update", "customer", cid, {k: v for k, v in data.items() if not old or old.get(k) != v})
        else:
            data.setdefault("customer_no", next_customer_no(con))
            if not data.get("letter_salutation"):
                data["letter_salutation"] = default_letter_salutation(data)
            cols = ", ".join(data)
            qs = ", ".join("?" for _ in data)
            cur = con.execute(f"INSERT INTO customer({cols}) VALUES ({qs})", list(data.values()))
            cid = cur.lastrowid
            audit.log(con, "create", "customer", cid, data)
    return cid


def deactivate_customer(con: sqlite3.Connection, cid: int) -> None:
    """Kunden mit Belegen, Fahrten oder Notizen werden nur deaktiviert (Bezug bleibt erhalten)."""
    with transaction(con):
        used = con.execute("SELECT (SELECT COUNT(*) FROM document WHERE customer_id=?) + (SELECT COUNT(*) FROM trip "
                           "WHERE customer_id=?) + (SELECT COUNT(*) FROM note WHERE customer_id=?)",
                           (cid, cid, cid)).fetchone()[0]
        if used:
            con.execute("UPDATE customer SET active=0 WHERE id=?", (cid,))
            audit.log(con, "deactivate", "customer", cid)
        else:
            con.execute("DELETE FROM project WHERE customer_id=?", (cid,))
            con.execute("DELETE FROM customer WHERE id=?", (cid,))
            audit.log(con, "delete", "customer", cid)


ISO_COUNTRIES = set("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC
CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE
GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM
KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH
SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG
VI VN VU WF WS XK YE YT ZA ZM ZW""".split())
_COUNTRY_NAMES = {
    "deutschland": "DE", "germany": "DE", "d": "DE", "deu": "DE", "brd": "DE", "österreich": "AT", "oesterreich": "AT",
    "austria": "AT", "a": "AT", "schweiz": "CH", "switzerland": "CH", "ch": "CH", "niederlande": "NL", "holland": "NL",
    "netherlands": "NL", "belgien": "BE", "belgium": "BE", "frankreich": "FR", "france": "FR", "luxemburg": "LU",
    "luxembourg": "LU", "dänemark": "DK", "daenemark": "DK", "denmark": "DK", "polen": "PL", "poland": "PL",
    "tschechien": "CZ", "czechia": "CZ", "italien": "IT", "italy": "IT", "spanien": "ES", "spain": "ES",
    "liechtenstein": "LI", "großbritannien": "GB", "grossbritannien": "GB", "vereinigtes königreich": "GB",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "schweden": "SE", "sweden": "SE", "norwegen": "NO",
    "norway": "NO", "ungarn": "HU", "hungary": "HU", "slowenien": "SI", "kroatien": "HR", "portugal": "PT",
    "irland": "IE", "ireland": "IE", "finnland": "FI", "finland": "FI", "griechenland": "GR", "greece": "GR",
    "usa": "US", "vereinigte staaten": "US", "united states": "US", "türkei": "TR", "tuerkei": "TR", "turkey": "TR",
}


def country_code(value: str | None) -> str | None:
    """Ländercode nach ISO 3166-1 (z.B. „Österreich“ → AT); leer → DE; unbekannt → None (nicht raten)."""
    v = (value or "").strip()
    if not v:
        return "DE"
    if v.upper() in ISO_COUNTRIES:
        return v.upper()
    return _COUNTRY_NAMES.get(v.lower())


def reactivate_customer(con: sqlite3.Connection, cid: int) -> None:
    with transaction(con):
        con.execute("UPDATE customer SET active=1 WHERE id=?", (cid,))
        audit.log(con, "reactivate", "customer", cid)


def find_similar_customers(con: sqlite3.Connection, c: dict) -> list[dict]:
    """Mögliche Dubletten: gleicher Nachname/Firma und gleiche PLZ, oder gleiche E-Mail."""
    res = []
    if c.get("email"):
        res += [dict(r) for r in con.execute("SELECT * FROM customer WHERE email<>'' AND LOWER(email)=LOWER(?)", (c["email"],))]
    name = c.get("last_name") or c.get("company")
    if name:
        res += [dict(r) for r in con.execute(
            "SELECT * FROM customer WHERE (LOWER(last_name)=LOWER(?) OR LOWER(company)=LOWER(?)) AND (zip=? OR ?='' OR zip='')",
            (name, name, c.get("zip") or "", c.get("zip") or ""),
        )]
    seen, out = set(), []
    for r in res:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


# ---------------------------------------------------------------- Projekte
def list_projects(con: sqlite3.Connection, customer_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM project WHERE customer_id=? ORDER BY id DESC", (customer_id,))]


def get_project(con: sqlite3.Connection, pid: int) -> dict | None:
    return row_to_dict(con.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone())


def save_project(con: sqlite3.Connection, data: dict) -> int:
    data = {k: v for k, v in data.items() if k in PROJECT_FIELDS or k == "id"}
    with transaction(con):
        pid = data.pop("id", None)
        if pid:
            sets = ", ".join(f"{k}=?" for k in data)
            con.execute(f"UPDATE project SET {sets} WHERE id=?", [*data.values(), pid])
            audit.log(con, "update", "project", pid, data)
        else:
            cols = ", ".join(data)
            cur = con.execute(f"INSERT INTO project({cols}) VALUES ({', '.join('?' for _ in data)})", list(data.values()))
            pid = cur.lastrowid
            audit.log(con, "create", "project", pid, data)
    return pid


def delete_project(con: sqlite3.Connection, pid: int) -> None:
    with transaction(con):
        if con.execute("SELECT COUNT(*) FROM document WHERE project_id=?", (pid,)).fetchone()[0]:
            raise ValueError("Projekt wird in Belegen verwendet und kann nicht gelöscht werden.")
        if con.execute("SELECT COUNT(*) FROM trip WHERE project_id=?", (pid,)).fetchone()[0]:
            raise ValueError("Zu diesem Projekt sind Fahrten erfasst, es kann nicht gelöscht werden.")
        con.execute("UPDATE note SET project_id=NULL WHERE project_id=?", (pid,))  # Notizen bleiben beim Kunden
        con.execute("DELETE FROM project WHERE id=?", (pid,))
        audit.log(con, "delete", "project", pid)


def project_address(p: dict | None) -> str:
    if not p:
        return ""
    zc = " ".join(x for x in [p.get("object_zip"), p.get("object_city")] if x)
    return ", ".join(x for x in [p.get("object_street"), zc] if x)


# ---------------------------------------------------------------- Textbausteine
TEXT_CATEGORIES = {
    "anrede": "Anrede",
    "kopf_rechnung": "Einleitung Rechnung",
    "kopf_angebot": "Einleitung Angebot",
    "fuss_rechnung": "Schluss Rechnung",
    "fuss_angebot": "Schluss Angebot",
    "gruss": "Grußformel",
    "hinweis": "Hinweise",
    "storno": "Einleitung Storno",
    "position": "Positionstexte",
    "fahrt_anlass": "Anlass Fahrt",
    "mahnung1": "Zahlungserinnerung",
    "mahnung2": "1. Mahnung",
    "mahnung3": "2. Mahnung",
}


def list_textblocks(con: sqlite3.Connection, category: str | None = None) -> list[dict]:
    if category:
        rows = con.execute("SELECT * FROM textblock WHERE category=? ORDER BY is_default DESC, sort, title", (category,))
    else:
        rows = con.execute("SELECT * FROM textblock ORDER BY category, sort, title")
    return [dict(r) for r in rows]


def default_text(con: sqlite3.Connection, category: str) -> str:
    row = con.execute(
        "SELECT body FROM textblock WHERE category=? ORDER BY is_default DESC, sort LIMIT 1", (category,)
    ).fetchone()
    return row[0] if row else ""


def save_textblock(con: sqlite3.Connection, data: dict) -> int:
    with transaction(con):
        if data.get("is_default"):
            con.execute("UPDATE textblock SET is_default=0 WHERE category=?", (data["category"],))
        if data.get("id"):
            con.execute(
                "UPDATE textblock SET category=?, title=?, body=?, is_default=? WHERE id=?",
                (data["category"], data["title"], data.get("body", ""), int(bool(data.get("is_default"))), data["id"]),
            )
            return data["id"]
        cur = con.execute(
            "INSERT INTO textblock(category, title, body, is_default, sort) VALUES (?,?,?,?,?)",
            (data["category"], data["title"], data.get("body", ""), int(bool(data.get("is_default"))), 999),
        )
        return cur.lastrowid


def remember_text(con: sqlite3.Connection, category: str, text: str) -> bool:
    """Eingabe als Textbaustein übernehmen, falls es den Text in der Kategorie noch nicht gibt. True = neu angelegt."""
    text = (text or "").strip()
    if not text:
        return False
    for tb in list_textblocks(con, category):
        if (tb["body"] or "").strip().casefold() == text.casefold():
            return False
    title = text.splitlines()[0]
    save_textblock(con, {"category": category, "title": title[:60] + ("…" if len(title) > 60 else ""), "body": text})
    return True


def text_choices(con: sqlite3.Connection, category: str) -> list[str]:
    """Texte einer Kategorie zur Auswahl in Eingabefeldern."""
    return [tb["body"] for tb in list_textblocks(con, category) if (tb["body"] or "").strip()]


def delete_textblock(con: sqlite3.Connection, tid: int) -> None:
    con.execute("DELETE FROM textblock WHERE id=?", (tid,))


# ---------------------------------------------------------------- Artikel
def list_articles(con: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM article" + ("" if include_inactive else " WHERE active=1") + " ORDER BY sort, title"
    return [dict(r) for r in con.execute(sql)]


def save_article(con: sqlite3.Connection, data: dict) -> int:
    fields = ["title", "description", "unit", "unit_price_cents", "tax_rate", "active"]
    vals = [data.get(f) for f in fields]
    if data.get("id"):
        con.execute(f"UPDATE article SET {', '.join(f + '=?' for f in fields)} WHERE id=?", [*vals, data["id"]])
        return data["id"]
    cur = con.execute(f"INSERT INTO article({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", vals)
    return cur.lastrowid


def delete_article(con: sqlite3.Connection, aid: int) -> None:
    con.execute("DELETE FROM article WHERE id=?", (aid,))
