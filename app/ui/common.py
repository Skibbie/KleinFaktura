"""Gemeinsame GUI-Helfer."""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import date

from PySide6.QtCore import QDate, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDateEdit, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QWidget)

from app.ui.theme import ACTION as ACCENT, INK_2, SCALE, STYLE  # noqa: E402,F401

STATUS_COLORS = {
    "Entwurf": INK_2, "Offen": "#1D2B2F", "Teilbezahlt": "#A06A00", "Bezahlt": SCALE[0],
    "Storniert": "#8A9A9D", "Storno": "#8A9A9D", "Angenommen": SCALE[0], "Abgelaufen": "#8A9A9D",
    "Überfällig": SCALE[7],
}


def page_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("pageTitle")
    return lbl


def button(text: str, slot=None, primary: bool = False, danger: bool = False, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if slot:
        b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def hbox(*widgets, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    for i, w in enumerate(widgets):
        if i == stretch_at:
            lay.addStretch(1)
        if isinstance(w, QWidget):
            lay.addWidget(w)
        elif w is None:
            lay.addStretch(1)
        else:
            lay.addLayout(w)
    return lay


_NUM = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$|^-?\d+(,\d+)?$")
_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})")
_UNITS = ("€", "%", "km", "Tage")


def sort_key(text: str):
    """Sortierschlüssel für eine Tabellenzelle: Zahlen/Beträge numerisch, Datumsangaben chronologisch, sonst Text.
    Leere Zellen kommen ans Ende."""
    t = text.strip()
    if not t:
        return (3, "")
    m = _DATE.match(t)
    if m:
        return (1, m.group(3) + m.group(2) + m.group(1))
    n = t
    for u in _UNITS:
        n = n.removesuffix(u).strip()
    n = n.replace("\u00a0", "").replace(" ", "")
    if _NUM.match(n):
        return (0, float(n.replace(".", "").replace(",", ".")))
    return (2, t.casefold())


class SortItem(QTableWidgetItem):
    """Tabellenzelle mit sinnvoller Sortierung (siehe sort_key); Häkchen-Zellen sortieren nach ihrem Zustand."""

    def __lt__(self, other):
        if self.data(Qt.CheckStateRole) is not None:  # Häkchen-Zelle (das Flag allein ist Standard)
            return self.checkState().value < other.checkState().value
        return sort_key(self.text()) < sort_key(other.text())


class SortableTable(QTableWidget):
    """Sortierbar per Klick auf den Spaltenkopf. Beim Befüllen (insertRow/setRowCount) wird die Sortierung bis zum
    nächsten Durchlauf der Ereignisschleife ausgesetzt, damit setItem(r, …) die richtige Zeile trifft; danach wird
    nach der zuletzt gewählten Spalte neu sortiert."""

    def __init__(self, rows: int, cols: int):
        super().__init__(rows, cols)
        self._resort_pending = False
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)  # zunächst Reihenfolge der Abfrage
        self.setSortingEnabled(True)

    def _suspend_sorting(self):
        if self.isSortingEnabled():
            self.setSortingEnabled(False)
            self._resort_pending = True
            QTimer.singleShot(0, self, self._resume_sorting)  # entfällt, falls die Tabelle vorher zerstört wird

    def _resume_sorting(self):
        if self._resort_pending:
            self._resort_pending = False
            self.setSortingEnabled(True)

    def insertRow(self, row: int):
        self._suspend_sorting()
        super().insertRow(row)

    def setRowCount(self, rows: int):
        self._suspend_sorting()
        super().setRowCount(rows)


def make_table(headers: list[str], stretch_col: int | None = None) -> QTableWidget:
    t = SortableTable(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(False)
    t.setShowGrid(False)
    h = t.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeToContents)
    if stretch_col is not None:
        h.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    return t


def check_item(checked: bool = False) -> QTableWidgetItem:
    it = SortItem()
    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    return it


def item(text, data=None, align_right: bool = False, color: str | None = None) -> QTableWidgetItem:
    it = SortItem("" if text is None else str(text))
    if data is not None:
        it.setData(Qt.UserRole, data)
    if align_right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        it.setForeground(QColor(color))
    return it


