"""SQLite-Verbindung, Schema-Initialisierung, Einstellungen und Startdaten."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app import config

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

# Bei jeder Schemaänderung erhöhen (PRAGMA user_version). 1 = Stand 1.0.0 inkl. Fahrten, 2 = Notizen (1.1.0).
SCHEMA_VERSION = 2


class NewerDatabaseError(RuntimeError):
    """Die Datenbank wurde von einer neueren Programmversion angelegt."""

DEFAULT_SETTINGS: dict[str, str] = {
    # Firmenstammdaten: werden beim ersten Start unter „Einstellungen“ erfasst (nicht im Quellcode)
    "company.name": "",
    "company.line1": "",
    "company.owner": "",
    "company.street": "",
    "company.zip": "",
    "company.city": "",
    "company.country": "DE",
    "company.phone": "",
    "company.email": "",
    "company.web": "",
    "company.tax_number": "",
    "company.vat_id": "",
    "company.iban": "",
    "company.bic": "",
    "company.bank": "",
    "company.logo": "",  # leer = assets/logo.jpg
    # Steuer
    "tax.mode": "kleinunternehmer",  # kleinunternehmer / regel
    "tax.default_rate": "19",
    "ku.basis": "ist",  # ist = vereinnahmte Entgelte, soll = berechnete Entgelte
    "ku.limit_prev": "25000",
    "ku.limit_curr": "100000",
    "ku.warn_percent": "80",
    # Belege
    "invoice.payment_days": "14",
    "offer.valid_days": "30",
    "invoice.girocode": "1",
    "invoice.zugferd_default": "0",
    "number.invoice_prefix": "",
    "number.offer_prefix": "AN",
    "number.digits": "2",
    # Mahnwesen
    "dunning.fee_1": "0",
    "dunning.fee_2": "5",
    "dunning.fee_3": "10",
    "dunning.days": "10",
    # Archiv / Backup
    "retention.years": "8",
    "backup.dir": str(config.default_backup_dir()),
    "backup.keep_daily": "30",
    "backup.keep_monthly": "12",
    "backup.last": "",
    # E-Mail-Versand über Thunderbird (Pfad leer = automatisch suchen)
    "mail.thunderbird_path": "",
    "mail.subject_rechnung": "{belegart} {belegnr} vom {datum}",
    "mail.body_rechnung": "{briefanrede},\n\nanbei erhalten Sie die {belegart} {belegnr} vom {datum} über {betrag}.\n"
                          "Bitte überweisen Sie den Betrag bis zum {faellig}.\n\nMit freundlichen Grüßen\n{inhaber}\n{firma}",
    "mail.subject_angebot": "Angebot {belegnr} vom {datum}",
    "mail.body_angebot": "{briefanrede},\n\nanbei erhalten Sie unser Angebot {belegnr} vom {datum}. "
                         "Es ist gültig bis zum {gueltig_bis}.\n\nBei Fragen melden Sie sich gerne.\n\n"
                         "Mit freundlichen Grüßen\n{inhaber}\n{firma}",
    # PDF-Kopie nach dem Festschreiben in einen eigenen Ordner (zusätzlich zum Archiv)
    "pdf.export_auto": "0",
    "pdf.export_dir": "",
    "pdf.export_year_dirs": "1",
    # Ausgaben
    "expense.km_rate": "0,30",
    # Textbausteine: Haken „In Textbausteine übernehmen“ unter Eingabefeldern standardmäßig gesetzt
    "textblock.remember_default": "1",
    # DATEV (SKR03: 1200 Bank, 8195 Erlöse Kleinunternehmer)
    "datev.konto_bank": "1200",
    "datev.konto_erloes": "8195",
    "datev.berater": "1001",
    "datev.mandant": "1",
    # Branchenprofil (siehe PROFILES): Startartikel, Fahrtanlässe, Hottgenroth-Import
    "profile": "allgemein",
    # Hottgenroth (nur Profil Energieberatung)
    "hottgenroth.server": r"(localdb)\HSETU",
    "hottgenroth.driver": "SQL Server Native Client 11.0",
}

DEFAULT_TEXTBLOCKS = [
    ("anrede", "Standard-Anrede", "{briefanrede}", 1),
    ("kopf_rechnung", "Rechnung Standard",
     "für die Erledigung der von Ihnen beauftragten Tätigkeiten berechne ich Ihnen wie folgt:", 1),
    ("kopf_angebot", "Angebot Standard",
     "vielen Dank für Ihre Anfrage. Gerne biete ich Ihnen folgende Leistungen an:", 1),
    ("fuss_rechnung", "Rechnung Standard",
     "Vielen Dank für Ihren Auftrag!\n"
     "Ich bitte um Überweisung des Rechnungsbetrages innerhalb von {zahlungsziel} Tagen bis zum {faellig} an\n"
     "IBAN: {iban}\n"
     "Verwendungszweck: {belegnr}", 1),
    ("fuss_angebot", "Angebot Standard",
     "Dieses Angebot ist gültig bis zum {gueltig_bis}.\n"
     "Über Ihren Auftrag würde ich mich sehr freuen. Bei Fragen stehe ich Ihnen gerne zur Verfügung.", 1),
    ("gruss", "Standard-Gruß", "Mit freundlichen Grüßen\n\n{inhaber}", 1),
    ("hinweis", "Kleinunternehmer + Leistungsdatum",
     "Hinweis: Als Kleinunternehmer im Sinne von § 19 Abs. 1 UStG wird Umsatzsteuer nicht berechnet.\n"
     "(Das Rechnungsdatum entspricht dem Leistungsdatum)", 1),
    ("hinweis", "Nur Leistungsdatum", "(Das Rechnungsdatum entspricht dem Leistungsdatum)", 0),
    ("storno", "Stornorechnung",
     "hiermit storniere ich die Rechnung {ref_belegnr} vom {ref_datum} vollständig.", 1),
    ("mahnung1", "Zahlungserinnerung",
     "sicher ist es Ihrer Aufmerksamkeit entgangen, dass die folgende Rechnung noch offen ist. "
     "Ich bitte Sie, den offenen Betrag von {offen} bis zum {neu_faellig} zu überweisen.\n"
     "Sollten Sie die Zahlung bereits veranlasst haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.", 1),
    ("mahnung2", "1. Mahnung",
     "leider konnte ich bis heute keinen Zahlungseingang zu der folgenden Rechnung feststellen. "
     "Ich bitte Sie, den offenen Betrag zzgl. Mahngebühr, insgesamt {gesamt}, bis zum {neu_faellig} zu überweisen.", 1),
    ("mahnung3", "2. Mahnung",
     "trotz meiner Zahlungserinnerung und Mahnung ist die folgende Rechnung weiterhin offen. "
     "Ich fordere Sie letztmalig auf, den Betrag von {gesamt} bis zum {neu_faellig} zu überweisen. "
     "Nach Ablauf dieser Frist werde ich ohne weitere Ankündigung rechtliche Schritte einleiten.", 1),
    ("position", "Objektadresse", "{objekt_adresse}", 0),
]

@dataclass(frozen=True)
class Profile:
    """Branchenprofil: Startartikel (Titel, Beschreibung, Einheit, Cent) und Fahrtanlässe (Kategorie fahrt_anlass)."""
    label: str
    articles: tuple[tuple[str, str, str, int], ...]
    trip_purposes: tuple[str, ...]
    hottgenroth: bool = False


PROFILES: dict[str, Profile] = {
    "allgemein": Profile(
        "Allgemein",
        (("Arbeitsleistung", "nach Aufwand", "Stunden", 6000),
         ("Pauschale", "", "pauschal", 0),
         ("Material", "", "Stück", 0),
         ("Fahrtkosten", "", "km", 30)),
        ("Kundentermin vor Ort", "Besprechung", "Auftragsausführung", "Fortbildung", "Büromaterial / Einkauf",
         "Bank / Post")),
    "energieberatung": Profile(
        "Energieberatung",
        (("Energieberatung", "Beratungsleistung nach Aufwand", "Stunden", 12000),
         ("Raumweise Heizlastberechnung", "nach DIN EN 12831", "Stunden", 12000),
         ("Abschätzung der Heizflächenauslegung", "", "Stunden", 12000),
         ("Hydraulischer Abgleich", "Berechnung Verfahren B", "Stunden", 12000),
         ("Fahrtkosten", "", "km", 30)),
        ("Vor-Ort-Termin Gebäudeaufnahme", "Vor-Ort-Termin iSFP", "Übergabe / Besprechung Ergebnisse",
         "Baubegleitung", "Abnahme / Baustellenkontrolle", "Blower-Door-Messung", "Fortbildung",
         "Büromaterial / Einkauf"),
        hottgenroth=True),
}


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or config.db_path()), isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = FULL")
    return con


def schema_version(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def backup_before_upgrade(con: sqlite3.Connection, old_version: int) -> Path | None:
    """Kopie der Datenbank vor einem Schemaumbau (data_dir/vor-update/)."""
    from datetime import datetime
    target_dir = config.data_dir() / "vor-update"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"faktura-schema{old_version}-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    dest = sqlite3.connect(str(target))
    try:
        con.backup(dest)
    finally:
        dest.close()
    return target


def init_db(con: sqlite3.Connection) -> None:
    current = schema_version(con)
    if current > SCHEMA_VERSION:
        raise NewerDatabaseError(
            f"Die Datenbank stammt von einer neueren Programmversion (Schema {current}, dieses Programm kennt {SCHEMA_VERSION}).\n"
            f"Bitte die aktuelle Version von {config.APP_NAME} starten. Die Daten wurden nicht verändert.")
    is_new = not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
    if current < SCHEMA_VERSION and not is_new:
        backup_before_upgrade(con, current)
    con.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    if current < SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    with transaction(con):
        if not is_new and con.execute("SELECT 1 FROM settings WHERE key='profile'").fetchone() is None:
            # Datenbank aus der Zeit vor den Branchenprofilen (EEE-Faktura): war immer Energieberatung,
            # deren Startartikel und Fahrtanlässe sind schon vorhanden
            con.execute("INSERT INTO settings(key, value) VALUES ('profile', 'energieberatung')")
            con.execute("INSERT INTO settings(key, value) VALUES ('seed.profile.energieberatung', '1')")
            _adopt_legacy_logo(con)
        for k, v in DEFAULT_SETTINGS.items():
            con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        if con.execute("SELECT COUNT(*) FROM textblock").fetchone()[0] == 0:
            for i, (cat, title, body, default) in enumerate(DEFAULT_TEXTBLOCKS):
                con.execute(
                    "INSERT INTO textblock(category, title, body, is_default, sort) VALUES (?,?,?,?,?)",
                    (cat, title, body, default, i),
                )
        if is_new:
            apply_profile(con, get_setting(con, "profile"))
        _seed_once(con)  # nach den Standard-Textbausteinen (die nur in leere Tabellen kommen)


def _seed_once(con: sqlite3.Connection) -> None:
    """Startwerte, die später ergänzt wurden, genau einmal anlegen (auch in bestehenden Datenbanken).
    Vom Anwender gelöschte Einträge kommen dadurch nicht wieder."""
    if con.execute("SELECT 1 FROM settings WHERE key='seed.fahrt_anlass'").fetchone() is None:
        _add_trip_purposes(con, profile(con).trip_purposes)
        set_setting(con, "seed.fahrt_anlass", "1")


def legacy_logo_candidates() -> list[Path]:
    """Logo, das EEE-Faktura fest mitbrachte: im Programmordner der alten Exe oder im Quellcode-Stand davor."""
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return [local / "Programs" / old / "_internal" / "assets" / "logo.jpg" for old in config.LEGACY_NAMES] + \
        [config.ASSETS_DIR / "logo.jpg"]


def _adopt_legacy_logo(con: sqlite3.Connection) -> None:
    """Das eingebaute Logo gibt es nicht mehr: ohne eigenes Logo wird das alte in den Datenordner kopiert."""
    row = con.execute("SELECT value FROM settings WHERE key='company.logo'").fetchone()
    if row and row[0]:
        return
    src = next((p for p in legacy_logo_candidates() if p.is_file()), None)
    if src is None:
        return
    dest = config.data_dir() / "logo.jpg"
    if not dest.exists():
        shutil.copy2(src, dest)
    set_setting(con, "company.logo", str(dest))


def profile(con: sqlite3.Connection) -> Profile:
    """Eingestelltes Branchenprofil."""
    return PROFILES.get(get_setting(con, "profile"), PROFILES["allgemein"])


def apply_profile(con: sqlite3.Connection, key: str) -> None:
    """Profil einstellen. Beim ersten Wählen kommen seine Startartikel und Fahrtanlässe dazu (nur fehlende Titel);
    beim Wechsel wird nichts gelöscht, und vom Anwender Gelöschtes kommt nicht wieder."""
    if key not in PROFILES:
        raise ValueError(f"Unbekanntes Branchenprofil: {key}")
    with transaction(con):
        set_setting(con, "profile", key)
        flag = f"seed.profile.{key}"
        if get_setting(con, flag) == "1":
            return
        p = PROFILES[key]
        have = {r[0] for r in con.execute("SELECT title FROM article")}
        sort = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM article").fetchone()[0]
        for title, desc, unit, price in p.articles:
            if title not in have:
                con.execute("INSERT INTO article(title, description, unit, unit_price_cents, sort) VALUES (?,?,?,?,?)",
                            (title, desc, unit, price, sort))
                sort += 1
        _add_trip_purposes(con, p.trip_purposes)
        set_setting(con, "seed.fahrt_anlass", "1")
        set_setting(con, flag, "1")


def _add_trip_purposes(con: sqlite3.Connection, purposes: tuple[str, ...]) -> None:
    have = {r[0] for r in con.execute("SELECT body FROM textblock WHERE category='fahrt_anlass'")}
    sort = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM textblock WHERE category='fahrt_anlass'").fetchone()[0]
    for text in purposes:
        if text not in have:
            con.execute("INSERT INTO textblock(category, title, body, is_default, sort) VALUES ('fahrt_anlass',?,?,0,?)",
                        (text, text, sort))
            sort += 1


@contextmanager
def transaction(con: sqlite3.Connection, immediate: bool = True):
    """Explizite Transaktion (Verbindung läuft im Autocommit-Modus)."""
    if con.in_transaction:
        # verschachtelt: Savepoint
        con.execute("SAVEPOINT sp")
        try:
            yield con
            con.execute("RELEASE sp")
        except BaseException:
            con.execute("ROLLBACK TO sp")
            con.execute("RELEASE sp")
            raise
        return
    con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def get_setting(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row[0]


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def settings_dict(con: sqlite3.Connection, prefix: str = "") -> dict[str, str]:
    d = {k: v for k, v in DEFAULT_SETTINGS.items() if k.startswith(prefix)}
    for row in con.execute("SELECT key, value FROM settings WHERE key LIKE ?", (prefix + "%",)):
        d[row["key"]] = row["value"]
    return d


def company(con: sqlite3.Connection) -> dict[str, str]:
    return {k.split(".", 1)[1]: v for k, v in settings_dict(con, "company.").items()}


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
