# -*- coding: utf-8 -*-
"""Shared Persona 5 visual theme: bundled fonts, palette, shapes, and Qt stylesheets.

Palette : Persona Crimson (#E60012), Obsidian Black (#0E0E12 / #15151B / #1C1C24),
          Crisp White (#FFFFFF), and subtle Morgana Scarf Gold (#F5C400).
Fonts   : Bundled SIL OFL fonts loaded from `assets/fonts/` via QFontDatabase:
            * Anton              — heavy condensed Persona 5 display titles & nameplate
            * Barlow Condensed   — crisp tall Persona 5 buttons, badges & popup menus
            * Arsenal            — Optima-inspired flared humanist sans for dialogue & UI text
Rule    : Hover states shift surface/background/border by default; text color changes only with owner approval.
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPen, QPolygonF

import app_paths

FONTS_DIR = app_paths.resource_path("assets/fonts")
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

# Dark Persona 5 window surfaces (Notes, Tasks, Settings, Finance, Stats, Timer)
P5_BG = QColor("#0B0B10")
P5_CARD = QColor("#13131B")
P5_SURFACE = QColor("#1B1B26")
P5_HOVER = QColor("#252534")
P5_SELECTED = QColor("#2D0A11")
P5_BORDER = QColor("#2B2B3A")
P5_BORDER_HI = QColor("#4A4A62")
P5_TEXT = QColor("#F5F5FA")
P5_MUTED = QColor("#9595AA")
P5_GOLD_BG = QColor("#2D2308")
P5_GOLD_FG = QColor("#FAD02C")
P5_RED_BG = QColor("#3A0A12")
P5_RED_FG = QColor("#FF5E6B")
P5_MINT_BG = QColor("#102B20")
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
     (-224, 108), (-128, 92), (-158, 114), (-78, 92), (-12, 54)),
    ((-24, 40), (-36, 52), (-236, 112)),
    ((16, -2), (248, -56), (236, -6), (22, 26)),
    ((10, -14), (24, -6), (178, -126)),
    ((18, 22), (244, 84), (222, 108), (16, 46)),
    ((18, 34), (30, 26), (188, 112)),
)

BLACK_BURST_SHARDS: tuple[tuple[tuple[float, float], ...], ...] = (
    ((-14, 6), (-36, 18), (-182, -138)),
    ((-18, 2), (-244, -8), (-22, 20)),
    ((-16, 30), (-38, 44), (-192, 110)),
    ((12, -10), (30, -2), (144, -118)),
    ((20, 4), (238, 12), (20, 22)),
    ((12, 34), (32, 24), (128, 108)),
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


UI_SCALE_PRESETS = (85, 100, 115, 130)


def _sp(px: float, scale: float, lo: int = 1) -> int:
    return max(lo, int(round(px * scale)))


def menu_qss(scale: float = 1.0) -> str:
    """Context & SYSTEM popup menus scaled cleanly by `scale` (constant white text on hover)."""
    fs = _sp(15, scale, 11)
    pv = _sp(6, scale, 4)
    pr = _sp(28, scale, 18)
    pl = _sp(16, scale, 10)
    ind = _sp(13, scale, 10)
    return f"""
