# -*- coding: utf-8 -*-
"""Shared Persona 5 visual theme: bundled fonts, palette, shapes, and Qt stylesheets.

Palette : Persona Crimson (#E60012), Obsidian Black (#0E0E12 / #15151B / #1C1C24),
          Crisp White (#FFFFFF), and subtle Morgana Scarf Gold (#F5C400).
Fonts   : Bundled SIL OFL fonts loaded from `fonts/` via QFontDatabase:
            * Anton              — heavy condensed Persona 5 display titles & nameplate
            * Barlow Condensed   — crisp tall Persona 5 buttons, badges & popup menus
            * Arsenal            — Optima-inspired flared humanist sans for dialogue & UI text
Rule    : Hover states shift surface/background/border only—never text color.
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPen, QPolygonF

import app_paths

FONTS_DIR = app_paths.resource_path("fonts")
_FONTS_LOADED = False

# Core Persona 5 palette
RED = QColor("#E60012")
RED_HOVER = QColor("#FF1F30")
DEEP_RED = QColor("#8F000C")
LIGHT_RED = QColor("#FF4B55")
BLACK = QColor("#0B0B0B")
WHITE = QColor("#FFFFFF")
GOLD = QColor("#F5C400")        # Morgana's scarf accent (pinned / pending badges)
BLUE = QColor("#2E9BFF")        # placeholder fallback only

# Dark Persona 5 window surfaces (Notes, Tasks, Settings)
P5_BG = QColor("#0E0E12")
P5_CARD = QColor("#15151B")
P5_SURFACE = QColor("#1C1C24")
P5_HOVER = QColor("#252530")
P5_SELECTED = QColor("#2A0C11")
P5_BORDER = QColor("#2C2C38")
P5_BORDER_HI = QColor("#444456")
P5_TEXT = QColor("#F4F4F8")
P5_MUTED = QColor("#9292A4")
P5_GOLD_BG = QColor("#2B2208")
P5_GOLD_FG = QColor("#FAD02C")
P5_RED_BG = QColor("#360A10")
P5_RED_FG = QColor("#FF5E6B")
P5_MINT_BG = QColor("#10281E")
P5_MINT_FG = QColor("#5CE09A")


def load_fonts() -> None:
    """Register bundled Persona 5-style TTF fonts from `fonts/` once a QApplication exists."""
    global _FONTS_LOADED
    if _FONTS_LOADED or QGuiApplication.instance() is None:
        return
    _FONTS_LOADED = True
    if FONTS_DIR.is_dir():
        for path in sorted(FONTS_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(path))


def p5_font(px: int, italic: bool = True) -> QFont:
    """Persona 5 display & UI header font: Anton for big display (>=16px), Barlow Condensed below."""
    load_fonts()
    f = QFont()
    if px >= 16:
        f.setFamilies(["Anton", "Barlow Condensed", "Impact", "Arial Black", "DejaVu Sans"])
        # Anton's Regular face is already Ultra-Black; avoid Qt faux-bold blur
        f.setWeight(QFont.Weight.Normal)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104.0)
    else:
        f.setFamilies(["Barlow Condensed", "Anton", "Arial Black", "Segoe UI", "DejaVu Sans"])
        f.setWeight(QFont.Weight.ExtraBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105.0)
    f.setPixelSize(px)
    if px < 16:
        f.setItalic(italic)
    return f


def p5_dialogue_font(px: int = 15) -> QFont:
    """Optima-inspired flared Persona 5 dialogue font (Arsenal Bold) for Morgana's textbox."""
    load_fonts()
    f = QFont()
    f.setFamilies(["Arsenal", "Optima", "Segoe UI", "Liberation Sans", "DejaVu Sans"])
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.Bold)
    return f


def ui_font(px: int, bold: bool = True) -> QFont:
    """Persona 5 flared humanist sans (Arsenal) for Notes, Tasks, and Settings content."""
    load_fonts()
    f = QFont()
    f.setFamilies(["Arsenal", "Optima", "Segoe UI", "Liberation Sans", "DejaVu Sans"])
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
    """Sharp 5-point Persona 5 cursor/hover star."""
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / points - math.pi / 2
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


# Radiating red & black shards bursting from behind Morgana (Persona 5 battle menu).
# Coordinates relative to the burst center, authored at base sprite size 180 px.
RED_BURST_SHARDS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-6, 10), (-26, 2), (-106, -168), (-96, -162)),
    ((-14, 14), (-28, 28), (-248, -114), (-238, -120)),
    ((-8, 4), (-212, 18), (-186, 48), (-252, 74), (-182, 76),
     (-224, 118), (-128, 96), (-166, 144), (-78, 96), (-12, 54)),
    ((-24, 40), (-36, 52), (-240, 126)),
    ((16, -2), (248, -56), (236, -6), (22, 26)),
    ((10, -14), (24, -6), (178, -126)),
    ((18, 22), (244, 84), (222, 114), (16, 46)),
    ((18, 34), (30, 26), (192, 134)),
)

BLACK_BURST_SHARDS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-14, 6), (-36, 18), (-182, -138)),
    ((-18, 2), (-244, -8), (-22, 20)),
    ((-16, 30), (-38, 44), (-196, 126)),
    ((12, -10), (30, -2), (144, -118)),
    ((20, 4), (238, 12), (20, 22)),
    ((12, 34), (32, 24), (130, 130)),
)


def scale_polygon(points: tuple[tuple[float, float], ...], cx: float, cy: float,
                  scale: float) -> QPolygonF:
    """Build a QPolygonF from relative `points` around `(cx, cy)` scaled by `scale`."""
    return QPolygonF([QPointF(cx + x * scale, cy + y * scale) for x, y in points])