class _SearchComboClick(QObject):
    """Klick ins Textfeld einer durchsuchbaren Auswahl: Platzhalter („kein Kunde“) leeren bzw. die gewählte Angabe
    zum Überschreiben markieren und die ganze Liste aufklappen. Tippen filtert die Liste weiter."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            cb: QComboBox = self.parent()
            le = cb.lineEdit()
            if not cb.isEnabled() or le.isReadOnly():
                return False
            if le.text() == cb.itemText(cb.currentIndex()):
                if cb.currentData() is None:
                    le.clear()
                else:
                    le.selectAll()
            if le.text() == "" or le.hasSelectedText():
                comp = cb.completer()
                comp.setCompletionPrefix("")
                comp.complete()
        return False


def searchable_combo(cb: QComboBox) -> QComboBox:
    """Bearbeitbare Auswahl (Eintrag 0 = „keine Auswahl“): Klick leert/markiert und klappt auf, leeres Feld beim
    Verlassen setzt auf Eintrag 0 zurück."""
    cb.completer().setCompletionMode(QCompleter.PopupCompletion)
    le = cb.lineEdit()
    le.installEventFilter(_SearchComboClick(cb))

    def finished():
        text = le.text().strip()
        if text == cb.itemText(cb.currentIndex()):
            return
        # Eingetippter Text, ohne einen Eintrag zu wählen: eindeutigen Treffer übernehmen, sonst „keine Auswahl“ –
        # nie stillschweigend den vorher gewählten Eintrag behalten, während das Feld etwas anderes zeigt
        hits = [i for i in range(1, cb.count()) if text and text.lower() in cb.itemText(i).lower()]
        exact = [i for i in hits if cb.itemText(i).lower() == text.lower()]
        pick = exact[0] if exact else hits[0] if len(hits) == 1 else 0
        cb.setCurrentIndex(pick)
        cb.setEditText(cb.itemText(pick))
    # verzögert: erst übernimmt Qt eine Auswahl aus der Vorschlagsliste, dann wird geprüft
    le.editingFinished.connect(lambda: QTimer.singleShot(0, finished))
    return cb


def selected_data(table: QTableWidget, col: int = 0):
    rows = table.selectionModel().selectedRows()
    if not rows:
        return None
    it = table.item(rows[0].row(), col)
    return it.data(Qt.UserRole) if it else None


def date_edit(iso: str | None = None, allow_empty: bool = False) -> QDateEdit:
    de = QDateEdit()
    de.setCalendarPopup(True)
    de.setDisplayFormat("dd.MM.yyyy")
    if allow_empty:
        de.setSpecialValueText(" ")
        de.setMinimumDate(QDate(1999, 12, 31))
    if iso:
        de.setDate(QDate.fromString(iso[:10], "yyyy-MM-dd"))
    elif allow_empty:
        de.setDate(de.minimumDate())
    else:
        de.setDate(QDate.currentDate())
    return de


def date_value(de: QDateEdit) -> str | None:
    if de.specialValueText() and de.date() == de.minimumDate():
        return None
    return de.date().toString("yyyy-MM-dd")


def set_date(de: QDateEdit, iso: str | None) -> None:
    if iso:
        de.setDate(QDate.fromString(iso[:10], "yyyy-MM-dd"))
    elif de.specialValueText():
        de.setDate(de.minimumDate())


def open_file(path) -> None:
    from app import config
    path = config.resolve_stored_path(path) or path
    if path and not os.path.isdir(path) and config.is_risky_file(path):
        # nie ein Programm/Skript starten, auch wenn ein (manipulierter) Belegpfad darauf zeigt
        QMessageBox.warning(None, "Datei nicht geöffnet", f"Diese Datei wird aus Sicherheitsgründen nicht geöffnet, "
                                                          f"weil Windows sie ausführen würde:\n{path}")
        return
    if path and os.path.exists(path):
        try:
            os.startfile(str(path))  # noqa: S606 (Windows)
        except OSError as e:
            QMessageBox.warning(None, "Datei öffnen", f"Die Datei konnte nicht geöffnet werden – ist ein passendes "
                                                      f"Programm (z.B. ein PDF-Betrachter) installiert?\n{path}\n\n{e}")
    else:
        QMessageBox.warning(None, "Datei fehlt", f"Die Datei wurde nicht gefunden:\n{path}")


def count(n: int, one: str, many: str) -> str:
    """Anzahl mit passender Einzahl/Mehrzahl: „1 Ausgabe“, „3 Ausgaben“."""
    return f"{n} {one if n == 1 else many}"


def error(parent, title: str, exc_or_msg) -> None:
    if isinstance(exc_or_msg, PermissionError):
        # typisch: Exportdatei ist noch in Excel geöffnet, oder ein Ordner ist schreibgeschützt
        name = getattr(exc_or_msg, "filename", None) or ""
        exc_or_msg = (f"Auf die Datei kann nicht geschrieben werden – ist sie noch in einem anderen Programm "
                      f"(z.B. Excel oder einem PDF-Betrachter) geöffnet? Bitte schließen oder einen anderen "
                      f"Dateinamen wählen.\n\n{name}")
    QMessageBox.critical(parent, title, str(exc_or_msg))


def confirm(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


class Ctx:
    """Anwendungskontext (DB-Verbindung + Signale zum Neuladen)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.listeners: list = []

    def changed(self):
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass


def today_iso() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- ungespeicherte Änderungen
SAVE, DISCARD, CANCEL = "save", "discard", "cancel"


def ask_save(parent, text: str = "Es gibt ungespeicherte Änderungen.") -> str:
    """Fragt Speichern / Verwerfen / Abbrechen. Rückgabe SAVE, DISCARD oder CANCEL."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Ungespeicherte Änderungen")
    box.setText(text)
    box.setInformativeText("Möchten Sie die Änderungen speichern?")
    b_save = box.addButton("Speichern", QMessageBox.AcceptRole)
    b_discard = box.addButton("Verwerfen", QMessageBox.DestructiveRole)
    box.addButton("Abbrechen", QMessageBox.RejectRole)
    box.setDefaultButton(b_save)
    box.exec()
    clicked = box.clickedButton()
    if clicked is b_save:
        return SAVE
    if clicked is b_discard:
        return DISCARD
    return CANCEL


class GuardedDialog:
    """Mixin für Dialoge: merkt sich den gespeicherten Stand und fragt beim Schließen nach.

    Unterklassen implementieren _snapshot() (aktueller Formularstand) und save() (ruft bei Erfolg accept()).
    """

    _clean = None

    def mark_clean(self):
        self._clean = self._snapshot()

    def is_dirty(self) -> bool:
        return self._clean is not None and self._snapshot() != self._clean

    def close_dialog(self):
        if self.is_dirty():
            r = ask_save(self, getattr(self, "dirty_text", "Es gibt ungespeicherte Änderungen."))
            if r == CANCEL:
                return
            if r == SAVE:
                self.save()  # schließt bei Erfolg selbst; bei Fehlern bleibt der Dialog offen
                return
        self.done(0)

    def reject(self):
        self.close_dialog()
