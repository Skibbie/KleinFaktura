"""Eigene Anzeige-Elemente."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.money import fmt_eur
from app.ui import theme


class EnergyScale(QWidget):
    """Skala im Stil des Energieausweises: farbiges Band von A+ (grün) bis H (rot), Pfeil markiert den Wert."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value_cents = 0
        self.limit_cents = 1
        self.caption = ""
        self.setMinimumHeight(86)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, value_cents: int, limit_cents: int, caption: str = ""):
        self.value_cents, self.limit_cents, self.caption = value_cents, max(limit_cents, 1), caption
        self.setToolTip(f"{fmt_eur(value_cents)} von {fmt_eur(limit_cents)}")
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        left, right = 2.0, w - 2.0
        bar_top, bar_h = 34.0, 16.0
        span = right - left
        n = len(theme.SCALE)
        seg = span / n
        # Band
        for i, col in enumerate(theme.SCALE):
            r = QRectF(left + i * seg, bar_top, seg + 0.6, bar_h)
            p.fillRect(r, QColor(col))
        # Grenze (100 %) als Abschluss
        p.setPen(QPen(QColor(theme.INK), 1.5))
        p.drawLine(QPointF(right, bar_top - 4), QPointF(right, bar_top + bar_h + 4))
        # Skalenbeschriftung
        small = QFont(theme.DISPLAY_FONT, 8)
        p.setFont(small)
        p.setPen(QColor(theme.INK_2))
        for frac in (0, 0.5, 1.0):
            x = left + frac * span
            txt = fmt_eur(int(self.limit_cents * frac)).replace(",00 €", " €")
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt)
            tx = min(max(x - tw / 2, left), right - tw)
            p.drawText(QPointF(tx, bar_top + bar_h + 15), txt)
        # Pfeil
        ratio = self.value_cents / self.limit_cents
        x = left + min(ratio, 1.0) * span
        col = QColor(theme.scale_color(ratio))
        label = fmt_eur(self.value_cents).replace(",00 €", " €")
        f = QFont(theme.DISPLAY_FONT, 10)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label) + 14
        bx = min(max(x - tw / 2, left), right - tw)
        path = QPainterPath()
        path.addRoundedRect(QRectF(bx, 4, tw, 20), 3, 3)
        tip = QPainterPath()
        tip.moveTo(x - 6, 23)
        tip.lineTo(x + 6, 23)
        tip.lineTo(x, 31)
        tip.closeSubpath()
        p.fillPath(path.united(tip), QColor(theme.INK))
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(bx, 4, tw, 20), Qt.AlignCenter, label)
        # Markierung im Band
        p.setPen(QPen(QColor(theme.INK), 2))
        p.drawLine(QPointF(x, bar_top), QPointF(x, bar_top + bar_h))
        if self.caption:
            p.setFont(QFont(theme.TEXT_FONT, 8))
            p.setPen(col if ratio >= 0.8 else QColor(theme.INK_2))
            fm = p.fontMetrics()
            p.drawText(QPointF(right - fm.horizontalAdvance(self.caption), bar_top + bar_h + 30), self.caption)
        p.end()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(400, 92)
