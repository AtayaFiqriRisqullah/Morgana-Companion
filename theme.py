# -*- coding: utf-8 -*-
"""Tema visual bersama (gaya Persona 5, khusus UI): warna, font, bentuk, dan stylesheet Qt.

Palet : merah Persona (#E60012), hitam pekat, putih. Tanpa gradasi; semua bidang datar.
Bentuk: jajargenjang miring, bayangan merah bergeser, bintang bergerigi, spike merah-hitam.
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPen, QPolygonF

RED = QColor("#E60012")
DEEP_RED = QColor("#8F000C")
LIGHT_RED = QColor("#FF4B55")
BLACK = QColor("#0B0B0B")
WHITE = QColor("#FFFFFF")
BLUE = QColor("#2E9BFF")      # hanya dipakai placeholder karakter, bukan UI


def p5_font(px: int, italic: bool = True) -> QFont:
    """Font tebal bergaya komik; urutan fallback aman untuk Windows & Linux."""
    f = QFont()
    f.setFamilies(["Arial Black", "Segoe UI", "Noto Sans", "DejaVu Sans", "Sans Serif"])
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.Bold)
    f.setItalic(italic)
    return f


def ui_font(px: int, bold: bool = True) -> QFont:
    """Font biasa yang mudah dibaca (daftar, isi catatan); font p5_font hanya untuk judul/tombol."""
    f = QFont()
    f.setFamilies(["Segoe UI", "Noto Sans", "DejaVu Sans", "Sans Serif"])
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return f


def pen(color: QColor, width: float) -> QPen:
    return QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap,
                Qt.PenJoinStyle.MiterJoin)


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def star_polygon(cx: float, cy: float, r_out: float, r_in: float, points: int = 5) -> QPolygonF:
    """Bintang bergerigi (ujung runcing) ala ikon/kursor Persona 5."""
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / points - math.pi / 2
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


def spike_border(rect: QRectF, length: float = 22.0, step: float = 26.0, seed: int = 0,
                 lean: float = 0.45, jitter: float = 0.45) -> QPolygonF:
    """Serpihan runcing (spike) di sekeliling `rect`, seperti pecahan merah-hitam di menu
    Persona 5. Tiap spike menjorok keluar sejauh `length` (diacak, deterministik per `seed`)
    dan sedikit miring (`lean`). Isi polygon dengan RED/BLACK di belakang bentuk utama."""
    rnd = random.Random(seed)
    corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
    pts = []
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        dx, dy = b.x() - a.x(), b.y() - a.y()
        side = math.hypot(dx, dy)
        if side < 1e-6:
            continue
        nx, ny = dy / side, -dx / side                 # normal keluar (sisi searah jarum jam)
        n = max(1, round(side / step))
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            pts.append(QPointF(a.x() + dx * t0, a.y() + dy * t0))             # pangkal spike
            tm = (t0 + t1) / 2 + lean * (t1 - t0) * 0.5                       # ujung, agak miring
            ln = length * (1.0 - jitter + rnd.random() * jitter * 2.0)
            pts.append(QPointF(a.x() + dx * tm + nx * ln, a.y() + dy * tm + ny * ln))
    return QPolygonF(pts)


# Menu klik kanan & menu popup (QMenu)
MENU_QSS = """
QMenu { background-color: #0B0B0B; color: #FFFFFF; border: 2px solid #FFFFFF;
        padding: 4px; font-family: "Arial Black", "Segoe UI", "Noto Sans", sans-serif;
        font-weight: bold; }
QMenu::item { padding: 6px 26px 6px 18px; border-left: 4px solid transparent; }
QMenu::item:selected { background-color: #E60012; color: #FFFFFF; border-left: 4px solid #FFFFFF; }
QMenu::item:disabled { color: #777777; }
QMenu::separator { height: 2px; background: #E60012; margin: 4px 6px; }
"""

# Isi jendela popup TASKS / NOTES. Tombol digambar sendiri (P5Button), jadi tidak ada aturan QPushButton.
DIALOG_QSS = """
QLabel { color: #FFFFFF; font-weight: bold; }
QLabel#tip { color: #9A9A9A; font-weight: normal; font-size: 11px; }
QLabel#section { color: #FF4B55; font-size: 13px; padding-top: 6px; }
QProgressBar { background-color: #161616; border: 2px solid #FFFFFF; }
QProgressBar::chunk { background-color: #E60012; }
QToolTip { background-color: #0B0B0B; color: #FFFFFF; border: 2px solid #E60012; padding: 3px; }
QLineEdit, QTextEdit, QListWidget, QDateTimeEdit, QTimeEdit, QSpinBox {
    background-color: #161616; color: #FFFFFF; border: 2px solid #FFFFFF;
    selection-background-color: #E60012; selection-color: #FFFFFF;
    padding: 4px; font-size: 13px;
    font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif;
}
QLineEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus, QSpinBox:focus { border: 2px solid #E60012; }
QListWidget::item { padding: 5px 4px; border-left: 4px solid transparent; }
QListWidget::item:selected { background-color: #E60012; color: #FFFFFF; border-left: 4px solid #FFFFFF; }
QListWidget::item:hover:!selected { background-color: #3A0008; }
QListWidget::indicator, QCheckBox::indicator {
    width: 16px; height: 16px; border: 2px solid #FFFFFF; background-color: #0B0B0B;
}
QListWidget::indicator:checked, QCheckBox::indicator:checked { background-color: #E60012; }
QCheckBox { color: #FFFFFF; font-weight: bold; spacing: 8px; }
QScrollBar:vertical { background: #0B0B0B; width: 12px; margin: 0px; }
QScrollBar::handle:vertical { background: #E60012; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QCalendarWidget QWidget { background-color: #0B0B0B; color: #FFFFFF; }
QCalendarWidget QAbstractItemView:enabled {
    color: #FFFFFF; background-color: #0B0B0B;
    selection-background-color: #E60012; selection-color: #FFFFFF;
}
QCalendarWidget QAbstractItemView:disabled { color: #666666; }
QCalendarWidget QToolButton { color: #FFFFFF; background-color: #161616; }
"""