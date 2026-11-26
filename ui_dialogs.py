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

import calendar
import math
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (QDate, QDateTime, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                          QTime, QTimer, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
                         QShortcut, QSyntaxHighlighter, QTextBlock, QTextBlockFormat,
                         QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextListFormat)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit,
                             QDateTimeEdit, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton,
                             QScrollArea, QSizePolicy, QSlider, QSpinBox, QStyle,
                             QStyleOptionSpinBox, QStyledItemDelegate, QTextEdit, QTimeEdit, QVBoxLayout, QWidget)

import sprites
from audio_manager import audio, play_ui, play_voice
from calendar_service import CalendarResult, events_by_day, human_delta, morgana_briefing
from lines import build_daily_briefing, build_evening_wrapup
from motion import FrameTicker, Spring, clamp
from particles import P5_NOTE_GOLD, P5_STAR_COLORS, Particle, draw_particle
from sheets_sync import FinanceSheetMonthData, forbidden_bank_word, month_tab_name
from storage_manager import (DAILY_BLOCK_TARGET, DEFAULT_STAT_POINTS, FINANCE_CATEGORIES,
                             FINANCE_CATEGORY_SHORT, FINANCE_WALLETS, FINANCE_WARN_RATIO,
                             QUICK_HOTKEY_PRESETS, QUICK_STAT_POINTS, RADIAL_TAB_KEYS,
                             SETTINGS_DEFAULTS, SOCIAL_STAT_KEYS, SOCIAL_STATS, STAT_MASTERY_STEP,
                             STAT_RANK_THRESHOLDS, TIME_BLOCKS, FinanceStore, HabitStore,
                             LocalEventStore, NoteStore, SettingsStore, SocialStatsStore,
                             TaskStore, default_time_block, format_rupiah, overall_confidant_level,
                             parse_dt, parse_rupiah, stat_level, stat_level_progress,
                             stat_pentagon_fraction, stat_rank_title)
from theme import (BLACK, DEEP_RED, GOLD, LIGHT_RED, P5_BG, P5_BORDER, P5_BORDER_HI,
                   P5_CARD, P5_GOLD_BG, P5_GOLD_FG, P5_HOVER, P5_MINT_BG, P5_MINT_FG, P5_MUTED,
                   P5_RED_BG, P5_RED_FG, P5_SELECTED, P5_SURFACE, P5_TEXT, RED, RED_HOVER, WHITE,
                   dialog_qss, lerp_color, load_fonts, menu_qss, p5_font, pen, star_polygon,
                   ui_font)

ROLE_ID = Qt.ItemDataRole.UserRole.value      # item id; None = empty-state hint row
ROLE_DONE = ROLE_ID + 1                       # bool: completed / archived
ROLE_PREVIEW = ROLE_ID + 2                    # preview snippet (notes)
ROLE_BADGE = ROLE_ID + 3                      # status chip text ("IN 2H", "OVERDUE", "DONE")
ROLE_DATE = ROLE_ID + 4                       # date / time label
ROLE_CAT = ROLE_ID + 5                        # category / tone
ROLE_FLAG = ROLE_ID + 6                       # bool: high priority (tasks) or pinned (notes)
ROLE_OVERDUE = ROLE_ID + 7                    # bool: task deadline overdue
ROLE_STREAK = ROLE_ID + 8                     # str: habit streak badge text
ROLE_TAGS = ROLE_ID + 9                       # list[str]: #tags on note card

