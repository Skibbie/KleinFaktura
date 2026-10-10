"""Update-Hinweis beim Start, „Nach Updates suchen“ und Installation (siehe app.services.update)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app import config
from app.db.database import get_setting
from app.services import update

CHECK_SETTING = "update.check_on_start"


def release_page() -> str:
    return f"github.com/{update.REPO}/releases"


class _Result(QObject):
    done = Signal(object, object)  # Ergebnis, Fehler – wird im GUI-Thread zugestellt


def _in_background(fn, on_done) -> _Result:
    res = _Result()
    res.done.connect(on_done, Qt.QueuedConnection)  # Rückmeldung immer im GUI-Thread (Dialoge)

    def run():
        try:
            res.done.emit(fn(), None)
        except Exception as e:  # noqa: BLE001
            res.done.emit(None, e)
    threading.Thread(target=run, daemon=True).start()
    return res  # Referenz halten, bis das Signal angekommen ist


def check_on_start(win) -> None:
    """Fragt nach dem Start still im Hintergrund nach; meldet sich nur, wenn es eine neuere Version gibt."""
    if update.install_dir() is None or get_setting(win.ctx.con, CHECK_SETTING, "1") != "1":
        return

    def done(release, err):
        win._update_check = None
        if err is None and release and update.is_newer(release.version):
            offer(win, release)
    win._update_check = _in_background(update.latest_release, done)


def check_now(win) -> None:
    """Knopf „Jetzt nach Updates suchen“."""
    if update.install_dir() is None:
        QMessageBox.information(win, "Update", "Updates lassen sich nur in der installierten Version einspielen.\n"
                                               f"Laufende Version: {config.APP_VERSION} (aus dem Quellcode)")
        return
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        release = update.latest_release()
    except update.UpdateError as e:
        QApplication.restoreOverrideCursor()
        QMessageBox.warning(win, "Update", f"Die Suche nach Updates ist fehlgeschlagen:\n{e}")
        return
    QApplication.restoreOverrideCursor()
    if update.is_newer(release.version):
        offer(win, release)
    else:
        QMessageBox.information(win, "Update", f"{config.APP_NAME} {config.APP_VERSION} ist aktuell.")


def offer(win, release: update.Release) -> None:
    box = QMessageBox(win)
    box.setWindowTitle("Update verfügbar")
    box.setIcon(QMessageBox.Information)
    box.setText(f"{config.APP_NAME} {release.version} ist verfügbar (installiert: {config.APP_VERSION}).\n\n"
                "Jetzt aktualisieren? Das Programm wird dazu beendet (mit der üblichen Sicherung) "
                "und startet danach in der neuen Version.")
    if release.notes:
        box.setDetailedText(release.notes)
    now = box.addButton("Jetzt aktualisieren", QMessageBox.AcceptRole)
    box.addButton("Später", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is now:
        install(win, release)


def install(win, release: update.Release) -> None:
    target = update.install_dir()
    if target is None:
        return
    dlg = QProgressDialog(f"{config.APP_NAME} {release.version} wird heruntergeladen …", "", 0, 0, win)
    dlg.setWindowTitle("Update")
    dlg.setCancelButton(None)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.show()
    loop = QEventLoop()
    out: dict = {}

    def done(new_dir, err):
        out.update(new_dir=new_dir, err=err)
        loop.quit()
    setup = update.installed_by_setup(target)
    fetch = (lambda: update.download_setup(release)) if setup else (lambda: update.download(release, target))
    keep = _in_background(fetch, done)  # noqa: F841
    loop.exec()
    dlg.close()
    if out["err"] is not None:
        QMessageBox.warning(win, "Update", f"Das Update konnte nicht heruntergeladen werden:\n{out['err']}")
        return
    new_dir = out["new_dir"]
    if win.close_for(lambda: update.launch_helper(new_dir, target, setup=setup)):
        QApplication.quit()  # ausdrücklich beenden, das Skript wartet auf das Prozessende
    else:
        QMessageBox.information(win, "Update", "Update abgebrochen, das Programm läuft weiter. "
                                               "Der Hinweis kommt beim nächsten Start wieder.")


def schedule_start_check(win, delay_ms: int = 3000) -> None:
    QTimer.singleShot(delay_ms, win, lambda: check_on_start(win))
