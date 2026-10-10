"""Erscheinungsbild „Effizienzskala“.

Farbsprache aus der Effizienzklassen-Skala des Energieausweises (A+ … H), Schrift Bahnschrift (DIN-Charakter)
für Titel und Beträge, Segoe UI für Texte. Die Palette wird fest gesetzt, damit die Oberfläche unabhängig
vom hellen/dunklen Windows-Modus lesbar bleibt.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (QApplication, QColorDialog, QDialog, QFileDialog, QFontDialog, QInputDialog,
                               QMessageBox, QProgressDialog)

from app import config

_ASSET = config.ASSETS_DIR.as_posix()

GROUND = "#EEF2F1"      # Hintergrund
SURFACE = "#FFFFFF"     # Flächen, Eingabefelder
SUNK = "#F6F8F7"        # leicht abgesetzte Flächen
INK = "#1D2B2F"         # Text
INK_2 = "#5A6A6E"       # Sekundärtext
LINE = "#CFD9D6"        # Linien
LINE_SOFT = "#E3E9E7"
ACTION = "#17604A"      # Klasse A+ – Primäraktion
ACTION_HOVER = "#0F4D3B"
ACTION_TINT = "#DCEBE5"
DANGER = "#B3261E"

# Effizienzklassen A+ … H
SCALE = ["#17604A", "#3E8E3E", "#8DBB35", "#D4D531", "#F2C230", "#F09A2E", "#E0662A", "#C9392A", "#9E1F24"]
SCALE_LABELS = ["A+", "A", "B", "C", "D", "E", "F", "G", "H"]

DISPLAY_FONT = "Bahnschrift"
TEXT_FONT = "Segoe UI"


def scale_color(ratio: float) -> str:
    """0 … 1 (und darüber) auf die Effizienzskala abbilden."""
    idx = int(max(0.0, min(ratio, 0.999)) * len(SCALE))
    return SCALE[idx] if ratio < 1 else SCALE[-1]


class _MaximizeDialogs(QObject):
    """Öffnet alle eigenen Dialoge maximiert (Meldungs-, Datei- und Eingabedialoge bleiben klein)."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QDialog) and obj.isWindow() and not isinstance(
                obj, (QMessageBox, QFileDialog, QInputDialog, QColorDialog, QFontDialog, QProgressDialog)):
            if not obj.property("_faktura_max"):
                obj.setProperty("_faktura_max", True)
                obj.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
                obj.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
                obj.setWindowState(obj.windowState() | Qt.WindowMaximized)
                obj.show()
        return False


_max_filter = None


_translator = None