NOTE_CATEGORIES = ("Personal", "Study", "Work", "Ideas", "Journal")
NOTE_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "blank": ("", "", "Personal"),
    "meeting": (
        "Meeting Notes — {date}",
        "## Agenda\n- Main topic to discuss\n\n## Key Points\n- Key takeaway\n\n## Decisions & Follow-ups\n- [ ] Action item\n\n#meeting",
        "Work",
    ),
    "study": (
        "Study Summary — {date}",
        "## Core Concept\nSummarize the main idea here.\n\n## Key Takeaways (In My Own Words)\n- First insight\n- Second insight\n\n## Questions to Review\n- What needs a closer look?\n\n#study",
        "Study",
    ),
    "idea": (
        "Idea Sketch — {date}",
        "## Spark\nDescribe the core idea in one sentence.\n\n## Why It Matters\n- Problem it solves\n\n## Rough First Steps\n- [ ] Quick prototype or research\n\n#ideas",
        "Ideas",
    ),
    "journal": (
        "Daily Reflection — {date}",
        "## Wins Today\n- Something that went well\n\n## What I Learned\n- A quick lesson or insight\n\n## Focus for Tomorrow\n- Top priority for tomorrow\n\n#journal",
        "Journal",
    ),
}
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
    elif kind == "edit":
        p.drawPolyline(QPolygonF([
            QPointF(cx - r * 0.78, cy + r * 0.78),
            QPointF(cx - r * 0.58, cy + r * 0.16),
            QPointF(cx + r * 0.42, cy - r * 0.84),
            QPointF(cx + r * 0.84, cy - r * 0.42),
            QPointF(cx - r * 0.16, cy + r * 0.58),
            QPointF(cx - r * 0.78, cy + r * 0.78),
        ]))
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
        self._flash = 0.0                # 1.0 -> 0.0 Persona 5 click/attention slash flash
        self._ticker = FrameTicker(self._frame, self)

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self.update()

    def set_active(self, on: bool, active_variant: str = "primary") -> None:
        was = self._active and self._variant == active_variant
        self._active = on
        self._variant = active_variant if on else self._base_variant
        if on and not was and self.isVisible():
            self._flash = 0.75
            self._ticker.start()
        self.update()

    def pulse(self) -> None:
        """Trigger a brief Persona 5 diagonal sheen flash to confirm or highlight this action."""
        self._flash = 1.0
        self._ticker.start()

    def changeEvent(self, e) -> None:
        if e.type() == QEvent.Type.EnabledChange:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)
            if not self.isEnabled():
                self._hover.target = 0.0
                self._press.target = 0.0
        super().changeEvent(e)

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
        if self.isEnabled():
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
            self._flash = 1.0
            self._ticker.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._press.target = 0.0
        self._ticker.start()
        super().mouseReleaseEvent(e)

    def _frame(self, dt: float) -> bool:
        moving = self._hover.step(dt) | self._press.step(dt)
        if self._flash > 0.01:
            self._flash = max(0.0, self._flash - dt * 4.8)
            moving = True
        else:
            self._flash = 0.0
        self.update()
        return moving

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bg0, bg1, bd0, bd1, fg = self.VARIANTS.get(self._variant, self.VARIANTS["soft"])
        hov = clamp(self._hover.value)
        prs = clamp(self._press.value)
        fl = clamp(self._flash)

        bg = lerp_color(bg0, bg1, hov)
        bg.setAlpha(int(bg0.alpha() + (bg1.alpha() - bg0.alpha()) * hov))
        bd = lerp_color(bd0, bd1, hov)
        bd.setAlpha(int(bd0.alpha() + (bd1.alpha() - bd0.alpha()) * hov))
        if fl > 0.05 and bg.alpha() > 20:
            bd = lerp_color(bd, WHITE, fl * 0.85)
            bd.setAlpha(255)

        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if prs > 0.01:
            s = 1.0 - 0.048 * prs
            c = r.center()
            p.translate(c.x(), c.y() + 1.4 * prs)
            p.scale(s, s)
            p.translate(-c.x(), -c.y())
        if not self.isEnabled():
            p.setOpacity(0.38)

        clip = QPainterPath()
        clip.addRoundedRect(r, self._radius, self._radius)

        # Sculpted subtle vertical gradient sheen on opaque variants
        if bg.alpha() > 20:
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0.0, bg.lighter(115 if self._variant == "primary" else 113))
            grad.setColorAt(1.0, bg.darker(108))
            p.fillPath(clip, grad)
        else:
            p.fillPath(clip, bg)

        # Persona 5 interactive accents + tactile click-slash flash (clipped neatly inside bounds)
        if bg.alpha() > 20 or fl > 0.02:
            p.save()
            p.setClipPath(clip)
            p.setPen(Qt.PenStyle.NoPen)
            if bg.alpha() > 20:
                top_a = 42 if self._variant == "primary" else 20
                p.setBrush(QColor(255, 255, 255, top_a))
                p.drawRect(QRectF(r.left(), r.top(), r.width(), 1.3))
                # Diagonal Persona gloss slash on colored accent variants (sweeps on hover)
                if self._variant in ("primary", "gold", "mint", "danger"):
                    sx = r.left() + r.width() * (0.14 + 0.56 * hov)
                    sw = max(14.0, r.width() * 0.15)
                    p.setBrush(QColor(255, 255, 255, int(20 + 26 * hov)))
                    p.drawPolygon(QPolygonF([
                        QPointF(sx, r.top()),
                        QPointF(sx + sw, r.top()),
                        QPointF(sx + sw - 12.0, r.bottom()),
                        QPointF(sx - 12.0, r.bottom()),
                    ]))
                elif self._variant == "soft" and hov > 0.02:
                    bar_h = r.height() * (0.35 + 0.65 * hov)
                    p.setBrush(RED)
                    p.drawRect(QRectF(r.left(), r.center().y() - bar_h / 2.0, 3.5, bar_h))

            # Crisp Persona 5 diagonal flash burst sweeping across button on every click!
            if fl > 0.01:
                prog = 1.0 - fl
                fx = r.left() - 18.0 + prog * (r.width() + 48.0)
                fw = max(18.0, r.width() * 0.24)
                p.setBrush(QColor(255, 255, 255, int(135 * fl)))
                p.drawPolygon(QPolygonF([
                    QPointF(fx, r.top()),
                    QPointF(fx + fw, r.top()),
                    QPointF(fx + fw - 14.0, r.bottom()),
                    QPointF(fx - 14.0, r.bottom()),
                ]))
                p.setBrush(QColor(255, 255, 255, int(42 * fl)))
                p.drawRect(r)
            p.restore()

        if bd.alpha() > 4:
            p.setPen(QPen(bd, 1.5 if (hov > 0.3 or fl > 0.1) else 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
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
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(_sc(14, self._scale, 11), bold=True))
        self.clicked.connect(lambda checked: play_ui("select" if checked else "cancel"))

    def enterEvent(self, e) -> None:
        self._hovered = True
        if self.isEnabled():
            play_ui("ui_hover")
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(e)

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
            grad = QLinearGradient(box.topLeft(), box.bottomLeft())
            grad.setColorAt(0.0, RED_HOVER)
            grad.setColorAt(1.0, RED)
            p.setPen(QPen(WHITE, 1.6))
            p.setBrush(grad)
            p.drawRoundedRect(box, 4.0, 4.0)
            c = box.center()
            p.setPen(QPen(WHITE, 2.2 * sc, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QPointF(c.x() - 4.4 * sc, c.y() + 0.4 * sc), QPointF(c.x() - 1.2 * sc, c.y() + 3.6 * sc))
            p.drawLine(QPointF(c.x() - 1.2 * sc, c.y() + 3.6 * sc), QPointF(c.x() + 4.8 * sc, c.y() - 3.8 * sc))
        else:
            p.setPen(QPen(RED if self._hovered else QColor("#8E8EA4"), 1.6))
            p.setBrush(P5_HOVER if self._hovered else P5_SURFACE)
            p.drawRoundedRect(box, 4.0, 4.0)
        tx = bs + 9.0 * sc
        p.setFont(self.font())
        p.setPen(P5_TEXT)
        p.drawText(QRectF(tx, 0.0, self.width() - tx, self.height()),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                   self.text())


class P5ComboBox(QComboBox):
    """QComboBox with a razor-sharp vector gold chevron indicator."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() - 14.0
        cy = self.height() * 0.5
        pen = QPen(GOLD if self.isEnabled() else QColor("#5C5C6E"), 2.0,
                   Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(cx - 4.0, cy - 2.0), QPointF(cx, cy + 2.5))
        p.drawLine(QPointF(cx, cy + 2.5), QPointF(cx + 4.0, cy - 2.0))


def _paint_step_chevrons(widget, dropdown: bool = False) -> None:
    """Paint crisp antialiased Persona 5 chevrons inside SpinBox / TimeEdit / DateEdit controls."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    enabled = widget.isEnabled()
    if dropdown:
        col = GOLD if enabled else QColor("#5C5C6E")
        p.setPen(QPen(col, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx = widget.width() - 12.5
        cy = widget.height() * 0.5
        p.drawLine(QPointF(cx - 3.6, cy - 1.8), QPointF(cx, cy + 2.2))
        p.drawLine(QPointF(cx, cy + 2.2), QPointF(cx + 3.6, cy - 1.8))
        return

    opt = QStyleOptionSpinBox()
    widget.initStyleOption(opt)
    st = widget.style()
    r_up = st.subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxUp, widget)
    r_dn = st.subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, QStyle.SubControl.SC_SpinBoxDown, widget)
    col = QColor("#E8E8F5") if enabled else QColor("#5C5C6E")
    p.setPen(QPen(col, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if r_up.isValid() and r_up.width() > 4:
        cx, cy = r_up.center().x() + 0.5, r_up.center().y() + 1.0
        p.drawLine(QPointF(cx - 3.2, cy + 1.5), QPointF(cx, cy - 1.8))
        p.drawLine(QPointF(cx, cy - 1.8), QPointF(cx + 3.2, cy + 1.5))
    if r_dn.isValid() and r_dn.width() > 4:
        cx, cy = r_dn.center().x() + 0.5, r_dn.center().y()
        p.drawLine(QPointF(cx - 3.2, cy - 1.5), QPointF(cx, cy + 1.8))
        p.drawLine(QPointF(cx, cy + 1.8), QPointF(cx + 3.2, cy - 1.5))


class P5SpinBox(QSpinBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _paint_step_chevrons(self, dropdown=False)


class P5TimeEdit(QTimeEdit):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _paint_step_chevrons(self, dropdown=False)


class P5DateEdit(QDateEdit):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _paint_step_chevrons(self, dropdown=self.calendarPopup())


class P5DateTimeEdit(QDateTimeEdit):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _paint_step_chevrons(self, dropdown=self.calendarPopup())


# ---------------------------------------------------------------------------
# P5ToastOverlay & _P5KpiCard: live Persona 5 feedback toast & interactive KPI cards
# ---------------------------------------------------------------------------
class P5ToastOverlay(QWidget):
    """Floating Persona 5 slanted confirmation/warning toast pinned cleanly over P5FluidPanel."""

    TONES = {
        "ok":   (P5_MINT_FG, BLACK, "✓"),
        "gold": (GOLD,       BLACK, "★"),
        "warn": (GOLD,       BLACK, "!"),
        "err":  (RED,        WHITE, "×"),
        "info": (RED,        WHITE, "★"),
    }

    def __init__(self, parent: QWidget, scale: float = 1.0) -> None:
        super().__init__(parent)
        self._scale = max(0.75, min(1.5, float(scale)))
        self._text = ""
        self._tone = "ok"
        self._show = Spring(0.0, 380.0, 26.0)
        self._ticker = FrameTicker(self._frame, self)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def show_toast(self, message: str, tone: str = "ok", ms: int = 2400) -> None:
        msg = (message or "").strip()
        if not msg:
            return
        self._text = msg
        self._tone = tone if tone in self.TONES else "info"
        sc = self._scale
        par = self.parentWidget()
        max_w = max(220, (par.width() - 56) if par else 420)
        f = ui_font(_sc(13, sc, 10), bold=True)
        tw = QFontMetrics(f).horizontalAdvance(msg)
        w = min(max_w, max(_sc(150, sc, 120), tw + _sc(64, sc, 48)))
        h = _sc(36, sc, 28)
        self.setFixedSize(w + 10, h + 10)
        self._reposition()
        self._show.target = 1.0
        if not self.isVisible():
            self._show.snap(0.0)
        else:
            self._show.value = min(self._show.value, 0.72)
        self.show()
        self.raise_()
        self._ticker.start()
        self._hide_timer.start(max(900, int(ms)))

    def _reposition(self) -> None:
        par = self.parentWidget()
        if par is None:
            return
        sc = self._scale
        x = max(14, (par.width() - self.width()) // 2)
        y = max(14, par.height() - self.height() - _sc(52, sc, 40))
        self.move(x, y)

    def dismiss(self) -> None:
        self._hide_timer.stop()
        if not self.isVisible():
            return
        self._show.target = 0.0
        self._ticker.start()

    def stop_all(self) -> None:
        self._hide_timer.stop()
        self._ticker.stop()
        self._show.snap(0.0)
        self.hide()

    def _frame(self, dt: float) -> bool:
        moving = self._show.step(dt)
        self.update()
        if self._show.target == 0.0 and self._show.value < 0.04:
            self._show.snap(0.0)
            self.hide()
            return False
        return moving

    def paintEvent(self, _event) -> None:
        sv = clamp(self._show.value)
        if sv <= 0.02 or not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        sc = self._scale
        accent, ic_fg, glyph = self.TONES.get(self._tone, self.TONES["info"])
        slide_y = (1.0 - sv) * 14.0 * sc
        p.setOpacity(sv)
        p.translate(0.0, slide_y)

        skew = 9.0 * sc
        r = QRectF(3.0, 2.0, self.width() - 10.0, self.height() - 10.0)
        poly = QPolygonF([
            QPointF(r.left() + skew, r.top()),
            QPointF(r.right(), r.top()),
            QPointF(r.right() - skew, r.bottom()),
            QPointF(r.left(), r.bottom()),
        ])

        # Hard Persona 5 offset accent shadow + obsidian pill
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 145))
        p.drawPolygon(poly.translated(5.0, 5.0))
        p.setBrush(accent)
        p.drawPolygon(poly.translated(3.0, 3.0))
        p.setBrush(QColor("#0B0B11"))
        p.drawPolygon(poly)

        # Left slanted accent slash + crisp icon badge
        clip = QPainterPath()
        clip.addPolygon(poly)
        clip.closeSubpath()
        badge_w = 28.0 * sc
        p.save()
        p.setClipPath(clip)
        slash = QPolygonF([
            QPointF(r.left(), r.top()),
            QPointF(r.left() + skew + badge_w, r.top()),
            QPointF(r.left() + badge_w, r.bottom()),
            QPointF(r.left(), r.bottom()),
        ])
        p.setBrush(accent)
        p.drawPolygon(slash)
        p.setPen(pen(WHITE, 1.4))
        p.drawLine(QPointF(r.left() + skew + badge_w + 1.0, r.top()),
                   QPointF(r.left() + badge_w + 1.0, r.bottom()))
        p.restore()

        p.setPen(pen(WHITE, 1.7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(poly)

        p.setFont(p5_font(_sc(14, sc, 11), italic=False))
        p.setPen(ic_fg)
        p.drawText(QRectF(r.left() + skew * 0.45, r.top(), badge_w, r.height()),
                   Qt.AlignmentFlag.AlignCenter.value, glyph)

        f = ui_font(_sc(13, sc, 10), bold=True)
        p.setFont(f)
        p.setPen(WHITE)
        tx = r.left() + skew + badge_w + 8.0 * sc
        tw = max(40.0, r.right() - tx - skew - 4.0 * sc)
        elided = QFontMetrics(f).elidedText(self._text, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, r.top(), tw, r.height()),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, elided)


class _P5KpiCard(QFrame):
    """Interactive Persona 5 KPI summary card with hover lift, click action, and update-pulse flash."""

    clicked = pyqtSignal()

    def __init__(
        self, title: str, accent: str, border_col: str, tint_col: str,
        action_hint: str = "", scale: float = 1.0, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scale = max(0.75, min(1.5, float(scale)))
        self._accent = QColor(border_col)
        self._tint = QColor(tint_col)
        self._action_hint = action_hint
        self._active = False
        self._hover = Spring(0.0, 380.0)
        self._press = Spring(0.0, 640.0)
        self._flash = 0.0
        self._ticker = FrameTicker(self._frame, self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        sc = self._scale
        cl = QVBoxLayout(self)
        cl.setContentsMargins(_sc(12, sc, 8), _sc(6, sc, 4), _sc(10, sc, 7), _sc(6, sc, 4))
        cl.setSpacing(_sc(2, sc, 1))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(_sc(4, sc, 2))
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(
            f'color: #A6ABC0; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(10, sc, 8)}px; font-weight: 800; letter-spacing: 0.7px; background: transparent;'
        )
        top.addWidget(t_lbl)
        top.addStretch(1)
        self._hint_lbl = QLabel(action_hint)
        self._hint_lbl.setStyleSheet(
            f'color: {accent}; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(9, sc, 8)}px; font-weight: 800; letter-spacing: 0.5px; background: transparent;'
        )
        self._hint_lbl.setVisible(False)
        top.addWidget(self._hint_lbl)
        cl.addLayout(top)

        self.val_lbl = QLabel("Rp 0")
        self.val_lbl.setStyleSheet(
            f'color: {accent}; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(18, sc, 14)}px; font-weight: 900; background: transparent;'
        )
        cl.addWidget(self.val_lbl)

        self.sub_lbl = QLabel("")
        self.sub_lbl.setStyleSheet(f'color: #9A9FA8; font-size: {_sc(10, sc, 8)}px; background: transparent;')
        cl.addWidget(self.sub_lbl)

    def set_active(self, on: bool) -> None:
        self._active = bool(on)
        self.update()

    def pulse(self) -> None:
        self._flash = 1.0
        self._ticker.start()

    def enterEvent(self, e) -> None:
        play_ui("ui_hover")
        self._hover.target = 1.0
        if self._action_hint:
            self._hint_lbl.setVisible(True)
        self._ticker.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover.target = 0.0
        self._hint_lbl.setVisible(False)
        self._ticker.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            play_ui("select")
            self._press.target = 1.0
            self._flash = 1.0
            self._ticker.start()
            self.clicked.emit()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._press.target = 0.0
        self._ticker.start()
        super().mouseReleaseEvent(e)

    def _frame(self, dt: float) -> bool:
        moving = self._hover.step(dt) | self._press.step(dt)
        if self._flash > 0.01:
            self._flash = max(0.0, self._flash - dt * 3.8)
            moving = True
        else:
            self._flash = 0.0
        self.update()
        return moving

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hov = clamp(self._hover.value)
        prs = clamp(self._press.value)
        fl = clamp(self._flash)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if prs > 0.01:
            p.translate(0.0, 1.2 * prs)

        clip = QPainterPath()
        clip.addRoundedRect(r, 8.0, 8.0)

        bg_grad = QLinearGradient(r.topLeft(), r.bottomRight())
        t0 = self._tint.lighter(int(100 + 22 * hov + (16 if self._active else 0)))
        bg_grad.setColorAt(0.0, t0)
        bg_grad.setColorAt(0.68, QColor("#1A1A26") if (hov > 0.2 or self._active) else QColor("#171722"))
        bg_grad.setColorAt(1.0, QColor("#13131C"))
        p.fillPath(clip, bg_grad)

        p.save()
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        # Bold left accent stripe (widens crisply on hover/active)
        bar_w = 3.5 + 2.0 * max(hov, 1.0 if self._active else 0.0)
        p.setBrush(self._accent)
        p.drawRect(QRectF(r.left(), r.top(), bar_w, r.height()))
        # Subtle top edge sheen
        p.setBrush(QColor(255, 255, 255, int(16 + 22 * hov)))
        p.drawRect(QRectF(r.left(), r.top(), r.width(), 1.2))
        # Persona 5 update / click diagonal light sweep
        if fl > 0.01:
            prog = 1.0 - fl
            fx = r.left() - 24.0 + prog * (r.width() + 60.0)
            fw = max(24.0, r.width() * 0.28)
            fc = QColor(self._accent)
            fc.setAlpha(int(95 * fl))
            p.setBrush(fc)
            p.drawPolygon(QPolygonF([
                QPointF(fx, r.top()),
                QPointF(fx + fw, r.top()),
                QPointF(fx + fw - 18.0, r.bottom()),
                QPointF(fx - 18.0, r.bottom()),
            ]))
            p.setBrush(QColor(255, 255, 255, int(45 * fl)))
            p.drawRect(r)
        p.restore()

        bd_col = lerp_color(QColor("#303042"), self._accent, max(hov * 0.85, 0.9 if self._active else fl))
        p.setPen(QPen(bd_col, 1.5 if (self._active or hov > 0.2 or fl > 0.1) else 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 8.0, 8.0)


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

        # Floating Persona 5 action confirmation / warning toast overlay (never shifts layout)
        self._toast = P5ToastOverlay(self, scale=sc)

    def notify(self, message: str, tone: str = "ok", ms: int = 2400) -> None:
        """Show an instant Persona 5 slanted feedback toast inside this window."""
        self._toast.show_toast(message, tone=tone, ms=ms)

    def nudge_input(self, widget: QWidget, message: str = "", tone: str = "warn") -> None:
        """Highlight an empty/invalid input briefly, focus it, play cue, and show feedback toast."""
        play_ui("cancel")
        if widget is not None:
            if not hasattr(widget, "_p5_orig_qss"):
                widget._p5_orig_qss = widget.styleSheet()
            gen = getattr(widget, "_p5_nudge_gen", 0) + 1
            widget._p5_nudge_gen = gen
            widget.setStyleSheet(
                "background-color: #2B1F0A; border: 2px solid #FAD02C; border-radius: 7px; color: #FFFFFF;"
            )
            widget.setFocus()
            if isinstance(widget, QLineEdit):
                widget.selectAll()

            def _restore(w=widget, expected=gen) -> None:
                try:
                    if getattr(w, "_p5_nudge_gen", 0) == expected:
                        w.setStyleSheet(getattr(w, "_p5_orig_qss", ""))
                except RuntimeError:
                    pass

            QTimer.singleShot(420, _restore)
        if message:
            self.notify(message, tone=tone)

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
        self._toast.stop_all()
        self.on_closing()
        self._closing = True
        self._open.configure(420.0)
        self._open.target = 0.0
        self._ticker.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        M, S, H = self.M, self.SHADOW, self._header_h
        sc = self._ui_scale
        o = clamp(self._open.value)
        body = QRectF(M, M, self.width() - 2 * M - S, self.height() - 2 * M - S)

        # Layered Persona 5 hard offset crimson shadow + crisp obsidian drop depth
        p.setOpacity(max(0.2, o))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRect(body.translated((S + 2.5) * o, (S + 2.5) * o))
        p.setBrush(RED)
        p.drawRect(body.translated(S * o, S * o))

        # Deep obsidian panel canvas with subtle Persona 5 ambient crimson atmosphere
        bg_grad = QLinearGradient(body.topLeft(), body.bottomRight())
        bg_grad.setColorAt(0.0, QColor("#101018"))
        bg_grad.setColorAt(0.45, P5_BG)
        bg_grad.setColorAt(1.0, QColor("#09090E"))
        p.setBrush(bg_grad)
        p.drawRect(body)

        p.save()
        p.setClipRect(body)

        # Subtle radial crimson glow anchored behind the top-left header
        glow = QRadialGradient(QPointF(M + 60.0 * sc, M + H * 0.6), 270.0 * sc)
        glow.setColorAt(0.0, QColor(230, 0, 18, 24))
        glow.setColorAt(0.55, QColor(230, 0, 18, 8))
        glow.setColorAt(1.0, QColor(230, 0, 18, 0))
        p.fillRect(body, glow)

        # Header dark strip + subtle diagonal Persona 5 racing texture on right side
        hdr_rect = QRectF(M, M, body.width(), H)
        p.fillRect(hdr_rect, QColor("#08080D"))
        stripe_w = 10.0 * sc
        p.setBrush(QColor(255, 255, 255, 8))
        sx = M + body.width() - 18.0 * sc
        for _ in range(4):
            p.drawPolygon(QPolygonF([
                QPointF(sx, M),
                QPointF(sx + stripe_w, M),
                QPointF(sx + stripe_w - 16.0 * sc, M + H),
                QPointF(sx - 16.0 * sc, M + H),
            ]))
            sx -= 22.0 * sc

        # Dynamic title-fitted slanted crimson banner + trailing razor WHITE P5 Royal accent stripe
        slant = 20.0 * sc
        title_need = self._title_lbl.sizeHint().width() + _sc(76, sc, 56) + slant
        cut = min(body.width() * 0.56, max(168.0 * sc, float(title_need))) * o
        if cut > 24:
            slash_grad = QLinearGradient(QPointF(M, M), QPointF(M, M + H))
            slash_grad.setColorAt(0.0, QColor("#F50C1F"))
            slash_grad.setColorAt(1.0, QColor("#C40010"))
            p.setBrush(slash_grad)
            p.drawPolygon(QPolygonF([
                QPointF(M, M),
                QPointF(M + cut, M),
                QPointF(M + cut - slant, M + H),
                QPointF(M, M + H),
            ]))
            # Crisp parallel WHITE trailing slash (Persona 5 Royal signature accent)
            gap = 4.0 * sc
            w_bar = 5.0 * sc
            p.setBrush(WHITE)
            p.drawPolygon(QPolygonF([
                QPointF(M + cut + gap, M),
                QPointF(M + cut + gap + w_bar, M),
                QPointF(M + cut + gap + w_bar - slant, M + H),
                QPointF(M + cut + gap - slant, M + H),
            ]))

        # Subtle crimson geometric corner notch at bottom-right of canvas
        cn = 14.0 * sc
        p.setBrush(QColor(230, 0, 18, 75))
        p.drawPolygon(QPolygonF([
            QPointF(body.right(), body.bottom() - cn),
            QPointF(body.right(), body.bottom()),
            QPointF(body.right() - cn, body.bottom()),
        ]))
        p.restore()

        # Crisp crimson divider under header + gold highlight dash under title cut + white P5 border
        p.setPen(pen(RED, 2.0))
        p.drawLine(QPointF(M, M + H), QPointF(M + body.width(), M + H))
        if cut > 24:
            p.setPen(pen(GOLD, 2.4))
            p.drawLine(QPointF(M + cut - slant, M + H), QPointF(M + cut - slant + 22.0 * sc, M + H))
        p.setPen(pen(WHITE, 2.4))
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

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if hasattr(self, "_toast") and self._toast.isVisible():
            self._toast._reposition()

    def closeEvent(self, e) -> None:
        # Handle OS/Alt+F4 close identically to clicking X so cleanup & signals always run
        self._ticker.stop()
        self._toast.stop_all()
        self._closing = False
        self.on_closing()
        e.accept()
        QWidget.hide(self)
        self.closed.emit()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._closing and self._open.value < 0.35 and self._open.target < 0.5:
            self._open.snap(1.0)
            self.setWindowOpacity(1.0)
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

        grad = QLinearGradient(card.topLeft(), card.bottomLeft())
        grad.setColorAt(0.0, bg.lighter(108))
        grad.setColorAt(1.0, bg)
        painter.setPen(QPen(border, 1.3 if (selected or overdue) else 1.2))
        painter.setBrush(grad)
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

        # Second line: optional HIGH chip + Social Stat chip + DUE/status chip + creation time
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

        stat_short = index.data(ROLE_CAT) or ""
        if stat_short:
            stw = pfm.horizontalAdvance(stat_short) + 12.0 * sc
            stpill = QRectF(x_cur, py, stw, ph)
            painter.setPen(QPen(QColor("#524518") if not done else P5_BORDER, 0.9))
            painter.setBrush(QColor("#221C09") if not done else P5_BG)
            painter.drawRoundedRect(stpill, 4.0, 4.0)
            painter.setFont(self._pill_font)
            painter.setPen(GOLD if not done else P5_MUTED)
            painter.drawText(stpill, Qt.AlignmentFlag.AlignCenter.value, stat_short)
            x_cur = stpill.right() + 6

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
        self._preview_font = ui_font(_sc(12, self._sc, 10), bold=False)
        self._chip_font = p5_font(_sc(10, self._sc, 8), italic=False)
        self._date_font = ui_font(_sc(11, self._sc, 9), bold=False)
        self._hint_font = ui_font(_sc(13, self._sc, 11), bold=False)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(180, _sc(92, self._sc, 72))
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
        archived = bool(index.data(ROLE_DONE))
        pinned = bool(index.data(ROLE_FLAG))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            bg, border = P5_SELECTED, RED
        elif hovered:
            bg, border = P5_HOVER, P5_BORDER_HI
        else:
            bg, border = P5_SURFACE, P5_BORDER

        grad = QLinearGradient(card.topLeft(), card.bottomLeft())
        grad.setColorAt(0.0, bg.lighter(108))
        grad.setColorAt(1.0, bg)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(grad)
        painter.drawRoundedRect(card, 8.0, 8.0)

        # Left stripe: Crimson when selected, Morgana Gold when pinned
        if selected or pinned:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(RED if selected else GOLD)
            painter.drawRoundedRect(QRectF(card.left(), card.top() + 5, 3.5, card.height() - 10), 1.5, 1.5)

        tx = card.left() + 12
        tw = max(40.0, card.right() - tx - 10)

        # Title + optional gold pin icon
        title = index.data(Qt.ItemDataRole.DisplayRole) or "(Untitled)"
        tfm = QFontMetrics(self._title_font)
        painter.setFont(self._title_font)
        painter.setPen(P5_MUTED if archived else WHITE)
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

        # Bottom strip: notebook chip + first #tag chip (or ARCHIVED) + updated date
        sc = self._sc
        ch = 16.0 * sc
        by = card.bottom() - ch - 5.0
        x_cur = tx
        cfm = QFontMetrics(self._chip_font)
        date_txt = index.data(ROLE_DATE) or ""
        dfm = QFontMetrics(self._date_font)
        date_w = (dfm.horizontalAdvance(date_txt) + 8.0) if date_txt else 0.0
        max_chip_right = card.right() - date_w - 8.0

        cat = (index.data(ROLE_CAT) or "Personal").upper()
        cw = cfm.horizontalAdvance(cat) + 10.0 * sc
        cpill = QRectF(x_cur, by, cw, ch)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#242432"))
        painter.drawRoundedRect(cpill, 4.0, 4.0)
        painter.setFont(self._chip_font)
        painter.setPen(QColor("#D0D0E2"))
        painter.drawText(cpill, Qt.AlignmentFlag.AlignCenter.value, cat)
        x_cur = cpill.right() + 4.0

        badge = index.data(ROLE_BADGE) or ""
        tags = index.data(ROLE_TAGS) or []
        if badge:
            bw = cfm.horizontalAdvance(badge) + 10.0 * sc
            if x_cur + bw <= max_chip_right:
                bpill = QRectF(x_cur, by, bw, ch)
                bbg, bfg = (P5_RED_BG, P5_RED_FG) if badge == "LEGACY DUE" else (QColor("#22222C"), P5_MUTED)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bbg)
                painter.drawRoundedRect(bpill, 4.0, 4.0)
                painter.setFont(self._chip_font)
                painter.setPen(bfg)
                painter.drawText(bpill, Qt.AlignmentFlag.AlignCenter.value, badge)
                x_cur = bpill.right() + 4.0
        elif tags:
            tag_lbl = f"#{tags[0]}"
            bw = cfm.horizontalAdvance(tag_lbl) + 10.0 * sc
            if x_cur + bw <= max_chip_right:
                tpill = QRectF(x_cur, by, bw, ch)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(P5_GOLD_BG)
                painter.drawRoundedRect(tpill, 4.0, 4.0)
                painter.setFont(self._chip_font)
                painter.setPen(P5_GOLD_FG)
                painter.drawText(tpill, Qt.AlignmentFlag.AlignCenter.value, tag_lbl)
                x_cur = tpill.right() + 4.0

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
        super().__init__("TASKS", QSize(560, 660), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self._filter = "all"
        self._new_high = False
        self._new_due_on = False
        self._new_stat = "proficiency"
        self._undo_items: list[dict] = []

        # Header quick actions
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            f'color: #FFE46B; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800; letter-spacing: 0.5px;'
        )
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

        # Social Stat selector strip for new tasks (KNO / GUT / PRO / CHA / KIN)
        stat_row = QHBoxLayout()
        stat_row.setSpacing(_sc(4, sc, 3))
        st_lbl = QLabel("STAT")
        st_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(11, sc, 9)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        stat_row.addWidget(st_lbl)
        self._stat_chips: dict[str, P5FluidButton] = {}
        stat_tips = {
            "knowledge": "Knowledge — study, reading, research",
            "guts": "Guts — tough chores, workouts, facing delays",
            "proficiency": "Proficiency — coding, craft, work tasks",
            "charm": "Charm — design, communication, presentations",
            "kindness": "Kindness — helping others, self-care, organizing",
        }
        for sk in ("knowledge", "guts", "proficiency", "charm", "kindness"):
            sb = self._btn(SOCIAL_STATS[sk]["short"], variant="soft", display=True, px=9)
            sb.setFixedHeight(_sc(24, sc, 20))
            sb.setToolTip(stat_tips.get(sk, SOCIAL_STATS[sk]["label"]))
            sb.clicked.connect(lambda _c=False, k=sk: self._set_new_stat(k))
            self._stat_chips[sk] = sb
            stat_row.addWidget(sb)
        self._stat_chips[self._new_stat].set_active(True, active_variant="gold")
        stat_row.addStretch(1)
        self._stat_hint_lbl = QLabel("Proficiency")
        self._stat_hint_lbl.setObjectName("tip")
        stat_row.addWidget(self._stat_hint_lbl)
        self.body.addLayout(stat_row)

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

        self._due_edit = P5DateTimeEdit()
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
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._open_task_context_menu)
        self._delegate = TaskRowDelegate(self.list, scale=sc)
        self.list.setItemDelegate(self._delegate)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemSelectionChanged.connect(self._sync_selection_ui)
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
        foot.setSpacing(_sc(5, sc, 4))
        self._foot_tip = QLabel("Space: done · Right-click: stat")
        self._foot_tip.setObjectName("tip")
        foot.addWidget(self._foot_tip, 1)

        self._foot_due_btn = self._btn("DUE", variant="soft", icon="clock", display=True, px=9)
        self._foot_due_btn.setFixedHeight(_sc(28, sc, 24))
        self._foot_due_btn.setToolTip("Set or clear deadline on selected task(s)")
        self._foot_due_btn.clicked.connect(self._open_due_for_selected)
        foot.addWidget(self._foot_due_btn)

        self._set_stat_btn = self._btn("STAT ▾", variant="gold", display=True, px=9)
        self._set_stat_btn.setFixedHeight(_sc(28, sc, 24))
        self._set_stat_btn.setToolTip("Change Social Stat type for selected task(s)")
        self._set_stat_btn.clicked.connect(self._open_stat_menu_for_selected)
        foot.addWidget(self._set_stat_btn)

        self._foot_prio_btn = self._btn("PRIO", variant="soft", icon="bolt", display=True, px=9)
        self._foot_prio_btn.setFixedHeight(_sc(28, sc, 24))
        self._foot_prio_btn.setToolTip("Toggle High priority on selected task(s)")
        self._foot_prio_btn.clicked.connect(self._toggle_selected_priority)
        foot.addWidget(self._foot_prio_btn)

        self._foot_del_btn = self._btn("DEL", variant="danger", display=True, px=9)
        self._foot_del_btn.setFixedHeight(_sc(28, sc, 24))
        self._foot_del_btn.setToolTip("Delete selected task(s) (Del)")
        self._foot_del_btn.clicked.connect(self._delete_selected)
        foot.addWidget(self._foot_del_btn)

        self._foot_clear_btn = self._btn("CLEAR DONE", variant="soft", display=True, px=9)
        self._foot_clear_btn.setFixedHeight(_sc(28, sc, 24))
        self._foot_clear_btn.clicked.connect(self._clear_done)
        foot.addWidget(self._foot_clear_btn)
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

    def _set_new_stat(self, stat_key: str) -> None:
        if stat_key not in SOCIAL_STATS:
            return
        self._new_stat = stat_key
        for k, btn in self._stat_chips.items():
            btn.set_active(k == stat_key, active_variant="gold")
        self._stat_hint_lbl.setText(SOCIAL_STATS[stat_key]["label"])

    def _apply_stat_to_selected(self, stat_key: str) -> None:
        changed = False
        for item in self.list.selectedItems():
            tid = item.data(ROLE_ID)
            if tid and self.store.set_stat(tid, stat_key):
                changed = True
        if changed:
            self.refresh()
            self.notify(f"Stat set to {SOCIAL_STATS[stat_key]['label']}", tone="gold", ms=1800)
            self.tasks_changed.emit()

    def _open_stat_menu_for_selected(self) -> None:
        sel = [i for i in self.list.selectedItems() if i.data(ROLE_ID)]
        m = QMenu(self)
        m.setStyleSheet(menu_qss(self._ui_scale))
        for sk in ("knowledge", "guts", "proficiency", "charm", "kindness"):
            meta = SOCIAL_STATS[sk]
            if sel:
                m.addAction(f"{meta['short']} · {meta['label']}", lambda k=sk: self._apply_stat_to_selected(k))
            else:
                m.addAction(f"{meta['short']} · {meta['label']} (new task)", lambda k=sk: self._set_new_stat(k))
        pos = self._set_stat_btn.mapToGlobal(QPoint(0, -m.sizeHint().height() - 4))
        m.exec(pos)

    def _open_task_context_menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None or item.data(ROLE_ID) is None:
            return
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        m = QMenu(self)
        m.setStyleSheet(menu_qss(self._ui_scale))
        stat_sub = m.addMenu("SOCIAL STAT")
        stat_sub.setStyleSheet(menu_qss(self._ui_scale))
        for sk in ("knowledge", "guts", "proficiency", "charm", "kindness"):
            meta = SOCIAL_STATS[sk]
            stat_sub.addAction(f"{meta['short']} · {meta['label']}", lambda k=sk: self._apply_stat_to_selected(k))
        m.addAction("TOGGLE PRIORITY", self._toggle_selected_priority)
        m.addAction("SET DEADLINE...", self._open_due_for_selected)
        m.addSeparator()
        m.addAction("DELETE", self._delete_selected)
        m.exec(self.list.viewport().mapToGlobal(pos))

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
        self.notify(f"Deadline preset: {target:%d %b %H:%M}", tone="gold", ms=1500)

    def _apply_due_to_selected(self) -> None:
        dt = _from_qdt(self._due_edit.dateTime())
        changed = False
        for item in self.list.selectedItems():
            tid = item.data(ROLE_ID)
            if tid and self.store.set_deadline(tid, dt):
                changed = True
        if changed:
            self.refresh()
            self.notify(f"Deadline set: {dt:%d %b %H:%M}", tone="gold")
            self.tasks_changed.emit()
        else:
            self.notify("Select a task in the list first to apply deadline", tone="warn")

    def _clear_due_selected(self) -> None:
        self._due_check.setChecked(False)
        changed = False
        for item in self.list.selectedItems():
            tid = item.data(ROLE_ID)
            if tid and self.store.set_deadline(tid, None):
                changed = True
        if changed:
            self.refresh()
            self.notify("Deadline cleared", tone="info")
            self.tasks_changed.emit()

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, chip in self._chips.items():
            chip.set_active(k == key)
        self.refresh()

    def _sync_selection_ui(self) -> None:
        sel = [i for i in self.list.selectedItems() if i.data(ROLE_ID)]
        has_sel = bool(sel)
        self._foot_due_btn.setEnabled(has_sel)
        self._foot_prio_btn.setEnabled(has_sel)
        self._foot_del_btn.setEnabled(has_sel)
        if has_sel:
            n = len(sel)
            self._foot_tip.setText(f"{n} sel · Space / Del")
        else:
            self._foot_tip.setText("Space: done · R-click: stat")

    def _update_status(self) -> None:
        tasks = self.store.all()
        done = sum(1 for t in tasks if t.get("done"))
        self.progress.setRange(0, max(len(tasks), 1))
        self.progress.setValue(done)
        self._count_lbl.setText(f"{len(tasks) - done} LEFT · {done} DONE")
        self._foot_clear_btn.setEnabled(done > 0)
        self._foot_clear_btn.setText(f"CLEAR DONE ({done})" if done > 0 else "CLEAR DONE")
        self._sync_selection_ui()

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
            stat_k = self.store.stat_of(t)
            item = QListWidgetItem(t.get("title", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsEditable)
            item.setData(ROLE_ID, t["id"])
            item.setData(ROLE_DONE, bool(t.get("done")))
            item.setData(ROLE_FLAG, t.get("priority") == "high")
            item.setData(ROLE_CAT, SOCIAL_STATS[stat_k]["short"])
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
            stat=self._new_stat,
        )
        if task is None:
            self.nudge_input(self.input, "Type a task title first!", tone="warn")
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
        self.notify(f"Added task: {task['title'][:36]}", tone="ok")
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
                self.notify(f"Completed '{task['title'][:34]}'!", tone="ok")
                self.task_reward_eligible.emit(task_id)
                self.task_completed.emit(task["title"], len(self.store.pending()))
            else:
                play_ui("cancel")
                self._delegate.clear_anim(task_id)
                self.notify("Marked task as pending", tone="info", ms=1500)
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
            self.notify("Updated task priority", tone="gold", ms=1600)
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
        self.notify(f"Deleted {n} {'task' if n == 1 else 'tasks'} · Undo available", tone="info")

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
        self.notify(f"Cleared {n} completed {'task' if n == 1 else 'tasks'}", tone="info")

    def _undo_delete(self) -> None:
        if self._undo_items:
            self.store.restore(self._undo_items)
            self._undo_items = []
            self.refresh()
            self.notify("Restored deleted task(s)", tone="ok")
            self.tasks_changed.emit()


# ---------------------------------------------------------------------------
# P5RichNoteEditor: Live WYSIWYG Rich-Text Markdown Editor (Bear / Notion / Obsidian style)
# ---------------------------------------------------------------------------
_NOTE_TAG_HL_RE = re.compile(r"(?<![\w/#])#[A-Za-z][A-Za-z0-9_-]{0,23}")
_MD_ESC_TAG_RE = re.compile(r"(?m)^\\#([A-Za-z][A-Za-z0-9_-]{0,23})")
_MD_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_MD_CODE_ESC_RE = re.compile(r"\\([#\-+*_[\]()>])")
_MD_BOLD_END_RE = re.compile(r"\*\*([^*\n]+)\*\*$")
_MD_ITALIC_END_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*$")
_MD_CODE_END_RE = re.compile(r"`([^`\n]+)`$")


def _is_code_char_format(cf: QTextCharFormat) -> bool:
    if cf.fontFixedPitch():
        return True
    fams = cf.fontFamilies()
    if isinstance(fams, (list, tuple)):
        return any("mono" in str(f).lower() or "consolas" in str(f).lower() for f in fams)
    return "mono" in str(fams or "").lower()


def _is_quote_block_format(bf: QTextBlockFormat) -> bool:
    try:
        return int(bf.property(QTextFormat.Property.BlockQuoteLevel) or 0) > 0
    except Exception:
        return False


class P5TagHighlighter(QSyntaxHighlighter):
    """Visual-only styling for `#tags`, checked checklists, and blockquotes without mutating Markdown."""

    def __init__(self, doc: QTextDocument) -> None:
        super().__init__(doc)
        self._tag_fmt = QTextCharFormat()
        self._tag_fmt.setForeground(GOLD)
        self._tag_fmt.setFontWeight(QFont.Weight.Bold)

        self._tag_checked_fmt = QTextCharFormat(self._tag_fmt)
        self._tag_checked_fmt.setFontStrikeOut(True)

        self._checked_fmt = QTextCharFormat()
        self._checked_fmt.setForeground(QColor("#7D7D92"))
        self._checked_fmt.setFontStrikeOut(True)

        self._checked_code_fmt = QTextCharFormat()
        self._checked_code_fmt.setFontStrikeOut(True)

        self._quote_fmt = QTextCharFormat()
        self._quote_fmt.setForeground(QColor("#C6C6DC"))
        self._quote_fmt.setFontItalic(True)

    def highlightBlock(self, text: str) -> None:
        if not text:
            return
        block = self.currentBlock()
        bf = block.blockFormat()
        is_checked = (
            block.textList() is not None
            and bf.marker() == QTextBlockFormat.MarkerType.Checked
        )
        is_quote = _is_quote_block_format(bf)

        if is_checked or is_quote:
            blk_pos = block.position()
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    start = frag.position() - blk_pos
                    length = frag.length()
                    if _is_code_char_format(frag.charFormat()):
                        if is_checked:
                            self.setFormat(start, length, self._checked_code_fmt)
                    else:
                        self.setFormat(start, length, self._checked_fmt if is_checked else self._quote_fmt)
                it += 1

        if "#" in text and bf.headingLevel() == 0:
            tf = self._tag_checked_fmt if is_checked else self._tag_fmt
            for m in _NOTE_TAG_HL_RE.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), tf)


class P5RichNoteEditor(QTextEdit):
    """Live WYSIWYG Persona 5 Markdown editor: edit directly inside the rendered rich note.

    Supports:
      * Seamless GitHub-Flavored Markdown load/save roundtrip (`set_markdown_content` /
        `to_markdown_content`) preserving 100% compatibility with existing `notes.json`.
      * Live Markdown-as-you-type shortcuts (`# `, `## `, `### `, `- `, `[] `, `[ ] `, `> `,
        `---`+Enter, `**bold**`, `*italic*`, `` `code` ``).
      * Interactive clickable checkboxes (`☐` ↔ `☑`) painted in Persona 5 crimson/gold style,
        plus `Ctrl+Enter` to toggle the current checklist item.
      * Natural `Enter` / `Backspace` handling (headings reset to normal text on Enter;
        empty list/checklist/quote lines exit cleanly on Enter or Backspace).
      * Clean dark-theme paste stripping external HTML colors/backgrounds.
    """

    checkbox_toggled = pyqtSignal(bool)

    def __init__(self, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sc = max(0.75, min(1.5, float(scale)))
        self._base_pt = max(10.0, round(11.0 * self._sc, 1))
        self._internal = False
        self.setObjectName("noteBody")
        self.setAcceptRichText(True)
        self.setTabChangesFocus(False)
        self.viewport().setMouseTracking(True)
        base_f = ui_font(_sc(14, self._sc, 11), bold=False)
        base_f.setPointSizeF(self._base_pt)
        self.document().setDefaultFont(base_f)
        self.document().setDefaultStyleSheet(
            "hr { background-color: #3E2028; border: none; height: 1px; }"
            "a { color: #FAD02C; }"
        )
        self.document().setDocumentMargin(10.0 * self._sc)
        self._tag_hl = P5TagHighlighter(self.document())
        self.set_markdown_content("")

    # -- format helpers ----------------------------------------------------------
    def _normal_char_format(self) -> QTextCharFormat:
        cf = QTextCharFormat()
        cf.setFont(ui_font(_sc(14, self._sc, 11), bold=False))
        cf.setFontPointSize(self._base_pt)
        cf.setFontWeight(QFont.Weight.Normal)
        cf.setFontItalic(False)
        cf.setFontStrikeOut(False)
        cf.setFontFixedPitch(False)
        cf.setForeground(P5_TEXT)
        cf.clearBackground()
        return cf

    def _heading_char_format(self, level: int) -> QTextCharFormat:
        cf = self._normal_char_format()
        cf.setFontWeight(QFont.Weight.Bold)
        if level == 1:
            cf.setFontPointSize(round(self._base_pt * 1.42, 1))
            cf.setForeground(WHITE)
        elif level == 2:
            cf.setFontPointSize(round(self._base_pt * 1.22, 1))
            cf.setForeground(GOLD)
        else:
            cf.setFontPointSize(round(self._base_pt * 1.08, 1))
            cf.setForeground(QColor("#FF5E6B"))
        return cf

    def _code_char_format(self) -> QTextCharFormat:
        cf = self._normal_char_format()
        cf.setFontFamilies(["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "monospace"])
        cf.setFontFixedPitch(True)
        cf.setFontPointSize(max(9.0, round(self._base_pt * 0.94, 1)))
        cf.setForeground(GOLD)
        cf.setBackground(QColor("#222230"))
        return cf

    @staticmethod
    def _select_block_text(block: QTextBlock) -> QTextCursor:
        """Select strictly this block's characters without touching the previous block break."""
        c = QTextCursor(block)
        c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        return c

    @staticmethod
    def _is_quote(bf: QTextBlockFormat) -> bool:
        return _is_quote_block_format(bf)

    @staticmethod
    def _is_code_fmt(cf: QTextCharFormat) -> bool:
        return _is_code_char_format(cf)

    # -- Markdown load / save roundtrip ------------------------------------------
    _EMPTY_LIST_RE = re.compile(r"(?m)^([ \t]*(?:[-*+]|\d+\.)(?: \[[ xX]\])?)[ \t]*$")

    def set_markdown_content(self, md: str) -> None:
        self._internal = True
        was_blocked = self.blockSignals(True)
        doc = self.document()
        doc.setUndoRedoEnabled(False)
        try:
            text = (md or "").strip("\r\n")
            if text:
                # Guard Qt6 md4c importer quirk where empty list items (`- `, `- [ ] `) swallow the next block
                safe_md = self._EMPTY_LIST_RE.sub(lambda m: f"{m.group(1)} \u200b", text)
                doc.setMarkdown(safe_md, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
                self._restyle_document()
            else:
                doc.clear()
                cur = self.textCursor()
                cur.setBlockFormat(QTextBlockFormat())
                cur.setBlockCharFormat(self._normal_char_format())
                cur.setCharFormat(self._normal_char_format())
                self.setTextCursor(cur)
                self.setCurrentCharFormat(self._normal_char_format())
            c = self.textCursor()
            c.movePosition(QTextCursor.MoveOperation.Start)
            self.setTextCursor(c)
        finally:
            doc.setUndoRedoEnabled(True)
            self.blockSignals(was_blocked)
            self._internal = False
            self._tag_hl.rehighlight()
            self.viewport().update()

    def to_markdown_content(self) -> str:
        """Export clean, standard GitHub-Flavored Markdown compatible with `notes.json`."""
        raw = self.document().toMarkdown(QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        raw = raw.replace("\u200b", "")
        # Unescape `#tag` at start of line (Qt escapes `#` even without a trailing space)
        raw = _MD_ESC_TAG_RE.sub(r"#\1", raw)
        # Clean Qt6 spurious backslash escapes inside inline code spans (`\## ` -> `## `)
        raw = _MD_CODE_SPAN_RE.sub(lambda m: "`" + _MD_CODE_ESC_RE.sub(r"\1", m.group(1)) + "`", raw)
        # Unescape mid-line ` \+ ` produced by Qt6 Markdown writer
        raw = raw.replace(" \\+ ", " + ")
        # Normalize Qt's `- - -` horizontal rule back to crisp `---`
        lines = ["---" if ln.strip() in ("- - -", "* * *") else ln.rstrip() for ln in raw.splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def _restyle_document(self) -> None:
        """Apply cohesive Persona 5 dark typography to every block parsed from Markdown."""
        sc = self._sc
        doc = self.document()
        block = doc.begin()
        while block.isValid():
            # Strip temporary empty-list sentinel cleanly while keeping the list/checkbox block intact
            if block.text() == "\u200b":
                sc0 = self._select_block_text(block)
                sc0.removeSelectedText()

            bf = QTextBlockFormat(block.blockFormat())
            level = bf.headingLevel()
            if level > 0 and block.textList() is not None:
                block.textList().remove(block)
                bf = QTextBlockFormat(block.blockFormat())
            is_list = block.textList() is not None
            is_quote = self._is_quote(bf)
            marker = bf.marker()

            # Fix Qt6 parser quirk where non-list blocks after checklists inherit MarkerType
            if not is_list and marker != QTextBlockFormat.MarkerType.NoMarker:
                bf.setMarker(QTextBlockFormat.MarkerType.NoMarker)

            cur = self._select_block_text(block)

            if level > 0:
                bf.setTopMargin((10.0 if level == 1 else (8.0 if level == 2 else 6.0)) * sc)
                bf.setBottomMargin(3.0 * sc)
                hcf = self._heading_char_format(level)
                cur.setBlockFormat(bf)
                cur.setBlockCharFormat(hcf)
                if cur.hasSelection():
                    cur.mergeCharFormat(hcf)
            else:
                if is_quote:
                    bf.setLeftMargin(18.0 * sc)
                    bf.setRightMargin(8.0 * sc)
                    bf.setTopMargin(4.0 * sc)
                    bf.setBottomMargin(4.0 * sc)
                else:
                    bf.setTopMargin((2.5 if is_list else 2.0) * sc)
                    bf.setBottomMargin((2.5 if is_list else 3.0) * sc)
                cur.setBlockFormat(bf)
                cur.setBlockCharFormat(self._normal_char_format())

                # Preserve inline bold/italic/code while enforcing clean Persona 5 colors & font size
                # (Visual-only checked strikethrough & blockquote italic are handled by P5TagHighlighter
                # so `to_markdown_content()` stays 100% pure Markdown without extra `~~` or `*` wrappers.)
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    if frag.isValid():
                        fc = QTextCursor(doc)
                        fc.setPosition(frag.position())
                        fc.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                        if self._is_code_fmt(frag.charFormat()):
                            fc.mergeCharFormat(self._code_char_format())
                        else:
                            ncf = QTextCharFormat()
                            ncf.setFontPointSize(self._base_pt)
                            ncf.setForeground(P5_TEXT)
                            fc.mergeCharFormat(ncf)
                    it += 1
            block = block.next()

    # -- Paste hygiene (never paste white backgrounds / broken external styles) --
    def insertFromMimeData(self, source) -> None:
        if source is not None and source.hasText():
            self.insertPlainText(source.text())

    # -- Custom Persona 5 painting: crimson quote bar & crisp checkboxes ---------
    def _checkbox_rect_for_block(self, block: QTextBlock) -> QRectF | None:
        if not block.isValid() or block.textList() is None:
            return None
        marker = block.blockFormat().marker()
        if marker not in (QTextBlockFormat.MarkerType.Unchecked, QTextBlockFormat.MarkerType.Checked):
            return None
        c = QTextCursor(block)
        cr = self.cursorRect(c)
        bs = max(12.0, round(14.0 * self._sc))
        bx = cr.left() - bs - max(7.0, 8.0 * self._sc)
        by = cr.top() + (cr.height() - bs) / 2.0
        return QRectF(bx, by, bs, bs)

    def _checkbox_block_at(self, pos: QPoint) -> QTextBlock | None:
        vp_h = self.viewport().height()
        block = self.cursorForPosition(QPoint(0, max(0, pos.y() - 24))).block()
        while block.isValid():
            cr = self.cursorRect(QTextCursor(block))
            if cr.top() > vp_h + 20:
                break
            box = self._checkbox_rect_for_block(block)
            if box is not None and box.adjusted(-6, -5, 6, 5).contains(QPointF(pos)):
                return block
            block = block.next()
        return None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        vp_h = self.viewport().height()
        sc = self._sc

        block = self.cursorForPosition(QPoint(0, 0)).block()
        if block.previous().isValid():
            block = block.previous()
        while block.isValid():
            first_cr = self.cursorRect(QTextCursor(block))
            if first_cr.top() > vp_h + 24:
                break
            bf = block.blockFormat()

            # 1) Crimson Persona 5 vertical accent bar for blockquotes
            if self._is_quote(bf):
                end_c = QTextCursor(block)
                end_c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                last_cr = self.cursorRect(end_c)
                bar_x = max(6.0, first_cr.left() - 12.0 * sc)
                bar_top = float(first_cr.top() + 2)
                bar_h = max(float(first_cr.height() - 4), float(last_cr.bottom() - first_cr.top() - 4))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(RED)
                p.drawRoundedRect(QRectF(bar_x, bar_top, 3.2 * sc, bar_h), 1.5, 1.5)

            # 2) Crisp Persona 5 interactive rounded checkboxes covering Qt's default glyph
            box = self._checkbox_rect_for_block(block)
            if box is not None:
                checked = bf.marker() == QTextBlockFormat.MarkerType.Checked
                # Erase Qt's entire built-in checkbox marker region cleanly with exact #noteBody surface
                erase_left = max(2.0, box.left() - 8.0 * sc)
                erase_right = max(box.right() + 2.0, float(first_cr.left()) - 2.0)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#121217"))
                p.drawRect(QRectF(erase_left, float(first_cr.top()) - 1.0,
                                  erase_right - erase_left, float(first_cr.height()) + 2.0))
                if checked:
                    p.setPen(QPen(WHITE, 1.3))
                    p.setBrush(RED)
                    p.drawRoundedRect(box, 3.5, 3.5)
                    c = box.center()
                    r = box.width() * 0.28
                    path = QPainterPath()
                    path.moveTo(c.x() - r * 1.15, c.y() + r * 0.1)
                    path.lineTo(c.x() - r * 0.25, c.y() + r * 1.0)
                    path.lineTo(c.x() + r * 1.25, c.y() - r * 0.95)
                    p.setPen(QPen(WHITE, max(1.7, 1.9 * sc), Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
                else:
                    p.setPen(QPen(QColor("#9292A6"), 1.4))
                    p.setBrush(P5_BG)
                    p.drawRoundedRect(box, 3.5, 3.5)
            block = block.next()
        p.end()

    # -- Interactive checkbox click & hover cursor -------------------------------
    def _toggle_block_checkbox(self, block: QTextBlock) -> bool:
        if not block.isValid() or block.textList() is None:
            return False
        bf = QTextBlockFormat(block.blockFormat())
        marker = bf.marker()
        if marker not in (QTextBlockFormat.MarkerType.Unchecked, QTextBlockFormat.MarkerType.Checked):
            return False
        now_checked = marker == QTextBlockFormat.MarkerType.Unchecked
        bf.setMarker(QTextBlockFormat.MarkerType.Checked if now_checked else QTextBlockFormat.MarkerType.Unchecked)

        cur = QTextCursor(block)
        cur.beginEditBlock()
        cur.setBlockFormat(bf)
        cur.endEditBlock()
        self._tag_hl.rehighlightBlock(block)
        play_ui("select" if now_checked else "cancel")
        self.viewport().update()
        self.checkbox_toggled.emit(now_checked)
        return True

    def mouseMoveEvent(self, e) -> None:
        hit = self._checkbox_block_at(e.position().toPoint())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if hit is not None else Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self._checkbox_block_at(e.position().toPoint())
            if hit is not None:
                self._toggle_block_checkbox(hit)
                e.accept()
                return
        super().mousePressEvent(e)

    # -- Block reset & conversion helpers ----------------------------------------
    def _restore_offset(self, cur: QTextCursor, orig_off: int, cf: QTextCharFormat) -> None:
        blk = cur.block()
        cur.setPosition(blk.position() + min(orig_off, len(blk.text())))
        self.setTextCursor(cur)
        self.setCurrentCharFormat(cf)

    def _reset_block_to_normal(self, cur: QTextCursor) -> None:
        orig_off = cur.position() - cur.block().position()
        lst = cur.currentList()
        if lst is not None:
            lst.remove(cur.block())
        bf = QTextBlockFormat()
        bf.setTopMargin(2.0 * self._sc)
        bf.setBottomMargin(3.0 * self._sc)
        ncf = self._normal_char_format()
        scur = self._select_block_text(cur.block())
        scur.setBlockFormat(bf)
        scur.setBlockCharFormat(ncf)
        if scur.hasSelection():
            scur.mergeCharFormat(ncf)
        self._restore_offset(cur, orig_off, ncf)
        self._tag_hl.rehighlightBlock(cur.block())
        self.viewport().update()

    def _apply_heading(self, cur: QTextCursor, level: int) -> None:
        orig_off = cur.position() - cur.block().position()
        lst = cur.currentList()
        if lst is not None:
            lst.remove(cur.block())
        bf = QTextBlockFormat()
        bf.setHeadingLevel(level)
        bf.setTopMargin((10.0 if level == 1 else (8.0 if level == 2 else 6.0)) * self._sc)
        bf.setBottomMargin(3.0 * self._sc)
        hcf = self._heading_char_format(level)
        scur = self._select_block_text(cur.block())
        scur.setBlockFormat(bf)
        scur.setBlockCharFormat(hcf)
        if scur.hasSelection():
            scur.mergeCharFormat(hcf)
        self._restore_offset(cur, orig_off, hcf)
        self._tag_hl.rehighlightBlock(cur.block())

    def _apply_list(self, cur: QTextCursor, checklist: bool = False, checked: bool = False) -> None:
        orig_off = cur.position() - cur.block().position()
        if cur.currentList() is None:
            lf = QTextListFormat()
            lf.setStyle(QTextListFormat.Style.ListDisc)
            cur.createList(lf)
        bf0 = QTextBlockFormat(cur.blockFormat())
        bf0.setHeadingLevel(0)
        bf0.clearProperty(QTextFormat.Property.BlockQuoteLevel)
        bf0.setLeftMargin(0.0)
        bf0.setTopMargin(2.5 * self._sc)
        bf0.setBottomMargin(2.5 * self._sc)
        if checklist:
            bf0.setMarker(QTextBlockFormat.MarkerType.Checked if checked else QTextBlockFormat.MarkerType.Unchecked)
        else:
            bf0.setMarker(QTextBlockFormat.MarkerType.NoMarker)
        ncf = self._normal_char_format()
        scur = self._select_block_text(cur.block())
        scur.setBlockFormat(bf0)
        scur.setBlockCharFormat(ncf)
        if scur.hasSelection():
            scur.mergeCharFormat(ncf)
        self._restore_offset(cur, orig_off, ncf)
        self._tag_hl.rehighlightBlock(cur.block())
        self.viewport().update()

    def _apply_blockquote(self, cur: QTextCursor) -> None:
        orig_off = cur.position() - cur.block().position()
        lst = cur.currentList()
        if lst is not None:
            lst.remove(cur.block())
        bf = QTextBlockFormat()
        bf.setProperty(QTextFormat.Property.BlockQuoteLevel, 1)
        bf.setLeftMargin(18.0 * self._sc)
        bf.setRightMargin(8.0 * self._sc)
        bf.setTopMargin(4.0 * self._sc)
        bf.setBottomMargin(4.0 * self._sc)
        ncf = self._normal_char_format()
        scur = self._select_block_text(cur.block())
        scur.setBlockFormat(bf)
        scur.setBlockCharFormat(ncf)
        if scur.hasSelection():
            scur.mergeCharFormat(ncf)
        self._restore_offset(cur, orig_off, ncf)
        self._tag_hl.rehighlightBlock(cur.block())
        self.viewport().update()

    # -- Live Markdown-as-you-type keyboard ergonomics ---------------------------
    def _try_inline_markdown_convert(self) -> None:
        cur = self.textCursor()
        if cur.hasSelection():
            return
        block = cur.block()
        if block.blockFormat().headingLevel() > 0:
            return
        pos_in_blk = cur.positionInBlock()
        prefix = block.text()[:pos_in_blk]
        if len(prefix) < 3:
            return

        for kind, regex in (("bold", _MD_BOLD_END_RE), ("code", _MD_CODE_END_RE), ("italic", _MD_ITALIC_END_RE)):
            m = regex.search(prefix)
            if not m:
                continue
            inner = m.group(1)
            if not inner or inner.startswith(" ") or inner.endswith(" "):
                continue
            start_pos = block.position() + m.start()
            end_pos = block.position() + m.end()
            ec = QTextCursor(self.document())
            ec.beginEditBlock()
            ec.setPosition(start_pos)
            ec.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
            if kind == "bold":
                cf = self._normal_char_format()
                cf.setFontWeight(QFont.Weight.Bold)
            elif kind == "italic":
                cf = self._normal_char_format()
                cf.setFontItalic(True)
            else:
                cf = self._code_char_format()
            ec.insertText(inner, cf)
            ec.endEditBlock()
            self.setTextCursor(ec)
            self.setCurrentCharFormat(self._normal_char_format())
            return

    def keyPressEvent(self, e) -> None:
        key = e.key()
        mods = e.modifiers()
        cur = self.textCursor()
        block = cur.block()
        bf = block.blockFormat()

        # Ctrl+Enter toggles current checklist checkbox
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (mods & Qt.KeyboardModifier.ControlModifier):
            if self._toggle_block_checkbox(block):
                e.accept()
                return

        # Live block Markdown triggers when pressing Space after `#`, `##`, `-`, `[]`, `>`
        if key == Qt.Key.Key_Space and not cur.hasSelection():
            if bf.headingLevel() == 0 and cur.currentList() is None and not self._is_quote(bf):
                prefix = block.text()[:cur.positionInBlock()]
                if prefix in ("#", "##", "###", "-", "*", "[]", "[ ]", "[x]", "[X]", ">"):
                    cur.beginEditBlock()
                    cur.setPosition(block.position())
                    cur.setPosition(block.position() + len(prefix), QTextCursor.MoveMode.KeepAnchor)
                    cur.removeSelectedText()
                    if prefix == "#":
                        self._apply_heading(cur, 1)
                    elif prefix == "##":
                        self._apply_heading(cur, 2)
                    elif prefix == "###":
                        self._apply_heading(cur, 3)
                    elif prefix in ("-", "*"):
                        self._apply_list(cur, checklist=False)
                    elif prefix in ("[]", "[ ]"):
                        self._apply_list(cur, checklist=True, checked=False)
                    elif prefix in ("[x]", "[X]"):
                        self._apply_list(cur, checklist=True, checked=True)
                    elif prefix == ">":
                        self._apply_blockquote(cur)
                    cur.endEditBlock()
                    e.accept()
                    return

        # Natural Enter behavior: divider on `---`, exit heading / empty list / quote cleanly
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (mods & Qt.KeyboardModifier.ShiftModifier):
            stripped = block.text().strip()
            if stripped in ("---", "***") and bf.headingLevel() == 0 and cur.currentList() is None:
                cur.beginEditBlock()
                cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                cur.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cur.removeSelectedText()
                cur.insertHtml("<hr/>")
                nbf = QTextBlockFormat()
                nbf.setTopMargin(2.0 * self._sc)
                nbf.setBottomMargin(3.0 * self._sc)
                ncf = self._normal_char_format()
                cur.insertBlock(nbf, ncf)
                cur.setBlockCharFormat(ncf)
                cur.endEditBlock()
                self.setTextCursor(cur)
                self.setCurrentCharFormat(ncf)
                e.accept()
                return
            if bf.headingLevel() > 0:
                cur.beginEditBlock()
                nbf = QTextBlockFormat()
                nbf.setTopMargin(2.0 * self._sc)
                nbf.setBottomMargin(3.0 * self._sc)
                ncf = self._normal_char_format()
                cur.insertBlock(nbf, ncf)
                cur.setBlockCharFormat(ncf)
                cur.endEditBlock()
                self.setTextCursor(cur)
                self.setCurrentCharFormat(ncf)
                e.accept()
                return
            if (cur.currentList() is not None or self._is_quote(bf)) and not stripped:
                cur.beginEditBlock()
                self._reset_block_to_normal(cur)
                cur.endEditBlock()
                e.accept()
                return
            if cur.currentList() is not None and bf.marker() in (
                QTextBlockFormat.MarkerType.Unchecked, QTextBlockFormat.MarkerType.Checked
            ):
                super().keyPressEvent(e)
                nc = self.textCursor()
                nbf = QTextBlockFormat(nc.blockFormat())
                nbf.setMarker(QTextBlockFormat.MarkerType.Unchecked)
                ncf = self._normal_char_format()
                nc.setBlockFormat(nbf)
                nc.setBlockCharFormat(ncf)
                nc.setCharFormat(ncf)
                self.setTextCursor(nc)
                self.setCurrentCharFormat(ncf)
                self.viewport().update()
                return

        # Natural Backspace at column 0 converts Heading / List / Quote back to normal text
        if key == Qt.Key.Key_Backspace and not cur.hasSelection() and cur.positionInBlock() == 0:
            if bf.headingLevel() > 0 or cur.currentList() is not None or self._is_quote(bf):
                cur.beginEditBlock()
                self._reset_block_to_normal(cur)
                cur.endEditBlock()
                e.accept()
                return

        super().keyPressEvent(e)
        if e.text() in ("*", "`"):
            self._try_inline_markdown_convert()

    # -- Direct WYSIWYG toolbar actions ------------------------------------------
    def toggle_heading(self, level: int = 2) -> None:
        cur = self.textCursor()
        cur.beginEditBlock()
        if cur.blockFormat().headingLevel() == level:
            self._reset_block_to_normal(cur)
        else:
            self._apply_heading(cur, level)
        cur.endEditBlock()
        self.setFocus()

    def toggle_bold(self) -> None:
        cur = self.textCursor()
        want = self.fontWeight() < QFont.Weight.Bold
        cf = QTextCharFormat()
        cf.setFontWeight(QFont.Weight.Bold if want else QFont.Weight.Normal)
        if cur.hasSelection():
            cur.mergeCharFormat(cf)
        self.mergeCurrentCharFormat(cf)
        self.setFocus()

    def toggle_italic(self) -> None:
        cur = self.textCursor()
        want = not self.fontItalic()
        cf = QTextCharFormat()
        cf.setFontItalic(want)
        if cur.hasSelection():
            cur.mergeCharFormat(cf)
        self.mergeCurrentCharFormat(cf)
        self.setFocus()

    def toggle_inline_code(self) -> None:
        cur = self.textCursor()
        is_code = self._is_code_fmt(self.currentCharFormat())
        cf = self._normal_char_format() if is_code else self._code_char_format()
        if cur.hasSelection():
            cur.mergeCharFormat(cf)
        self.mergeCurrentCharFormat(cf)
        self.setFocus()

    def toggle_bullet_list(self) -> None:
        cur = self.textCursor()
        cur.beginEditBlock()
        is_bullet = (
            cur.currentList() is not None
            and cur.blockFormat().marker() == QTextBlockFormat.MarkerType.NoMarker
        )
        if is_bullet:
            self._reset_block_to_normal(cur)
        else:
            self._apply_list(cur, checklist=False)
        cur.endEditBlock()
        self.setFocus()

    def toggle_checklist(self) -> None:
        cur = self.textCursor()
        cur.beginEditBlock()
        is_check = (
            cur.currentList() is not None
            and cur.blockFormat().marker() in (
                QTextBlockFormat.MarkerType.Unchecked, QTextBlockFormat.MarkerType.Checked
            )
        )
        if is_check:
            self._reset_block_to_normal(cur)
        else:
            self._apply_list(cur, checklist=True, checked=False)
        cur.endEditBlock()
        self.setFocus()

    def toggle_blockquote(self) -> None:
        cur = self.textCursor()
        cur.beginEditBlock()
        if self._is_quote(cur.blockFormat()):
            self._reset_block_to_normal(cur)
        else:
            self._apply_blockquote(cur)
        cur.endEditBlock()
        self.setFocus()

    def insert_divider(self) -> None:
        cur = self.textCursor()
        cur.beginEditBlock()
        if cur.block().text().strip():
            cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            cur.insertBlock(QTextBlockFormat(), self._normal_char_format())
        cur.insertHtml("<hr/>")
        cur.insertBlock(QTextBlockFormat(), self._normal_char_format())
        cur.endEditBlock()
        self.setTextCursor(cur)
        self.setCurrentCharFormat(self._normal_char_format())
        self.setFocus()

    def active_formats(self) -> dict[str, bool]:
        cur = self.textCursor()
        bf = cur.blockFormat()
        cf = self.currentCharFormat()
        in_list = cur.currentList() is not None
        mk = bf.marker()
        is_check = in_list and mk in (QTextBlockFormat.MarkerType.Unchecked, QTextBlockFormat.MarkerType.Checked)
        return {
            "h2": bf.headingLevel() > 0,
            "bold": cf.fontWeight() >= QFont.Weight.Bold and bf.headingLevel() == 0,
            "italic": cf.fontItalic() and not self._is_quote(bf),
            "code": self._is_code_fmt(cf),
            "bullet": in_list and not is_check,
            "check": is_check,
            "quote": self._is_quote(bf),
        }


# ---------------------------------------------------------------------------
# NOTES DIALOG (2-column Persona 5 workspace: Live Rich WYSIWYG editor + fast sidebar)
# ---------------------------------------------------------------------------
class NotesDialog(P5FluidPanel):
    """Frictionless Persona 5 Note-Taking workspace (distinct from Tasks).

    Built around proven desktop note UX (Apple Notes / Bear / Obsidian Live Preview):
      * Live WYSIWYG rich Markdown editor by default — edit headings, bold, italic, code,
        blockquotes, `#tags`, and clickable checkboxes directly without switching modes.
      * Optional `RAW MD` source toggle (`Ctrl+E`) synced both ways with live rich view.
      * Zero-friction capture with instant debounced auto-save (no Save button friction).
      * Notebooks (`Personal`, `Study`, `Work`, `Ideas`, `Journal`) + automatic `#tag` discovery.
      * Quick Templates (`Meeting`, `Study`, `Idea`, `Journal`) & 1-click `→ TASK` bridge.
      * Clean Archive + Undo Delete + legacy deadline migration banner for older `notes.json`.
    """

    FILTERS = (("ALL", "all"), ("PIN", "pinned"), ("ARCHIVE", "archived"))
    SORT_MODES = (("recent", "RECENT"), ("alpha", "A–Z"), ("created", "CREATED"))

    open_settings = pyqtSignal()
    open_tasks = pyqtSignal()
    tasks_changed = pyqtSignal()
    notes_changed = pyqtSignal()
    note_completed = pyqtSignal(str)                # kept for legacy signal compatibility
    send_to_task = pyqtSignal(str, object)          # (task_title, optional_deadline_datetime)

    def __init__(self, store: NoteStore, task_store: TaskStore | None = None,
                 on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("NOTES", QSize(870, 625), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self.task_store = task_store
        self._current_id: str | None = None
        self._category = "Personal"
        self._filter = "all"
        self._active_tag = ""
        self._sort_idx = 0
        self._raw_mode = False
        self._loading = False
        self._dirty = False
        self._undo_note: dict | None = None
        self._legacy_dl_note_id: str | None = None

        # Header quick actions
        self._tasks_btn = self._btn("TASKS", variant="soft", display=True, px=10)
        self._tasks_btn.setToolTip("Open Tasks for to-dos & deadlines")
        self._tasks_btn.clicked.connect(lambda _c=False: self.open_tasks.emit())
        self.header_bar.addWidget(self._tasks_btn)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Two-column split workspace
        cols = QHBoxLayout()
        cols.setSpacing(_sc(12, sc, 8))

        # -- Left sidebar: Capture, Search, Filter, #Tags, Note List --
        left_card = _make_card()
        left_card.setMinimumWidth(_sc(228, sc, 195))
        left_card.setMaximumWidth(_sc(305, sc, 245))
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(_sc(7, sc, 5))

        new_row = QHBoxLayout()
        new_row.setSpacing(_sc(5, sc, 4))
        new_btn = self._btn("NEW NOTE", variant="primary", icon="plus", display=True, px=11)
        new_btn.setFixedHeight(_sc(32, sc, 26))
        new_btn.setToolTip("Create blank note (Ctrl+N)")
        new_btn.clicked.connect(lambda _c=False: self._new())
        new_row.addWidget(new_btn, 1)

        self._tmpl_btn = self._btn("TMPL ▾", variant="gold", display=True, px=10)
        self._tmpl_btn.setFixedHeight(_sc(32, sc, 26))
        self._tmpl_btn.setToolTip("Start a note from a quick template")
        self._tmpl_btn.clicked.connect(self._open_template_menu)
        new_row.addWidget(self._tmpl_btn)
        ll.addLayout(new_row)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search notes or #tags... (Ctrl+F)")
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
            frow.addWidget(fb, 1)
        self._fchips["all"].set_active(True)
        ll.addLayout(frow)

        # Dynamic #Tag filter strip inside a sleek horizontal scroll area (zero chip clipping at any scale)
        self._tags_scroll = QScrollArea()
        self._tags_scroll.setWidgetResizable(True)
        self._tags_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tags_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tags_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._tags_scroll.setFixedHeight(_sc(30, sc, 25))
        self._tags_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:horizontal { height: 4px; background: transparent; margin: 0px; }"
            "QScrollBar::handle:horizontal { background: #3D3D4E; border-radius: 2px; min-width: 18px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }"
        )
        self._tags_bar = QWidget()
        self._tags_lay = QHBoxLayout(self._tags_bar)
        self._tags_lay.setContentsMargins(0, 1, 0, 1)
        self._tags_lay.setSpacing(4)
        self._tags_scroll.setWidget(self._tags_bar)
        self._tags_scroll.setVisible(False)
        ll.addWidget(self._tags_scroll)

        mrow = QHBoxLayout()
        self.count_label = QLabel("0 NOTES")
        self.count_label.setObjectName("subhead")
        mrow.addWidget(self.count_label)
        mrow.addStretch(1)
        self._sort_btn = self._btn("RECENT", variant="ghost", icon="sort", px=10)
        self._sort_btn.setFixedHeight(_sc(22, sc, 20))
        self._sort_btn.setToolTip("Switch sort order (Recent / A–Z / Created)")
        self._sort_btn.clicked.connect(self._cycle_sort)
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
        cols.addWidget(left_card, 35)

        # -- Right panel: Clean Note Editor + Markdown Preview --
        editor_card = _make_card()
        el = QVBoxLayout(editor_card)
        el.setContentsMargins(14, 12, 14, 12)
        el.setSpacing(_sc(6, sc, 5))

        # Row 1: Spacious full-width Title + Pin + Delete (title never squeezes at 130%)
        eh = QHBoxLayout()
        eh.setSpacing(_sc(5, sc, 4))
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("titleInput")
        self.title_edit.setPlaceholderText("Untitled note...")
        self.title_edit.textChanged.connect(self._on_edited)
        eh.addWidget(self.title_edit, 1)

        self._pin_btn = self._btn("", variant="soft", icon="pin")
        self._pin_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        self._pin_btn.setToolTip("Pin note to top (Ctrl+P)")
        self._pin_btn.setEnabled(False)
        self._pin_btn.clicked.connect(self._toggle_pin)
        eh.addWidget(self._pin_btn)

        self._del_btn = self._btn("", variant="danger", icon="trash")
        self._del_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        self._del_btn.setToolTip("Delete note (can undo)")
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self._delete)
        eh.addWidget(self._del_btn)
        el.addLayout(eh)

        # Row 2: Editor actions (RAW MD toggle, → TASK bridge, ARCHIVE) + reading stats
        act = QHBoxLayout()
        act.setSpacing(_sc(5, sc, 4))
        self._raw_btn = self._btn("RAW MD", variant="soft", display=True, px=9)
        self._raw_btn.setFixedHeight(_sc(26, sc, 22))
        self._raw_btn.setToolTip("Switch between Live Rich editor and Raw Markdown source (Ctrl+E)")
        self._raw_btn.clicked.connect(self._toggle_raw_mode)
        act.addWidget(self._raw_btn)

        self._to_task_btn = self._btn("→ TASK", variant="gold", display=True, px=9)
        self._to_task_btn.setFixedHeight(_sc(26, sc, 22))
        self._to_task_btn.setToolTip("Promote selected text (or note title) into a Task in Tasks")
        self._to_task_btn.clicked.connect(self._promote_to_task)
        act.addWidget(self._to_task_btn)

        self._arch_btn = self._btn("ARCHIVE", variant="soft", display=True, px=9)
        self._arch_btn.setFixedHeight(_sc(26, sc, 22))
        self._arch_btn.setToolTip("Archive / Restore note without deleting")
        self._arch_btn.setEnabled(False)
        self._arch_btn.clicked.connect(self._toggle_archive)
        act.addWidget(self._arch_btn)

        act.addStretch(1)
        self._words_lbl = QLabel("0 words")
        self._words_lbl.setObjectName("tip")
        act.addWidget(self._words_lbl)
        el.addLayout(act)

        # Row 3: Notebook chips (1-click)
        meta = QHBoxLayout()
        meta.setSpacing(4)
        self._cat_btns: dict[str, P5FluidButton] = {}
        for cat in NOTE_CATEGORIES:
            cb = self._btn(cat.upper(), variant="soft", display=True, px=9)
            cb.setFixedHeight(_sc(23, sc, 20))
            cb.clicked.connect(lambda _c=False, c=cat: self._set_category(c))
            self._cat_btns[cat] = cb
            meta.addWidget(cb)
        self._sync_category_ui()
        meta.addStretch(1)
        el.addLayout(meta)

        # Detected #tags indicator row
        self._note_tags_lbl = QLabel("Tip: type #tag anywhere in your note to organize & filter.")
        self._note_tags_lbl.setObjectName("tip")
        el.addWidget(self._note_tags_lbl)

        # Gentle legacy-deadline migration banner (only shown for older v1.1/v1.2 notes that had deadlines)
        self._legacy_bar = _make_card("subcard")
        lgl = QHBoxLayout(self._legacy_bar)
        lgl.setContentsMargins(10, 6, 10, 6)
        lgl.setSpacing(6)
        self._legacy_lbl = QLabel("")
        self._legacy_lbl.setStyleSheet(f"color: #FAD02C; font-size: {_sc(12, sc, 10)}px; font-weight: bold;")
        lgl.addWidget(self._legacy_lbl, 1)
        mv_btn = self._btn("MOVE TO TASKS", variant="primary", display=True, px=9)
        mv_btn.setFixedHeight(_sc(24, sc, 20))
        mv_btn.clicked.connect(self._migrate_legacy_deadline)
        lgl.addWidget(mv_btn)
        clr_btn = self._btn("CLEAR", variant="soft", display=True, px=9)
        clr_btn.setFixedHeight(_sc(24, sc, 20))
        clr_btn.clicked.connect(self._clear_legacy_deadline)
        lgl.addWidget(clr_btn)
        self._legacy_bar.setVisible(False)
        el.addWidget(self._legacy_bar)

        # Default Live WYSIWYG rich Markdown editor (edit formatted notes directly!)
        self.content_edit = P5RichNoteEditor(scale=sc)
        self.content_edit.setPlaceholderText(
            "Start writing... Formatted live as you type!\n"
            "Try ## heading, **bold**, *italic*, `code`, - bullet, [] checklist, > quote, --- divider, or #tags."
        )
        self.content_edit.textChanged.connect(self._on_content_changed)
        self.content_edit.checkbox_toggled.connect(self._on_checkbox_toggled)
        self.content_edit.cursorPositionChanged.connect(self._sync_toolbar_state)
        self.content_edit.currentCharFormatChanged.connect(lambda _f: self._sync_toolbar_state())
        el.addWidget(self.content_edit, 1)

        # Optional Raw Markdown source editor (synced 2-way via RAW MD / Ctrl+E)
        self.raw_edit = QTextEdit()
        self.raw_edit.setObjectName("noteRaw")
        self.raw_edit.setAcceptRichText(False)
        self.raw_edit.setPlaceholderText("Raw Markdown source...")
        self.raw_edit.textChanged.connect(self._on_content_changed)
        self.raw_edit.setVisible(False)
        el.addWidget(self.raw_edit, 1)

        # Undo strip
        self._undo = UndoBar(scale=sc)
        self._undo.undo_clicked.connect(self._undo_delete)
        el.addWidget(self._undo)

        # Bottom live formatting toolbar + true auto-save indicator + COPY MD
        bf = QHBoxLayout()
        bf.setSpacing(6)
        self._fmt_pill = _make_card("toolbarPill")
        pl = QHBoxLayout(self._fmt_pill)
        pl.setContentsMargins(4, 3, 4, 3)
        pl.setSpacing(1)
        self._fmt_btns: dict[str, P5FluidButton] = {}
        for key, label, ic, tip, fn in (
            ("h2",     "H2", "", "Heading (## )", self._fmt_heading),
            ("bold",   "B",  "", "Bold (Ctrl+B)", self._fmt_bold),
            ("italic", "I",  "", "Italic (Ctrl+I)", self._fmt_italic),
            ("code",   "<>", "", "Inline code (Ctrl+`)", self._fmt_code),
            ("bullet", "•",  "", "Bullet list (- )", self._fmt_bullet),
            ("check",  "☐",  "", "Checklist ([] / Ctrl+Enter to toggle)", self._fmt_checklist),
            ("quote",  "”",  "", "Blockquote (> )", self._fmt_quote),
            ("",       "—",  "", "Section divider (---)", self._fmt_divider),
            ("",       "",   "clock", "Insert timestamp", self._insert_timestamp),
        ):
            tb_btn = self._btn(label, variant="ghost", icon=ic, px=10)
            tb_btn.setFixedSize(_sc(25, sc, 21), _sc(25, sc, 21))
            tb_btn.setToolTip(tip)
            tb_btn.clicked.connect(lambda _c=False, f=fn: f())
            pl.addWidget(tb_btn)
            if key:
                self._fmt_btns[key] = tb_btn
        bf.addWidget(self._fmt_pill)

        self.status = QLabel("Auto-saves as you type")
        self.status.setObjectName("tip")
        bf.addWidget(self.status, 1)

        copy_btn = self._btn("COPY MD", variant="soft", display=True, px=10)
        copy_btn.setFixedHeight(_sc(28, sc, 24))
        copy_btn.setToolTip("Copy note title & Markdown body to clipboard")
        copy_btn.clicked.connect(self._copy_markdown)
        bf.addWidget(copy_btn)
        el.addLayout(bf)

        cols.addWidget(editor_card, 65)
        self.body.addLayout(cols, 1)

        # Keyboard shortcuts (familiar desktop note app ergonomics)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(lambda: self._new())
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.search_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._toggle_raw_mode)
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self._toggle_pin)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._fmt_bold)
        QShortcut(QKeySequence("Ctrl+I"), self).activated.connect(self._fmt_italic)
        QShortcut(QKeySequence("Ctrl+`"), self).activated.connect(self._fmt_code)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())

        # Fast debounced auto-save (650 ms after typing stops)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(650)
        self._autosave_timer.timeout.connect(self._debounced_autosave)

    # -- panel hooks -------------------------------------------------------------
    def on_shown(self) -> None:
        if self._current_id is None:
            active = [n for n in self.store.all() if not n.get("archived")]
            if active:
                active.sort(key=lambda n: (0 if n.get("pinned") else 1, n.get("updated", "")), reverse=False)
                # Pinned first, then most recently updated
                active.sort(key=lambda n: n.get("updated", ""), reverse=True)
                active.sort(key=lambda n: 0 if n.get("pinned") else 1)
                self._load(active[0])
                self.refresh()
            else:
                QTimer.singleShot(20, self.title_edit.setFocus)

    def on_closing(self) -> None:
        self._autosave_timer.stop()
        if self._dirty:
            self._save(silent=True)
        self.refresh()

    # -- public helper for Choice Prompts / Templates ----------------------------
    def create_from_template(self, template_key: str, title: str = "") -> None:
        """Open a fresh note pre-filled from `NOTE_TEMPLATES[template_key]` with optional custom title."""
        self._new(template_key=template_key, custom_title=title)

    def _open_template_menu(self) -> None:
        m = QMenu(self)
        m.setStyleSheet(menu_qss(self._ui_scale))
        for key, label in (
            ("blank",   "BLANK NOTE"),
            ("meeting", "MEETING NOTES"),
            ("study",   "STUDY SUMMARY"),
            ("idea",    "IDEA SKETCH"),
            ("journal", "DAILY JOURNAL"),
        ):
            m.addAction(label, lambda k=key: self._new(template_key=k))
        pos = self._tmpl_btn.mapToGlobal(QPoint(0, self._tmpl_btn.height() + 4))
        m.exec(pos)

    # -- filters, tags & sort ----------------------------------------------------
    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, btn in self._fchips.items():
            btn.set_active(k == key)
        self.refresh()

    def _set_tag_filter(self, tag: str) -> None:
        self._active_tag = "" if self._active_tag == tag else tag
        self.refresh()

    def _cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self.SORT_MODES)
        self._sort_btn.setText(self.SORT_MODES[self._sort_idx][1])
        self.refresh()

    def _set_category(self, cat: str) -> None:
        self._category = cat
        self._sync_category_ui()
        self._on_edited()

    def _sync_category_ui(self) -> None:
        for cat, btn in self._cat_btns.items():
            btn.set_active(cat == self._category, active_variant="primary")

    def _rebuild_tag_bar(self) -> None:
        _clear_layout(self._tags_lay)
        tags = self.store.all_tags(include_archived=(self._filter == "archived"))[:10]
        if self._active_tag and all(t != self._active_tag for t, _ in tags):
            self._active_tag = ""
        if not tags:
            self._tags_scroll.setVisible(False)
            return
        sc = self._ui_scale
        for tag, _cnt in tags:
            tb = self._btn(f"#{tag}", variant="soft", display=True, px=9)
            tb.setFixedHeight(_sc(21, sc, 18))
            tb.setMinimumWidth(tb.sizeHint().width() + _sc(4, sc, 2))
            tb.set_active(tag == self._active_tag, active_variant="gold")
            tb.clicked.connect(lambda _c=False, t=tag: self._set_tag_filter(t))
            self._tags_lay.addWidget(tb)
        self._tags_lay.addStretch(1)
        self._tags_scroll.setVisible(True)

    # -- Live WYSIWYG formatting & optional RAW MD source toggle -----------------
    def _active_editor(self) -> QTextEdit:
        return self.raw_edit if self._raw_mode else self.content_edit

    def _current_markdown(self) -> str:
        if self._raw_mode:
            return self.raw_edit.toPlainText()
        return self.content_edit.to_markdown_content()

    def _current_plain_text(self) -> str:
        if self._raw_mode:
            return self.raw_edit.toPlainText()
        return self.content_edit.toPlainText()

    def _set_raw_mode(self, on: bool) -> None:
        on = bool(on)
        if on == self._raw_mode:
            return
        was_loading = self._loading
        self._loading = True
        try:
            if on:
                md = self.content_edit.to_markdown_content()
                self.raw_edit.blockSignals(True)
                self.raw_edit.setPlainText(md)
                self.raw_edit.blockSignals(False)
            else:
                md = self.raw_edit.toPlainText()
                self.content_edit.set_markdown_content(md)
            self._raw_mode = on
            self.content_edit.setVisible(not on)
            self.raw_edit.setVisible(on)
            self._raw_btn.set_active(on, active_variant="gold")
            self._raw_btn.setText("RICH" if on else "RAW MD")
        finally:
            self._loading = was_loading
        self._sync_toolbar_state()
        self._update_word_and_tag_meta()
        self._active_editor().setFocus()

    def _toggle_raw_mode(self) -> None:
        self._set_raw_mode(not self._raw_mode)

    def _sync_toolbar_state(self) -> None:
        if self._raw_mode:
            for btn in self._fmt_btns.values():
                btn.set_active(False, active_variant="gold")
            return
        fmts = self.content_edit.active_formats()
        for key, btn in self._fmt_btns.items():
            btn.set_active(bool(fmts.get(key)), active_variant="gold")

    def _insert_snippet(self, snippet: str) -> None:
        ed = self._active_editor()
        cur = ed.textCursor()
        cur.insertText(snippet)
        ed.setFocus()

    def _insert_line_prefix(self, prefix: str) -> None:
        ed = self._active_editor()
        cur = ed.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cur.insertText(prefix)
        ed.setFocus()

    def _wrap_selection(self, left: str, right: str, placeholder: str) -> None:
        ed = self._active_editor()
        cur = ed.textCursor()
        sel = cur.selectedText()
        if sel:
            cur.insertText(f"{left}{sel}{right}")
        else:
            pos = cur.position()
            cur.insertText(f"{left}{placeholder}{right}")
            cur.setPosition(pos + len(left))
            cur.setPosition(pos + len(left) + len(placeholder), QTextCursor.MoveMode.KeepAnchor)
            ed.setTextCursor(cur)
        ed.setFocus()

    def _fmt_heading(self) -> None:
        if self._raw_mode:
            self._insert_line_prefix("## ")
        else:
            self.content_edit.toggle_heading(2)
            self._sync_toolbar_state()

    def _fmt_bold(self) -> None:
        if self._raw_mode:
            self._wrap_selection("**", "**", "bold")
        else:
            self.content_edit.toggle_bold()
            self._sync_toolbar_state()

    def _fmt_italic(self) -> None:
        if self._raw_mode:
            self._wrap_selection("*", "*", "italic")
        else:
            self.content_edit.toggle_italic()
            self._sync_toolbar_state()

    def _fmt_code(self) -> None:
        if self._raw_mode:
            self._wrap_selection("`", "`", "code")
        else:
            self.content_edit.toggle_inline_code()
            self._sync_toolbar_state()

    def _fmt_bullet(self) -> None:
        if self._raw_mode:
            self._insert_line_prefix("- ")
        else:
            self.content_edit.toggle_bullet_list()
            self._sync_toolbar_state()

    def _fmt_checklist(self) -> None:
        if self._raw_mode:
            self._insert_line_prefix("- [ ] ")
        else:
            self.content_edit.toggle_checklist()
            self._sync_toolbar_state()

    def _fmt_quote(self) -> None:
        if self._raw_mode:
            self._insert_line_prefix("> ")
        else:
            self.content_edit.toggle_blockquote()
            self._sync_toolbar_state()

    def _fmt_divider(self) -> None:
        if self._raw_mode:
            self._insert_snippet("\n---\n")
        else:
            self.content_edit.insert_divider()
            self._sync_toolbar_state()

    def _insert_timestamp(self) -> None:
        self._insert_snippet(f"[{datetime.now():%d %b %H:%M}] ")

    def _update_word_and_tag_meta(self) -> None:
        txt = self._current_plain_text()
        n = len(txt.split())
        read_m = max(1, (n + 199) // 200) if n else 0
        read_s = f" · ~{read_m}m read" if n >= 40 else ""
        self._words_lbl.setText(f"{n} {'word' if n == 1 else 'words'}{read_s}")

        tags = self.store.note_tags({"title": self.title_edit.text(), "content": txt})
        if tags:
            self._note_tags_lbl.setText("Tags: " + "  ".join(f"#{t}" for t in tags[:8]))
            self._note_tags_lbl.setStyleSheet("color: #FAD02C;")
        else:
            self._note_tags_lbl.setText("Tip: type #tag anywhere in your note to organize & filter.")
            self._note_tags_lbl.setStyleSheet("")

    def _on_checkbox_toggled(self, _checked: bool) -> None:
        self._sync_toolbar_state()
        self._on_edited()

    def _on_content_changed(self) -> None:
        if self._loading:
            return
        self._update_word_and_tag_meta()
        self._sync_toolbar_state()
        self._on_edited()

    def _on_edited(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status.setText("Saving...")
        self._autosave_timer.start()

    def _debounced_autosave(self) -> None:
        if not self._dirty:
            return
        if self.title_edit.text().strip() or self._current_plain_text().strip():
            if self._save(silent=True):
                self.refresh()
                self.status.setText(f"Auto-saved · {datetime.now():%H:%M}")

    # -- Notes -> Tasks bridge & Clipboard --------------------------------------
    def _promote_to_task(self) -> None:
        """Convert highlighted text (or current note title) into a real Task in Tasks."""
        sel = self._active_editor().textCursor().selectedText().replace("\u2029", " ").strip()
        if sel:
            # Strip leading markdown list/checkbox markers cleanly
            for prefix in ("- [ ] ", "- [x] ", "- [X] ", "[ ] ", "- ", "• ", "* "):
                if sel.startswith(prefix):
                    sel = sel[len(prefix):].strip()
                    break
            title = sel[:120]
        else:
            title = self.title_edit.text().strip()
            if not title:
                first_line = (self._current_plain_text().strip().splitlines() or [""])[0].strip()
                title = first_line[:120]
        if not title:
            msg = "Write or highlight something first to send to Tasks."
            self.status.setText(msg)
            self.nudge_input(self.title_edit, msg, tone="warn")
            return
        if self._dirty:
            self._save(silent=True)
        self.send_to_task.emit(title, None)
        self.status.setText(f"Sent to Tasks: '{title[:34]}'")
        self.notify(f"Sent to Tasks: '{title[:34]}'", tone="ok")

    def _migrate_legacy_deadline(self) -> None:
        if not self._legacy_dl_note_id:
            return
        note = self.store.get(self._legacy_dl_note_id)
        if note is None:
            self._legacy_bar.setVisible(False)
            return
        dl = parse_dt(note.get("deadline"))
        title = note.get("title") or "Legacy Note Reminder"
        self.store.clear_deadline(note["id"])
        self._legacy_bar.setVisible(False)
        self._legacy_dl_note_id = None
        self.send_to_task.emit(title, dl)
        self.notes_changed.emit()
        self.refresh()
        self.status.setText("Moved legacy deadline to Tasks!")
        self.notify("Moved legacy deadline to Tasks!", tone="ok")

    def _clear_legacy_deadline(self) -> None:
        if not self._legacy_dl_note_id:
            return
        self.store.clear_deadline(self._legacy_dl_note_id)
        self._legacy_bar.setVisible(False)
        self._legacy_dl_note_id = None
        self.notes_changed.emit()
        self.refresh()
        self.status.setText("Cleared legacy deadline.")
        self.notify("Cleared legacy deadline", tone="info")

    def _copy_markdown(self) -> None:
        title = self.title_edit.text().strip()
        body = self._current_markdown().strip()
        payload = f"# {title}\n\n{body}".strip() if title else body
        if not payload:
            self.status.setText("Nothing to copy yet.")
            self.notify("Nothing to copy yet — write a note first", tone="warn")
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(payload)
            self.status.setText("Copied Markdown to clipboard!")
            self.notify("Copied Markdown to clipboard!", tone="ok")

    # -- note list ---------------------------------------------------------------
    def _card_meta(self, note: dict) -> tuple[str, str, str, list[str]]:
        raw = note.get("content") or ""
        # Strip Markdown prefixes & inline formatting tokens for crisp 2-line preview cards
        clean_lines = []
        for ln in raw.splitlines():
            s = ln.strip()
            if not s or s in ("---", "***", "- - -"):
                continue
            s = re.sub(r"^(?:[-*+]\s+\[[ xX]\]\s+|\[[ xX]\]\s+|#{1,6}\s+|[>\-•*]+\s+)", "", s)
            s = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__|~~([^~]+)~~|`([^`]+)`|\*([^*]+)\*",
                       lambda m: next(g for g in m.groups() if g is not None), s)
            s = s.strip()
            if s:
                clean_lines.append(s)
        preview = " ".join(clean_lines)[:95] or "Empty note"
        date_txt = _fmt_short_date(note.get("updated") or note.get("created"))
        tags = self.store.note_tags(note)
        badge = ""
        if note.get("archived"):
            badge = "ARCHIVED"
        elif note.get("deadline") and not note.get("done"):
            badge = "LEGACY DUE"
        return preview, badge, date_txt, tags

    def refresh(self) -> None:
        self._rebuild_tag_bar()
        query = self.search_edit.text().strip().lower()
        tag_filter = self._active_tag

        def matches(n: dict) -> bool:
            is_arch = bool(n.get("archived"))
            if self._filter == "archived":
                if not is_arch:
                    return False
            else:
                if is_arch:
                    return False
                if self._filter == "pinned" and not n.get("pinned"):
                    return False
            ntags = self.store.note_tags(n)
            if tag_filter and tag_filter not in ntags:
                return False
            if query:
                tag_blob = " ".join(f"#{t}" for t in ntags)
                blob = f"{n.get('title', '')} {n.get('content', '')} {n.get('category', '')} {tag_blob}".lower()
                return query in blob
            return True

        notes = [n for n in self.store.all() if matches(n)]
        sort_key = self.SORT_MODES[self._sort_idx][0]
        if sort_key == "alpha":
            notes.sort(key=lambda n: (n.get("title") or "").lower())
        elif sort_key == "created":
            notes.sort(key=lambda n: n.get("created", ""), reverse=True)
        else:
            notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
        # Pinned notes always float cleanly to top
        notes.sort(key=lambda n: 0 if n.get("pinned") else 1)

        n_cnt = len(notes)
        self.count_label.setText(f"{n_cnt} {'NOTE' if n_cnt == 1 else 'NOTES'}")
        self.list.blockSignals(True)
        self.list.clear()
        if not notes:
            if self._filter == "archived":
                msg = "Archive is empty.\nArchive old notes anytime to keep your notebook tidy."
            elif query or tag_filter or self._filter != "all":
                msg = "No notes match this filter."
            else:
                msg = "No notes yet.\nStart writing on the right or pick a template!"
            self.list.addItem(_hint_item(msg))
        for n in notes:
            preview, badge, date_txt, tags = self._card_meta(n)
            item = QListWidgetItem(n.get("title") or "(Untitled)")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(ROLE_ID, n["id"])
            item.setData(ROLE_DONE, bool(n.get("archived")))
            item.setData(ROLE_FLAG, bool(n.get("pinned")))
            item.setData(ROLE_PREVIEW, preview)
            item.setData(ROLE_BADGE, badge)
            item.setData(ROLE_DATE, date_txt)
            item.setData(ROLE_CAT, n.get("category") or "Personal")
            item.setData(ROLE_TAGS, tags)
            self.list.addItem(item)
            if n["id"] == self._current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    # -- selection, load, new, pin, archive, save, delete + undo ----------------
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

    def _sync_flags_ui(self, pinned: bool, archived: bool) -> None:
        self._pin_btn.set_variant("gold" if pinned else "soft")
        self._arch_btn.set_variant("gold" if archived else "soft")
        self._arch_btn.setText("UNARCHIVE" if archived else "ARCHIVE")

    def _load(self, note: dict) -> None:
        self._loading = True
        self._autosave_timer.stop()
        self._current_id = note["id"]
        cat = note.get("category") or "Personal"
        self._category = cat if cat in NOTE_CATEGORIES else "Personal"
        self._sync_category_ui()
        self.title_edit.setText(note.get("title", ""))
        self.title_edit.setCursorPosition(0)
        body = note.get("content", "")
        self.content_edit.set_markdown_content(body)
        if self._raw_mode:
            self.raw_edit.blockSignals(True)
            self.raw_edit.setPlainText(body)
            self.raw_edit.blockSignals(False)
        self._update_word_and_tag_meta()
        self._sync_toolbar_state()

        # Legacy deadline banner for smooth transition from v1.1/v1.2 notes
        dl = parse_dt(note.get("deadline"))
        if dl is not None and not note.get("done"):
            self._legacy_dl_note_id = note["id"]
            self._legacy_lbl.setText(f"Legacy deadline ({dl:%d %b %H:%M}) — deadlines now live in Tasks:")
            self._legacy_bar.setVisible(True)
        else:
            self._legacy_dl_note_id = None
            self._legacy_bar.setVisible(False)

        for btn in (self._pin_btn, self._arch_btn, self._del_btn):
            btn.setEnabled(True)
        self._sync_flags_ui(bool(note.get("pinned")), bool(note.get("archived")))
        self._loading = False
        self._dirty = False
        upd = _fmt_short_date(note.get("updated") or note.get("created"))
        self.status.setText(f"Updated {upd}" if upd else "Auto-saves as you type")

    def _new(self, template_key: str = "blank", custom_title: str = "") -> None:
        self._autosave_timer.stop()
        if self._dirty:
            self._save(silent=True)
        self._set_raw_mode(False)
        self._loading = True
        self._current_id = None
        tmpl_title, tmpl_body, tmpl_cat = NOTE_TEMPLATES.get(template_key, NOTE_TEMPLATES["blank"])
        date_str = datetime.now().strftime("%d %b %Y")
        self._category = tmpl_cat if tmpl_cat in NOTE_CATEGORIES else "Personal"
        self._sync_category_ui()
        final_title = (custom_title or "").strip() or (tmpl_title.format(date=date_str) if tmpl_title else "")
        self.title_edit.setText(final_title)
        self.content_edit.set_markdown_content(tmpl_body)
        self.raw_edit.blockSignals(True)
        self.raw_edit.setPlainText(tmpl_body)
        self.raw_edit.blockSignals(False)
        self._update_word_and_tag_meta()
        self._sync_toolbar_state()
        self._legacy_dl_note_id = None
        self._legacy_bar.setVisible(False)
        for btn in (self._pin_btn, self._arch_btn, self._del_btn):
            btn.setEnabled(False)
        self._sync_flags_ui(False, False)
        self._loading = False
        self._dirty = bool(final_title or tmpl_body)
        self.list.clearSelection()
        if self._filter == "archived":
            self._set_filter("all")
        else:
            self.refresh()
        self.status.setText("New note — auto-saves as you type")
        if tmpl_body or final_title:
            self._save(silent=True)
            self.refresh()
            self.content_edit.setFocus()
        else:
            self.title_edit.setFocus()

    def _toggle_pin(self) -> None:
        if not self._current_id:
            return
        note = self.store.get(self._current_id)
        if note is None:
            return
        pinned = not bool(note.get("pinned"))
        self.store.set_pinned(self._current_id, pinned)
        self._sync_flags_ui(pinned, bool(note.get("archived")))
        self.refresh()
        self.notify("Note pinned to top" if pinned else "Note unpinned", tone="gold" if pinned else "info", ms=1800)

    def _toggle_archive(self) -> None:
        if not self._current_id:
            return
        note = self.store.get(self._current_id)
        if note is None:
            return
        arch = not bool(note.get("archived"))
        self.store.set_archived(self._current_id, arch)
        self._sync_flags_ui(bool(note.get("pinned")), arch)
        self.refresh()
        self.notes_changed.emit()
        self.status.setText("Moved to Archive." if arch else "Restored from Archive.")
        self.notify("Moved note to Archive" if arch else "Restored note from Archive", tone="info")

    def _save(self, silent: bool = False) -> bool:
        silent = silent is True
        self._autosave_timer.stop()
        title = self.title_edit.text().strip()
        content = self._current_markdown().strip()
        if not title and not content:
            if not silent:
                msg = "Add a title or content first."
                self.status.setText(msg)
                self.nudge_input(self.title_edit, msg, tone="warn")
            return False
        if not title:
            first = (content.splitlines() or ["Untitled"])[0].lstrip("#>-•* ").strip()
            title = first[:44] or "Untitled"
        if self._current_id and self.store.get(self._current_id):
            note = self.store.update(
                self._current_id, title, content, category=self._category, preserve_deadline=True,
            )
        else:
            note = self.store.add(title, content, deadline=None, category=self._category)
        self._current_id = note["id"] if note else None
        self._dirty = False
        for btn in (self._pin_btn, self._arch_btn, self._del_btn):
            btn.setEnabled(self._current_id is not None)
        if note:
            self._sync_flags_ui(bool(note.get("pinned")), bool(note.get("archived")))
        self.notes_changed.emit()
        if silent:
            return True
        self.refresh()
        if self.store.last_error:
            self.status.setText("Save failed: " + self.store.last_error)
            self.notify("Save failed: " + self.store.last_error[:60], tone="err")
        else:
            self.status.setText(f"Saved · {datetime.now():%H:%M}")
            self.notify(f"Saved '{title[:34]}'", tone="ok", ms=1800)
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
        self.notify(f"Deleted '{title[:28]}' · Undo available", tone="info")

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
            self.notify("Note restored!", tone="ok")


# ---------------------------------------------------------------------------
# SETTINGS DIALOG (Persona 5 dark theme, Bedtime + Reminders + Focus + Behavior)
# ---------------------------------------------------------------------------
class SettingsDialog(P5FluidPanel):
    saved = pyqtSignal()
    always_on_top_changed = pyqtSignal(bool)
    autostart_changed = pyqtSignal(bool)
    test_ai_requested = pyqtSignal(dict)
    test_sheets_sync_requested = pyqtSignal(dict)
    sync_sheets_now_requested = pyqtSignal(dict)

    def __init__(self, store: SettingsStore, get_on_top=None, get_autostart=None,
                 get_hotkey_status=None, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("SETTINGS", QSize(500, 740), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        self._get_on_top = get_on_top
        self._get_autostart = get_autostart
        self._get_hotkey_status = get_hotkey_status
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
        self.bed_edit = P5TimeEdit()
        self.bed_edit.setDisplayFormat("HH:mm")
        self._row(l1, "Bedtime starts", self.bed_edit)
        self.wake_edit = P5TimeEdit()
        self.wake_edit.setDisplayFormat("HH:mm")
        self._row(l1, "Reminder stops (wake up)", self.wake_edit)
        self.curfew_interval = P5SpinBox()
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
        self.event_window = P5SpinBox()
        self.event_window.setRange(5, 1440)
        self.event_window.setSuffix(" min")
        self._row(l2, "Start reminding before", self.event_window)
        self.event_interval = P5SpinBox()
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
        self.focus_spin = P5SpinBox()
        self.focus_spin.setRange(1, 180)
        self.focus_spin.setSuffix(" min")
        self._row(l3, "Focus session duration", self.focus_spin)
        self.rest_spin = P5SpinBox()
        self.rest_spin.setRange(1, 60)
        self.rest_spin.setSuffix(" min")
        self._row(l3, "Break duration", self.rest_spin)
        self.ui_scale_spin = P5SpinBox()
        self.ui_scale_spin.setRange(75, 150)
        self.ui_scale_spin.setSingleStep(5)
        self.ui_scale_spin.setSuffix("%")
        self._row(l3, "UI size", self.ui_scale_spin)
        self.briefing_check = P5CheckBox("Show Daily Briefing & Confidant Summary once a day", scale=sc)
        self.evening_wrapup_check = P5CheckBox("Show Evening Wrap-up recap once per evening", scale=sc)
        self.wrapup_edit = P5TimeEdit()
        self.wrapup_edit.setDisplayFormat("HH:mm")
        self.auto_reward_check = P5CheckBox("Auto-reward Social Stats from tasks, habits & focus", scale=sc)
        self.auto_open_stats_check = P5CheckBox("Auto-open Social Stats star animation on rewards", scale=sc)
        self.on_top_check = P5CheckBox("Keep Morgana always on top", scale=sc)
        self.autostart_check = P5CheckBox("Start Morgana automatically on boot", scale=sc)
        l3.addWidget(self.briefing_check)
        l3.addWidget(self.evening_wrapup_check)
        self._row(l3, "Evening Wrap-up time", self.wrapup_edit)
        l3.addWidget(self.auto_reward_check)
        l3.addWidget(self.auto_open_stats_check)
        l3.addWidget(self.on_top_check)
        l3.addWidget(self.autostart_check)
        cards_lay.addWidget(c3)

        # Card 5: Awareness, Screen Time, Auto-DND & Global Shortcut
        c_aw = _make_card()
        law = QVBoxLayout(c_aw)
        law.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        law.setSpacing(row_gap)
        haw = QLabel("AWARENESS, AUTO-DND & QUICK SHORTCUT")
        haw.setObjectName("section")
        law.addWidget(haw)
        self.awareness_check = P5CheckBox("Active App / Window Awareness (100% local)", scale=sc)
        self.screen_time_check = P5CheckBox("Track daily Screen Time & suggest healthy breaks", scale=sc)
        self.break_nudge_spin = P5SpinBox()
        self.break_nudge_spin.setRange(15, 240)
        self.break_nudge_spin.setSingleStep(5)
        self.break_nudge_spin.setSuffix(" min")
        self.particles_check = P5CheckBox("Micro-particles (music notes, stars, hearts, Zzz)", scale=sc)
        self.spontaneous_check = P5CheckBox("Occasional interactive choice conversations", scale=sc)
        self.dnd_meeting_check = P5CheckBox("Auto-DND during video calls & meetings", scale=sc)
        self.dnd_fullscreen_check = P5CheckBox("Auto-DND during fullscreen apps & presentations", scale=sc)
        self.manual_dnd_check = P5CheckBox("Manual Do Not Disturb (silence popups & chatter)", scale=sc)
        self.hotkey_combo = P5ComboBox()
        for preset in QUICK_HOTKEY_PRESETS:
            self.hotkey_combo.addItem(preset, preset)
        self._hotkey_status_lbl = QLabel("")
        self._hotkey_status_lbl.setObjectName("tip")
        self._hotkey_status_lbl.setWordWrap(True)
        law.addWidget(self.awareness_check)
        law.addWidget(self.screen_time_check)
        self._row(law, "Nudge break after nonstop", self.break_nudge_spin)
        law.addWidget(self.particles_check)
        law.addWidget(self.spontaneous_check)
        law.addWidget(self.dnd_meeting_check)
        law.addWidget(self.dnd_fullscreen_check)
        law.addWidget(self.manual_dnd_check)
        self._row(law, "Quick Command shortcut", self.hotkey_combo)
        law.addWidget(self._hotkey_status_lbl)
        cards_lay.addWidget(c_aw)

        # Card 6: AI Companion (Google Gemini API Key or OpenAI-Compatible / Ollama)
        c_ai = _make_card()
        lai = QVBoxLayout(c_ai)
        lai.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        lai.setSpacing(row_gap)
        hai = QLabel("AI COMPANION (GEMINI / JOKER ↔ MORGANA)")
        hai.setObjectName("section")
        lai.addWidget(hai)

        self.ai_enabled_check = P5CheckBox("Enable AI Morgana ↔ Joker dialogue (opt-in)", scale=sc)
        self.ai_enabled_check.toggled.connect(self._sync_ai_fields)
        lai.addWidget(self.ai_enabled_check)

        self.ai_provider_combo = P5ComboBox()
        self.ai_provider_combo.addItem("Google Gemini (API Key)", "gemini")
        self.ai_provider_combo.addItem("OpenAI-Compatible / Ollama", "openai")
        self.ai_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        self._row(lai, "Provider", self.ai_provider_combo, wide=True)

        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("AIza... (from aistudio.google.com/apikey)")
        self._row(lai, "API Key", self.ai_key_edit, wide=True)

        self.ai_model_edit = QLineEdit()
        self.ai_model_edit.setPlaceholderText("gemini-3.5-flash-lite")
        self._row(lai, "Model", self.ai_model_edit, wide=True)

        self._ai_endpoint_wrap = QWidget()
        ep_lay = QVBoxLayout(self._ai_endpoint_wrap)
        ep_lay.setContentsMargins(0, 0, 0, 0)
        ep_lay.setSpacing(0)
        self.ai_endpoint_edit = QLineEdit()
        self.ai_endpoint_edit.setPlaceholderText("http://localhost:11434/v1/chat/completions")
        self._row(ep_lay, "API Endpoint", self.ai_endpoint_edit, wide=True)
        lai.addWidget(self._ai_endpoint_wrap)

        self.ai_call_user_edit = QLineEdit()
        self.ai_call_user_edit.setPlaceholderText("Joker")
        self._row(lai, "Call me", self.ai_call_user_edit, wide=True)

        self.ai_dialogue_check = P5CheckBox("Use AI for Talk / Choice Prompts & live reactions", scale=sc)
        lai.addWidget(self.ai_dialogue_check)

        test_row = QHBoxLayout()
        test_row.setSpacing(_sc(8, sc, 6))
        self._ai_test_btn = self._btn("TEST KEY", variant="gold", display=True, px=10)
        self._ai_test_btn.setFixedHeight(_sc(26, sc, 22))
        self._ai_test_btn.clicked.connect(self._on_test_ai_clicked)
        test_row.addWidget(self._ai_test_btn)
        self._ai_test_lbl = QLabel("Off by default. Free Gemini keys: aistudio.google.com/apikey")
        self._ai_test_lbl.setObjectName("tip")
        self._ai_test_lbl.setWordWrap(True)
        test_row.addWidget(self._ai_test_lbl, 1)
        lai.addLayout(test_row)
        cards_lay.addWidget(c_ai)

        # Card 7: Google Sheets Sync (Tasks Relay + bot-wa Monthly Budget Finance)
        c_sh = _make_card()
        lsh = QVBoxLayout(c_sh)
        lsh.setContentsMargins(pad_x, pad_y, pad_x, pad_y)
        lsh.setSpacing(row_gap)
        hsh = QLabel("GOOGLE SHEETS SYNC (BOT-WA)")
        hsh.setObjectName("section")
        lsh.addWidget(hsh)

        self.sheets_sync_enabled_check = P5CheckBox("Sync task deadlines to Google Sheets (opt-in)", scale=sc)
        self.sheets_sync_enabled_check.toggled.connect(self._sync_sheets_fields)
        lsh.addWidget(self.sheets_sync_enabled_check)

        self.finance_sheets_enabled_check = P5CheckBox(
            "Connect Finance window to Monthly Budget tabs (bot-wa)", scale=sc,
        )
        self.finance_sheets_enabled_check.toggled.connect(self._sync_sheets_fields)
        lsh.addWidget(self.finance_sheets_enabled_check)

        self.sheets_id_edit = QLineEdit()
        self.sheets_id_edit.setPlaceholderText("Spreadsheet ID or Google Sheets URL")
        self._row(lsh, "Sheet ID / URL", self.sheets_id_edit, wide=True)

        self.sheets_tab_edit = QLineEdit()
        self.sheets_tab_edit.setPlaceholderText("Morgana")
        self._row(lsh, "Tasks Tab", self.sheets_tab_edit, wide=True)

        key_wrap = QWidget()
        key_lay = QHBoxLayout(key_wrap)
        key_lay.setContentsMargins(0, 0, 0, 0)
        key_lay.setSpacing(_sc(6, sc, 4))
        self.sheets_key_edit = QLineEdit()
        self.sheets_key_edit.setPlaceholderText("Path to kunci-google.json (or place beside app)")
        self.sheets_key_edit.setMinimumHeight(_sc(30, sc, 25))
        key_lay.addWidget(self.sheets_key_edit, 1)
        self._sheets_browse_btn = self._btn("BROWSE", variant="soft", display=True, px=9)
        self._sheets_browse_btn.setFixedHeight(_sc(30, sc, 25))
        self._sheets_browse_btn.clicked.connect(self._browse_sheets_key)
        key_lay.addWidget(self._sheets_browse_btn)
        self._row(lsh, "Key JSON", key_wrap, wide=True)

        sh_btn_row = QHBoxLayout()
        sh_btn_row.setSpacing(_sc(8, sc, 6))
        self._sheets_test_btn = self._btn("TEST", variant="gold", display=True, px=10)
        self._sheets_test_btn.setFixedHeight(_sc(26, sc, 22))
        self._sheets_test_btn.clicked.connect(self._on_test_sheets_clicked)
        sh_btn_row.addWidget(self._sheets_test_btn)

        self._sheets_sync_btn = self._btn("SYNC TASKS", variant="soft", display=True, px=10)
        self._sheets_sync_btn.setFixedHeight(_sc(26, sc, 22))
        self._sheets_sync_btn.clicked.connect(self._on_sync_sheets_now_clicked)
        sh_btn_row.addWidget(self._sheets_sync_btn)
        sh_btn_row.addStretch(1)
        lsh.addLayout(sh_btn_row)

        self._sheets_status_lbl = QLabel(
            "Off by default. Shares the exact same Spreadsheet ID & kunci-google.json as bot-wa "
            "(Januari–Desember budget tabs + optional task deadline relay)."
        )
        self._sheets_status_lbl.setObjectName("tip")
        self._sheets_status_lbl.setWordWrap(True)
        lsh.addWidget(self._sheets_status_lbl)
        cards_lay.addWidget(c_sh)

        # Card 8: Radial menu tabs (enable/disable individual tabs)
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
            "notes": "Notes (Notebook)",
            "talk": "Talk (Choice Chat)",
            "stats": "Social Stats",
            "finance": "Finance (Money)",
            "system": "System Menu",
        }
        # Mirror radial menu layout: Left column (Events, Timer, Talk, System), Right column (Tasks, Notes, Stats, Finance)
        left_keys = ("events", "timer", "talk", "system")
        right_keys = ("tasks", "notes", "stats", "finance")
        for r_idx in range(max(len(left_keys), len(right_keys))):
            if r_idx < len(left_keys):
                lk = left_keys[r_idx]
                cb = P5CheckBox(tab_short.get(lk, lk.title()), scale=sc)
                self.tab_checks[lk] = cb
                tabs_grid.addWidget(cb, r_idx, 0)
            if r_idx < len(right_keys):
                rk = right_keys[r_idx]
                cb = P5CheckBox(tab_short.get(rk, rk.title()), scale=sc)
                self.tab_checks[rk] = cb
                tabs_grid.addWidget(cb, r_idx, 1)
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

    def _sync_ai_fields(self, enabled: bool) -> None:
        on = bool(enabled)
        prov = str(self.ai_provider_combo.currentData() or "gemini")
        is_gemini = (prov == "gemini")
        self._ai_endpoint_wrap.setVisible(not is_gemini)
        self.ai_key_edit.setPlaceholderText(
            "AIza... (from aistudio.google.com/apikey)" if is_gemini else "Optional (blank for local Ollama)"
        )
        self.ai_model_edit.setPlaceholderText("gemini-3.5-flash-lite" if is_gemini else "llama3.2")
        for w in (self.ai_provider_combo, self.ai_endpoint_edit, self.ai_model_edit,
                  self.ai_key_edit, self.ai_call_user_edit, self.ai_dialogue_check):
            w.setEnabled(on)
        self._ai_test_btn.setEnabled(on)

    def _on_ai_provider_changed(self, _idx: int) -> None:
        if not self._filling:
            prov = str(self.ai_provider_combo.currentData() or "gemini")
            cur_model = self.ai_model_edit.text().strip().lower()
            if prov == "gemini" and cur_model in ("", "llama3.2", "llama3", "gpt-4o-mini"):
                self.ai_model_edit.setText("gemini-3.5-flash-lite")
            elif prov == "openai" and (not cur_model or cur_model.startswith("gemini-")):
                self.ai_model_edit.setText("llama3.2")
        self._sync_ai_fields(self.ai_enabled_check.isChecked())

    def _on_test_ai_clicked(self) -> None:
        prov = str(self.ai_provider_combo.currentData() or "gemini")
        self._ai_test_btn.setEnabled(False)
        self._ai_test_lbl.setStyleSheet(f"color: #FAD02C; font-size: {_sc(12, self._ui_scale, 9)}px;")
        self._ai_test_lbl.setText("Testing connection with Morgana...")
        self.notify("Testing AI connection...", tone="info", ms=1600)
        self.test_ai_requested.emit({
            "provider": prov,
            "api_key": self.ai_key_edit.text().strip(),
            "model": self.ai_model_edit.text().strip() or ("gemini-3.5-flash-lite" if prov == "gemini" else "llama3.2"),
            "endpoint": self.ai_endpoint_edit.text().strip() or SETTINGS_DEFAULTS["ai_endpoint"],
            "call_user": self.ai_call_user_edit.text().strip() or "Joker",
        })

    def set_ai_test_result(self, ok: bool, message: str) -> None:
        self._ai_test_btn.setEnabled(self.ai_enabled_check.isChecked())
        color = "#5CE09A" if ok else "#FF6B78"
        self._ai_test_lbl.setStyleSheet(f"color: {color}; font-size: {_sc(12, self._ui_scale, 9)}px; font-weight: bold;")
        self._ai_test_lbl.setText(message)
        self.notify(message[:76], tone="ok" if ok else "err", ms=2800)

    def _sync_sheets_fields(self, _checked: bool = False) -> None:
        tasks_on = self.sheets_sync_enabled_check.isChecked()
        fin_on = self.finance_sheets_enabled_check.isChecked()
        any_on = tasks_on or fin_on
        for w in (self.sheets_id_edit, self.sheets_key_edit,
                  self._sheets_browse_btn, self._sheets_test_btn):
            w.setEnabled(any_on)
        self.sheets_tab_edit.setEnabled(tasks_on)
        self._sheets_sync_btn.setEnabled(tasks_on)

    def _browse_sheets_key(self) -> None:
        cur = self.sheets_key_edit.text().strip()
        start_dir = str(Path(cur).parent) if cur and Path(cur).parent.exists() else ""
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select Google Service Account JSON",
            start_dir,
            "JSON Files (*.json);;All Files (*)",
        )
        if chosen:
            self.sheets_key_edit.setText(chosen)

    def _sheets_form_payload(self) -> dict:
        return {
            "enabled": self.sheets_sync_enabled_check.isChecked(),
            "finance_enabled": self.finance_sheets_enabled_check.isChecked(),
            "spreadsheet_id": self.sheets_id_edit.text().strip(),
            "tab_name": self.sheets_tab_edit.text().strip() or SETTINGS_DEFAULTS["sheets_sync_tab"],
            "key_path": self.sheets_key_edit.text().strip(),
        }

    def _on_test_sheets_clicked(self) -> None:
        self._sheets_test_btn.setEnabled(False)
        self._sheets_sync_btn.setEnabled(False)
        self._sheets_status_lbl.setStyleSheet(f"color: #FAD02C; font-size: {_sc(12, self._ui_scale, 9)}px;")
        self._sheets_status_lbl.setText("Testing Google Sheets connection...")
        self.notify("Testing Google Sheets connection...", tone="info", ms=1600)
        self.test_sheets_sync_requested.emit(self._sheets_form_payload())

    def _on_sync_sheets_now_clicked(self) -> None:
        self._sheets_test_btn.setEnabled(False)
        self._sheets_sync_btn.setEnabled(False)
        self._sheets_status_lbl.setStyleSheet(f"color: #FAD02C; font-size: {_sc(12, self._ui_scale, 9)}px;")
        self._sheets_status_lbl.setText("Pushing active task deadlines to Google Sheets...")
        self.notify("Syncing task deadlines to Google Sheets...", tone="info", ms=1600)
        self.sync_sheets_now_requested.emit(self._sheets_form_payload())

    def set_sheets_status(self, ok: bool, message: str) -> None:
        tasks_on = self.sheets_sync_enabled_check.isChecked()
        fin_on = self.finance_sheets_enabled_check.isChecked()
        self._sheets_test_btn.setEnabled(tasks_on or fin_on)
        self._sheets_sync_btn.setEnabled(tasks_on)
        color = "#5CE09A" if ok else "#FF6B78"
        self._sheets_status_lbl.setStyleSheet(f"color: {color}; font-size: {_sc(12, self._ui_scale, 9)}px; font-weight: bold;")
        self._sheets_status_lbl.setText(message)
        self.notify(message[:76], tone="ok" if ok else "err", ms=2800)

    def _row(self, layout: QVBoxLayout, text: str, widget: QWidget, wide: bool = False) -> None:
        sc = self._ui_scale
        row = QHBoxLayout()
        row.setSpacing(_sc(8, sc, 6))
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: #D6D6E2; font-size: {_sc(14, sc, 11)}px;")
        widget.setMinimumHeight(_sc(30, sc, 25))
        if wide:
            lbl.setFixedWidth(_sc(94, sc, 78))
            row.addWidget(lbl)
            row.addWidget(widget, 1)
        else:
            row.addWidget(lbl, 1)
            widget.setMinimumWidth(_sc(144, sc, 112))
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
        if not self.sound_check.isChecked():
            self.notify("Enable sound checkbox above to hear voice preview", tone="warn")
            return
        mgr = audio()
        if mgr is None:
            return
        mgr.set_enabled(True)
        mgr.set_voice_volume(self.voice_slider.value())
        play_voice("hmm")
        self.notify(f"Playing voice preview ({self.voice_slider.value()}%)", tone="info", ms=1200)

    def _test_sfx(self) -> None:
        if not self.sound_check.isChecked():
            self.notify("Enable sound checkbox above to hear SFX preview", tone="warn")
            return
        mgr = audio()
        if mgr is None:
            return
        mgr.set_enabled(True)
        mgr.set_sfx_volume(self.sfx_slider.value())
        play_ui("open_window")
        self.notify(f"Playing UI SFX preview ({self.sfx_slider.value()}%)", tone="info", ms=1200)

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
            self.evening_wrapup_check.setChecked(bool(values.get("evening_wrapup", SETTINGS_DEFAULTS["evening_wrapup"])))
            self.wrapup_edit.setTime(QTime.fromString(values.get("wrapup_time", SETTINGS_DEFAULTS["wrapup_time"]), "HH:mm"))
            self.auto_reward_check.setChecked(bool(values.get("auto_stat_rewards", SETTINGS_DEFAULTS["auto_stat_rewards"])))
            self.auto_open_stats_check.setChecked(bool(values.get("auto_open_stats_on_reward", SETTINGS_DEFAULTS["auto_open_stats_on_reward"])))
            self.awareness_check.setChecked(bool(values.get("app_awareness_enabled", SETTINGS_DEFAULTS["app_awareness_enabled"])))
            self.screen_time_check.setChecked(bool(values.get("screen_time_enabled", SETTINGS_DEFAULTS["screen_time_enabled"])))
            self.break_nudge_spin.setValue(int(values.get("break_nudge_min", SETTINGS_DEFAULTS["break_nudge_min"])))
            self.particles_check.setChecked(bool(values.get("particles_enabled", SETTINGS_DEFAULTS["particles_enabled"])))
            self.spontaneous_check.setChecked(bool(values.get("spontaneous_choices_enabled", SETTINGS_DEFAULTS["spontaneous_choices_enabled"])))
            self.dnd_meeting_check.setChecked(bool(values.get("auto_dnd_meetings", SETTINGS_DEFAULTS["auto_dnd_meetings"])))
            self.dnd_fullscreen_check.setChecked(bool(values.get("auto_dnd_fullscreen", SETTINGS_DEFAULTS["auto_dnd_fullscreen"])))
            self.manual_dnd_check.setChecked(bool(values.get("manual_dnd", SETTINGS_DEFAULTS["manual_dnd"])))
            hk = str(values.get("quick_hotkey", SETTINGS_DEFAULTS["quick_hotkey"]))
            idx = self.hotkey_combo.findData(hk)
            self.hotkey_combo.setCurrentIndex(idx if idx >= 0 else 0)
            if self._get_hotkey_status is not None:
                self._hotkey_status_lbl.setText(f"Global shortcut status: {self._get_hotkey_status()}  (In-app: Ctrl+K)")
            else:
                self._hotkey_status_lbl.setText("Press shortcut anywhere to open Ask & Command (In-app: Ctrl+K).")
            ai_on = bool(values.get("ai_enabled", SETTINGS_DEFAULTS["ai_enabled"]))
            self.ai_enabled_check.setChecked(ai_on)
            prov = str(values.get("ai_provider", SETTINGS_DEFAULTS["ai_provider"]))
            p_idx = self.ai_provider_combo.findData(prov)
            self.ai_provider_combo.setCurrentIndex(p_idx if p_idx >= 0 else 0)
            self.ai_endpoint_edit.setText(str(values.get("ai_endpoint", SETTINGS_DEFAULTS["ai_endpoint"])))
            self.ai_model_edit.setText(str(values.get("ai_model", SETTINGS_DEFAULTS["ai_model"])))
            self.ai_key_edit.setText(str(values.get("ai_api_key", SETTINGS_DEFAULTS["ai_api_key"])))
            self.ai_call_user_edit.setText(str(values.get("ai_call_user", SETTINGS_DEFAULTS["ai_call_user"])))
            self.ai_dialogue_check.setChecked(bool(values.get("ai_dialogue_enabled", SETTINGS_DEFAULTS["ai_dialogue_enabled"])))
            self._ai_test_lbl.setStyleSheet("")
            self._ai_test_lbl.setText("Off by default. Free Gemini keys: aistudio.google.com/apikey")
            self._sync_ai_fields(ai_on)
            sh_on = bool(values.get("sheets_sync_enabled", SETTINGS_DEFAULTS["sheets_sync_enabled"]))
            fin_sh_on = bool(values.get("finance_sheets_enabled", SETTINGS_DEFAULTS["finance_sheets_enabled"]))
            self.sheets_sync_enabled_check.setChecked(sh_on)
            self.finance_sheets_enabled_check.setChecked(fin_sh_on)
            self.sheets_id_edit.setText(str(values.get("sheets_sync_id", SETTINGS_DEFAULTS["sheets_sync_id"])))
            self.sheets_tab_edit.setText(str(values.get("sheets_sync_tab", SETTINGS_DEFAULTS["sheets_sync_tab"])))
            self.sheets_key_edit.setText(str(values.get("sheets_sync_key_path", SETTINGS_DEFAULTS["sheets_sync_key_path"])))
            self._sheets_status_lbl.setStyleSheet("")
            self._sheets_status_lbl.setText(
                "Off by default. Shares the exact same Spreadsheet ID & kunci-google.json as bot-wa "
                "(Januari–Desember budget tabs + optional task deadline relay)."
            )
            self._sync_sheets_fields()
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
        self.notify("Defaults loaded — click SAVE to apply", tone="gold")

    def _save(self) -> None:
        self._saving = True
        try:
            for w in (self.bed_edit, self.wake_edit, self.wrapup_edit, self.curfew_interval,
                      self.event_window, self.event_interval, self.focus_spin,
                      self.rest_spin, self.ui_scale_spin, self.break_nudge_spin):
                w.interpretText()
            chosen_tabs = [k for k in RADIAL_TAB_KEYS if self.tab_checks[k].isChecked()]
            if not chosen_tabs:
                play_ui("cancel")
                msg = "Keep at least one radial menu tab enabled."
                self.status.setText(msg)
                self.notify(msg, tone="warn")
                return
            b, w = self.bed_edit.time(), self.wake_edit.time()
            wu = self.wrapup_edit.time()
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
                evening_wrapup=self.evening_wrapup_check.isChecked(),
                wrapup_time=f"{wu.hour():02d}:{wu.minute():02d}",
                auto_stat_rewards=self.auto_reward_check.isChecked(),
                auto_open_stats_on_reward=self.auto_open_stats_check.isChecked(),
                app_awareness_enabled=self.awareness_check.isChecked(),
                screen_time_enabled=self.screen_time_check.isChecked(),
                break_nudge_min=self.break_nudge_spin.value(),
                particles_enabled=self.particles_check.isChecked(),
                spontaneous_choices_enabled=self.spontaneous_check.isChecked(),
                auto_dnd_meetings=self.dnd_meeting_check.isChecked(),
                auto_dnd_fullscreen=self.dnd_fullscreen_check.isChecked(),
                manual_dnd=self.manual_dnd_check.isChecked(),
                quick_hotkey=str(self.hotkey_combo.currentData() or "Off"),
                ai_enabled=self.ai_enabled_check.isChecked(),
                ai_provider=str(self.ai_provider_combo.currentData() or "gemini"),
                ai_endpoint=self.ai_endpoint_edit.text().strip() or SETTINGS_DEFAULTS["ai_endpoint"],
                ai_model=self.ai_model_edit.text().strip() or (
                    "gemini-3.5-flash-lite" if str(self.ai_provider_combo.currentData() or "gemini") == "gemini" else "llama3.2"
                ),
                ai_api_key=self.ai_key_edit.text().strip(),
                ai_call_user=self.ai_call_user_edit.text().strip() or "Joker",
                ai_dialogue_enabled=self.ai_dialogue_check.isChecked(),
                sheets_sync_enabled=self.sheets_sync_enabled_check.isChecked(),
                finance_sheets_enabled=self.finance_sheets_enabled_check.isChecked(),
                sheets_sync_id=self.sheets_id_edit.text().strip(),
                sheets_sync_tab=self.sheets_tab_edit.text().strip() or SETTINGS_DEFAULTS["sheets_sync_tab"],
                sheets_sync_key_path=self.sheets_key_edit.text().strip(),
                enabled_tabs=chosen_tabs,
            )
            self.saved.emit()
            if self._get_hotkey_status is not None:
                self._hotkey_status_lbl.setText(f"Global shortcut status: {self._get_hotkey_status()}  (In-app: Ctrl+K)")
            if self._get_on_top is not None and self.on_top_check.isChecked() != bool(self._get_on_top()):
                self.always_on_top_changed.emit(self.on_top_check.isChecked())
            if self._get_autostart is not None and self.autostart_check.isChecked() != bool(self._get_autostart()):
                self.autostart_changed.emit(self.autostart_check.isChecked())
            if self.store.last_error:
                self.status.setText("Save failed: " + self.store.last_error)
                self.notify("Save failed: " + self.store.last_error[:60], tone="err")
            elif b == w:
                self.status.setText("Saved, but bedtime and wake times match—bedtime reminders won't trigger.")
                self.notify("Saved (bedtime & wake match — curfew paused)", tone="warn")
            else:
                self.status.setText("Saved and applied.")
                self.notify("Settings saved & applied!", tone="ok")
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
        clip = QPainterPath()
        clip.addRoundedRect(r, 10.0, 10.0)

        # Rich obsidian base + subtle accent tint emanating from the left slash
        base = QLinearGradient(r.topLeft(), r.bottomLeft())
        base.setColorAt(0.0, QColor("#191924"))
        base.setColorAt(1.0, P5_CARD)
        p.fillPath(clip, base)

        p.save()
        p.setClipPath(clip)
        wash = QLinearGradient(r.left(), r.top(), r.left() + r.width() * 0.45, r.top())
        c0 = QColor(self._accent)
        c0.setAlpha(34)
        c1 = QColor(self._accent)
        c1.setAlpha(0)
        wash.setColorAt(0.0, c0)
        wash.setColorAt(1.0, c1)
        p.fillRect(r, wash)

        # Slanted P5 left slash accent + trailing crisp parallel hairline
        slash = QPolygonF([
            QPointF(r.left(), r.top()),
            QPointF(r.left() + 26.0 * sc, r.top()),
            QPointF(r.left() + 14.0 * sc, r.bottom()),
            QPointF(r.left(), r.bottom()),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawPolygon(slash)

        gap = 3.5 * sc
        tw = 2.6 * sc
        trail = QPolygonF([
            QPointF(r.left() + 26.0 * sc + gap, r.top()),
            QPointF(r.left() + 26.0 * sc + gap + tw, r.top()),
            QPointF(r.left() + 14.0 * sc + gap + tw, r.bottom()),
            QPointF(r.left() + 14.0 * sc + gap, r.bottom()),
        ])
        c_tr = QColor(self._accent)
        c_tr.setAlpha(155)
        p.setBrush(c_tr)
        p.drawPolygon(trail)
        p.restore()

        p.setPen(QPen(self._accent, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 10.0, 10.0)
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
        self._ev_start = P5DateTimeEdit()
        self._ev_start.setCalendarPopup(True)
        self._ev_start.setDisplayFormat("dd MMM HH:mm")
        self._ev_start.setMinimumHeight(_sc(28, sc, 24))
        self._ev_start.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ev_start.dateTimeChanged.connect(self._on_ev_start_changed)
        ef_r2.addWidget(self._ev_start, 1)

        self._ev_end_lbl = QLabel("End")
        self._ev_end_lbl.setObjectName("tip")
        ef_r2.addWidget(self._ev_end_lbl)
        self._ev_end = P5DateTimeEdit()
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
        self._foot_refresh.clicked.connect(self._on_manual_refresh)
        foot.addWidget(self._foot_refresh)
        self.body.addLayout(foot)

        QShortcut(QKeySequence("F5"), self).activated.connect(self._on_manual_refresh)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._on_manual_refresh)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._toggle_new_event_form)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())

    def _on_manual_refresh(self) -> None:
        self.notify("Refreshing 7-day schedule...", tone="info", ms=1400)
        self.refresh_requested.emit(True)

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
        self.notify(f"Editing '{ev.get('title', '')[:30]}'", tone="gold", ms=1800)

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
        self.notify(f"Event time preset: {st:%d %b %H:%M}", tone="gold", ms=1500)

    def _save_local_event(self) -> None:
        if self.local_store is None:
            return
        title = self._ev_title_edit.text().strip()
        if not title:
            msg = "Please enter an event title."
            self._ev_err.setText(msg)
            self.nudge_input(self._ev_title_edit, msg, tone="warn")
            return
        all_day = self._ev_all_day.isChecked()
        st = _from_qdt(self._ev_start.dateTime())
        en = None if all_day else _from_qdt(self._ev_end.dateTime())
        loc = self._ev_loc_edit.text().strip()
        was_edit = bool(self._editing_local_id)
        if self._editing_local_id:
            self.local_store.update(self._editing_local_id, title, st, en, all_day=all_day, location=loc)
        else:
            self.local_store.add(title, st, en, all_day=all_day, location=loc)
        self._close_event_form()
        self.notify(f"Updated '{title[:34]}'" if was_edit else f"Added event '{title[:34]}'", tone="ok")
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
        self.notify(f"Deleted '{ev.get('title', '')[:30]}' · Undo available", tone="info")

    def _undo_delete_local(self) -> None:
        if self.local_store is not None and self._undo_event is not None:
            self.local_store.restore(self._undo_event)
            self._undo_event = None
            self.notify("Restored deleted event!", tone="ok")
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
    """Persona 5 Royal 5-point golden star chart with music-note & Rank Up particle animations."""

    stat_clicked = pyqtSignal(str)

    def __init__(self, scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scale = scale
        self._totals: dict[str, int] = {k: 0 for k in SOCIAL_STAT_KEYS}
        self._anim_pts: dict[str, Spring] = {k: Spring(0.0, 105.0, 12.5) for k in SOCIAL_STAT_KEYS}
        self._pulse = Spring(0.0, 170.0, 11.0)
        self._rank_flash = Spring(0.0, 125.0, 10.5)
        self._banner_spring = Spring(0.0, 260.0, 16.0)
        self._selected = "knowledge"
        self._gain_stat = ""
        self._vertex_pts: dict[str, QPointF] = {}
        self._tip_pts: dict[str, QPointF] = {}
        self._particles: list[Particle] = []
        self._banner_mode = ""        # "gain" | "rank_up" | ""
        self._banner_title = ""
        self._banner_sub = ""
        self._banner_timer = 0.0
        self._banner_duration = 1.8
        self._clock = 0.0
        self._ticker = FrameTicker(self._frame, self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(_sc(256, scale, 206))

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

    def _geometry_metrics(self) -> tuple[float, float, float, list[float]]:
        sc = self._scale
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        cx = rect.center().x()
        cy = rect.center().y() + 7.0 * sc
        radius = min(rect.width() * 0.258, rect.height() * 0.315)
        angles = [-math.pi / 2.0 + i * (2.0 * math.pi / 5.0) for i in range(5)]
        return cx, cy, radius, angles

    def _stat_tip_pos(self, stat: str) -> QPointF:
        if stat in self._tip_pts:
            return self._tip_pts[stat]
        cx, cy, radius, angles = self._geometry_metrics()
        if stat in SOCIAL_STAT_KEYS:
            idx = SOCIAL_STAT_KEYS.index(stat)
            frac = stat_pentagon_fraction(self._totals.get(stat, 0))
            a = angles[idx]
            return QPointF(cx + math.cos(a) * radius * frac, cy + math.sin(a) * radius * frac)
        return QPointF(cx, cy)

    def trigger_point_gain(self, stat: str = "", points: int = 2, activity: str = "") -> None:
        """Spawn Persona 5 golden music notes (♪/♫) & star sparkles from the gained stat vertex."""
        sc = self._scale
        key = stat if stat in SOCIAL_STATS else self._selected
        self._selected = key
        self._gain_stat = key
        tip = self._stat_tip_pos(key)
        note_cnt = 3 if points >= 4 else (2 if points >= 2 else 1)

        for i in range(note_cnt):
            spread = (i - (note_cnt - 1) / 2.0) * 26.0 * sc
            self._particles.append(Particle(
                kind="note2" if (points >= 3 and i == note_cnt - 1) else "note",
                x=tip.x() + spread + random.uniform(-4, 4) * sc,
                y=tip.y() + random.uniform(-6, 4) * sc,
                vx=spread * 0.5 + random.uniform(-10, 10) * sc,
                vy=-58.0 * sc - random.uniform(6, 18) * sc,
                size=(21.0 if points >= 3 else 18.0) * sc,
                max_life=1.35,
                rot=random.uniform(-14, 14),
                vrot=random.uniform(-18, 18),
                wave_amp=6.5 * sc,
                wave_freq=6.0,
                phase=i * 1.4,
                delay=i * 0.12,
                color=P5_NOTE_GOLD,
                outline=BLACK,
            ))

        for i in range(7 if points >= 3 else 5):
            ang = random.uniform(0.0, 2.0 * math.pi)
            spd = random.uniform(35, 92) * sc
            self._particles.append(Particle(
                kind="star" if i % 2 == 0 else "sparkle",
                x=tip.x(),
                y=tip.y(),
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd - 14.0 * sc,
                size=random.uniform(10, 16) * sc,
                max_life=random.uniform(0.85, 1.2),
                rot=random.uniform(0, 360),
                vrot=random.uniform(-180, 180),
                drag=1.3,
                delay=0.04 + i * 0.04,
                color=P5_STAR_COLORS[i % len(P5_STAR_COLORS)],
                outline=BLACK,
            ))

        label = SOCIAL_STATS.get(key, {}).get("label", key.title()).upper()
        notes_glyph = "♪" * min(3, max(1, note_cnt))
        self._banner_mode = "gain"
        self._banner_title = f"+{points} {label} {notes_glyph}"
        self._banner_sub = (activity[:30] + "…") if len(activity) > 31 else activity
        self._banner_timer = 0.0
        self._banner_duration = 1.85
        self._banner_spring.snap(0.0)
        self._banner_spring.target = 1.0
        self._pulse.value = 1.0
        self._pulse.target = 0.0
        self._ticker.start()

    def trigger_rank_up(self, stat: str = "", old_lv: int = 0, new_lv: int = 0,
                        rank_title: str = "") -> None:
        """Full Persona 5 Royal Rank Up golden star shockwave + music notes + banner."""
        sc = self._scale
        key = stat if stat in SOCIAL_STATS else self._selected
        self._selected = key
        self._gain_stat = key
        cx, cy, radius, _angles = self._geometry_metrics()
        tip = self._stat_tip_pos(key)

        self._rank_flash.value = 1.0
        self._rank_flash.velocity = 0.0
        self._rank_flash.target = 0.0

        # Twin expanding golden starburst shockwaves
        self._particles.append(Particle(
            kind="ring", x=cx, y=cy, vx=0.0, vy=0.0,
            size=radius * 0.92, max_life=0.85, rot=0.0, vrot=36.0,
            color=GOLD, outline=WHITE,
        ))
        self._particles.append(Particle(
            kind="ring", x=tip.x(), y=tip.y(), vx=0.0, vy=0.0,
            size=radius * 0.55, max_life=0.72, rot=18.0, vrot=-48.0,
            delay=0.10, color=WHITE, outline=GOLD,
        ))

        # 18 vibrant Persona 5 gold & colored stars bursting across the chart
        for i in range(18):
            ang = (2.0 * math.pi * i / 18.0) + random.uniform(-0.14, 0.14)
            spd = random.uniform(68, 155) * sc
            ox = cx if i % 2 == 0 else tip.x()
            oy = cy if i % 2 == 0 else tip.y()
            self._particles.append(Particle(
                kind="star" if i % 3 != 2 else "sparkle",
                x=ox, y=oy,
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd - 20.0 * sc,
                size=random.uniform(12, 22) * sc,
                max_life=random.uniform(1.15, 1.75),
                rot=random.uniform(0, 360),
                vrot=random.uniform(-240, 240),
                gravity=55.0 * sc,
                drag=1.35,
                delay=random.uniform(0.0, 0.18),
                color=P5_STAR_COLORS[i % len(P5_STAR_COLORS)],
                outline=BLACK,
            ))

        # 5 celebratory golden music notes rising across the star
        for i in range(5):
            sx = cx + (i - 2) * 34.0 * sc
            self._particles.append(Particle(
                kind="note2" if i % 2 == 1 else "note",
                x=sx, y=cy + 14.0 * sc,
                vx=(i - 2) * 14.0 * sc,
                vy=-74.0 * sc - (i % 3) * 10.0 * sc,
                size=22.0 * sc,
                max_life=1.75,
                rot=random.uniform(-12, 12),
                vrot=random.uniform(-16, 16),
                wave_amp=8.0 * sc,
                wave_freq=6.2,
                phase=i * 1.1,
                delay=0.05 + i * 0.10,
                color=P5_NOTE_GOLD,
                outline=BLACK,
            ))

        label = SOCIAL_STATS.get(key, {}).get("label", key.title()).upper()
        lv_now = new_lv or stat_level(self._totals.get(key, 0))
        lv_prev = old_lv or max(1, lv_now - 1)
        rtitle = (rank_title or stat_rank_title(key, lv_now)).upper()
        self._banner_mode = "rank_up"
        self._banner_title = "RANK UP!"
        self._banner_sub = f"{label}  LV {lv_prev} → LV {lv_now} · {rtitle}"
        self._banner_timer = 0.0
        self._banner_duration = 2.85
        self._banner_spring.snap(0.0)
        self._banner_spring.target = 1.0
        self._ticker.start()

    def trigger_rank_up_flash(self) -> None:
        self.trigger_rank_up(self._selected)

    def _frame(self, dt: float) -> bool:
        self._clock += dt
        moving = self._pulse.step(dt) | self._rank_flash.step(dt) | self._banner_spring.step(dt)
        for sp in self._anim_pts.values():
            moving |= sp.step(dt)
        if self._particles:
            self._particles = [pt for pt in self._particles if pt.step(dt)]
            if self._particles:
                moving = True
        if self._banner_mode:
            self._banner_timer += dt
            if self._banner_timer >= self._banner_duration:
                self._banner_mode = ""
            else:
                moving = True
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

        # Obsidian P5 card background + diagonal Persona crimson slash & subtle radial glow
        p.setPen(QPen(P5_BORDER, 1.2))
        p.setBrush(P5_CARD)
        p.drawRoundedRect(rect, 10.0, 10.0)

        clip = QPainterPath()
        clip.addRoundedRect(rect, 10.0, 10.0)
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(230, 0, 18, 28))
        p.drawPolygon(QPolygonF([
            QPointF(rect.left() + rect.width() * 0.58, rect.top()),
            QPointF(rect.right(), rect.top()),
            QPointF(rect.right(), rect.bottom()),
            QPointF(rect.left() + rect.width() * 0.30, rect.bottom()),
        ]))

        cx, cy, radius, angles = self._geometry_metrics()
        burst = clamp(self._rank_flash.value)
        pulse = clamp(self._pulse.value)

        # Subtle golden aura behind the Persona 5 star
        aura = QRadialGradient(QPointF(cx, cy), radius * (1.28 + 0.25 * burst))
        aura.setColorAt(0.0, QColor(255, 214, 48, int(42 + 88 * burst)))
        aura.setColorAt(0.6, QColor(230, 0, 18, int(18 + 38 * burst)))
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(aura)
        p.drawEllipse(QPointF(cx, cy), radius * 1.35, radius * 1.35)

        # Outer pentagon frame + 5 concentric Persona 5 5-pointed star guide rings
        outer_pent = QPolygonF([
            QPointF(cx + math.cos(a) * radius, cy + math.sin(a) * radius)
            for a in angles
        ])
        p.setPen(QPen(QColor("#3E3E50"), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(outer_pent)

        for ring in range(1, 6):
            frac = ring / 5.0
            r_out = radius * frac
            r_in = r_out * 0.45
            guide_star = star_polygon(cx, cy, r_out, r_in, 5)
            p.setPen(QPen(QColor("#5C4D1E") if ring == 5 else QColor("#2E2E3C"),
                          1.5 if ring == 5 else 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(guide_star)

        # Radial spokes from center to the 5 stat tips
        p.setPen(QPen(QColor("#2E2E3C"), 1.0))
        for a in angles:
            p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * radius, cy + math.sin(a) * radius))

        # Build the dynamic Persona 5 GOLD 5-POINTED STAR polygon
        fracs: list[float] = []
        tip_pts: list[QPointF] = []
        self._vertex_pts.clear()
        self._tip_pts.clear()
        for i, key in enumerate(SOCIAL_STAT_KEYS):
            a = angles[i]
            pts = max(0.0, self._anim_pts[key].value)
            frac = stat_pentagon_fraction(pts)
            if key == self._selected and pulse > 0.01:
                frac = min(1.06, frac + 0.06 * pulse)
            fracs.append(frac)
            tpt = QPointF(cx + math.cos(a) * radius * frac, cy + math.sin(a) * radius * frac)
            tip_pts.append(tpt)
            self._tip_pts[key] = tpt
            self._vertex_pts[key] = QPointF(cx + math.cos(a) * radius * 1.15, cy + math.sin(a) * radius * 1.15)

        star_verts: list[QPointF] = []
        for i in range(5):
            star_verts.append(tip_pts[i])
            nxt_i = (i + 1) % 5
            mid_a = angles[i] + math.pi / 5.0
            valley_frac = max(0.14, 0.46 * 0.5 * (fracs[i] + fracs[nxt_i]))
            star_verts.append(QPointF(
                cx + math.cos(mid_a) * radius * valley_frac,
                cy + math.sin(mid_a) * radius * valley_frac,
            ))
        gold_star = QPolygonF(star_verts)

        # Crimson Persona drop shadow behind the golden star
        p.save()
        p.translate(3.2 * sc, 4.0 * sc)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(230, 0, 18, int(145 + 60 * pulse)))
        p.drawPolygon(gold_star)
        p.restore()

        # Luminous Persona 5 Royal gold gradient fill
        grad = QRadialGradient(QPointF(cx, cy - radius * 0.12), radius * 1.05)
        grad.setColorAt(0.0, QColor(255, 250, 186, 245))
        grad.setColorAt(0.42, QColor(255, 214, 38, 232))
        grad.setColorAt(0.85, QColor(232, 162, 0, 224))
        grad.setColorAt(1.0, QColor(200, 118, 0, 218))
        p.setPen(QPen(WHITE if burst > 0.22 else QColor("#FFF2A6"), (2.4 + 1.4 * pulse) * sc))
        p.setBrush(grad)
        p.drawPolygon(gold_star)

        # Crisp inner star facet lines from center to tips (gives faceted 3D Persona gold-star gleam)
        p.setPen(QPen(QColor(255, 255, 230, 115), 1.1 * sc))
        for tpt in tip_pts:
            p.drawLine(QPointF(cx, cy), tpt)

        # Expanding golden Rank Up starburst wave
        if burst > 0.02:
            b_rad = radius * (0.48 + 0.78 * (1.0 - burst))
            b_star = star_polygon(cx, cy, b_rad, b_rad * 0.46, 5)
            p.setPen(QPen(QColor(255, 232, 96, int(240 * burst)), 3.4 * sc))
            p.setBrush(QColor(255, 220, 64, int(68 * burst)))
            p.drawPolygon(b_star)

        # Vertex gold stars & crisp Persona 5 stat labels
        lbl_font = p5_font(_sc(13, sc, 10), italic=False)
        lv_font = p5_font(_sc(11, sc, 9), italic=False)
        fm_l = QFontMetrics(lbl_font)
        fm_v = QFontMetrics(lv_font)

        for i, key in enumerate(SOCIAL_STAT_KEYS):
            a = angles[i]
            dpt = tip_pts[i]
            is_sel = key == self._selected
            lv = stat_level(self._totals.get(key, 0))
            is_mastery = lv > len(STAT_RANK_THRESHOLDS)

            # Halo glow on the active/gained vertex
            if is_sel and (pulse > 0.04 or burst > 0.04):
                glow_r = (13.0 + 9.0 * max(pulse, burst)) * sc
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 224, 66, int(130 * max(pulse, burst))))
                p.drawEllipse(dpt, glow_r, glow_r)

            # Mini golden 5-point star marker at each stat tip
            star_r = (8.2 if (is_sel or is_mastery) else 6.0) * sc
            p.setPen(QPen(BLACK, 1.5 * sc))
            p.setBrush(WHITE if (is_sel and pulse > 0.35) else (QColor("#FFE852") if (is_sel or is_mastery) else GOLD))
            p.drawPolygon(star_polygon(dpt.x(), dpt.y(), star_r, star_r * 0.45, 5))

            # Label placement around outer ring
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
                p.setPen(GOLD if is_sel else P5_TEXT)
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
                p.setPen(GOLD if is_sel else P5_TEXT)
                p.drawText(QRectF(bx, by, box_w, lh), align | Qt.AlignmentFlag.AlignVCenter.value, name)
                px_lv = bx if i in (1, 2) else (bx + box_w - vw)
                pill = QRectF(px_lv, by + lh + 1 * sc, vw, fm_v.height() + 2)
                p.setPen(QPen(WHITE, 1.1) if is_sel else Qt.PenStyle.NoPen)
                p.setBrush(RED if is_sel else P5_GOLD_BG)
                p.drawRoundedRect(pill, 4.0, 4.0)
                p.setFont(lv_font)
                p.setPen(WHITE if is_sel else P5_GOLD_FG)
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, lv_txt)

        # In-widget Persona 5 music notes & golden star particles
        for pt in self._particles:
            draw_particle(p, pt)

        # Comic Persona 5 floating toast (`+2 PROFICIENCY ♪♪` or `RANK UP!`)
        if self._banner_mode:
            self._paint_banner(p, rect)

        p.setClipping(False)
        p.end()

    def _paint_banner(self, p: QPainter, rect: QRectF) -> None:
        sc = self._scale
        sv = max(0.0, self._banner_spring.value)
        rem = self._banner_duration - self._banner_timer
        fade = clamp(rem / 0.42) if rem < 0.42 else 1.0
        alpha = clamp(sv * 1.4) * fade
        if alpha <= 0.02:
            return

        is_rank = self._banner_mode == "rank_up"
        p.save()
        p.setOpacity(alpha)
        bx_c = rect.center().x()
        # Center neatly over the star heart so KNOWLEDGE (top) and GUTS/CHARM (sides) stay visible
        by_c = rect.center().y() + (6.0 * sc if is_rank else 10.0 * sc)
        p.translate(bx_c, by_c)
        p.rotate(-4.0 if is_rank else -2.5)
        scale_f = 0.68 + 0.32 * sv
        p.scale(scale_f, scale_f)

        tf = p5_font(_sc(22 if is_rank else 15, sc, 12), italic=False)
        sf = p5_font(_sc(11 if is_rank else 10, sc, 8), italic=False)
        tfm = QFontMetrics(tf)
        sfm = QFontMetrics(sf)
        max_w = rect.width() * (0.68 if is_rank else 0.62)
        inner_pad = (44.0 if is_rank else 26.0) * sc
        sub_txt = sfm.elidedText(self._banner_sub, Qt.TextElideMode.ElideRight, int(max(60.0, max_w - inner_pad))) if self._banner_sub else ""
        tw = max(tfm.horizontalAdvance(self._banner_title),
                 sfm.horizontalAdvance(sub_txt) if sub_txt else 0)
        bw = min(max_w, tw + inner_pad)
        bh = (tfm.height() + (sfm.height() + 12.0 if sub_txt else 10.0) * sc)

        skew = 9.0 * sc
        poly = QPolygonF([
            QPointF(-bw * 0.5 + skew, -bh * 0.5),
            QPointF(bw * 0.5, -bh * 0.5),
            QPointF(bw * 0.5 - skew, bh * 0.5),
            QPointF(-bw * 0.5, bh * 0.5),
        ])
        # Shadow ribbon
        p.save()
        p.translate(4.0 * sc, 4.5 * sc)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(RED if is_rank else GOLD)
        p.drawPolygon(poly)
        p.restore()

        p.setPen(QPen(GOLD if is_rank else WHITE, 2.2 * sc))
        p.setBrush(BLACK)
        p.drawPolygon(poly)

        # Spinning gold stars flanking RANK UP!
        if is_rank:
            for side in (-1.0, 1.0):
                p.save()
                p.translate(side * (bw * 0.5 - 10.0 * sc), -bh * 0.28)
                p.rotate(self._clock * 140.0 * side)
                p.setPen(QPen(BLACK, 1.4 * sc))
                p.setBrush(P5_NOTE_GOLD)
                p.drawPolygon(star_polygon(0.0, 0.0, 10.0 * sc, 4.5 * sc, 5))
                p.restore()

        p.setFont(tf)
        p.setPen(P5_NOTE_GOLD)
        if sub_txt:
            p.drawText(QRectF(-bw * 0.5, -bh * 0.5 + 3 * sc, bw, tfm.height()),
                       Qt.AlignmentFlag.AlignCenter.value, self._banner_title)
            p.setFont(sf)
            p.setPen(WHITE)
            p.drawText(QRectF(-bw * 0.5, -bh * 0.5 + tfm.height() + 2 * sc, bw, sfm.height() + 4),
                       Qt.AlignmentFlag.AlignCenter.value, sub_txt)
        else:
            p.drawText(QRectF(-bw * 0.5, -bh * 0.5, bw, bh),
                       Qt.AlignmentFlag.AlignCenter.value, self._banner_title)
        p.restore()


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

        grad = QLinearGradient(card.topLeft(), card.bottomLeft())
        grad.setColorAt(0.0, bg.lighter(108))
        grad.setColorAt(1.0, bg)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(grad)
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
    stats_changed = pyqtSignal()                                  # emitted on log edit, delete, or undo
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
        self._editing_log_id: str | None = None

        # Header bar
        self._blocks_hdr = QLabel("")
        self._blocks_hdr.setStyleSheet(
            f'color: #FFE46B; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
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
        self._habit_list.itemSelectionChanged.connect(self._sync_habit_selection_ui)
        self._habit_list.space_pressed.connect(self._toggle_selected_habit)
        self._habit_list.delete_pressed.connect(self._delete_selected_habit)
        hlc.addWidget(self._habit_list, 1)

        hf_foot = QHBoxLayout()
        hf_foot.setSpacing(6)
        htip = QLabel("Resets daily · Auto-rewards Stats · Space")
        htip.setObjectName("tip")
        hf_foot.addWidget(htip, 1)
        self._hdel_btn = self._btn("DELETE", variant="danger", display=True, px=10)
        self._hdel_btn.setFixedHeight(_sc(26, sc, 22))
        self._hdel_btn.clicked.connect(self._delete_selected_habit)
        hf_foot.addWidget(self._hdel_btn)
        hlc.addLayout(hf_foot)
        hpl.addWidget(hlist_card, 1)
        right.addWidget(self._habits_page, 1)

        # ---- PAGE B: Manual Time Block Logger & Recent History ----------------
        self._log_page = QWidget()
        lpl = QVBoxLayout(self._log_page)
        lpl.setContentsMargins(0, 0, 0, 0)
        lpl.setSpacing(_sc(8, sc, 6))

        self._log_card = _make_card()
        fl = QVBoxLayout(self._log_card)
        fl.setContentsMargins(_sc(12, sc, 9), _sc(9, sc, 7), _sc(12, sc, 9), _sc(9, sc, 7))
        fl.setSpacing(_sc(6, sc, 5))

        f_hdr = QHBoxLayout()
        self._log_hdr_lbl = QLabel("LOG TIME BLOCK (~2H)")
        self._log_hdr_lbl.setObjectName("section")
        f_hdr.addWidget(self._log_hdr_lbl)
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

        self._cancel_edit_btn = self._btn("CANCEL", variant="soft", display=True, px=10)
        self._cancel_edit_btn.setFixedHeight(_sc(30, sc, 25))
        self._cancel_edit_btn.setToolTip("Cancel editing log entry (Esc)")
        self._cancel_edit_btn.clicked.connect(self._cancel_edit)
        self._cancel_edit_btn.setVisible(False)
        act_row.addWidget(self._cancel_edit_btn)
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

        self._pts_spin = P5SpinBox()
        self._pts_spin.setRange(1, 500)
        self._pts_spin.setValue(DEFAULT_STAT_POINTS)
        self._pts_spin.setSuffix(" pts")
        self._pts_spin.setMinimumWidth(_sc(84, sc, 68))
        self._pts_spin.setFixedHeight(_sc(26, sc, 24))
        self._pts_spin.valueChanged.connect(self._on_points_changed)
        pts_row.addWidget(self._pts_spin)
        fl.addLayout(pts_row)
        lpl.addWidget(self._log_card)

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
        self._log_list.itemSelectionChanged.connect(self._sync_log_selection_ui)
        self._log_list.delete_pressed.connect(self._delete_selected)
        self._log_list.itemDoubleClicked.connect(lambda _it: self._start_edit_selected())
        hl.addWidget(self._log_list, 1)

        h_foot = QHBoxLayout()
        h_foot.setSpacing(6)
        req_str = "/".join(str(x) for x in STAT_RANK_THRESHOLDS[1:])
        tip = QLabel(f"Double-click to edit · Ranks: {req_str} pts")
        tip.setObjectName("tip")
        h_foot.addWidget(tip, 1)

        self._log_edit_btn = self._btn("EDIT", variant="gold", display=True, px=10)
        self._log_edit_btn.setFixedHeight(_sc(26, sc, 22))
        self._log_edit_btn.setToolTip("Edit selected log entry (stat, activity, block, points)")
        self._log_edit_btn.clicked.connect(self._start_edit_selected)
        h_foot.addWidget(self._log_edit_btn)

        self._log_del_btn = self._btn("DELETE", variant="danger", display=True, px=10)
        self._log_del_btn.setFixedHeight(_sc(26, sc, 22))
        self._log_del_btn.clicked.connect(self._delete_selected)
        h_foot.addWidget(self._log_del_btn)
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
        if self._editing_log_id is None:
            self._log_btn.setText(f"LOG +{value}")

    def _set_log_filter(self, key: str) -> None:
        self._log_filter = key
        for k, b in self._filter_btns.items():
            b.set_active(k == key)
        self._refresh_log()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self._editing_log_id is not None:
            self._cancel_edit()
            return
        super().keyPressEvent(e)

    def on_closing(self) -> None:
        if self._editing_log_id is not None:
            self._cancel_edit()

    # -- Custom Daily Habits actions -----------------------------------------
    def _sync_habit_selection_ui(self) -> None:
        item = self._habit_list.currentItem()
        self._hdel_btn.setEnabled(bool(item is not None and item.data(ROLE_ID)))

    def _sync_log_selection_ui(self) -> None:
        item = self._log_list.currentItem()
        has_sel = bool(item is not None and item.data(ROLE_ID))
        self._log_edit_btn.setEnabled(has_sel)
        self._log_del_btn.setEnabled(has_sel)

    def _add_habit(self) -> None:
        if self.habits is None:
            return
        h = self.habits.add(self._habit_input.text(), stat=self._habit_stat, points=self._habit_pts)
        if h is None:
            self.nudge_input(self._habit_input, "Type a daily habit title first!", tone="warn")
            return
        self._habit_input.clear()
        self._refresh_habits()
        self.notify(
            f"Added habit '{h['title'][:30]}' (+{h['points']} {SOCIAL_STATS[h['stat']]['short']})",
            tone="ok",
        )
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
            sk = habit.get("stat", "knowledge")
            self.notify(
                f"Completed '{habit['title'][:28]}' (+{habit.get('points', 2)} {SOCIAL_STATS.get(sk, {}).get('short', sk[:3].upper())})",
                tone="ok",
            )
        else:
            play_ui("cancel")
            self._habit_delegate.clear_anim(hid)
            self._refresh_visuals(animate=True)
            self.notify("Unchecked habit for today", tone="info", ms=1500)
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
        self.notify(f"Removed habit '{habit.get('title', '')[:28]}' · Undo available", tone="info")

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
            self._sync_habit_selection_ui()
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
        self._sync_habit_selection_ui()

    # -- Manual Time Block & History actions ---------------------------------
    def _start_edit_selected(self) -> None:
        item = self._log_list.currentItem()
        if item is None:
            return
        entry_id = item.data(ROLE_ID)
        if not entry_id:
            return
        entry = self.store.get(entry_id)
        if entry is None:
            return
        self._editing_log_id = entry_id
        self._set_right_tab("log")
        self._select_stat(entry.get("stat", "knowledge"))
        blk = entry.get("block", default_time_block())
        if blk in TIME_BLOCKS:
            self._select_block(blk)
        self._pts_spin.setValue(max(1, min(500, int(entry.get("points", DEFAULT_STAT_POINTS)))))
        self._act_input.setText(str(entry.get("activity") or ""))
        self._log_hdr_lbl.setText("EDIT LOG ENTRY")
        self._log_hdr_lbl.setStyleSheet("color: #FAD02C;")
        self._log_card.setStyleSheet(
            "QFrame#card { background-color: #1A1610; border: 1.5px solid #F5C400; border-radius: 10px; }"
        )
        self._log_btn.setText("SAVE EDIT")
        self._log_btn._icon = ""
        self._log_btn.set_variant("gold")
        self._log_btn.pulse()
        self._cancel_edit_btn.setVisible(True)
        self.notify(f"Editing '{entry.get('activity', '')[:28]}' — Enter to save, Esc to cancel", tone="gold", ms=2000)
        self._act_input.setFocus()
        self._act_input.selectAll()

    def _cancel_edit(self) -> None:
        self._editing_log_id = None
        self._log_hdr_lbl.setText("LOG TIME BLOCK (~2H)")
        self._log_hdr_lbl.setStyleSheet("")
        self._log_card.setStyleSheet("")
        self._log_btn.setText(f"LOG +{self._pts_spin.value()}")
        self._log_btn._icon = "plus"
        self._log_btn.set_variant("primary")
        self._cancel_edit_btn.setVisible(False)
        self._act_input.clear()

    def _log_activity(self) -> None:
        self._pts_spin.interpretText()
        pts = self._pts_spin.value()
        if self._editing_log_id is not None:
            if not self._act_input.text().strip():
                self.nudge_input(self._act_input, "Describe your activity before saving!", tone="warn")
                return
            eid = self._editing_log_id
            updated, prev = self.store.update(
                eid,
                stat=self._selected_stat,
                activity=self._act_input.text(),
                points=pts,
                block=self._selected_block,
            )
            self._cancel_edit()
            if updated is None or prev is None:
                return
            play_ui("select")
            self._undo_kind = "log_edit"
            self._undo_payload = dict(prev)
            self._refresh_visuals(animate=True)
            self._refresh_log()
            self.stats_changed.emit()
            lbl = SOCIAL_STATS.get(updated["stat"], {}).get("label", updated["stat"].title())
            self._undo.trigger(f"Updated '{updated['activity'][:24]}' → {lbl} +{updated['points']}.")
            self.notify(f"Updated log → {lbl} +{updated['points']}", tone="gold")
            return

        entry, old_lv, new_lv = self.store.add(
            self._selected_stat,
            self._act_input.text(),
            points=pts,
            block=self._selected_block,
            source="manual",
        )
        if entry is None:
            self.nudge_input(self._act_input, "Describe your activity before logging!", tone="warn")
            return
        self._act_input.clear()
        self._refresh_log()
        lbl = SOCIAL_STATS.get(entry["stat"], {}).get("label", entry["stat"].title())
        self.notify(f"+{entry['points']} {lbl} logged!", tone="ok")
        self.stat_logged.emit(entry["stat"], entry["activity"], entry["points"], old_lv, new_lv)

    def prepare_reward_animation(self, stat: str, prev_totals: dict[str, int] | None = None) -> None:
        """Select `stat` and hold the gold star at `prev_totals` so growth animates dramatically."""
        if stat in SOCIAL_STATS:
            self._selected_stat = stat
            for k, b in self._stat_btns.items():
                b.set_active(k == stat)
            meta = SOCIAL_STATS[stat]
            self._rule_hint.setText(f"{meta['label']} — {meta['examples']}")
            self._act_input.setPlaceholderText(meta["placeholder"])
        if prev_totals is not None:
            self._pentagon.set_data(prev_totals, self._selected_stat, animate=False)
        self._refresh_habits()
        self._refresh_log()

    def animate_point_gain(self, stat: str = "", points: int = 0, activity: str = "") -> None:
        """Called in sync with `social stats add point animation.wav` to grow the gold star & emit music notes."""
        if stat in SOCIAL_STATS:
            self._selected_stat = stat
        self._refresh_visuals(animate=True)
        self._refresh_habits()
        self._refresh_log()
        if points > 0 or stat:
            self._pentagon.trigger_point_gain(stat or self._selected_stat, max(1, int(points or 2)), activity)

    def animate_rank_up(self, stat: str = "", old_lv: int = 0, new_lv: int = 0) -> None:
        """Called in sync with `social stats level up animation.wav` for golden star & music note Rank Up."""
        key = stat if stat in SOCIAL_STATS else self._selected_stat
        self._selected_stat = key
        self._refresh_visuals(animate=True)
        lv_now = new_lv or stat_level(self.store.totals().get(key, 0))
        self._pentagon.trigger_rank_up(key, old_lv or max(1, lv_now - 1), lv_now, stat_rank_title(key, lv_now))

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
        if self._editing_log_id == entry_id:
            self._cancel_edit()
        self._undo_kind = "log"
        self._undo_payload = dict(entry)
        self.store.delete(entry_id)
        self.refresh()
        self.stats_changed.emit()
        self._undo.trigger(f"Removed '{entry.get('activity', '')[:28]}' (+{entry.get('points', 0)} pts).")
        self.notify(f"Removed log entry · Undo available", tone="info")

    def _undo_delete(self) -> None:
        if self._undo_payload is None:
            return
        if self._editing_log_id is not None:
            self._cancel_edit()
        if self._undo_kind == "habit" and self.habits is not None:
            self.habits.restore(self._undo_payload)
        elif self._undo_kind in ("log", "log_edit"):
            self.store.restore(self._undo_payload)
        self._undo_kind = ""
        self._undo_payload = None
        self._refresh_visuals(animate=True)
        self._refresh_habits()
        self._refresh_log()
        self.notify("Restored deleted item!", tone="ok")
        self.stats_changed.emit()

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
                "QFrame#subcard { background-color: #2B2009; border: 1.5px solid #F5C400; border-radius: 8px; }"
                if is_sel else ""
            )
            bar.setStyleSheet(
                "QProgressBar::chunk { background-color: #F5C400; border-radius: 3px; }"
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
            self._sync_log_selection_ui()
            return
        src_labels = {"habit": "HABIT", "task": "TASK", "focus": "FOCUS", "note": "NOTE", "chat": "CHAT"}
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
        self._sync_log_selection_ui()


# ---------------------------------------------------------------------------
# DAILY BRIEFING & CONFIDANT SUMMARY DIALOG
# ---------------------------------------------------------------------------
class BriefingDialog(P5FluidPanel):
    """Persona 5 Daily Morning Briefing & Confidant Summary report card."""

    open_events = pyqtSignal()
    open_tasks = pyqtSignal()
    open_stats = pyqtSignal()
    open_wrapup = pyqtSignal()

    def __init__(self, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("DAILY BRIEFING", QSize(565, 640), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale

        self._date_hdr = QLabel("")
        self._date_hdr.setObjectName("subhead")
        self.header_bar.addWidget(self._date_hdr)

        wu_btn = self._btn("WRAP-UP", variant="soft", display=True, px=9)
        wu_btn.setFixedHeight(_sc(24, sc, 20))
        wu_btn.setToolTip("Open Evening Wrap-up recap")
        wu_btn.clicked.connect(lambda _c=False: self.open_wrapup.emit())
        self.header_bar.addWidget(wu_btn)
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

        # Screen Time & App Activity card
        scr_card = _make_card()
        scrl = QVBoxLayout(scr_card)
        scrl.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        scrl.setSpacing(_sc(5, sc, 4))
        scr_top = QHBoxLayout()
        scr_hdr = QLabel("SCREEN TIME & ACTIVITY")
        scr_hdr.setObjectName("section")
        scr_top.addWidget(scr_hdr)
        scr_top.addStretch(1)
        self._scr_total_lbl = QLabel("0m TODAY · 0m SESSION")
        self._scr_total_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
        )
        scr_top.addWidget(self._scr_total_lbl)
        scrl.addLayout(scr_top)
        self._scr_cats_lbl = QLabel("Tracking active screen time locally (pauses when AFK).")
        self._scr_cats_lbl.setObjectName("tip")
        self._scr_cats_lbl.setWordWrap(True)
        scrl.addWidget(self._scr_cats_lbl)
        s_lay.addWidget(scr_card)
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

        # Screen Time summary
        scr = payload.get("screen_time") or {}
        act_txt = scr.get("active_text", "0m")
        cont_txt = scr.get("continuous_text", "0m")
        brks = int(scr.get("breaks", 0))
        self._scr_total_lbl.setText(f"{act_txt.upper()} TODAY · {cont_txt.upper()} NONSTOP · {brks} BREAKS")
        top_cats = scr.get("top_categories") or []
        if top_cats:
            parts = [f"{lbl}: {dur}" for _k, lbl, _s, dur in top_cats[:3]]
            self._scr_cats_lbl.setText("Top activity — " + "  ·  ".join(parts))
        else:
            self._scr_cats_lbl.setText("Active screen time updates locally while you work (pauses when AFK).")


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
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRect(r.translated(6.0, 6.0))
        p.setBrush(accent)
        p.drawRect(r.translated(4.0, 4.0))

        # Deep metallic obsidian body + subtle radial halo behind countdown digits
        body_grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        body_grad.setColorAt(0.0, QColor("#12121A"))
        body_grad.setColorAt(1.0, QColor("#07070A"))
        p.setBrush(body_grad)
        p.drawRect(r)

        p.save()
        p.setClipRect(r)
        slash_w = min(r.width() * 0.34, 156.0 * sc)
        halo_c = QPointF(r.left() + slash_w + (r.width() - slash_w) * 0.5, r.center().y())
        halo = QRadialGradient(halo_c, r.width() * 0.42)
        hc = QColor(accent)
        hc.setAlpha(46 if self._running else 22)
        halo.setColorAt(0.0, hc)
        halo.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.fillRect(r, halo)

        # Diagonal left slash panel for phase tag
        slash = QPolygonF([
            QPointF(r.left(), r.top()),
            QPointF(r.left() + slash_w, r.top()),
            QPointF(r.left() + slash_w - 22.0 * sc, r.bottom()),
            QPointF(r.left(), r.bottom()),
        ])
        if self._running:
            sgrad = QLinearGradient(r.topLeft(), r.bottomLeft())
            sgrad.setColorAt(0.0, accent.lighter(110))
            sgrad.setColorAt(1.0, accent.darker(115))
            p.setBrush(sgrad)
        else:
            sgrad = QLinearGradient(r.topLeft(), r.bottomLeft())
            sgrad.setColorAt(0.0, QColor("#4A0D16"))
            sgrad.setColorAt(1.0, QColor("#28060B"))
            p.setBrush(sgrad)
        p.drawPolygon(slash)

        # Crisp Persona 5 white diagonal razor separator + thin trailing accent stripe
        p.setPen(pen(WHITE, 2.0 * sc))
        p.drawLine(QPointF(r.left() + slash_w + 1.0, r.top()),
                   QPointF(r.left() + slash_w - 22.0 * sc + 1.0, r.bottom()))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRect(QRectF(r.left(), r.top(), r.width(), 1.5))
        p.restore()

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

        # Giant Persona 5 Anton countdown digits on right (with subtle crisp crimson drop)
        digits_rect = QRectF(r.left() + slash_w - 6 * sc, r.top() + 6 * sc,
                             r.width() - slash_w - 8 * sc, r.height() - 22 * sc)
        p.setFont(p5_font(_sc(50, sc, 36), italic=False))
        p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 110))
        p.drawText(digits_rect.translated(2.0 * sc, 2.0 * sc), Qt.AlignmentFlag.AlignCenter.value, self._clock)
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
    focus_stat_changed = pyqtSignal(str)              # knowledge | guts | proficiency | charm | kindness | none
    manual_add_requested = pyqtSignal(int, str)       # total_seconds, label
    manual_cancel_requested = pyqtSignal(str)         # timer_id
    break_requested = pyqtSignal(int)                 # break_minutes
    open_settings = pyqtSignal()

    PRESETS = ((5, "5M"), (10, "10M"), (15, "15M"), (30, "30M"), (60, "1H"))

    def __init__(self, settings: SettingsStore, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("TIMER", QSize(565, 725), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.settings = settings
        self._focus_running = False
        self._focus_phase = "focus"
        self._focus_left = 0
        self._focus_total = 1
        self._focus_completed = 0
        self._focus_stat = str(self.settings["focus_stat"] or "knowledge")
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
        self._focus_spin = P5SpinBox()
        self._focus_spin.setRange(1, 180)
        self._focus_spin.setSuffix(" m")
        self._focus_spin.setMinimumWidth(_sc(76, sc, 62))
        self._focus_spin.setFixedHeight(_sc(30, sc, 25))
        self._focus_spin.valueChanged.connect(self._on_focus_spin_changed)
        ctrl.addWidget(self._focus_spin)

        r_lbl = QLabel("Break")
        r_lbl.setStyleSheet(f'color: #C8C8D8; font-size: {_sc(13, sc, 10)}px; font-weight: bold;')
        ctrl.addWidget(r_lbl)
        self._rest_spin = P5SpinBox()
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

        # Social Stat reward selector for Focus Mode (KNO / GUT / PRO / CHA / KIN / OFF)
        fstat_row = QHBoxLayout()
        fstat_row.setSpacing(_sc(4, sc, 3))
        fs_lbl = QLabel("STAT")
        fs_lbl.setStyleSheet(
            f'color: #FAD02C; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(11, sc, 9)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        fstat_row.addWidget(fs_lbl)
        self._focus_stat_chips: dict[str, P5FluidButton] = {}
        fstat_tips = {
            "knowledge": "Knowledge — study, reading, research",
            "guts": "Guts — tough chores, workouts, pushing through",
            "proficiency": "Proficiency — coding, craft, deep work",
            "charm": "Charm — design, writing, presentations",
            "kindness": "Kindness — helping others, self-care, organizing",
            "none": "Off — run Focus timer without auto-logging a Social Stat",
        }
        for sk in ("knowledge", "guts", "proficiency", "charm", "kindness", "none"):
            chip_txt = "OFF" if sk == "none" else SOCIAL_STATS[sk]["short"]
            sb = self._btn(chip_txt, variant="soft", display=True, px=9)
            sb.setFixedHeight(_sc(24, sc, 20))
            sb.setToolTip(fstat_tips[sk])
            sb.clicked.connect(lambda _c=False, k=sk: self._select_focus_stat(k, emit=True))
            self._focus_stat_chips[sk] = sb
            fstat_row.addWidget(sb)
        fstat_row.addStretch(1)
        self._focus_stat_hint = QLabel("")
        self._focus_stat_hint.setObjectName("tip")
        fstat_row.addWidget(self._focus_stat_hint)
        fl.addLayout(fstat_row)
        self.body.addWidget(fc)

        # -- SCREEN TIME LIVE STRIP ---------------------------------------------
        scr_strip = _make_card("subcard")
        ssl = QHBoxLayout(scr_strip)
        ssl.setContentsMargins(_sc(11, sc, 8), _sc(6, sc, 4), _sc(11, sc, 8), _sc(6, sc, 4))
        ssl.setSpacing(_sc(8, sc, 5))
        scr_tag = QLabel("SCREEN TIME")
        scr_tag.setStyleSheet(
            f'color: #FF4B55; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800;'
        )
        ssl.addWidget(scr_tag)
        self._screen_time_lbl = QLabel("0m today · 0m nonstop")
        self._screen_time_lbl.setStyleSheet(f'color: #F4F4F8; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
        ssl.addWidget(self._screen_time_lbl, 1)
        brk_btn = self._btn("5M BREAK", variant="mint", display=True, px=9)
        brk_btn.setFixedHeight(_sc(24, sc, 20))
        brk_btn.setToolTip("Start a 5-minute break timer and reset continuous screen-time counter")
        brk_btn.clicked.connect(self._request_quick_break)
        ssl.addWidget(brk_btn)
        self.body.addWidget(scr_strip)

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
        self._h_spin = P5SpinBox()
        self._h_spin.setRange(0, 23)
        self._h_spin.setSuffix(" h")
        self._h_spin.setFixedHeight(_sc(30, sc, 25))
        self._m_spin = P5SpinBox()
        self._m_spin.setRange(0, 59)
        self._m_spin.setValue(10)
        self._m_spin.setSuffix(" m")
        self._m_spin.setFixedHeight(_sc(30, sc, 25))
        self._s_spin = P5SpinBox()
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

        self._start_manual_btn = self._btn("START TIMER", variant="primary", icon="plus", display=True, px=10)
        self._start_manual_btn.setFixedHeight(_sc(30, sc, 25))
        self._start_manual_btn.clicked.connect(self._add_manual_timer)
        lbl_row.addWidget(self._start_manual_btn)
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

    def _request_quick_break(self) -> None:
        self.notify("5-minute break started — stretch & hydrate!", tone="ok")
        self.break_requested.emit(5)

    def _cancel_manual_timer(self, tid: str) -> None:
        self.notify("Timer cancelled", tone="info", ms=1500)
        self.manual_cancel_requested.emit(tid)

    def _select_focus_stat(self, stat: str, emit: bool = False) -> None:
        key = stat if stat in self._focus_stat_chips else "knowledge"
        self._focus_stat = key
        for k, btn in self._focus_stat_chips.items():
            btn.set_active(k == key, active_variant="gold" if k != "none" else "primary")
        self._focus_stat_hint.setText("No reward" if key == "none" else SOCIAL_STATS[key]["label"])
        if emit:
            self.notify(
                "Focus stat reward disabled" if key == "none" else f"Focus reward: {SOCIAL_STATS[key]['label']}",
                tone="gold",
                ms=1600,
            )
            self.focus_stat_changed.emit(key)

    def _sync_spinners_from_settings(self) -> None:
        self._select_focus_stat(str(self.settings["focus_stat"] or "knowledge"), emit=False)
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
            self.notify("Focus session stopped", tone="info")
            self.focus_stop_requested.emit()
        else:
            self._focus_spin.interpretText()
            self._rest_spin.interpretText()
            fm, rm = self._focus_spin.value(), self._rest_spin.value()
            self.notify(f"Focus started: {fm}m focus / {rm}m break!", tone="ok")
            self.focus_start_requested.emit(fm, rm)

    def _on_manual_spin_edited(self, _val: int) -> None:
        self._preset_default_label = ""

    def _apply_preset(self, minutes: int) -> None:
        h, m = divmod(minutes, 60)
        self._preset_default_label = ""
        self._h_spin.setValue(h)
        self._m_spin.setValue(m)
        self._s_spin.setValue(0)
        self._manual_err.hide()
        self._start_manual_btn.pulse()
        self.notify(f"Preset {minutes}m ready — press Enter or START TIMER", tone="gold", ms=1600)
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
        self._start_manual_btn.pulse()
        self.notify(f"Countdown to 23:59 set ({h:02d}:{m:02d}:{s:02d})", tone="gold", ms=1800)
        self._label_edit.setFocus()

    def _add_manual_timer(self) -> None:
        for sp in (self._h_spin, self._m_spin, self._s_spin):
            sp.interpretText()
        total = self._h_spin.value() * 3600 + self._m_spin.value() * 60 + self._s_spin.value()
        if total <= 0:
            msg = "Set at least 1 second before starting a timer."
            self._manual_err.setText(msg)
            self._manual_err.show()
            self.nudge_input(self._m_spin, msg, tone="warn")
            return
        self._manual_err.hide()
        label = self._label_edit.text().strip() or self._preset_default_label
        self._preset_default_label = ""
        self._label_edit.clear()
        self.notify(f"Started timer: {label or _fmt_hms(total)} ({_fmt_hms(total)})", tone="ok")
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
            cancel.clicked.connect(lambda _c=False, i=tid: self._cancel_manual_timer(i))
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

    def update_screen_time(self, summary: dict) -> None:
        if not summary:
            return
        act = summary.get("active_text", "0m")
        cont = summary.get("continuous_text", "0m")
        brks = int(summary.get("breaks", 0))
        self._screen_time_lbl.setText(f"{act} today · {cont} nonstop · {brks} breaks")


# ---------------------------------------------------------------------------
# QUICK COMMAND & FREE-FORM ASK BAR (Spotlight / Raycast-style Persona 5 palette)
# ---------------------------------------------------------------------------
class QuickCommandDialog(P5FluidPanel):
    """Floating Persona 5 Quick Command & Free-Form Ask bar (summonable via global shortcut or Tray)."""

    submitted = pyqtSignal(str)
    action_requested = pyqtSignal(str)
    clear_requested = pyqtSignal()

    def __init__(self, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("ASK & COMMAND", QSize(565, 330), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.setFixedSize(_sc(565, sc, 450), _sc(330, sc, 280))
        self._history: list[str] = []
        self._hist_idx = -1
        self._action_target = ""

        # Header status pill (shortcut / DND indicator) + Close
        self._hdr_badge = QLabel("CTRL+SHIFT+M")
        self._hdr_badge.setFixedHeight(_sc(22, sc, 18))
        self._hdr_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hdr_badge.setStyleSheet(
            f'background: #1C1C26; color: #FAD02C; border: 1px solid #4A3B12; border-radius: 4px; '
            f'padding: 0px {_sc(8, sc, 6)}px; font-family: "Barlow Condensed", sans-serif; '
            f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
        )
        self.header_bar.addWidget(self._hdr_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self._add_close_button()

        # Quick-capture chips row
        chips_row = QHBoxLayout()
        chips_row.setSpacing(_sc(5, sc, 4))
        for label, kind, payload, var in (
            ("+ TASK",    "prefix", "task ",     "soft"),
            ("+ NOTE",    "prefix", "note ",     "soft"),
            ("+ EVENT",   "prefix", "event ",    "soft"),
            ("FOCUS 25M", "run",    "focus 25",  "primary"),
            ("5M BREAK",  "run",    "break 5",   "soft"),
            ("WRAP-UP",   "run",    "wrapup",    "gold"),
        ):
            cb = self._btn(label, variant=var, display=True, px=9)
            cb.setFixedHeight(_sc(24, sc, 20))
            if kind == "prefix":
                cb.clicked.connect(lambda _c=False, p=payload: self._prefill(p))
            else:
                cb.clicked.connect(lambda _c=False, p=payload: self._run_quick(p))
            chips_row.addWidget(cb)
        chips_row.addStretch(1)
        self.body.addLayout(chips_row)

        # Prominent Spotlight input bar
        in_row = QHBoxLayout()
        in_row.setSpacing(_sc(6, sc, 4))
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Talk to Morgana, or type: task <title> at 15:00 · note <idea> · focus 25 · dnd")
        self.input_edit.setFixedHeight(_sc(38, sc, 30))
        self.input_edit.returnPressed.connect(self._submit)
        self.input_edit.installEventFilter(self)
        in_row.addWidget(self.input_edit, 1)

        send_btn = self._btn("SEND", variant="primary", display=True, px=11)
        send_btn.setFixedHeight(_sc(38, sc, 30))
        send_btn.setMinimumWidth(_sc(68, sc, 56))
        send_btn.clicked.connect(self._submit)
        in_row.addWidget(send_btn)
        self.body.addLayout(in_row)

        # Morgana Live Response Card
        self._resp_frame = _MorganaBriefingFrame(scale=sc)
        self._resp_frame.set_accent(GOLD)
        rl = QHBoxLayout(self._resp_frame)
        rl.setContentsMargins(_sc(24, sc, 18), _sc(10, sc, 8), _sc(12, sc, 9), _sc(10, sc, 8))
        rl.setSpacing(_sc(11, sc, 8))

        av_lbl = QLabel()
        av_lbl.setPixmap(_morgana_avatar(_sc(48, sc, 38)))
        av_lbl.setFixedSize(_sc(48, sc, 38), _sc(48, sc, 38))
        rl.addWidget(av_lbl, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(_sc(6, sc, 4))
        top_r = QHBoxLayout()
        top_r.setSpacing(_sc(6, sc, 4))

        m_lbl = QLabel("MORGANA")
        m_lbl.setStyleSheet(
            f'background: #0B0B0B; color: #FFFFFF; border: 1.5px solid #FFFFFF; '
            f'border-radius: 4px; padding: 1px {_sc(7, sc, 5)}px; '
            f'font-family: "Anton", "Barlow Condensed", sans-serif; font-size: {_sc(12, sc, 10)}px;'
        )
        top_r.addWidget(m_lbl)

        self._resp_badge = QLabel("READY")
        self._resp_badge.setStyleSheet(
            f'background: #2B2208; color: #FAD02C; border: 1px solid #6C5514; border-radius: 4px; '
            f'padding: 1px {_sc(7, sc, 5)}px; font-family: "Barlow Condensed", sans-serif; '
            f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
        )
        top_r.addWidget(self._resp_badge)
        top_r.addStretch(1)

        self._clear_btn = self._btn("NEW CHAT", variant="ghost", display=True, px=9)
        self._clear_btn.setFixedHeight(_sc(22, sc, 19))
        self._clear_btn.setToolTip("Clear multi-turn AI chat memory")
        self._clear_btn.clicked.connect(self._on_clear_chat)
        self._clear_btn.setVisible(False)
        top_r.addWidget(self._clear_btn)

        self._jump_btn = self._btn("OPEN", variant="gold", display=True, px=9)
        self._jump_btn.setFixedHeight(_sc(22, sc, 19))
        self._jump_btn.clicked.connect(self._on_jump_clicked)
        self._jump_btn.setVisible(False)
        top_r.addWidget(self._jump_btn)
        col.addLayout(top_r)

        self._reply_lbl = QLabel(
            "What's on your mind? Add a quick task/note, kick off a Focus sprint, toggle Do Not Disturb, "
            "or just talk to me!"
        )
        self._reply_lbl.setWordWrap(True)
        self._reply_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._reply_lbl.setStyleSheet(
            f'color: #FFFFFF; font-family: "Arsenal", "Optima", "Segoe UI", sans-serif; '
            f'font-size: {_sc(14, sc, 11)}px; font-weight: bold;'
        )
        col.addWidget(self._reply_lbl, 1)
        rl.addLayout(col, 1)
        self.body.addWidget(self._resp_frame, 1)

        # Compact hint strip
        self._hint_lbl = QLabel(
            "Examples:  \"remind me send report tomorrow 10:00\"  ·  \"note great idea #work\"  ·  "
            "\"status\"  ·  \"dnd on\"  ·  or just banter with Morgana!"
        )
        self._hint_lbl.setObjectName("tip")
        self._hint_lbl.setWordWrap(True)
        self.body.addWidget(self._hint_lbl)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.input_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up and self._history:
                if self._hist_idx < 0:
                    self._hist_idx = len(self._history) - 1
                elif self._hist_idx > 0:
                    self._hist_idx -= 1
                self.input_edit.setText(self._history[self._hist_idx])
                return True
            if key == Qt.Key.Key_Down and self._history and self._hist_idx >= 0:
                self._hist_idx += 1
                if self._hist_idx >= len(self._history):
                    self._hist_idx = -1
                    self.input_edit.clear()
                else:
                    self.input_edit.setText(self._history[self._hist_idx])
                return True
        return super().eventFilter(obj, event)

    def set_header_badge(self, text: str, alert: bool = False) -> None:
        sc = self._ui_scale
        self._hdr_badge.setText(text.upper())
        if alert:
            self._hdr_badge.setStyleSheet(
                f'background: #360A10; color: #FF5E6B; border: 1px solid #E60012; border-radius: 4px; '
                f'padding: 0px {_sc(8, sc, 6)}px; font-family: "Barlow Condensed", sans-serif; '
                f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
            )
        else:
            self._hdr_badge.setStyleSheet(
                f'background: #1C1C26; color: #FAD02C; border: 1px solid #4A3B12; border-radius: 4px; '
                f'padding: 0px {_sc(8, sc, 6)}px; font-family: "Barlow Condensed", sans-serif; '
                f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
            )

    def set_has_chat_history(self, has_history: bool) -> None:
        self._clear_btn.setVisible(bool(has_history))

    def show_reply(self, text: str, badge: str = "MORGANA", action_label: str = "",
                   action_target: str = "", accent_red: bool = False) -> None:
        self._resp_badge.setText((badge or "MORGANA").upper())
        self._reply_lbl.setText(text)
        self._resp_frame.set_accent(RED if accent_red else GOLD)
        self._action_target = action_target or ""
        if action_label and action_target:
            self._jump_btn.setText(action_label.upper())
            self._jump_btn.setVisible(True)
        else:
            self._jump_btn.setVisible(False)

    def show_thinking(self, prompt: str) -> None:
        short = (prompt[:36] + "…") if len(prompt) > 36 else prompt
        self.show_reply(f"Hmm... let me think about \"{short}\"...", badge="GEMINI · THINKING", accent_red=False)

    def _focus_input(self) -> None:
        if self.isVisible() and not self._closing:
            self._bring_to_front()
            self.input_edit.setFocus()
            self.input_edit.selectAll()

    def show_spotlight(self) -> None:
        """Open cleanly at full opacity centered near the top of the active cursor screen."""
        from PyQt6.QtGui import QCursor as _QCursor
        screen = QGuiApplication.screenAt(_QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left() + 8, min(area.left() + (area.width() - self.width()) // 2, area.right() - self.width() - 8))
        y = max(area.top() + 16, min(area.top() + max(32, int(area.height() * 0.16)), area.bottom() - self.height() - 16))
        self._base_pos = QPoint(x, y)
        self._closing = False
        self.setWindowOpacity(1.0)
        if not self.isVisible():
            self._open.snap(0.45)
            self.move(self._base_pos.x(), self._base_pos.y() + int(round(0.55 * self.SLIDE)))
            self.show()
        else:
            self.move(self._base_pos)
            self.refresh()
        self._open.configure(360.0)
        self._open.target = 1.0
        self._ticker.start()
        self.update()
        self._focus_input()
        QTimer.singleShot(0, self._focus_input)
        QTimer.singleShot(50, self._focus_input)

    def _prefill(self, prefix: str) -> None:
        self.input_edit.setText(prefix)
        self.input_edit.setFocus()
        self.input_edit.end(False)

    def _run_quick(self, cmd: str) -> None:
        self.input_edit.setText(cmd)
        self._submit()

    def _on_clear_chat(self) -> None:
        self.notify("Started a fresh chat", tone="info", ms=1500)
        self.clear_requested.emit()

    def _submit(self) -> None:
        txt = self.input_edit.text().strip()
        if not txt:
            self.nudge_input(self.input_edit, "Type a command or message for Morgana first!", tone="warn")
            return
        if not self._history or self._history[-1] != txt:
            self._history.append(txt)
            self._history = self._history[-30:]
        self._hist_idx = -1
        self.input_edit.clear()
        self.submitted.emit(txt)
        self.input_edit.setFocus()

    def _on_jump_clicked(self) -> None:
        if self._action_target:
            self.action_requested.emit(self._action_target)


# ---------------------------------------------------------------------------
# EVENING WRAP-UP RECAP DIALOG (End-of-Day Summary + Task Rollover + 1-Line Journal)
# ---------------------------------------------------------------------------
class WrapUpDialog(P5FluidPanel):
    """Persona 5 Evening Wrap-up companion panel closing the daily loop."""

    rollover_requested = pyqtSignal(list)       # list[str] of task IDs to move to tomorrow
    reflection_saved = pyqtSignal(str, str)     # (reflection_text, mood_label)
    open_notes = pyqtSignal()
    open_tasks = pyqtSignal()
    open_stats = pyqtSignal()

    MOODS = ("GREAT", "GOOD", "OKAY", "TIRED", "ROUGH")

    def __init__(self, on_top: bool = True, ui_scale: float = 1.0) -> None:
        super().__init__("EVENING WRAP-UP", QSize(580, 665), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self._selected_mood = "GOOD"
        self._rollover_checks: list[tuple[str, P5CheckBox]] = []

        self._date_hdr = QLabel("")
        self._date_hdr.setObjectName("subhead")
        self.header_bar.addWidget(self._date_hdr)
        self._add_close_button()

        # 1) Morgana Evening Banner
        self._banner = _MorganaBriefingFrame(scale=sc)
        self._banner.set_accent(GOLD)
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

        ev_pill = QLabel("EVENING RECAP")
        ev_pill.setStyleSheet(
            f'background: #2B2208; color: #FAD02C; border: 1px solid #6C5514; border-radius: 4px; '
            f'padding: 1px {_sc(7, sc, 5)}px; font-family: "Barlow Condensed", sans-serif; '
            f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
        )
        top_m.addWidget(ev_pill)

        self._pts_pill = QLabel("+0 PTS TODAY")
        self._pts_pill.setStyleSheet(
            f'background: #360A10; color: #FF5E6B; border: 1px solid #751824; border-radius: 4px; '
            f'padding: 1px {_sc(7, sc, 5)}px; font-family: "Barlow Condensed", sans-serif; '
            f'font-weight: 800; font-size: {_sc(11, sc, 9)}px;'
        )
        top_m.addWidget(self._pts_pill)
        top_m.addStretch(1)
        scol.addLayout(top_m)

        self._headline_lbl = QLabel("SOLID WORK TODAY!")
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
        self._kpis: dict[str, QLabel] = {}
        for key, title in (("done", "DONE TODAY"), ("habits", "HABITS"),
                           ("focus", "FOCUS"), ("screen", "SCREEN TIME")):
            tile = _make_card("subcard")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(_sc(8, sc, 5), _sc(6, sc, 4), _sc(8, sc, 5), _sc(6, sc, 4))
            tl.setSpacing(2)
            v_lbl = QLabel("0")
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v_lbl.setStyleSheet(
                f'color: #FFFFFF; font-family: "Anton", "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(19, sc, 14)}px;'
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
            self._kpis[key] = v_lbl
        self.body.addLayout(kpi_row)

        # 3) Scrollable Recap, Rollover & 1-Line Reflection cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        s_body = QWidget()
        s_lay = QVBoxLayout(s_body)
        s_lay.setContentsMargins(0, 0, _sc(4, sc, 2), 0)
        s_lay.setSpacing(_sc(8, sc, 6))

        # Card A: Today's Wins & Activity Summary
        win_card = _make_card()
        wl = QVBoxLayout(win_card)
        wl.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        wl.setSpacing(_sc(4, sc, 3))
        wh = QLabel("TODAY'S WINS & ACTIVITY")
        wh.setObjectName("section")
        wl.addWidget(wh)
        self._wins_lbl = QLabel("")
        self._wins_lbl.setWordWrap(True)
        self._wins_lbl.setStyleSheet(f"color: #E4E4F0; font-size: {_sc(13, sc, 10)}px;")
        wl.addWidget(self._wins_lbl)
        self._activity_lbl = QLabel("")
        self._activity_lbl.setObjectName("tip")
        self._activity_lbl.setWordWrap(True)
        wl.addWidget(self._activity_lbl)
        s_lay.addWidget(win_card)

        # Card B: Unfinished Tasks -> Roll Over to Tomorrow
        ro_card = _make_card()
        rol = QVBoxLayout(ro_card)
        rol.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        rol.setSpacing(_sc(6, sc, 4))
        ro_top = QHBoxLayout()
        ro_hdr = QLabel("UNFINISHED TASKS — MOVE TO TOMORROW")
        ro_hdr.setObjectName("section")
        ro_top.addWidget(ro_hdr)
        ro_top.addStretch(1)
        self._sel_all_btn = self._btn("SELECT ALL", variant="ghost", display=True, px=9)
        self._sel_all_btn.setFixedHeight(_sc(22, sc, 19))
        self._sel_all_btn.clicked.connect(self._toggle_select_all)
        ro_top.addWidget(self._sel_all_btn)
        rol.addLayout(ro_top)

        self._rollover_lay = QVBoxLayout()
        self._rollover_lay.setSpacing(_sc(4, sc, 3))
        rol.addLayout(self._rollover_lay)

        ro_act = QHBoxLayout()
        ro_act.setSpacing(_sc(6, sc, 4))
        self._rollover_btn = self._btn("MOVE SELECTED → TOMORROW", variant="gold", display=True, px=10)
        self._rollover_btn.setFixedHeight(_sc(27, sc, 23))
        self._rollover_btn.clicked.connect(self._on_rollover_clicked)
        ro_act.addWidget(self._rollover_btn)
        self._rollover_status = QLabel("")
        self._rollover_status.setObjectName("tip")
        ro_act.addWidget(self._rollover_status, 1)
        rol.addLayout(ro_act)
        s_lay.addWidget(ro_card)

        # Card C: 1-Line Daily Journal Reflection
        jr_card = _make_card()
        jrl = QVBoxLayout(jr_card)
        jrl.setContentsMargins(_sc(11, sc, 8), _sc(9, sc, 7), _sc(11, sc, 8), _sc(9, sc, 7))
        jrl.setSpacing(_sc(6, sc, 4))
        jrh = QLabel("1-LINE REFLECTION → JOURNAL NOTE")
        jrh.setObjectName("section")
        jrl.addWidget(jrh)

        mood_row = QHBoxLayout()
        mood_row.setSpacing(_sc(4, sc, 3))
        ml = QLabel("MOOD:")
        ml.setStyleSheet(
            f'color: #9292A4; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
        )
        mood_row.addWidget(ml)
        self._mood_btns: dict[str, P5FluidButton] = {}
        for m in self.MOODS:
            mb = self._btn(m, variant="soft", display=True, px=9)
            mb.setFixedHeight(_sc(22, sc, 19))
            mb.clicked.connect(lambda _c=False, k=m: self._set_mood(k))
            self._mood_btns[m] = mb
            mood_row.addWidget(mb)
        mood_row.addStretch(1)
        jrl.addLayout(mood_row)
        self._sync_mood_ui()

        ref_row = QHBoxLayout()
        ref_row.setSpacing(_sc(6, sc, 4))
        self.reflection_edit = QLineEdit()
        self.reflection_edit.setPlaceholderText("One win, lesson, or thought to close out today...")
        self.reflection_edit.setFixedHeight(_sc(34, sc, 28))
        self.reflection_edit.returnPressed.connect(self._on_save_reflection)
        ref_row.addWidget(self.reflection_edit, 1)

        save_ref_btn = self._btn("SAVE TO JOURNAL", variant="primary", display=True, px=10)
        save_ref_btn.setFixedHeight(_sc(34, sc, 28))
        save_ref_btn.clicked.connect(self._on_save_reflection)
        ref_row.addWidget(save_ref_btn)
        jrl.addLayout(ref_row)

        self._ref_status = QLabel("Saves cleanly into your Journal notebook with #journal #wrapup.")
        self._ref_status.setObjectName("tip")
        jrl.addWidget(self._ref_status)
        s_lay.addWidget(jr_card)
        s_lay.addStretch(1)

        scroll.setWidget(s_body)
        self.body.addWidget(scroll, 1)

        # Footer quick jumps + primary close
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self._btn_nt = self._btn("NOTES", variant="soft", display=True, px=10)
        self._btn_nt.setFixedHeight(_sc(28, sc, 24))
        self._btn_nt.clicked.connect(lambda _c=False: self.open_notes.emit())
        foot.addWidget(self._btn_nt)

        self._btn_tk = self._btn("TASKS", variant="soft", display=True, px=10)
        self._btn_tk.setFixedHeight(_sc(28, sc, 24))
        self._btn_tk.clicked.connect(lambda _c=False: self.open_tasks.emit())
        foot.addWidget(self._btn_tk)

        self._btn_st = self._btn("STATS", variant="soft", display=True, px=10)
        self._btn_st.setFixedHeight(_sc(28, sc, 24))
        self._btn_st.clicked.connect(lambda _c=False: self.open_stats.emit())
        foot.addWidget(self._btn_st)
        foot.addStretch(1)

        done_btn = self._btn("GOOD NIGHT!", variant="primary", display=True, px=11)
        done_btn.setFixedHeight(_sc(28, sc, 24))
        done_btn.clicked.connect(self.close)
        foot.addWidget(done_btn)
        self.body.addLayout(foot)

    def _set_mood(self, mood: str) -> None:
        self._selected_mood = mood
        self._sync_mood_ui()

    def _sync_mood_ui(self) -> None:
        for k, btn in self._mood_btns.items():
            btn.set_active(k == self._selected_mood, active_variant="gold")

    def set_data(self, payload: dict) -> None:
        """Populate Evening Wrap-up metrics, unfinished tasks, and daily summary."""
        sc = self._ui_scale
        recap = build_evening_wrapup(payload)
        self._date_hdr.setText(recap["date_banner"])
        self._headline_lbl.setText(recap["headline"])
        self._advice_lbl.setText(recap["advice"])

        pts = int(payload.get("today_points", 0))
        self._pts_pill.setText(f"+{pts} PTS TODAY")

        done_n = int(payload.get("done_today_count", 0))
        h_done = int(payload.get("habits_done", 0))
        h_tot = int(payload.get("habits_total", 0))
        foc_n = int(payload.get("focus_sessions", 0))
        scr_txt = str(payload.get("screen_time_text") or "0m")

        self._kpis["done"].setText(str(done_n))
        self._kpis["done"].setStyleSheet(
            f'color: {"#5CE09A" if done_n > 0 else "#FFFFFF"}; '
            f'font-family: "Anton", "Barlow Condensed", sans-serif; font-size: {_sc(19, sc, 14)}px;'
        )
        self._kpis["habits"].setText(f"{h_done}/{h_tot}" if h_tot else "—")
        self._kpis["focus"].setText(str(foc_n))
        self._kpis["screen"].setText(scr_txt)

        done_titles = list(payload.get("done_today_titles") or ())
        if done_titles:
            shown = [f"✓ {t[:46]}" for t in done_titles[:5]]
            if len(done_titles) > 5:
                shown.append(f"…and {len(done_titles) - 5} more completed today!")
            self._wins_lbl.setText("\n".join(shown))
        else:
            self._wins_lbl.setText("No tasks checked off today yet — even small progress or rest counts!")

        top_cats = list(payload.get("top_categories") or ())
        brks = int(payload.get("breaks", 0))
        if top_cats:
            cat_str = "  ·  ".join(f"{lbl} {dur}" for _k, lbl, _s, dur in top_cats[:4])
            self._activity_lbl.setText(f"Top activity: {cat_str}  ·  {brks} breaks taken")
        else:
            self._activity_lbl.setText(f"Active screen time: {scr_txt}  ·  {brks} breaks taken")

        # Populate unfinished tasks rollover list
        _clear_layout(self._rollover_lay)
        self._rollover_checks.clear()
        pending_items = list(payload.get("pending_items") or ())
        if not pending_items:
            ok_lbl = QLabel("All clear! Zero unfinished tasks waiting—tomorrow's slate is wide open.")
            ok_lbl.setWordWrap(True)
            ok_lbl.setStyleSheet(f"color: #5CE09A; font-size: {_sc(13, sc, 10)}px; font-weight: bold;")
            self._rollover_lay.addWidget(ok_lbl)
            self._rollover_btn.setEnabled(False)
            self._sel_all_btn.setEnabled(False)
        else:
            self._rollover_btn.setEnabled(True)
            self._sel_all_btn.setEnabled(True)
            for item in pending_items[:8]:
                tid = item["id"]
                tag = item.get("tag") or ""
                label = f"{item['title'][:46]}" + (f"  [{tag}]" if tag else "")
                cb = P5CheckBox(label, scale=sc)
                cb.setChecked(bool(item.get("preselect")))
                self._rollover_lay.addWidget(cb)
                self._rollover_checks.append((tid, cb))
            if len(pending_items) > 8:
                more = QLabel(f"…and {len(pending_items) - 8} more pending tasks in Tasks")
                more.setObjectName("tip")
                self._rollover_lay.addWidget(more)

    def _toggle_select_all(self) -> None:
        if not self._rollover_checks:
            return
        any_unchecked = any(not cb.isChecked() for _, cb in self._rollover_checks)
        for _, cb in self._rollover_checks:
            cb.setChecked(any_unchecked)

    def _on_rollover_clicked(self) -> None:
        chosen = [tid for tid, cb in self._rollover_checks if cb.isChecked()]
        if not chosen:
            play_ui("cancel")
            msg = "Check at least one task to move to tomorrow."
            self._rollover_status.setText(msg)
            self.notify(msg, tone="warn")
            return
        self.rollover_requested.emit(chosen)
        word = "task" if len(chosen) == 1 else "tasks"
        msg = f"Moved {len(chosen)} {word} to tomorrow!"
        self._rollover_status.setText(msg)
        self.notify(msg, tone="ok")

    def _on_save_reflection(self) -> None:
        txt = self.reflection_edit.text().strip()
        if not txt:
            msg = "Type a short reflection first!"
            self._ref_status.setText(msg)
            self.nudge_input(self.reflection_edit, msg, tone="warn")
            return
        self.reflection_edit.clear()
        self.reflection_saved.emit(txt, self._selected_mood)
        msg = f"Saved [{self._selected_mood}] reflection to Journal in Notes!"
        self._ref_status.setText(msg)
        self.notify(msg, tone="ok")


# ---------------------------------------------------------------------------
# 10. FINANCE LEDGER (bot-wa inspired personal finance window)
# ---------------------------------------------------------------------------
class FinanceTxDelegate(QStyledItemDelegate):
    """Paints Persona 5 finance transaction rows with color-coded left stripe, inline hover Edit/Delete, and flash."""

    edit_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, scale: float = 1.0) -> None:
        super().__init__(parent)
        self._scale = scale
        self._flashes: dict[str, float] = {}
        self._ticker = FrameTicker(self._frame, self)

    def flash_item(self, tx_id: str) -> None:
        if not tx_id:
            return
        self._flashes[tx_id] = 1.0
        self._ticker.start()

    def _frame(self, dt: float) -> bool:
        for k in list(self._flashes):
            nxt = self._flashes[k] - dt * 2.2
            if nxt <= 0.01:
                del self._flashes[k]
            else:
                self._flashes[k] = nxt
        view = self.parent()
        if view is not None and hasattr(view, "viewport"):
            view.viewport().update()
        return bool(self._flashes)

    def _action_rects(self, r: QRectF) -> tuple[QRectF, QRectF]:
        sc = self._scale
        sz = float(_sc(24, sc, 20))
        gap = float(_sc(4, sc, 3))
        y = r.center().y() - sz / 2.0
        del_r = QRectF(r.right() - sz - _sc(7, sc, 5), y, sz, sz)
        edit_r = QRectF(del_r.left() - gap - sz, y, sz, sz)
        return edit_r, del_r

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(0, _sc(72, self._scale, 56))
        return QSize(0, _sc(54, self._scale, 44))

    def paint(self, p: QPainter, option, index) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        sc = self._scale
        tx_id = index.data(ROLE_ID)

        if tx_id is None:
            p.setPen(P5_MUTED)
            p.setFont(ui_font(_sc(13, sc, 10), bold=False))
            p.drawText(r.adjusted(12, 0, -12, 0),
                       Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                       index.data(Qt.ItemDataRole.DisplayRole) or "")
            p.restore()
            return

        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = P5_SELECTED if sel else (P5_HOVER if hov else P5_SURFACE)
        bd = RED if sel else (P5_BORDER_HI if hov else P5_BORDER)
        tx_type = index.data(ROLE_CAT) or "expense"
        amt_txt = index.data(ROLE_BADGE) or "Rp 0"
        meta_txt = index.data(ROLE_DATE) or ""
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        fl = self._flashes.get(tx_id, 0.0)

        if tx_type == "income":
            tag, tag_bg, tag_fg, amt_fg, stripe_col = "IN", P5_MINT_BG, P5_MINT_FG, P5_MINT_FG, P5_MINT_FG
        elif tx_type == "transfer":
            tag, tag_bg, tag_fg, amt_fg, stripe_col = "TF", P5_GOLD_BG, P5_GOLD_FG, P5_GOLD_FG, GOLD
        else:
            tag, tag_bg, tag_fg, amt_fg, stripe_col = "OUT", P5_RED_BG, P5_RED_FG, P5_RED_FG, RED

        grad = QLinearGradient(r.topLeft(), r.bottomLeft())
        grad.setColorAt(0.0, bg.lighter(112 if hov else 108))
        grad.setColorAt(1.0, bg)
        if fl > 0.01:
            bd = lerp_color(bd, stripe_col, fl)
        p.setPen(QPen(bd, 1.5 if (sel or fl > 0.05) else 1.0))
        p.setBrush(grad)
        p.drawRoundedRect(r, 7.0, 7.0)

        # Brief Persona 5 flash sweep for newly added / edited rows
        if fl > 0.01:
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(r, 7.0, 7.0)
            p.setClipPath(clip)
            fc = QColor(stripe_col)
            fc.setAlpha(int(85 * fl))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fc)
            p.drawRect(r)
            p.restore()

        # Crisp color-coded left accent bar (Mint IN / Gold TF / Crimson OUT)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(WHITE if sel else stripe_col)
        p.drawRoundedRect(QRectF(r.left() + 0.5, r.top() + 5.0, 3.2 if hov else 3.0, r.height() - 10.0), 1.5, 1.5)

        pill_w = _sc(38, sc, 30)
        pill_h = _sc(22, sc, 18)
        pill = QRectF(r.left() + _sc(9, sc, 7), r.center().y() - pill_h / 2.0, pill_w, pill_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tag_bg)
        p.drawRoundedRect(pill, 4.0, 4.0)
        p.setFont(p5_font(_sc(11, sc, 9), italic=False))
        p.setPen(tag_fg)
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, tag)

        # Inline quick-action Edit & Delete buttons appear crisply on hover
        show_actions = hov and not bool(index.data(ROLE_FLAG))
        right_edge = r.right() - _sc(8, sc, 6)
        if show_actions:
            edit_r, del_r = self._action_rects(r)
            p.setPen(QPen(QColor("#6C5514"), 1.1))
            p.setBrush(P5_GOLD_BG)
            p.drawRoundedRect(edit_r, 5.0, 5.0)
            _draw_icon(p, "edit", edit_r.adjusted(2, 2, -2, -2), P5_GOLD_FG)

            p.setPen(QPen(QColor("#731823"), 1.1))
            p.setBrush(P5_RED_BG)
            p.drawRoundedRect(del_r, 5.0, 5.0)
            _draw_icon(p, "trash", del_r.adjusted(2, 2, -2, -2), P5_RED_FG)
            right_edge = edit_r.left() - _sc(8, sc, 6)

        # Right-aligned Rupiah amount
        af = p5_font(_sc(13, sc, 10), italic=False)
        p.setFont(af)
        amt_w = min(_sc(125, sc, 94), max(_sc(74, sc, 60), QFontMetrics(af).horizontalAdvance(amt_txt) + _sc(8, sc, 6)))
        amt_rect = QRectF(right_edge - amt_w, r.top(), amt_w, r.height())
        p.setPen(amt_fg)
        p.drawText(amt_rect, Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignRight.value, amt_txt)

        # Middle title + metadata
        tx = pill.right() + _sc(9, sc, 6)
        tw = max(40.0, amt_rect.left() - tx - _sc(8, sc, 6))
        tf = ui_font(_sc(13, sc, 10), bold=True)
        p.setFont(tf)
        p.setPen(WHITE)
        elided = QFontMetrics(tf).elidedText(title, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, r.top() + _sc(5, sc, 3), tw, _sc(21, sc, 16)),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, elided)

        mf = ui_font(_sc(11, sc, 9), bold=False)
        p.setFont(mf)
        p.setPen(P5_MUTED)
        el_meta = QFontMetrics(mf).elidedText(meta_txt, Qt.TextElideMode.ElideRight, int(tw))
        p.drawText(QRectF(tx, r.top() + _sc(25, sc, 20), tw, _sc(18, sc, 14)),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, el_meta)
        p.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        tx_id = index.data(ROLE_ID)
        if not tx_id or bool(index.data(ROLE_FLAG)):
            return False
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            r = QRectF(option.rect).adjusted(2, 2, -2, -2)
            edit_r, del_r = self._action_rects(r)
            pos = event.position()
            if edit_r.adjusted(-2, -2, 2, 2).contains(pos):
                play_ui("select")
                self.edit_clicked.emit(tx_id)
                return True
            if del_r.adjusted(-2, -2, 2, 2).contains(pos):
                play_ui("cancel")
                self.delete_clicked.emit(tx_id)
                return True
        return super().editorEvent(event, model, option, index)


class FinanceDialog(P5FluidPanel):
    """Persona 5 personal finance ledger inspired by bot-wa (local or live Google Sheets Monthly Budget)."""

    finance_changed = pyqtSignal(str, dict)          # event_kind ("add"|"edit"|"delete"|"bill"), payload
    open_settings = pyqtSignal()
    sheet_connect_requested = pyqtSignal()           # 1-click enable Finance Sheets when creds already configured
    sheet_refresh_requested = pyqtSignal(int, int)   # year, month
    sheet_write_requested = pyqtSignal(dict)         # tx payload (+ optional bill_id/bill_year/bill_month)
    sheet_edit_requested = pyqtSignal(dict, dict)    # old_tx, new_payload
    sheet_delete_requested = pyqtSignal(dict)        # tx
    sheet_undo_requested = pyqtSignal(str, dict)     # undo_kind ("sheet_add"|"sheet_delete"), payload

    _MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

    def __init__(
        self,
        store: FinanceStore,
        on_top: bool = True,
        ui_scale: float = 1.0,
        sheet_mode: bool = False,
        sheet_creds_ready: bool = False,
    ) -> None:
        super().__init__("FINANCE", QSize(890, 680), on_top=on_top, ui_scale=ui_scale)
        sc = self._ui_scale
        self.store = store
        today = date.today()
        self._view_year = today.year
        self._view_month = today.month
        self._active_tab = "tx"
        self._tx_type = "expense"
        self._selected_cat = "Jajan"
        self._selected_wallet = "Tunai"
        self._selected_to_wallet = "BNI"
        self._tx_filter = "all"
        self._editing_tx_id: str | None = None
        self._editing_sheet_tx: dict | None = None
        self._undo_kind = ""
        self._undo_payload: dict | None = None
        self._sheet_mode = bool(sheet_mode)
        self._sheet_creds_ready = bool(sheet_creds_ready)
        self._sheet_busy = False
        self._sheet_data: FinanceSheetMonthData | None = None
        self._sheet_cache: dict[tuple[int, int], FinanceSheetMonthData] = {}
        self._sheet_tx_by_id: dict[str, dict] = {}
        self._sheet_error = ""
        self._sheet_silent_refresh = False
        self._active_shortcut_name = ""
        self._budget_inputs: dict[str, QLineEdit] = {}
        self._budget_bars: dict[str, tuple[QLabel, QLabel, QProgressBar]] = {}
        self._wallet_rows: dict[str, tuple[QLabel, QProgressBar]] = {}

        # -- Header bar: Sheets status + Month navigation + Settings + Close -
        self._sheet_badge = QLabel("LOCAL")
        self._sheet_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sheet_badge.setFixedHeight(_sc(24, sc, 20))
        self.header_bar.addWidget(self._sheet_badge)

        self._sheet_action_btn = self._btn("CONNECT", variant="soft", display=True, px=9)
        self._sheet_action_btn.setFixedHeight(_sc(26, sc, 22))
        self._sheet_action_btn.clicked.connect(self._on_sheet_action_clicked)
        self.header_bar.addWidget(self._sheet_action_btn)

        prev_m = self._btn("◀", variant="soft", display=True, px=11)
        prev_m.setFixedSize(_sc(28, sc, 22), _sc(26, sc, 22))
        prev_m.setToolTip("Previous month")
        prev_m.clicked.connect(lambda _c=False: self._shift_month(-1))
        self.header_bar.addWidget(prev_m)

        self._month_lbl = QLabel("")
        self._month_lbl.setMinimumWidth(_sc(76, sc, 62))
        self._month_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._month_lbl.setStyleSheet(
            f'color: #FFE46B; font-family: "Barlow Condensed", "Anton", sans-serif; '
            f'font-size: {_sc(13, sc, 10)}px; font-weight: 800; letter-spacing: 0.7px;'
        )
        self.header_bar.addWidget(self._month_lbl)

        next_m = self._btn("▶", variant="soft", display=True, px=11)
        next_m.setFixedSize(_sc(28, sc, 22), _sc(26, sc, 22))
        next_m.setToolTip("Next month")
        next_m.clicked.connect(lambda _c=False: self._shift_month(+1))
        self.header_bar.addWidget(next_m)

        today_m = self._btn("TODAY", variant="gold", display=True, px=9)
        today_m.setFixedHeight(_sc(26, sc, 22))
        today_m.setToolTip("Jump to current month")
        today_m.clicked.connect(self._jump_this_month)
        self.header_bar.addWidget(today_m)

        set_btn = self._btn("", variant="soft", icon="gear")
        set_btn.setFixedSize(_sc(30, sc, 24), _sc(30, sc, 24))
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # -- Top Interactive KPI Summary Strip (clickable filters + update pulse) ---
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(_sc(8, sc, 6))

        self._kpi_bal_card = _P5KpiCard("TOTAL BALANCE", "#FFE46B", "#FAD02C", "#241E0F", "WALLETS ▸", scale=sc)
        self._kpi_inc_card = _P5KpiCard("MONTH INCOME", "#5CE09A", "#5CE09A", "#11241C", "FILTER IN ▸", scale=sc)
        self._kpi_exp_card = _P5KpiCard("MONTH EXPENSE", "#FF5C6C", "#E60012", "#281116", "FILTER OUT ▸", scale=sc)
        self._kpi_tod_card = _P5KpiCard("TODAY SPEND", "#FFFFFF", "#D8D8EC", "#1F1F2E", "TODAY ▸", scale=sc)
        self._kpi_bal_val, self._kpi_bal_sub = self._kpi_bal_card.val_lbl, self._kpi_bal_card.sub_lbl
        self._kpi_inc_val, self._kpi_inc_sub = self._kpi_inc_card.val_lbl, self._kpi_inc_card.sub_lbl
        self._kpi_exp_val, self._kpi_exp_sub = self._kpi_exp_card.val_lbl, self._kpi_exp_card.sub_lbl
        self._kpi_tod_val, self._kpi_tod_sub = self._kpi_tod_card.val_lbl, self._kpi_tod_card.sub_lbl

        self._kpi_bal_card.setToolTip("Click to view Wallet Balances & Monthly Budgets")
        self._kpi_inc_card.setToolTip("Click to filter Income transactions")
        self._kpi_exp_card.setToolTip("Click to filter Expense transactions")
        self._kpi_tod_card.setToolTip("Click to filter Today's transactions")
        self._kpi_bal_card.clicked.connect(lambda: self._on_kpi_clicked("bal"))
        self._kpi_inc_card.clicked.connect(lambda: self._on_kpi_clicked("inc"))
        self._kpi_exp_card.clicked.connect(lambda: self._on_kpi_clicked("exp"))
        self._kpi_tod_card.clicked.connect(lambda: self._on_kpi_clicked("tod"))

        kpi_row.addWidget(self._kpi_bal_card, 3)
        kpi_row.addWidget(self._kpi_inc_card, 2)
        kpi_row.addWidget(self._kpi_exp_card, 2)
        kpi_row.addWidget(self._kpi_tod_card, 2)
        self.body.addLayout(kpi_row)

        # -- Segmented Tab Bar: TRANSACTIONS | BUDGETS & BILLS ---------------
        tab_row = QHBoxLayout()
        tab_row.setSpacing(_sc(6, sc, 4))
        self._tab_tx_btn = self._btn("TRANSACTIONS", variant="soft", display=True, px=11)
        self._tab_tx_btn.setFixedHeight(_sc(28, sc, 24))
        self._tab_tx_btn.clicked.connect(lambda _c=False: self._set_tab("tx"))
        self._tab_plan_btn = self._btn("BUDGETS & BILLS", variant="soft", display=True, px=11)
        self._tab_plan_btn.setFixedHeight(_sc(28, sc, 24))
        self._tab_plan_btn.clicked.connect(lambda _c=False: self._set_tab("plan"))
        tab_row.addWidget(self._tab_tx_btn, 1)
        tab_row.addWidget(self._tab_plan_btn, 1)
        self.body.addLayout(tab_row)

        # ====================================================================
        # PAGE 1: TRANSACTIONS (Quick Record / Edit + Searchable History)
        # ====================================================================
        self._tx_page = QWidget()
        tx_cols = QHBoxLayout(self._tx_page)
        tx_cols.setContentsMargins(0, 0, 0, 0)
        tx_cols.setSpacing(_sc(10, sc, 7))

        # Left column: Record / Edit form card
        left_wrap = QVBoxLayout()
        left_wrap.setSpacing(_sc(7, sc, 5))

        self._form_card = _make_card()
        fl = QVBoxLayout(self._form_card)
        fl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        fl.setSpacing(_sc(6, sc, 4))

        fh = QHBoxLayout()
        self._form_hdr_lbl = QLabel("RECORD TRANSACTION")
        self._form_hdr_lbl.setObjectName("section")
        fh.addWidget(self._form_hdr_lbl)
        fh.addStretch(1)
        self._amt_preview_lbl = QLabel("")
        self._amt_preview_lbl.setStyleSheet(
            f'color: #FFE46B; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(12, sc, 10)}px; font-weight: 800;'
        )
        fh.addWidget(self._amt_preview_lbl)
        fl.addLayout(fh)

        # Semantic color-coded Transaction type pills (EXPENSE=Red / INCOME=Mint / TRANSFER=Gold)
        type_row = QHBoxLayout()
        type_row.setSpacing(_sc(5, sc, 4))
        self._type_btns: dict[str, P5FluidButton] = {}
        for tkey, tlbl in (("expense", "EXPENSE"), ("income", "INCOME"), ("transfer", "TRANSFER")):
            tb = self._btn(tlbl, variant="soft", display=True, px=10)
            tb.setFixedHeight(_sc(26, sc, 22))
            tb.clicked.connect(lambda _c=False, k=tkey: self._select_tx_type(k))
            self._type_btns[tkey] = tb
            type_row.addWidget(tb, 1)
        fl.addLayout(type_row)

        # Quick Shortcuts strip (bot-wa /pintasan — shown for Expense)
        self._sc_wrap = QWidget()
        sc_vl = QVBoxLayout(self._sc_wrap)
        sc_vl.setContentsMargins(0, 0, 0, 0)
        sc_vl.setSpacing(_sc(3, sc, 2))
        sc_title = QLabel("QUICK SHORTCUTS")
        sc_title.setStyleSheet(
            f'color: #A0A5B5; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(10, sc, 8)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        sc_vl.addWidget(sc_title)
        self._sc_grid = QGridLayout()
        self._sc_grid.setContentsMargins(0, 0, 0, 0)
        self._sc_grid.setSpacing(_sc(4, sc, 3))
        sc_vl.addLayout(self._sc_grid)
        fl.addWidget(self._sc_wrap)

        # Amount + Date row
        amt_row = QHBoxLayout()
        amt_row.setSpacing(_sc(6, sc, 4))
        self._amt_input = QLineEdit()
        self._amt_input.setPlaceholderText("Amount (e.g. 15rb, 1.5jt, 25000)")
        self._amt_input.textChanged.connect(self._update_amount_preview)
        self._amt_input.returnPressed.connect(self._submit_transaction)
        amt_row.addWidget(self._amt_input, 3)

        self._date_edit = P5DateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("dd/MM/yyyy")
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setMinimumWidth(_sc(110, sc, 92))
        self._date_edit.setFixedHeight(_sc(30, sc, 25))
        amt_row.addWidget(self._date_edit, 2)
        fl.addLayout(amt_row)

        # 1-Click Quick-Amount Accumulator Chips (+5rb .. +100rb & CLR)
        qamt_row = QHBoxLayout()
        qamt_row.setSpacing(_sc(4, sc, 3))
        for q_lbl, q_val in (("+5rb", 5_000), ("+10rb", 10_000), ("+20rb", 20_000), ("+50rb", 50_000), ("+100rb", 100_000)):
            qb = self._btn(q_lbl, variant="soft", display=True, px=9)
            qb.setFixedHeight(_sc(21, sc, 18))
            qb.setToolTip(f"Add {format_rupiah(q_val)} to amount")
            qb.clicked.connect(lambda _c=False, v=q_val: self._bump_amount(v))
            qamt_row.addWidget(qb, 1)
        clr_amt = self._btn("CLR", variant="ghost", display=True, px=9, sfx_click="cancel")
        clr_amt.setFixedHeight(_sc(21, sc, 18))
        clr_amt.setToolTip("Clear amount")
        clr_amt.clicked.connect(self._clear_amount)
        qamt_row.addWidget(clr_amt)
        fl.addLayout(qamt_row)

        # Description / Source input
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("Item or note (e.g. Kopi susu, Galon)")
        self._title_input.returnPressed.connect(self._submit_transaction)
        fl.addWidget(self._title_input)

        # Category chips (shown for Expense)
        self._cat_wrap = QWidget()
        cat_vl = QVBoxLayout(self._cat_wrap)
        cat_vl.setContentsMargins(0, 0, 0, 0)
        cat_vl.setSpacing(_sc(3, sc, 2))
        cat_hdr = QLabel("CATEGORY")
        cat_hdr.setStyleSheet(
            f'color: #A0A5B5; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(10, sc, 8)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        cat_vl.addWidget(cat_hdr)
        cat_grid = QGridLayout()
        cat_grid.setContentsMargins(0, 0, 0, 0)
        cat_grid.setSpacing(_sc(4, sc, 3))
        self._cat_btns: dict[str, P5FluidButton] = {}
        for idx, cat in enumerate(FINANCE_CATEGORIES):
            cb = self._btn(FINANCE_CATEGORY_SHORT[cat], variant="soft", display=True, px=10)
            cb.setFixedHeight(_sc(25, sc, 22))
            cb.setToolTip(cat)
            cb.clicked.connect(lambda _c=False, c=cat: self._select_category(c))
            self._cat_btns[cat] = cb
            cat_grid.addWidget(cb, idx // 2, idx % 2)
        cat_vl.addLayout(cat_grid)
        fl.addWidget(self._cat_wrap)

        # Source / Active Wallet chips (show live compact balance!)
        self._wallet_hdr = QLabel("WALLET (QRIS = BNI / MANDIRI)")
        self._wallet_hdr.setStyleSheet(
            f'color: #A0A5B5; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(10, sc, 8)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        fl.addWidget(self._wallet_hdr)
        w_row = QHBoxLayout()
        w_row.setSpacing(_sc(5, sc, 4))
        self._wallet_btns: dict[str, P5FluidButton] = {}
        for w in FINANCE_WALLETS:
            wb = self._btn(w.upper(), variant="soft", display=True, px=9)
            wb.setFixedHeight(_sc(26, sc, 22))
            wb.clicked.connect(lambda _c=False, wk=w: self._select_wallet(wk))
            self._wallet_btns[w] = wb
            w_row.addWidget(wb, 1)
        fl.addLayout(w_row)

        # Target wallet chips (shown ONLY for Transfer)
        self._to_wallet_wrap = QWidget()
        tw_vl = QVBoxLayout(self._to_wallet_wrap)
        tw_vl.setContentsMargins(0, 0, 0, 0)
        tw_vl.setSpacing(_sc(3, sc, 2))
        tw_hdr = QLabel("TO WALLET")
        tw_hdr.setStyleSheet(
            f'color: #A0A5B5; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(10, sc, 8)}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        tw_vl.addWidget(tw_hdr)
        tw_row = QHBoxLayout()
        tw_row.setSpacing(_sc(5, sc, 4))
        self._to_wallet_btns: dict[str, P5FluidButton] = {}
        for w in FINANCE_WALLETS:
            twb = self._btn(w.upper(), variant="soft", display=True, px=9)
            twb.setFixedHeight(_sc(26, sc, 22))
            twb.clicked.connect(lambda _c=False, wk=w: self._select_to_wallet(wk))
            self._to_wallet_btns[w] = twb
            tw_row.addWidget(twb, 1)
        tw_vl.addLayout(tw_row)
        fl.addWidget(self._to_wallet_wrap)

        # Primary CTA + Cancel Edit row
        cta_row = QHBoxLayout()
        cta_row.setSpacing(_sc(6, sc, 4))
        self._submit_btn = self._btn("RECORD EXPENSE", variant="primary", icon="plus", display=True, px=11)
        self._submit_btn.setFixedHeight(_sc(33, sc, 27))
        self._submit_btn.clicked.connect(self._submit_transaction)
        cta_row.addWidget(self._submit_btn, 1)

        self._cancel_edit_btn = self._btn("CANCEL", variant="soft", display=True, px=10, sfx_click="cancel")
        self._cancel_edit_btn.setFixedHeight(_sc(33, sc, 27))
        self._cancel_edit_btn.setToolTip("Cancel edit (Esc)")
        self._cancel_edit_btn.clicked.connect(self._cancel_edit)
        self._cancel_edit_btn.hide()
        cta_row.addWidget(self._cancel_edit_btn)
        fl.addLayout(cta_row)

        self._form_status = QLabel("")
        self._form_status.setObjectName("tip")
        self._form_status.setWordWrap(True)
        fl.addWidget(self._form_status)
        fl.addStretch(1)

        left_wrap.addWidget(self._form_card, 1)
        tx_cols.addLayout(left_wrap, 44)

        # Right column: Searchable Transaction History + Inline Hover Actions + Undo bar
        right_wrap = QVBoxLayout()
        right_wrap.setSpacing(_sc(6, sc, 4))

        hist_card = _make_card()
        hl = QVBoxLayout(hist_card)
        hl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        hl.setSpacing(_sc(5, sc, 4))

        hh = QHBoxLayout()
        hh.setSpacing(_sc(6, sc, 4))
        h_lbl = QLabel("TRANSACTIONS")
        h_lbl.setObjectName("section")
        hh.addWidget(h_lbl)
        hh.addStretch(1)
        self._tx_count_lbl = QLabel("")
        self._tx_count_lbl.setStyleSheet(
            f'color: #C2C6D8; font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {_sc(12, sc, 9)}px; font-weight: 800;'
        )
        hh.addWidget(self._tx_count_lbl)
        self._reset_flt_btn = self._btn("× RESET", variant="gold", display=True, px=9)
        self._reset_flt_btn.setFixedHeight(_sc(20, sc, 18))
        self._reset_flt_btn.setToolTip("Clear search & filter")
        self._reset_flt_btn.clicked.connect(self._reset_filters)
        self._reset_flt_btn.hide()
        hh.addWidget(self._reset_flt_btn)
        hl.addLayout(hh)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search transactions, category, wallet... (Ctrl+F)")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(lambda _t: self._refresh_tx_list())
        hl.addWidget(self._search_input)

        # 2 clean compact filter rows (4 chips each -> zero crowding at 85%..130%)
        self._filter_btns: dict[str, P5FluidButton] = {}
        f_grid = QGridLayout()
        f_grid.setContentsMargins(0, 0, 0, 0)
        f_grid.setSpacing(_sc(4, sc, 3))
        filters = (
            ("all", "ALL"), ("expense", "OUT"), ("income", "IN"), ("transfer", "TF"),
            ("cat:Jajan", "JAJAN"), ("cat:Alat", "ALAT"),
            ("cat:Mingguan&Bulanan", "MINGGUAN"), ("cat:Hiburan", "HIBURAN"),
        )
        for idx, (fkey, flbl) in enumerate(filters):
            fb = self._btn(flbl, variant="soft", display=True, px=9)
            fb.setFixedHeight(_sc(22, sc, 19))
            fb.clicked.connect(lambda _c=False, k=fkey: self._set_filter(k))
            self._filter_btns[fkey] = fb
            f_grid.addWidget(fb, idx // 4, idx % 4)
        self._filter_btns["all"].set_active(True)
        hl.addLayout(f_grid)

        self._tx_list = _KeyAwareList()
        self._tx_list.setMouseTracking(True)
        self._tx_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._tx_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tx_list.customContextMenuRequested.connect(self._open_tx_context_menu)
        self._tx_delegate = FinanceTxDelegate(self._tx_list, scale=sc)
        self._tx_delegate.edit_clicked.connect(self._start_edit_by_id)
        self._tx_delegate.delete_clicked.connect(self._delete_tx_by_id)
        self._tx_list.setItemDelegate(self._tx_delegate)
        self._tx_list.itemSelectionChanged.connect(self._sync_tx_selection_ui)
        self._tx_list.delete_pressed.connect(self._delete_selected_tx)
        self._tx_list.itemDoubleClicked.connect(lambda _it: self._start_edit_selected_tx())
        hl.addWidget(self._tx_list, 1)

        h_foot = QHBoxLayout()
        h_foot.setSpacing(_sc(6, sc, 4))
        self._tx_foot_tip = QLabel("Hover a row to edit/delete · Right-click for more")
        self._tx_foot_tip.setObjectName("tip")
        h_foot.addWidget(self._tx_foot_tip, 1)

        self._edit_tx_btn = self._btn("EDIT", variant="gold", display=True, px=10)
        self._edit_tx_btn.setFixedHeight(_sc(26, sc, 22))
        self._edit_tx_btn.clicked.connect(self._start_edit_selected_tx)
        h_foot.addWidget(self._edit_tx_btn)

        self._del_tx_btn = self._btn("DELETE", variant="danger", display=True, px=10)
        self._del_tx_btn.setFixedHeight(_sc(26, sc, 22))
        self._del_tx_btn.clicked.connect(self._delete_selected_tx)
        h_foot.addWidget(self._del_tx_btn)
        hl.addLayout(h_foot)

        right_wrap.addWidget(hist_card, 1)

        self._undo = UndoBar(scale=sc)
        self._undo.undo_clicked.connect(self._undo_last)
        right_wrap.addWidget(self._undo)
        tx_cols.addLayout(right_wrap, 56)

        self.body.addWidget(self._tx_page, 1)

        # ====================================================================
        # PAGE 2: BUDGETS, WALLETS, BILLS & SHORTCUTS
        # ====================================================================
        self._plan_page = QScrollArea()
        self._plan_page.setWidgetResizable(True)
        self._plan_page.setFrameShape(QFrame.Shape.NoFrame)
        self._plan_page.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        plan_body = QWidget()
        plan_body.setStyleSheet("background: transparent;")
        plan_cols = QHBoxLayout(plan_body)
        plan_cols.setContentsMargins(0, 0, _sc(4, sc, 2), 0)
        plan_cols.setSpacing(_sc(10, sc, 7))

        # Left side: Monthly Category Budgets + Wallet Balances
        pl_left = QVBoxLayout()
        pl_left.setSpacing(_sc(8, sc, 6))

        bud_card = _make_card()
        bl = QVBoxLayout(bud_card)
        bl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        bl.setSpacing(_sc(7, sc, 5))
        bh = QLabel("MONTHLY CATEGORY BUDGETS")
        bh.setObjectName("section")
        bl.addWidget(bh)

        for cat in FINANCE_CATEGORIES:
            box = QVBoxLayout()
            box.setSpacing(_sc(3, sc, 2))
            top = QHBoxLayout()
            name_lbl = QLabel(cat)
            name_lbl.setStyleSheet(f'color: #FFFFFF; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
            pct_lbl = QLabel("No limit")
            pct_lbl.setStyleSheet(
                f'color: #A0A5B5; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(11, sc, 9)}px; font-weight: 800;'
            )
            top.addWidget(name_lbl)
            top.addStretch(1)
            top.addWidget(pct_lbl)
            box.addLayout(top)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(_sc(7, sc, 5))
            box.addWidget(bar)

            edit_row = QHBoxLayout()
            edit_row.setSpacing(_sc(5, sc, 3))
            info_lbl = QLabel("Rp 0")
            info_lbl.setObjectName("tip")
            edit_row.addWidget(info_lbl, 1)

            inp = QLineEdit()
            inp.setPlaceholderText("Limit (e.g. 500rb)")
            inp.setFixedWidth(_sc(118, sc, 94))
            inp.setFixedHeight(_sc(25, sc, 22))
            inp.returnPressed.connect(lambda c=cat: self._save_budget(c))
            self._budget_inputs[cat] = inp
            edit_row.addWidget(inp)

            set_b = self._btn("SET", variant="gold", display=True, px=9)
            set_b.setFixedHeight(_sc(25, sc, 22))
            set_b.clicked.connect(lambda _c=False, c=cat: self._save_budget(c))
            edit_row.addWidget(set_b)

            clr_b = self._btn("×", variant="soft", display=True, px=10, sfx_click="cancel")
            clr_b.setFixedSize(_sc(25, sc, 22), _sc(25, sc, 22))
            clr_b.setToolTip(f"Clear {cat} budget limit")
            clr_b.clicked.connect(lambda _c=False, c=cat: self._clear_budget(c))
            edit_row.addWidget(clr_b)
            box.addLayout(edit_row)

            self._budget_bars[cat] = (info_lbl, pct_lbl, bar)
            bl.addLayout(box)

        pl_left.addWidget(bud_card)

        # Wallet Balances Card
        wal_card = _make_card()
        wl = QVBoxLayout(wal_card)
        wl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        wl.setSpacing(_sc(6, sc, 4))
        wh = QLabel("WALLET BALANCES")
        wh.setObjectName("section")
        wl.addWidget(wh)
        for w in FINANCE_WALLETS:
            wr = QHBoxLayout()
            w_name = QLabel(w)
            w_name.setStyleSheet(f'color: #FFFFFF; font-size: {_sc(12, sc, 10)}px; font-weight: bold;')
            w_val = QLabel("Rp 0")
            w_val.setStyleSheet(
                f'color: #FFE46B; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(14, sc, 11)}px; font-weight: 800;'
            )
            wr.addWidget(w_name)
            wr.addStretch(1)
            wr.addWidget(w_val)
            wl.addLayout(wr)
            wbar = QProgressBar()
            wbar.setRange(0, 100)
            wbar.setTextVisible(False)
            wbar.setFixedHeight(_sc(6, sc, 5))
            wbar.setStyleSheet(
                "QProgressBar { background: #1B1E28; border-radius: 3px; }"
                "QProgressBar::chunk { background: #F5C400; border-radius: 3px; }"
            )
            wl.addWidget(wbar)
            self._wallet_rows[w] = (w_val, wbar)
        pl_left.addWidget(wal_card)
        pl_left.addStretch(1)
        plan_cols.addLayout(pl_left, 48)

        # Right side: Recurring Bills + Quick Shortcuts Manager
        pl_right = QVBoxLayout()
        pl_right.setSpacing(_sc(8, sc, 6))

        bill_card = _make_card()
        bill_l = QVBoxLayout(bill_card)
        bill_l.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        bill_l.setSpacing(_sc(6, sc, 4))
        bill_hdr = QLabel("RECURRING MONTHLY BILLS")
        bill_hdr.setObjectName("section")
        bill_l.addWidget(bill_hdr)

        self._bills_box = QVBoxLayout()
        self._bills_box.setSpacing(_sc(4, sc, 3))
        bill_l.addLayout(self._bills_box)

        # Compact add-bill form
        b_add1 = QHBoxLayout()
        b_add1.setSpacing(_sc(5, sc, 3))
        self._bill_name_in = QLineEdit()
        self._bill_name_in.setPlaceholderText("Bill name (e.g. Kos, Wi-Fi)")
        self._bill_name_in.returnPressed.connect(self._add_bill)
        b_add1.addWidget(self._bill_name_in, 2)
        self._bill_amt_in = QLineEdit()
        self._bill_amt_in.setPlaceholderText("Amount (e.g. 500rb)")
        self._bill_amt_in.returnPressed.connect(self._add_bill)
        b_add1.addWidget(self._bill_amt_in, 1)
        bill_l.addLayout(b_add1)

        b_add2 = QHBoxLayout()
        b_add2.setSpacing(_sc(5, sc, 3))
        day_lbl = QLabel("Day")
        day_lbl.setObjectName("tip")
        b_add2.addWidget(day_lbl)
        self._bill_day_spin = P5SpinBox()
        self._bill_day_spin.setRange(1, 31)
        self._bill_day_spin.setValue(1)
        self._bill_day_spin.setFixedWidth(_sc(56, sc, 48))
        self._bill_day_spin.setFixedHeight(_sc(26, sc, 24))
        b_add2.addWidget(self._bill_day_spin)

        self._bill_cat_cb = P5ComboBox()
        self._bill_cat_cb.addItems(list(FINANCE_CATEGORIES))
        self._bill_cat_cb.setCurrentText("Mingguan&Bulanan")
        self._bill_cat_cb.setFixedHeight(_sc(26, sc, 24))
        b_add2.addWidget(self._bill_cat_cb, 1)

        self._bill_wal_cb = P5ComboBox()
        self._bill_wal_cb.addItems(list(FINANCE_WALLETS))
        self._bill_wal_cb.setCurrentText("BNI")
        self._bill_wal_cb.setFixedHeight(_sc(26, sc, 24))
        b_add2.addWidget(self._bill_wal_cb)

        add_bill_btn = self._btn("ADD BILL", variant="primary", icon="plus", display=True, px=9)
        add_bill_btn.setFixedHeight(_sc(26, sc, 24))
        add_bill_btn.clicked.connect(self._add_bill)
        b_add2.addWidget(add_bill_btn)
        bill_l.addLayout(b_add2)
        pl_right.addWidget(bill_card)

        # Quick Shortcuts Manager Card
        sc_card = _make_card()
        scl = QVBoxLayout(sc_card)
        scl.setContentsMargins(_sc(10, sc, 8), _sc(8, sc, 6), _sc(10, sc, 8), _sc(8, sc, 6))
        scl.setSpacing(_sc(6, sc, 4))
        sch = QLabel("QUICK SHORTCUTS (PINTASAN)")
        sch.setObjectName("section")
        scl.addWidget(sch)

        self._sc_mgr_box = QVBoxLayout()
        self._sc_mgr_box.setSpacing(_sc(4, sc, 3))
        scl.addLayout(self._sc_mgr_box)

        sc_add = QHBoxLayout()
        sc_add.setSpacing(_sc(5, sc, 3))
        self._sc_name_in = QLineEdit()
        self._sc_name_in.setPlaceholderText("Name (e.g. Parkir)")
        self._sc_name_in.returnPressed.connect(self._add_shortcut)
        sc_add.addWidget(self._sc_name_in, 2)
        self._sc_amt_in = QLineEdit()
        self._sc_amt_in.setPlaceholderText("Price (opt.)")
        self._sc_amt_in.setFixedWidth(_sc(92, sc, 74))
        self._sc_amt_in.returnPressed.connect(self._add_shortcut)
        sc_add.addWidget(self._sc_amt_in)
        self._sc_cat_cb = P5ComboBox()
        self._sc_cat_cb.addItems(list(FINANCE_CATEGORIES))
        self._sc_cat_cb.setFixedHeight(_sc(26, sc, 24))
        sc_add.addWidget(self._sc_cat_cb, 1)
        add_sc_btn = self._btn("ADD", variant="gold", display=True, px=9)
        add_sc_btn.setFixedHeight(_sc(26, sc, 24))
        add_sc_btn.clicked.connect(self._add_shortcut)
        sc_add.addWidget(add_sc_btn)
        scl.addLayout(sc_add)

        self._plan_status = QLabel("")
        self._plan_status.setObjectName("tip")
        scl.addWidget(self._plan_status)
        pl_right.addWidget(sc_card)
        pl_right.addStretch(1)
        plan_cols.addLayout(pl_right, 52)

        self._plan_page.setWidget(plan_body)
        self.body.addWidget(self._plan_page, 1)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_search)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())
        self._select_tx_type("expense")
        self._select_category("Jajan")
        self._select_wallet("Tunai")
        self._select_to_wallet("BNI")
        self._set_tab("tx")
        self._update_sheet_badge()
        self.refresh()

    # -- Google Sheets Mode State & Callbacks --------------------------------
    def is_sheet_mode(self) -> bool:
        return self._sheet_mode

    def is_sheet_busy(self) -> bool:
        return self._sheet_busy

    def view_period(self) -> tuple[int, int]:
        return self._view_year, self._view_month

    def set_sheets_credentials_ready(self, ready: bool) -> None:
        self._sheet_creds_ready = bool(ready)
        self._update_sheet_badge()

    def set_sheet_mode(self, enabled: bool) -> None:
        want = bool(enabled)
        if want == self._sheet_mode:
            self._update_sheet_badge()
            return
        if self._editing_tx_id is not None:
            self._cancel_edit()
        self._sheet_mode = want
        self._sheet_busy = False
        self._sheet_error = ""
        self._undo_kind = ""
        self._undo_payload = None
        cached = self._sheet_cache.get((self._view_year, self._view_month)) if want else None
        self._sheet_data = cached
        self._sheet_tx_by_id = {t["id"]: t for t in cached.transactions} if cached else {}
        self._update_sheet_badge()
        self.refresh()
        if self._sheet_mode and self.isVisible():
            self.request_sheet_refresh(silent=bool(cached))

    def set_sheet_busy(self, busy: bool, status_text: str = "") -> None:
        self._sheet_busy = bool(busy)
        self._submit_btn.setEnabled(not self._sheet_busy)
        self._sheet_action_btn.setEnabled((not self._sheet_mode) or (not self._sheet_busy))
        if status_text:
            self._form_status.setText(status_text)
        self._update_sheet_badge()

    def request_sheet_refresh(self, silent: bool = False) -> None:
        if not self._sheet_mode:
            return
        self._sheet_error = ""
        self._sheet_silent_refresh = bool(silent)
        if silent and self._sheet_data is not None:
            # Keep current numbers & form fully usable during background sync!
            self._sheet_action_btn.setEnabled(False)
            self._sheet_badge.setText("SYNCING...")
        else:
            self.set_sheet_busy(True)
            if self.isVisible():
                self.notify(f"Syncing '{month_tab_name(self._view_month)}' from Sheets...", tone="info", ms=1500)
        self.sheet_refresh_requested.emit(self._view_year, self._view_month)

    def apply_sheet_month(self, data: FinanceSheetMonthData) -> None:
        if data.ok:
            self._sheet_cache[(data.year, data.month)] = data
        if not self._sheet_mode:
            return
        if (data.year, data.month) != (self._view_year, self._view_month):
            return
        was_silent = self._sheet_silent_refresh
        self._sheet_silent_refresh = False
        self._sheet_busy = False
        self._submit_btn.setEnabled(True)
        self._sheet_action_btn.setEnabled(True)
        if data.ok:
            self._sheet_data = data
            self._sheet_error = ""
            self._sheet_tx_by_id = {t["id"]: t for t in data.transactions}
            if not was_silent and self.isVisible():
                self._pulse_kpis("bal", "inc", "exp", "tod")
                self.notify(f"Synced '{data.tab}' · {len(data.transactions)} entries", tone="ok")
        else:
            # Keep cached data visible if transient network hiccup; only clear if no cache for this month
            cached = self._sheet_cache.get((self._view_year, self._view_month))
            self._sheet_data = cached
            self._sheet_tx_by_id = {t["id"]: t for t in cached.transactions} if cached else {}
            self._sheet_error = data.error or "Failed to load Google Sheets."
            self._form_status.setText(self._sheet_error)
            if self.isVisible():
                self.notify(self._sheet_error[:72], tone="err", ms=3200)
        self._update_sheet_badge()
        self.refresh()

    def on_sheet_op_done(
        self,
        ok: bool,
        action: str,
        message: str,
        undo_kind: str = "",
        undo_payload: dict | None = None,
    ) -> None:
        self._sheet_busy = False
        self._submit_btn.setEnabled(True)
        self._sheet_action_btn.setEnabled(True)
        self._update_sheet_badge()
        if not ok:
            err_msg = message or ("Google Sheets bill write failed." if action == "bill" else "Google Sheets write failed.")
            if action == "bill":
                self._plan_status.setText(err_msg)
            else:
                self._form_status.setText(err_msg)
            self.notify(err_msg[:76], tone="err", ms=3200)
            return
        if action in ("add", "edit", "bill", "undo"):
            play_ui("select")
        elif action == "delete":
            play_ui("close")
        self._active_shortcut_name = ""
        if action == "edit":
            self._cancel_edit()
            self._form_status.setText(message)
        elif action == "add":
            self._amt_input.clear()
            self._title_input.clear()
            self._form_status.setText(message)
            self._update_submit_cta()
        elif action == "bill":
            self._plan_status.setText(message)
        else:
            self._form_status.setText(message)
        self._pulse_kpis("bal", "inc", "exp", "tod")
        self.notify(message or "Synced to Google Sheets!", tone="gold" if action == "edit" else "ok")
        if undo_kind and undo_payload:
            self._undo_kind = undo_kind
            self._undo_payload = undo_payload
            self._undo.trigger(message[:38])

    def _on_sheet_action_clicked(self) -> None:
        if not self._sheet_mode:
            if self._sheet_creds_ready:
                self.notify("Switching to live Google Sheets sync...", tone="info", ms=1800)
                self.sheet_connect_requested.emit()
            else:
                self.open_settings.emit()
            return
        if not self._sheet_busy:
            self.request_sheet_refresh(silent=False)

    def _update_sheet_badge(self) -> None:
        sc = self._ui_scale
        pad_x = _sc(8, sc, 6)
        px = _sc(10, sc, 8)
        base_css = (
            f'border-radius: 4px; padding: 2px {pad_x}px; '
            f'font-family: "Barlow Condensed", sans-serif; '
            f'font-size: {px}px; font-weight: 800; letter-spacing: 0.6px;'
        )
        if not self._sheet_mode:
            self._sheet_badge.setText("LOCAL")
            self._sheet_badge.setToolTip("Local finance.json storage. Sync with your bot-wa Google Sheet anytime.")
            self._sheet_badge.setStyleSheet(
                f"background: #222634; color: #9FA6BA; border: 1px solid #353B4E; {base_css}"
            )
            if self._sheet_creds_ready:
                self._sheet_action_btn.setText("USE SHEET")
                self._sheet_action_btn.set_variant("gold")
                self._sheet_action_btn.setToolTip("Google Sheets is already configured — click to sync Finance live!")
            else:
                self._sheet_action_btn.setText("CONNECT")
                self._sheet_action_btn.set_variant("soft")
                self._sheet_action_btn.setToolTip("Open Settings to connect Google Sheets (bot-wa)")
            self._sheet_action_btn.setEnabled(True)
            return

        tab_name = (self._sheet_data.tab if self._sheet_data and self._sheet_data.tab else month_tab_name(self._view_month))
        if self._sheet_busy:
            self._sheet_badge.setText("SYNCING...")
            self._sheet_badge.setToolTip(f"Syncing tab '{tab_name}' with Google Sheets...")
            self._sheet_badge.setStyleSheet(
                f"background: #332910; color: #FAD02C; border: 1px solid #6E5818; {base_css}"
            )
            self._sheet_action_btn.setText("SYNC")
            self._sheet_action_btn.set_variant("soft")
            self._sheet_action_btn.setEnabled(False)
        elif self._sheet_error:
            self._sheet_badge.setText("SHEET ERR")
            self._sheet_badge.setToolTip(self._sheet_error)
            self._sheet_badge.setStyleSheet(
                f"background: #3A1218; color: #FF6B78; border: 1px solid #7A2330; {base_css}"
            )
            self._sheet_action_btn.setText("RETRY")
            self._sheet_action_btn.set_variant("gold")
            self._sheet_action_btn.setToolTip("Retry Google Sheets sync")
            self._sheet_action_btn.setEnabled(True)
        else:
            sync_note = f" · Synced {self._sheet_data.synced_at}" if (self._sheet_data and self._sheet_data.synced_at) else ""
            self._sheet_badge.setText(f"SHEET · {tab_name[:3].upper()}")
            self._sheet_badge.setToolTip(f"Connected to Google Sheets tab '{tab_name}' (bot-wa format){sync_note}")
            self._sheet_badge.setStyleSheet(
                f"background: #123322; color: #5CE09A; border: 1px solid #246845; {base_css}"
            )
            self._sheet_action_btn.setText("SYNC")
            self._sheet_action_btn.set_variant("soft")
            self._sheet_action_btn.setToolTip(f"Refresh '{tab_name}' from Google Sheets{sync_note}")
            self._sheet_action_btn.setEnabled(True)

    # -- Navigation, KPIs & Tab Switching ------------------------------------
    @staticmethod
    def _short_rp(v: int) -> str:
        sign = "-" if v < 0 else ""
        a = abs(int(v))
        if a >= 1_000_000:
            fmt = f"{a / 1_000_000:.1f}" if a >= 10_000_000 else f"{a / 1_000_000:.2f}"
            s = fmt.rstrip("0").rstrip(".")
            return f"{sign}{s}jt"
        if a >= 1_000:
            return f"{sign}{a / 1_000:.0f}rb"
        return f"{sign}{a}"

    def _pulse_kpis(self, *which: str) -> None:
        cards = {
            "bal": self._kpi_bal_card,
            "inc": self._kpi_inc_card,
            "exp": self._kpi_exp_card,
            "tod": self._kpi_tod_card,
        }
        for key in which:
            card = cards.get(key)
            if card is not None:
                card.pulse()

    def _sync_kpi_active_states(self) -> None:
        on_tx = self._active_tab == "tx"
        self._kpi_bal_card.set_active(self._active_tab == "plan")
        self._kpi_inc_card.set_active(on_tx and self._tx_filter == "income")
        self._kpi_exp_card.set_active(on_tx and self._tx_filter == "expense")
        self._kpi_tod_card.set_active(on_tx and self._tx_filter == "today")

    def _on_kpi_clicked(self, which: str) -> None:
        if which == "bal":
            nxt = "tx" if self._active_tab == "plan" else "plan"
            self._set_tab(nxt)
            self.notify(
                "Viewing Wallet Balances & Monthly Budgets" if nxt == "plan" else "Back to Transactions",
                tone="gold" if nxt == "plan" else "info",
                ms=1800,
            )
            return

        if self._active_tab != "tx":
            self._set_tab("tx")

        if which == "tod":
            today = date.today()
            if (self._view_year, self._view_month) != (today.year, today.month):
                self._activate_month_view(today.year, today.month)
            nxt_flt = "all" if self._tx_filter == "today" else "today"
            self._set_filter(nxt_flt)
            self.notify(
                "Filtering: Today's transactions" if nxt_flt == "today" else "Filter cleared: All entries",
                tone="info",
                ms=1800,
            )
            return

        target = "income" if which == "inc" else "expense"
        nxt_flt = "all" if self._tx_filter == target else target
        self._set_filter(nxt_flt)
        label = "Income" if target == "income" else "Expense"
        self.notify(
            f"Filtering: {label} entries" if nxt_flt == target else "Filter cleared: All entries",
            tone="ok" if nxt_flt == "income" else "info",
            ms=1800,
        )

    def _focus_search(self) -> None:
        if self._active_tab != "tx":
            self._set_tab("tx")
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _reset_filters(self) -> None:
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._set_filter("all")
        self.notify("Search & filters cleared", tone="info", ms=1500)

    def on_shown(self) -> None:
        self._update_sheet_badge()
        self.refresh()
        if self._sheet_mode:
            self.request_sheet_refresh(silent=bool(self._sheet_data))
        if self._active_tab == "tx":
            QTimer.singleShot(20, self._amt_input.setFocus)

    def on_closing(self) -> None:
        self._tx_delegate._ticker.stop()
        self._tx_delegate._flashes.clear()
        if self._editing_tx_id is not None:
            self._cancel_edit()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape and self._editing_tx_id is not None:
            self._cancel_edit()
            self.notify("Edit discarded", tone="info", ms=1500)
            e.accept()
            return
        super().keyPressEvent(e)

    def _set_tab(self, tab: str) -> None:
        self._active_tab = tab
        self._tx_page.setVisible(tab == "tx")
        self._plan_page.setVisible(tab == "plan")
        self._tab_tx_btn.set_active(tab == "tx")
        self._tab_plan_btn.set_active(tab == "plan")
        self._sync_kpi_active_states()

    def _activate_month_view(self, year: int, month: int) -> None:
        self._view_year, self._view_month = int(year), int(month)
        if self._sheet_mode:
            cached = self._sheet_cache.get((self._view_year, self._view_month))
            self._sheet_data = cached
            self._sheet_tx_by_id = {t["id"]: t for t in cached.transactions} if cached else {}
            self._sheet_error = ""
            self.refresh()
            self.request_sheet_refresh(silent=bool(cached))
        else:
            self.refresh()

    def _shift_month(self, delta: int) -> None:
        m = self._view_month + delta
        y = self._view_year
        if m < 1:
            m = 12
            y -= 1
        elif m > 12:
            m = 1
            y += 1
        self._activate_month_view(y, m)
        self._pulse_kpis("bal", "inc", "exp")

    def _jump_this_month(self) -> None:
        today = date.today()
        changed = (self._view_year, self._view_month) != (today.year, today.month)
        self._activate_month_view(today.year, today.month)
        if changed:
            self.notify(f"Jumped to {self._MONTH_NAMES[today.month - 1]} {today.year}", tone="gold", ms=1600)
            self._pulse_kpis("bal", "inc", "exp", "tod")

    # -- Form Controls -------------------------------------------------------
    def _bump_amount(self, delta: int) -> None:
        cur = parse_rupiah(self._amt_input.text()) or 0
        nxt = cur + int(delta)
        self._amt_input.setText(str(nxt))
        self._amt_input.setFocus()
        self._amt_input.setCursorPosition(len(self._amt_input.text()))
        self._submit_btn.pulse()

    def _clear_amount(self) -> None:
        self._amt_input.clear()
        self._amt_input.setFocus()

    def _update_submit_cta(self) -> None:
        amt = parse_rupiah(self._amt_input.text())
        if self._editing_tx_id is not None:
            base = "SAVE EDIT"
            variant = "gold"
        else:
            base = {"expense": "RECORD EXPENSE", "income": "RECORD INCOME", "transfer": "RECORD TRANSFER"}[self._tx_type]
            variant = {"expense": "primary", "income": "mint", "transfer": "gold"}[self._tx_type]
        self._submit_btn.set_variant(variant)
        self._submit_btn.setText(f"{base} · {format_rupiah(amt)}" if amt else base)

    def _select_tx_type(self, kind: str) -> None:
        self._tx_type = kind
        mode_var = {"expense": "primary", "income": "mint", "transfer": "gold"}
        for k, btn in self._type_btns.items():
            btn.set_active(k == kind, mode_var[k])
        is_exp = kind == "expense"
        is_tf = kind == "transfer"
        self._sc_wrap.setVisible(is_exp and bool(self.store.shortcuts()))
        self._cat_wrap.setVisible(is_exp)
        self._to_wallet_wrap.setVisible(is_tf)
        if is_tf:
            self._wallet_hdr.setText("FROM WALLET")
            if self._selected_to_wallet == self._selected_wallet:
                alt = next((w for w in FINANCE_WALLETS if w != self._selected_wallet), "BNI")
                self._select_to_wallet(alt)
            self._title_input.setPlaceholderText("Note (optional, e.g. Tarik ATM)")
        elif kind == "income":
            self._wallet_hdr.setText("TO WALLET")
            self._title_input.setPlaceholderText("Source (e.g. Uang saku, Gaji, Bonus)")
        else:
            self._wallet_hdr.setText("WALLET (QRIS = BNI / MANDIRI)")
            self._title_input.setPlaceholderText("Item or note (e.g. Kopi susu, Galon)")
        if self._editing_tx_id is None:
            if kind == "expense":
                self._form_card.setStyleSheet("")
            else:
                accent = "#2B684E" if kind == "income" else "#7A6118"
                self._form_card.setStyleSheet(
                    "QFrame#card { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #171722, stop:1 #111118); "
                    f"border: 1.3px solid {accent}; border-radius: 9px; }}"
                )
        self._update_submit_cta()

    def _select_category(self, cat: str) -> None:
        self._selected_cat = cat
        for c, btn in self._cat_btns.items():
            btn.set_active(c == cat)

    def _select_wallet(self, wallet: str) -> None:
        self._selected_wallet = wallet
        for w, btn in self._wallet_btns.items():
            btn.set_active(w == wallet)
        if self._tx_type == "transfer" and self._selected_to_wallet == wallet:
            alt = next((x for x in FINANCE_WALLETS if x != wallet), "BNI")
            self._select_to_wallet(alt)

    def _select_to_wallet(self, wallet: str) -> None:
        self._selected_to_wallet = wallet
        for w, btn in self._to_wallet_btns.items():
            btn.set_active(w == wallet, "gold")

    def _update_amount_preview(self, text: str) -> None:
        raw = text.strip()
        if not raw:
            self._amt_preview_lbl.setText("")
            self._update_submit_cta()
            return
        amt = parse_rupiah(raw)
        if amt is None:
            self._amt_preview_lbl.setStyleSheet(
                f'color: #FF5C6C; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(12, self._ui_scale, 10)}px; font-weight: 800;'
            )
            self._amt_preview_lbl.setText("INVALID AMOUNT")
        else:
            self._amt_preview_lbl.setStyleSheet(
                f'color: #FFE46B; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(12, self._ui_scale, 10)}px; font-weight: 800;'
            )
            self._amt_preview_lbl.setText(f"= {format_rupiah(amt)}")
        self._update_submit_cta()

    def _apply_shortcut(self, sc_entry: dict) -> None:
        if self._editing_tx_id is not None:
            self._cancel_edit()
        self._active_shortcut_name = sc_entry["name"]
        self._select_tx_type("expense")
        self._select_category(sc_entry["category"])
        self._title_input.setText(sc_entry["name"])
        self._refresh_shortcut_chips()
        if sc_entry.get("amount"):
            self._amt_input.setText(str(sc_entry["amount"]))
            self._amt_input.setFocus()
            self._amt_input.selectAll()
            self._submit_btn.pulse()
            self._form_status.setText(f"Shortcut '{sc_entry['name']}' ready — press Enter or click Record!")
            self.notify(f"Loaded '{sc_entry['name']}' · {format_rupiah(sc_entry['amount'])} — press Enter!", tone="gold", ms=2000)
        else:
            self._amt_input.clear()
            self._amt_input.setFocus()
            self._form_status.setText(f"Shortcut '{sc_entry['name']}' selected — type amount!")
            self.notify(f"Loaded '{sc_entry['name']}' — enter amount", tone="info", ms=1800)

    def _set_filter(self, key: str) -> None:
        self._tx_filter = key
        for k, btn in self._filter_btns.items():
            btn.set_active(k == key)
        self._sync_kpi_active_states()
        self._refresh_tx_list()

    # -- Transaction CRUD + Inline Edit + Duplicate + Context Menu + Undo ----
    def _submit_transaction(self) -> None:
        if self._sheet_mode and self._sheet_busy:
            self.notify("Still syncing with Google Sheets...", tone="info", ms=1400)
            return
        amt = parse_rupiah(self._amt_input.text())
        if amt is None:
            msg = "Enter a valid amount (e.g. 15rb, 1.5jt, or 25000)."
            self._form_status.setText(msg)
            self.nudge_input(self._amt_input, msg, tone="warn")
            return
        if self._tx_type == "transfer" and self._selected_wallet == self._selected_to_wallet:
            play_ui("cancel")
            msg = "Choose two different wallets for a transfer."
            self._form_status.setText(msg)
            self.notify(msg, tone="warn")
            return
        title = self._title_input.text().strip()
        qd = self._date_edit.date()
        day_s = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"

        if self._sheet_mode:
            # Immediate bot-wa formula guard before sending any network request
            if self._tx_type == "income":
                allowed = self._selected_wallet if self._selected_wallet in ("BNI", "Mandiri") else None
                bad = forbidden_bank_word(title, allowed_bank=allowed)
                if bad:
                    msg = (
                        f"Cannot include '{bad}' in a {allowed} deposit note (protects Sheet bank formulas)."
                        if allowed else
                        f"Cash income note cannot include '{bad}' (Sheets would count it as bank income)."
                    )
                    self._form_status.setText(msg)
                    self.nudge_input(self._title_input, msg, tone="warn")
                    return
            elif self._tx_type == "transfer":
                bad = forbidden_bank_word(title, allowed_bank=None)
                if bad:
                    msg = f"Transfer note cannot include '{bad}' (protects Sheet bank formulas)."
                    self._form_status.setText(msg)
                    self.nudge_input(self._title_input, msg, tone="warn")
                    return

            payload = {
                "type": self._tx_type,
                "amount": amt,
                "title": title,
                "category": self._selected_cat,
                "wallet": self._selected_wallet,
                "to_wallet": self._selected_to_wallet,
                "date": day_s,
            }
            self._view_year, self._view_month = qd.year(), qd.month()
            if self._editing_tx_id is not None and self._editing_sheet_tx is not None:
                self.set_sheet_busy(True, "Updating row in Google Sheets...")
                self.notify("Updating row in Google Sheets...", tone="info", ms=1500)
                self.sheet_edit_requested.emit(dict(self._editing_sheet_tx), payload)
            else:
                self.set_sheet_busy(True, f"Writing to Google Sheets ({month_tab_name(qd.month())})...")
                self.notify(f"Writing to Sheets ({month_tab_name(qd.month())})...", tone="info", ms=1500)
                self.sheet_write_requested.emit(payload)
            return

        if self._editing_tx_id is not None:
            tx_id = self._editing_tx_id
            updated, prev = self.store.update_transaction(
                tx_id, self._tx_type, amt, title,
                category=self._selected_cat,
                wallet=self._selected_wallet,
                to_wallet=self._selected_to_wallet,
                day=day_s,
            )
            self._cancel_edit()
            if updated is not None and prev is not None:
                play_ui("select")
                self._active_shortcut_name = ""
                self._undo_kind = "tx_edit"
                self._undo_payload = prev
                self._undo.trigger(f"Updated '{updated['title'][:26]}'")
                self._form_status.setText(f"Saved edit: {updated['title']} ({format_rupiah(updated['amount'])})")
                self.refresh()
                self._tx_delegate.flash_item(updated["id"])
                self._pulse_kpis("bal", "inc", "exp", "tod")
                self.notify(f"Saved edit: {updated['title']} · {format_rupiah(updated['amount'])}", tone="gold")
                self.finance_changed.emit("edit", updated)
            return

        tx = self.store.add_transaction(
            self._tx_type, amt, title,
            category=self._selected_cat,
            wallet=self._selected_wallet,
            to_wallet=self._selected_to_wallet,
            day=day_s,
        )
        if tx is None:
            self.nudge_input(self._amt_input, "Could not save transaction. Check inputs.", tone="err")
            self._form_status.setText("Could not save transaction. Check inputs.")
            return
        play_ui("select")
        self._active_shortcut_name = ""
        self._amt_input.clear()
        self._title_input.clear()
        self._view_year, self._view_month = qd.year(), qd.month()
        self._form_status.setText(f"Recorded {tx['title']} · {format_rupiah(tx['amount'])}")
        self.refresh()
        self._tx_delegate.flash_item(tx["id"])
        if tx["type"] == "income":
            self._pulse_kpis("bal", "inc")
            self.notify(f"+{format_rupiah(tx['amount'])} · {tx['title']} ({tx['wallet']})", tone="ok")
        elif tx["type"] == "transfer":
            self._pulse_kpis("bal")
            self.notify(f"Transferred {format_rupiah(tx['amount'])} · {tx['wallet']} → {tx['to_wallet']}", tone="gold")
        else:
            self._pulse_kpis("bal", "exp", "tod")
            self.notify(f"-{format_rupiah(tx['amount'])} · {tx['title']} ({tx['category']})", tone="ok")
        self._amt_input.setFocus()
        self.finance_changed.emit("add", tx)

    def _select_tx_by_id(self, tx_id: str) -> None:
        for i in range(self._tx_list.count()):
            it = self._tx_list.item(i)
            if it is not None and it.data(ROLE_ID) == tx_id:
                self._tx_list.setCurrentRow(i)
                return

    def _start_edit_selected_tx(self) -> None:
        item = self._tx_list.currentItem()
        if item is None:
            self.notify("Select a transaction to edit first", tone="info", ms=1800)
            return
        tx_id = item.data(ROLE_ID)
        if tx_id:
            self._start_edit_by_id(str(tx_id))

    def _start_edit_by_id(self, tx_id: str) -> None:
        if self._sheet_mode and self._sheet_busy:
            self.notify("Wait for Google Sheets sync to finish", tone="info", ms=1500)
            return
        tx = self._sheet_tx_by_id.get(tx_id) if self._sheet_mode else self.store.get_transaction(tx_id)
        if tx is None:
            return
        if self._sheet_mode and tx.get("sheet_kind") == "qris_pool":
            msg = "QRIS monthly pool rows update automatically from QRIS expenses."
            self._form_status.setText(msg)
            self.notify(msg, tone="info")
            return
        self._select_tx_by_id(tx_id)
        self._editing_tx_id = tx_id
        self._editing_sheet_tx = dict(tx) if self._sheet_mode else None
        self._select_tx_type(tx["type"])
        if tx["type"] == "expense" and tx.get("category"):
            self._select_category(tx["category"])
        self._select_wallet(tx["wallet"] if tx.get("wallet") in FINANCE_WALLETS else "Tunai")
        if tx["type"] == "transfer" and tx.get("to_wallet") in FINANCE_WALLETS:
            self._select_to_wallet(tx["to_wallet"])
        self._amt_input.setText(str(tx["amount"]))
        self._title_input.setText(str(tx.get("edit_note", tx["title"])))
        try:
            d = date.fromisoformat(tx["date"])
            self._date_edit.setDate(QDate(d.year, d.month, d.day))
        except ValueError:
            pass
        self._form_hdr_lbl.setText("EDIT TRANSACTION")
        self._form_hdr_lbl.setStyleSheet("color: #FAD02C;")
        self._form_card.setStyleSheet(
            "QFrame#card { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1D1A12, stop:1 #13120F); "
            "border: 1.5px solid #FAD02C; border-radius: 9px; }"
        )
        self._update_submit_cta()
        self._submit_btn.pulse()
        self._cancel_edit_btn.show()
        self._form_status.setText("Editing entry — press Esc or Cancel to discard.")
        self.notify(f"Editing '{tx['title']}' — press Enter to save or Esc to cancel", tone="gold", ms=2000)
        self._amt_input.setFocus()
        self._amt_input.selectAll()

    def _duplicate_tx(self, tx_id: str) -> None:
        if self._editing_tx_id is not None:
            self._cancel_edit()
        tx = self._sheet_tx_by_id.get(tx_id) if self._sheet_mode else self.store.get_transaction(tx_id)
        if tx is None or tx.get("sheet_kind") == "qris_pool":
            return
        self._select_tx_type(tx["type"])
        if tx["type"] == "expense" and tx.get("category"):
            self._select_category(tx["category"])
        self._select_wallet(tx["wallet"] if tx.get("wallet") in FINANCE_WALLETS else "Tunai")
        if tx["type"] == "transfer" and tx.get("to_wallet") in FINANCE_WALLETS:
            self._select_to_wallet(tx["to_wallet"])
        self._amt_input.setText(str(tx["amount"]))
        self._title_input.setText(str(tx.get("edit_note", tx["title"])))
        self._date_edit.setDate(QDate.currentDate())
        self._submit_btn.pulse()
        self._form_status.setText(f"Duplicated '{tx['title']}' for today — hit Enter to save!")
        self.notify(f"Duplicated '{tx['title']}' into form — press Enter to save!", tone="gold", ms=2200)
        self._amt_input.setFocus()
        self._amt_input.selectAll()

    def _cancel_edit(self) -> None:
        self._editing_tx_id = None
        self._editing_sheet_tx = None
        self._form_hdr_lbl.setText("RECORD TRANSACTION")
        self._form_hdr_lbl.setStyleSheet("")
        self._cancel_edit_btn.hide()
        self._amt_input.clear()
        self._title_input.clear()
        self._date_edit.setDate(QDate.currentDate())
        self._form_status.setText("")
        self._select_tx_type(self._tx_type)

    def _delete_selected_tx(self) -> None:
        item = self._tx_list.currentItem()
        if item is None:
            self.notify("Select a transaction to delete first", tone="info", ms=1800)
            return
        tx_id = item.data(ROLE_ID)
        if tx_id:
            self._delete_tx_by_id(str(tx_id))

    def _delete_tx_by_id(self, tx_id: str) -> None:
        if self._sheet_mode and self._sheet_busy:
            self.notify("Wait for Google Sheets sync to finish", tone="info", ms=1500)
            return
        if self._editing_tx_id == tx_id:
            self._cancel_edit()
        if self._sheet_mode:
            tx = self._sheet_tx_by_id.get(tx_id)
            if tx is None:
                return
            if tx.get("sheet_kind") == "qris_pool":
                msg = "QRIS monthly pool rows update automatically — undo individual QRIS expenses instead."
                self._form_status.setText(msg)
                self.notify(msg, tone="info")
                return
            self.set_sheet_busy(True, "Clearing row in Google Sheets...")
            self.notify("Clearing row in Google Sheets...", tone="info", ms=1500)
            self.sheet_delete_requested.emit(dict(tx))
            return
        removed = self.store.delete_transaction(tx_id)
        if removed is None:
            return
        play_ui("close")
        self._undo_kind = "tx_delete"
        self._undo_payload = removed
        self._undo.trigger(f"Deleted '{removed['title'][:26]}'")
        self.refresh()
        self._pulse_kpis("bal", "inc", "exp", "tod")
        self.notify(f"Deleted '{removed['title']}' · Undo available", tone="info")
        self.finance_changed.emit("delete", removed)

    def _open_tx_context_menu(self, pos: QPoint) -> None:
        item = self._tx_list.itemAt(pos)
        if item is None:
            return
        tx_id = item.data(ROLE_ID)
        if not tx_id:
            return
        self._tx_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.setStyleSheet(menu_qss(self._ui_scale))
        if bool(item.data(ROLE_FLAG)):
            a_ro = menu.addAction("Auto-computed QRIS Pool (read-only)")
            a_ro.setEnabled(False)
            menu.exec(self._tx_list.viewport().mapToGlobal(pos))
            return
        a_edit = menu.addAction("Edit Transaction")
        a_dup = menu.addAction("Duplicate to Form (Today)")
        menu.addSeparator()
        a_del = menu.addAction("Delete Transaction")
        chosen = menu.exec(self._tx_list.viewport().mapToGlobal(pos))
        if chosen == a_edit:
            self._start_edit_by_id(str(tx_id))
        elif chosen == a_dup:
            self._duplicate_tx(str(tx_id))
        elif chosen == a_del:
            self._delete_tx_by_id(str(tx_id))

    def _sync_tx_selection_ui(self) -> None:
        item = self._tx_list.currentItem()
        tx_id = item.data(ROLE_ID) if item is not None else None
        is_pool = bool(item.data(ROLE_FLAG)) if item is not None else False
        editable = bool(tx_id) and not is_pool
        self._edit_tx_btn.setEnabled(editable)
        self._del_tx_btn.setEnabled(editable)
        if editable and item is not None:
            title = item.text()
            amt = item.data(ROLE_BADGE) or ""
            self._tx_foot_tip.setText(f"Selected: {title[:26]} ({amt}) · Double-click to edit")
        elif tx_id and is_pool:
            self._tx_foot_tip.setText("QRIS pool row (auto-synced by QRIS expenses)")
        else:
            self._tx_foot_tip.setText("Hover a row to edit/delete · Right-click for more")

    def _undo_last(self) -> None:
        if not self._undo_payload:
            return
        if self._sheet_mode and self._sheet_busy:
            return
        if self._editing_tx_id is not None:
            self._cancel_edit()
        kind = self._undo_kind
        payload = self._undo_payload
        self._undo_kind = ""
        self._undo_payload = None
        if self._sheet_mode and kind.startswith("sheet_"):
            self.set_sheet_busy(True, "Reverting last change in Google Sheets...")
            self.notify("Reverting last change in Google Sheets...", tone="info", ms=1600)
            self.sheet_undo_requested.emit(kind, payload)
            return
        restored = self.store.restore_transaction(payload)
        if restored is not None:
            play_ui("select")
            self.refresh()
            self._tx_delegate.flash_item(restored["id"])
            self._pulse_kpis("bal", "inc", "exp", "tod")
            self.notify(f"Restored '{restored['title']}'", tone="ok")
            self.finance_changed.emit("edit", restored)

    # -- Budgets, Bills & Shortcuts Actions ----------------------------------
    def _save_budget(self, category: str) -> None:
        inp = self._budget_inputs[category]
        raw = inp.text().strip()
        if not raw:
            self._clear_budget(category)
            return
        if not self.store.set_budget(category, raw):
            msg = f"Invalid budget amount for {category} (try e.g. 500rb)."
            self._plan_status.setText(msg)
            self.nudge_input(inp, msg, tone="warn")
            return
        play_ui("select")
        lim = format_rupiah(self.store.budgets()[category])
        self._plan_status.setText(f"Set {category} monthly budget to {lim}.")
        self.notify(f"{category} budget set to {lim}", tone="ok")
        self.refresh()

    def _clear_budget(self, category: str) -> None:
        self.store.set_budget(category, None)
        self._budget_inputs[category].clear()
        self._plan_status.setText(f"Cleared monthly budget for {category}.")
        self.notify(f"Cleared {category} budget limit", tone="info")
        self.refresh()

    def _add_bill(self) -> None:
        name = self._bill_name_in.text().strip()
        if not name:
            msg = "Enter a bill name first (e.g. Kos, Wi-Fi)."
            self._plan_status.setText(msg)
            self.nudge_input(self._bill_name_in, msg, tone="warn")
            return
        amt = parse_rupiah(self._bill_amt_in.text())
        if amt is None:
            msg = "Enter a valid bill amount (e.g. 500rb)."
            self._plan_status.setText(msg)
            self.nudge_input(self._bill_amt_in, msg, tone="warn")
            return
        bill = self.store.add_bill(
            name, self._bill_day_spin.value(), amt,
            category=self._bill_cat_cb.currentText(),
            wallet=self._bill_wal_cb.currentText(),
        )
        if bill is not None:
            play_ui("select")
            self._bill_name_in.clear()
            self._bill_amt_in.clear()
            self._plan_status.setText(f"Added monthly bill '{bill['name']}' (Day {bill['day']}).")
            self.notify(f"Added recurring bill '{bill['name']}' · {format_rupiah(amt)}", tone="ok")
            self.refresh()

    def _pay_bill(self, bill_id: str) -> None:
        if self._sheet_mode:
            if self._sheet_busy:
                return
            bill = self.store.get_bill(bill_id)
            if bill is None:
                return
            today = date.today()
            if today.year == self._view_year and today.month == self._view_month:
                pay_day = today.isoformat()
            else:
                last_d = calendar.monthrange(self._view_year, self._view_month)[1]
                pay_day = date(self._view_year, self._view_month, min(bill["day"], last_d)).isoformat()
            self._plan_status.setText(f"Recording bill '{bill['name']}' to Google Sheets...")
            self.notify(f"Recording bill '{bill['name']}' to Sheets...", tone="info", ms=1600)
            self.set_sheet_busy(True)
            self.sheet_write_requested.emit({
                "type": "expense",
                "amount": bill["amount"],
                "title": bill["name"],
                "category": bill["category"],
                "wallet": bill["wallet"],
                "to_wallet": "",
                "date": pay_day,
                "bill_id": bill_id,
                "bill_year": self._view_year,
                "bill_month": self._view_month,
            })
            return
        bill, tx = self.store.pay_bill(bill_id, self._view_year, self._view_month)
        if bill is not None and tx is not None:
            play_ui("select")
            self._plan_status.setText(f"Paid '{bill['name']}' ({format_rupiah(tx['amount'])})!")
            self.refresh()
            self._pulse_kpis("bal", "exp", "tod")
            self.notify(f"Paid '{bill['name']}' · {format_rupiah(tx['amount'])}", tone="ok")
            self.finance_changed.emit("bill", tx)

    def _delete_bill(self, bill_id: str) -> None:
        if self.store.delete_bill(bill_id):
            play_ui("close")
            self.notify("Removed recurring bill", tone="info")
            self.refresh()

    def _add_shortcut(self) -> None:
        name = self._sc_name_in.text().strip()
        raw_amt = self._sc_amt_in.text().strip()
        if not name:
            msg = "Enter a shortcut name first (e.g. Parkir, Kopi)."
            self._plan_status.setText(msg)
            self.nudge_input(self._sc_name_in, msg, tone="warn")
            return
        if raw_amt and parse_rupiah(raw_amt) is None:
            msg = "Invalid shortcut price (leave empty or type e.g. 7rb)."
            self._plan_status.setText(msg)
            self.nudge_input(self._sc_amt_in, msg, tone="warn")
            return
        sc_entry = self.store.add_shortcut(name, self._sc_cat_cb.currentText(), raw_amt or None)
        if sc_entry is not None:
            play_ui("select")
            self._sc_name_in.clear()
            self._sc_amt_in.clear()
            self._plan_status.setText(f"Saved quick shortcut '{sc_entry['name']}'.")
            self.notify(f"Saved shortcut '{sc_entry['name']}'", tone="ok")
            self.refresh()

    def _delete_shortcut(self, name: str) -> None:
        if self.store.delete_shortcut(name):
            play_ui("close")
            self.notify(f"Removed shortcut '{name}'", tone="info")
            self.refresh()

    # -- Full UI Refresh -----------------------------------------------------
    def _active_data(self) -> tuple[dict[str, int], dict, list[dict]]:
        if self._sheet_mode:
            if (self._sheet_data is not None and self._sheet_data.ok
                    and (self._sheet_data.year, self._sheet_data.month) == (self._view_year, self._view_month)):
                return self._sheet_data.balances, self._sheet_data.summary, self._sheet_data.transactions
            empty_bal = {"Tunai": 0, "BNI": 0, "Mandiri": 0, "Total": 0}
            empty_sum = {
                "income": 0,
                "expense": 0,
                "net": 0,
                "today_expense": 0,
                "count": 0,
                "by_category": {c: 0 for c in FINANCE_CATEGORIES},
            }
            return empty_bal, empty_sum, []
        return (
            self.store.wallet_balances(),
            self.store.month_summary(self._view_year, self._view_month),
            self.store.month_transactions(self._view_year, self._view_month),
        )

    def refresh(self) -> None:
        self._month_lbl.setText(f"{self._MONTH_NAMES[self._view_month - 1]} {self._view_year}")
        self._update_sheet_badge()

        bal, summ, _items = self._active_data()
        sheet_waiting = self._sheet_mode and self._sheet_data is None

        if sheet_waiting:
            self._kpi_bal_val.setText("..." if self._sheet_busy else "—")
            self._kpi_bal_sub.setText(
                f"Loading '{month_tab_name(self._view_month)}' from Google Sheets..."
                if self._sheet_busy else (self._sheet_error or "Click SYNC to load Google Sheets")
            )
            self._kpi_inc_val.setText("..." if self._sheet_busy else "—")
            self._kpi_inc_sub.setText("Google Sheets mode")
            self._kpi_exp_val.setText("..." if self._sheet_busy else "—")
            self._kpi_exp_sub.setText(f"Tab: {month_tab_name(self._view_month)}")
            self._kpi_tod_val.setText("..." if self._sheet_busy else "—")
            self._kpi_tod_sub.setText("Spent today")
            for w in FINANCE_WALLETS:
                self._wallet_btns[w].setText(w.upper())
                self._wallet_btns[w].setToolTip(w)
                self._to_wallet_btns[w].setText(w.upper())
                self._to_wallet_btns[w].setToolTip(w)
        else:
            self._kpi_bal_val.setText(format_rupiah(bal["Total"]))
            self._kpi_bal_sub.setText(
                f"Tunai {format_rupiah(bal['Tunai'])} · BNI {format_rupiah(bal['BNI'])} · Mandiri {format_rupiah(bal['Mandiri'])}"
            )
            self._kpi_inc_val.setText("+" + format_rupiah(summ["income"]))
            net = summ["net"]
            net_prefix = "+" if net >= 0 else ""
            self._kpi_inc_sub.setText(f"Net: {net_prefix}{format_rupiah(net)}")
            self._kpi_exp_val.setText("-" + format_rupiah(summ["expense"]))
            self._kpi_exp_sub.setText(f"{summ['count']} entries this month")
            self._kpi_tod_val.setText(format_rupiah(summ["today_expense"]))
            if self._sheet_mode and self._sheet_data and self._sheet_data.synced_at:
                self._kpi_tod_sub.setText(f"Spent today · Synced {self._sheet_data.synced_at}")
            else:
                self._kpi_tod_sub.setText("Spent today")
            # Live compact balances right on the wallet picker chips!
            for w in FINANCE_WALLETS:
                v = bal.get(w, 0)
                lbl = f"{w.upper()} · {self._short_rp(v)}"
                tip = f"{w} balance: {format_rupiah(v)}"
                self._wallet_btns[w].setText(lbl)
                self._wallet_btns[w].setToolTip(tip)
                self._to_wallet_btns[w].setText(lbl)
                self._to_wallet_btns[w].setToolTip(tip)

        self._refresh_shortcut_chips()
        self._refresh_tx_list()
        self._refresh_budgets_and_wallets(bal, summ)
        self._refresh_bills_and_sc_mgr()

    def _refresh_shortcut_chips(self) -> None:
        sc = self._ui_scale
        _clear_layout(self._sc_grid)
        shortcuts = self.store.shortcuts()
        self._sc_wrap.setVisible(self._tx_type == "expense" and bool(shortcuts))
        for idx, entry in enumerate(shortcuts[:8]):
            amt = entry.get("amount")
            lbl = f"{entry['name']} · {amt // 1000}rb" if (amt and amt >= 1000 and amt % 1000 == 0) else (
                f"{entry['name']} · {format_rupiah(amt)}" if amt else entry["name"]
            )
            btn = self._btn(lbl, variant="soft", display=False, px=10)
            btn.setFixedHeight(_sc(23, sc, 20))
            btn.setToolTip(f"{entry['category']}" + (f" · {format_rupiah(amt)}" if amt else ""))
            if self._active_shortcut_name and entry["name"] == self._active_shortcut_name:
                btn.set_active(True, "gold")
            btn.clicked.connect(lambda _c=False, e=dict(entry): self._apply_shortcut(e))
            self._sc_grid.addWidget(btn, idx // 4, idx % 4)

    def _refresh_tx_list(self) -> None:
        _bal, _summ, items = self._active_data()
        q = self._search_input.text().strip().lower()
        flt = self._tx_filter
        today_iso = date.today().isoformat()
        filtered: list[dict] = []
        for t in items:
            if flt == "today" and t["date"] != today_iso:
                continue
            elif flt in ("expense", "income", "transfer") and t["type"] != flt:
                continue
            elif flt.startswith("cat:") and (t["type"] != "expense" or t.get("category") != flt[4:]):
                continue
            if q:
                hay = f"{t['title']} {t.get('category', '')} {t['wallet']} {t.get('to_wallet', '')} {t['date']}".lower()
                if q not in hay:
                    continue
            filtered.append(t)

        # Count + live filtered subtotal badge
        cnt_txt = f"{len(filtered)} {'entry' if len(filtered) == 1 else 'entries'}"
        if filtered:
            types = {t["type"] for t in filtered}
            if types == {"expense"}:
                cnt_txt += f" · -{format_rupiah(sum(t['amount'] for t in filtered))}"
            elif types == {"income"}:
                cnt_txt += f" · +{format_rupiah(sum(t['amount'] for t in filtered))}"
            elif types == {"transfer"}:
                cnt_txt += f" · {format_rupiah(sum(t['amount'] for t in filtered))}"
            else:
                inc_s = sum(t["amount"] for t in filtered if t["type"] == "income")
                exp_s = sum(t["amount"] for t in filtered if t["type"] == "expense")
                net_s = inc_s - exp_s
                cnt_txt += f" · Net {'+' if net_s >= 0 else ''}{format_rupiah(net_s)}"
        self._tx_count_lbl.setText(cnt_txt)
        self._reset_flt_btn.setVisible(flt != "all" or bool(q))

        self._tx_list.clear()
        if not filtered:
            if self._sheet_mode and self._sheet_busy and self._sheet_data is None:
                msg = f"Loading '{month_tab_name(self._view_month)}' from Google Sheets..."
            elif self._sheet_mode and self._sheet_error:
                msg = f"Google Sheets unavailable:\n{self._sheet_error}"
            elif flt == "today":
                msg = "No transactions recorded today yet.\nUse the quick form on the left to log one!"
            elif flt != "all" or q:
                msg = "No transactions match this search or filter.\nClick × RESET to show all entries."
            else:
                msg = "No transactions recorded this month yet.\nLog an expense, income, or transfer on the left!"
            empty = QListWidgetItem(msg)
            empty.setData(ROLE_ID, None)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._tx_list.addItem(empty)
            self._sync_tx_selection_ui()
            self._sync_kpi_active_states()
            return

        for t in filtered:
            it = QListWidgetItem(t["title"])
            it.setData(ROLE_ID, t["id"])
            it.setData(ROLE_CAT, t["type"])
            it.setData(ROLE_FLAG, t.get("sheet_kind") == "qris_pool")
            try:
                d_str = date.fromisoformat(t["date"]).strftime("%d %b")
            except ValueError:
                d_str = t["date"]
            if t["type"] == "income":
                amt_txt = "+" + format_rupiah(t["amount"])
                meta = f"{t['wallet']} · {d_str}"
            elif t["type"] == "transfer":
                amt_txt = format_rupiah(t["amount"])
                meta = f"{t['wallet']} → {t.get('to_wallet', '')} · {d_str}"
            else:
                amt_txt = "-" + format_rupiah(t["amount"])
                wal_part = f" · {t['wallet']}" if t.get("wallet") else ""
                meta = f"{t.get('category', 'Jajan')}{wal_part} · {d_str}"
            it.setData(ROLE_BADGE, amt_txt)
            it.setData(ROLE_DATE, meta)
            self._tx_list.addItem(it)
        self._sync_tx_selection_ui()
        self._sync_kpi_active_states()

    def _refresh_budgets_and_wallets(self, bal: dict[str, int], summ: dict) -> None:
        budgets = self.store.budgets()
        by_cat = summ["by_category"]
        for cat in FINANCE_CATEGORIES:
            spent = by_cat.get(cat, 0)
            limit = budgets.get(cat)
            info_lbl, pct_lbl, bar = self._budget_bars[cat]
            inp = self._budget_inputs[cat]
            if not inp.hasFocus():
                inp.setText(str(limit) if limit else "")
            if limit and limit > 0:
                ratio = spent / float(limit)
                pct = int(round(ratio * 100))
                bar.setValue(min(100, pct))
                info_lbl.setText(f"{format_rupiah(spent)} / {format_rupiah(limit)}")
                if ratio >= 1.0:
                    col = "#E60012"
                    pct_lbl.setText(f"{pct}% OVER!")
                elif ratio >= FINANCE_WARN_RATIO:
                    col = "#F5C400"
                    pct_lbl.setText(f"{pct}% WARN")
                else:
                    col = "#5CE09A"
                    pct_lbl.setText(f"{pct}%")
                pct_lbl.setStyleSheet(
                    f'color: {col}; font-family: "Barlow Condensed", sans-serif; '
                    f'font-size: {_sc(11, self._ui_scale, 9)}px; font-weight: 800;'
                )
                bar.setStyleSheet(
                    f"QProgressBar {{ background: #1B1E28; border-radius: 3px; }}"
                    f"QProgressBar::chunk {{ background: {col}; border-radius: 3px; }}"
                )
            else:
                bar.setValue(0)
                info_lbl.setText(f"Spent: {format_rupiah(spent)}")
                pct_lbl.setText("No limit")
                pct_lbl.setStyleSheet(
                    f'color: #8A8F9E; font-family: "Barlow Condensed", sans-serif; '
                    f'font-size: {_sc(11, self._ui_scale, 9)}px; font-weight: 800;'
                )
                bar.setStyleSheet(
                    "QProgressBar { background: #1B1E28; border-radius: 3px; }"
                    "QProgressBar::chunk { background: #3E4457; border-radius: 3px; }"
                )

        max_pos = max([1] + [max(0, bal[w]) for w in FINANCE_WALLETS])
        for w in FINANCE_WALLETS:
            val_lbl, wbar = self._wallet_rows[w]
            v = bal[w]
            val_lbl.setText(format_rupiah(v))
            val_lbl.setStyleSheet(
                f'color: {"#FF5C6C" if v < 0 else "#FFE46B"}; font-family: "Barlow Condensed", sans-serif; '
                f'font-size: {_sc(14, self._ui_scale, 11)}px; font-weight: 800;'
            )
            wbar.setValue(int(round(max(0, v) * 100.0 / max_pos)))

    def _refresh_bills_and_sc_mgr(self) -> None:
        sc = self._ui_scale
        _clear_layout(self._bills_box)
        bills = self.store.bills()
        if not bills:
            empty = QLabel("No recurring bills yet. Add rent, Wi-Fi, or subscriptions below!")
            empty.setObjectName("tip")
            empty.setWordWrap(True)
            self._bills_box.addWidget(empty)
        else:
            for b in bills:
                paid = self.store.is_bill_paid(b, self._view_year, self._view_month)
                row = QFrame()
                row.setStyleSheet("QFrame { background: #181B25; border: 1px solid #2A2F3E; border-radius: 6px; }")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(_sc(8, sc, 6), _sc(5, sc, 4), _sc(8, sc, 6), _sc(5, sc, 4))
                rl.setSpacing(_sc(6, sc, 4))

                info = QLabel(f"Day {b['day']:02d} · {b['name']}  ({format_rupiah(b['amount'])} · {b['wallet']})")
                info.setStyleSheet(f'color: {"#8A919E" if paid else "#FFFFFF"}; font-size: {_sc(12, sc, 9)}px; border: none;')
                rl.addWidget(info, 1)

                if paid:
                    badge = QLabel("PAID")
                    badge.setStyleSheet(
                        f'background: #143324; color: #5CE09A; border: none; border-radius: 4px; '
                        f'padding: 2px {_sc(7, sc, 5)}px; font-family: "Barlow Condensed", sans-serif; '
                        f'font-size: {_sc(10, sc, 8)}px; font-weight: 800;'
                    )
                    rl.addWidget(badge)
                else:
                    pay_b = self._btn("PAY", variant="gold", display=True, px=9)
                    pay_b.setFixedHeight(_sc(22, sc, 19))
                    pay_b.clicked.connect(lambda _c=False, bid=b["id"]: self._pay_bill(bid))
                    rl.addWidget(pay_b)

                del_b = self._btn("×", variant="soft", display=True, px=10, sfx_click="cancel")
                del_b.setFixedSize(_sc(22, sc, 19), _sc(22, sc, 19))
                del_b.clicked.connect(lambda _c=False, bid=b["id"]: self._delete_bill(bid))
                rl.addWidget(del_b)
                self._bills_box.addWidget(row)

        _clear_layout(self._sc_mgr_box)
        shortcuts = self.store.shortcuts()
        if not shortcuts:
            empty_sc = QLabel("No shortcuts yet.")
            empty_sc.setObjectName("tip")
            self._sc_mgr_box.addWidget(empty_sc)
        else:
            for entry in shortcuts:
                row = QFrame()
                row.setStyleSheet("QFrame { background: #181B25; border: 1px solid #2A2F3E; border-radius: 6px; }")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(_sc(8, sc, 6), _sc(4, sc, 3), _sc(8, sc, 6), _sc(4, sc, 3))
                rl.setSpacing(_sc(6, sc, 4))
                amt_s = f" · {format_rupiah(entry['amount'])}" if entry.get("amount") else " · manual price"
                lbl = QLabel(f"{entry['name']}  ({entry['category']}{amt_s})")
                lbl.setStyleSheet(f'color: #ECEEF5; font-size: {_sc(11, sc, 9)}px; border: none;')
                rl.addWidget(lbl, 1)
                del_s = self._btn("×", variant="soft", display=True, px=10, sfx_click="cancel")
                del_s.setFixedSize(_sc(22, sc, 19), _sc(22, sc, 19))
                del_s.clicked.connect(lambda _c=False, nm=entry["name"]: self._delete_shortcut(nm))
                rl.addWidget(del_s)
                self._sc_mgr_box.addWidget(row)