def tapered_wedge(side: int, length: float, h_in: float, h_out: float) -> QPolygonF:
    """Asymmetric Persona 5 command trapezoid: narrow near the character (x=0), wide outward."""
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


# Context & SYSTEM popup menus (Barlow Condensed bold + obsidian + crimson highlight, constant white text)
MENU_QSS = """
QMenu {
    background-color: #0E0E12;
    color: #FFFFFF;
    border: 2px solid #FFFFFF;
    padding: 5px 0px;
    font-family: "Barlow Condensed", "Arsenal", "Segoe UI", sans-serif;
    font-size: 15px;
    font-weight: bold;
}
QMenu::item {
    padding: 6px 28px 6px 16px;
    border-left: 4px solid transparent;
    color: #FFFFFF;
}
QMenu::item:selected {
    background-color: #E60012;
    color: #FFFFFF;
    border-left: 4px solid #FFFFFF;
}
QMenu::item:disabled {
    color: #848496;
}
QMenu::separator {
    height: 2px;
    background: #E60012;
    margin: 5px 10px;
}
QMenu::indicator {
    width: 13px;
    height: 13px;
    margin-left: 6px;
    border: 1.5px solid #FFFFFF;
    background: #0E0E12;
}
QMenu::indicator:checked {
    background: #E60012;
    border: 1.5px solid #FFFFFF;
}
"""

# Popup windows (Notes, Tasks, Settings): Persona 5 obsidian / crimson / white + Arsenal & Barlow Condensed.
# Hover changes background/border only—text color never changes.
DIALOG_QSS = """
QLabel {
    color: #F4F4F8;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
}
QLabel#heading {
    color: #FFFFFF;
    font-family: "Anton", "Barlow Condensed", "Impact", sans-serif;
    font-size: 18px;
}
QLabel#subhead {
    color: #FF4B55;
    font-family: "Barlow Condensed", "Arsenal", sans-serif;
    font-size: 13px;
    font-weight: bold;
}
QLabel#tip {
    color: #9292A4;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
    font-size: 12px;
    font-weight: normal;
}
QLabel#section {
    color: #FF4B55;
    font-family: "Barlow Condensed", "Anton", sans-serif;
    font-size: 15px;
    font-weight: 800;
}
QFrame#card {
    background-color: #15151B;
    border: 1px solid #2C2C38;
    border-radius: 10px;
}
QFrame#subcard {
    background-color: #1B1B23;
    border: 1px solid #323240;
    border-radius: 8px;
}
QFrame#toolbarPill {
    background-color: #1B1B23;
    border: 1px solid #2E2E3B;
    border-radius: 8px;
}
QFrame#undoBar {
    background-color: #24090E;
    border: 1px solid #E60012;
    border-radius: 8px;
}
QProgressBar {
    background-color: #1E1E27;
    border: none;
    border-radius: 3px;
}
QProgressBar::chunk {
    background-color: #E60012;
    border-radius: 3px;
}
QToolTip {
    background-color: #0B0B0E;
    color: #FFFFFF;
    border: 1.5px solid #E60012;
    padding: 4px 8px;
    font-family: "Arsenal", "Segoe UI", sans-serif;
    font-size: 12px;
}
QLineEdit, QTextEdit, QDateTimeEdit, QTimeEdit, QSpinBox {
    background-color: #1A1A22;
    color: #F4F4F8;
    border: 1.5px solid #323240;
    border-radius: 7px;
    selection-background-color: #E60012;
    selection-color: #FFFFFF;
    padding: 5px 10px;
    font-size: 14px;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
}
QLineEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus, QSpinBox:focus {
    background-color: #14141A;
    border: 1.5px solid #E60012;
}
QLineEdit#titleInput {
    background-color: transparent;
    border: 1.5px solid transparent;
    border-radius: 6px;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
    font-size: 19px;
    font-weight: bold;
    color: #FFFFFF;
    padding: 3px 6px;
}
QLineEdit#titleInput:hover {
    background-color: #1D1D26;
}
QLineEdit#titleInput:focus {
    background-color: #1A1A22;
    border: 1.5px solid #E60012;
}
QLineEdit#searchInput {
    background-color: #1A1A22;
    border: 1.5px solid #2C2C38;
    border-radius: 7px;
    padding: 5px 10px;
    font-size: 13px;
    color: #F4F4F8;
}
QLineEdit#searchInput:focus {
    border: 1.5px solid #E60012;
}
QTextEdit#noteBody {
    background-color: #121217;
    border: 1px solid #262632;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 14px;
    color: #EAEAF0;
}
QTextEdit#noteBody:focus {
    border: 1px solid #4A1921;
}
QListWidget {
    background-color: transparent;
    border: none;
    outline: none;
}
QScrollBar:vertical {
    background: transparent;
    width: 7px;
    margin: 4px 0px;
}
QScrollBar::handle:vertical {
    background: #3A3A4A;
    border-radius: 3px;
    min-height: 26px;
}
QScrollBar::handle:vertical:hover {
    background: #E60012;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QCalendarWidget QWidget {
    background-color: #15151B;
    color: #FFFFFF;
    font-family: "Arsenal", "Segoe UI", sans-serif;
}
QCalendarWidget QAbstractItemView:enabled {
    color: #FFFFFF;
    background-color: #15151B;
    selection-background-color: #E60012;
    selection-color: #FFFFFF;
}
QCalendarWidget QAbstractItemView:disabled {
    color: #5C5C6E;
}
QCalendarWidget QToolButton {
    color: #FFFFFF;
    background-color: #20202A;
    border-radius: 4px;
    padding: 3px 6px;
}
"""
