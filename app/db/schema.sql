-- KleinFaktura Datenbankschema (SQLite)
-- Geldbeträge in Cent (INTEGER), Mengen/Steuersätze als Dezimal-Text.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS customer (
    id                INTEGER PRIMARY KEY,
    customer_no       TEXT UNIQUE,
    salutation        TEXT DEFAULT '',      -- Herr / Frau / Firma / ''
    title             TEXT DEFAULT '',
    first_name        TEXT DEFAULT '',
    last_name         TEXT DEFAULT '',
    company           TEXT DEFAULT '',
    letter_salutation TEXT DEFAULT '',      -- z.B. "Sehr geehrte Frau Mustermann,"
    street            TEXT DEFAULT '',
    zip               TEXT DEFAULT '',
    city              TEXT DEFAULT '',
    country           TEXT DEFAULT 'DE',
    email             TEXT DEFAULT '',
    phone             TEXT DEFAULT '',
    mobile            TEXT DEFAULT '',
    vat_id            TEXT DEFAULT '',      -- USt-IdNr. (Geschäftskunden)
    buyer_reference   TEXT DEFAULT '',      -- Leitweg-ID / Käuferreferenz (E-Rechnung)
    notes             TEXT DEFAULT '',
    source            TEXT DEFAULT 'manuell', -- manuell / hottgenroth / csv / import
    hottgenroth_uuid  TEXT UNIQUE,
    hottgenroth_hash  TEXT,                 -- Hash des zuletzt übernommenen Hottgenroth-Stands
    locally_modified  INTEGER DEFAULT 0,    -- nach Import lokal geändert
    active            INTEGER DEFAULT 1,
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    updated_at        TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS project (
    id               INTEGER PRIMARY KEY,
    customer_id      INTEGER NOT NULL REFERENCES customer(id),
    name             TEXT DEFAULT '',
    object_street    TEXT DEFAULT '',
    object_zip       TEXT DEFAULT '',
    object_city      TEXT DEFAULT '',
    hottgenroth_uuid TEXT UNIQUE,
    notes            TEXT DEFAULT '',
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS document (
    id                INTEGER PRIMARY KEY,
    doc_type          TEXT NOT NULL CHECK (doc_type IN ('angebot','rechnung','storno')),
    number            TEXT UNIQUE,          -- NULL solange Entwurf
    status            TEXT NOT NULL DEFAULT 'entwurf' CHECK (status IN ('entwurf','festgeschrieben')),
    customer_id       INTEGER REFERENCES customer(id),
    project_id        INTEGER REFERENCES project(id),
    doc_date          TEXT,
    service_date      TEXT,
    service_date_to   TEXT,
    due_date          TEXT,
    valid_until       TEXT,                 -- Angebote
    subject           TEXT DEFAULT '',
    salutation_text   TEXT DEFAULT '',
    header_text       TEXT DEFAULT '',
    footer_text       TEXT DEFAULT '',
    notes_text        TEXT DEFAULT '',      -- Hinweise (§19, Leistungsdatum ...)
    recipient_json    TEXT,                 -- Empfänger-Schnappschuss beim Festschreiben
    company_json      TEXT,                 -- Absender-Schnappschuss beim Festschreiben
    tax_mode          TEXT NOT NULL DEFAULT 'kleinunternehmer' CHECK (tax_mode IN ('kleinunternehmer','regel')),
    total_net_cents   INTEGER DEFAULT 0,
    total_tax_cents   INTEGER DEFAULT 0,
    total_gross_cents INTEGER DEFAULT 0,
    ref_document_id   INTEGER REFERENCES document(id), -- Storno -> Rechnung, Rechnung -> Angebot
    zugferd           INTEGER DEFAULT 0,
    girocode          INTEGER DEFAULT 1,
    pdf_path          TEXT,
    pdf_sha256        TEXT,
    xml_sha256        TEXT,
    source            TEXT DEFAULT 'app' CHECK (source IN ('app','import')),
    import_file_path  TEXT,
    import_sha256     TEXT,
    import_note       TEXT,
    finalized_at      TEXT,
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    updated_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_document_customer ON document(customer_id);
CREATE INDEX IF NOT EXISTS ix_document_ref ON document(ref_document_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_document_import_sha ON document(import_sha256) WHERE import_sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS document_line (
    id               INTEGER PRIMARY KEY,
    document_id      INTEGER NOT NULL REFERENCES document(id),
    pos              INTEGER NOT NULL,
    title            TEXT DEFAULT '',
    description      TEXT DEFAULT '',
    quantity         TEXT DEFAULT '1',
    unit             TEXT DEFAULT '',
    unit_price_cents INTEGER DEFAULT 0,
    tax_rate         TEXT DEFAULT '0',
    total_cents      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_line_doc ON document_line(document_id);

CREATE TABLE IF NOT EXISTS payment (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES document(id),
    pay_date     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,          -- negative Beträge = Korrekturbuchung
    method       TEXT DEFAULT 'Überweisung',
    note         TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_payment_doc ON payment(document_id);

CREATE TABLE IF NOT EXISTS dunning (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES document(id),
    level        INTEGER NOT NULL,          -- 1 = Zahlungserinnerung, 2 = 1. Mahnung, 3 = 2. Mahnung
    dunning_date TEXT NOT NULL,
    new_due_date TEXT,
    fee_cents    INTEGER DEFAULT 0,
    open_cents   INTEGER DEFAULT 0,
    pdf_path     TEXT,
    pdf_sha256   TEXT,
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_dunning_doc ON dunning(document_id, level);

CREATE TABLE IF NOT EXISTS textblock (
    id       INTEGER PRIMARY KEY,
    category TEXT NOT NULL,                 -- anrede, kopf_rechnung, kopf_angebot, fuss_rechnung, fuss_angebot, hinweis, position, mahnung1..3
    title    TEXT NOT NULL,
    body     TEXT DEFAULT '',
    is_default INTEGER DEFAULT 0,
    sort     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS article (
    id               INTEGER PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT DEFAULT '',
    unit             TEXT DEFAULT 'Stunden',
    unit_price_cents INTEGER DEFAULT 0,
    tax_rate         TEXT DEFAULT '19',
    active           INTEGER DEFAULT 1,
    sort             INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS number_range (
    doc_type   TEXT NOT NULL,               -- rechnung (inkl. Storno), angebot
    period     TEXT NOT NULL,               -- JJJJMM
    last_value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_type, period)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    data      TEXT,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- GoBD: Unveränderbarkeit festgeschriebener Belege
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_document_no_update_final
BEFORE UPDATE ON document
WHEN OLD.status <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'GoBD: festgeschriebener Beleg darf nicht geändert werden');
END;

CREATE TRIGGER IF NOT EXISTS trg_document_no_delete_final
BEFORE DELETE ON document
WHEN OLD.status <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'GoBD: festgeschriebener Beleg darf nicht gelöscht werden');
END;

CREATE TRIGGER IF NOT EXISTS trg_line_no_insert_final
BEFORE INSERT ON document_line
WHEN (SELECT status FROM document WHERE id = NEW.document_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'GoBD: Positionen eines festgeschriebenen Belegs sind unveränderbar');
END;

CREATE TRIGGER IF NOT EXISTS trg_line_no_update_final
BEFORE UPDATE ON document_line
WHEN (SELECT status FROM document WHERE id = OLD.document_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'GoBD: Positionen eines festgeschriebenen Belegs sind unveränderbar');
END;

CREATE TRIGGER IF NOT EXISTS trg_line_no_delete_final
BEFORE DELETE ON document_line
WHEN (SELECT status FROM document WHERE id = OLD.document_id) <> 'entwurf'
BEGIN
    SELECT RAISE(ABORT, 'GoBD: Positionen eines festgeschriebenen Belegs sind unveränderbar');
END;

CREATE TRIGGER IF NOT EXISTS trg_document_number_only_final
BEFORE UPDATE ON document
WHEN OLD.status = 'entwurf' AND NEW.status = 'entwurf' AND NEW.number IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Entwürfe erhalten keine Belegnummer');
END;

-- Zahlungen, Mahnungen und Protokoll: nur anfügen (Korrektur per Gegenbuchung)
CREATE TRIGGER IF NOT EXISTS trg_payment_no_update BEFORE UPDATE ON payment
BEGIN SELECT RAISE(ABORT, 'GoBD: Zahlungen sind unveränderbar – bitte Korrekturbuchung erfassen'); END;
CREATE TRIGGER IF NOT EXISTS trg_payment_no_delete BEFORE DELETE ON payment
BEGIN SELECT RAISE(ABORT, 'GoBD: Zahlungen sind unveränderbar – bitte Korrekturbuchung erfassen'); END;
CREATE TRIGGER IF NOT EXISTS trg_payment_only_final BEFORE INSERT ON payment
WHEN (SELECT status FROM document WHERE id = NEW.document_id) <> 'festgeschrieben'
BEGIN SELECT RAISE(ABORT, 'Zahlungen nur auf festgeschriebene Belege'); END;
CREATE TRIGGER IF NOT EXISTS trg_dunning_no_update BEFORE UPDATE ON dunning
BEGIN SELECT RAISE(ABORT, 'GoBD: Mahnungen sind unveränderbar'); END;
CREATE TRIGGER IF NOT EXISTS trg_dunning_no_delete BEFORE DELETE ON dunning
BEGIN SELECT RAISE(ABORT, 'GoBD: Mahnungen sind unveränderbar'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'Protokoll ist unveränderbar'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'Protokoll ist unveränderbar'); END;

-- Nummernkreise dürfen nur steigen
CREATE TRIGGER IF NOT EXISTS trg_number_range_monotonic BEFORE UPDATE ON number_range
WHEN NEW.last_value < OLD.last_value
BEGIN SELECT RAISE(ABORT, 'Nummernkreis darf nicht zurückgesetzt werden'); END;

-- ---------------------------------------------------------------------------
-- Ausgabenbuch (Betriebsausgaben für die EÜR)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expense (
    id             INTEGER PRIMARY KEY,
    exp_date       TEXT NOT NULL,
    supplier       TEXT DEFAULT '',
    description    TEXT DEFAULT '',
    category       TEXT NOT NULL,
    amount_cents   INTEGER NOT NULL,        -- Bruttobetrag (Kleinunternehmer: Vorsteuer nicht abziehbar)
    vat_rate       TEXT DEFAULT '19',       -- USt-Satz laut Beleg (für die 800-€-netto-Grenze)
    km             TEXT,                    -- Fahrten mit Privat-Pkw
    useful_life    INTEGER,                 -- Nutzungsdauer in Jahren (Abschreibung)
    payment_method TEXT DEFAULT 'Überweisung',
    receipt_path   TEXT,
    receipt_sha256 TEXT,
    note           TEXT DEFAULT '',
    source         TEXT DEFAULT 'manuell',  -- manuell / bank
    bank_ref       TEXT,                    -- Fingerabdruck der Bankbuchung (Dublettenprüfung)
    created_at     TEXT DEFAULT (datetime('now','localtime')),
    updated_at     TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_expense_date ON expense(exp_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_expense_bank_ref ON expense(bank_ref) WHERE bank_ref IS NOT NULL;

CREATE TABLE IF NOT EXISTS expense_lock (
    year      TEXT PRIMARY KEY,
    locked_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TRIGGER IF NOT EXISTS trg_expense_lock_insert BEFORE INSERT ON expense
WHEN EXISTS (SELECT 1 FROM expense_lock WHERE year = substr(NEW.exp_date, 1, 4))
BEGIN SELECT RAISE(ABORT, 'Das Jahr ist abgeschlossen – Ausgaben können nicht mehr hinzugefügt werden'); END;
CREATE TRIGGER IF NOT EXISTS trg_expense_lock_update BEFORE UPDATE ON expense
WHEN EXISTS (SELECT 1 FROM expense_lock WHERE year IN (substr(OLD.exp_date, 1, 4), substr(NEW.exp_date, 1, 4)))
BEGIN SELECT RAISE(ABORT, 'Das Jahr ist abgeschlossen – Ausgaben sind unveränderbar'); END;
CREATE TRIGGER IF NOT EXISTS trg_expense_lock_delete BEFORE DELETE ON expense
WHEN EXISTS (SELECT 1 FROM expense_lock WHERE year = substr(OLD.exp_date, 1, 4))
BEGIN SELECT RAISE(ABORT, 'Das Jahr ist abgeschlossen – Ausgaben sind unveränderbar'); END;
CREATE TRIGGER IF NOT EXISTS trg_expense_lock_no_delete BEFORE DELETE ON expense_lock
BEGIN SELECT RAISE(ABORT, 'Ein Jahresabschluss kann nicht zurückgenommen werden'); END;

-- ---------------------------------------------------------------------------
-- Fahrten mit dem Privat-Pkw (Kilometerpauschale, zählt in der EÜR als eigener Bereich)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trip (
    id           INTEGER PRIMARY KEY,
    trip_date    TEXT NOT NULL,
    customer_id  INTEGER REFERENCES customer(id),
    project_id   INTEGER REFERENCES project(id),
    document_id  INTEGER REFERENCES document(id),   -- zugehöriges Angebot / zugehörige Rechnung
    destination  TEXT NOT NULL,                     -- Zieladresse
    purpose      TEXT NOT NULL,                     -- Anlass, z.B. Kundentermin vor Ort
    km_one_way   TEXT,                              -- einfache Strecke
    round_trip   INTEGER DEFAULT 1,                 -- 1 = Hin- und Rückfahrt
    km           TEXT NOT NULL,                     -- gefahrene Kilometer gesamt
    rate         TEXT NOT NULL,                     -- Pauschale je km zum Zeitpunkt der Erfassung
    amount_cents INTEGER NOT NULL,
    note         TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_trip_date ON trip(trip_date);
CREATE INDEX IF NOT EXISTS ix_trip_customer ON trip(customer_id);

-- Jahresabschluss (expense_lock) sperrt auch die Fahrten
CREATE TRIGGER IF NOT EXISTS trg_trip_lock_insert BEFORE INSERT ON trip
WHEN EXISTS (SELECT 1 FROM expense_lock WHERE year = substr(NEW.trip_date, 1, 4))
BEGIN SELECT RAISE(ABORT, 'Das Jahr ist abgeschlossen – Fahrten können nicht mehr hinzugefügt werden'); END;
CREATE TRIGGER IF NOT EXISTS trg_trip_lock_update BEFORE UPDATE ON trip
WHEN EXISTS (SELECT 1 FROM expense_lock WHERE year IN (substr(OLD.trip_date, 1, 4), substr(NEW.trip_date, 1, 4)))
BEGIN SELECT RAISE(ABORT, 'Das Jahr ist abgeschlossen – Fahrten sind unveränderbar'); END;
CREATE TRIGGER IF NOT EXISTS trg_trip_lock_delete BEFORE DELETE ON trip
WHEN EXISTS (SELECT 1 FROM expense_lock WHERE year = substr(OLD.trip_date, 1, 4))
BEGIN SELECT RAISE(ABORT, 'Das Jahr ist abgeschlossen – Fahrten sind unveränderbar'); END;

-- ---------------------------------------------------------------------------
-- Notizen (optional einem Kunden / Objekt zugeordnet; keine Buchungsbelege)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS note (
    id          INTEGER PRIMARY KEY,
    note_date   TEXT NOT NULL,
    customer_id INTEGER REFERENCES customer(id),
    project_id  INTEGER REFERENCES project(id),
    title       TEXT DEFAULT '',
    body        TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_note_customer ON note(customer_id);
