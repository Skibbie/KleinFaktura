"""UI-Aktionen: Beleg per Thunderbird versenden, PDF-Kopie in den Wunschordner nach dem Festschreiben."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from app.services import mail
from app.ui.common import Ctx, error


def send_mail(parent, ctx: Ctx, doc: dict) -> None:
    if doc.get("status") != "festgeschrieben":
        QMessageBox.information(parent, "E-Mail", "Entwürfe können nicht versendet werden – bitte zuerst festschreiben.")
        return
    try:
        to = mail.open_compose(ctx.con, doc)
    except mail.MailError as e:
        QMessageBox.warning(parent, "E-Mail", str(e))
        return
    except Exception as e:  # noqa: BLE001
        error(parent, "E-Mail", f"Thunderbird konnte nicht gestartet werden: {e}")
        return
    if not to:
        QMessageBox.information(parent, "E-Mail", "Beim Kunden ist keine E-Mail-Adresse hinterlegt – bitte den "
                                                  "Empfänger in Thunderbird eintragen.")


def ask_send_after_finalize(parent, text: str) -> bool:
    """Erfolgsmeldung nach dem Festschreiben mit Wahl „Per E-Mail senden“ oder „PDF öffnen“."""
    box = QMessageBox(QMessageBox.Information, "Festgeschrieben", text, parent=parent)
    mail_btn = box.addButton("Per E-Mail senden…", QMessageBox.ActionRole)
    box.addButton("PDF öffnen", QMessageBox.AcceptRole)
    box.exec()
    return box.clickedButton() is mail_btn


def export_after_finalize(parent, ctx: Ctx, doc: dict) -> str:
    """Kopiert das PDF in den Wunschordner. Rückgabe: Hinweiszeile für die Erfolgsmeldung (ggf. leer).
    Ein Fehler hier ändert nichts am Festschreiben – das Archiv-PDF ist bereits gesichert."""
    try:
        target = mail.export_copy(ctx.con, doc)
    except Exception as e:  # noqa: BLE001
        QMessageBox.warning(parent, "PDF-Kopie", f"Das PDF konnte nicht in den PDF-Ordner kopiert werden:\n{e}\n\n"
                                                 "Der Beleg ist trotzdem festgeschrieben und im Archiv gespeichert.")
        return ""
    return f"\n\nKopie gespeichert unter:\n{target}" if target else ""