def _load_german(app: QApplication) -> None:
    """Deutsche Beschriftung der Qt-Standardknöpfe (Speichern, Abbrechen, Ja, Nein …)."""
    global _translator
    from PySide6.QtCore import QLibraryInfo, QTranslator
    tr = QTranslator(app)
    if tr.load("qtbase_de", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
        _translator = tr


def apply(app: QApplication) -> None:
    global _max_filter
    if _translator is None:
        _load_german(app)
    if _max_filter is None:
        _max_filter = _MaximizeDialogs(app)
        app.installEventFilter(_max_filter)
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: GROUND, QPalette.WindowText: INK, QPalette.Base: SURFACE, QPalette.AlternateBase: SUNK,
        QPalette.Text: INK, QPalette.Button: SURFACE, QPalette.ButtonText: INK, QPalette.BrightText: "#FFFFFF",
        QPalette.Highlight: ACTION_TINT, QPalette.HighlightedText: INK, QPalette.ToolTipBase: INK,
        QPalette.ToolTipText: "#FFFFFF", QPalette.PlaceholderText: "#8A9A9D", QPalette.Link: ACTION,
        QPalette.Light: "#FFFFFF", QPalette.Midlight: LINE_SOFT, QPalette.Mid: LINE, QPalette.Dark: "#9AAAA7",
        QPalette.Shadow: "#7D8C89",
    }
    for role, color in roles.items():
        pal.setColor(QPalette.All, role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#9AA7A9"))
    pal.setColor(QPalette.Disabled, QPalette.Base, QColor(SUNK))
    app.setPalette(pal)
    f = QFont(TEXT_FONT, 10)
    app.setFont(f)
    app.setStyleSheet(STYLE)


STYLE = f"""
* {{ color: {INK}; }}
QWidget {{ font-family: '{TEXT_FONT}'; font-size: 10pt; }}
QMainWindow, QDialog, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {GROUND}; }}

/* Navigation */
QListWidget#nav {{
    background: {SURFACE}; border: none; border-right: 1px solid {LINE};
    font-family: '{DISPLAY_FONT}'; font-size: 11.5pt; padding-top: 6px; outline: 0;
}}
QListWidget#nav::item {{ color: {INK_2}; padding: 9px 16px 9px 18px; border-left: 4px solid transparent; }}
QListWidget#nav::item:hover {{ color: {INK}; background: {SUNK}; }}
QListWidget#nav::item:selected {{ color: {ACTION}; background: {ACTION_TINT}; border-left: 4px solid {ACTION}; }}
QLabel#brand {{ background: {SURFACE}; padding: 14px 12px 10px 12px; border-right: 1px solid {LINE}; }}
QLabel#brandName {{ background: {SURFACE}; color: {INK}; font-size: 17pt; font-weight: 600;
    padding: 18px 18px 4px 18px; border-right: 1px solid {LINE}; }}
QLabel#brandSub {{ background: {SURFACE}; color: {INK_2}; font-size: 8.5pt; padding: 0 18px 12px 18px;
    border-right: 1px solid {LINE}; border-bottom: 1px solid {LINE_SOFT}; }}
QWidget#navCol {{ background: {SURFACE}; }}

/* Titel */
QLabel#pageTitle {{ font-family: '{DISPLAY_FONT}'; font-size: 21pt; font-weight: 600; color: {INK}; padding-bottom: 2px; }}
QLabel#sectionTitle {{ font-family: '{DISPLAY_FONT}'; font-size: 12.5pt; font-weight: 600; color: {INK}; }}
QLabel#kpi {{ font-family: '{DISPLAY_FONT}'; font-size: 22pt; font-weight: 600; color: {INK}; }}
QLabel#kpiLabel {{ color: {INK_2}; font-size: 9.5pt; }}
QLabel#muted {{ color: {INK_2}; }}

/* Knöpfe */
QPushButton {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE}; border-radius: 4px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {ACTION}; color: {ACTION}; }}
QPushButton:pressed {{ background: {ACTION_TINT}; }}
QPushButton:focus {{ border: 1px solid {ACTION}; }}
QPushButton:disabled {{ color: #9AA7A9; background: {SUNK}; border-color: {LINE_SOFT}; }}
QPushButton[primary="true"] {{ background: {ACTION}; color: #FFFFFF; border: 1px solid {ACTION}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACTION_HOVER}; border-color: {ACTION_HOVER}; color: #FFFFFF; }}
QPushButton[danger="true"] {{ color: {DANGER}; }}
QPushButton[danger="true"]:hover {{ border-color: {DANGER}; color: {DANGER}; }}

/* Eingaben */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE}; border-radius: 4px; padding: 4px 6px;
    selection-background-color: {ACTION_TINT}; selection-color: {INK};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
    border: 1px solid {ACTION};
}}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QPlainTextEdit[readOnly="true"] {{
    background: {SUNK}; color: {INK_2};
}}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 24px; subcontrol-position: center right; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({_ASSET}/chevron-down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE};
    selection-background-color: {ACTION_TINT}; selection-color: {INK}; outline: 0;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_ASSET}/chevron-up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_ASSET}/chevron-down.svg); width: 10px; height: 10px; }}
QTableView::indicator, QListView::indicator {{ width: 15px; height: 15px; border: 1px solid #93A3A0; border-radius: 3px;
    background: {SURFACE}; }}
QTableView::indicator:checked, QListView::indicator:checked {{ background: {ACTION}; border-color: {ACTION};
    image: url({_ASSET}/check.svg); }}
QCheckBox, QRadioButton {{ color: {INK}; spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
QCheckBox::indicator {{ border: 1px solid #93A3A0; border-radius: 3px; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACTION}; border-color: {ACTION}; image: url({_ASSET}/check.svg); }}
QRadioButton::indicator {{ border: 1px solid #93A3A0; border-radius: 8px; background: {SURFACE}; }}
QRadioButton::indicator:checked {{ background: {SURFACE}; border: 5px solid {ACTION}; }}

/* Tabellen */
QTableWidget, QTableView, QListWidget {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE}; border-radius: 4px;
    gridline-color: {LINE_SOFT}; selection-background-color: {ACTION_TINT}; selection-color: {INK};
    alternate-background-color: {SUNK}; outline: 0;
}}
QTableWidget::item {{ padding: 4px 6px; border-bottom: 1px solid {LINE_SOFT}; }}
QTableWidget::item:selected {{ background: {ACTION_TINT}; color: {INK}; }}
QHeaderView {{ background: {SURFACE}; }}
QHeaderView::section {{
    background: {SURFACE}; color: {INK_2}; padding: 7px 6px; border: none; border-bottom: 2px solid {LINE};
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

/* Gruppen, Tabs, Karten */
QGroupBox {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px; margin-top: 16px; padding: 12px 10px 10px 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px;
    color: {INK}; font-family: '{DISPLAY_FONT}'; font-size: 10.5pt; font-weight: 600;
}}
QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px; }}
QFrame#card QLabel {{ background: transparent; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; background: transparent; padding-top: 10px; }}
QTabWidget > QWidget > QWidget {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {INK_2}; padding: 8px 16px; border: none; border-bottom: 3px solid transparent;
    font-family: '{DISPLAY_FONT}'; font-size: 10.5pt; }}
QTabBar::tab:selected {{ color: {ACTION}; border-bottom: 3px solid {ACTION}; }}
QTabBar::tab:hover {{ color: {INK}; }}

/* Hinweise */
QLabel#warn {{ background: #FCF3D9; color: #5C4300; border: 1px solid #EBCB72; border-radius: 4px; padding: 7px 9px; }}
QLabel#error {{ background: #FBE4E2; color: #6E1712; border: 1px solid #E3A19B; border-radius: 4px; padding: 7px 9px; }}
QLabel#ok {{ background: #E2F0E9; color: #0F4D3B; border: 1px solid #A8CDBC; border-radius: 4px; padding: 7px 9px; }}

/* Diverses */
QMenu {{ background: {SURFACE}; color: {INK}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; }}
QMenu::item:selected {{ background: {ACTION_TINT}; color: {INK}; }}
QToolTip {{ background: {INK}; color: #FFFFFF; border: none; padding: 5px 8px; }}
QMessageBox, QInputDialog, QFileDialog {{ background: {GROUND}; }}
QMessageBox QLabel {{ color: {INK}; }}
QProgressBar {{ background: {SUNK}; border: 1px solid {LINE}; border-radius: 3px; text-align: center; color: {INK}; height: 14px; }}
QProgressBar::chunk {{ background: {ACTION}; border-radius: 2px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #BCC8C5; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #93A3A0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #BCC8C5; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {GROUND}; }}
QCalendarWidget {{ min-width: 300px; min-height: 240px; }}
QCalendarWidget QWidget {{ background: {SURFACE}; color: {INK}; }}
QCalendarWidget QTableView {{ border: none; font-size: 10pt; }}
QCalendarWidget QTableView::item {{ padding: 0; border: none; }}
QCalendarWidget QToolButton {{ color: {INK}; background: {SURFACE}; border: none; padding: 4px 8px; font-weight: 600; }}
QCalendarWidget QAbstractItemView:enabled {{ color: {INK}; background: {SURFACE}; selection-background-color: {ACTION};
    selection-color: #FFFFFF; }}
QCalendarWidget QAbstractItemView:disabled {{ color: #AAB5B3; }}
QCalendarWidget #qt_calendar_navigationbar {{ background: {SURFACE}; border-bottom: 1px solid {LINE}; }}
"""