QMenu {{
    background-color: #0A0A0F;
    color: #FFFFFF;
    border: 2px solid #FFFFFF;
    padding: 5px 0px;
    font-family: "Barlow Condensed", "Arsenal", "Segoe UI", sans-serif;
    font-size: {fs}px;
    font-weight: bold;
}}
QMenu::item {{
    padding: {pv}px {pr}px {pv}px {pl}px;
    border-left: 4px solid transparent;
    color: #FFFFFF;
}}
QMenu::item:selected {{
    background-color: #E60012;
    color: #FFFFFF;
    border-left: 4px solid #FFFFFF;
}}
QMenu::item:disabled {{
    color: #848496;
}}
QMenu::separator {{
    height: 2px;
    background: #E60012;
    margin: 5px 10px;
}}
QMenu::indicator {{
    width: {ind}px;
    height: {ind}px;
    margin-left: 6px;
    border: 1.5px solid #FFFFFF;
    background: #0A0A0F;
}}
QMenu::indicator:checked {{
    background: #E60012;
    border: 1.5px solid #FFFFFF;
}}
"""


def dialog_qss(scale: float = 1.0) -> str:
    """Popup windows (Notes, Tasks, Settings, Finance, Stats, Timer) scaled cleanly by `scale`."""
    hd = _sp(18, scale, 14)
    sub = _sp(13, scale, 10)
    tip = _sp(12, scale, 10)
    sec = _sp(15, scale, 12)
    inp = _sp(14, scale, 11)
    tit = _sp(19, scale, 14)
    srch = _sp(13, scale, 10)
    padv = _sp(5, scale, 3)
    padh = _sp(10, scale, 7)
    sbw = _sp(7, scale, 5)
    spin_w = _sp(20, scale, 16)
    return f"""
QLabel {{
    color: #F5F5FA;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
}}
QLabel#heading {{
    color: #FFFFFF;
    font-family: "Anton", "Barlow Condensed", "Impact", sans-serif;
    font-size: {hd}px;
}}
QLabel#subhead {{
    color: #FF4B55;
    font-family: "Barlow Condensed", "Arsenal", sans-serif;
    font-size: {sub}px;
    font-weight: bold;
}}
QLabel#tip {{
    color: #9595AA;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
    font-size: {tip}px;
    font-weight: normal;
}}
QLabel#section {{
    color: #FF4B55;
    font-family: "Barlow Condensed", "Anton", sans-serif;
    font-size: {sec}px;
    font-weight: 800;
    letter-spacing: 0.4px;
}}
QFrame#card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #171722, stop:1 #111118);
    border: 1px solid #2D2D3D;
    border-top: 1px solid #38384C;
    border-radius: 9px;
}}
QFrame#subcard {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1F1F2D, stop:1 #161620);
    border: 1px solid #323244;
    border-top: 1px solid #3F3F55;
    border-radius: 8px;
}}
QFrame#toolbarPill {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #20202E, stop:1 #171722);
    border: 1px solid #323244;
    border-radius: 8px;
}}
QFrame#undoBar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2E0910, stop:1 #1E070C);
    border: 1.5px solid #E60012;
    border-radius: 8px;
}}
QProgressBar {{
    background-color: #181824;
    border: 1px solid #2A2A38;
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #D60010, stop:1 #FF3545);
    border-radius: 2px;
}}
QToolTip {{
    background-color: #09090D;
    color: #FFFFFF;
    border: 1.5px solid #E60012;
    padding: 4px 8px;
    font-family: "Arsenal", "Segoe UI", sans-serif;
    font-size: {tip}px;
}}
QLineEdit, QTextEdit, QDateTimeEdit, QDateEdit, QTimeEdit, QSpinBox, QComboBox {{
    background-color: #161620;
    color: #F5F5FA;
    border: 1.5px solid #323244;
    border-radius: 7px;
    selection-background-color: #E60012;
    selection-color: #FFFFFF;
    padding: {padv}px {padh}px;
    font-size: {inp}px;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
}}
QLineEdit:hover, QTextEdit:hover, QDateTimeEdit:hover, QDateEdit:hover, QTimeEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border: 1.5px solid #484860;
}}
QLineEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus, QComboBox:focus {{
    background-color: #111119;
    border: 1.5px solid #E60012;
}}
QSpinBox, QTimeEdit, QDateTimeEdit {{
    padding-right: {spin_w + 6}px;
}}
QSpinBox::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: {spin_w}px;
    background-color: #222230;
    border-left: 1px solid #353548;
    border-bottom: 1px solid #2C2C3C;
    border-top-right-radius: 6px;
}}
QSpinBox::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: {spin_w}px;
    background-color: #222230;
    border-left: 1px solid #353548;
    border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QTimeEdit::up-button:hover, QDateTimeEdit::up-button:hover,
