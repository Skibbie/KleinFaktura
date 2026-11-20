"""Artikel: Kategorien (Schema 3), Suche/Filter, Import aus Excel/CSV mit Dubletten, Vorlage/Export und Oberfläche."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_core import con  # noqa: E402, F401
from test_gui import gui  # noqa: E402, F401 (Fixture)


def _article(con, title, **kw):
    from app.db import repo
    data = {"title": title, "description": "", "unit": "Stunden", "unit_price_cents": 1000, "tax_rate": "19",
            "active": 1, "category": ""}
    data.update(kw)
    return repo.save_article(con, data)


# ------------------------------------------------------------------ Migration
def test_schema_2_database_gets_category_column(tmp_path, monkeypatch):
    """Datenbank im Stand Schema 2 (ohne Kategorie): Spalte kommt dazu, vorhandene Artikel bleiben unverändert."""
    monkeypatch.setenv("FAKTURA_DATA_DIR", str(tmp_path / "data"))
    from app.db import database, repo
    c = database.connect(tmp_path / "v2.sqlite3")
    database.init_db(c)
    # Artikeltabelle wie in Schema 2 nachbauen (SQLite kann Spalten erst ab 3.35 löschen, daher neu anlegen)
    c.executescript("""
        DROP TABLE article;
        CREATE TABLE article (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '',
            unit TEXT DEFAULT 'Stunden', unit_price_cents INTEGER DEFAULT 0, tax_rate TEXT DEFAULT '19',
            active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
        INSERT INTO article(title, description, unit, unit_price_cents, tax_rate, active, sort)
            VALUES ('Altleistung', 'aus Version 2', 'Stück', 4250, '7', 0, 5);
        PRAGMA user_version = 2;
    """)
    database.init_db(c)
    assert database.schema_version(c) == database.SCHEMA_VERSION >= 3
    assert "category" in {r[1] for r in c.execute("PRAGMA table_info(article)")}
    a = repo.list_articles(c, include_inactive=True)[0]
    assert (a["title"], a["description"], a["unit"], a["unit_price_cents"], a["tax_rate"], a["active"], a["sort"],
            a["category"]) == ("Altleistung", "aus Version 2", "Stück", 4250, "7", 0, 5, "")
    assert len(list((tmp_path / "data" / "vor-update").glob("faktura-schema2-*.sqlite3"))) == 1
    c.close()


def test_new_database_has_category(con):
    from app.db import repo
    aid = _article(con, "Neu", category="  Beratung ")
    assert [a["category"] for a in repo.list_articles(con) if a["id"] == aid] == ["Beratung"]


# ------------------------------------------------------------------ Suche / Filter
def test_search_and_category_filter(con):
    from app.db import repo
    _article(con, "Heizlastberechnung", description="nach DIN EN 12831", category="Berechnung")
    _article(con, "Gebäudeaufnahme", description="Vor-Ort-Termin", category="Beratung")
    _article(con, "Übergabe", description="Ergebnisse besprechen", category="beratung")
    _article(con, "Inaktiv", description="Termin", active=0, category="Beratung")
    titles = lambda **kw: sorted(a["title"] for a in repo.list_articles(con, **kw))  # noqa: E731
    assert titles(search="HEIZLAST") == ["Heizlastberechnung"]
    assert titles(search="din en") == ["Heizlastberechnung"]               # Beschreibung zählt mit
    assert titles(search="übergabe") == ["Übergabe"]                        # Umlaute ohne Groß-/Kleinschreibung
    assert titles(search="ÜBERGABE ergebnisse") == ["Übergabe"]             # alle Wörter müssen vorkommen
    assert titles(search="termin") == ["Gebäudeaufnahme"]                   # inaktive nur auf Wunsch
    assert "Inaktiv" in titles(search="termin", include_inactive=True)
    assert titles(category="Beratung") == ["Gebäudeaufnahme", "Übergabe"]
    assert "Fahrtkosten" in titles(category="")                              # ohne Kategorie (Startartikel)
    assert titles(category="Beratung", search="vor-ort") == ["Gebäudeaufnahme"]
    assert set(repo.article_categories(con)) >= {"Berechnung", "Beratung"}


# ------------------------------------------------------------------ Import
def test_mapping_guess():
    from app.importers import article_import as ai
    assert ai.guess_mapping(["Bezeichnung", "Langtext", "ME", "Preis netto", "MwSt %", "Warengruppe", "xyz"]) == \
        ["title", "description", "unit", "unit_price_cents", "tax_rate", "category", ""]
    assert ai.guess_mapping(["Artikel", "Name"]) == ["title", ""]               # nicht doppelt zuordnen


def test_rows_to_articles_prices_and_errors():
    from app.importers import article_import as ai
    mapping = ["title", "unit_price_cents", "tax_rate", "category", "unit"]
    rows = ai.rows_to_articles([
        ["Beratung", "1.234,50", "19 %", " Beratung ", "Stunden"],
        ["Material", "12,5", "7", "", "Stück"],
        ["Fahrt", "0,30 €", "", "", "km"],
        ["Pauschale", "1.500", "0", "", ""],
        ["Kaputt", "zwölf", "19", "", ""],
        ["Steuer falsch", "1", "150", "", ""],
        ["", "5", "", "", ""],
        ["", "", "", "", ""],
    ], mapping, default_tax="19")
    by = {r.data.get("title"): r for r in rows}
    assert by["Beratung"].data["unit_price_cents"] == 123450 and by["Beratung"].data["tax_rate"] == "19"
    assert by["Beratung"].data["category"] == "Beratung"
    assert by["Material"].data["unit_price_cents"] == 1250 and by["Material"].data["tax_rate"] == "7"
    assert by["Fahrt"].data["unit_price_cents"] == 30 and by["Fahrt"].data["tax_rate"] == "19"
    assert by["Pauschale"].data["unit_price_cents"] == 150000 and by["Pauschale"].data["unit"] == "Stunden"
    assert by["Kaputt"].status == ai.INVALID and "Preis" in by["Kaputt"].message
    assert by["Steuer falsch"].status == ai.INVALID
    assert [r.line for r in rows if r.status == ai.INVALID and "Bezeichnung" in r.message] == [8]
    assert len(rows) == 7                                                      # leere Zeile entfällt


def test_import_csv_with_duplicates_skip_and_update(con, tmp_path):
    from app.db import repo
    from app.importers import article_import as ai
    _article(con, "Beratung", description="alt", unit_price_cents=8000, category="Alt", active=0)
    p = tmp_path / "artikel.csv"
    p.write_text("Bezeichnung;Beschreibung;Einheit;Preis netto;USt %;Kategorie\n"
                 "beratung ;neu;Stunden;1.234,50;19;Beratung\n"
                 "Messung;Blower-Door;pauschal;350;19;Messung\n"
                 "Messung;doppelt;pauschal;1;19;Messung\n", encoding="cp1252")
    rows = ai.read_articles(con, p)
    assert [r.status for r in rows] == [ai.EXISTS, ai.NEW, ai.DUPLICATE]
    assert rows[0].existing["title"] == "Beratung"

    assert ai.apply_import(con, rows, update_existing=False) == (1, 0, 2)
    old = [a for a in repo.list_articles(con, True) if a["title"] == "Beratung"][0]
    assert old["unit_price_cents"] == 8000 and old["description"] == "alt"
    assert [a["title"] for a in repo.list_articles(con, True)].count("Messung") == 1

    rows = ai.read_articles(con, p)                                           # zweiter Lauf: Messung ist jetzt da
    assert [r.status for r in rows] == [ai.EXISTS, ai.EXISTS, ai.DUPLICATE]
    assert ai.apply_import(con, rows, update_existing=True) == (0, 2, 1)
    upd = [a for a in repo.list_articles(con, True) if a["title"] == "Beratung"][0]
    assert (upd["unit_price_cents"], upd["description"], upd["category"], upd["active"]) == (123450, "neu", "Beratung", 0)
    assert upd["id"] == old["id"] and upd["title"] == "Beratung"             # Bezeichnung des Bestands bleibt


def test_import_is_all_or_nothing(con, monkeypatch):
    from app.db import repo
    from app.importers import article_import as ai
    before = len(repo.list_articles(con, True))
    rows = ai.plan_import(con, ai.rows_to_articles([["A", "1"], ["B", "2"]], ["title", "unit_price_cents"]))
    calls = []

    def save(c, data):
        calls.append(data["title"])
        if len(calls) == 2:
            raise RuntimeError("Platte voll")
        return orig(c, data)
    orig = repo.save_article
    monkeypatch.setattr(repo, "save_article", save)
    with pytest.raises(RuntimeError):
        ai.apply_import(con, rows, False)
    monkeypatch.undo()
    assert len(repo.list_articles(con, True)) == before


def test_template_and_export_roundtrip(con, tmp_path):
    from app.db import repo
    from app.importers import article_import as ai
    from app.importers.csv_import import read_table
    headers, rows = read_table(ai.write_template(tmp_path / "vorlage.xlsx"))
    assert headers == list(ai.HEADERS.values())
    assert ai.guess_mapping(headers) == list(ai.HEADERS)
    assert len(rows) == 1

    _article(con, "Präzise", unit_price_cents=1235, tax_rate="7", category="Test", description="=1+1")
    _article(con, "Teuer", unit_price_cents=123450, tax_rate="19")
    for name in ("artikel.xlsx", "artikel.csv"):
        path = ai.export_articles(con, tmp_path / name)
        imported = ai.read_articles(con, path)
        assert {r.status for r in imported} == {ai.EXISTS}, name               # alles schon vorhanden
        by = {r.data["title"]: r.data for r in imported}
        assert by["Präzise"]["unit_price_cents"] == 1235 and by["Präzise"]["tax_rate"] == "7"
        assert by["Präzise"]["category"] == "Test" and by["Präzise"]["description"] == "=1+1"
        assert by["Teuer"]["unit_price_cents"] == 123450
        assert len(imported) == len(repo.list_articles(con, True))
    from openpyxl import load_workbook
    ws = load_workbook(tmp_path / "artikel.xlsx").active
    assert all(c.data_type == "s" for c in ws["B"][1:] if c.value == "=1+1")   # nie als Formel


def test_xlsx_decimal_prices_not_read_as_thousands(con, tmp_path):
    """Preis 12,345 € in einer Excel-Zelle (Zahl) darf nicht als 12.345 € gelesen werden."""
    from openpyxl import Workbook
    from app.importers import article_import as ai
    wb = Workbook()
    wb.active.append(["Bezeichnung", "Preis"])
    wb.active.append(["Kleinteil", 12.345])
    wb.active.append(["Großteil", 1500])
    wb.save(tmp_path / "p.xlsx")
    by = {r.data["title"]: r.data["unit_price_cents"] for r in ai.read_articles(con, tmp_path / "p.xlsx")}
    assert by == {"Kleinteil": 1235, "Großteil": 150000}


# ------------------------------------------------------------------ Oberfläche
def test_article_tab_search_filter_import(gui):
    app, ctx, log, mp, tmp = gui
    from test_gui_pages import _file_dialog
    from app.db import repo
    from app.ui import article_dialogs
    from app.ui.import_dialogs import CsvMappingDialog
    from app.ui.texts_page import ArticleTab
    at = ArticleTab(ctx)
    at.new()
    at.title.setText("Thermografie")
    at.price.setText("250")
    at.category.setCurrentText("Messung")
    assert at.save()
    assert at.cat_filter.findData("Messung") >= 0 and at.category.findText("Messung") >= 0
    at.search.setText("thermo")
    assert at.table.rowCount() == 1
    at.search.clear()
    at.cat_filter.setCurrentIndex(at.cat_filter.findData("Messung"))
    assert at.table.rowCount() == 1
    at.new()
    assert at.category.currentText() == "Messung"                          # gefilterte Kategorie vorgeschlagen
    at.cat_filter.setCurrentIndex(0)

    # Vorlage und Export speichern
    _file_dialog(mp, save_names=[tmp / "vorlage", tmp / "export.xlsx"])
    assert article_dialogs.save_article_template(at) == tmp / "vorlage.xlsx"
    assert article_dialogs.export_articles(at, ctx) == tmp / "export.xlsx"

    # Import mit Vorschau: vorhandene aktualisieren
    csvp = tmp / "a.csv"
    csvp.write_text("Bezeichnung;Preis;Kategorie\nThermografie;300;Messung\nLuftdichtheit;400;Messung\n",
                    encoding="utf-8")
    _file_dialog(mp, open_name=csvp)
    mp.setattr(CsvMappingDialog, "exec", lambda self: 1)
    seen = {}

    def preview_exec(self):
        seen["rows"] = self.table.rowCount()
        seen["hint"] = self.table.item(0, 7).text()
        self.update_rb.setChecked(True)
        return 1
    mp.setattr(article_dialogs.ArticleImportDialog, "exec", preview_exec)
    at.import_file()
    assert seen == {"rows": 2, "hint": "Preis 250,00 € → 300,00 €"}
    prices = {a["title"]: a["unit_price_cents"] for a in repo.list_articles(ctx.con)}
    assert prices["Thermografie"] == 30000 and prices["Luftdichtheit"] == 40000
    assert ("information", "Neu angelegt: 1\nAktualisiert: 1\nNicht übernommen: 0") in log

    # ohne Bezeichnungsspalte: Hinweis statt Import
    mp.setattr(CsvMappingDialog, "mapping", lambda self: ["", "unit_price_cents", ""])
    assert not article_dialogs.run_article_import(at, ctx)


def test_article_picker_in_editor(gui):
    app, ctx, log, mp, tmp = gui
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from test_core import _customer
    from app.ui import article_dialogs, editor
    from app.ui.editor import DocumentEditor
    _article(ctx.con, "Heizlastberechnung", description="nach DIN EN 12831", unit_price_cents=12000,
             category="Berechnung")
    _article(ctx.con, "Hydraulischer Abgleich", unit_price_cents=9000, category="Berechnung")

    dlg = article_dialogs.ArticlePickerDialog(None, ctx.con)
    total = dlg.table.rowCount()
    dlg.search.setText("din en")
    assert dlg.table.rowCount() == 1 and dlg.selected()["title"] == "Heizlastberechnung"
    dlg.search.setText("gibt es nicht")
    assert dlg.table.rowCount() == 0 and not dlg.ok.isEnabled()
    dlg.accept()
    assert dlg.result() == 0                                                # leere Liste übernimmt nichts
    dlg.search.clear()
    dlg.category.setCurrentIndex(dlg.category.findData("Berechnung"))
    assert dlg.table.rowCount() == 2 < total
    app.sendEvent(dlg.search, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    app.sendEvent(dlg.search, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert dlg.result() == 1 and dlg.selected() is not None

    # im Editor: Auswahl übernimmt Preis, Einheit und Steuersatz
    cid = _customer(ctx.con)
    ed = DocumentEditor(None, ctx, doc_type="rechnung", customer_id=cid)

    def pick(self):
        self.search.setText("heizlast")
        return 1
    mp.setattr(editor.ArticlePickerDialog, "exec", pick)
    rows = ed.table.rowCount()
    ed.add_article()
    assert ed.table.rowCount() == rows + 1
    line = ed._row_dict(rows)
    assert line["title"] == "Heizlastberechnung" and line["unit_price_cents"] == 12000
    mp.setattr(editor.ArticlePickerDialog, "exec", lambda self: 0)           # abgebrochen
    ed.add_article()
    assert ed.table.rowCount() == rows + 1


def test_update_keeps_fields_not_in_file(con):
    """Preisliste nur mit Bezeichnung und Preis: Beschreibung, Einheit, USt und Kategorie des Bestands bleiben."""
    from app.db import repo
    from app.importers import article_import as ai
    aid = _article(con, "Messung", description="Blower-Door", unit="pauschal", tax_rate="7", category="Messung")
    rows = ai.plan_import(con, ai.rows_to_articles([["Messung", "400", ""]], ["title", "unit_price_cents", "tax_rate"]))
    assert rows[0].update_fields == {"unit_price_cents"}                    # leere USt-Zelle zählt nicht
    assert ai.apply_import(con, rows, update_existing=True) == (0, 1, 0)
    a = [a for a in repo.list_articles(con) if a["id"] == aid][0]
    assert (a["unit_price_cents"], a["description"], a["unit"], a["tax_rate"], a["category"]) == \
        (40000, "Blower-Door", "pauschal", "7", "Messung")
    # zugeordnete, leere Beschreibung leert den Bestand (Export bearbeitet, Text gelöscht)
    rows = ai.plan_import(con, ai.rows_to_articles([["Messung", ""]], ["title", "description"]))
    ai.apply_import(con, rows, update_existing=True)
    assert [a["description"] for a in repo.list_articles(con) if a["id"] == aid] == [""]


def test_percent_formatted_tax_cell(con, tmp_path):
    from openpyxl import Workbook
    from app.importers import article_import as ai
    wb = Workbook()
    wb.active.append(["Bezeichnung", "USt %"])
    wb.active.append(["Prozentzelle", 0.19])
    wb.active.append(["Ermäßigt", 0.07])
    wb.active.append(["Null", 0])
    wb.save(tmp_path / "t.xlsx")
    assert {r.data["title"]: r.data["tax_rate"] for r in ai.read_articles(con, tmp_path / "t.xlsx")} == \
        {"Prozentzelle": "19", "Ermäßigt": "7", "Null": "0"}


def test_categories_case_insensitive_once(con):
    from app.db import repo
    _article(con, "A", category="Beratung")
    _article(con, "B", category="beratung")
    assert [c.casefold() for c in repo.article_categories(con)].count("beratung") == 1
