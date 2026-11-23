# -*- coding: utf-8 -*-
"""Tema visual bersama (gaya Persona 5, khusus UI): warna, font, bentuk, dan stylesheet Qt.

Palet : merah Persona (#E60012), hitam pekat, putih. Tanpa gradasi; semua bidang datar.
Bentuk: jajargenjang miring, bayangan merah bergeser, bintang bergerigi, spike merah-hitam.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
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


# Serpihan spike merah & hitam yang memancar dari belakang karakter (ala menu battle P5).
# Koordinat relatif terhadap titik pusat ledakan, untuk ukuran sprite dasar 180 px.
RED_BURST_SHARDS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-6, 10), (-26, 2), (-106, -168), (-96, -162)),            # jarum merah tinggi kiri-atas
    ((-14, 14), (-28, 28), (-248, -114), (-238, -120)),         # garis merah panjang di atas kiri
    ((-8, 4), (-212, 18), (-186, 48), (-252, 74), (-182, 76),   # pecahan merah besar kiri-bawah
     (-224, 118), (-128, 96), (-166, 144), (-78, 96), (-12, 54)),
    ((-24, 40), (-36, 52), (-240, 126)),                        # jarum tipis kiri-bawah
    ((16, -2), (248, -56), (236, -6), (22, 26)),                # bilah merah di belakang kanan-atas
    ((10, -14), (24, -6), (178, -126)),                         # jarum merah kanan-atas
    ((18, 22), (244, 84), (222, 114), (16, 46)),                # bilah merah di bawah kanan-bawah
    ((18, 34), (30, 26), (192, 134)),                           # jarum tipis kanan-bawah
)

BLACK_BURST_SHARDS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-14, 6), (-36, 18), (-182, -138)),                        # serpihan hitam kiri-atas
    ((-18, 2), (-244, -8), (-22, 20)),                          # jarum hitam di celah kiri
    ((-16, 30), (-38, 44), (-196, 126)),                        # tebasan hitam kiri-bawah
    ((12, -10), (30, -2), (144, -118)),                         # serpihan hitam kanan-atas
    ((20, 4), (238, 12), (20, 22)),                             # jarum hitam di celah kanan
    ((12, 34), (32, 24), (130, 130)),                           # serpihan hitam kanan-bawah
)


def scale_polygon(points: tuple[tuple[float, float], ...], cx: float, cy: float,
                  scale: float) -> QPolygonF:
    """Bangun QPolygonF dari koordinat relatif `points` dengan pusat `(cx, cy)` dan skala."""
    return QPolygonF([QPointF(cx + x * scale, cy + y * scale) for x, y in points])


def tapered_wedge(side: int, length: float, h_in: float, h_out: float) -> QPolygonF:
    """Trapesium meruncing ala tombol menu battle Persona 5: sempit di sisi karakter (x=0),
    melebar ke luar (x = side * length) dengan potongan ujung miring."""
    if side < 0:
        return QPolygonF([
            QPointF(-length - 6.0, -h_out * 0.56),
            QPointF(2.0, -h_in * 0.52),
            QPointF(0.0, h_in * 0.48),
            QPointF(-length + 8.0, h_out * 0.46),
        ])
    return QPolygonF([
        QPointF(-2.0, -h_in * 0.52),
        QPointF(length + 6.0, -h_out * 0.56),
        QPointF(length - 8.0, h_out * 0.46),
        QPointF(0.0, h_in * 0.48),
    ])

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