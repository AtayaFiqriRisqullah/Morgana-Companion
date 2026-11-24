# -*- coding: utf-8 -*-
"""Persona 5 themed popup windows for Tasks, Notes, Events, Social Stats, Timer, and Settings.

Design principles:
  * Cohesive Persona 5 dark palette: Obsidian Black, Persona Crimson (#E60012),
    Crisp White, and subtle Morgana Scarf Gold (#F5C400).
  * Strict typography: heavy p5_font() for headers, badges & action buttons;
    clean upright ui_font() for note bodies, task titles, and form controls.
  * Restrained, critically-damped motion (smooth slide, hover tint, checkmark draw).
  * Hover states shift surface/background/border by default (text color changes only with owner approval).
  * Practical desktop UX: instant search, task priority, pinned notes, 1-click
    category chips, debounced autosave, keyboard shortcuts, and 5-second Undo bars.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (QDate, QDateTime, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                          QTime, QTimer, pyqtSignal)
from PyQt6.QtGui import (QColor, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF, QShortcut, QTextCursor)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDateTimeEdit, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
                             QSpinBox, QStyle, QStyledItemDelegate, QTextEdit, QTimeEdit,
                             QVBoxLayout, QWidget)

import sprites
from audio_manager import audio, play_ui, play_voice
from calendar_service import CalendarResult, events_by_day, human_delta, morgana_briefing
from lines import build_daily_briefing
from motion import FrameTicker, Spring, clamp
from storage_manager import (DAILY_BLOCK_TARGET, DEFAULT_STAT_POINTS, QUICK_STAT_POINTS,
                             RADIAL_TAB_KEYS, SETTINGS_DEFAULTS, SOCIAL_STAT_KEYS, SOCIAL_STATS,
                             STAT_MASTERY_STEP, STAT_RANK_THRESHOLDS, TIME_BLOCKS, HabitStore,
                             LocalEventStore, NoteStore, SettingsStore, SocialStatsStore,
                             TaskStore, default_time_block, overall_confidant_level, parse_dt,
                             stat_level, stat_level_progress, stat_pentagon_fraction,
                             stat_rank_title)
from theme import (BLACK, DEEP_RED, GOLD, LIGHT_RED, P5_BG, P5_BORDER, P5_BORDER_HI,
                   P5_CARD, P5_GOLD_BG, P5_GOLD_FG, P5_HOVER, P5_MINT_BG, P5_MINT_FG, P5_MUTED,
                   P5_RED_BG, P5_RED_FG, P5_SELECTED, P5_SURFACE, P5_TEXT, RED, RED_HOVER, WHITE,
                   dialog_qss, lerp_color, load_fonts, p5_font, pen, ui_font)

ROLE_ID = Qt.ItemDataRole.UserRole.value      # item id; None = empty-state hint row
ROLE_DONE = ROLE_ID + 1                       # bool: completed
ROLE_PREVIEW = ROLE_ID + 2                    # preview snippet (notes)
ROLE_BADGE = ROLE_ID + 3                      # status chip text ("IN 2H", "OVERDUE", "DONE")
ROLE_DATE = ROLE_ID + 4                       # date / time label
ROLE_CAT = ROLE_ID + 5                        # category / tone
ROLE_FLAG = ROLE_ID + 6                       # bool: high priority (tasks) or pinned (notes)
ROLE_OVERDUE = ROLE_ID + 7                    # bool: task deadline overdue
ROLE_STREAK = ROLE_ID + 8                     # str: habit streak badge text

NOTE_CATEGORIES = ("Personal", "Work", "Study")
_AVATAR_CACHE: dict[int, QPixmap] = {}


def _sc(val: float, scale: float, lo: int = 1) -> int:
    return max(lo, int(round(val * scale)))


def _to_qdt(dt: datetime) -> QDateTime:
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute))


def _from_qdt(q: QDateTime) -> datetime:
    d, t = q.date(), q.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())


def _fmt_time_12h(iso: str | None) -> str:
    dt = parse_dt(iso)
    if dt is None:
        return ""
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{h}:{dt.minute:02d} {ampm}"


def _fmt_short_date(iso: str | None) -> str:
    dt = parse_dt(iso)
    return f"{dt:%d %b}" if dt else ""


def _morgana_avatar(size: int = 30) -> QPixmap:
    """Circular Morgana portrait framed with a crisp white/crimson Persona 5 ring."""
    if size in _AVATAR_CACHE:
        return _AVATAR_CACHE[size]
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(1.5, 1.5, size - 3.0, size - 3.0))
    p.setClipPath(clip)
    p.fillRect(0, 0, size, size, RED)

    sprite = sprites.SPRITE_DIR / "b003_000_00.png"
    src = QPixmap(str(sprite)) if sprite.is_file() else QPixmap()
    if not src.isNull():
        crop = src.copy(QRect(120, 85, 485, 485))
        p.drawPixmap(QRect(1, 1, size - 2, size - 2), crop)
    else:
        p.setPen(WHITE)
        p.setFont(p5_font(max(12, int(size * 0.5)), italic=False))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter.value, "M")
    p.setClipping(False)
    p.setPen(QPen(WHITE, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(1.5, 1.5, size - 3.0, size - 3.0))
    p.end()
    _AVATAR_CACHE[size] = pm
    return pm


def _draw_icon(p: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    """Crisp minimal vector icons drawn cleanly with QPainter."""
    cx, cy = rect.center().x(), rect.center().y()
    r = min(rect.width(), rect.height()) * 0.36
    p.save()
    p.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "plus":
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    elif kind == "close":
        k = r * 0.85
        p.drawLine(QPointF(cx - k, cy - k), QPointF(cx + k, cy + k))
        p.drawLine(QPointF(cx + k, cy - k), QPointF(cx - k, cy + k))
    elif kind == "gear":
        p.drawEllipse(QPointF(cx, cy), r * 0.42, r * 0.42)
        p.drawEllipse(QPointF(cx, cy), r * 0.88, r * 0.88)
    elif kind == "sort":
        p.drawLine(QPointF(cx - r, cy - r * 0.6), QPointF(cx + r, cy - r * 0.6))
        p.drawLine(QPointF(cx - r * 0.65, cy), QPointF(cx + r * 0.65, cy))
        p.drawLine(QPointF(cx - r * 0.3, cy + r * 0.6), QPointF(cx + r * 0.3, cy + r * 0.6))
    elif kind == "clock":
        p.drawEllipse(QPointF(cx, cy), r * 0.9, r * 0.9)
        p.drawLine(QPointF(cx, cy - r * 0.48), QPointF(cx, cy))
        p.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.44, cy + r * 0.2))
    elif kind == "check":
        p.drawLine(QPointF(cx - r * 0.65, cy + r * 0.05), QPointF(cx - r * 0.14, cy + r * 0.56))
        p.drawLine(QPointF(cx - r * 0.14, cy + r * 0.56), QPointF(cx + r * 0.72, cy - r * 0.5))
    elif kind == "trash":
        p.drawLine(QPointF(cx - r * 0.8, cy - r * 0.52), QPointF(cx + r * 0.8, cy - r * 0.52))
        p.drawLine(QPointF(cx - r * 0.32, cy - r * 0.85), QPointF(cx + r * 0.32, cy - r * 0.85))
        p.drawRoundedRect(QRectF(cx - r * 0.56, cy - r * 0.52, r * 1.12, r * 1.42), 1.5, 1.5)
    elif kind == "pin":
        p.drawEllipse(QPointF(cx, cy - r * 0.35), r * 0.46, r * 0.46)
        p.drawLine(QPointF(cx, cy + r * 0.12), QPointF(cx, cy + r * 0.92))
    elif kind == "bolt":
        p.drawPolyline(QPolygonF([
            QPointF(cx + r * 0.15, cy - r * 0.9),
            QPointF(cx - r * 0.45, cy + r * 0.08),
            QPointF(cx + r * 0.05, cy + r * 0.08),
            QPointF(cx - r * 0.18, cy + r * 0.92),
        ]))
    elif kind == "refresh":
        arc = QRectF(cx - r * 0.85, cy - r * 0.85, r * 1.7, r * 1.7)
        p.drawArc(arc, 35 * 16, 275 * 16)
        p.drawPolyline(QPolygonF([
            QPointF(cx + r * 0.42, cy - r * 0.82),
            QPointF(cx + r * 0.82, cy - r * 0.48),
            QPointF(cx + r * 0.34, cy - r * 0.32),
        ]))
    elif kind == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([
            QPointF(cx - r * 0.55, cy - r * 0.78),
            QPointF(cx + r * 0.82, cy),
            QPointF(cx - r * 0.55, cy + r * 0.78),
        ]))
    elif kind == "stop":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(cx - r * 0.65, cy - r * 0.65, r * 1.3, r * 1.3), 1.8, 1.8)
    p.restore()


def _fmt_hms(seconds: int) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------------
# P5FluidButton: Persona 5 dark/crimson button; hover never changes text color
# ---------------------------------------------------------------------------
class P5FluidButton(QPushButton):
    # variant -> (bg_idle, bg_hover, border_idle, border_hover, fg_fixed)
    VARIANTS = {
        "primary": (RED, RED_HOVER, WHITE, WHITE, WHITE),
        "soft":    (P5_SURFACE, P5_HOVER, P5_BORDER, P5_BORDER_HI, P5_TEXT),
        "ghost":   (QColor(37, 37, 48, 0), P5_HOVER, QColor(0, 0, 0, 0), P5_BORDER, P5_TEXT),
        "gold":    (P5_GOLD_BG, QColor("#3D300C"), QColor("#6C5514"), GOLD, P5_GOLD_FG),
        "danger":  (P5_RED_BG, QColor("#4D1018"), QColor("#731823"), LIGHT_RED, P5_RED_FG),
        "mint":    (P5_MINT_BG, QColor("#183C2D"), QColor("#245943"), P5_MINT_FG, P5_MINT_FG),
    }

    def __init__(self, text: str = "", variant: str = "soft", icon: str = "",
                 display: bool = False, px: int = 12, radius: float = 6.0,
                 scale: float = 1.0, parent: QWidget | None = None,
                 sfx_hover: bool = True, sfx_click: str | None = None) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._base_variant = variant
        self._icon = icon
        self._radius = radius
        self._scale = max(0.75, min(1.5, float(scale)))
        self._active = False
        self._sfx_hover = bool(sfx_hover)
        self._sfx_click = sfx_click      # None = auto ('cancel' for danger/trash/stop else 'select'); "" = silent
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fpx = _sc((px + 2) if display else (px + 1), self._scale, 9)
        self.setFont(p5_font(fpx, italic=False) if display else ui_font(fpx, bold=True))
        self._hover = Spring(0.0, 380.0)
        self._press = Spring(0.0, 640.0)
        self._ticker = FrameTicker(self._frame, self)

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self.update()

    def set_active(self, on: bool, active_variant: str = "primary") -> None:
        self._active = on
        self._variant = active_variant if on else self._base_variant
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self.text()) if self.text() else 0
        sc = self._scale
        iw = _sc(15, sc, 11) if self._icon else 0
        gap = _sc(6, sc, 4) if (self._icon and self.text()) else 0
        pad = _sc(22, sc, 16) if self.text() else _sc(14, sc, 10)
        h = _sc(30, sc, 24)
        return QSize(max(h, tw + iw + gap + pad), h)

    def enterEvent(self, e) -> None:
        if self.isEnabled() and self._sfx_hover:
            play_ui("ui_hover")
        self._hover.target = 1.0
        self._ticker.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover.target = 0.0
        self._ticker.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            if self._sfx_click is None:
                cue = "cancel" if (self._variant == "danger" or self._icon in ("trash", "stop")) else "select"
                play_ui(cue)
            elif self._sfx_click:
                play_ui(self._sfx_click)
            self._press.target = 1.0
            self._ticker.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._press.target = 0.0
        self._ticker.start()
        super().mouseReleaseEvent(e)

    def _frame(self, dt: float) -> bool:
        moving = self._hover.step(dt) | self._press.step(dt)
        self.update()
        return moving

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bg0, bg1, bd0, bd1, fg = self.VARIANTS.get(self._variant, self.VARIANTS["soft"])
        hov = clamp(self._hover.value)
        prs = clamp(self._press.value)

        bg = lerp_color(bg0, bg1, hov)
        bg.setAlpha(int(bg0.alpha() + (bg1.alpha() - bg0.alpha()) * hov))
        bd = lerp_color(bd0, bd1, hov)
        bd.setAlpha(int(bd0.alpha() + (bd1.alpha() - bd0.alpha()) * hov))

        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if prs > 0.01:
            s = 1.0 - 0.03 * prs
            c = r.center()
            p.translate(c)
            p.scale(s, s)
            p.translate(-c)
        if not self.isEnabled():
            p.setOpacity(0.4)

        p.setPen(QPen(bd, 1.4) if bd.alpha() > 4 else Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, self._radius, self._radius)

        # Text and icon keep the exact same color on hover
        txt = self.text()
        p.setFont(self.font())
        p.setPen(fg)
        if self._icon and txt:
            fm = QFontMetrics(self.font())
            tw = fm.horizontalAdvance(txt)
            ic = 15.0 * self._scale
            gap = 5.0 * self._scale
            total = ic + gap + tw
            x0 = r.center().x() - total / 2.0
            _draw_icon(p, self._icon, QRectF(x0, r.center().y() - ic / 2.0, ic, ic), fg)
            p.drawText(QRectF(x0 + ic + gap, r.top(), tw + 4, r.height()),
                       Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, txt)
        elif self._icon:
            ic = 17.0 * self._scale
            _draw_icon(p, self._icon, QRectF(r.center().x() - ic / 2.0, r.center().y() - ic / 2.0, ic, ic), fg)
        else:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter.value, txt)


class P5CheckBox(QCheckBox):
    """Crisp Persona 5 dark checkbox with crimson fill and painted white checkmark."""

    def __init__(self, text: str, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._scale = max(0.75, min(1.5, float(scale)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(_sc(14, self._scale, 11), bold=True))
        self.clicked.connect(lambda checked: play_ui("select" if checked else "cancel"))

    def enterEvent(self, e) -> None:
        if self.isEnabled():
            play_ui("ui_hover")
        super().enterEvent(e)

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        return QSize(_sc(28, self._scale, 22) + fm.horizontalAdvance(self.text()) + 8, _sc(26, self._scale, 22))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        sc = self._scale
        bs = 18.0 * sc
        box = QRectF(1.0, (self.height() - bs) / 2.0, bs, bs)
        if self.isChecked():
            p.setPen(QPen(WHITE, 1.5))
            p.setBrush(RED)
            p.drawRoundedRect(box, 4.0, 4.0)
            c = box.center()
            p.setPen(QPen(WHITE, 2.2 * sc, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QPointF(c.x() - 4.4 * sc, c.y() + 0.4 * sc), QPointF(c.x() - 1.2 * sc, c.y() + 3.6 * sc))
            p.drawLine(QPointF(c.x() - 1.2 * sc, c.y() + 3.6 * sc), QPointF(c.x() + 4.8 * sc, c.y() - 3.8 * sc))
        else:
            p.setPen(QPen(QColor("#8E8EA0"), 1.6))
            p.setBrush(P5_SURFACE)
            p.drawRoundedRect(box, 4.0, 4.0)
        tx = bs + 9.0 * sc
        p.setFont(self.font())
        p.setPen(P5_TEXT)
        p.drawText(QRectF(tx, 0.0, self.width() - tx, self.height()),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                   self.text())


# ---------------------------------------------------------------------------
# P5FluidPanel: obsidian + white-bordered Persona 5 frame with Windows-safe reveal
# ---------------------------------------------------------------------------
class P5FluidPanel(QWidget):
    closed = pyqtSignal()
    M, SHADOW, HEADER, SLIDE = 10, 6, 48, 12

    def __init__(self, title: str, size: QSize, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__()
        load_fonts()
        sc = max(0.75, min(1.5, float(ui_scale)))
        self._ui_scale = sc
        self._header_h = _sc(self.HEADER, sc, 38)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(_sc(size.width(), sc, 380), _sc(size.height(), sc, 420))
        self.setStyleSheet(dialog_qss(sc))
        self._title = title
        self._on_top = bool(on_top)
        self._drag_offset: QPoint | None = None

        self._open = Spring(1.0, 340.0)
        self._closing = False
        self._base_pos = QPoint()
        self._ticker = FrameTicker(self._frame, self)

        m = self.M
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(m + 2, m + 2, m + self.SHADOW + 2, m + self.SHADOW + 2)
        self.outer.setSpacing(0)

        # Integrated Persona 5 header bar (subclasses can add quick buttons to self.header_bar)
        header_w = QWidget()
        header_w.setFixedHeight(self._header_h)
        self.header_bar = QHBoxLayout(header_w)
        self.header_bar.setContentsMargins(_sc(14, sc, 10), _sc(6, sc, 4), _sc(10, sc, 8), _sc(6, sc, 4))
        self.header_bar.setSpacing(_sc(8, sc, 5))
        av = QLabel()
        av.setPixmap(_morgana_avatar(_sc(30, sc, 24)))
        self.header_bar.addWidget(av)
        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("heading")
        self._title_lbl.setStyleSheet(
            f'color: #FFFFFF; background: transparent; font-family: "Anton", "Barlow Condensed", "Impact", sans-serif; font-size: {_sc(22, sc, 16)}px;'
        )
        self.header_bar.addWidget(self._title_lbl)
        self.header_bar.addStretch(1)
        self.outer.addWidget(header_w)

        # Main content area
        content_w = QWidget()
        self.body = QVBoxLayout(content_w)
        self.body.setContentsMargins(_sc(14, sc, 10), _sc(12, sc, 8), _sc(14, sc, 10), _sc(14, sc, 10))
        self.body.setSpacing(_sc(10, sc, 7))
        self.outer.addWidget(content_w, 1)

    def _btn(self, text: str = "", variant: str = "soft", icon: str = "",
             display: bool = False, px: int = 12, radius: float = 6.0,
             sfx_hover: bool = True, sfx_click: str | None = None) -> P5FluidButton:
        return P5FluidButton(text, variant=variant, icon=icon, display=display,
                             px=px, radius=radius, scale=self._ui_scale,
                             sfx_hover=sfx_hover, sfx_click=sfx_click)

    def _add_close_button(self) -> None:
        sz = _sc(30, self._ui_scale, 24)
        close_btn = self._btn("", variant="ghost", icon="close", radius=6.0, sfx_click="")
        close_btn.setFixedSize(sz, sz)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.close_animated)
        self.header_bar.addWidget(close_btn)

    def set_on_top(self, on: bool) -> None:
        on = bool(on)
        if on == self._on_top:
            return
        self._on_top = on
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
            QTimer.singleShot(30, self._bring_to_front)

    def _bring_to_front(self) -> None:
        if self.isVisible() and not self._closing:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.raise_()
            self.activateWindow()

    def _frame(self, dt: float) -> bool:
        moving = self._open.step(dt)
        o = clamp(self._open.value)
        self.move(self._base_pos.x(), self._base_pos.y() + int(round((1.0 - o) * self.SLIDE)))
        self.update()
        if self._closing and o < 0.05:
            self._closing = False
            self._open.snap(0.0)
            QWidget.hide(self)
            self.closed.emit()
            return False
        return moving

    def close_animated(self) -> None:
        if not self.isVisible() or self._closing:
            return
        play_ui("close")
        self.on_closing()
        self._closing = True
        self._open.configure(420.0)
        self._open.target = 0.0
        self._ticker.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        M, S, H = self.M, self.SHADOW, self._header_h
        o = clamp(self._open.value)
        body = QRectF(M, M, self.width() - 2 * M - S, self.height() - 2 * M - S)

        # Signature Persona 5 offset crimson shadow + obsidian frame
        p.setOpacity(max(0.2, o))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(RED)
        p.drawRect(body.translated(S * o, S * o))

        p.setBrush(P5_BG)
        p.drawRect(body)

        # Slanted crimson title slash in top-left header
        cut = min(body.width() * 0.38, 195.0 * self._ui_scale) * o
        if cut > 20:
            p.setBrush(RED)
            p.drawPolygon(QPolygonF([
                QPointF(M, M),
                QPointF(M + cut, M),
                QPointF(M + cut - 20.0, M + H),
                QPointF(M, M + H),
            ]))

        # Crisp crimson divider under header + white Persona 5 outer border
        p.setPen(pen(RED, 2.0))
        p.drawLine(QPointF(M, M + H), QPointF(M + body.width(), M + H))
        p.setPen(pen(WHITE, 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(body)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= self.M + self._header_h:
            self._drag_offset = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_offset is not None:
            target = e.globalPosition().toPoint() - self._drag_offset
            self._base_pos = QPoint(target.x(), target.y() - int(round((1.0 - clamp(self._open.value)) * self.SLIDE)))
            self.move(target)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_offset = None

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_animated()
        else:
            super().keyPressEvent(e)

    def refresh(self) -> None:
        """Called whenever the panel is shown."""

    def on_shown(self) -> None:
        """Called after showing & raising."""

    def on_closing(self) -> None:
        """Called right before closing."""

    def closeEvent(self, e) -> None:
        # Handle OS/Alt+F4 close identically to clicking X so cleanup & signals always run
        self._ticker.stop()
        self._closing = False
        self.on_closing()
        e.accept()
        QWidget.hide(self)
        self.closed.emit()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()

    def show_near(self, anchor: QRect, prefer_center: bool = False) -> None:
        """Open cleanly near `anchor` (or centered over `anchor` when `prefer_center=True`).
        Uses deferred raise+activate so Windows QMenu/Tool transitions never bury the window."""
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w = min(self.width(), max(440, area.width() - 28))
        h = min(self.height(), max(440, area.height() - 28))
        if w != self.width() or h != self.height():
            self.setFixedSize(w, h)

        if not self.isVisible() or prefer_center:
            if prefer_center:
                x = max(area.left(), min(anchor.center().x() - w // 2, area.right() - w))
                y = max(area.top(), min(anchor.center().y() - h // 2, area.bottom() - h))
            else:
                x = anchor.left() - w - 10
                if x < area.left():
                    x = min(anchor.right() + 10, area.right() - w)
                y = max(area.top(), min(anchor.center().y() - h // 2, area.bottom() - h))
            self._base_pos = QPoint(x, y)

        self._closing = False
        self.setWindowOpacity(1.0)
        if not self.isVisible():
            play_ui("open_window")
            self._open.snap(0.35)
            self.move(self._base_pos.x(), self._base_pos.y() + int(round(0.65 * self.SLIDE)))
            self.show()
        else:
            self.move(self._base_pos)
            self.refresh()
        self._open.configure(340.0)
        self._open.target = 1.0
        self._ticker.start()
        self._bring_to_front()
        QTimer.singleShot(0, self._bring_to_front)
        QTimer.singleShot(50, self._bring_to_front)
        self.on_shown()


# ---------------------------------------------------------------------------
# UndoBar: non-blocking 5-second undo strip for Tasks & Notes
# ---------------------------------------------------------------------------
class UndoBar(QFrame):
    undo_clicked = pyqtSignal()

    def __init__(self, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("undoBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(_sc(12, scale, 8), _sc(6, scale, 4), _sc(8, scale, 6), _sc(6, scale, 4))
        lay.setSpacing(_sc(8, scale, 6))
        self._lbl = QLabel("")
        self._lbl.setStyleSheet(f"color: #FFFFFF; font-size: {_sc(13, scale, 10)}px; font-weight: bold;")
        lay.addWidget(self._lbl, 1)
        btn = P5FluidButton("UNDO", variant="primary", display=True, px=11, radius=5.0, scale=scale)
        btn.setFixedHeight(_sc(26, scale, 22))
        btn.clicked.connect(self._on_undo)
        lay.addWidget(btn)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def trigger(self, message: str) -> None:
        self._lbl.setText(message)
        self.show()
        self._timer.start()

    def dismiss(self) -> None:
        self._timer.stop()
        self.hide()

    def _on_undo(self) -> None:
        self._timer.stop()
        self.hide()
        self.undo_clicked.emit()


# ---------------------------------------------------------------------------
# TaskRowDelegate: Persona 5 dark task cards with priority bar & spring checkbox
# ---------------------------------------------------------------------------
class TaskRowDelegate(QStyledItemDelegate):
    CHECK_POINTS = ((-4.8, 0.4), (-1.4, 3.8), (5.2, -4.2))

    def __init__(self, view: QListWidget, scale: float = 1.0) -> None:
        super().__init__(view)
        self._sc = max(0.75, min(1.5, float(scale)))
        self._springs: dict[str, Spring] = {}
        self._swallow_release = False
        self._title_font = ui_font(_sc(15, self._sc, 11), bold=True)
        self._done_font = ui_font(_sc(15, self._sc, 11), bold=True)
        self._done_font.setStrikeOut(True)
        self._pill_font = p5_font(_sc(11, self._sc, 9), italic=False)
        self._time_font = ui_font(_sc(11, self._sc, 9), bold=False)
        self._hint_font = ui_font(_sc(13, self._sc, 11), bold=False)
        self._ticker = FrameTicker(self._frame, self)

    def start_check_anim(self, item_id: str) -> None:
        sp = Spring(0.0, 420.0)
        sp.target = 1.0
        self._springs[item_id] = sp
        self._ticker.start()

    def clear_anim(self, item_id: str) -> None:
        self._springs.pop(item_id, None)

    def _frame(self, dt: float) -> bool:
        for k in list(self._springs):
            if not self._springs[k].step(dt):
                del self._springs[k]
        view = self.parent()
        if view is not None:
            view.viewport().update()
        return bool(self._springs)

    def _box_rect(self, card: QRectF) -> QRectF:
        bs = 20.0 * self._sc
        return QRectF(card.left() + 14.0 * self._sc, card.top() + 12.0 * self._sc, bs, bs)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(120, _sc(74, self._sc, 56))
        return QSize(120, _sc(62, self._sc, 50))

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect.adjusted(_sc(46, self._sc, 36), _sc(7, self._sc, 4), -10, -_sc(24, self._sc, 18)))

    def _paint_checkbox(self, p: QPainter, box: QRectF, done: bool, prog: float) -> None:
        p.save()
        if done:
            p.setPen(QPen(WHITE, 1.5))
            p.setBrush(RED)
            p.drawRoundedRect(box, 4.0, 4.0)
            if prog > 0.01:
                c = box.center()
                pts = self.CHECK_POINTS
                seg1 = ((pts[1][0] - pts[0][0]) ** 2 + (pts[1][1] - pts[0][1]) ** 2) ** 0.5
                seg2 = ((pts[2][0] - pts[1][0]) ** 2 + (pts[2][1] - pts[1][1]) ** 2) ** 0.5
                reach = clamp(prog) * (seg1 + seg2)
                path = QPainterPath()
                path.moveTo(c.x() + pts[0][0], c.y() + pts[0][1])
                if reach <= seg1:
                    f = reach / seg1
                    path.lineTo(c.x() + pts[0][0] + (pts[1][0] - pts[0][0]) * f,
                                c.y() + pts[0][1] + (pts[1][1] - pts[0][1]) * f)
                else:
                    f = (reach - seg1) / seg2
                    path.lineTo(c.x() + pts[1][0], c.y() + pts[1][1])
                    path.lineTo(c.x() + pts[1][0] + (pts[2][0] - pts[1][0]) * f,
                                c.y() + pts[1][1] + (pts[2][1] - pts[1][1]) * f)
                p.setPen(QPen(WHITE, 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        else:
            p.setPen(QPen(QColor("#8E8EA0"), 1.6))
            p.setBrush(P5_BG)
            p.drawRoundedRect(box, 4.0, 4.0)
        p.restore()

    def paint(self, painter, option, index) -> None:
        r = QRectF(option.rect)
        item_id = index.data(ROLE_ID)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if item_id is None:
            painter.setPen(P5_MUTED)
            painter.setFont(self._hint_font)
            painter.drawText(r.adjusted(14, 6, -14, -6),
                             Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                             index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        card = r.adjusted(2, 3, -4, -3)
        done = bool(index.data(ROLE_DONE))
        high = bool(index.data(ROLE_FLAG))
        overdue = bool(index.data(ROLE_OVERDUE)) and not done
        due_txt = index.data(ROLE_BADGE) or ""
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            bg, border = P5_SELECTED, RED
        elif overdue:
            bg, border = (P5_HOVER if hovered else QColor("#210C10")), RED
        elif hovered:
            bg, border = P5_HOVER, P5_BORDER_HI
        else:
            bg, border = P5_SURFACE, P5_BORDER

        painter.setPen(QPen(border, 1.3 if (selected or overdue) else 1.2))
        painter.setBrush(bg)
        painter.drawRoundedRect(card, 8.0, 8.0)

        # Left accent bar for overdue, high priority, or selection
        if (high or overdue) and not done:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(RED)
            painter.drawRoundedRect(QRectF(card.left(), card.top() + 5, 3.5, card.height() - 10), 1.5, 1.5)
        elif selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(WHITE)
            painter.drawRoundedRect(QRectF(card.left(), card.top() + 6, 3.0, card.height() - 12), 1.5, 1.5)

        box = self._box_rect(card)
        sp = self._springs.get(item_id)
        self._paint_checkbox(painter, box, done, sp.value if sp else 1.0)

        tx = box.right() + 12
        tw = max(40.0, card.right() - tx - 10)
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        font = self._done_font if done else self._title_font
        fm = QFontMetrics(font)
        painter.setFont(font)
        # Constant text color on hover/selection
        painter.setPen(P5_MUTED if done else WHITE)
        painter.drawText(QPointF(tx, card.top() + 10 + fm.ascent()),
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, int(tw)))

        # Second line: optional HIGH chip + status chip + optional DUE/OVERDUE chip + creation time
        sc = self._sc
        ph = 16.0 * sc
        py = card.top() + 33.0 * sc
        x_cur = tx
        pfm = QFontMetrics(self._pill_font)
        if high and not done:
            pw = pfm.horizontalAdvance("HIGH") + 14.0 * sc
            pill = QRectF(x_cur, py, pw, ph)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(P5_RED_BG)
            painter.drawRoundedRect(pill, 4.0, 4.0)
            painter.setFont(self._pill_font)
            painter.setPen(P5_RED_FG)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, "HIGH")
            x_cur = pill.right() + 6

        if due_txt and not done:
            dw = pfm.horizontalAdvance(due_txt) + 14.0 * sc
            dpill = QRectF(x_cur, py, dw, ph)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(RED if overdue else P5_GOLD_BG)
            painter.drawRoundedRect(dpill, 4.0, 4.0)
            painter.setFont(self._pill_font)
            painter.setPen(WHITE if overdue else P5_GOLD_FG)
            painter.drawText(dpill, Qt.AlignmentFlag.AlignCenter.value, due_txt)
            x_cur = dpill.right() + 6
        else:
            st_txt = "DONE" if done else "PENDING"
            sw = pfm.horizontalAdvance(st_txt) + 14.0 * sc
            spill = QRectF(x_cur, py, sw, ph)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(P5_MINT_BG if done else P5_GOLD_BG)
            painter.drawRoundedRect(spill, 4.0, 4.0)
            painter.setFont(self._pill_font)
            painter.setPen(P5_MINT_FG if done else P5_GOLD_FG)
            painter.drawText(spill, Qt.AlignmentFlag.AlignCenter.value, st_txt)
            x_cur = spill.right() + 8

        time_txt = index.data(ROLE_DATE) or ""
        if time_txt and card.right() - x_cur > 36:
            painter.setFont(self._time_font)
            painter.setPen(P5_MUTED)
            painter.drawText(QRectF(x_cur, py, max(0.0, card.right() - x_cur - 8), ph),
                             Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                             time_txt)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if index.data(ROLE_ID) is None:
            return False
        et = event.type()
        if et in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick) \
                and event.button() == Qt.MouseButton.LeftButton:
            card = QRectF(option.rect).adjusted(2, 3, -4, -3)
            hit = self._box_rect(card).adjusted(-8, -8, 8, 8)
            if hit.contains(event.position()):
                if et == QEvent.Type.MouseButtonDblClick:
                    self._swallow_release = True
                    return True
                if self._swallow_release:
                    self._swallow_release = False
                    return True
                model.setData(index, not bool(index.data(ROLE_DONE)), ROLE_DONE)
                return True
        return super().editorEvent(event, model, option, index)


# ---------------------------------------------------------------------------
# NoteCardDelegate: Persona 5 dark note cards with pin & reminder badges
# ---------------------------------------------------------------------------
class NoteCardDelegate(QStyledItemDelegate):
    def __init__(self, view: QListWidget, scale: float = 1.0) -> None:
        super().__init__(view)
        self._sc = max(0.75, min(1.5, float(scale)))
        self._title_font = ui_font(_sc(15, self._sc, 11), bold=True)
        self._done_font = ui_font(_sc(15, self._sc, 11), bold=True)
        self._done_font.setStrikeOut(True)
        self._preview_font = ui_font(_sc(12, self._sc, 10), bold=False)
        self._chip_font = p5_font(_sc(11, self._sc, 9), italic=False)
        self._date_font = ui_font(_sc(11, self._sc, 9), bold=False)
        self._hint_font = ui_font(_sc(13, self._sc, 11), bold=False)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(180, _sc(90, self._sc, 70))
        return QSize(180, _sc(96, self._sc, 78))

    def paint(self, painter, option, index) -> None:
        r = QRectF(option.rect)
        item_id = index.data(ROLE_ID)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if item_id is None:
            painter.setPen(P5_MUTED)
            painter.setFont(self._hint_font)
            painter.drawText(r.adjusted(14, 10, -14, -10),
                             Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                             index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        card = r.adjusted(2, 3, -4, -3)
        done = bool(index.data(ROLE_DONE))
        pinned = bool(index.data(ROLE_FLAG))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            bg, border = P5_SELECTED, RED
        elif hovered:
            bg, border = P5_HOVER, P5_BORDER_HI
        else:
            bg, border = P5_SURFACE, P5_BORDER

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(bg)
        painter.drawRoundedRect(card, 8.0, 8.0)

        # Left stripe: Crimson when selected, Morgana Gold when pinned
        if selected or pinned:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(RED if selected else GOLD)
            painter.drawRoundedRect(QRectF(card.left(), card.top() + 5, 3.5, card.height() - 10), 1.5, 1.5)

        tx = card.left() + 12
        tw = max(40.0, card.right() - tx - 10)

        # Title + optional gold pin dot (text color never changes on hover)
        title = index.data(Qt.ItemDataRole.DisplayRole) or "(Untitled)"
        tf = self._done_font if done else self._title_font
        tfm = QFontMetrics(tf)
        painter.setFont(tf)
        painter.setPen(P5_MUTED if done else WHITE)
        title_w = int(tw - (16 if pinned else 0))
        painter.drawText(QPointF(tx, card.top() + 9 + tfm.ascent()),
                         tfm.elidedText(title, Qt.TextElideMode.ElideRight, title_w))
        if pinned:
            _draw_icon(painter, "pin", QRectF(card.right() - 22, card.top() + 7, 15, 15), GOLD)

        # Strictly 2-line preview with clean ellipsis
        preview = index.data(ROLE_PREVIEW) or ""
        painter.setFont(self._preview_font)
        painter.setPen(P5_MUTED)
        pfm = QFontMetrics(self._preview_font)
        max_w = int(tw)
        words = preview.split(" ")
        line1, rest = "", []
        for i_w, word in enumerate(words):
            trial = f"{line1} {word}" if line1 else word
            if line1 and pfm.horizontalAdvance(trial) > max_w:
                rest = words[i_w:]
                break
            line1 = trial
        y_p = card.top() + 28.0 * self._sc + pfm.ascent()
        painter.drawText(QPointF(tx, y_p), pfm.elidedText(line1, Qt.TextElideMode.ElideRight, max_w))
        if rest:
            painter.drawText(QPointF(tx, y_p + pfm.height() + 1),
                             pfm.elidedText(" ".join(rest), Qt.TextElideMode.ElideRight, max_w))

        # Bottom strip: category chip + status badge + date
        sc = self._sc
        ch = 16.0 * sc
        by = card.bottom() - ch - 5.0
        x_cur = tx
        cfm = QFontMetrics(self._chip_font)
        cat = (index.data(ROLE_CAT) or "Personal").upper()
        cw = cfm.horizontalAdvance(cat) + 12.0 * sc
        cpill = QRectF(x_cur, by, cw, ch)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#242430"))
        painter.drawRoundedRect(cpill, 4.0, 4.0)
        painter.setFont(self._chip_font)
        painter.setPen(QColor("#C8C8D6"))
        painter.drawText(cpill, Qt.AlignmentFlag.AlignCenter.value, cat)
        x_cur = cpill.right() + 5

        badge = index.data(ROLE_BADGE) or ""
        if badge:
            bw = cfm.horizontalAdvance(badge) + 12.0 * sc
            bpill = QRectF(x_cur, by, bw, ch)
            if badge == "OVERDUE":
                bbg, bfg = P5_RED_BG, P5_RED_FG
            elif badge == "DONE":
                bbg, bfg = P5_MINT_BG, P5_MINT_FG
            else:
                bbg, bfg = P5_GOLD_BG, P5_GOLD_FG
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bbg)
            painter.drawRoundedRect(bpill, 4.0, 4.0)
            painter.setFont(self._chip_font)
            painter.setPen(bfg)
            painter.drawText(bpill, Qt.AlignmentFlag.AlignCenter.value, badge)
            x_cur = bpill.right() + 6

        date_txt = index.data(ROLE_DATE) or ""
        if date_txt:
            painter.setFont(self._date_font)
            painter.setPen(QColor("#7C7C90"))
            painter.drawText(QRectF(x_cur, by, max(0.0, card.right() - x_cur - 8), ch),
                             Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignRight.value,
                             date_txt)
        painter.restore()


class _KeyAwareList(QListWidget):
    delete_pressed = pyqtSignal()
    space_pressed = pyqtSignal()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
            return
        if e.key() == Qt.Key.Key_Space:
            self.space_pressed.emit()
            return
        super().keyPressEvent(e)


def _hint_item(text: str) -> QListWidgetItem:
    item = QListWidgetItem(text)
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    return item


def _make_card(object_name: str = "card") -> QFrame:
    f = QFrame()
    f.setObjectName(object_name)
    return f


# ---------------------------------------------------------------------------
# TASKS DIALOG
# ---------------------------------------------------------------------------
class TasksDialog(P5FluidPanel):
    FILTERS = (("ALL", "all"), ("PENDING", "todo"), ("DUE", "due"), ("HIGH", "high"), ("DONE", "done"))
    open_settings = pyqtSignal()
    open_notes = pyqtSignal()
    tasks_changed = pyqtSignal()
    task_completed = pyqtSignal(str, int)
    task_reward_eligible = pyqtSignal(str)    # emits task_id when marked done

    def __init__(self, store: TaskStore, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("TASKS", QSize(535, 635), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self._filter = "all"
        self._new_high = False
        self._new_due_on = False
        self._undo_items: list[dict] = []

        # Header quick actions
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("subhead")
        self.header_bar.addWidget(self._count_lbl)

        self._notes_btn = self._btn("NOTES", variant="soft", display=True, px=10)
        self._notes_btn.clicked.connect(lambda _c=False: self.open_notes.emit())
        self.header_bar.addWidget(self._notes_btn)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.body.addWidget(self.progress)

        # Quick-add bar + High-priority + Deadline toggle
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a task and press Enter...")
        self.input.returnPressed.connect(self._add)
        add_row.addWidget(self.input, 1)

        self._due_btn = self._btn("DUE", variant="soft", icon="clock", display=True, px=10)
        self._due_btn.setToolTip("Set a deadline for the new task or selected task(s)")
        self._due_btn.clicked.connect(self._toggle_due_bar)
        add_row.addWidget(self._due_btn)

        self._prio_btn = self._btn("HIGH", variant="soft", icon="bolt", display=True, px=10)
        self._prio_btn.setToolTip("Mark new task as high priority")
        self._prio_btn.clicked.connect(self._toggle_new_priority)
        add_row.addWidget(self._prio_btn)

        add_btn = self._btn("ADD", variant="primary", icon="plus", display=True, px=11)
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        self.body.addLayout(add_row)

        # Collapsible inline deadline card (applies to new task OR selected task(s))
        self._due_box = _make_card("subcard")
        dl = QVBoxLayout(self._due_box)
        dl.setContentsMargins(10, 8, 10, 8)
        dl.setSpacing(6)

        dr1 = QHBoxLayout()
        dr1.setSpacing(6)
        self._due_check = P5CheckBox("Attach to new task", scale=sc)
        self._due_check.toggled.connect(self._on_due_check_toggled)
        dr1.addWidget(self._due_check)

        self._due_edit = QDateTimeEdit()
        self._due_edit.setCalendarPopup(True)
        self._due_edit.setDisplayFormat("dd MMM yyyy HH:mm")
        self._due_edit.setDateTime(_to_qdt(self._default_due()))
        self._due_edit.setMinimumHeight(_sc(28, sc, 24))
        self._due_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        dr1.addWidget(self._due_edit, 1)

        apply_sel = self._btn("SET SELECTED", variant="gold", display=True, px=9)
        apply_sel.setFixedHeight(_sc(26, sc, 22))
        apply_sel.setToolTip("Apply this deadline to currently selected task(s)")
        apply_sel.clicked.connect(self._apply_due_to_selected)
        dr1.addWidget(apply_sel)

        clear_sel = self._btn("CLEAR", variant="soft", display=True, px=9)
        clear_sel.setFixedHeight(_sc(26, sc, 22))
        clear_sel.setToolTip("Clear deadline on selected task(s) and new task")
        clear_sel.clicked.connect(self._clear_due_selected)
        dr1.addWidget(clear_sel)
        dl.addLayout(dr1)

        dr2 = QHBoxLayout()
        dr2.setSpacing(_sc(5, sc, 4))
        for text, kind in (("+1h", "1h"), ("+3h", "3h"), ("23:59", "eod"), ("Tomorrow 9:00", "tomorrow")):
            pb = self._btn(text, variant="gold" if kind == "eod" else "soft", px=10)
            pb.setFixedHeight(_sc(24, sc, 21))
            if kind == "eod":
                pb.setToolTip("Set deadline to 23:59 today (or tomorrow if past 23:59)")
            pb.clicked.connect(lambda _c=False, k=kind: self._apply_due_preset(k))
            dr2.addWidget(pb)
        dr2.addStretch(1)
        dl.addLayout(dr2)
        self._due_box.setVisible(False)
        self.body.addWidget(self._due_box)

        # Filter chips + compact inline search
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self._chips: dict[str, P5FluidButton] = {}
        for label, key in self.FILTERS:
            chip = self._btn(label, variant="soft", display=True, px=9)
            chip.setFixedHeight(_sc(26, sc, 22))
            chip.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self._chips[key] = chip
            bar.addWidget(chip)
        self._chips["all"].set_active(True)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(_sc(138, sc, 100))
        self.search_edit.textChanged.connect(self.refresh)
        bar.addWidget(self.search_edit)
        self.body.addLayout(bar)

        # Task list inside dark card
        card = _make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 6, 6, 6)
        self.list = _KeyAwareList()
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        self._delegate = TaskRowDelegate(self.list, scale=sc)
        self.list.setItemDelegate(self._delegate)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.delete_pressed.connect(self._delete_selected)
        self.list.space_pressed.connect(self._toggle_selected_done)
        cl.addWidget(self.list)
        self.body.addWidget(card, 1)

        # Undo strip (hidden unless something was deleted)
        self._undo = UndoBar(scale=sc)
        self._undo.undo_clicked.connect(self._undo_delete)
        self.body.addWidget(self._undo)

        # Footer actions
        foot = QHBoxLayout()
        foot.setSpacing(6)
        tip = QLabel("Space: done · F2: rename · Del")
        tip.setObjectName("tip")
        foot.addWidget(tip, 1)

        tog_due = self._btn("SET DUE", variant="soft", icon="clock", display=True, px=9)
        tog_due.setFixedHeight(_sc(28, sc, 24))
        tog_due.setToolTip("Open deadline bar to set or clear deadline on selected task(s)")
        tog_due.clicked.connect(self._open_due_for_selected)
        foot.addWidget(tog_due)

        tog_prio = self._btn("PRIORITY", variant="gold", icon="bolt", display=True, px=9)
        tog_prio.setFixedHeight(_sc(28, sc, 24))
        tog_prio.setToolTip("Toggle High priority on selected task(s)")
        tog_prio.clicked.connect(self._toggle_selected_priority)
        foot.addWidget(tog_prio)

        del_btn = self._btn("DELETE", variant="danger", display=True, px=9)
        del_btn.setFixedHeight(_sc(28, sc, 24))
        del_btn.clicked.connect(self._delete_selected)
        foot.addWidget(del_btn)

        clear_btn = self._btn("CLEAR DONE", variant="soft", display=True, px=9)
        clear_btn.setFixedHeight(_sc(28, sc, 24))
        clear_btn.clicked.connect(self._clear_done)
        foot.addWidget(clear_btn)
        self.body.addLayout(foot)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.search_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())

        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(420)
        self._resort_timer.timeout.connect(self.refresh)

    @staticmethod
    def _default_due() -> datetime:
        now = datetime.now().replace(second=0, microsecond=0)
        target = now.replace(hour=23, minute=59)
        return target if target > now else (now + timedelta(hours=2))

    def on_shown(self) -> None:
        QTimer.singleShot(20, self.input.setFocus)

    def _toggle_new_priority(self) -> None:
        self._new_high = not self._new_high
        self._prio_btn.set_active(self._new_high, active_variant="gold")

    def _toggle_due_bar(self) -> None:
        vis = not self._due_box.isVisible()
        self._due_box.setVisible(vis)
        if vis and not self._new_due_on:
            self._due_edit.setDateTime(_to_qdt(self._default_due()))
            self._due_check.setChecked(True)
        self._sync_due_btn()

    def _open_due_for_selected(self) -> None:
        sel = [i.data(ROLE_ID) for i in self.list.selectedItems() if i.data(ROLE_ID)]
        if sel:
            task = self.store.get(sel[0])
            dl = parse_dt(task.get("deadline")) if task else None
            self._due_edit.setDateTime(_to_qdt(dl or self._default_due()))
        self._due_box.setVisible(True)
        self._sync_due_btn()

    def _on_due_check_toggled(self, checked: bool) -> None:
        self._new_due_on = bool(checked)
        self._sync_due_btn()

    def _sync_due_btn(self) -> None:
        self._due_btn.set_active(self._new_due_on, active_variant="gold")

    def _apply_due_preset(self, kind: str) -> None:
        now = datetime.now().replace(second=0, microsecond=0)
        if kind == "1h":
            target = now + timedelta(hours=1)
        elif kind == "3h":
            target = now + timedelta(hours=3)
        elif kind == "eod":
            target = now.replace(hour=23, minute=59)
            if target <= now:
                target += timedelta(days=1)
        else:
            target = (now + timedelta(days=1)).replace(hour=9, minute=0)
        self._due_edit.setDateTime(_to_qdt(target))
        self._due_check.setChecked(True)

    def _apply_due_to_selected(self) -> None:
        dt = _from_qdt(self._due_edit.dateTime())
        changed = False
        for item in self.list.selectedItems():
            tid = item.data(ROLE_ID)
            if tid and self.store.set_deadline(tid, dt):
                changed = True
        if changed:
            self.refresh()
            self.tasks_changed.emit()

    def _clear_due_selected(self) -> None:
        self._due_check.setChecked(False)
        changed = False
        for item in self.list.selectedItems():
            tid = item.data(ROLE_ID)
            if tid and self.store.set_deadline(tid, None):
                changed = True
        if changed:
            self.refresh()
            self.tasks_changed.emit()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, chip in self._chips.items():
            chip.set_active(k == key)
        self.refresh()

    def _update_status(self) -> None:
        tasks = self.store.all()
        done = sum(1 for t in tasks if t.get("done"))
        self.progress.setRange(0, max(len(tasks), 1))
        self.progress.setValue(done)
        self._count_lbl.setText(f"{len(tasks) - done} LEFT · {done} DONE")

    @staticmethod
    def _due_badge(task: dict, now: datetime) -> tuple[str, bool]:
        dl = parse_dt(task.get("deadline"))
        if dl is None or task.get("done"):
            return "", False
        if dl <= now:
            when = f"{dl:%H:%M}" if dl.date() == now.date() else f"{dl:%d %b}"
            return f"OVERDUE {when}".upper(), True
        if dl.date() == now.date():
            return f"DUE {dl:%H:%M}", False
        return f"DUE {dl:%d %b %H:%M}".upper(), False

    def refresh(self) -> None:
        now = datetime.now()
        query = self.search_edit.text().strip().lower()
        tasks = self.store.all()
        pending = [t for t in tasks if not t.get("done")]

        def _sort_key(t: dict):
            dl = parse_dt(t.get("deadline"))
            is_ov = 0 if (dl is not None and dl <= now) else 1
            prio = 0 if t.get("priority") == "high" else 1
            has_dl = 0 if dl is not None else 1
            dl_ts = dl.timestamp() if dl is not None else float("inf")
            return (is_ov, prio, has_dl, dl_ts)

        pending.sort(key=_sort_key)
        finished = [t for t in tasks if t.get("done")]
        pools = {
            "all": pending + finished,
            "todo": pending,
            "due": [t for t in pending if t.get("deadline")],
            "high": [t for t in pending if t.get("priority") == "high"],
            "done": finished,
        }
        shown = pools[self._filter]
        if query:
            shown = [t for t in shown if query in (t.get("title") or "").lower()]

        self.list.blockSignals(True)
        self.list.clear()
        if not shown:
            if query:
                msg = "No tasks match your search."
            elif not tasks:
                msg = "No tasks yet.\nType one above and press Enter."
            elif self._filter == "todo":
                msg = "All tasks completed! Nice work."
            elif self._filter == "due":
                msg = "No tasks with deadlines.\nClick DUE above to attach one!"
            elif self._filter == "high":
                msg = "No high-priority tasks right now."
            else:
                msg = "No completed tasks yet."
            self.list.addItem(_hint_item(msg))
        for t in shown:
            badge_txt, is_overdue = self._due_badge(t, now)
            item = QListWidgetItem(t.get("title", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsEditable)
            item.setData(ROLE_ID, t["id"])
            item.setData(ROLE_DONE, bool(t.get("done")))
            item.setData(ROLE_FLAG, t.get("priority") == "high")
            item.setData(ROLE_BADGE, badge_txt)
            item.setData(ROLE_OVERDUE, is_overdue)
            item.setData(ROLE_DATE, _fmt_time_12h(t.get("created")))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_status()

    def _add(self) -> None:
        dl = _from_qdt(self._due_edit.dateTime()) if self._new_due_on else None
        task = self.store.add(
            self.input.text(),
            priority="high" if self._new_high else "normal",
            deadline=dl,
        )
        if task is None:
            return
        self.input.clear()
        if self._new_high:
            self._toggle_new_priority()
        if self._new_due_on:
            self._due_check.setChecked(False)
        if self._filter == "done":
            self._set_filter("all")
        else:
            self.refresh()
        self.tasks_changed.emit()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        task_id = item.data(ROLE_ID)
        task = self.store.get(task_id) if task_id else None
        if task is None:
            return
        title = item.text().strip()
        if title and title != task["title"]:
            self.store.rename(task_id, title)
        elif not title:
            self.list.blockSignals(True)
            item.setText(task["title"])
            self.list.blockSignals(False)
        done = bool(item.data(ROLE_DONE))
        if done != bool(task.get("done")):
            self.store.set_done(task_id, done)
            if done:
                play_ui("select")
                self._delegate.start_check_anim(task_id)
                self.task_reward_eligible.emit(task_id)
                self.task_completed.emit(task["title"], len(self.store.pending()))
            else:
                play_ui("cancel")
                self._delegate.clear_anim(task_id)
            self._resort_timer.start()
        self._update_status()
        self.tasks_changed.emit()

    def _toggle_selected_done(self) -> None:
        for item in self.list.selectedItems():
            if item.data(ROLE_ID):
                item.setData(ROLE_DONE, not bool(item.data(ROLE_DONE)))

    def _toggle_selected_priority(self) -> None:
        changed = False
        for item in self.list.selectedItems():
            tid = item.data(ROLE_ID)
            task = self.store.get(tid) if tid else None
            if task is not None:
                nxt = "normal" if task.get("priority") == "high" else "high"
                self.store.set_priority(tid, nxt)
                changed = True
        if changed:
            self.refresh()
            self.tasks_changed.emit()

    def _delete_selected(self) -> None:
        ids = [i.data(ROLE_ID) for i in self.list.selectedItems() if i.data(ROLE_ID)]
        removed = [dict(self.store.get(tid)) for tid in ids if self.store.get(tid)]
        if not removed:
            return
        for tid in ids:
            self.store.delete(tid)
        self._undo_items = removed
        self.refresh()
        self.tasks_changed.emit()
        n = len(removed)
        self._undo.trigger(f"Deleted {n} {'task' if n == 1 else 'tasks'}.")

    def _clear_done(self) -> None:
        done_items = [dict(t) for t in self.store.all() if t.get("done")]
        if not done_items:
            return
        self.store.clear_done()
        self._undo_items = done_items
        self.refresh()
        self.tasks_changed.emit()
        n = len(done_items)
        self._undo.trigger(f"Cleared {n} completed {'task' if n == 1 else 'tasks'}.")

    def _undo_delete(self) -> None:
        if self._undo_items:
            self.store.restore(self._undo_items)
            self._undo_items = []
            self.refresh()
            self.tasks_changed.emit()


# ---------------------------------------------------------------------------
# NOTES DIALOG (2-column Persona 5 workspace: full-height editor + fast sidebar)
# ---------------------------------------------------------------------------
class NotesDialog(P5FluidPanel):
    FILTERS = (("ALL", "all"), ("PIN", "pinned"), ("DUE", "reminders"), ("DONE", "done"))
    open_settings = pyqtSignal()
    open_tasks = pyqtSignal()
    tasks_changed = pyqtSignal()
    notes_changed = pyqtSignal()
    note_completed = pyqtSignal(str)          # emits note_id when marked done

    def __init__(self, store: NoteStore, task_store: TaskStore | None = None,
                 on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("NOTES", QSize(820, 600), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self.task_store = task_store
        self._current_id: str | None = None
        self._category = "Personal"
        self._filter = "all"
        self._sort_recent = False
        self._loading = False
        self._dirty = False
        self._undo_note: dict | None = None

        # Header quick actions (1-click jump to Tasks or Settings)
        self._tasks_btn = self._btn("TASKS", variant="soft", display=True, px=10)
        self._tasks_btn.clicked.connect(lambda _c=False: self.open_tasks.emit())
        self.header_bar.addWidget(self._tasks_btn)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Two-column body
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # -- Left sidebar: New button, search, filters, note list --
        left_card = _make_card()
        left_card.setMinimumWidth(_sc(210, sc, 185))
        left_card.setMaximumWidth(_sc(284, sc, 225))
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(8)

        new_btn = self._btn("NEW NOTE", variant="primary", icon="plus", display=True, px=11)
        new_btn.setFixedHeight(_sc(32, sc, 26))
        new_btn.clicked.connect(self._new)
        ll.addWidget(new_btn)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search notes... (Ctrl+F)")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh)
        ll.addWidget(self.search_edit)

        frow = QHBoxLayout()
        frow.setSpacing(4)
        self._fchips: dict[str, P5FluidButton] = {}
        for label, key in self.FILTERS:
            fb = self._btn(label, variant="soft", display=True, px=9)
            fb.setFixedHeight(_sc(24, sc, 20))
            fb.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self._fchips[key] = fb
            frow.addWidget(fb)
        self._fchips["all"].set_active(True)
        ll.addLayout(frow)

        mrow = QHBoxLayout()
        self.count_label = QLabel("0 NOTES")
        self.count_label.setObjectName("subhead")
        mrow.addWidget(self.count_label)
        mrow.addStretch(1)
        self._sort_btn = self._btn("DEADLINES", variant="ghost", icon="sort", px=10)
        self._sort_btn.setFixedHeight(_sc(22, sc, 20))
        self._sort_btn.setToolTip("Switch sort order (Deadlines / Recent)")
        self._sort_btn.clicked.connect(self._toggle_sort)
        mrow.addWidget(self._sort_btn)
        ll.addLayout(mrow)

        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._delegate = NoteCardDelegate(self.list, scale=sc)
        self.list.setItemDelegate(self._delegate)
        self.list.itemSelectionChanged.connect(self._on_select)
        ll.addWidget(self.list, 1)
        cols.addWidget(left_card, 34)

        # -- Right panel: Full-height Note Editor --
        editor_card = _make_card()
        el = QVBoxLayout(editor_card)
        el.setContentsMargins(14, 12, 14, 12)
        el.setSpacing(8)

        # Title + Pin + Done + Delete
        eh = QHBoxLayout()
        eh.setSpacing(6)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("titleInput")
        self.title_edit.setPlaceholderText("Note title...")
        self.title_edit.textChanged.connect(self._on_edited)
        eh.addWidget(self.title_edit, 1)

        self._pin_btn = self._btn("", variant="soft", icon="pin")
        self._pin_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        self._pin_btn.setToolTip("Pin note to top")
        self._pin_btn.setEnabled(False)
        self._pin_btn.clicked.connect(self._toggle_pin)
        eh.addWidget(self._pin_btn)

        self._done_btn = self._btn("DONE", variant="soft", icon="check", display=True, px=10)
        self._done_btn.setFixedHeight(_sc(30, sc, 24))
        self._done_btn.setEnabled(False)
        self._done_btn.clicked.connect(self._toggle_done)
        eh.addWidget(self._done_btn)

        self._del_btn = self._btn("", variant="danger", icon="trash")
        self._del_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        self._del_btn.setToolTip("Delete note (can undo)")
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self._delete)
        eh.addWidget(self._del_btn)
        el.addLayout(eh)

        # Category chips (1-click) + Reminder toggle + word count
        meta = QHBoxLayout()
        meta.setSpacing(5)
        self._cat_btns: dict[str, P5FluidButton] = {}
        for cat in NOTE_CATEGORIES:
            cb = self._btn(cat.upper(), variant="soft", display=True, px=9)
            cb.setFixedHeight(_sc(24, sc, 20))
            cb.clicked.connect(lambda _c=False, c=cat: self._set_category(c))
            self._cat_btns[cat] = cb
            meta.addWidget(cb)
        self._sync_category_ui()

        self._rem_btn = self._btn("REMINDER", variant="soft", icon="clock", display=True, px=9)
        self._rem_btn.setFixedHeight(_sc(24, sc, 20))
        self._rem_btn.clicked.connect(self._toggle_reminder_bar)
        meta.addWidget(self._rem_btn)
        meta.addStretch(1)

        self._words_lbl = QLabel("0 words")
        self._words_lbl.setObjectName("tip")
        meta.addWidget(self._words_lbl)
        el.addLayout(meta)

        # Collapsible inline reminder card
        self.deadline_box = _make_card("subcard")
        db = QVBoxLayout(self.deadline_box)
        db.setContentsMargins(10, 8, 10, 8)
        db.setSpacing(6)
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        self.deadline_check = P5CheckBox("Remind me", scale=sc)
        self.deadline_check.toggled.connect(self._on_deadline_toggled)
        r1.addWidget(self.deadline_check)
        self.deadline_edit = QDateTimeEdit()
        self.deadline_edit.setCalendarPopup(True)
        self.deadline_edit.setDisplayFormat("dd MMM yyyy HH:mm")
        self.deadline_edit.setDateTime(self._default_deadline())
        self.deadline_edit.setMinimumHeight(_sc(28, sc, 24))
        self.deadline_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.deadline_edit.dateTimeChanged.connect(self._on_deadline_changed)
        r1.addWidget(self.deadline_edit, 1)
        self.deadline_hint = QLabel("")
        self.deadline_hint.setObjectName("tip")
        self.deadline_hint.setMinimumWidth(0)
        r1.addWidget(self.deadline_hint)
        db.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(_sc(5, sc, 4))
        for text, kind in (("+30m", "30m"), ("+1h", "1h"), ("+3h", "3h"), ("23:59", "eod"), ("Tomorrow 9:00", "tomorrow")):
            pb = self._btn(text, variant="gold" if kind == "eod" else "soft", px=10)
            pb.setFixedHeight(_sc(24, sc, 21))
            if kind == "eod":
                pb.setToolTip("Set deadline to 23:59 today (or tomorrow if past 23:59)")
            pb.clicked.connect(lambda _c=False, k=kind: self._apply_preset(k))
            r2.addWidget(pb)
        r2.addStretch(1)
        db.addLayout(r2)
        self.deadline_box.setVisible(False)
        el.addWidget(self.deadline_box)

        # Spacious note body editor
        self.content_edit = QTextEdit()
        self.content_edit.setObjectName("noteBody")
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setPlaceholderText("Write notes, ideas, or reminders here... (auto-saves as you type)")
        self.content_edit.textChanged.connect(self._on_content_changed)
        el.addWidget(self.content_edit, 1)

        # Undo strip (hidden unless a note was deleted)
        self._undo = UndoBar(scale=sc)
        self._undo.undo_clicked.connect(self._undo_delete)
        el.addWidget(self._undo)

        # Bottom quick-insert toolbar + live status + SAVE button
        bf = QHBoxLayout()
        bf.setSpacing(8)
        pill = _make_card("toolbarPill")
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(4, 3, 4, 3)
        pl.setSpacing(2)
        for label, ic, tip, fn in (
            ("•", "", "Insert bullet point", lambda: self._insert_snippet("• ")),
            ("☐", "", "Insert checklist item", lambda: self._insert_snippet("[ ] ")),
            ("—", "", "Insert divider", lambda: self._insert_snippet("\n---\n")),
            ("", "clock", "Insert current timestamp", self._insert_timestamp),
        ):
            tb_btn = self._btn(label, variant="ghost", icon=ic, px=12)
            tb_btn.setFixedSize(_sc(30, sc, 24), _sc(26, sc, 22))
            tb_btn.setToolTip(tip)
            tb_btn.clicked.connect(lambda _c=False, f=fn: f())
            pl.addWidget(tb_btn)
        bf.addWidget(pill)

        self.status = QLabel("")
        self.status.setObjectName("tip")
        bf.addWidget(self.status, 1)

        save_btn = self._btn("SAVE", variant="primary", display=True, px=11)
        save_btn.setMinimumWidth(_sc(82, sc, 68))
        save_btn.clicked.connect(self._save)
        bf.addWidget(save_btn)
        el.addLayout(bf)

        cols.addWidget(editor_card, 66)
        self.body.addLayout(cols, 1)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._new)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.search_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())

        # Debounced autosave (800 ms after typing stops)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(800)
        self._autosave_timer.timeout.connect(self._debounced_autosave)

    # -- panel hooks -------------------------------------------------------------
    def on_shown(self) -> None:
        if self._current_id is None:
            notes = self.store.all()
            if notes:
                self._load(notes[0])
                self.refresh()
            else:
                QTimer.singleShot(20, self.title_edit.setFocus)

    def on_closing(self) -> None:
        self._autosave_timer.stop()
        if self._dirty:
            self._save(silent=True)
        self.refresh()

    # -- helpers -----------------------------------------------------------------
    @staticmethod
    def _default_deadline() -> QDateTime:
        return _to_qdt(datetime.now() + timedelta(hours=1))

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, btn in self._fchips.items():
            btn.set_active(k == key)
        self.refresh()

    def _toggle_sort(self) -> None:
        self._sort_recent = not self._sort_recent
        self._sort_btn.setText("RECENT" if self._sort_recent else "DEADLINES")
        self.refresh()

    def _set_category(self, cat: str) -> None:
        self._category = cat
        self._sync_category_ui()
        self._on_edited()

    def _sync_category_ui(self) -> None:
        for cat, btn in self._cat_btns.items():
            btn.set_active(cat == self._category, active_variant="primary")

    def _toggle_reminder_bar(self) -> None:
        show = not self.deadline_box.isVisible()
        self.deadline_box.setVisible(show)
        if show and not self.deadline_check.isChecked():
            self.deadline_check.setChecked(True)

    def _insert_snippet(self, snippet: str) -> None:
        cur = self.content_edit.textCursor()
        if snippet in ("• ", "[ ] ") and cur.positionInBlock() > 0:
            cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cur.insertText(snippet)
        self.content_edit.setFocus()

    def _insert_timestamp(self) -> None:
        self._insert_snippet(f"[{datetime.now():%d %b %H:%M}] ")

    def _on_content_changed(self) -> None:
        n = len(self.content_edit.toPlainText().split())
        self._words_lbl.setText(f"{n} {'word' if n == 1 else 'words'}")
        self._on_edited()

    def _on_edited(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status.setText("Editing...")
        self._autosave_timer.start()

    def _debounced_autosave(self) -> None:
        if not self._dirty:
            return
        if self.title_edit.text().strip() or self.content_edit.toPlainText().strip():
            if self._save(silent=True):
                self.refresh()
                self.status.setText("Auto-saved")

    def _on_deadline_toggled(self, _on: bool) -> None:
        self._update_hint()
        self._on_edited()

    def _on_deadline_changed(self, *_args) -> None:
        self._update_hint()
        self._on_edited()

    def _update_hint(self) -> None:
        if not self.deadline_check.isChecked():
            self.deadline_hint.setText("Off")
            self._rem_btn.set_variant("soft")
            self._rem_btn.setText("REMINDER")
            return
        dt = _from_qdt(self.deadline_edit.dateTime())
        delta = dt - datetime.now()
        if delta.total_seconds() > 0:
            self.deadline_hint.setText(f"In {human_delta(delta)}")
            self._rem_btn.set_variant("gold")
            self._rem_btn.setText(f"DUE {dt:%d %b %H:%M}".upper())
        else:
            self.deadline_hint.setText("Past due")
            self._rem_btn.set_variant("danger")
            self._rem_btn.setText("OVERDUE")

    def _apply_preset(self, kind: str) -> None:
        now = datetime.now()
        if kind == "30m":
            target = now + timedelta(minutes=30)
        elif kind == "1h":
            target = now + timedelta(hours=1)
        elif kind == "3h":
            target = now + timedelta(hours=3)
        elif kind == "eod":
            target = now.replace(hour=23, minute=59, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
        else:
            target = (now + timedelta(days=1)).replace(hour=9, minute=0)
        self.deadline_check.setChecked(True)
        self.deadline_edit.setDateTime(_to_qdt(target))

    # -- note list ---------------------------------------------------------------
    @staticmethod
    def _card_meta(note: dict) -> tuple[str, str, str]:
        preview = " ".join((note.get("content") or "").split())[:95] or "Empty note"
        date_txt = _fmt_short_date(note.get("updated") or note.get("created"))
        if note.get("done"):
            return preview, "DONE", date_txt
        dl = parse_dt(note.get("deadline"))
        if dl is not None:
            delta = dl - datetime.now()
            if delta.total_seconds() > 0:
                return preview, f"IN {human_delta(delta).upper()}", f"{dl:%d %b %H:%M}"
            return preview, "OVERDUE", f"{dl:%d %b %H:%M}"
        return preview, "", date_txt

    def refresh(self) -> None:
        far = datetime.max
        query = self.search_edit.text().strip().lower()

        def matches(n: dict) -> bool:
            if self._filter == "pinned" and not n.get("pinned"):
                return False
            if self._filter == "reminders" and (not n.get("deadline") or n.get("done")):
                return False
            if self._filter == "done" and not n.get("done"):
                return False
            if query:
                blob = f"{n.get('title', '')} {n.get('content', '')} {n.get('category', '')}".lower()
                return query in blob
            return True

        notes = [n for n in self.store.all() if matches(n)]
        notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
        if self._sort_recent:
            notes.sort(key=lambda n: (0 if n.get("pinned") else 1))
        else:
            def group(n: dict):
                pin = 0 if (n.get("pinned") and not n.get("done")) else 1
                if n.get("done"):
                    return (2, 1, far)
                dl = parse_dt(n.get("deadline"))
                return (pin, 0, dl) if dl else (pin, 1, far)
            notes.sort(key=group)

        n_cnt = len(notes)
        self.count_label.setText(f"{n_cnt} {'NOTE' if n_cnt == 1 else 'NOTES'}")
        self.list.blockSignals(True)
        self.list.clear()
        if not notes:
            msg = ("No notes match this filter." if (query or self._filter != "all")
                   else "No notes yet.\nClick NEW NOTE or start typing.")
            self.list.addItem(_hint_item(msg))
        for n in notes:
            preview, badge, date_txt = self._card_meta(n)
            item = QListWidgetItem(n.get("title") or "(Untitled)")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(ROLE_ID, n["id"])
            item.setData(ROLE_DONE, bool(n.get("done")))
            item.setData(ROLE_FLAG, bool(n.get("pinned")))
            item.setData(ROLE_PREVIEW, preview)
            item.setData(ROLE_BADGE, badge)
            item.setData(ROLE_DATE, date_txt)
            item.setData(ROLE_CAT, n.get("category") or "Personal")
            self.list.addItem(item)
            if n["id"] == self._current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    # -- selection, load, new, pin, done, save, delete + undo --------------------
    def _on_select(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        note_id = items[0].data(ROLE_ID)
        if note_id is None or note_id == self._current_id:
            return
        self._autosave_timer.stop()
        if self._dirty:
            self._save(silent=True)
        note = self.store.get(note_id)
        if note is not None:
            play_ui("select")
            self._load(note)
        QTimer.singleShot(0, self.refresh)

    def _sync_flags_ui(self, pinned: bool, done: bool) -> None:
        self._pin_btn.set_variant("gold" if pinned else "soft")
        self._done_btn.set_variant("mint" if done else "soft")
        self._done_btn.setText("DONE" if done else "MARK DONE")

    def _load(self, note: dict) -> None:
        self._loading = True
        self._autosave_timer.stop()
        self._current_id = note["id"]
        self._category = note.get("category") if note.get("category") in NOTE_CATEGORIES else "Personal"
        self._sync_category_ui()
        self.title_edit.setText(note.get("title", ""))
        self.title_edit.setCursorPosition(0)
        self.content_edit.setPlainText(note.get("content", ""))
        n_words = len((note.get("content") or "").split())
        self._words_lbl.setText(f"{n_words} {'word' if n_words == 1 else 'words'}")
        dl = parse_dt(note.get("deadline"))
        self.deadline_check.setChecked(dl is not None)
        self.deadline_edit.setDateTime(_to_qdt(dl) if dl else self._default_deadline())
        self.deadline_box.setVisible(False)
        for btn in (self._pin_btn, self._done_btn, self._del_btn):
            btn.setEnabled(True)
        self._sync_flags_ui(bool(note.get("pinned")), bool(note.get("done")))
        self._loading = False
        self._dirty = False
        self._update_hint()
        self.status.setText("")

    def _new(self) -> None:
        self._autosave_timer.stop()
        if self._dirty:
            self._save(silent=True)
        self._loading = True
        self._current_id = None
        self._category = "Personal"
        self._sync_category_ui()
        self.title_edit.clear()
        self.content_edit.clear()
        self._words_lbl.setText("0 words")
        want_rem = self._filter == "reminders"
        self.deadline_check.setChecked(want_rem)
        self.deadline_edit.setDateTime(self._default_deadline())
        self.deadline_box.setVisible(want_rem)
        for btn in (self._pin_btn, self._done_btn, self._del_btn):
            btn.setEnabled(False)
        self._sync_flags_ui(False, False)
        self._loading = False
        self._dirty = False
        self._update_hint()
        self.list.clearSelection()
        self.refresh()
        self.status.setText("")
        self.title_edit.setFocus()

    def _toggle_pin(self) -> None:
        if not self._current_id:
            return
        note = self.store.get(self._current_id)
        if note is None:
            return
        pinned = not bool(note.get("pinned"))
        self.store.set_pinned(self._current_id, pinned)
        self._sync_flags_ui(pinned, bool(note.get("done")))
        self.refresh()

    def _toggle_done(self) -> None:
        if not self._current_id:
            return
        note = self.store.get(self._current_id)
        if note is None:
            return
        done = not bool(note.get("done"))
        self.store.set_done(self._current_id, done)
        self._sync_flags_ui(bool(note.get("pinned")), done)
        self.refresh()
        if done:
            self.note_completed.emit(self._current_id)
        self.notes_changed.emit()

    def _save(self, silent: bool = False) -> bool:
        silent = silent is True
        self._autosave_timer.stop()
        title = self.title_edit.text().strip()
        content = self.content_edit.toPlainText().strip()
        if not title and not content:
            if not silent:
                self.status.setText("Add a title or content first.")
            return False
        if not title:
            title = content.splitlines()[0][:40]
        deadline = _from_qdt(self.deadline_edit.dateTime()) if self.deadline_check.isChecked() else None
        if self._current_id and self.store.get(self._current_id):
            note = self.store.update(self._current_id, title, content, deadline, self._category)
        else:
            note = self.store.add(title, content, deadline, self._category)
        self._current_id = note["id"] if note else None
        self._dirty = False
        for btn in (self._pin_btn, self._done_btn, self._del_btn):
            btn.setEnabled(self._current_id is not None)
        if note:
            self._sync_flags_ui(bool(note.get("pinned")), bool(note.get("done")))
        self.notes_changed.emit()
        if silent:
            return True
        self.refresh()
        if self.store.last_error:
            self.status.setText("Save failed: " + self.store.last_error)
        elif deadline:
            self.status.setText(f"Saved · reminder set for {deadline:%d %b %H:%M}")
        else:
            self.status.setText("Saved")
        return True

    def _delete(self) -> None:
        if not self._current_id:
            return
        note = self.store.get(self._current_id)
        if note is None:
            return
        self._undo_note = dict(note)
        title = note.get("title") or "Untitled"
        self.store.delete(self._current_id)
        self._dirty = False
        self._new()
        self.notes_changed.emit()
        self._undo.trigger(f"Deleted '{title[:28]}'.")

    def _undo_delete(self) -> None:
        if self._undo_note is None:
            return
        restored = self.store.restore(self._undo_note)
        self._undo_note = None
        if restored is not None:
            self._load(restored)
            self.refresh()
            self.notes_changed.emit()
            self.status.setText("Note restored.")


# ---------------------------------------------------------------------------
# SETTINGS DIALOG (Persona 5 dark theme, Bedtime + Reminders + Focus + Behavior)
# ---------------------------------------------------------------------------
class SettingsDialog(P5FluidPanel):
    saved = pyqtSignal()
    always_on_top_changed = pyqtSignal(bool)
    autostart_changed = pyqtSignal(bool)

    def __init__(self, store: SettingsStore, get_on_top=None, get_autostart=None,
                 on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("SETTINGS", QSize(500, 740), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self._get_on_top = get_on_top
        self._get_autostart = get_autostart
        self._saving = False
        self._filling = False
        self._add_close_button()

        # Scrollable cards container (prevents widget squeezing on compact screens or 130-150% UI scale)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_body = QWidget()
        cards_lay = QVBoxLayout(scroll_body)
        cards_lay.setContentsMargins(0, 0, _sc(4, sc, 2), 0)
        cards_lay.setSpacing(_sc(10, sc, 7))
        pad_x, pad_y = _sc(14, sc, 10), _sc(11, sc, 8)
        row_gap = _sc(7, sc, 5)

        # Card 1: Audio & Separate Volume Controls
        c_aud = _make_card()
        la = QVBoxLayout(c_aud)
        la.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        la.setSpacing(row_gap)
        ha = QLabel("AUDIO & VOLUME")
        ha.setObjectName("section")
        la.addWidget(ha)
        self.sound_check = P5CheckBox("Enable audio (Morgana voice & UI SFX)", scale=sc)
        self.sound_check.toggled.connect(self._on_sound_toggle_preview)
        la.addWidget(self.sound_check)

        self.voice_slider, self._voice_val_lbl = self._vol_row(
            la, "Morgana Voice", "TEST", self._test_voice
        )
        self.voice_slider.valueChanged.connect(self._on_voice_slider)

        self.sfx_slider, self._sfx_val_lbl = self._vol_row(
            la, "UI & Stats SFX", "TEST", self._test_sfx
        )
        self.sfx_slider.valueChanged.connect(self._on_sfx_slider)
        cards_lay.addWidget(c_aud)

        # Card 2: Bedtime reminder
        c1 = _make_card()
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        l1.setSpacing(row_gap)
        h1 = QLabel("BEDTIME REMINDER")
        h1.setObjectName("section")
        l1.addWidget(h1)
        self.curfew_check = P5CheckBox("Remind me to go to sleep", scale=sc)
        l1.addWidget(self.curfew_check)
        self.bed_edit = QTimeEdit()
        self.bed_edit.setDisplayFormat("HH:mm")
        self._row(l1, "Bedtime starts", self.bed_edit)
        self.wake_edit = QTimeEdit()
        self.wake_edit.setDisplayFormat("HH:mm")
        self._row(l1, "Reminder stops (wake up)", self.wake_edit)
        self.curfew_interval = QSpinBox()
        self.curfew_interval.setRange(10, 3600)
        self.curfew_interval.setSingleStep(10)
        self.curfew_interval.setSuffix(" sec")
        self._row(l1, "Morgana speaks every", self.curfew_interval)
        cards_lay.addWidget(c1)

        # Card 3: Event, Task & Note reminders
        c2 = _make_card()
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        l2.setSpacing(row_gap)
        h2 = QLabel("EVENT, TASK & NOTE REMINDERS")
        h2.setObjectName("section")
        l2.addWidget(h2)
        self.event_window = QSpinBox()
        self.event_window.setRange(5, 1440)
        self.event_window.setSuffix(" min")
        self._row(l2, "Start reminding before", self.event_window)
        self.event_interval = QSpinBox()
        self.event_interval.setRange(1, 120)
        self.event_interval.setSuffix(" min")
        self._row(l2, "Repeat every", self.event_interval)
        cards_lay.addWidget(c2)

        # Card 4: Focus timer, Companion & Behavior
        c3 = _make_card()
        l3 = QVBoxLayout(c3)
        l3.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        l3.setSpacing(row_gap)
        h3 = QLabel("FOCUS, COMPANION & APP")
        h3.setObjectName("section")
        l3.addWidget(h3)
        self.focus_spin = QSpinBox()
        self.focus_spin.setRange(1, 180)
        self.focus_spin.setSuffix(" min")
        self._row(l3, "Focus session duration", self.focus_spin)
        self.rest_spin = QSpinBox()
        self.rest_spin.setRange(1, 60)
        self.rest_spin.setSuffix(" min")
        self._row(l3, "Break duration", self.rest_spin)
        self.ui_scale_spin = QSpinBox()
        self.ui_scale_spin.setRange(75, 150)
        self.ui_scale_spin.setSingleStep(5)
        self.ui_scale_spin.setSuffix("%")
        self._row(l3, "UI size", self.ui_scale_spin)
        self.briefing_check = P5CheckBox("Show Daily Briefing & Confidant Summary once a day", scale=sc)
        self.auto_reward_check = P5CheckBox("Auto-reward Social Stats from tasks, notes & focus", scale=sc)
        self.on_top_check = P5CheckBox("Keep Morgana always on top", scale=sc)
        self.autostart_check = P5CheckBox("Start Morgana automatically on boot", scale=sc)
        l3.addWidget(self.briefing_check)
        l3.addWidget(self.auto_reward_check)
        l3.addWidget(self.on_top_check)
        l3.addWidget(self.autostart_check)
        cards_lay.addWidget(c3)

        # Card 5: Radial menu tabs (enable/disable individual tabs)
        c4 = _make_card()
        l4 = QVBoxLayout(c4)
        l4.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        l4.setSpacing(row_gap)
        h4 = QLabel("RADIAL MENU TABS")
        h4.setObjectName("section")
        l4.addWidget(h4)
        tabs_grid = QGridLayout()
        tabs_grid.setContentsMargins(0, 0, 0, 0)
        tabs_grid.setHorizontalSpacing(_sc(12, sc, 8))
        tabs_grid.setVerticalSpacing(_sc(6, sc, 4))
        self.tab_checks: dict[str, P5CheckBox] = {}
        tab_short = {
            "events": "Events (7-Day)",
            "tasks": "Tasks (To-Do)",
            "timer": "Timer & Focus",
            "notes": "Quick Notes",
            "stats": "Social Stats",
            "system": "System Menu",
        }
        # Mirror radial menu layout: Left column (Events, Timer, System), Right column (Tasks, Notes, Stats)
        left_keys = ("events", "timer", "system")
        right_keys = ("tasks", "notes", "stats")
        for r_idx, (lk, rk) in enumerate(zip(left_keys, right_keys)):
            for c_idx, key in enumerate((lk, rk)):
                cb = P5CheckBox(tab_short.get(key, key.title()), scale=sc)
                self.tab_checks[key] = cb
                tabs_grid.addWidget(cb, r_idx, c_idx)
        l4.addLayout(tabs_grid)
        tab_tip = QLabel("Choose which tabs show in Morgana's radial menu. Right-click Morgana anytime for Settings.")
        tab_tip.setObjectName("tip")
        tab_tip.setWordWrap(True)
        l4.addWidget(tab_tip)
        cards_lay.addWidget(c4)
        cards_lay.addStretch(1)

        scroll.setWidget(scroll_body)
        self.body.addWidget(scroll, 1)

        # Footer
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("tip")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)

        reset_btn = self._btn("RESET", variant="soft", display=True, px=11)
        reset_btn.clicked.connect(self._reset)
        foot.addWidget(reset_btn)

        save_btn = self._btn("SAVE", variant="primary", display=True, px=11)
        save_btn.setMinimumWidth(_sc(88, sc, 72))
        save_btn.clicked.connect(self._save)
        foot.addWidget(save_btn)
        self.body.addLayout(foot)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)

    def _row(self, layout: QVBoxLayout, text: str, widget: QWidget) -> None:
        sc = self._ui_scale
        row = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: #D6D6E2; font-size: {_sc(14, sc, 11)}px;")
        row.addWidget(lbl, 1)
        widget.setMinimumWidth(_sc(112, sc, 92))
        widget.setMinimumHeight(_sc(30, sc, 25))
        row.addWidget(widget)
        layout.addLayout(row)

    def _vol_row(self, layout: QVBoxLayout, text: str, btn_text: str,
                 on_test) -> tuple[QSlider, QLabel]:
        sc = self._ui_scale
        row = QHBoxLayout()
        row.setSpacing(_sc(8, sc, 5))
        lbl = QLabel(text)
        lbl.setMinimumWidth(_sc(108, sc, 86))
        lbl.setStyleSheet(f"color: #D6D6E2; font-size: {_sc(14, sc, 11)}px;")
        row.addWidget(lbl)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(5)
        slider.setPageStep(10)
        row.addWidget(slider, 1)

        val_lbl = QLabel("80%")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setFixedWidth(_sc(40, sc, 32))
        val_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(13, sc, 10)}px; font-weight: 800;'
        )
        row.addWidget(val_lbl)

        test_btn = self._btn(btn_text, variant="soft", display=True, px=9, sfx_click="")
        test_btn.setFixedHeight(_sc(24, sc, 20))
        test_btn.setToolTip(f"Preview {text} volume")
        test_btn.clicked.connect(on_test)
        row.addWidget(test_btn)
        layout.addLayout(row)
        return slider, val_lbl

    def _on_sound_toggle_preview(self, checked: bool) -> None:
        self.voice_slider.setEnabled(checked)
        self.sfx_slider.setEnabled(checked)
        if not self._filling and audio() is not None:
            audio().set_enabled(bool(checked))

    def _on_voice_slider(self, value: int) -> None:
        self._voice_val_lbl.setText(f"{int(value)}%")
        if not self._filling and audio() is not None:
            audio().set_voice_volume(int(value))

    def _on_sfx_slider(self, value: int) -> None:
        self._sfx_val_lbl.setText(f"{int(value)}%")
        if not self._filling and audio() is not None:
            audio().set_sfx_volume(int(value))

    def _test_voice(self) -> None:
        mgr = audio()
        if mgr is None:
            return
        mgr.set_enabled(self.sound_check.isChecked())
        mgr.set_voice_volume(self.voice_slider.value())
        play_voice("hmm")

    def _test_sfx(self) -> None:
        mgr = audio()
        if mgr is None:
            return
        mgr.set_enabled(self.sound_check.isChecked())
        mgr.set_sfx_volume(self.sfx_slider.value())
        play_ui("open_window")

    def on_closing(self) -> None:
        """Restore persisted audio volumes if the user closed Settings without saving."""
        mgr = audio()
        if mgr is not None:
            mgr.set_enabled(bool(self.store["sound_enabled"]))
            mgr.set_volumes(int(self.store["voice_volume"]), int(self.store["sfx_volume"]))

    def _fill(self, values: dict) -> None:
        self._filling = True
        try:
            self.curfew_check.setChecked(bool(values["curfew_enabled"]))
            self.bed_edit.setTime(QTime.fromString(values["bedtime"], "HH:mm"))
            self.wake_edit.setTime(QTime.fromString(values["wake"], "HH:mm"))
            self.curfew_interval.setValue(int(values.get("curfew_interval_sec", SETTINGS_DEFAULTS["curfew_interval_sec"])))
            self.event_window.setValue(int(values["event_window_min"]))
            self.event_interval.setValue(int(values["event_interval_min"]))
            self.focus_spin.setValue(int(values.get("focus_min", SETTINGS_DEFAULTS["focus_min"])))
            self.rest_spin.setValue(int(values.get("rest_min", SETTINGS_DEFAULTS["rest_min"])))
            self.ui_scale_spin.setValue(int(values.get("ui_scale", SETTINGS_DEFAULTS["ui_scale"])))
            sound_on = bool(values.get("sound_enabled", SETTINGS_DEFAULTS["sound_enabled"]))
            self.sound_check.setChecked(sound_on)
            v_vol = int(values.get("voice_volume", SETTINGS_DEFAULTS["voice_volume"]))
            s_vol = int(values.get("sfx_volume", SETTINGS_DEFAULTS["sfx_volume"]))
            self.voice_slider.setValue(v_vol)
            self.sfx_slider.setValue(s_vol)
            self._voice_val_lbl.setText(f"{v_vol}%")
            self._sfx_val_lbl.setText(f"{s_vol}%")
            self.voice_slider.setEnabled(sound_on)
            self.sfx_slider.setEnabled(sound_on)
            self.briefing_check.setChecked(bool(values.get("morning_briefing", SETTINGS_DEFAULTS["morning_briefing"])))
            self.auto_reward_check.setChecked(bool(values.get("auto_stat_rewards", SETTINGS_DEFAULTS["auto_stat_rewards"])))
            enabled = set(values.get("enabled_tabs", RADIAL_TAB_KEYS))
            for key, cb in self.tab_checks.items():
                cb.setChecked(key in enabled)
            if self._get_on_top is not None:
                self.on_top_check.setChecked(bool(self._get_on_top()))
            if self._get_autostart is not None:
                self.autostart_check.setChecked(bool(self._get_autostart()))
        finally:
            self._filling = False

    def refresh(self) -> None:
        if self._saving:
            return
        self._fill(self.store.data)
        self.status.setText("")

    def _reset(self) -> None:
        self._fill(SETTINGS_DEFAULTS)
        self.status.setText("Defaults loaded. Click SAVE to apply.")

    def _save(self) -> None:
        self._saving = True
        try:
            for w in (self.bed_edit, self.wake_edit, self.curfew_interval,
                      self.event_window, self.event_interval, self.focus_spin,
                      self.rest_spin, self.ui_scale_spin):
                w.interpretText()
            chosen_tabs = [k for k in RADIAL_TAB_KEYS if self.tab_checks[k].isChecked()]
            if not chosen_tabs:
                self.status.setText("Keep at least one radial menu tab enabled.")
                return
            b, w = self.bed_edit.time(), self.wake_edit.time()
            v_vol = int(self.voice_slider.value())
            s_vol = int(self.sfx_slider.value())
            self.store.set(
                bedtime=f"{b.hour():02d}:{b.minute():02d}",
                wake=f"{w.hour():02d}:{w.minute():02d}",
                curfew_enabled=self.curfew_check.isChecked(),
                curfew_interval_sec=self.curfew_interval.value(),
                event_window_min=self.event_window.value(),
                event_interval_min=self.event_interval.value(),
                focus_min=self.focus_spin.value(),
                rest_min=self.rest_spin.value(),
                ui_scale=self.ui_scale_spin.value(),
                sound_enabled=self.sound_check.isChecked(),
                sound_volume=max(v_vol, s_vol),
                voice_volume=v_vol,
                sfx_volume=s_vol,
                morning_briefing=self.briefing_check.isChecked(),
                auto_stat_rewards=self.auto_reward_check.isChecked(),
                enabled_tabs=chosen_tabs,
            )
            self.saved.emit()
            if self._get_on_top is not None and self.on_top_check.isChecked() != bool(self._get_on_top()):
                self.always_on_top_changed.emit(self.on_top_check.isChecked())
            if self._get_autostart is not None and self.autostart_check.isChecked() != bool(self._get_autostart()):
                self.autostart_changed.emit(self.autostart_check.isChecked())
            if self.store.last_error:
                self.status.setText("Save failed: " + self.store.last_error)
            elif b == w:
                self.status.setText("Saved, but bedtime and wake times match—bedtime reminders won't trigger.")
            else:
                self.status.setText("Saved and applied.")
        finally:
            self._saving = False


# ---------------------------------------------------------------------------
# EVENTS DIALOG (7-day Google Calendar schedule + Morgana next-event briefing)
# ---------------------------------------------------------------------------
class _MorganaBriefingFrame(QFrame):
    """Persona 5 slanted obsidian/crimson briefing banner where Morgana announces the next event."""

    def __init__(self, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._accent = RED

    def set_accent(self, color: QColor) -> None:
        if color != self._accent:
            self._accent = color
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        sc = self._scale
        # Obsidian base with subtle crimson glow border
        p.setPen(QPen(self._accent, 1.6))
        p.setBrush(P5_CARD)
        p.drawRoundedRect(r, 10.0, 10.0)
        # Slanted P5 left slash accent
        clip = QPainterPath()
        clip.addRoundedRect(r, 10.0, 10.0)
        p.setClipPath(clip)
        slash = QPolygonF([
            QPointF(r.left(), r.top()),
            QPointF(r.left() + 26.0 * sc, r.top()),
            QPointF(r.left() + 14.0 * sc, r.bottom()),
            QPointF(r.left(), r.bottom()),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawPolygon(slash)
        p.end()


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class EventsDialog(P5FluidPanel):
    """7-day weekly calendar window with local events, Google Calendar, task/note deadlines, and Morgana briefing."""

    refresh_requested = pyqtSignal(bool)     # True = allow interactive OAuth sign-in
    local_events_changed = pyqtSignal()
    open_tasks = pyqtSignal()
    open_notes = pyqtSignal()
    open_settings = pyqtSignal()

    def __init__(self, local_events: LocalEventStore | None = None,
                 on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("EVENTS", QSize(645, 670), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.local_store = local_events
        self._result: CalendarResult = CalendarResult([], "ok")
        self._selected_day: date | None = None    # None = ALL 7 DAYS
        self._loading = False
        self._editing_local_id: str | None = None
        self._undo_event: dict | None = None
        self._day_btns: dict[date | None, P5FluidButton] = {}

        # Header quick actions
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("subhead")
        self.header_bar.addWidget(self._count_lbl)

        self._new_ev_btn = self._btn("EVENT", variant="primary", icon="plus", display=True, px=10)
        self._new_ev_btn.setToolTip("Create a local event (works offline without Google Calendar)")
        self._new_ev_btn.clicked.connect(self._toggle_new_event_form)
        if self.local_store is None:
            self._new_ev_btn.hide()
        self.header_bar.addWidget(self._new_ev_btn)

        self._hdr_refresh = self._btn("", variant="soft", icon="refresh")
        self._hdr_refresh.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        self._hdr_refresh.setToolTip("Refresh calendar (F5)")
        self._hdr_refresh.clicked.connect(lambda _c=False: self.refresh_requested.emit(True))
        self.header_bar.addWidget(self._hdr_refresh)

        self._notes_btn = self._btn("NOTES", variant="soft", display=True, px=10)
        self._notes_btn.clicked.connect(lambda _c=False: self.open_notes.emit())
        self.header_bar.addWidget(self._notes_btn)

        self._tasks_btn = self._btn("TASKS", variant="soft", display=True, px=10)
        self._tasks_btn.clicked.connect(lambda _c=False: self.open_tasks.emit())
        self.header_bar.addWidget(self._tasks_btn)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Dedicated Morgana next-event briefing card
        self._brief_card = _MorganaBriefingFrame(scale=sc)
        bl = QHBoxLayout(self._brief_card)
        bl.setContentsMargins(_sc(24, sc, 18), _sc(10, sc, 8), _sc(12, sc, 9), _sc(10, sc, 8))
        bl.setSpacing(_sc(12, sc, 8))

        av_lbl = QLabel()
        av_lbl.setPixmap(_morgana_avatar(_sc(50, sc, 38)))
        av_lbl.setFixedSize(_sc(50, sc, 38), _sc(50, sc, 38))
        bl.addWidget(av_lbl, 0, Qt.AlignmentFlag.AlignTop)

        speech_col = QVBoxLayout()
        speech_col.setSpacing(_sc(4, sc, 3))
        top_meta = QHBoxLayout()
        top_meta.setSpacing(_sc(8, sc, 5))

        name_lbl = QLabel("MORGANA")
        name_lbl.setStyleSheet(
            f'background: #0B0B0B; color: #FFFFFF; border: 1.5px solid #FFFFFF; '
            f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
            f'font-family: "Anton", "Barlow Condensed", sans-serif; font-size: {_sc(12, sc, 10)}px;'
        )
        top_meta.addWidget(name_lbl)

        self._brief_tag = QLabel("NEXT UP")
        self._brief_tag.setStyleSheet(
            f'background: #360A10; color: #FF5E6B; border: 1px solid #731823; '
            f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
            f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
            f'font-size: {_sc(11, sc, 9)}px;'
        )
        top_meta.addWidget(self._brief_tag)
        top_meta.addStretch(1)

        self._signin_btn = self._btn("SIGN IN", variant="primary", display=True, px=10)
        self._signin_btn.setFixedHeight(_sc(24, sc, 20))
        self._signin_btn.clicked.connect(lambda _c=False: self.refresh_requested.emit(True))
        self._signin_btn.hide()
        top_meta.addWidget(self._signin_btn)
        speech_col.addLayout(top_meta)

        self._brief_text = QLabel("Checking your schedule for the week...")
        self._brief_text.setWordWrap(True)
        self._brief_text.setStyleSheet(
            f'color: #FFFFFF; background: transparent; '
            f'font-family: "Arsenal", "Optima", "Segoe UI", sans-serif; '
            f'font-size: {_sc(14, sc, 11)}px; font-weight: bold;'
        )
        speech_col.addWidget(self._brief_text)
        bl.addLayout(speech_col, 1)
        self.body.addWidget(self._brief_card)

        # Collapsible Local Event Creator / Editor card
        self._ev_form_box = _make_card("subcard")
        ef = QVBoxLayout(self._ev_form_box)
        ef.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        ef.setSpacing(_sc(6, sc, 4))

        ef_r1 = QHBoxLayout()
        ef_r1.setSpacing(6)
        self._ev_title_edit = QLineEdit()
        self._ev_title_edit.setPlaceholderText("Local event title (e.g. Study Group, Dentist, Game Night)...")
        self._ev_title_edit.returnPressed.connect(self._save_local_event)
        ef_r1.addWidget(self._ev_title_edit, 1)

        self._ev_all_day = P5CheckBox("All day", scale=sc)
        self._ev_all_day.toggled.connect(self._on_ev_all_day_toggled)
        ef_r1.addWidget(self._ev_all_day)
        ef.addLayout(ef_r1)

        ef_r2 = QHBoxLayout()
        ef_r2.setSpacing(6)
        st_lbl = QLabel("Start")
        st_lbl.setObjectName("tip")
        ef_r2.addWidget(st_lbl)
        self._ev_start = QDateTimeEdit()
        self._ev_start.setCalendarPopup(True)
        self._ev_start.setDisplayFormat("dd MMM HH:mm")
        self._ev_start.setMinimumHeight(_sc(28, sc, 24))
        self._ev_start.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ev_start.dateTimeChanged.connect(self._on_ev_start_changed)
        ef_r2.addWidget(self._ev_start, 1)

        self._ev_end_lbl = QLabel("End")
        self._ev_end_lbl.setObjectName("tip")
        ef_r2.addWidget(self._ev_end_lbl)
        self._ev_end = QDateTimeEdit()
        self._ev_end.setCalendarPopup(True)
        self._ev_end.setDisplayFormat("dd MMM HH:mm")
        self._ev_end.setMinimumHeight(_sc(28, sc, 24))
        self._ev_end.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ef_r2.addWidget(self._ev_end, 1)
        ef.addLayout(ef_r2)

        ef_r3 = QHBoxLayout()
        ef_r3.setSpacing(_sc(5, sc, 4))
        for text, kind in (("+1h", "1h"), ("23:59", "eod"), ("Tmrw 9:00", "tomorrow")):
            pb = self._btn(text, variant="gold" if kind == "eod" else "soft", px=10)
            pb.setFixedHeight(_sc(24, sc, 20))
            pb.clicked.connect(lambda _c=False, k=kind: self._apply_ev_preset(k))
            ef_r3.addWidget(pb)
        self._ev_loc_edit = QLineEdit()
        self._ev_loc_edit.setPlaceholderText("Location (optional)...")
        self._ev_loc_edit.setFixedHeight(_sc(26, sc, 22))
        self._ev_loc_edit.returnPressed.connect(self._save_local_event)
        ef_r3.addWidget(self._ev_loc_edit, 1)
        ef.addLayout(ef_r3)

        ef_r4 = QHBoxLayout()
        ef_r4.setSpacing(6)
        self._ev_err = QLabel("")
        self._ev_err.setStyleSheet(f'color: #FF5E6B; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        ef_r4.addWidget(self._ev_err, 1)

        cancel_ev = self._btn("CANCEL", variant="soft", display=True, px=9)
        cancel_ev.setFixedHeight(_sc(26, sc, 22))
        cancel_ev.clicked.connect(self._close_event_form)
        ef_r4.addWidget(cancel_ev)

        self._ev_save_btn = self._btn("SAVE EVENT", variant="primary", icon="plus", display=True, px=10)
        self._ev_save_btn.setFixedHeight(_sc(26, sc, 22))
        self._ev_save_btn.clicked.connect(self._save_local_event)
        ef_r4.addWidget(self._ev_save_btn)
        ef.addLayout(ef_r4)

        self._ev_form_box.setVisible(False)
        self.body.addWidget(self._ev_form_box)

        # 7-day filter strip
        self._day_strip = QHBoxLayout()
        self._day_strip.setSpacing(_sc(4, sc, 3))
        self.body.addLayout(self._day_strip)

        # Weekly timeline inside dark card
        list_card = _make_card()
        lc_lay = QVBoxLayout(list_card)
        lc_lay.setContentsMargins(_sc(8, sc, 6), _sc(8, sc, 6), _sc(6, sc, 4), _sc(8, sc, 6))
        lc_lay.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_body = QWidget()
        self._scroll_body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._days_lay = QVBoxLayout(self._scroll_body)
        self._days_lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self._days_lay.setContentsMargins(2, 2, _sc(6, sc, 4), 4)
        self._days_lay.setSpacing(_sc(8, sc, 5))
        self._scroll.setWidget(self._scroll_body)
        lc_lay.addWidget(self._scroll)
        self.body.addWidget(list_card, 1)

        # Undo strip for deleted local events
        self._undo = UndoBar(scale=sc)
        self._undo.undo_clicked.connect(self._undo_delete_local)
        self.body.addWidget(self._undo)

        # Footer
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self._foot_tip = QLabel("7-day calendar, local events & deadlines")
        self._foot_tip.setObjectName("tip")
        self._foot_tip.setWordWrap(True)
        foot.addWidget(self._foot_tip, 1)

        self._foot_refresh = self._btn("REFRESH", variant="soft", icon="refresh", display=True, px=10)
        self._foot_refresh.setFixedHeight(_sc(28, sc, 24))
        self._foot_refresh.clicked.connect(lambda _c=False: self.refresh_requested.emit(True))
        foot.addWidget(self._foot_refresh)
        self.body.addLayout(foot)

        QShortcut(QKeySequence("F5"), self).activated.connect(lambda: self.refresh_requested.emit(True))
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(lambda: self.refresh_requested.emit(True))
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._toggle_new_event_form)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())

    # -- Local Event Form ----------------------------------------------------
    def _default_ev_times(self) -> tuple[datetime, datetime]:
        now = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if self._selected_day is not None and self._selected_day != now.date():
            now = datetime(self._selected_day.year, self._selected_day.month, self._selected_day.day, 10, 0)
        return now, now + timedelta(hours=1)

    def _toggle_new_event_form(self) -> None:
        if self.local_store is None:
            return
        if self._ev_form_box.isVisible() and self._editing_local_id is None:
            self._close_event_form()
            return
        self._editing_local_id = None
        st, en = self._default_ev_times()
        self._ev_title_edit.clear()
        self._ev_loc_edit.clear()
        self._ev_all_day.setChecked(False)
        self._ev_start.setDateTime(_to_qdt(st))
        self._ev_end.setDateTime(_to_qdt(en))
        self._ev_err.setText("")
        self._ev_save_btn.setText("SAVE EVENT")
        self._ev_form_box.setVisible(True)
        self._ev_title_edit.setFocus()

    def _edit_local_event(self, raw_id: str) -> None:
        if self.local_store is None:
            return
        ev = self.local_store.get(raw_id)
        if ev is None:
            return
        self._editing_local_id = raw_id
        st = parse_dt(ev.get("start")) or datetime.now()
        en = parse_dt(ev.get("end")) or (st + timedelta(hours=1))
        self._ev_title_edit.setText(ev.get("title", ""))
        self._ev_loc_edit.setText(ev.get("location", ""))
        self._ev_all_day.setChecked(bool(ev.get("all_day")))
        self._ev_start.setDateTime(_to_qdt(st))
        self._ev_end.setDateTime(_to_qdt(en))
        self._ev_err.setText("")
        self._ev_save_btn.setText("UPDATE EVENT")
        self._ev_form_box.setVisible(True)
        self._ev_title_edit.setFocus()

    def _close_event_form(self) -> None:
        self._editing_local_id = None
        self._ev_err.setText("")
        self._ev_form_box.setVisible(False)

    def _on_ev_all_day_toggled(self, checked: bool) -> None:
        self._ev_start.setDisplayFormat("dd MMM yyyy" if checked else "dd MMM HH:mm")
        self._ev_end.setVisible(not checked)
        self._ev_end_lbl.setVisible(not checked)

    def _on_ev_start_changed(self, qdt: QDateTime) -> None:
        if not self._ev_all_day.isChecked() and self._ev_end.dateTime() <= qdt:
            self._ev_end.setDateTime(qdt.addSecs(3600))

    def _apply_ev_preset(self, kind: str) -> None:
        now = datetime.now().replace(second=0, microsecond=0)
        self._ev_all_day.setChecked(False)
        if kind == "1h":
            st = now + timedelta(hours=1)
            en = st + timedelta(hours=1)
        elif kind == "eod":
            st = now.replace(hour=23, minute=59)
            if st <= now:
                st += timedelta(days=1)
            en = st + timedelta(minutes=30)
        else:
            st = (now + timedelta(days=1)).replace(hour=9, minute=0)
            en = st + timedelta(hours=1)
        self._ev_start.setDateTime(_to_qdt(st))
        self._ev_end.setDateTime(_to_qdt(en))

    def _save_local_event(self) -> None:
        if self.local_store is None:
            return
        title = self._ev_title_edit.text().strip()
        if not title:
            self._ev_err.setText("Please enter an event title.")
            self._ev_title_edit.setFocus()
            return
        all_day = self._ev_all_day.isChecked()
        st = _from_qdt(self._ev_start.dateTime())
        en = None if all_day else _from_qdt(self._ev_end.dateTime())
        loc = self._ev_loc_edit.text().strip()
        if self._editing_local_id:
            self.local_store.update(self._editing_local_id, title, st, en, all_day=all_day, location=loc)
        else:
            self.local_store.add(title, st, en, all_day=all_day, location=loc)
        self._close_event_form()
        self.local_events_changed.emit()

    def _delete_local_event(self, raw_id: str) -> None:
        if self.local_store is None:
            return
        ev = self.local_store.get(raw_id)
        if ev is None:
            return
        self._undo_event = dict(ev)
        self.local_store.delete(raw_id)
        if self._editing_local_id == raw_id:
            self._close_event_form()
        self.local_events_changed.emit()
        self._undo.trigger(f"Deleted event '{ev.get('title', '')[:30]}'.")

    def _undo_delete_local(self) -> None:
        if self.local_store is not None and self._undo_event is not None:
            self.local_store.restore(self._undo_event)
            self._undo_event = None
            self.local_events_changed.emit()

    # -- View state ----------------------------------------------------------
    def set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        if loading:
            self._count_lbl.setText("SYNCING...")
            self._brief_tag.setText("SYNCING")
            self._brief_text.setText("Hang on, I'm checking your schedule for the next 7 days...")
            self._signin_btn.hide()

    def set_result(self, result: CalendarResult) -> None:
        self._loading = False
        self._result = result
        self.refresh()

    def _select_day(self, day: date | None) -> None:
        self._selected_day = day
        self._rebuild_view()

    def refresh(self) -> None:
        if self._loading:
            return
        self._rebuild_view()

    def _rebuild_view(self) -> None:
        sc = self._ui_scale
        now = datetime.now().astimezone()
        res = self._result
        grouped = events_by_day(res.events, now=now, days=7)
        valid_days = {d for d, _ in grouped}
        if self._selected_day is not None and self._selected_day not in valid_days:
            self._selected_day = None

        # Header count & footer note
        all_shown = [ev for _, evs in grouped for ev in evs]
        total = len(all_shown)
        n_deadlines = sum(1 for ev in all_shown if ev.source in ("note", "task"))
        n_events = total - n_deadlines
        due_suffix = (f" · {n_deadlines} DUE" if n_deadlines else "")
        if res.status == "error":
            self._count_lbl.setText(f"OFFLINE · {total}")
            self._foot_tip.setText(f"Google offline ({res.message}) · Local events & deadlines still active.")
        elif res.status == "auth_needed":
            self._count_lbl.setText(f"SIGN IN · {total}")
            self._foot_tip.setText("Google token expired. Click SIGN IN to reconnect · Local events & deadlines still active.")
        elif res.is_dummy:
            self._count_lbl.setText(f"SAMPLE · {total}")
            self._foot_tip.setText("Click + EVENT to add local events (replaces sample week) or add credentials.json for Google.")
        elif res.status in ("no_credentials", "no_libs"):
            word = "EVENT" if n_events == 1 else "EVENTS"
            self._count_lbl.setText(f"{n_events} {word}{due_suffix}")
            self._foot_tip.setText("Local events + Task & Note deadlines · Ctrl+N for new event · add credentials.json for Google.")
        else:
            word = "EVENT" if n_events == 1 else "EVENTS"
            self._count_lbl.setText(f"{n_events} {word}{due_suffix}")
            self._foot_tip.setText("Google + Local events + Task & Note deadlines · F5 to refresh · Ctrl+N to add")

        # Morgana briefing banner
        tag, speech = morgana_briefing(res, now=now)
        self._brief_tag.setText(tag)
        self._brief_text.setText(speech)
        self._signin_btn.setVisible(res.status == "auth_needed")
        self._foot_refresh.set_variant("primary" if res.status in ("error", "auth_needed") else "soft")
        if "HAPPENING NOW" in tag or "OVERDUE" in tag:
            self._brief_card.set_accent(RED)
            self._brief_tag.setStyleSheet(
                f'background: #E60012; color: #FFFFFF; border: 1px solid #FFFFFF; '
                f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
                f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
                f'font-size: {_sc(11, sc, 9)}px;'
            )
        elif "CLEAR" in tag:
            self._brief_card.set_accent(P5_MINT_FG)
            self._brief_tag.setStyleSheet(
                f'background: #10281E; color: #5CE09A; border: 1px solid #245943; '
                f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
                f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
                f'font-size: {_sc(11, sc, 9)}px;'
            )
        elif "DEADLINE" in tag:
            self._brief_card.set_accent(GOLD)
            self._brief_tag.setStyleSheet(
                f'background: #2B2208; color: #FAD02C; border: 1px solid #6C5514; '
                f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
                f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
                f'font-size: {_sc(11, sc, 9)}px;'
            )
        else:
            self._brief_card.set_accent(GOLD if "SAMPLE" in tag else RED)
            self._brief_tag.setStyleSheet(
                f'background: #360A10; color: #FF5E6B; border: 1px solid #731823; '
                f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
                f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
                f'font-size: {_sc(11, sc, 9)}px;'
            )

        # Rebuild 7-day filter strip
        _clear_layout(self._day_strip)
        self._day_btns.clear()
        all_btn = self._btn("ALL 7D", variant="soft", display=True, px=9)
        all_btn.setFixedHeight(_sc(26, sc, 22))
        all_btn.set_active(self._selected_day is None)
        all_btn.clicked.connect(lambda _c=False: self._select_day(None))
        self._day_strip.addWidget(all_btn)
        self._day_btns[None] = all_btn

        for d, evs in grouped:
            short = f"{d:%a} {d.day}"
            base_var = "gold" if evs else "soft"
            btn = self._btn(short.upper(), variant=base_var, display=True, px=9)
            btn.setFixedHeight(_sc(26, sc, 22))
            btn.set_active(self._selected_day == d)
            n_ev = len(evs)
            btn.setToolTip(f"{d:%A, %d %b} — {n_ev} item" + ("" if n_ev == 1 else "s"))
            btn.clicked.connect(lambda _c=False, day=d: self._select_day(day))
            self._day_strip.addWidget(btn, 1)
            self._day_btns[d] = btn

        # Find next upcoming event/deadline for card highlighting
        timed = sorted((e for e in res.events if not e.all_day), key=lambda e: e.start)
        next_ev = next((e for e in timed if e.start > now), None)

        # Rebuild weekly day sections
        _clear_layout(self._days_lay)
        shown_days = grouped if self._selected_day is None else [
            (d, evs) for d, evs in grouped if d == self._selected_day
        ]
        today = now.date()
        for d, evs in shown_days:
            diff = (d - today).days
            if diff == 0:
                title = f"TODAY · {d:%a %d %b}".upper()
                title_col = "#FF4B55"
            elif diff == 1:
                title = f"TOMORROW · {d:%a %d %b}".upper()
                title_col = "#FAD02C"
            else:
                title = f"{d:%A · %d %b}".upper()
                title_col = "#F4F4F8"

            hdr = QHBoxLayout()
            hdr.setContentsMargins(2, _sc(4, sc, 2), 2, 0)
            dlbl = QLabel(title)
            dlbl.setMinimumHeight(_sc(20, sc, 16))
            dlbl.setStyleSheet(
                f'color: {title_col}; font-family: "Barlow Condensed", "Anton", sans-serif; '
                f'font-size: {_sc(14, sc, 11)}px; font-weight: 800;'
            )
            hdr.addWidget(dlbl)
            hdr.addStretch(1)
            cnt_txt = f"{len(evs)} ITEM" + ("" if len(evs) == 1 else "S") if evs else "CLEAR"
            clbl = QLabel(cnt_txt)
            clbl.setMinimumHeight(_sc(18, sc, 14))
            clbl.setStyleSheet(
                f'color: {"#FAD02C" if evs else "#66667A"}; '
                f'font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
            )
            hdr.addWidget(clbl)
            self._days_lay.addLayout(hdr)

            if not evs:
                empty = QLabel("No events or deadlines." if self._selected_day is None
                               else "Your schedule is clear on this day!")
                empty.setMinimumHeight(_sc(24, sc, 19))
                empty.setStyleSheet(
                    f'color: #6B6B7F; padding: {_sc(4, sc, 2)}px {_sc(8, sc, 6)}px; '
                    f'font-size: {_sc(12, sc, 10)}px; font-style: italic;'
                )
                self._days_lay.addWidget(empty)
                continue

            for ev in evs:
                is_deadline = ev.source in ("note", "task")
                is_local = ev.source == "local"
                is_overdue = is_deadline and ev.start <= now
                is_now = (not is_deadline and not ev.all_day and ev.start <= now and (ev.end is None or ev.end > now))
                is_next = (next_ev is not None and ev is next_ev)
                is_past = (not is_deadline and not ev.all_day and (ev.end or ev.start) <= now and not is_now)

                row_card = _make_card("subcard")
                row_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                row_card.setMinimumHeight(_sc(42, sc, 34))
                if is_now or is_overdue:
                    row_card.setStyleSheet(
                        "QFrame#subcard { background-color: #2A0C11; border: 1.5px solid #E60012; border-radius: 8px; }"
                    )
                elif is_next or is_deadline:
                    row_card.setStyleSheet(
                        "QFrame#subcard { background-color: #221B0A; border: 1.5px solid #6C5514; border-radius: 8px; }"
                    )
                rl = QHBoxLayout(row_card)
                rl.setContentsMargins(_sc(10, sc, 7), _sc(7, sc, 5), _sc(10, sc, 7), _sc(7, sc, 5))
                rl.setSpacing(_sc(7, sc, 5))

                # Time column
                if ev.all_day:
                    time_txt = "ALL DAY"
                    time_style = (
                        f'background: #2B2208; color: #FAD02C; border: 1px solid #6C5514; '
                        f'border-radius: 4px; padding: 2px 6px; '
                        f'font-family: "Barlow Condensed", sans-serif; font-weight: 800; '
                        f'font-size: {_sc(12, sc, 10)}px;'
                    )
                elif is_deadline:
                    time_txt = f"DUE {ev.start:%H:%M}" if ev.start.date() == d else f"{ev.start:%d %b %H:%M}"
                    time_style = (
                        f'color: {"#FF5E6B" if is_overdue else "#FAD02C"}; '
                        f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
                        f'font-size: {_sc(13, sc, 10)}px;'
                    )
                else:
                    end_s = f"–{ev.end:%H:%M}" if ev.end else ""
                    time_txt = f"{ev.start:%H:%M}{end_s}"
                    time_style = (
                        f'color: {"#FF5E6B" if is_now else ("#FAD02C" if is_next else ("#6E6E80" if is_past else "#D2D2E0"))}; '
                        f'font-family: "Barlow Condensed", "Arsenal", sans-serif; font-weight: 800; '
                        f'font-size: {_sc(14, sc, 11)}px;'
                    )
                tlbl = QLabel(time_txt)
                tlbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                tlbl.setFixedWidth(_sc(92, sc, 74))
                tlbl.setStyleSheet(time_style)
                rl.addWidget(tlbl)

                # Source pill for Local Events, Task Deadlines, and Note Deadlines
                if ev.source in ("local", "task", "note"):
                    pill_map = {
                        "local": ("LOCAL", "#10281E", "#5CE09A", "#245943"),
                        "task":  ("TASK",  "#2B2208", "#FAD02C", "#6C5514"),
                        "note":  ("NOTE",  "#360A10", "#FF7882", "#731823"),
                    }
                    ptxt, pbg, pfg, pbd = pill_map[ev.source]
                    src_lbl = QLabel(ptxt)
                    src_lbl.setStyleSheet(
                        f'background: {pbg}; color: {pfg}; border: 1px solid {pbd}; '
                        f'border-radius: 4px; padding: 1px {_sc(5, sc, 4)}px; '
                        f'font-family: "Barlow Condensed", sans-serif; font-weight: 800; '
                        f'font-size: {_sc(10, sc, 8)}px;'
                    )
                    rl.addWidget(src_lbl)

                # Title (+ optional location subtitle)
                t_col = QVBoxLayout()
                t_col.setContentsMargins(0, 0, 0, 0)
                t_col.setSpacing(1)
                elbl = QLabel(ev.title)
                elbl.setWordWrap(True)
                elbl.setStyleSheet(
                    f'color: {"#828294" if is_past else "#FFFFFF"}; '
                    f'font-family: "Arsenal", "Optima", "Segoe UI", sans-serif; '
                    f'font-size: {_sc(14, sc, 11)}px; font-weight: bold;'
                )
                t_col.addWidget(elbl)
                if ev.location:
                    loc_lbl = QLabel(ev.location)
                    loc_lbl.setStyleSheet(f'color: #9292A4; font-size: {_sc(11, sc, 9)}px;')
                    t_col.addWidget(loc_lbl)
                rl.addLayout(t_col, 1)

                # Status chip
                badge_txt, badge_css = "", ""
                if is_overdue:
                    badge_txt = "OVERDUE"
                    badge_css = "background: #E60012; color: #FFFFFF; border: none;"
                elif is_now:
                    badge_txt = "NOW"
                    badge_css = "background: #E60012; color: #FFFFFF; border: none;"
                elif is_next:
                    badge_txt = f"IN {human_delta(ev.start - now).upper()}"
                    badge_css = "background: #2B2208; color: #FAD02C; border: 1px solid #6C5514;"
                elif is_past:
                    badge_txt = "DONE"
                    badge_css = "background: #1B1B24; color: #7C7C90; border: 1px solid #2E2E3B;"
                if badge_txt:
                    blbl = QLabel(badge_txt)
                    blbl.setStyleSheet(
                        f'{badge_css} border-radius: 4px; padding: 2px {_sc(7, sc, 5)}px; '
                        f'font-family: "Barlow Condensed", sans-serif; font-weight: 800; '
                        f'font-size: {_sc(11, sc, 9)}px;'
                    )
                    rl.addWidget(blbl)

                # Local event quick Edit & Delete actions
                if is_local and self.local_store is not None and ev.id.startswith("local:"):
                    raw_id = ev.id[len("local:"):]
                    eb = self._btn("EDIT", variant="soft", display=True, px=9)
                    eb.setFixedHeight(_sc(22, sc, 19))
                    eb.clicked.connect(lambda _c=False, eid=raw_id: self._edit_local_event(eid))
                    rl.addWidget(eb)

                    db_btn = self._btn("DEL", variant="danger", display=True, px=9)
                    db_btn.setFixedHeight(_sc(22, sc, 19))
                    db_btn.clicked.connect(lambda _c=False, eid=raw_id: self._delete_local_event(eid))
                    rl.addWidget(db_btn)

                self._days_lay.addWidget(row_card)

        self._days_lay.addStretch(1)


# ---------------------------------------------------------------------------
# SOCIAL STATS DIALOG (Persona 5 Royal 5-stat pentagon + daily time blocks)
# ---------------------------------------------------------------------------
class SocialPentagonWidget(QWidget):
    """Persona 5 Royal style 5-point radar star chart for Knowledge/Charm/Kindness/Proficiency/Guts."""

    stat_clicked = pyqtSignal(str)

    def __init__(self, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._totals: dict[str, int] = {k: 0 for k in SOCIAL_STAT_KEYS}
        self._anim_pts: dict[str, Spring] = {k: Spring(0.0, 120.0, 14.0) for k in SOCIAL_STAT_KEYS}
        self._pulse = Spring(0.0, 180.0, 11.0)
        self._rank_flash = Spring(0.0, 140.0, 11.0)
        self._selected = "knowledge"
        self._vertex_pts: dict[str, QPointF] = {}
        self._ticker = FrameTicker(self._frame, self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(_sc(240, scale, 195))

    def set_data(self, totals: dict[str, int], selected: str, animate: bool = False) -> None:
        self._totals = dict(totals)
        self._selected = selected
        for k in SOCIAL_STAT_KEYS:
            val = float(max(0, int(totals.get(k, 0))))
            if animate:
                self._anim_pts[k].target = val
            else:
                self._anim_pts[k].snap(val)
        if animate:
            self._pulse.value = 1.0
            self._pulse.target = 0.0
            self._ticker.start()
        self.update()

    def trigger_rank_up_flash(self) -> None:
        self._rank_flash.value = 1.0
        self._rank_flash.velocity = 0.0
        self._rank_flash.target = 0.0
        self._ticker.start()

    def _frame(self, dt: float) -> bool:
        moving = self._pulse.step(dt) | self._rank_flash.step(dt)
        for sp in self._anim_pts.values():
            moving |= sp.step(dt)
        self.update()
        return moving

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self._vertex_pts:
            return
        pos = e.position()
        best_k, best_d = "", 1e9
        for k, pt in self._vertex_pts.items():
            d = math.hypot(pos.x() - pt.x(), pos.y() - pt.y())
            if d < best_d:
                best_k, best_d = k, d
        if best_k and best_d <= 78.0 * self._scale:
            play_ui("select")
            self.stat_clicked.emit(best_k)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        sc = self._scale
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)

        # Obsidian P5 card background + subtle slanted crimson motif
        p.setPen(QPen(P5_BORDER, 1.2))
        p.setBrush(P5_CARD)
        p.drawRoundedRect(rect, 10.0, 10.0)

        clip = QPainterPath()
        clip.addRoundedRect(rect, 10.0, 10.0)
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(230, 0, 18, 22))
        p.drawPolygon(QPolygonF([
            QPointF(rect.left() + rect.width() * 0.62, rect.top()),
            QPointF(rect.right(), rect.top()),
            QPointF(rect.right(), rect.bottom()),
            QPointF(rect.left() + rect.width() * 0.34, rect.bottom()),
        ]))
        p.setClipping(False)

        cx = rect.center().x()
        cy = rect.center().y() + 6.0 * sc
        radius = min(rect.width() * 0.255, rect.height() * 0.31)
        angles = [-math.pi / 2.0 + i * (2.0 * math.pi / 5.0) for i in range(5)]

        # 5 concentric pentagon guide rings (representing Lv 1..5 scale)
        for ring in range(1, 6):
            frac = ring / 5.0
            poly = QPolygonF([
                QPointF(cx + math.cos(a) * radius * frac, cy + math.sin(a) * radius * frac)
                for a in angles
            ])
            p.setPen(QPen(P5_BORDER_HI if ring == 5 else P5_BORDER, 1.4 if ring == 5 else 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(poly)

        # Spokes
        p.setPen(QPen(P5_BORDER, 1.0))
        for a in angles:
            p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * radius, cy + math.sin(a) * radius))

        # Filled stat polygon mapped directly to Persona 5 Ranks 1..5 rings
        pulse = clamp(self._pulse.value)
        burst = clamp(self._rank_flash.value)
        data_pts: list[QPointF] = []
        self._vertex_pts.clear()
        for i, key in enumerate(SOCIAL_STAT_KEYS):
            a = angles[i]
            pts = max(0.0, self._anim_pts[key].value)
            frac = stat_pentagon_fraction(pts)
            if key == self._selected and pulse > 0.01:
                frac = min(1.05, frac + 0.05 * pulse)
            data_pts.append(QPointF(cx + math.cos(a) * radius * frac, cy + math.sin(a) * radius * frac))
            self._vertex_pts[key] = QPointF(cx + math.cos(a) * radius * 1.15, cy + math.sin(a) * radius * 1.15)

        if burst > 0.02:
            b_rad = radius * (0.45 + 0.72 * (1.0 - burst))
            b_poly = QPolygonF([
                QPointF(cx + math.cos(a) * b_rad, cy + math.sin(a) * b_rad)
                for a in angles
            ])
            p.setPen(QPen(QColor(245, 196, 0, int(230 * burst)), 3.2 * sc))
            p.setBrush(QColor(245, 196, 0, int(55 * burst)))
            p.drawPolygon(b_poly)

        p.setPen(QPen(WHITE if burst > 0.25 else GOLD, 2.2 + 1.2 * pulse))
        p.setBrush(QColor(230, 0, 18, int(128 + 65 * pulse)))
        p.drawPolygon(QPolygonF(data_pts))

        # Vertex dots & crisp Persona 5 labels around pentagon
        lbl_font = p5_font(_sc(13, sc, 10), italic=False)
        lv_font = p5_font(_sc(11, sc, 9), italic=False)
        fm_l = QFontMetrics(lbl_font)
        fm_v = QFontMetrics(lv_font)

        for i, key in enumerate(SOCIAL_STAT_KEYS):
            a = angles[i]
            dpt = data_pts[i]
            is_sel = key == self._selected
            lv = stat_level(self._totals.get(key, 0))
            is_mastery = lv > len(STAT_RANK_THRESHOLDS)
            p.setPen(QPen(WHITE if is_sel else GOLD, 2.0 if is_sel else 1.4))
            p.setBrush(GOLD if is_mastery else (RED if is_sel else BLACK))
            dot_r = (5.0 if is_sel else 3.8) * sc
            p.drawEllipse(dpt, dot_r, dot_r)

            # Label placement around vertex
            meta = SOCIAL_STATS[key]
            name = meta["label"].upper()
            lv_txt = f"LV {lv} ★" if is_mastery else f"LV {lv}"
            nw = fm_l.horizontalAdvance(name)
            vw = fm_v.horizontalAdvance(lv_txt) + int(10 * sc)
            lh = fm_l.height()

            vx = cx + math.cos(a) * (radius + 14.0 * sc)
            vy = cy + math.sin(a) * (radius + 14.0 * sc)

            if i == 0:                      # Top (Knowledge)
                bx = cx - (nw + vw + 6 * sc) / 2.0
                by = max(rect.top() + 6 * sc, vy - lh - 2 * sc)
            elif i in (1, 2):               # Right side (Charm, Kindness)
                bx = min(rect.right() - max(nw, vw) - 8 * sc, vx + 4 * sc)
                by = vy - lh * 0.75
            else:                           # Left side (Proficiency, Guts)
                bx = max(rect.left() + 8 * sc, vx - max(nw, vw) - 4 * sc)
                by = vy - lh * 0.75

            if i == 0:
                p.setFont(lbl_font)
                p.setPen(WHITE if is_sel else P5_TEXT)
                p.drawText(QRectF(bx, by, nw + 4, lh), Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value, name)
                pill = QRectF(bx + nw + 5 * sc, by + 1, vw, lh - 1)
                p.setPen(QPen(WHITE, 1.1) if is_sel else Qt.PenStyle.NoPen)
                p.setBrush(RED if is_sel else P5_GOLD_BG)
                p.drawRoundedRect(pill, 4.0, 4.0)
                p.setFont(lv_font)
                p.setPen(WHITE if is_sel else P5_GOLD_FG)
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, lv_txt)
            else:
                align = Qt.AlignmentFlag.AlignLeft.value if i in (1, 2) else Qt.AlignmentFlag.AlignRight.value
                box_w = max(nw, vw) + 4
                p.setFont(lbl_font)
                p.setPen(WHITE if is_sel else P5_TEXT)
                p.drawText(QRectF(bx, by, box_w, lh), align | Qt.AlignmentFlag.AlignVCenter.value, name)
                px_lv = bx if i in (1, 2) else (bx + box_w - vw)
                pill = QRectF(px_lv, by + lh + 1 * sc, vw, fm_v.height() + 2)
                p.setPen(QPen(WHITE, 1.1) if is_sel else Qt.PenStyle.NoPen)
                p.setBrush(RED if is_sel else P5_GOLD_BG)
                p.drawRoundedRect(pill, 4.0, 4.0)
                p.setFont(lv_font)
                p.setPen(WHITE if is_sel else P5_GOLD_FG)
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, lv_txt)
        p.end()


class StatLogDelegate(QStyledItemDelegate):
    """Paints Persona 5 social-stat log rows with stat chip, activity text, block/date, and +pts badge."""

    def __init__(self, parent: QWidget | None = None, scale: float = 1.0) -> None:
        super().__init__(parent)
        self._scale = scale

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(0, _sc(68, self._scale, 52))
        return QSize(0, _sc(52, self._scale, 42))

    def paint(self, p: QPainter, option, index) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        sc = self._scale

        if index.data(ROLE_ID) is None:
            p.setPen(P5_MUTED)
            p.setFont(ui_font(_sc(13, sc, 10), bold=False))
            p.drawText(r.adjusted(10, 0, -10, 0),
                       Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                       index.data(Qt.ItemDataRole.DisplayRole) or "")
            p.restore()
            return

        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = P5_SELECTED if sel else (P5_HOVER if hov else P5_SURFACE)
        bd = RED if sel else (P5_BORDER_HI if hov else P5_BORDER)
        p.setPen(QPen(bd, 1.3 if sel else 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(r, 7.0, 7.0)

        stat_short = index.data(ROLE_CAT) or "STAT"
        pts_txt = index.data(ROLE_BADGE) or "+3"
        meta_txt = index.data(ROLE_DATE) or ""
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""

        # Left stat pill
        pill_w = _sc(42, sc, 34)
        pill_h = _sc(22, sc, 18)
        pill = QRectF(r.left() + _sc(8, sc, 6), r.center().y() - pill_h / 2.0, pill_w, pill_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(P5_RED_BG)
        p.drawRoundedRect(pill, 4.0, 4.0)
        p.setFont(p5_font(_sc(11, sc, 9), italic=False))
        p.setPen(P5_RED_FG)
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, stat_short)

        # Right +PTS gold badge
        pts_w = _sc(44, sc, 36)
        pts_rect = QRectF(r.right() - pts_w - _sc(8, sc, 6), r.center().y() - pill_h / 2.0, pts_w, pill_h)
        p.setBrush(P5_GOLD_BG)
        p.drawRoundedRect(pts_rect, 4.0, 4.0)
        p.setPen(P5_GOLD_FG)
        p.drawText(pts_rect, Qt.AlignmentFlag.AlignCenter.value, pts_txt)

        # Middle text (activity + block/date)
        tx = pill.right() + _sc(9, sc, 6)
        tw = max(40.0, pts_rect.left() - tx - _sc(8, sc, 6))
        tf = ui_font(_sc(13, sc, 10), bold=True)
        p.setFont(tf)
        p.setPen(WHITE)
        elided = QFontMetrics(tf).elidedText(title, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, r.top() + _sc(5, sc, 3), tw, _sc(20, sc, 16)),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, elided)

        mf = ui_font(_sc(11, sc, 9), bold=False)
        p.setFont(mf)
        p.setPen(P5_MUTED)
        p.drawText(QRectF(tx, r.top() + _sc(24, sc, 19), tw, _sc(18, sc, 14)),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, meta_txt)
        p.restore()


class HabitRowDelegate(QStyledItemDelegate):
    """Paints Persona 5 custom daily habit rows with spring checkbox, stat pill, +pts, and streak badge."""

    CHECK_POINTS = ((-4.8, 0.4), (-1.4, 3.8), (5.2, -4.2))

    def __init__(self, view: QListWidget, scale: float = 1.0) -> None:
        super().__init__(view)
        self._sc = max(0.75, min(1.5, float(scale)))
        self._springs: dict[str, Spring] = {}
        self._swallow_release = False
        self._title_font = ui_font(_sc(14, self._sc, 11), bold=True)
        self._done_font = ui_font(_sc(14, self._sc, 11), bold=True)
        self._done_font.setStrikeOut(True)
        self._pill_font = p5_font(_sc(10, self._sc, 8), italic=False)
        self._hint_font = ui_font(_sc(13, self._sc, 10), bold=False)
        self._ticker = FrameTicker(self._frame, self)

    def start_check_anim(self, item_id: str) -> None:
        sp = Spring(0.0, 420.0)
        sp.target = 1.0
        self._springs[item_id] = sp
        self._ticker.start()

    def clear_anim(self, item_id: str) -> None:
        self._springs.pop(item_id, None)

    def _frame(self, dt: float) -> bool:
        for k in list(self._springs):
            if not self._springs[k].step(dt):
                del self._springs[k]
        view = self.parent()
        if view is not None:
            view.viewport().update()
        return bool(self._springs)

    def _box_rect(self, card: QRectF) -> QRectF:
        bs = 20.0 * self._sc
        return QRectF(card.left() + 12.0 * self._sc, card.center().y() - bs / 2.0, bs, bs)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(120, _sc(84, self._sc, 64))
        return QSize(120, _sc(58, self._sc, 46))

    def _paint_checkbox(self, p: QPainter, box: QRectF, done: bool, prog: float) -> None:
        p.save()
        if done:
            p.setPen(QPen(WHITE, 1.5))
            p.setBrush(RED)
            p.drawRoundedRect(box, 4.0, 4.0)
            if prog > 0.01:
                c = box.center()
                pts = self.CHECK_POINTS
                seg1 = ((pts[1][0] - pts[0][0]) ** 2 + (pts[1][1] - pts[0][1]) ** 2) ** 0.5
                seg2 = ((pts[2][0] - pts[1][0]) ** 2 + (pts[2][1] - pts[1][1]) ** 2) ** 0.5
                reach = clamp(prog) * (seg1 + seg2)
                path = QPainterPath()
                path.moveTo(c.x() + pts[0][0], c.y() + pts[0][1])
                if reach <= seg1:
                    f = reach / seg1
                    path.lineTo(c.x() + pts[0][0] + (pts[1][0] - pts[0][0]) * f,
                                c.y() + pts[0][1] + (pts[1][1] - pts[0][1]) * f)
                else:
                    f = (reach - seg1) / seg2
                    path.lineTo(c.x() + pts[1][0], c.y() + pts[1][1])
                    path.lineTo(c.x() + pts[1][0] + (pts[2][0] - pts[1][0]) * f,
                                c.y() + pts[1][1] + (pts[2][1] - pts[1][1]) * f)
                p.setPen(QPen(WHITE, 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        else:
            p.setPen(QPen(QColor("#8E8EA0"), 1.6))
            p.setBrush(P5_BG)
            p.drawRoundedRect(box, 4.0, 4.0)
        p.restore()

    def paint(self, painter, option, index) -> None:
        r = QRectF(option.rect)
        item_id = index.data(ROLE_ID)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if item_id is None:
            painter.setPen(P5_MUTED)
            painter.setFont(self._hint_font)
            painter.drawText(r.adjusted(14, 6, -14, -6),
                             Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                             index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        card = r.adjusted(2, 3, -4, -3)
        done = bool(index.data(ROLE_DONE))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        sc = self._sc

        if selected:
            bg, border = P5_SELECTED, RED
        elif done:
            bg, border = (P5_HOVER if hovered else QColor("#141F1B")), QColor("#245943")
        elif hovered:
            bg, border = P5_HOVER, P5_BORDER_HI
        else:
            bg, border = P5_SURFACE, P5_BORDER

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(bg)
        painter.drawRoundedRect(card, 8.0, 8.0)

        box = self._box_rect(card)
        sp = self._springs.get(item_id)
        self._paint_checkbox(painter, box, done, sp.value if sp else 1.0)

        tx = box.right() + 10.0 * sc
        tw = max(40.0, card.right() - tx - 10.0 * sc)
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        font = self._done_font if done else self._title_font
        fm = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(P5_MUTED if done else WHITE)
        painter.drawText(QPointF(tx, card.top() + 8.0 * sc + fm.ascent()),
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, int(tw)))

        # Second line: STAT chip + +PTS gold chip + optional STREAK chip + DONE chip
        ph = 16.0 * sc
        py = card.top() + 30.0 * sc
        x_cur = tx
        pfm = QFontMetrics(self._pill_font)

        def _chip(txt: str, bg_c: QColor, fg_c: QColor) -> None:
            nonlocal x_cur
            w = pfm.horizontalAdvance(txt) + 12.0 * sc
            rect = QRectF(x_cur, py, w, ph)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg_c)
            painter.drawRoundedRect(rect, 4.0, 4.0)
            painter.setFont(self._pill_font)
            painter.setPen(fg_c)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter.value, txt)
            x_cur = rect.right() + 5.0 * sc

        stat_short = index.data(ROLE_CAT) or "KNO"
        pts_txt = index.data(ROLE_BADGE) or "+2"
        streak_txt = index.data(ROLE_STREAK) or ""
        _chip(stat_short, P5_RED_BG, P5_RED_FG)
        _chip(pts_txt, P5_GOLD_BG, P5_GOLD_FG)
        if streak_txt:
            _chip(streak_txt, QColor("#2B2208"), GOLD)
        if done:
            _chip("DONE TODAY", P5_MINT_BG, P5_MINT_FG)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if index.data(ROLE_ID) is None:
            return False
        et = event.type()
        if et in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick) \
                and event.button() == Qt.MouseButton.LeftButton:
            card = QRectF(option.rect).adjusted(2, 3, -4, -3)
            hit = self._box_rect(card).adjusted(-8, -8, 8, 8)
            if hit.contains(event.position()):
                if et == QEvent.Type.MouseButtonDblClick:
                    self._swallow_release = True
                    return True
                if self._swallow_release:
                    self._swallow_release = False
                    return True
                model.setData(index, not bool(index.data(ROLE_DONE)), ROLE_DONE)
                return True
        return super().editorEvent(event, model, option, index)


class SocialStatsDialog(P5FluidPanel):
    """Persona 5 Royal Social Stats tracker with progressive ranks, custom Daily Habits, and activity log."""

    stat_logged = pyqtSignal(str, str, int, int, int)             # stat, activity, pts, old_lv, new_lv
    habit_toggled = pyqtSignal(str, bool, object, int, int)       # habit_id, now_done, stat_entry, old_lv, new_lv
    open_briefing = pyqtSignal()
    open_settings = pyqtSignal()

    def __init__(self, store: SocialStatsStore, habits: HabitStore | None = None,
                 on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("SOCIAL STATS", QSize(830, 670), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self.habits = habits
        self._selected_stat = "knowledge"
        self._habit_stat = "knowledge"
        self._habit_pts = 2
        self._selected_block = default_time_block()
        self._log_filter = "all"
        self._right_tab = "habits" if habits is not None else "log"
        self._undo_kind = ""
        self._undo_payload: dict | None = None

        # Header bar
        self._blocks_hdr = QLabel("")
        self._blocks_hdr.setObjectName("subhead")
        self.header_bar.addWidget(self._blocks_hdr)

        brief_btn = self._btn("BRIEFING", variant="gold", display=True, px=10)
        brief_btn.setToolTip("Open Daily Briefing & Confidant Summary")
        brief_btn.clicked.connect(lambda _c=False: self.open_briefing.emit())
        self.header_bar.addWidget(brief_btn)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Two-column layout
        cols = QHBoxLayout()
        cols.setSpacing(_sc(12, sc, 8))

        # -- LEFT COLUMN: Pentagon radar + Confidant rank strip + 5 stat bars ---
        left = QVBoxLayout()
        left.setSpacing(_sc(7, sc, 5))

        self._pentagon = SocialPentagonWidget(scale=sc)
        self._pentagon.stat_clicked.connect(self._select_stat)
        left.addWidget(self._pentagon)

        # Confidant Rank & Streak summary pill row
        conf_bar = _make_card("subcard")
        cbl = QHBoxLayout(conf_bar)
        cbl.setContentsMargins(_sc(10, sc, 7), _sc(5, sc, 4), _sc(10, sc, 7), _sc(5, sc, 4))
        cbl.setSpacing(6)
        self._conf_rank_lbl = QLabel("CONFIDANT LV 5 / 25")
        self._conf_rank_lbl.setStyleSheet(
            f'color: #FFFFFF; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(13, sc, 10)}px; font-weight: 800;'
        )
        cbl.addWidget(self._conf_rank_lbl)
        cbl.addStretch(1)
        self._streak_lbl = QLabel("0D STREAK")
        self._streak_lbl.setStyleSheet(
            f'background: #2B2208; color: #FAD02C; border: 1px solid #6C5514; '
            f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
            f'font-family: "Barlow Condensed", sans-serif; font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
        )
        cbl.addWidget(self._streak_lbl)
        left.addWidget(conf_bar)

        stats_card = _make_card()
        sl = QVBoxLayout(stats_card)
        sl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        sl.setSpacing(_sc(5, sc, 4))

        self._stat_rows: dict[str, tuple[QFrame, QLabel, QLabel, QProgressBar]] = {}
        for key in ("knowledge", "guts", "proficiency", "charm", "kindness"):
            row_f = _make_card("subcard")
            row_f.setCursor(Qt.CursorShape.PointingHandCursor)
            row_f.mousePressEvent = lambda _e, k=key: self._select_stat(k)
            rl = QVBoxLayout(row_f)
            rl.setContentsMargins(_sc(8, sc, 6), _sc(5, sc, 4), _sc(8, sc, 6), _sc(5, sc, 4))
            rl.setSpacing(_sc(3, sc, 2))

            top_r = QHBoxLayout()
            top_r.setSpacing(_sc(5, sc, 4))
            title_lbl = QLabel(SOCIAL_STATS[key]["label"].upper())
            title_lbl.setStyleSheet(
                f'color: #FFFFFF; font-family: "Barlow Condensed", "Anton", sans-serif; '
                f'font-size: {_sc(13, sc, 10)}px; font-weight: 800;'
            )
            top_r.addWidget(title_lbl)

            rank_lbl = QLabel("")
            rank_lbl.setStyleSheet(
                f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
            )
            top_r.addWidget(rank_lbl)
            top_r.addStretch(1)

            pts_lbl = QLabel("")
            pts_lbl.setStyleSheet(f'color: #9292A4; font-size: {_sc(10, sc, 8)}px; font-weight: bold;')
            top_r.addWidget(pts_lbl)
            rl.addLayout(top_r)

            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(_sc(5, sc, 4))
            rl.addWidget(bar)

            sl.addWidget(row_f)
            self._stat_rows[key] = (row_f, rank_lbl, pts_lbl, bar)

        left.addWidget(stats_card, 1)
        cols.addLayout(left, 45)

        # -- RIGHT COLUMN: Mode switcher (DAILY HABITS | LOG & HISTORY) ---------
        right = QVBoxLayout()
        right.setSpacing(_sc(8, sc, 6))

        tab_row = QHBoxLayout()
        tab_row.setSpacing(_sc(6, sc, 4))
        self._tab_habits_btn = self._btn("DAILY HABITS", variant="soft", display=True, px=11)
        self._tab_habits_btn.setFixedHeight(_sc(30, sc, 24))
        self._tab_habits_btn.clicked.connect(lambda _c=False: self._set_right_tab("habits"))
        self._tab_log_btn = self._btn("LOG & HISTORY", variant="soft", display=True, px=11)
        self._tab_log_btn.setFixedHeight(_sc(30, sc, 24))
        self._tab_log_btn.clicked.connect(lambda _c=False: self._set_right_tab("log"))
        tab_row.addWidget(self._tab_habits_btn, 1)
        tab_row.addWidget(self._tab_log_btn, 1)
        if self.habits is None:
            self._tab_habits_btn.hide()
        right.addLayout(tab_row)

        # ---- PAGE A: Custom Daily Habits --------------------------------------
        self._habits_page = QWidget()
        hpl = QVBoxLayout(self._habits_page)
        hpl.setContentsMargins(0, 0, 0, 0)
        hpl.setSpacing(_sc(8, sc, 6))

        add_h_card = _make_card()
        ahl = QVBoxLayout(add_h_card)
        ahl.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        ahl.setSpacing(_sc(6, sc, 4))

        ah_top = QHBoxLayout()
        ah_lbl = QLabel("CUSTOM DAILY HABITS")
        ah_lbl.setObjectName("section")
        ah_top.addWidget(ah_lbl)
        ah_top.addStretch(1)
        self._habits_prog_lbl = QLabel("0 / 0 TODAY")
        self._habits_prog_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800;'
        )
        ah_top.addWidget(self._habits_prog_lbl)
        ahl.addLayout(ah_top)

        h_inp_row = QHBoxLayout()
        h_inp_row.setSpacing(_sc(6, sc, 4))
        self._habit_input = QLineEdit()
        self._habit_input.setPlaceholderText("New habit (e.g. Read 20m, Workout, Drink 2L water)...")
        self._habit_input.returnPressed.connect(self._add_habit)
        h_inp_row.addWidget(self._habit_input, 1)

        add_h_btn = self._btn("ADD HABIT", variant="primary", icon="plus", display=True, px=10)
        add_h_btn.setFixedHeight(_sc(30, sc, 25))
        add_h_btn.clicked.connect(self._add_habit)
        h_inp_row.addWidget(add_h_btn)
        ahl.addLayout(h_inp_row)

        # Custom stat & reward points selector for new habit
        h_opt_row = QHBoxLayout()
        h_opt_row.setSpacing(_sc(4, sc, 3))
        self._hstat_btns: dict[str, P5FluidButton] = {}
        for k in ("knowledge", "guts", "proficiency", "charm", "kindness"):
            hb = self._btn(SOCIAL_STATS[k]["short"], variant="soft", display=True, px=9)
            hb.setFixedHeight(_sc(24, sc, 20))
            hb.setToolTip(f"Reward {SOCIAL_STATS[k]['label']} points")
            hb.clicked.connect(lambda _c=False, key=k: self._select_habit_stat(key))
            self._hstat_btns[k] = hb
            h_opt_row.addWidget(hb, 1)

        self._hpts_btns: dict[int, P5FluidButton] = {}
        for pv in (1, 2, 3):
            pb = self._btn(f"+{pv}", variant="soft", display=True, px=9)
            pb.setFixedHeight(_sc(24, sc, 20))
            pb.setToolTip(f"Award +{pv} pts each day completed")
            pb.clicked.connect(lambda _c=False, v=pv: self._select_habit_pts(v))
            self._hpts_btns[pv] = pb
            h_opt_row.addWidget(pb)
        ahl.addLayout(h_opt_row)
        hpl.addWidget(add_h_card)

        hlist_card = _make_card()
        hlc = QVBoxLayout(hlist_card)
        hlc.setContentsMargins(_sc(8, sc, 6), _sc(8, sc, 6), _sc(8, sc, 6), _sc(8, sc, 6))
        hlc.setSpacing(_sc(6, sc, 4))

        self._habit_list = _KeyAwareList()
        self._habit_list.setMouseTracking(True)
        self._habit_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._habit_delegate = HabitRowDelegate(self._habit_list, scale=sc)
        self._habit_list.setItemDelegate(self._habit_delegate)
        self._habit_list.itemChanged.connect(self._on_habit_item_changed)
        self._habit_list.space_pressed.connect(self._toggle_selected_habit)
        self._habit_list.delete_pressed.connect(self._delete_selected_habit)
        hlc.addWidget(self._habit_list, 1)

        hf_foot = QHBoxLayout()
        hf_foot.setSpacing(6)
        htip = QLabel("Resets daily · Auto-awards Social Stats · Space: check")
        htip.setObjectName("tip")
        hf_foot.addWidget(htip, 1)
        hdel_btn = self._btn("DELETE", variant="danger", display=True, px=10)
        hdel_btn.setFixedHeight(_sc(26, sc, 22))
        hdel_btn.clicked.connect(self._delete_selected_habit)
        hf_foot.addWidget(hdel_btn)
        hlc.addLayout(hf_foot)
        hpl.addWidget(hlist_card, 1)
        right.addWidget(self._habits_page, 1)

        # ---- PAGE B: Manual Time Block Logger & Recent History ----------------
        self._log_page = QWidget()
        lpl = QVBoxLayout(self._log_page)
        lpl.setContentsMargins(0, 0, 0, 0)
        lpl.setSpacing(_sc(8, sc, 6))

        log_card = _make_card()
        fl = QVBoxLayout(log_card)
        fl.setContentsMargins(_sc(12, sc, 9), _sc(9, sc, 7), _sc(12, sc, 9), _sc(9, sc, 7))
        fl.setSpacing(_sc(6, sc, 5))

        f_hdr = QHBoxLayout()
        h_lbl = QLabel("LOG TIME BLOCK (~2H)")
        h_lbl.setObjectName("section")
        f_hdr.addWidget(h_lbl)
        f_hdr.addStretch(1)
        self._pips_lbl = QLabel("")
        self._pips_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800;'
        )
        f_hdr.addWidget(self._pips_lbl)
        fl.addLayout(f_hdr)

        # Stat selector chips (2 clean rows: 3 + 2)
        self._stat_btns: dict[str, P5FluidButton] = {}
        r1 = QHBoxLayout()
        r1.setSpacing(_sc(5, sc, 4))
        for k in ("knowledge", "guts", "proficiency"):
            b = self._btn(SOCIAL_STATS[k]["label"].upper(), variant="soft", display=True, px=10)
            b.setFixedHeight(_sc(25, sc, 21))
            b.clicked.connect(lambda _c=False, key=k: self._select_stat(key))
            self._stat_btns[k] = b
            r1.addWidget(b, 1)
        fl.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(_sc(5, sc, 4))
        for k in ("charm", "kindness"):
            b = self._btn(SOCIAL_STATS[k]["label"].upper(), variant="soft", display=True, px=10)
            b.setFixedHeight(_sc(25, sc, 21))
            b.clicked.connect(lambda _c=False, key=k: self._select_stat(key))
            self._stat_btns[k] = b
            r2.addWidget(b, 1)
        fl.addLayout(r2)

        self._rule_hint = QLabel("")
        self._rule_hint.setObjectName("tip")
        self._rule_hint.setWordWrap(True)
        fl.addWidget(self._rule_hint)

        act_row = QHBoxLayout()
        act_row.setSpacing(_sc(6, sc, 4))
        self._act_input = QLineEdit()
        self._act_input.returnPressed.connect(self._log_activity)
        act_row.addWidget(self._act_input, 1)

        self._log_btn = self._btn(f"LOG +{DEFAULT_STAT_POINTS}", variant="primary", icon="plus",
                                  display=True, px=10, sfx_click="")
        self._log_btn.setFixedHeight(_sc(30, sc, 25))
        self._log_btn.clicked.connect(self._log_activity)
        act_row.addWidget(self._log_btn)
        fl.addLayout(act_row)

        blk_row = QHBoxLayout()
        blk_row.setSpacing(_sc(5, sc, 4))
        blk_lbl = QLabel("Block")
        blk_lbl.setStyleSheet(f'color: #9292A4; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        blk_row.addWidget(blk_lbl)
        self._block_btns: dict[str, P5FluidButton] = {}
        for blk in TIME_BLOCKS:
            b = self._btn(blk.upper(), variant="soft", display=True, px=9)
            b.setFixedHeight(_sc(24, sc, 20))
            b.clicked.connect(lambda _c=False, name=blk: self._select_block(name))
            self._block_btns[blk] = b
            blk_row.addWidget(b, 1)
        fl.addLayout(blk_row)

        pts_row = QHBoxLayout()
        pts_row.setSpacing(_sc(5, sc, 4))
        pts_tag = QLabel("Points")
        pts_tag.setStyleSheet(f'color: #9292A4; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        pts_row.addWidget(pts_tag)
        self._quick_pts_btns: dict[int, P5FluidButton] = {}
        for val in QUICK_STAT_POINTS:
            b = self._btn(f"+{val}", variant="soft", display=True, px=10)
            b.setFixedHeight(_sc(25, sc, 21))
            b.clicked.connect(lambda _c=False, v=val: self._set_points(v))
            self._quick_pts_btns[val] = b
            pts_row.addWidget(b, 1)

        self._pts_spin = QSpinBox()
        self._pts_spin.setRange(1, 500)
        self._pts_spin.setValue(DEFAULT_STAT_POINTS)
        self._pts_spin.setSuffix(" pts")
        self._pts_spin.setMinimumWidth(_sc(84, sc, 68))
        self._pts_spin.setFixedHeight(_sc(25, sc, 21))
        self._pts_spin.valueChanged.connect(self._on_points_changed)
        pts_row.addWidget(self._pts_spin)
        fl.addLayout(pts_row)
        lpl.addWidget(log_card)

        hist_card = _make_card()
        hl = QVBoxLayout(hist_card)
        hl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        hl.setSpacing(_sc(5, sc, 4))

        h_top = QHBoxLayout()
        rec_lbl = QLabel("RECENT LOG")
        rec_lbl.setObjectName("section")
        h_top.addWidget(rec_lbl)
        h_top.addStretch(1)
        self._log_cnt_lbl = QLabel("")
        self._log_cnt_lbl.setObjectName("tip")
        h_top.addWidget(self._log_cnt_lbl)
        hl.addLayout(h_top)

        f_row = QHBoxLayout()
        f_row.setSpacing(_sc(4, sc, 3))
        self._filter_btns: dict[str, P5FluidButton] = {}
        for fkey, flabel in (("all", "ALL"), ("knowledge", "KNO"), ("guts", "GUT"),
                             ("proficiency", "PRO"), ("charm", "CHA"), ("kindness", "KIN")):
            fb = self._btn(flabel, variant="soft", display=True, px=9)
            fb.setFixedHeight(_sc(22, sc, 19))
            fb.clicked.connect(lambda _c=False, k=fkey: self._set_log_filter(k))
            self._filter_btns[fkey] = fb
            f_row.addWidget(fb, 1)
        self._filter_btns["all"].set_active(True)
        hl.addLayout(f_row)

        self._log_list = _KeyAwareList()
        self._log_list.setMouseTracking(True)
        self._log_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._log_list.setItemDelegate(StatLogDelegate(self._log_list, scale=sc))
        self._log_list.delete_pressed.connect(self._delete_selected)
        hl.addWidget(self._log_list, 1)

        h_foot = QHBoxLayout()
        h_foot.setSpacing(6)
        req_str = "/".join(str(x) for x in STAT_RANK_THRESHOLDS[1:])
        tip = QLabel(f"Ranks: {req_str} pts · +{STAT_MASTERY_STEP} Mastery")
        tip.setObjectName("tip")
        h_foot.addWidget(tip, 1)

        del_btn = self._btn("DELETE", variant="danger", display=True, px=10)
        del_btn.setFixedHeight(_sc(26, sc, 22))
        del_btn.clicked.connect(self._delete_selected)
        h_foot.addWidget(del_btn)
        hl.addLayout(h_foot)

        lpl.addWidget(hist_card, 1)
        right.addWidget(self._log_page, 1)

        self._undo = UndoBar(scale=sc)
        self._undo.undo_clicked.connect(self._undo_delete)
        right.addWidget(self._undo)

        cols.addLayout(right, 55)
        self.body.addLayout(cols, 1)

        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())
        self._select_stat("knowledge")
        self._select_habit_stat("knowledge")
        self._select_habit_pts(2)
        self._select_block(self._selected_block)
        self._on_points_changed(DEFAULT_STAT_POINTS)
        self._set_right_tab(self._right_tab)

    def on_shown(self) -> None:
        self._select_block(default_time_block())
        target = self._habit_input if self._right_tab == "habits" else self._act_input
        QTimer.singleShot(20, target.setFocus)

    def _set_right_tab(self, tab: str) -> None:
        if tab == "habits" and self.habits is None:
            tab = "log"
        self._right_tab = tab
        self._habits_page.setVisible(tab == "habits")
        self._log_page.setVisible(tab == "log")
        self._tab_habits_btn.set_active(tab == "habits")
        self._tab_log_btn.set_active(tab == "log")

    def _select_stat(self, key: str) -> None:
        if key not in SOCIAL_STATS:
            return
        self._selected_stat = key
        for k, b in self._stat_btns.items():
            b.set_active(k == key)
        meta = SOCIAL_STATS[key]
        self._rule_hint.setText(f"{meta['label']} — {meta['examples']}")
        self._act_input.setPlaceholderText(meta["placeholder"])
        self._refresh_visuals()

    def _select_habit_stat(self, key: str) -> None:
        if key not in SOCIAL_STATS:
            return
        self._habit_stat = key
        for k, b in self._hstat_btns.items():
            b.set_active(k == key)

    def _select_habit_pts(self, pts: int) -> None:
        self._habit_pts = max(1, min(5, int(pts)))
        for v, b in self._hpts_btns.items():
            b.set_active(v == self._habit_pts, active_variant="gold")

    def _select_block(self, blk: str) -> None:
        if blk not in TIME_BLOCKS:
            return
        self._selected_block = blk
        for name, b in self._block_btns.items():
            b.set_active(name == blk, active_variant="gold")

    def _set_points(self, value: int) -> None:
        self._pts_spin.setValue(value)

    def _on_points_changed(self, value: int) -> None:
        for v, b in self._quick_pts_btns.items():
            b.set_active(v == value, active_variant="gold")
        self._log_btn.setText(f"LOG +{value}")

    def _set_log_filter(self, key: str) -> None:
        self._log_filter = key
        for k, b in self._filter_btns.items():
            b.set_active(k == key)
        self._refresh_log()

    # -- Custom Daily Habits actions -----------------------------------------
    def _add_habit(self) -> None:
        if self.habits is None:
            return
        h = self.habits.add(self._habit_input.text(), stat=self._habit_stat, points=self._habit_pts)
        if h is None:
            return
        self._habit_input.clear()
        self._refresh_habits()
        self._habit_input.setFocus()

    def _on_habit_item_changed(self, item: QListWidgetItem) -> None:
        if self.habits is None:
            return
        hid = item.data(ROLE_ID)
        habit = self.habits.get(hid) if hid else None
        if habit is None:
            return
        want_done = bool(item.data(ROLE_DONE))
        cur_done = self.habits.is_done_on(habit)
        if want_done == cur_done:
            return
        now_done, entry, old_lv, new_lv = self.habits.toggle_today(hid, self.store)
        if now_done:
            self._habit_delegate.start_check_anim(hid)
        else:
            play_ui("cancel")
            self._habit_delegate.clear_anim(hid)
            self._refresh_visuals(animate=True)
        self._refresh_habits()
        self._refresh_log()
        self.habit_toggled.emit(hid, now_done, entry, old_lv, new_lv)

    def _toggle_selected_habit(self) -> None:
        item = self._habit_list.currentItem()
        if item is not None and item.data(ROLE_ID):
            item.setData(ROLE_DONE, not bool(item.data(ROLE_DONE)))

    def _delete_selected_habit(self) -> None:
        if self.habits is None:
            return
        item = self._habit_list.currentItem()
        if item is None:
            return
        hid = item.data(ROLE_ID)
        habit = self.habits.get(hid) if hid else None
        if habit is None:
            return
        self._undo_kind = "habit"
        self._undo_payload = dict(habit)
        self.habits.delete(hid)
        self._refresh_habits()
        self._undo.trigger(f"Removed habit '{habit.get('title', '')[:28]}'.")

    def _refresh_habits(self) -> None:
        if self.habits is None:
            return
        items = self.habits.all()
        done_cnt, total_cnt = self.habits.today_summary()
        self._habits_prog_lbl.setText(f"{done_cnt} / {total_cnt} TODAY")
        self._tab_habits_btn.setText(f"DAILY HABITS ({done_cnt}/{total_cnt})" if total_cnt else "DAILY HABITS")

        self._habit_list.blockSignals(True)
        self._habit_list.clear()
        if not items:
            self._habit_list.addItem(_hint_item(
                "No custom daily habits yet!\n"
                "Type a habit above, choose its Social Stat & points (+1 to +3), and click ADD HABIT."
            ))
            self._habit_list.blockSignals(False)
            return

        # Pending today first, then done today
        ordered = sorted(items, key=lambda h: (1 if self.habits.is_done_on(h) else 0))
        for h in ordered:
            stat = h.get("stat", "knowledge")
            meta = SOCIAL_STATS.get(stat, {})
            done = self.habits.is_done_on(h)
            st_days = self.habits.streak(h)
            it = QListWidgetItem(h.get("title", ""))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            it.setData(ROLE_ID, h["id"])
            it.setData(ROLE_DONE, done)
            it.setData(ROLE_CAT, meta.get("short", stat[:3].upper()))
            it.setData(ROLE_BADGE, f"+{h.get('points', 2)}")
            it.setData(ROLE_STREAK, f"{st_days}D STREAK" if st_days >= 1 else "")
            self._habit_list.addItem(it)
        self._habit_list.blockSignals(False)

    # -- Manual Time Block & History actions ---------------------------------
    def _log_activity(self) -> None:
        self._pts_spin.interpretText()
        pts = self._pts_spin.value()
        entry, old_lv, new_lv = self.store.add(
            self._selected_stat,
            self._act_input.text(),
            points=pts,
            block=self._selected_block,
            source="manual",
        )
        if entry is None:
            return
        self._act_input.clear()
        self._refresh_log()
        self.stat_logged.emit(entry["stat"], entry["activity"], entry["points"], old_lv, new_lv)

    def animate_point_gain(self) -> None:
        """Called in sync with `social stats add point animation.wav` to swell the pentagon."""
        self._refresh_visuals(animate=True)

    def animate_rank_up(self) -> None:
        """Called in sync with `social stats level up animation.wav` to flash a golden burst."""
        self._pentagon.trigger_rank_up_flash()
        self._refresh_visuals(animate=True)

    def _delete_selected(self) -> None:
        item = self._log_list.currentItem()
        if item is None:
            return
        entry_id = item.data(ROLE_ID)
        if not entry_id:
            return
        entry = self.store.get(entry_id)
        if entry is None:
            return
        self._undo_kind = "log"
        self._undo_payload = dict(entry)
        self.store.delete(entry_id)
        self.refresh()
        self._undo.trigger(f"Removed '{entry.get('activity', '')[:28]}' (+{entry.get('points', 0)} pts).")

    def _undo_delete(self) -> None:
        if self._undo_payload is None:
            return
        if self._undo_kind == "habit" and self.habits is not None:
            self.habits.restore(self._undo_payload)
        elif self._undo_kind == "log":
            self.store.restore(self._undo_payload)
        self._undo_kind = ""
        self._undo_payload = None
        self.refresh()

    def refresh(self) -> None:
        self._refresh_visuals(animate=False)
        self._refresh_habits()
        self._refresh_log()

    def _refresh_visuals(self, animate: bool = False) -> None:
        totals = self.store.totals()
        self._pentagon.set_data(totals, self._selected_stat, animate=animate)

        today_pts = self.store.today_points()
        today_n = len(self.store.today_entries())
        conf_lv, conf_max = overall_confidant_level(totals)
        streak = self.store.streak_days()
        self._blocks_hdr.setText(f"TODAY: +{today_pts} PTS · {today_n} LOGS")
        self._conf_rank_lbl.setText(f"CONFIDANT LV {conf_lv} / {conf_max}")
        self._streak_lbl.setText(f"{streak}D STREAK" if streak else "START STREAK")

        filled = min(today_n, DAILY_BLOCK_TARGET)
        pips = "● " * filled + "○ " * (DAILY_BLOCK_TARGET - filled)
        extra = f" (+{today_n - DAILY_BLOCK_TARGET})" if today_n > DAILY_BLOCK_TARGET else ""
        self._pips_lbl.setText(f"TODAY {pips.strip()}{extra}")

        for key, (row_f, rank_lbl, pts_lbl, bar) in self._stat_rows.items():
            pts = totals.get(key, 0)
            lv, into, span, to_next = stat_level_progress(pts)
            title = stat_rank_title(key, lv)
            is_sel = key == self._selected_stat
            row_f.setStyleSheet(
                "QFrame#subcard { background-color: #2A0C11; border: 1.5px solid #E60012; border-radius: 8px; }"
                if is_sel else ""
            )
            rank_lbl.setText(f"LV {lv} · {title.upper()}")
            pts_lbl.setText(f"{into}/{span} (-{to_next})")
            bar.setRange(0, max(1, span))
            bar.setValue(into)

    def _refresh_log(self) -> None:
        entries = self.store.recent(limit=60, stat_filter=self._log_filter)
        n = len(entries)
        self._log_cnt_lbl.setText(f"{n} " + ("entry" if n == 1 else "entries"))
        self._log_list.clear()
        if not entries:
            msg = ("No activities logged yet.\nComplete a Habit, Task, Focus block, or log manually above!"
                   if self._log_filter == "all"
                   else f"No {SOCIAL_STATS[self._log_filter]['label']} entries yet.")
            self._log_list.addItem(_hint_item(msg))
            return
        src_labels = {"habit": "HABIT", "task": "TASK", "focus": "FOCUS", "note": "NOTE"}
        for e in entries:
            stat = e.get("stat", "knowledge")
            meta = SOCIAL_STATS.get(stat, {})
            item = QListWidgetItem(e.get("activity", ""))
            item.setData(ROLE_ID, e["id"])
            item.setData(ROLE_CAT, meta.get("short", stat[:3].upper()))
            item.setData(ROLE_BADGE, f"+{e.get('points', 0)}")
            d_str = e.get("date", "")
            try:
                d_fmt = datetime.strptime(d_str, "%Y-%m-%d").strftime("%d %b")
            except ValueError:
                d_fmt = d_str
            src_tag = src_labels.get(str(e.get("source", "")).lower(), "")
            meta_parts = [p for p in (src_tag, e.get("block", "Daytime"), d_fmt) if p]
            item.setData(ROLE_DATE, " · ".join(meta_parts))
            self._log_list.addItem(item)


# ---------------------------------------------------------------------------
# DAILY BRIEFING & CONFIDANT SUMMARY DIALOG
# ---------------------------------------------------------------------------
class BriefingDialog(P5FluidPanel):
    """Persona 5 Daily Morning Briefing & Confidant Summary report card."""

    open_events = pyqtSignal()
    open_tasks = pyqtSignal()
    open_stats = pyqtSignal()

    def __init__(self, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("DAILY BRIEFING", QSize(565, 640), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale

        self._date_hdr = QLabel("")
        self._date_hdr.setObjectName("subhead")
        self.header_bar.addWidget(self._date_hdr)
        self._add_close_button()

        # 1) Morgana Confidant Banner
        self._banner = _MorganaBriefingFrame(scale=sc)
        bl = QHBoxLayout(self._banner)
        bl.setContentsMargins(_sc(24, sc, 18), _sc(10, sc, 8), _sc(12, sc, 9), _sc(10, sc, 8))
        bl.setSpacing(_sc(12, sc, 8))

        av_lbl = QLabel()
        av_lbl.setPixmap(_morgana_avatar(_sc(52, sc, 40)))
        av_lbl.setFixedSize(_sc(52, sc, 40), _sc(52, sc, 40))
        bl.addWidget(av_lbl, 0, Qt.AlignmentFlag.AlignTop)

        scol = QVBoxLayout()
        scol.setSpacing(_sc(4, sc, 3))
        top_m = QHBoxLayout()
        top_m.setSpacing(_sc(6, sc, 4))

        m_lbl = QLabel("MORGANA")
        m_lbl.setStyleSheet(
            f'background: #0B0B0B; color: #FFFFFF; border: 1.5px solid #FFFFFF; '
            f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
            f'font-family: "Anton", "Barlow Condensed", sans-serif; font-size: {_sc(12, sc, 10)}px;'
        )
        top_m.addWidget(m_lbl)

        self._period_pill = QLabel("MORNING")
        self._period_pill.setStyleSheet(
            f'background: #E60012; color: #FFFFFF; border: none; border-radius: 4px; '
            f'padding: 1px {_sc(7, sc, 5)}px; font-family: "Barlow Condensed", sans-serif; '
            f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
        )
        top_m.addWidget(self._period_pill)

        self._conf_pill = QLabel("CONFIDANT LV 5/25")
        self._conf_pill.setStyleSheet(
            f'background: #2B2208; color: #FAD02C; border: 1px solid #6C5514; border-radius: 4px; '
            f'padding: 1px {_sc(7, sc, 5)}px; font-family: "Barlow Condensed", sans-serif; '
            f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
        )
        top_m.addWidget(self._conf_pill)
        top_m.addStretch(1)
        scol.addLayout(top_m)

        self._headline_lbl = QLabel("GOOD MORNING!")
        self._headline_lbl.setWordWrap(True)
        self._headline_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(15, sc, 12)}px; font-weight: 800;'
        )
        scol.addWidget(self._headline_lbl)

        self._advice_lbl = QLabel("")
        self._advice_lbl.setWordWrap(True)
        self._advice_lbl.setStyleSheet(
            f'color: #FFFFFF; font-family: "Arsenal", "Optima", "Segoe UI", sans-serif; '
            f'font-size: {_sc(13, sc, 10)}px; font-weight: bold;'
        )
        scol.addWidget(self._advice_lbl)
        bl.addLayout(scol, 1)
        self.body.addWidget(self._banner)

        # 2) 4 KPI summary tiles
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(_sc(7, sc, 5))
        self._kpis: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title in (("events", "TODAY EVENTS"), ("tasks", "PENDING TASKS"),
                           ("habits", "DAILY HABITS"), ("streak", "STREAK")):
            tile = _make_card("subcard")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(_sc(8, sc, 5), _sc(6, sc, 4), _sc(8, sc, 5), _sc(6, sc, 4))
            tl.setSpacing(2)
            v_lbl = QLabel("0")
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v_lbl.setStyleSheet(
                f'color: #FFFFFF; font-family: "Anton", "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(20, sc, 15)}px;'
            )
            k_lbl = QLabel(title)
            k_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            k_lbl.setStyleSheet(
                f'color: #9292A4; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(10, sc, 8)}px; font-weight: 800;'
            )
            tl.addWidget(v_lbl)
            tl.addWidget(k_lbl)
            kpi_row.addWidget(tile, 1)
            self._kpis[key] = (v_lbl, k_lbl)
        self.body.addLayout(kpi_row)

        # 3) Scrollable Agenda + Confidant Stats summary
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        s_body = QWidget()
        s_lay = QVBoxLayout(s_body)
        s_lay.setContentsMargins(0, 0, _sc(4, sc, 2), 0)
        s_lay.setSpacing(_sc(8, sc, 6))

        # Today's Agenda & Deadlines card
        ag_card = _make_card()
        agl = QVBoxLayout(ag_card)
        agl.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        agl.setSpacing(_sc(5, sc, 4))
        ag_hdr = QLabel("TODAY'S SCHEDULE & DEADLINES")
        ag_hdr.setObjectName("section")
        agl.addWidget(ag_hdr)
        self._agenda_lay = QVBoxLayout()
        self._agenda_lay.setSpacing(_sc(4, sc, 3))
        agl.addLayout(self._agenda_lay)
        s_lay.addWidget(ag_card)

        # Confidant Social Stats Summary card
        st_card = _make_card()
        stl = QVBoxLayout(st_card)
        stl.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        stl.setSpacing(_sc(5, sc, 4))
        st_hdr_row = QHBoxLayout()
        st_hdr = QLabel("CONFIDANT SUMMARY")
        st_hdr.setObjectName("section")
        st_hdr_row.addWidget(st_hdr)
        st_hdr_row.addStretch(1)
        self._today_pts_lbl = QLabel("+0 PTS TODAY")
        self._today_pts_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
        )
        st_hdr_row.addWidget(self._today_pts_lbl)
        stl.addLayout(st_hdr_row)

        self._stat_summary_rows: dict[str, tuple[QLabel, QLabel, QProgressBar]] = {}
        for key in ("knowledge", "guts", "proficiency", "charm", "kindness"):
            row = QHBoxLayout()
            row.setSpacing(_sc(8, sc, 5))
            nlbl = QLabel(SOCIAL_STATS[key]["label"].upper())
            nlbl.setFixedWidth(_sc(86, sc, 68))
            nlbl.setStyleSheet(
                f'color: #FFFFFF; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(12, sc, 10)}px; font-weight: 800;'
            )
            rlbl = QLabel("LV 1")
            rlbl.setFixedWidth(_sc(118, sc, 94))
            rlbl.setStyleSheet(
                f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
            )
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(_sc(6, sc, 5))
            plbl = QLabel("0/30")
            plbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            plbl.setFixedWidth(_sc(56, sc, 46))
            plbl.setStyleSheet(f'color: #9292A4; font-size: {_sc(11, sc, 9)}px; font-weight: bold;')
            row.addWidget(nlbl)
            row.addWidget(rlbl)
            row.addWidget(bar, 1)
            row.addWidget(plbl)
            stl.addLayout(row)
            self._stat_summary_rows[key] = (rlbl, plbl, bar)
        s_lay.addWidget(st_card)
        s_lay.addStretch(1)

        scroll.setWidget(s_body)
        self.body.addWidget(scroll, 1)

        # Footer quick jumps + primary close
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self._btn_ev = self._btn("EVENTS", variant="soft", display=True, px=10)
        self._btn_ev.setFixedHeight(_sc(28, sc, 24))
        self._btn_ev.clicked.connect(lambda _c=False: self.open_events.emit())
        foot.addWidget(self._btn_ev)

        self._btn_tk = self._btn("TASKS", variant="soft", display=True, px=10)
        self._btn_tk.setFixedHeight(_sc(28, sc, 24))
        self._btn_tk.clicked.connect(lambda _c=False: self.open_tasks.emit())
        foot.addWidget(self._btn_tk)

        self._btn_st = self._btn("STATS & HABITS", variant="gold", display=True, px=10)
        self._btn_st.setFixedHeight(_sc(28, sc, 24))
        self._btn_st.clicked.connect(lambda _c=False: self.open_stats.emit())
        foot.addWidget(self._btn_st)
        foot.addStretch(1)

        go_btn = self._btn("LET'S GO!", variant="primary", display=True, px=11)
        go_btn.setFixedHeight(_sc(28, sc, 24))
        go_btn.clicked.connect(self.close)
        foot.addWidget(go_btn)
        self.body.addLayout(foot)

    def set_data(self, payload: dict) -> None:
        """Populate the Daily Briefing & Confidant Summary card from a prepared snapshot dict."""
        sc = self._ui_scale
        brief = build_daily_briefing(payload)
        self._date_hdr.setText(brief["date_banner"])
        self._period_pill.setText(brief["period"])
        self._headline_lbl.setText(brief["headline"])
        self._advice_lbl.setText(brief["advice"])
        conf_lv = int(payload.get("confidant_lv", 5))
        conf_max = int(payload.get("confidant_max", 25))
        streak = int(payload.get("streak_days", 0))
        self._conf_pill.setText(f"CONFIDANT LV {conf_lv}/{conf_max}")

        overdue = int(payload.get("overdue_count", 0))
        self._banner.set_accent(RED if overdue else GOLD)

        # KPIs
        ev_n = len(payload.get("today_events") or ())
        pending_n = int(payload.get("pending_tasks", 0))
        h_done = int(payload.get("habits_done", 0))
        h_total = int(payload.get("habits_total", 0))
        self._kpis["events"][0].setText(str(ev_n))
        self._kpis["tasks"][0].setText(str(pending_n))
        self._kpis["tasks"][0].setStyleSheet(
            f'color: {"#FF5E6B" if overdue else "#FFFFFF"}; '
            f'font-family: "Anton", "Barlow Condensed", sans-serif; font-size: {_sc(20, sc, 15)}px;'
        )
        self._kpis["habits"][0].setText(f"{h_done}/{h_total}" if h_total else "—")
        self._kpis["streak"][0].setText(f"{streak}D")
        self._kpis["streak"][0].setStyleSheet(
            f'color: {"#FAD02C" if streak >= 1 else "#FFFFFF"}; '
            f'font-family: "Anton", "Barlow Condensed", sans-serif; font-size: {_sc(20, sc, 15)}px;'
        )

        # Agenda & Deadlines rows
        _clear_layout(self._agenda_lay)
        agenda_items = list(payload.get("agenda_rows") or ())
        if not agenda_items:
            empty = QLabel("All clear today! No timed events or deadlines due.")
            empty.setObjectName("tip")
            self._agenda_lay.addWidget(empty)
        else:
            pill_styles = {
                "OVERDUE": ("#E60012", "#FFFFFF"),
                "LOCAL":   ("#10281E", "#5CE09A"),
                "EVENT":   ("#1C1C28", "#D6D6E8"),
                "TASK":    ("#2B2208", "#FAD02C"),
                "NOTE":    ("#360A10", "#FF7882"),
            }
            for tag, time_s, title_s in agenda_items[:6]:
                r = QHBoxLayout()
                r.setSpacing(_sc(6, sc, 4))
                bg_c, fg_c = pill_styles.get(tag, ("#1C1C28", "#FFFFFF"))
                p_lbl = QLabel(tag)
                p_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                p_lbl.setFixedWidth(_sc(56, sc, 46))
                p_lbl.setStyleSheet(
                    f'background: {bg_c}; color: {fg_c}; border-radius: 4px; padding: 1px 4px; '
                    f'font-family: "Barlow Condensed", sans-serif; font-weight: 800; font-size: {_sc(10, sc, 8)}px;'
                )
                t_lbl = QLabel(time_s)
                t_lbl.setFixedWidth(_sc(56, sc, 46))
                t_lbl.setStyleSheet(
                    f'color: {"#FF5E6B" if tag == "OVERDUE" else "#FAD02C"}; '
                    f'font-family: "Barlow Condensed", sans-serif; font-weight: 800; font-size: {_sc(12, sc, 10)}px;'
                )
                n_lbl = QLabel(title_s)
                n_lbl.setStyleSheet(f'color: #FFFFFF; font-size: {_sc(13, sc, 10)}px; font-weight: bold;')
                r.addWidget(p_lbl)
                r.addWidget(t_lbl)
                r.addWidget(n_lbl, 1)
                self._agenda_lay.addLayout(r)
            if len(agenda_items) > 6:
                more = QLabel(f"+{len(agenda_items) - 6} more items in Events / Tasks")
                more.setObjectName("tip")
                self._agenda_lay.addWidget(more)

        # Social Stats rows
        totals = payload.get("stat_totals") or {}
        today_pts = int(payload.get("today_points", 0))
        self._today_pts_lbl.setText(f"+{today_pts} PTS TODAY")
        for key, (rlbl, plbl, bar) in self._stat_summary_rows.items():
            pts = int(totals.get(key, 0))
            lv, into, span, _to_next = stat_level_progress(pts)
            rlbl.setText(f"LV {lv} · {stat_rank_title(key, lv).upper()}")
            plbl.setText(f"{into}/{span}")
            bar.setRange(0, max(1, span))
            bar.setValue(into)


# ---------------------------------------------------------------------------
# TIMER DIALOG (Persona 5 redesigned Focus Mode + Manual Timers)
# ---------------------------------------------------------------------------
class _FocusClockDisplay(QWidget):
    """Persona 5 slanted obsidian & crimson countdown scoreboard for Focus Mode."""

    def __init__(self, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._running = False
        self._phase = "focus"
        self._clock = "25:00"
        self._progress = 0.0
        self._sessions = 0
        self.setFixedHeight(_sc(112, scale, 90))

    def set_state(self, running: bool, phase: str, clock: str, progress: float, sessions: int) -> None:
        self._running = running
        self._phase = phase
        self._clock = clock
        self._progress = clamp(progress, 0.0, 1.0)
        self._sessions = sessions
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        sc = self._scale
        r = QRectF(self.rect()).adjusted(2.0, 2.0, -7.0, -7.0)
        is_rest = self._running and self._phase == "rest"
        accent = P5_MINT_FG if is_rest else RED

        # Signature P5 hard offset shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawRect(r.translated(4.0, 4.0))

        # Obsidian body
        p.setBrush(BLACK)
        p.drawRect(r)

        # Diagonal left slash panel for phase tag
        slash_w = min(r.width() * 0.34, 156.0 * sc)
        slash = QPolygonF([
            QPointF(r.left(), r.top()),
            QPointF(r.left() + slash_w, r.top()),
            QPointF(r.left() + slash_w - 22.0 * sc, r.bottom()),
            QPointF(r.left(), r.bottom()),
        ])
        p.setBrush(accent if self._running else P5_RED_BG)
        p.drawPolygon(slash)

        # Phase label inside left slash
        tag_top = "BREAK TIME" if is_rest else ("FOCUS MODE" if self._running else "READY")
        tag_sub = "RECHARGE" if is_rest else ("LOCKED IN" if self._running else "POMODORO")
        tag_col = BLACK if is_rest else WHITE
        p.setFont(p5_font(_sc(19, sc, 14), italic=False))
        p.setPen(tag_col)
        p.drawText(QRectF(r.left() + 12 * sc, r.top() + 14 * sc, slash_w - 20 * sc, 34 * sc),
                   Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value, tag_top)
        p.setFont(p5_font(_sc(12, sc, 9), italic=True))
        p.setPen(tag_col if is_rest else P5_GOLD_FG)
        p.drawText(QRectF(r.left() + 12 * sc, r.top() + 48 * sc, slash_w - 24 * sc, 20 * sc),
                   Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value, tag_sub)

        # Sessions pill at bottom-left
        sess_txt = f"{self._sessions} DONE"
        p.setFont(p5_font(_sc(11, sc, 9), italic=False))
        p.setPen(BLACK if is_rest else P5_TEXT)
        p.drawText(QRectF(r.left() + 12 * sc, r.bottom() - 28 * sc, slash_w - 28 * sc, 18 * sc),
                   Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value, sess_txt)

        # Giant Persona 5 Anton countdown digits on right
        digits_rect = QRectF(r.left() + slash_w - 6 * sc, r.top() + 6 * sc,
                             r.width() - slash_w - 8 * sc, r.height() - 22 * sc)
        p.setFont(p5_font(_sc(50, sc, 36), italic=False))
        p.setPen(WHITE)
        p.drawText(digits_rect, Qt.AlignmentFlag.AlignCenter.value, self._clock)

        # Bottom progress track inside scoreboard
        bar_h = max(4.0, 6.0 * sc)
        bar_rect = QRectF(r.left() + 2.0, r.bottom() - bar_h - 1.0, r.width() - 4.0, bar_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(P5_SURFACE)
        p.drawRect(bar_rect)
        if self._progress > 0.005:
            p.setBrush(accent)
            p.drawRect(QRectF(bar_rect.left(), bar_rect.top(),
                              bar_rect.width() * self._progress, bar_rect.height()))

        # Crisp white P5 outer border
        p.setPen(pen(WHITE, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        p.end()


class TimerDialog(P5FluidPanel):
    """Unified Persona 5 Timer window: redesigned Focus Mode (Pomodoro) + Manual Timers."""

    focus_start_requested = pyqtSignal(int, int)      # focus_min, rest_min
    focus_stop_requested = pyqtSignal()
    manual_add_requested = pyqtSignal(int, str)       # total_seconds, label
    manual_cancel_requested = pyqtSignal(str)         # timer_id
    open_settings = pyqtSignal()

    PRESETS = ((5, "5M"), (10, "10M"), (15, "15M"), (30, "30M"), (60, "1H"))

    def __init__(self, settings: SettingsStore, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("TIMER", QSize(560, 650), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.settings = settings
        self._focus_running = False
        self._focus_phase = "focus"
        self._focus_left = 0
        self._focus_total = 1
        self._focus_completed = 0
        self._manual_items: list[dict] = []
        self._timer_rows: dict[str, tuple[QFrame, QLabel, QProgressBar]] = {}

        # Header
        self._hdr_status = QLabel("READY")
        self._hdr_status.setObjectName("subhead")
        self.header_bar.addWidget(self._hdr_status)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # -- CARD 1: FOCUS MODE (Pomodoro) --------------------------------------
        fc = _make_card()
        fl = QVBoxLayout(fc)
        fl.setContentsMargins(_sc(12, sc, 9), _sc(10, sc, 8), _sc(12, sc, 9), _sc(10, sc, 8))
        fl.setSpacing(_sc(8, sc, 6))

        f_top = QHBoxLayout()
        fh = QLabel("FOCUS MODE")
        fh.setObjectName("section")
        f_top.addWidget(fh)
        f_top.addStretch(1)
        self._focus_note = QLabel("Stay steady at your own pace—I'll watch the clock!")
        self._focus_note.setObjectName("tip")
        f_top.addWidget(self._focus_note)
        fl.addLayout(f_top)

        self._clock_display = _FocusClockDisplay(scale=sc)
        fl.addWidget(self._clock_display)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(_sc(8, sc, 5))

        f_lbl = QLabel("Focus")
        f_lbl.setStyleSheet(f'color: #C8C8D8; font-size: {_sc(13, sc, 10)}px; font-weight: bold;')
        ctrl.addWidget(f_lbl)
        self._focus_spin = QSpinBox()
        self._focus_spin.setRange(1, 180)
        self._focus_spin.setSuffix(" m")
        self._focus_spin.setMinimumWidth(_sc(76, sc, 62))
        self._focus_spin.setFixedHeight(_sc(30, sc, 25))
        self._focus_spin.valueChanged.connect(self._on_focus_spin_changed)
        ctrl.addWidget(self._focus_spin)

        r_lbl = QLabel("Break")
        r_lbl.setStyleSheet(f'color: #C8C8D8; font-size: {_sc(13, sc, 10)}px; font-weight: bold;')
        ctrl.addWidget(r_lbl)
        self._rest_spin = QSpinBox()
        self._rest_spin.setRange(1, 60)
        self._rest_spin.setSuffix(" m")
        self._rest_spin.setMinimumWidth(_sc(72, sc, 58))
        self._rest_spin.setFixedHeight(_sc(30, sc, 25))
        ctrl.addWidget(self._rest_spin)

        ctrl.addStretch(1)

        self._focus_btn = self._btn("START FOCUS", variant="primary", icon="play", display=True, px=11)
        self._focus_btn.setFixedHeight(_sc(32, sc, 26))
        self._focus_btn.setMinimumWidth(_sc(132, sc, 106))
        self._focus_btn.clicked.connect(self._toggle_focus)
        ctrl.addWidget(self._focus_btn)
        fl.addLayout(ctrl)
        self.body.addWidget(fc)

        # -- CARD 2: MANUAL TIMERS ----------------------------------------------
        mc = _make_card()
        ml = QVBoxLayout(mc)
        ml.setContentsMargins(_sc(12, sc, 9), _sc(10, sc, 8), _sc(12, sc, 9), _sc(10, sc, 8))
        ml.setSpacing(_sc(8, sc, 6))

        m_top = QHBoxLayout()
        mh = QLabel("MANUAL TIMERS")
        mh.setObjectName("section")
        m_top.addWidget(mh)
        m_top.addStretch(1)
        self._manual_cnt_lbl = QLabel("0 ACTIVE")
        self._manual_cnt_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800;'
        )
        m_top.addWidget(self._manual_cnt_lbl)
        ml.addLayout(m_top)

        # Quick presets + H/M/S inputs
        prow = QHBoxLayout()
        prow.setSpacing(_sc(5, sc, 4))
        plbl = QLabel("Quick")
        plbl.setStyleSheet(f'color: #9292A4; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        prow.addWidget(plbl)
        for mins, text in self.PRESETS:
            pb = self._btn(text, variant="soft", display=True, px=10)
            pb.setFixedHeight(_sc(26, sc, 22))
            pb.clicked.connect(lambda _c=False, m=mins: self._apply_preset(m))
            prow.addWidget(pb, 1)
        self._eod_btn = self._btn("23:59", variant="gold", display=True, px=10)
        self._eod_btn.setFixedHeight(_sc(26, sc, 22))
        self._eod_btn.setToolTip("Set countdown until 23:59 today (or tomorrow if past 23:59)")
        self._eod_btn.clicked.connect(self._apply_until_2359)
        prow.addWidget(self._eod_btn, 1)
        ml.addLayout(prow)

        self._preset_default_label = ""
        hms_row = QHBoxLayout()
        hms_row.setSpacing(_sc(6, sc, 4))
        dur_lbl = QLabel("Duration")
        dur_lbl.setStyleSheet(f'color: #9292A4; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        hms_row.addWidget(dur_lbl)
        self._h_spin = QSpinBox()
        self._h_spin.setRange(0, 23)
        self._h_spin.setSuffix(" h")
        self._h_spin.setFixedHeight(_sc(30, sc, 25))
        self._m_spin = QSpinBox()
        self._m_spin.setRange(0, 59)
        self._m_spin.setValue(10)
        self._m_spin.setSuffix(" m")
        self._m_spin.setFixedHeight(_sc(30, sc, 25))
        self._s_spin = QSpinBox()
        self._s_spin.setRange(0, 59)
        self._s_spin.setSuffix(" s")
        self._s_spin.setFixedHeight(_sc(30, sc, 25))

        for w in (self._h_spin, self._m_spin, self._s_spin):
            w.setMinimumWidth(_sc(76, sc, 60))
            w.valueChanged.connect(self._on_manual_spin_edited)
            hms_row.addWidget(w, 1)
        ml.addLayout(hms_row)

        lbl_row = QHBoxLayout()
        lbl_row.setSpacing(_sc(6, sc, 4))
        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("Reminder label (e.g. Laundry, Stretch, Tea)...")
        self._label_edit.setFixedHeight(_sc(30, sc, 25))
        self._label_edit.returnPressed.connect(self._add_manual_timer)
        lbl_row.addWidget(self._label_edit, 1)

        add_btn = self._btn("START TIMER", variant="primary", icon="plus", display=True, px=10)
        add_btn.setFixedHeight(_sc(30, sc, 25))
        add_btn.clicked.connect(self._add_manual_timer)
        lbl_row.addWidget(add_btn)
        ml.addLayout(lbl_row)

        self._manual_err = QLabel("")
        self._manual_err.setStyleSheet(f'color: #FF5E6B; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        self._manual_err.hide()
        ml.addWidget(self._manual_err)

        # Active manual timers scroll area
        self._m_scroll = QScrollArea()
        self._m_scroll.setWidgetResizable(True)
        self._m_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._m_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._m_body = QWidget()
        self._m_list_lay = QVBoxLayout(self._m_body)
        self._m_list_lay.setContentsMargins(0, 2, _sc(4, sc, 2), 2)
        self._m_list_lay.setSpacing(_sc(6, sc, 4))
        self._m_scroll.setWidget(self._m_body)
        ml.addWidget(self._m_scroll, 1)

        self.body.addWidget(mc, 1)

        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())
        self._sync_spinners_from_settings()
        self._rebuild_manual_list()

    def _sync_spinners_from_settings(self) -> None:
        if self._focus_running:
            return
        self._focus_spin.blockSignals(True)
        self._rest_spin.blockSignals(True)
        self._focus_spin.setValue(int(self.settings["focus_min"]))
        self._rest_spin.setValue(int(self.settings["rest_min"]))
        self._focus_spin.blockSignals(False)
        self._rest_spin.blockSignals(False)
        self._update_focus_display()

    def refresh(self) -> None:
        self._sync_spinners_from_settings()
        self._manual_err.hide()

    def _on_focus_spin_changed(self, _val: int) -> None:
        if not self._focus_running:
            self._update_focus_display()

    def _toggle_focus(self) -> None:
        if self._focus_running:
            self.focus_stop_requested.emit()
        else:
            self._focus_spin.interpretText()
            self._rest_spin.interpretText()
            self.focus_start_requested.emit(self._focus_spin.value(), self._rest_spin.value())

    def _on_manual_spin_edited(self, _val: int) -> None:
        self._preset_default_label = ""

    def _apply_preset(self, minutes: int) -> None:
        h, m = divmod(minutes, 60)
        self._preset_default_label = ""
        self._h_spin.setValue(h)
        self._m_spin.setValue(m)
        self._s_spin.setValue(0)
        self._manual_err.hide()
        self._label_edit.setFocus()

    def _apply_until_2359(self) -> None:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        remaining = max(1, int(round((target - now).total_seconds())))
        remaining = min(remaining, 23 * 3600 + 59 * 60 + 59)
        h, rem = divmod(remaining, 3600)
        m, s = divmod(rem, 60)
        for sp in (self._h_spin, self._m_spin, self._s_spin):
            sp.blockSignals(True)
        self._h_spin.setValue(h)
        self._m_spin.setValue(m)
        self._s_spin.setValue(s)
        for sp in (self._h_spin, self._m_spin, self._s_spin):
            sp.blockSignals(False)
        self._preset_default_label = "23:59 Reminder"
        self._manual_err.hide()
        self._label_edit.setFocus()

    def _add_manual_timer(self) -> None:
        for sp in (self._h_spin, self._m_spin, self._s_spin):
            sp.interpretText()
        total = self._h_spin.value() * 3600 + self._m_spin.value() * 60 + self._s_spin.value()
        if total <= 0:
            self._manual_err.setText("Set at least 1 second before starting a timer.")
            self._manual_err.show()
            return
        self._manual_err.hide()
        label = self._label_edit.text().strip() or self._preset_default_label
        self._preset_default_label = ""
        self._label_edit.clear()
        self.manual_add_requested.emit(total, label)

    def update_focus(self, running: bool, phase: str, remaining_s: int,
                     total_s: int, completed: int) -> None:
        self._focus_running = bool(running)
        self._focus_phase = phase
        self._focus_left = max(0, int(remaining_s))
        self._focus_total = max(1, int(total_s))
        self._focus_completed = max(0, int(completed))
        self._focus_spin.setEnabled(not running)
        self._rest_spin.setEnabled(not running)
        if running:
            self._focus_btn.setText("STOP FOCUS")
            self._focus_btn._icon = "stop"
            self._focus_btn.set_variant("danger")
            self._focus_note.setText(
                "Take a breather—drink some water and stretch!"
                if phase == "rest" else "Locked in! Keep that momentum going."
            )
        else:
            self._focus_btn.setText("START FOCUS")
            self._focus_btn._icon = "play"
            self._focus_btn.set_variant("primary")
            self._focus_note.setText("Stay steady at your own pace—I'll watch the clock!")
        self._update_focus_display()
        self._update_header_status()

    def _update_focus_display(self) -> None:
        if self._focus_running:
            clock = _fmt_hms(self._focus_left)
            prog = self._focus_left / float(max(1, self._focus_total))
        else:
            clock = _fmt_hms(self._focus_spin.value() * 60)
            prog = 0.0
        self._clock_display.set_state(
            self._focus_running, self._focus_phase, clock, prog, self._focus_completed
        )

    def update_manual_timers(self, items: list[dict]) -> None:
        self._manual_items = list(items)
        new_ids = [i["id"] for i in items]
        if new_ids == list(self._timer_rows.keys()) and new_ids:
            for item in items:
                _card, clock_lbl, bar = self._timer_rows[item["id"]]
                clock_lbl.setText(_fmt_hms(item["remaining_s"]))
                bar.setRange(0, max(1, int(item["total_s"])))
                bar.setValue(max(0, int(item["remaining_s"])))
        else:
            self._rebuild_manual_list()
        self._update_header_status()

    def _update_header_status(self) -> None:
        parts = []
        if self._focus_running:
            tag = "REST" if self._focus_phase == "rest" else "FOCUS"
            parts.append(f"{tag} {_fmt_hms(self._focus_left)}")
        n = len(self._manual_items)
        if n:
            parts.append(f"{n} TIMER" + ("" if n == 1 else "S"))
        self._hdr_status.setText(" · ".join(parts) if parts else "READY")
        self._manual_cnt_lbl.setText(f"{n} ACTIVE")

    def _rebuild_manual_list(self) -> None:
        sc = self._ui_scale
        _clear_layout(self._m_list_lay)
        self._timer_rows.clear()

        if not self._manual_items:
            empty = QLabel("No manual timers running.\nPick a quick preset or set H / M / S above—Morgana will alert you when time's up!")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f'color: #7A7A90; padding: {_sc(22, sc, 14)}px {_sc(14, sc, 10)}px; '
                f'font-size: {_sc(13, sc, 10)}px;'
            )
            self._m_list_lay.addWidget(empty)
            self._m_list_lay.addStretch(1)
            return

        for item in self._manual_items:
            tid = item["id"]
            row_f = _make_card("subcard")
            rl = QVBoxLayout(row_f)
            rl.setContentsMargins(_sc(10, sc, 8), _sc(7, sc, 5), _sc(10, sc, 8), _sc(7, sc, 5))
            rl.setSpacing(_sc(5, sc, 3))

            top = QHBoxLayout()
            top.setSpacing(_sc(8, sc, 6))

            lbl = QLabel(item.get("label") or "Manual Timer")
            lbl.setStyleSheet(
                f'color: #FFFFFF; font-family: "Arsenal", "Optima", "Segoe UI", sans-serif; '
                f'font-size: {_sc(14, sc, 11)}px; font-weight: bold;'
            )
            top.addWidget(lbl, 1)

            clk = QLabel(_fmt_hms(item["remaining_s"]))
            clk.setStyleSheet(
                f'color: #FAD02C; font-family: "Anton", "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(20, sc, 15)}px;'
            )
            top.addWidget(clk)

            cancel = self._btn("CANCEL", variant="danger", display=True, px=9)
            cancel.setFixedHeight(_sc(24, sc, 20))
            cancel.clicked.connect(lambda _c=False, i=tid: self.manual_cancel_requested.emit(i))
            top.addWidget(cancel)
            rl.addLayout(top)

            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(_sc(4, sc, 3))
            bar.setStyleSheet(
                "QProgressBar { background-color: #1E1E27; border: none; border-radius: 2px; }"
                "QProgressBar::chunk { background-color: #F5C400; border-radius: 2px; }"
            )
            bar.setRange(0, max(1, int(item["total_s"])))
            bar.setValue(max(0, int(item["remaining_s"])))
            rl.addWidget(bar)

            self._m_list_lay.addWidget(row_f)
            self._timer_rows[tid] = (row_f, clk, bar)

        self._m_list_lay.addStretch(1)