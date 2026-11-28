"""Platzhalter komfortabel einfügen: Vorschlagliste beim Tippen von „{“ und eine Übersicht zum Nachschlagen.

Bewusst ohne QCompleter: der reicht Enter zusätzlich an das Textfeld weiter (Zeilenumbruch beim Auswählen) und
ließe sich nur mit abgeleiteten Textfeldern bändigen. Die eigene Liste nimmt keinen Fokus; Pfeiltasten, Enter,
Tab und Escape fängt ein Ereignisfilter am Textfeld ab, alles andere tippt normal weiter.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from app.services.placeholder_catalog import CONTEXT_LABEL, Placeholder
from app.ui.common import button

ItemsFn = Callable[[], list[Placeholder]]
ValuesFn = Callable[[], dict] | None
_OPEN = re.compile(r"\{(\w*)$")
_MAX_VALUE = 40


def _short(value) -> str:
    v = " ".join(str(value).split())
    return v if len(v) <= _MAX_VALUE else v[:_MAX_VALUE - 1] + "…"


def _safe_values(values_fn: ValuesFn) -> dict:
    if values_fn is None:
        return {}
    try:
        return values_fn() or {}
    except Exception:  # noqa: BLE001 – eine fehlende Vorschau darf das Tippen nie stören
        return {}


def insert_at_cursor(edit: QWidget, text: str) -> None:
    if isinstance(edit, QPlainTextEdit):
        edit.insertPlainText(text)
    else:
        edit.insert(text)
    edit.setFocus()


class PlaceholderCompleter(QObject):
    """Vorschlagliste für ein QPlainTextEdit oder QLineEdit."""

    def __init__(self, edit: QWidget, items_fn: ItemsFn, values_fn: ValuesFn = None,
                 on_focus: Callable[[QWidget], None] | None = None):
        super().__init__(edit)
        self.edit, self.items_fn, self.values_fn, self.on_focus = edit, items_fn, values_fn, on_focus
        self.prefix = ""
        self.popup = QListWidget()
        self.popup.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.popup.setAttribute(Qt.WA_ShowWithoutActivating)
        self.popup.setFocusPolicy(Qt.NoFocus)
        self.popup.setSelectionMode(QAbstractItemView.SingleSelection)
        self.popup.itemClicked.connect(self._accept_item)
        edit.destroyed.connect(self.popup.deleteLater)
        edit.installEventFilter(self)
        if isinstance(edit, QPlainTextEdit):
            edit.textChanged.connect(self.update_popup)
            edit.cursorPositionChanged.connect(self._cursor_moved)
        else:
            edit.textEdited.connect(self.update_popup)
            edit.cursorPositionChanged.connect(self._cursor_moved)

    # ------------------------------------------------------------ Text am Cursor
    def _before_cursor(self) -> str:
        if isinstance(self.edit, QPlainTextEdit):
            c = self.edit.textCursor()
            return c.block().text()[:c.positionInBlock()]
        return self.edit.text()[:self.edit.cursorPosition()]

    def _anchor(self) -> QPoint:
        if isinstance(self.edit, QPlainTextEdit):
            return self.edit.viewport().mapToGlobal(self.edit.cursorRect().bottomLeft() + QPoint(0, 4))
        return self.edit.mapToGlobal(self.edit.rect().bottomLeft())

    # ------------------------------------------------------------ Liste
    def update_popup(self, *_):
        m = _OPEN.search(self._before_cursor()) if self.edit.hasFocus() else None
        items = self.items_fn() if m else []
        if not m or not items:
            self.hide()
            return
        self.prefix = m[1]
        needle = self.prefix.casefold()
        values = _safe_values(self.values_fn)
        # zuerst Namen, die so beginnen, dann solche, die den Text enthalten (z.B. „dat“ → datum, leistungsdatum)
        hits = sorted((p for p in items if needle in p.name.casefold()), key=lambda p: not p.name.startswith(needle))
        if not hits:
            self.hide()
            return
        self.popup.clear()
        for p in hits:
            text = f"{p.token}   –   {p.description}"
            if values.get(p.name) not in (None, ""):
                text += f"   ({_short(values[p.name])})"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, p.name)
            self.popup.addItem(it)
        self.popup.setCurrentRow(0)
        rows = min(len(hits), 10)
        self.popup.resize(max(420, self.popup.sizeHintForColumn(0) + 24),
                          rows * self.popup.sizeHintForRow(0) + 2 * self.popup.frameWidth() + 2)
        self.popup.move(self._anchor())
        self.popup.show()

    def _cursor_moved(self):
        if self.popup.isVisible():
            self.update_popup()

    def hide(self):
        self.popup.hide()

    def _hide_if_unfocused(self):
        if not self.edit.hasFocus():
            self.hide()

    def is_open(self) -> bool:
        return self.popup.isVisible()

    def _accept_item(self, item: QListWidgetItem | None):
        if item is None:
            return
        name = item.data(Qt.UserRole)
        token = "{" + name + "}"
        n = len(self.prefix) + 1          # „{“ + bereits getippter Teil wird ersetzt
        if isinstance(self.edit, QPlainTextEdit):
            c = self.edit.textCursor()
            c.movePosition(c.MoveOperation.Left, c.MoveMode.KeepAnchor, n)
            c.insertText(token)
            self.edit.setTextCursor(c)
        else:
            pos = self.edit.cursorPosition()
            self.edit.setSelection(pos - n, n)
            self.edit.insert(token)
        self.hide()
        self.edit.setFocus()

    def accept_current(self):
        self._accept_item(self.popup.currentItem())

    # ------------------------------------------------------------ Tasten
    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.FocusIn and self.on_focus:
            self.on_focus(self.edit)
        elif t == QEvent.FocusOut:
            # verzögert: ein Mausklick in die Liste soll noch ankommen (Kontextobjekt: entfällt mit dem Textfeld)
            QTimer.singleShot(150, self, self._hide_if_unfocused)
        elif t == QEvent.KeyPress and self.popup.isVisible():
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
                row, count = self.popup.currentRow(), self.popup.count()
                step = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: 5, Qt.Key_PageUp: -5}[key]
                self.popup.setCurrentRow(max(0, min(count - 1, row + step)))
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                self.accept_current()
                return True
            if key == Qt.Key_Escape:
                self.hide()
                return True
        return False


def attach(edit: QWidget, items_fn: ItemsFn, values_fn: ValuesFn = None,
           on_focus: Callable[[QWidget], None] | None = None) -> PlaceholderCompleter:
    """Vorschlagliste an ein Textfeld hängen (das Objekt lebt als Kind des Textfelds)."""
    c = PlaceholderCompleter(edit, items_fn, values_fn, on_focus)
    edit._placeholder_completer = c   # für Tests und zum Wiederfinden
    return c


class PlaceholderDialog(QDialog):
    """Übersicht aller Platzhalter; mit Ziel-Textfeld lässt sich der gewählte direkt einfügen."""

    def __init__(self, parent, items: list[Placeholder], target: QWidget | None = None, values: dict | None = None,
                 title: str = "Platzhalter"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        self.target, self.items = target, items
        values = values or {}
        lay = QVBoxLayout(self)
        intro = QLabel("Platzhalter in geschweiften Klammern werden beim Erstellen des Belegs, der Mahnung bzw. der "
                       "E-Mail durch den passenden Wert ersetzt. Tipp: im Textfeld „{“ tippen öffnet eine Auswahlliste.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen …")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        cols = ["Platzhalter", "Bedeutung", "Verfügbar in"] + (["Wert im aktuellen Beleg"] if values else [])
        self.table = QTableWidget(len(items), len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        for r, p in enumerate(items):
            cells = [p.token, p.description, CONTEXT_LABEL[p.context]]
            if values:
                cells.append(_short(values.get(p.name, "")))
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, p.name)
                self.table.setItem(r, c, it)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(lambda *_: self.insert())
        if items:
            self.table.selectRow(0)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        row.addStretch(1)
        if target is not None:
            row.addWidget(button("Einfügen", self.insert, primary=True))
        row.addWidget(button("Schließen", self.reject))
        lay.addLayout(row)

    def _filter(self, text: str):
        needle = text.strip().casefold()
        for r in range(self.table.rowCount()):
            hay = " ".join(self.table.item(r, c).text() for c in range(self.table.columnCount())).casefold()
            self.table.setRowHidden(r, bool(needle) and needle not in hay)

    def selected_token(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self.table.isRowHidden(rows[0].row()):
            return None
        return "{" + self.table.item(rows[0].row(), 0).data(Qt.UserRole) + "}"

    def insert(self):
        token = self.selected_token()
        if self.target is None or token is None:
            return
        insert_at_cursor(self.target, token)
        self.accept()
