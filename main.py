"""KleinFaktura – Angebote, Rechnungen, offene Posten und EÜR für Kleinunternehmer."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QLocale, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen  # noqa: E402

from app import config  # noqa: E402

try:  # nur in der Exe: Startbild, das der PyInstaller-Starter schon vor Python anzeigt
    import pyi_splash  # type: ignore[import-not-found]  # noqa: E402
except ImportError:
    pyi_splash = None

# Hauptfenster, Datenbank und PDF-Bausteine werden erst nach dem Startfenster geladen (main),
# damit der Doppelklick sofort sichtbar ist.

SPLASH_LINGER_MS = 300  # Startfenster erst schließen, wenn das Hauptfenster fertig eingeblendet ist


def _close_splash(splash: QSplashScreen | None = None) -> None:
    if splash is not None:
        splash.close()
    if pyi_splash is not None and pyi_splash.is_alive():
        pyi_splash.close()


def _excepthook(exc_type, exc, tb):
    for w in QApplication.topLevelWidgets():   # Startfenster würde die Meldung verdecken
        if isinstance(w, QSplashScreen):
            w.close()
    _close_splash()
    msg = "".join(traceback.format_exception(exc_type, exc, tb))
    log = config.data_dir() / "fehler.log"
    with open(log, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    QMessageBox.critical(None, "Unerwarteter Fehler", f"{exc}\n\nDetails in {log}")


def _set_taskbar_identity() -> None:
    """Eigene AppUserModelID, damit Windows das Programmsymbol statt des Python-Symbols in der Taskleiste zeigt."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{config.APP_NAME}.App")
        except Exception:  # noqa: BLE001
            pass


def splash_pixmap() -> QPixmap:
    """Startbild mit Programmsymbol und Name; auch Vorlage für das Startbild der Exe (tools/make_splash.py)."""
    pm = QPixmap(420, 200)
    pm.fill(QColor("white"))
    icon = QPixmap(str(config.ASSETS_DIR / "icon.png"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setPen(QColor("#C9D3D5"))
    p.drawRect(0, 0, pm.width() - 1, pm.height() - 1)
    if not icon.isNull():
        p.drawPixmap(40, 45, icon.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    font = p.font()
    font.setPointSize(24)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#1D2B2F"))
    p.drawText(pm.rect().adjusted(156, 0, -20, -20), Qt.AlignVCenter | Qt.AlignLeft, config.APP_NAME)
    font.setPointSize(9)
    font.setBold(False)
    p.setFont(font)
    p.setPen(QColor("#5A6A6E"))
    p.drawText(pm.rect().adjusted(0, 0, 0, -6), Qt.AlignBottom | Qt.AlignHCenter,
               f"{config.APP_NAME} {config.APP_VERSION} wird gestartet …")
    p.end()
    return pm


def main() -> int:
    _set_taskbar_identity()
    QLocale.setDefault(QLocale(QLocale.German, QLocale.Germany))
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    splash = None
    if pyi_splash is None:   # Quellcode-Start; in der Exe steht das Startbild schon
        splash = QSplashScreen(splash_pixmap(), Qt.WindowStaysOnTopHint)
        splash.show()
        app.processEvents()

    from app.db import database
    from app.ui import theme
    from app.ui.common import Ctx
    from app.ui.main_window import MainWindow
    theme.apply(app)
    icon = config.ASSETS_DIR / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    sys.excepthook = _excepthook
    con = database.connect()
    try:
        database.init_db(con)
    except database.NewerDatabaseError as e:
        _close_splash(splash)
        QMessageBox.critical(None, config.APP_NAME, str(e))
        return 1
    win = MainWindow(Ctx(con))
    win.showMaximized()
    # nicht sofort schließen: sonst ist kurz der Desktop zu sehen, während Windows das Hauptfenster einblendet
    QTimer.singleShot(SPLASH_LINGER_MS, lambda: _close_splash(splash))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