QSpinBox::down-button:hover, QTimeEdit::down-button:hover, QDateTimeEdit::down-button:hover {{
    background-color: #E60012;
}}
QSpinBox::up-arrow, QTimeEdit::up-arrow, QDateTimeEdit::up-arrow,
QSpinBox::down-arrow, QTimeEdit::down-arrow, QDateTimeEdit::down-arrow,
QDateEdit::down-arrow {{
    image: none;
    border: none;
    width: 0px;
    height: 0px;
}}
QDateEdit::drop-down, QDateTimeEdit::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: {spin_w + 4}px;
    background-color: #222230;
    border-left: 1px solid #353548;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}}
QDateEdit::drop-down:hover, QDateTimeEdit::drop-down:hover {{
    background-color: #E60012;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    border: none;
    width: 0px;
    height: 0px;
}}
QComboBox QAbstractItemView {{
    background-color: #12121A;
    color: #F5F5FA;
    border: 1.5px solid #E60012;
    selection-background-color: #E60012;
    selection-color: #FFFFFF;
    outline: none;
}}
QLineEdit#titleInput {{
    background-color: transparent;
    border: 1.5px solid transparent;
    border-radius: 6px;
    font-family: "Arsenal", "Optima", "Segoe UI", sans-serif;
    font-size: {tit}px;
    font-weight: bold;
    color: #FFFFFF;
    padding: 3px 6px;
}}
QLineEdit#titleInput:hover {{
    background-color: #1D1D28;
}}
QLineEdit#titleInput:focus {{
    background-color: #161620;
    border: 1.5px solid #E60012;
}}
QLineEdit#searchInput {{
    background-color: #151520;
    border: 1.5px solid #2E2E3E;
    border-radius: 7px;
    padding: {padv}px {padh}px;
    font-size: {srch}px;
    color: #F5F5FA;
}}
QLineEdit#searchInput:focus {{
    background-color: #111119;
    border: 1.5px solid #E60012;
}}
QTextEdit#noteBody {{
    background-color: #101017;
    border: 1px solid #282836;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: {inp}px;
    color: #ECECF4;
}}
QTextEdit#noteBody:focus {{
    background-color: #0E0E15;
    border: 1px solid #5C1A24;
}}
QTextEdit#noteRaw {{
    background-color: #0E0E14;
    border: 1px solid #2D2D3E;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: {srch}px;
    color: #DCDCEB;
    font-family: "Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace;
    selection-background-color: #E60012;
    selection-color: #FFFFFF;
}}
QTextEdit#noteRaw:focus {{
    background-color: #0D0D13;
    border: 1px solid #FAD02C;
}}
QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: {sbw}px;
    margin: 4px 0px;
}}
QScrollBar::handle:vertical {{
    background: #3B3B4E;
    border-radius: 3px;
    min-height: 26px;
}}
QScrollBar::handle:vertical:hover {{
    background: #E60012;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QCalendarWidget QWidget {{
    background-color: #13131B;
    color: #FFFFFF;
    font-family: "Arsenal", "Segoe UI", sans-serif;
}}
QCalendarWidget QAbstractItemView:enabled {{
    color: #FFFFFF;
    background-color: #13131B;
    selection-background-color: #E60012;
    selection-color: #FFFFFF;
}}
QCalendarWidget QAbstractItemView:disabled {{
    color: #5C5C6E;
}}
QCalendarWidget QToolButton {{
    color: #FFFFFF;
    background-color: #20202D;
    border-radius: 4px;
    padding: 3px 6px;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: #1B1B26;
    border: 1px solid #323244;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #CC0010, stop:1 #FF2A3B);
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 2px solid #E60012;
    width: 14px;
    height: 14px;
    margin: -5px 0px;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: #FAD02C;
    border: 2px solid #FFFFFF;
}}
"""


MENU_QSS = menu_qss(1.0)
DIALOG_QSS = dialog_qss(1.0)
