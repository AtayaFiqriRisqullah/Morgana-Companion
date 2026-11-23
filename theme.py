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

# Palet lembut untuk jendela Notes, Tasks, dan Settings (kartu putih membulat di atas kanvas terang)
UI_BG = QColor("#EAEEF5")
UI_CARD = QColor("#FFFFFF")
UI_SURFACE = QColor("#F6F8FC")
UI_HOVER = QColor("#EEF2FA")
UI_SELECTED = QColor("#E9EEFF")
UI_BORDER = QColor("#E2E7F2")
UI_TEXT = QColor("#1E2433")
UI_MUTED = QColor("#737C91")
UI_ACCENT = QColor("#6E85F7")
UI_ACCENT_HOVER = QColor("#5B73EB")
UI_AMBER = QColor("#FCD674")
UI_AMBER_HOVER = QColor("#F6C956")
UI_AMBER_SOFT = QColor("#FEF0C7")
UI_AMBER_TEXT = QColor("#8F5F00")
UI_MINT_SOFT = QColor("#DCFCE7")
UI_MINT_TEXT = QColor("#166534")
UI_ROSE_SOFT = QColor("#FEE4E7")
UI_ROSE_HOVER = QColor("#FCD1D7")
UI_ROSE_TEXT = QColor("#C43245")
UI_LAVENDER_SOFT = QColor("#E3E7FF")

# Stylesheet jendela popup (Notes, Tasks, Settings) — terang, bersih, membulat.
# Warna teks sengaja tidak berubah saat hover.
DIALOG_QSS = """
QLabel {
    color: #1E2433;
    font-family: "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
}
QLabel#heading {
    color: #1E2433;
    font-size: 16px;
    font-weight: bold;
}
QLabel#subhead {
    color: #6E85F7;
    font-size: 12px;
    font-weight: bold;
}
QLabel#tip {
    color: #7B8499;
    font-size: 11px;
    font-weight: normal;
}
QLabel#section {
    color: #1E2433;
    font-size: 13px;
    font-weight: bold;
}
QFrame#card {
    background-color: #FFFFFF;
    border: 1px solid #E2E7F2;
    border-radius: 16px;
}
QFrame#topbar {
    background-color: #FFFFFF;
    border: 1px solid #E2E7F2;
    border-radius: 14px;
}
QFrame#toolbarPill {
    background-color: #F4F6FB;
    border: 1px solid #E6EBF5;
    border-radius: 10px;
}
QFrame#subcard {
    background-color: #F7F9FD;
    border: 1px solid #E7ECF5;
    border-radius: 12px;
}
QProgressBar {
    background-color: #EAEEF7;
    border: none;
    border-radius: 4px;
}
QProgressBar::chunk {
    background-color: #6E85F7;
    border-radius: 4px;
}
QToolTip {
    background-color: #1E2433;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11px;
}
QLineEdit, QTextEdit, QDateTimeEdit, QTimeEdit, QSpinBox {
    background-color: #F6F8FC;
    color: #1E2433;
    border: 1px solid #E2E7F2;
    border-radius: 10px;
    selection-background-color: #B9C9FF;
    selection-color: #1E2433;
    padding: 4px 10px;
    font-size: 13px;
    font-family: "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
}
QLineEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus, QSpinBox:focus {
    background-color: #FFFFFF;
    border: 1px solid #6E85F7;
}
QLineEdit#titleInput {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
    color: #1E2433;
    padding: 4px 6px;
}
QLineEdit#titleInput:hover {
    background-color: #F6F8FC;
}
QLineEdit#titleInput:focus {
    background-color: #F6F8FC;
    border: 1px solid #D3DCFA;
}
QLineEdit#searchInput {
    background-color: #F4F6FB;
    border: 1px solid #E6EBF5;
    border-radius: 10px;
    padding: 5px 10px;
    font-size: 12px;
    color: #1E2433;
}
QLineEdit#searchInput:focus {
    background-color: #FFFFFF;
    border: 1px solid #6E85F7;
}
QTextEdit#noteBody {
    background-color: #FFFFFF;
    border: none;
    padding: 4px 2px;
    font-size: 13px;
    color: #2B3245;
}
QListWidget {
    background-color: transparent;
    border: none;
    outline: none;
}
QCheckBox {
    color: #1E2433;
    font-weight: 600;
    font-size: 13px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1.5px solid #C4CCDE;
    border-radius: 6px;
    background-color: #FFFFFF;
}
QCheckBox::indicator:hover {
    border: 1.5px solid #6E85F7;
}
QCheckBox::indicator:checked {
    border: 1.5px solid #6E85F7;
    background-color: #6E85F7;
}
QScrollBar:vertical {
    background: transparent;
    width: 7px;
    margin: 4px 0px;
}
QScrollBar::handle:vertical {
    background: #CFD6E6;
    border-radius: 3px;
    min-height: 26px;
}
QScrollBar::handle:vertical:hover {
    background: #AEB8CF;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QCalendarWidget QWidget {
    background-color: #FFFFFF;
    color: #1E2433;
}
QCalendarWidget QAbstractItemView:enabled {
    color: #1E2433;
    background-color: #FFFFFF;
    selection-background-color: #6E85F7;
    selection-color: #FFFFFF;
}
QCalendarWidget QAbstractItemView:disabled {
    color: #B2BACB;
}
QCalendarWidget QToolButton {
    color: #1E2433;
    background-color: #F4F6FB;
    border-radius: 6px;
    padding: 3px 6px;
}
"""
