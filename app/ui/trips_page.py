"""Fahrten mit dem Privat-Pkw (Kilometerpauschale)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget)

from app.db import repo
from app.db.database import get_setting
from app.money import fmt_date, fmt_eur, to_decimal
from app.services import expenses, trips
from app.ui.common import (Ctx, GuardedDialog, button, confirm, date_edit, date_value, error, hbox, item, make_table,
                           open_file, page_title, searchable_combo, selected_data)


def _year_combo() -> QComboBox:
    cb = QComboBox()
    cb.addItem("Alle Jahre", None)
    for y in range(date.today().year, date.today().year - 11, -1):
        cb.addItem(str(y), y)
    cb.setCurrentIndex(1)
    return cb


# ======================================================================= Erfassung
class TripDialog(GuardedDialog, QDialog):
    dirty_text = "Die Fahrt wurde geändert."

    def __init__(self, parent, ctx: Ctx, trip: dict | None = None, embedded: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self.trip = dict(trip or {})
        self.setWindowTitle("Fahrt")
        self.setMinimumWidth(640)
        locked = bool(self.trip.get("trip_date")) and expenses.is_locked(ctx.con, self.trip["trip_date"][:4])
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Fahrt bearbeiten" if self.trip.get("id") else "Neue Fahrt")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(button("Zurück zur Liste" if embedded else "Schließen", self.close_dialog))
        lay.addLayout(head)
        if locked:
            w = QLabel(f"Das Jahr {self.trip['trip_date'][:4]} ist abgeschlossen. Die Fahrt kann nur angesehen werden.")
            w.setObjectName("warn")
            lay.addWidget(w)

        box = QFrame()
        box.setObjectName("card")
        f = QFormLayout(box)
        f.setContentsMargins(18, 16, 18, 16)
        f.setVerticalSpacing(10)
        self.date = date_edit(self.trip.get("trip_date"))

        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.NoInsert)
        self.customer.addItem("(kein Kunde, z.B. Fortbildung, Einkauf)", None)
        for c in repo.list_customers(ctx.con, include_inactive=bool(self.trip.get("customer_id"))):
            label = repo.customer_display_name(c) + (f", {c['city']}" if c.get("city") else "")
            self.customer.addItem(label, c["id"])
        self.customer.completer().setFilterMode(Qt.MatchContains)
        self.customer.completer().setCompletionMode(QCompleter.PopupCompletion)
        searchable_combo(self.customer)
        self.customer.setCurrentIndex(max(self.customer.findData(self.trip.get("customer_id")), 0))
        self.project = QComboBox()
        self.document = QComboBox()
        self.destination = QLineEdit(self.trip.get("destination", ""))
        self.destination.setPlaceholderText("Straße, PLZ Ort")
        self.destination.editingFinished.connect(self._destination_done)
        self.purpose = QComboBox()
        self.purpose.setEditable(True)
        self.purpose.addItems([""] + repo.text_choices(ctx.con, trips.PURPOSE_CATEGORY))
        self.purpose.setCurrentText(self.trip.get("purpose", ""))
        self.remember_purpose = QCheckBox("In Textbausteine übernehmen")
        self.remember_purpose.setToolTip("Neuer Anlass steht danach in der Auswahl (Textbausteine → Anlass Fahrt)")
        self.remember_purpose.setChecked(get_setting(ctx.con, "textblock.remember_default", "1") == "1")
        self.km_one_way = QLineEdit(trips.fmt_km(self.trip.get("km_one_way") or self.trip.get("km") or "")
                                    if (self.trip.get("km_one_way") or self.trip.get("km")) else "")
        self.km_one_way.setPlaceholderText("einfache Strecke")
        self.km_one_way.textChanged.connect(self._update_calc)
        self.round_trip = QCheckBox("Hin- und Rückfahrt")
        self.round_trip.setChecked(bool(self.trip.get("round_trip", 1)) if self.trip.get("km_one_way") or not self.trip.get("id") else False)
        self.round_trip.toggled.connect(self._update_calc)
        self.km_hint = QLabel()
        self.km_hint.setObjectName("muted")
        self.calc = QLabel()
        self.calc.setObjectName("muted")
        self.note = QPlainTextEdit(self.trip.get("note", ""))
        self.note.setFixedHeight(60)

        f.addRow("Datum", self.date)
        f.addRow("Kunde", self.customer)
        f.addRow("Objekt", self.project)
        f.addRow("Zum Beleg", self.document)
        self.route_btn = button("Route anzeigen", self._show_route,
                                tooltip="Öffnet die Strecke von der eigenen Anschrift zum Ziel im Browser (Google Maps). "
                                        "Die Kilometer dort ablesen und unten eintragen.")
        f.addRow("Ziel", hbox(self.destination, self.route_btn))
        f.addRow("Anlass", self.purpose)
        f.addRow("", self.remember_purpose)
        f.addRow("Strecke einfach (km)", hbox(self.km_one_way, self.round_trip, None))
        f.addRow("", self.km_hint)
        f.addRow("", self.calc)
        f.addRow("Notiz", self.note)
        lay.addWidget(box)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setProperty("primary", True)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._fill_customer_lists(self.trip.get("project_id"), self.trip.get("document_id"))
        self.customer.currentIndexChanged.connect(self._customer_changed)
        self.project.currentIndexChanged.connect(self._project_changed)
        if locked:
            for w in (self.date, self.customer, self.project, self.document, self.destination, self.route_btn, self.purpose,
                      self.remember_purpose, self.km_one_way, self.round_trip, self.note):
                w.setEnabled(False)
            bb.button(QDialogButtonBox.Save).setEnabled(False)
        if not self.trip.get("id") and not self.trip.get("destination") and self.customer.currentData():
            self._apply_destination()  # vorbelegt, z.B. aus der Rechnung
        self._update_calc()
        self.mark_clean()

    def _snapshot(self):
        return (date_value(self.date), self.customer.currentData(), self.project.currentData(), self.document.currentData(),
                self.destination.text(), self.purpose.currentText(), self.km_one_way.text(), self.round_trip.isChecked(),
                self.note.toPlainText())

    # ------------------------------------------------------------ Kunde -> Objekt, Beleg, Ziel, km
    def _fill_customer_lists(self, project_id=None, document_id=None):
        cid = self.customer.currentData()
        for cb in (self.project, self.document):
            cb.blockSignals(True)
            cb.clear()
        self.project.addItem("Kundenadresse", None)
        self.document.addItem("–", None)
        if cid:
            for p in repo.list_projects(self.ctx.con, cid):
                self.project.addItem(" – ".join(x for x in [p.get("name"), repo.project_address(p)] if x) or f"Objekt {p['id']}", p["id"])
            for d in trips.documents_for(self.ctx.con, cid):
                kind = "Rechnung" if d["doc_type"] == "rechnung" else "Angebot"
                self.document.addItem(f"{kind} {d['number']} vom {fmt_date(d['doc_date'])}" + (f" – {d['subject']}" if d.get("subject") else ""),
                                      d["id"])
        self.project.setCurrentIndex(max(self.project.findData(project_id), 0))
        self.document.setCurrentIndex(max(self.document.findData(document_id), 0))
        for cb in (self.project, self.document):
            cb.blockSignals(False)
            cb.setEnabled(bool(cid))

    def _customer_changed(self, *_):
        self._fill_customer_lists()
        self._apply_destination()

    def _project_changed(self, *_):
        self._apply_destination()

    def _apply_destination(self):
        dest = trips.default_destination(self.ctx.con, self.customer.currentData(), self.project.currentData())
        if dest:
            self.destination.setText(dest)
        self._apply_remembered()

    def _destination_done(self):
        if not self.km_one_way.text().strip():
            self._apply_remembered()

    def _apply_remembered(self):
        """Kilometer der letzten Fahrt zum selben Ziel vorschlagen (nur bei neuen Fahrten)."""
        if self.trip.get("id"):
            return
        r = trips.remembered_km(self.ctx.con, self.destination.text().strip(), self.customer.currentData(),
                                self.project.currentData())
        if r:
            self.km_one_way.setText(trips.fmt_km(r[0]))
            self.round_trip.setChecked(r[1])
            self.km_hint.setText("Kilometer von der letzten Fahrt zu diesem Ziel übernommen, bitte prüfen.")
        else:
            self.km_hint.setText("")
        self.km_hint.setVisible(bool(self.km_hint.text()))

    def _show_route(self):
        dest = self.destination.text().strip()
        start = trips.own_address(self.ctx.con)
        if not dest:
            error(self, "Route anzeigen", "Bitte zuerst das Ziel angeben.")
            return
        if not start:
            error(self, "Route anzeigen", "Die eigene Anschrift fehlt (Einstellungen → Firma).")
            return
        QDesktopServices.openUrl(QUrl(trips.route_url(start, dest)))

    def _update_calc(self, *_):
        try:
            km = trips.total_km(self.km_one_way.text() or "0", self.round_trip.isChecked())
            rate = self.trip.get("rate") or trips.current_rate(self.ctx.con)
            txt = (f"{trips.fmt_km(km)} km × {trips.fmt_rate(rate)} € = {fmt_eur(trips.amount_cents(km, rate))}"
                   if km else "")
        except ValueError:
            txt = "Ungültige Zahl."
        self.calc.setText(txt)
        self.calc.setVisible(bool(txt))
        if not self.km_hint.text():
            self.km_hint.setVisible(False)

    def save(self):
        d = {"id": self.trip.get("id"), "trip_date": date_value(self.date), "customer_id": self.customer.currentData(),
             "project_id": self.project.currentData(), "document_id": self.document.currentData(),
             "destination": self.destination.text(), "purpose": self.purpose.currentText(),
             "round_trip": self.round_trip.isChecked(), "rate": self.trip.get("rate"), "note": self.note.toPlainText()}
        try:
            d["km_one_way"] = str(to_decimal(self.km_one_way.text() or "0"))
            self.trip_id = trips.save_trip(self.ctx.con, d)
            if self.remember_purpose.isChecked():
                repo.remember_text(self.ctx.con, trips.PURPOSE_CATEGORY, d["purpose"])
        except Exception as e:  # noqa: BLE001
            error(self, "Fahrt nicht gespeichert", e)
            return
        self.mark_clean()
        self.accept()


# ======================================================================= Liste
class TripsPage(QWidget):
    def __init__(self, ctx: Ctx):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.addWidget(page_title("Fahrten"))
        intro = QLabel("Betriebliche Fahrten mit dem privaten Pkw. Je Kilometer wird die Pauschale angesetzt "
                       "(Einstellungen → Fahrten), die Jahressumme erscheint unter „EÜR-Werte“. Park- und "
                       "Mautgebühren gehören zusätzlich als Ausgabe ins Ausgabenbuch.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Kunde, Ziel, Anlass, Rechnungsnummer) …")
        self.search.textChanged.connect(self.reload)
        self.year = _year_combo()
        self.year.currentIndexChanged.connect(self.reload)
        lay.addLayout(hbox(self.search, self.year))
        lay.addLayout(hbox(button("Neue Fahrt", self.new, primary=True), button("Bearbeiten", self.edit),
                           button("Als neue Fahrt kopieren", self.copy, tooltip="z.B. zweiter Termin beim selben Kunden"),
                           button("Löschen", self.delete, danger=True), None,
                           button("Als Excel speichern", self.export)))
        self.table = make_table(["Datum", "Kunde", "Ziel", "Anlass", "Beleg", "km", "Betrag"], stretch_col=2)
        self.table.doubleClicked.connect(self.edit)
        lay.addWidget(self.table)
        self.sum_label = QLabel()
        lay.addWidget(self.sum_label)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        ctx.listeners.append(self.reload)
        self.reload()

    def reload(self, *_):
        y = self.year.currentData()
        rows = trips.list_trips(self.ctx.con, y, self.search.text().strip())
        self.table.setRowCount(0)
        total = 0
        km = to_decimal(0)
        for t in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [fmt_date(t["trip_date"]), t["customer_name"], t["destination"], t["purpose"], t.get("doc_number") or "",
                    trips.fmt_km(t["km"]), fmt_eur(t["amount_cents"])]
            for i, v in enumerate(vals):
                self.table.setItem(r, i, item(v, t["id"] if i == 0 else None, align_right=i in (5, 6)))
            total += t["amount_cents"]
            km += to_decimal(t["km"])
        period = self.year.currentText() if y else "alle Jahre"
        self.sum_label.setText(f"{len(rows)} Fahrten {period}, {trips.fmt_km(km)} km, {fmt_eur(total)}")
        hint = ""
        if y:
            missing = trips.invoices_without_trip(self.ctx.con, y)
            if missing:
                nums = ", ".join(m["number"] for m in missing[:6]) + (" …" if len(missing) > 6 else "")
                hint = (f"Zu {len(missing)} Rechnungen {y} ist keine Fahrt verknüpft ({nums}). "
                        "Falls dafür Fahrten angefallen sind, bitte nachtragen.")
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def _open(self, trip=None):
        host = getattr(self.ctx, "editor_host", None)
        dlg = TripDialog(self, self.ctx, trip, embedded=host is not None)
        if host is not None:
            host.open_embedded(dlg, return_row=host.page_index("fahrten"))
        elif dlg.exec():
            self.ctx.changed()

    def new(self):
        self._open()

    def edit(self, *_):
        tid = selected_data(self.table)
        if tid:
            self._open(trips.get_trip(self.ctx.con, tid))

    def copy(self):
        tid = selected_data(self.table)
        if not tid:
            return
        t = trips.get_trip(self.ctx.con, tid)
        keep = ("customer_id", "project_id", "document_id", "destination", "purpose", "km_one_way", "round_trip", "km")
        self._open({k: t[k] for k in keep})

    def delete(self):
        tid = selected_data(self.table)
        if not tid:
            return
        t = trips.get_trip(self.ctx.con, tid)
        if confirm(self, "Fahrt löschen", f"Fahrt vom {fmt_date(t['trip_date'])} nach {t['destination']} "
                   f"({trips.fmt_km(t['km'])} km) löschen?\n\nDie Löschung wird protokolliert."):
            try:
                trips.delete_trip(self.ctx.con, tid)
            except Exception as ex:  # noqa: BLE001
                error(self, "Löschen nicht möglich", ex)
            self.ctx.changed()

    def export(self):
        y = self.year.currentData() or date.today().year
        p, _ = QFileDialog.getSaveFileName(self, "Fahrten speichern", str(Path.home() / "Documents" / f"Fahrten-{y}.xlsx"),
                                           "Excel (*.xlsx)")
        if not p:
            return
        from app.services import export
        try:
            export.write_xlsx(Path(p), {f"Fahrten {y}": (trips.EXPORT_HEADER, trips.rows_for_export(self.ctx.con, y))})
            open_file(p)
        except Exception as e:  # noqa: BLE001
            error(self, "Export", e)
