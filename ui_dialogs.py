# -*- coding: utf-8 -*-
"""Clean, modern, fluid popup windows for Notes, Tasks, and Settings.

Inspired by a calm rounded-card workspace:
  * Soft light canvas with white rounded cards and restrained spring motion.
  * Text color never changes on hover (only subtle surface/background tint shifts).
  * NotesDialog pairs a searchable categorized note card list with a clean editor
    and a live mini To-do list card synchronized with TasksDialog.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (QDate, QDateTime, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                          QTime, QTimer, pyqtSignal)
from PyQt6.QtGui import (QColor, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
                         QPainterPath, QPen, QPixmap, QShortcut, QTextCursor, QTextOption)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDateTimeEdit, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                             QPushButton, QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate,
                             QTextEdit, QTimeEdit, QVBoxLayout, QWidget)

from calendar_service import human_delta
from motion import FrameTicker, Spring, clamp
from storage_manager import (SETTINGS_DEFAULTS, NoteStore, SettingsStore, TaskStore, parse_dt)
from theme import (DIALOG_QSS, UI_ACCENT, UI_ACCENT_HOVER, UI_AMBER, UI_AMBER_HOVER, UI_AMBER_SOFT,
                   UI_AMBER_TEXT, UI_BG, UI_BORDER, UI_CARD, UI_HOVER, UI_LAVENDER_SOFT,
                   UI_MINT_SOFT, UI_MINT_TEXT, UI_MUTED, UI_ROSE_HOVER, UI_ROSE_SOFT, UI_ROSE_TEXT,
                   UI_SELECTED, UI_SURFACE, UI_TEXT, WHITE, lerp_color, ui_font)

ROLE_ID = Qt.ItemDataRole.UserRole.value      # item id; None = empty-state hint row
ROLE_DONE = ROLE_ID + 1                       # bool: completed
ROLE_PREVIEW = ROLE_ID + 2                    # 2-line preview text (notes)
ROLE_BADGE = ROLE_ID + 3                      # pill badge text (e.g. "In 2h", "Pending")
ROLE_DATE = ROLE_ID + 4                       # formatted date or time string
ROLE_CAT = ROLE_ID + 5                        # category / tone key ("Projects", "Meeting", "Personal", "overdue", "done")

NOTE_CATEGORIES = ("Personal", "Projects", "Meeting")
_AVATAR_CACHE: dict[int, QPixmap] = {}


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


def _fmt_date(iso: str | None) -> str:
    dt = parse_dt(iso)
    return f"{dt:%d-%m-%Y}" if dt else ""


def _read_time_label(content: str) -> str:
    words = len((content or "").split())
    if words == 0:
        return "Note"
    mins = max(1, round(words / 180))
    return f"{mins} min"


def _morgana_avatar(size: int = 32) -> QPixmap:
    """Circular Morgana portrait cropped from Morgana/b003_000_00.png (or clean 'M' fallback)."""
    if size in _AVATAR_CACHE:
        return _AVATAR_CACHE[size]
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(clip)
    p.fillRect(0, 0, size, size, UI_LAVENDER_SOFT)

    sprite = Path(__file__).resolve().parent / "Morgana" / "b003_000_00.png"
    src = QPixmap(str(sprite)) if sprite.is_file() else QPixmap()
    if not src.isNull():
        # Focus neatly on Morgana's head
        crop = src.copy(QRect(120, 85, 485, 485))
        p.drawPixmap(QRect(0, 0, size, size), crop)
    else:
        p.setPen(UI_ACCENT)
        p.setFont(ui_font(max(12, int(size * 0.52)), bold=True))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter.value, "M")
    p.end()
    _AVATAR_CACHE[size] = pm
    return pm


def _draw_icon(p: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    """Minimal geometric vector icons drawn cleanly with QPainter."""
    cx, cy = rect.center().x(), rect.center().y()
    r = min(rect.width(), rect.height()) * 0.36
    p.save()
    p.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "plus":
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    elif kind == "close":
        k = r * 0.85
        p.drawLine(QPointF(cx - k, cy - k), QPointF(cx + k, cy + k))
        p.drawLine(QPointF(cx + k, cy - k), QPointF(cx - k, cy + k))
    elif kind == "gear":
        p.drawEllipse(QPointF(cx, cy), r * 0.45, r * 0.45)
        p.drawEllipse(QPointF(cx, cy), r * 0.92, r * 0.92)
    elif kind == "sort":
        p.drawLine(QPointF(cx - r, cy - r * 0.6), QPointF(cx + r, cy - r * 0.6))
        p.drawLine(QPointF(cx - r * 0.65, cy), QPointF(cx + r * 0.65, cy))
        p.drawLine(QPointF(cx - r * 0.3, cy + r * 0.6), QPointF(cx + r * 0.3, cy + r * 0.6))
    elif kind == "doc":
        box = QRectF(cx - r * 0.78, cy - r * 0.95, r * 1.56, r * 1.9)
        p.drawRoundedRect(box, 2.5, 2.5)
        p.drawLine(QPointF(cx - r * 0.4, cy - r * 0.25), QPointF(cx + r * 0.4, cy - r * 0.25))
        p.drawLine(QPointF(cx - r * 0.4, cy + r * 0.25), QPointF(cx + r * 0.25, cy + r * 0.25))
    elif kind == "check_box":
        box = QRectF(cx - r * 0.88, cy - r * 0.88, r * 1.76, r * 1.76)
        p.drawRoundedRect(box, 3.0, 3.0)
        p.drawLine(QPointF(cx - r * 0.42, cy + r * 0.04), QPointF(cx - r * 0.1, cy + r * 0.36))
        p.drawLine(QPointF(cx - r * 0.1, cy + r * 0.36), QPointF(cx + r * 0.46, cy - r * 0.32))
    elif kind == "clock":
        p.drawEllipse(QPointF(cx, cy), r * 0.9, r * 0.9)
        p.drawLine(QPointF(cx, cy - r * 0.5), QPointF(cx, cy))
        p.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.42, cy + r * 0.22))
    elif kind == "check":
        p.drawLine(QPointF(cx - r * 0.65, cy + r * 0.05), QPointF(cx - r * 0.15, cy + r * 0.55))
        p.drawLine(QPointF(cx - r * 0.15, cy + r * 0.55), QPointF(cx + r * 0.72, cy - r * 0.5))
    elif kind == "trash":
        p.drawLine(QPointF(cx - r * 0.8, cy - r * 0.55), QPointF(cx + r * 0.8, cy - r * 0.55))
        p.drawLine(QPointF(cx - r * 0.32, cy - r * 0.85), QPointF(cx + r * 0.32, cy - r * 0.85))
        p.drawRoundedRect(QRectF(cx - r * 0.58, cy - r * 0.55, r * 1.16, r * 1.45), 2.0, 2.0)
    elif kind == "briefcase":
        p.drawRoundedRect(QRectF(cx - r * 0.9, cy - r * 0.45, r * 1.8, r * 1.25), 2.5, 2.5)
        p.drawRoundedRect(QRectF(cx - r * 0.4, cy - r * 0.85, r * 0.8, r * 0.4), 1.5, 1.5)
    elif kind == "calendar":
        p.drawRoundedRect(QRectF(cx - r * 0.85, cy - r * 0.75, r * 1.7, r * 1.6), 2.5, 2.5)
        p.drawLine(QPointF(cx - r * 0.85, cy - r * 0.2), QPointF(cx + r * 0.85, cy - r * 0.2))
        p.drawLine(QPointF(cx - r * 0.4, cy - r * 0.95), QPointF(cx - r * 0.4, cy - r * 0.55))
        p.drawLine(QPointF(cx + r * 0.4, cy - r * 0.95), QPointF(cx + r * 0.4, cy - r * 0.55))
    p.restore()


def _category_style(cat: str) -> tuple[QColor, QColor, str]:
    """Return (pastel_bg, icon_fg, icon_kind) for a note category or status."""
    if cat == "overdue":
        return UI_ROSE_SOFT, UI_ROSE_TEXT, "clock"
    if cat == "done":
        return UI_MINT_SOFT, UI_MINT_TEXT, "check"
    if cat == "Projects":
        return UI_LAVENDER_SOFT, UI_ACCENT, "briefcase"
    if cat == "Meeting":
        return UI_AMBER_SOFT, UI_AMBER_TEXT, "calendar"
    return QColor("#E4F3FF"), QColor("#3A7BD5"), "doc"


class _BadgeIcon(QWidget):
    """Small rounded pastel icon badge (used in card headers)."""

    def __init__(self, cat: str = "Projects", size: int = 30, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cat = cat
        self.setFixedSize(size, size)

    def set_category(self, cat: str) -> None:
        if cat != self._cat:
            self._cat = cat
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg, fg, icon = _category_style(self._cat)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), 9.0, 9.0)
        _draw_icon(p, icon, QRectF(self.rect()).adjusted(4, 4, -4, -4), fg)


# ---------------------------------------------------------------------------
# FluidButton: clean rounded pill button; hover shifts background only (never text color)
# ---------------------------------------------------------------------------
class FluidButton(QPushButton):
    VARIANTS = {
        "primary": (UI_ACCENT, UI_ACCENT_HOVER, WHITE),
        "amber":   (UI_AMBER, UI_AMBER_HOVER, QColor("#3A2D12")),
        "soft":    (QColor("#EFF2F8"), QColor("#E2E7F2"), QColor("#2B3245")),
        "ghost":   (QColor(238, 242, 250, 0), UI_HOVER, QColor("#363E52")),
        "danger":  (UI_ROSE_SOFT, UI_ROSE_HOVER, UI_ROSE_TEXT),
        "mint":    (UI_MINT_SOFT, QColor("#C6F6D5"), UI_MINT_TEXT),
    }

    def __init__(self, text: str = "", variant: str = "soft", icon: str = "",
                 radius: float = 10.0, px: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._base_variant = variant
        self._icon = icon
        self._radius = radius
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFont(ui_font(px, bold=True))
        # Critically damped springs: smooth without bouncy overshoot
        self._hover = Spring(0.0, 360.0)
        self._press = Spring(0.0, 620.0)
        self._ticker = FrameTicker(self._frame, self)

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self.update()

    def set_active(self, on: bool) -> None:
        self._active = on
        self._variant = "primary" if on else self._base_variant
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self.text()) if self.text() else 0
        iw = 16 if self._icon else 0
        gap = 6 if (self._icon and self.text()) else 0
        pad = 24 if self.text() else 16
        return QSize(max(32, tw + iw + gap + pad), 32)

    def enterEvent(self, e) -> None:
        self._hover.target = 1.0
        self._ticker.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover.target = 0.0
        self._ticker.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
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
        bg0, bg1, fg = self.VARIANTS.get(self._variant, self.VARIANTS["soft"])
        hov = clamp(self._hover.value)
        prs = clamp(self._press.value)

        # Blend alpha as well so "ghost" fades smoothly from transparent to subtle tint
        bg = lerp_color(bg0, bg1, hov)
        bg.setAlpha(int(bg0.alpha() + (bg1.alpha() - bg0.alpha()) * hov))

        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if prs > 0.01:
            s = 1.0 - 0.03 * prs
            c = r.center()
            p.translate(c)
            p.scale(s, s)
            p.translate(-c)

        if not self.isEnabled():
            p.setOpacity(0.45)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, self._radius, self._radius)

        # Text & icon keep the exact same color on hover (never inverted)
        txt = self.text()
        p.setFont(self.font())
        p.setPen(fg)
        if self._icon and txt:
            fm = QFontMetrics(self.font())
            tw = fm.horizontalAdvance(txt)
            total = 15 + 6 + tw
            x0 = r.center().x() - total / 2.0
            _draw_icon(p, self._icon, QRectF(x0, r.center().y() - 8, 16, 16), fg)
            p.drawText(QRectF(x0 + 21, r.top(), tw + 4, r.height()),
                       Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, txt)
        elif self._icon:
            _draw_icon(p, self._icon, QRectF(r.center().x() - 9, r.center().y() - 9, 18, 18), fg)
        else:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter.value, txt)


class FluidCheckBox(QCheckBox):
    """Clean rounded checkbox with a crisp painted white checkmark."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(13, bold=True))

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        return QSize(26 + fm.horizontalAdvance(self.text()) + 6, 26)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        box = QRectF(1.0, (self.height() - 18.0) / 2.0, 18.0, 18.0)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(UI_ACCENT)
            p.drawRoundedRect(box, 6.0, 6.0)
            c = box.center()
            p.setPen(QPen(WHITE, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QPointF(c.x() - 4.4, c.y() + 0.4), QPointF(c.x() - 1.2, c.y() + 3.6))
            p.drawLine(QPointF(c.x() - 1.2, c.y() + 3.6), QPointF(c.x() + 4.8, c.y() - 3.8))
        else:
            p.setPen(QPen(QColor("#8C96AC"), 1.6))
            p.setBrush(WHITE)
            p.drawRoundedRect(box, 6.0, 6.0)
        p.setFont(self.font())
        p.setPen(UI_TEXT)
        p.drawText(QRectF(26.0, 0.0, self.width() - 26.0, self.height()),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                   self.text())


# ---------------------------------------------------------------------------
# FluidPanel: soft rounded window with restrained slide+fade open/close
# ---------------------------------------------------------------------------
class FluidPanel(QWidget):
    M, SLIDE, RADIUS = 12, 14, 20.0

    def __init__(self, size: QSize) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(size)
        self.setStyleSheet(DIALOG_QSS)
        self._drag_offset: QPoint | None = None

        # Critically damped open/close: fluid without distracting bounce
        self._open = Spring(0.0, 320.0)
        self._closing = False
        self._base_pos = QPoint()
        self._ticker = FrameTicker(self._frame, self)

        m = self.M
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(m + 14, m + 14, m + 14, m + 14)
        self.root.setSpacing(12)

    def _frame(self, dt: float) -> bool:
        moving = self._open.step(dt)
        o = clamp(self._open.value)
        self.setWindowOpacity(o)
        self.move(self._base_pos.x(), self._base_pos.y() + int(round((1.0 - o) * self.SLIDE)))
        if self._closing and o < 0.03:
            self._closing = False
            self._open.snap(0.0)
            QWidget.hide(self)
            self.setWindowOpacity(1.0)
            return False
        return moving

    def close_animated(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self.on_closing()
        self._closing = True
        self._open.configure(380.0)
        self._open.target = 0.0
        self._ticker.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.M
        body = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)

        # Layered soft ambient shadow
        p.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((8, 10), (5, 16), (2, 22)):
            p.setBrush(QColor(22, 30, 52, alpha))
            p.drawRoundedRect(body.adjusted(-spread, -spread + 3, spread, spread + 4),
                              self.RADIUS + spread * 0.5, self.RADIUS + spread * 0.5)

        p.setBrush(UI_BG)
        p.setPen(QPen(QColor("#D8DFEC"), 1.0))
        p.drawRoundedRect(body, self.RADIUS, self.RADIUS)

    # Drag window via the top bar region
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= self.M + 62:
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
        """Called whenever the dialog is shown."""

    def on_shown(self) -> None:
        """Called right after the dialog appears."""

    def on_closing(self) -> None:
        """Called right before closing (e.g. autosave)."""

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()

    def show_near(self, anchor: QRect) -> None:
        if not self.isVisible():
            screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            w = min(self.width(), max(460, area.width() - 28))
            h = min(self.height(), max(440, area.height() - 28))
            if w != self.width() or h != self.height():
                self.setFixedSize(w, h)
            x = anchor.left() - w - 10
            if x < area.left():
                x = min(anchor.right() + 10, area.right() - w)
            y = max(area.top(), min(anchor.center().y() - h // 2, area.bottom() - h))
            self._base_pos = QPoint(x, y)
            self._open.snap(0.0)
            self.setWindowOpacity(0.0)
            self.move(x, y + self.SLIDE)
            self.show()
        self._closing = False
        self._open.configure(320.0)
        self._open.target = 1.0
        self._ticker.start()
        self.raise_()
        self.activateWindow()
        self.on_shown()


# ---------------------------------------------------------------------------
# NoteCardDelegate: rounded card list items (left column of NotesDialog)
# ---------------------------------------------------------------------------
class NoteCardDelegate(QStyledItemDelegate):
    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._springs: dict[str, Spring] = {}
        self._title_font = ui_font(13, bold=True)
        self._done_font = ui_font(13, bold=True)
        self._done_font.setStrikeOut(True)
        self._preview_font = ui_font(11, bold=False)
        self._meta_font = ui_font(10, bold=True)
        self._date_font = ui_font(10, bold=False)
        self._hint_font = ui_font(12, bold=False)
        self._ticker = FrameTicker(self._frame, self)

    def start_check_anim(self, item_id: str) -> None:
        sp = Spring(0.0, 380.0)
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

    @staticmethod
    def _icon_rect(card: QRectF) -> QRectF:
        return QRectF(card.left() + 10, card.center().y() - 22, 44, 44)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(180, 90)
        return QSize(180, 100)

    def paint(self, painter, option, index) -> None:
        r = QRectF(option.rect)
        item_id = index.data(ROLE_ID)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if item_id is None:
            painter.setPen(UI_MUTED)
            painter.setFont(self._hint_font)
            painter.drawText(r.adjusted(14, 10, -14, -10),
                             Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                             index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        card = r.adjusted(2, 4, -4, -4)
        done = bool(index.data(ROLE_DONE))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            bg, border = UI_SELECTED, QColor("#C6D4FF")
        elif hovered:
            bg, border = UI_HOVER, QColor("#E2E7F4")
        else:
            bg, border = UI_SURFACE, QColor("#EDF0F7")

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(card, 14.0, 14.0)

        # Left pastel icon badge (click to toggle done)
        cat = index.data(ROLE_CAT) or "Personal"
        icon_r = self._icon_rect(card)
        ib, ifg, ikind = _category_style(cat)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ib)
        painter.drawRoundedRect(icon_r, 12.0, 12.0)
        sp = self._springs.get(item_id)
        if done and sp is not None:
            painter.save()
            sc = 0.7 + 0.3 * clamp(sp.value)
            c = icon_r.center()
            painter.translate(c)
            painter.scale(sc, sc)
            painter.translate(-c)
            _draw_icon(painter, "check", icon_r.adjusted(7, 7, -7, -7), ifg)
            painter.restore()
        else:
            _draw_icon(painter, ikind, icon_r.adjusted(7, 7, -7, -7), ifg)

        tx = icon_r.right() + 11
        tw = max(40.0, card.right() - tx - 10)

        # Title (color stays constant on hover/select)
        title = index.data(Qt.ItemDataRole.DisplayRole) or "(Untitled)"
        tf = self._done_font if done else self._title_font
        tfm = QFontMetrics(tf)
        painter.setFont(tf)
        painter.setPen(UI_MUTED if done else UI_TEXT)
        ty = card.top() + 10 + tfm.ascent()
        painter.drawText(QPointF(tx, ty), tfm.elidedText(title, Qt.TextElideMode.ElideRight, int(tw)))

        # Strictly 2-line preview snippet with clean ellipsis on line 2
        preview = index.data(ROLE_PREVIEW) or ""
        painter.setFont(self._preview_font)
        painter.setPen(UI_MUTED)
        pfm = QFontMetrics(self._preview_font)
        max_w = int(tw)
        words = preview.split(" ")
        line1, rest = "", []
        for idx_w, word in enumerate(words):
            trial = f"{line1} {word}" if line1 else word
            if line1 and pfm.horizontalAdvance(trial) > max_w:
                rest = words[idx_w:]
                break
            line1 = trial
        y_p = card.top() + 29 + pfm.ascent()
        painter.drawText(QPointF(tx, y_p), pfm.elidedText(line1, Qt.TextElideMode.ElideRight, max_w))
        if rest:
            line2 = pfm.elidedText(" ".join(rest), Qt.TextElideMode.ElideRight, max_w)
            painter.drawText(QPointF(tx, y_p + pfm.height() + 1), line2)

        # Bottom meta row: pill badge + date
        badge = index.data(ROLE_BADGE) or ""
        date_txt = index.data(ROLE_DATE) or ""
        by = card.bottom() - 23
        x_cur = tx
        if badge:
            bfm = QFontMetrics(self._meta_font)
            bw = bfm.horizontalAdvance(badge) + 14
            pill = QRectF(x_cur, by, bw, 17)
            if cat == "overdue":
                pbg, pfg = UI_ROSE_SOFT, UI_ROSE_TEXT
            elif cat == "done":
                pbg, pfg = UI_MINT_SOFT, UI_MINT_TEXT
            elif "In " in badge or "Due" in badge:
                pbg, pfg = UI_AMBER_SOFT, UI_AMBER_TEXT
            else:
                pbg, pfg = WHITE, UI_MUTED
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pbg)
            painter.drawRoundedRect(pill, 8.5, 8.5)
            painter.setFont(self._meta_font)
            painter.setPen(pfg)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, badge)
            x_cur = pill.right() + 8

        if date_txt:
            painter.setFont(self._date_font)
            painter.setPen(QColor("#8E96AA"))
            painter.drawText(QRectF(x_cur, by, max(0.0, card.right() - x_cur - 8), 17),
                             Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                             date_txt)

        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if index.data(ROLE_ID) is None:
            return False
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton):
            card = QRectF(option.rect).adjusted(2, 4, -4, -4)
            if self._icon_rect(card).contains(event.position()):
                model.setData(index, not bool(index.data(ROLE_DONE)), ROLE_DONE)
                return True
        return super().editorEvent(event, model, option, index)


# ---------------------------------------------------------------------------
# TaskCardDelegate: clean rounded checkbox + status pill + time
# ---------------------------------------------------------------------------
class TaskCardDelegate(QStyledItemDelegate):
    CHECK_POINTS = ((-4.8, 0.4), (-1.4, 3.8), (5.2, -4.2))

    def __init__(self, view: QListWidget, compact: bool = False) -> None:
        super().__init__(view)
        self._compact = compact
        self._springs: dict[str, Spring] = {}
        self._swallow_release = False
        self._title_font = ui_font(13, bold=True)
        self._done_font = ui_font(13, bold=True)
        self._done_font.setStrikeOut(True)
        self._pill_font = ui_font(10, bold=True)
        self._time_font = ui_font(10, bold=False)
        self._hint_font = ui_font(12, bold=False)
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

    @staticmethod
    def _box_rect(card: QRectF) -> QRectF:
        return QRectF(card.left() + 12, card.top() + 12, 20, 20)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(120, 64)
        return QSize(120, 56 if self._compact else 62)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect.adjusted(44, 6, -10, -24))

    def _paint_checkbox(self, p: QPainter, box: QRectF, done: bool, prog: float) -> None:
        p.save()
        if done:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(UI_ACCENT)
            p.drawRoundedRect(box, 6.0, 6.0)
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
                p.setPen(QPen(WHITE, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        else:
            p.setPen(QPen(QColor("#4B556B"), 1.6))
            p.setBrush(WHITE)
            p.drawRoundedRect(box, 6.0, 6.0)
        p.restore()

    def paint(self, painter, option, index) -> None:
        r = QRectF(option.rect)
        item_id = index.data(ROLE_ID)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if item_id is None:
            painter.setPen(UI_MUTED)
            painter.setFont(self._hint_font)
            painter.drawText(r.adjusted(12, 4, -12, -4),
                             Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                             index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        card = r.adjusted(2, 3, -4, -3)
        done = bool(index.data(ROLE_DONE))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        if selected:
            painter.setPen(QPen(QColor("#C6D4FF"), 1.0))
            painter.setBrush(UI_SELECTED)
            painter.drawRoundedRect(card, 12.0, 12.0)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(UI_HOVER)
            painter.drawRoundedRect(card, 12.0, 12.0)
        elif not self._compact:
            painter.setPen(QPen(QColor("#EDF0F7"), 1.0))
            painter.setBrush(UI_SURFACE)
            painter.drawRoundedRect(card, 12.0, 12.0)

        box = self._box_rect(card)
        sp = self._springs.get(item_id)
        self._paint_checkbox(painter, box, done, sp.value if sp else 1.0)

        tx = box.right() + 12
        tw = max(40.0, card.right() - tx - 10)
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        font = self._done_font if done else self._title_font
        fm = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(UI_MUTED if done else UI_TEXT)
        painter.drawText(QPointF(tx, card.top() + 11 + fm.ascent()),
                         fm.elidedText(title, Qt.TextElideMode.ElideRight, int(tw)))

        # Status pill ("Pending" amber / "Done" mint) + creation time
        pill_txt = "Done" if done else "Pending"
        pfm = QFontMetrics(self._pill_font)
        pw = pfm.horizontalAdvance(pill_txt) + 16
        py = card.top() + 33
        pill = QRectF(tx, py, pw, 17)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(UI_MINT_SOFT if done else UI_AMBER_SOFT)
        painter.drawRoundedRect(pill, 8.5, 8.5)
        painter.setFont(self._pill_font)
        painter.setPen(UI_MINT_TEXT if done else UI_AMBER_TEXT)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, pill_txt)

        time_txt = index.data(ROLE_DATE) or ""
        if time_txt:
            painter.setFont(self._time_font)
            painter.setPen(QColor("#8E96AA"))
            painter.drawText(QRectF(pill.right() + 10, py, max(0.0, card.right() - pill.right() - 16), 17),
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


class _DeleteAwareList(QListWidget):
    delete_pressed = pyqtSignal()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
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
class TasksDialog(FluidPanel):
    FILTERS = (("All", "all"), ("Pending", "todo"), ("Done", "done"))
    open_settings = pyqtSignal()
    tasks_changed = pyqtSignal()

    def __init__(self, store: TaskStore) -> None:
        super().__init__(QSize(500, 600))
        self.store = store
        self._filter = "all"

        # Top navigation bar
        topbar = _make_card("topbar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(12, 8, 10, 8)
        tb.setSpacing(6)
        av = QLabel()
        av.setPixmap(_morgana_avatar(30))
        tb.addWidget(av)
        brand = QLabel("Morgana")
        brand.setFont(ui_font(13, bold=True))
        tb.addWidget(brand)
        tb.addStretch(1)
        self._chips: dict[str, FluidButton] = {}
        for label, key in self.FILTERS:
            chip = FluidButton(label, variant="ghost", radius=14.0, px=12)
            chip.clicked.connect(lambda _checked=False, k=key: self._set_filter(k))
            self._chips[key] = chip
            tb.addWidget(chip)
        self._chips["all"].set_active(True)
        tb.addStretch(1)
        set_btn = FluidButton("Setting", variant="amber", icon="gear", radius=14.0, px=12)
        set_btn.clicked.connect(self.open_settings.emit)
        tb.addWidget(set_btn)
        close_btn = FluidButton("", variant="ghost", icon="close", radius=14.0)
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close_animated)
        tb.addWidget(close_btn)
        self.root.addWidget(topbar)

        # Main card
        card = _make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        head = QHBoxLayout()
        ic = _BadgeIcon("done", 28)
        head.addWidget(ic)
        title = QLabel("To-do list")
        title.setObjectName("heading")
        head.addWidget(title)
        head.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("subhead")
        head.addWidget(self.status)
        lay.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        lay.addWidget(self.progress)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a new task and press Enter...")
        self.input.returnPressed.connect(self._add)
        add_btn = FluidButton("Add", variant="primary", icon="plus", radius=10.0, px=12)
        add_btn.clicked.connect(self._add)
        row.addWidget(self.input, 1)
        row.addWidget(add_btn)
        lay.addLayout(row)

        self.list = _DeleteAwareList()
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        self._delegate = TaskCardDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.delete_pressed.connect(self._delete_selected)
        lay.addWidget(self.list, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        tip = QLabel("Double-click to rename · Del to remove")
        tip.setObjectName("tip")
        foot.addWidget(tip, 1)
        del_btn = FluidButton("Delete", variant="danger", radius=10.0, px=11)
        del_btn.clicked.connect(self._delete_selected)
        clear_btn = FluidButton("Clear done", variant="soft", radius=10.0, px=11)
        clear_btn.clicked.connect(self._clear_done)
        foot.addWidget(del_btn)
        foot.addWidget(clear_btn)
        lay.addLayout(foot)
        self.root.addWidget(card, 1)

        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(420)
        self._resort_timer.timeout.connect(self.refresh)

    def on_shown(self) -> None:
        QTimer.singleShot(0, self.input.setFocus)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, chip in self._chips.items():
            chip.set_active(k == key)
        self.refresh()

    def _update_status(self, extra: str = "") -> None:
        tasks = self.store.all()
        done = sum(1 for t in tasks if t.get("done"))
        self.progress.setRange(0, max(len(tasks), 1))
        self.progress.setValue(done)
        text = f"{len(tasks) - done} pending · {done} done"
        self.status.setText(f"{text}  {extra}".strip())

    def refresh(self) -> None:
        tasks = self.store.all()
        pending = [t for t in tasks if not t.get("done")]
        finished = [t for t in tasks if t.get("done")]
        shown = {"all": pending + finished, "todo": pending, "done": finished}[self._filter]
        self.list.blockSignals(True)
        self.list.clear()
        if not shown:
            if not tasks:
                msg = "No tasks yet.\nType one above and press Enter."
            elif self._filter == "todo":
                msg = "All tasks completed! Nice work."
            else:
                msg = "No completed tasks yet."
            self.list.addItem(_hint_item(msg))
        for t in shown:
            item = QListWidgetItem(t.get("title", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsEditable)
            item.setData(ROLE_ID, t["id"])
            item.setData(ROLE_DONE, bool(t.get("done")))
            item.setData(ROLE_DATE, _fmt_time_12h(t.get("created")))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_status()

    def _add(self) -> None:
        task = self.store.add(self.input.text())
        if task is None:
            return
        self.input.clear()
        if self._filter == "done":
            self._set_filter("all")
        else:
            self.refresh()
        for i in range(self.list.count()):
            if self.list.item(i).data(ROLE_ID) == task["id"]:
                self.list.scrollToItem(self.list.item(i))
                break
        if self.store.last_error:
            self._update_status("(save failed)")
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
                self._delegate.start_check_anim(task_id)
            else:
                self._delegate.clear_anim(task_id)
            self._resort_timer.start()
        self._update_status()
        self.tasks_changed.emit()

    def _delete_selected(self) -> None:
        ids = [i.data(ROLE_ID) for i in self.list.selectedItems() if i.data(ROLE_ID)]
        for task_id in ids:
            self.store.delete(task_id)
        if ids:
            self.refresh()
            self.tasks_changed.emit()

    def _clear_done(self) -> None:
        removed = self.store.clear_done()
        self.refresh()
        if removed:
            self._update_status(f"({removed} cleared)")
            self.tasks_changed.emit()


# ---------------------------------------------------------------------------
# NOTES WORKSPACE DIALOG (2-column layout matching reference screenshot)
# ---------------------------------------------------------------------------
class NotesDialog(FluidPanel):
    TABS = (("All", "all"), ("Projects", "Projects"), ("Meeting", "Meeting"),
            ("Personal", "Personal"), ("Reminders", "reminders"))
    open_settings = pyqtSignal()
    open_tasks = pyqtSignal()
    tasks_changed = pyqtSignal()

    def __init__(self, store: NoteStore, task_store: TaskStore | None = None) -> None:
        super().__init__(QSize(800, 600))
        self.store = store
        self.task_store = task_store
        self._current_id: str | None = None
        self._category = "Personal"
        self._tab = "all"
        self._sort_recent = False
        self._loading = False
        self._dirty = False
        self._confirm_delete = False

        # 1) Top navigation bar (avatar + category pills + '+' + amber Setting + close)
        topbar = _make_card("topbar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(12, 8, 10, 8)
        tb.setSpacing(6)
        av = QLabel()
        av.setPixmap(_morgana_avatar(30))
        tb.addWidget(av)
        brand = QLabel("Morgana")
        brand.setFont(ui_font(13, bold=True))
        tb.addWidget(brand)
        tb.addStretch(1)

        self._tabs: dict[str, FluidButton] = {}
        for label, key in self.TABS:
            btn = FluidButton(label, variant="ghost", radius=14.0, px=12)
            btn.clicked.connect(lambda _checked=False, k=key: self._set_tab(k))
            self._tabs[key] = btn
            tb.addWidget(btn)
        self._tabs["all"].set_active(True)

        plus_btn = FluidButton("", variant="soft", icon="plus", radius=14.0)
        plus_btn.setFixedSize(30, 30)
        plus_btn.setToolTip("New note")
        plus_btn.clicked.connect(self._new)
        tb.addWidget(plus_btn)
        tb.addStretch(1)

        set_btn = FluidButton("Setting", variant="amber", icon="gear", radius=14.0, px=12)
        set_btn.clicked.connect(self.open_settings.emit)
        tb.addWidget(set_btn)

        close_btn = FluidButton("", variant="ghost", icon="close", radius=14.0)
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close_animated)
        tb.addWidget(close_btn)
        self.root.addWidget(topbar)

        # 2) Two-column body
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # -- Left column: All Notes card --
        left_card = _make_card()
        left_card.setFixedWidth(306)
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.setSpacing(8)

        lh = QHBoxLayout()
        lh.setSpacing(8)
        lh.addWidget(_BadgeIcon("Personal", 26))
        ltitle = QLabel("All Notes")
        ltitle.setObjectName("heading")
        lh.addWidget(ltitle)
        lh.addStretch(1)
        ll.addLayout(lh)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search notes...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh)
        ll.addWidget(self.search_edit)

        meta_row = QHBoxLayout()
        self.count_label = QLabel("0 Notes")
        self.count_label.setObjectName("subhead")
        meta_row.addWidget(self.count_label)
        meta_row.addStretch(1)
        self._sort_btn = FluidButton("", variant="ghost", icon="sort", radius=8.0)
        self._sort_btn.setFixedSize(28, 26)
        self._sort_btn.setToolTip("Sort: Deadlines first (click to toggle)")
        self._sort_btn.clicked.connect(self._toggle_sort)
        meta_row.addWidget(self._sort_btn)
        ll.addLayout(meta_row)

        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._delegate = NoteCardDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemChanged.connect(self._on_item_changed)
        ll.addWidget(self.list, 1)
        cols.addWidget(left_card)

        # -- Right column: Note editor card + Compact To-do list card --
        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        editor_card = _make_card()
        el = QVBoxLayout(editor_card)
        el.setContentsMargins(16, 14, 16, 14)
        el.setSpacing(8)

        # Editor header
        eh = QHBoxLayout()
        eh.setSpacing(8)
        self._editor_icon = _BadgeIcon("Personal", 30)
        eh.addWidget(self._editor_icon)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("titleInput")
        self.title_edit.setPlaceholderText("Untitled note...")
        self.title_edit.textChanged.connect(self._mark_dirty)
        eh.addWidget(self.title_edit, 1)

        self._done_btn = FluidButton("Mark done", variant="soft", icon="check", radius=12.0, px=11)
        self._done_btn.setEnabled(False)
        self._done_btn.clicked.connect(self._toggle_done)
        eh.addWidget(self._done_btn)
        el.addLayout(eh)

        # Compact meta & reminder strip
        mr = QHBoxLayout()
        mr.setSpacing(6)
        self._cat_btn = FluidButton("Personal", variant="soft", radius=10.0, px=11)
        self._cat_btn.setFixedHeight(24)
        self._cat_btn.setToolTip("Click to switch category")
        self._cat_btn.clicked.connect(self._cycle_category)
        mr.addWidget(self._cat_btn)

        self._read_pill = QLabel("Note")
        self._read_pill.setStyleSheet(
            "background-color: #EEF1FF; color: #5E74E6; border-radius: 9px;"
            "padding: 3px 9px; font-size: 11px; font-weight: bold;"
        )
        mr.addWidget(self._read_pill)

        self._rem_toggle = FluidButton("Add reminder", variant="ghost", icon="clock", radius=10.0, px=11)
        self._rem_toggle.clicked.connect(self._toggle_reminder_bar)
        mr.addWidget(self._rem_toggle)
        mr.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("tip")
        mr.addWidget(self.status)
        el.addLayout(mr)

        # Collapsible deadline bar (shown only when adding/editing a reminder)
        self.deadline_box = _make_card("subcard")
        db = QVBoxLayout(self.deadline_box)
        db.setContentsMargins(10, 8, 10, 8)
        db.setSpacing(6)
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.deadline_check = FluidCheckBox("Remind me")
        self.deadline_check.toggled.connect(self._on_deadline_toggled)
        row1.addWidget(self.deadline_check)
        for text, kind in (("+1h", "1h"), ("+3h", "3h"), ("Tomorrow 9:00", "tomorrow")):
            pb = FluidButton(text, variant="soft", radius=8.0, px=11)
            pb.clicked.connect(lambda _checked=False, k=kind: self._apply_preset(k))
            row1.addWidget(pb)
        row1.addStretch(1)
        db.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.deadline_edit = QDateTimeEdit()
        self.deadline_edit.setCalendarPopup(True)
        self.deadline_edit.setDisplayFormat("dd MMM yyyy   HH:mm")
        self.deadline_edit.setDateTime(self._default_deadline())
        self.deadline_edit.dateTimeChanged.connect(self._on_deadline_changed)
        row2.addWidget(self.deadline_edit)
        self.deadline_hint = QLabel("")
        self.deadline_hint.setObjectName("tip")
        row2.addWidget(self.deadline_hint, 1)
        db.addLayout(row2)
        self.deadline_box.setVisible(False)
        el.addWidget(self.deadline_box)

        # Note content editor
        self.content_edit = QTextEdit()
        self.content_edit.setObjectName("noteBody")
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setPlaceholderText("Write your thoughts, meeting notes, or reminders here...")
        self.content_edit.textChanged.connect(self._on_content_changed)
        el.addWidget(self.content_edit, 1)

        # Bottom toolbar pill + Save button (mirrors reference screenshot)
        bf = QHBoxLayout()
        bf.setSpacing(8)
        pill = _make_card("toolbarPill")
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(6, 4, 6, 4)
        pl.setSpacing(2)
        for label, tip, prefix in (
            ("•", "Insert bullet point", "• "),
            ("☐", "Insert checklist item", "[ ] "),
            ("—", "Insert divider", "\n---\n"),
        ):
            tb_btn = FluidButton(label, variant="ghost", radius=7.0, px=12)
            tb_btn.setFixedSize(30, 26)
            tb_btn.setToolTip(tip)
            tb_btn.clicked.connect(lambda _c=False, pr=prefix: self._insert_snippet(pr))
            pl.addWidget(tb_btn)

        self._del_btn = FluidButton("", variant="ghost", icon="trash", radius=7.0, px=11)
        self._del_btn.setFixedHeight(26)
        self._del_btn.setToolTip("Delete note")
        self._del_btn.clicked.connect(self._delete)
        pl.addWidget(self._del_btn)
        bf.addWidget(pill)
        bf.addStretch(1)

        save_btn = FluidButton("Save", variant="primary", radius=10.0, px=12)
        save_btn.setMinimumWidth(84)
        save_btn.clicked.connect(self._save)
        bf.addWidget(save_btn)
        el.addLayout(bf)
        right_col.addWidget(editor_card, 3)

        # -- Bottom-right card: Quick To-do list (just like reference screenshot) --
        self._todo_list: QListWidget | None = None
        self._todo_input: QLineEdit | None = None
        self._todo_delegate: TaskCardDelegate | None = None
        if self.task_store is not None:
            todo_card = _make_card()
            todo_card.setMaximumHeight(205)
            tl = QVBoxLayout(todo_card)
            tl.setContentsMargins(16, 12, 16, 12)
            tl.setSpacing(6)

            th = QHBoxLayout()
            th.setSpacing(8)
            th.addWidget(_BadgeIcon("done", 24))
            ttitle = QLabel("To-do list")
            ttitle.setObjectName("heading")
            th.addWidget(ttitle)
            th.addStretch(1)
            view_all = FluidButton("Open all", variant="ghost", radius=8.0, px=11)
            view_all.clicked.connect(self.open_tasks.emit)
            th.addWidget(view_all)
            tl.addLayout(th)

            self._todo_list = QListWidget()
            self._todo_list.setMouseTracking(True)
            self._todo_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self._todo_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._todo_delegate = TaskCardDelegate(self._todo_list, compact=True)
            self._todo_list.setItemDelegate(self._todo_delegate)
            self._todo_list.itemChanged.connect(self._on_mini_task_changed)
            tl.addWidget(self._todo_list, 1)

            qrow = QHBoxLayout()
            qrow.setSpacing(6)
            self._todo_input = QLineEdit()
            self._todo_input.setObjectName("searchInput")
            self._todo_input.setPlaceholderText("Add a quick to-do and press Enter...")
            self._todo_input.returnPressed.connect(self._add_mini_task)
            qrow.addWidget(self._todo_input, 1)
            qadd = FluidButton("", variant="primary", icon="plus", radius=9.0)
            qadd.setFixedSize(28, 28)
            qadd.clicked.connect(self._add_mini_task)
            qrow.addWidget(qadd)
            tl.addLayout(qrow)
            right_col.addWidget(todo_card, 2)

        cols.addLayout(right_col, 1)
        self.root.addLayout(cols, 1)

        save_key = QShortcut(QKeySequence("Ctrl+S"), self)
        save_key.activated.connect(self._save)

        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(420)
        self._resort_timer.timeout.connect(self.refresh)
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(2500)
        self._confirm_timer.timeout.connect(self._reset_delete_confirm)

    # -- panel hooks -------------------------------------------------------------
    def on_shown(self) -> None:
        if self._current_id is None:
            notes = self.store.all()
            if notes:
                self._load(notes[0])
                self.refresh()
            else:
                QTimer.singleShot(0, self.title_edit.setFocus)

    def on_closing(self) -> None:
        self._autosave_if_dirty()
        self.refresh()

    # -- filters & helpers -------------------------------------------------------
    @staticmethod
    def _default_deadline() -> QDateTime:
        return _to_qdt(datetime.now() + timedelta(hours=1))

    def _set_tab(self, key: str) -> None:
        self._tab = key
        for k, btn in self._tabs.items():
            btn.set_active(k == key)
        self.refresh()

    def _toggle_sort(self) -> None:
        self._sort_recent = not self._sort_recent
        self._sort_btn.setToolTip(
            "Sort: Recently updated (click to toggle)" if self._sort_recent
            else "Sort: Deadlines first (click to toggle)"
        )
        self.refresh()

    def _cycle_category(self) -> None:
        idx = NOTE_CATEGORIES.index(self._category) if self._category in NOTE_CATEGORIES else 0
        self._category = NOTE_CATEGORIES[(idx + 1) % len(NOTE_CATEGORIES)]
        self._sync_category_ui()
        self._mark_dirty()

    def _sync_category_ui(self) -> None:
        self._cat_btn.setText(self._category)
        self._editor_icon.set_category(self._category)

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

    def _on_content_changed(self) -> None:
        self._read_pill.setText(_read_time_label(self.content_edit.toPlainText()))
        self._mark_dirty()

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status.setText("Unsaved changes (Ctrl+S)")

    def _on_deadline_toggled(self, on: bool) -> None:
        self._update_hint()
        self._mark_dirty()

    def _on_deadline_changed(self, *_args) -> None:
        self._update_hint()
        self._mark_dirty()

    def _update_hint(self) -> None:
        if not self.deadline_check.isChecked():
            self.deadline_hint.setText("No reminder set")
            self._rem_toggle.setText("Add reminder")
            return
        dt = _from_qdt(self.deadline_edit.dateTime())
        delta = dt - datetime.now()
        if delta.total_seconds() > 0:
            self.deadline_hint.setText(f"In {human_delta(delta)}")
            self._rem_toggle.setText(f"Due {dt:%d %b %H:%M}")
        else:
            self.deadline_hint.setText("This time has already passed")
            self._rem_toggle.setText(f"Overdue · {dt:%d %b %H:%M}")

    def _apply_preset(self, kind: str) -> None:
        now = datetime.now()
        if kind == "1h":
            target = now + timedelta(hours=1)
        elif kind == "3h":
            target = now + timedelta(hours=3)
        else:
            target = (now + timedelta(days=1)).replace(hour=9, minute=0)
        self.deadline_check.setChecked(True)
        self.deadline_edit.setDateTime(_to_qdt(target))

    # -- note list ---------------------------------------------------------------
    @staticmethod
    def _card_meta(note: dict) -> tuple[str, str, str, str]:
        """Return (preview, badge_text, date_text, visual_cat)."""
        cat = note.get("category") or "Personal"
        preview = " ".join((note.get("content") or "").split())[:95] or "No content yet."
        date_txt = _fmt_date(note.get("updated") or note.get("created"))
        if note.get("done"):
            return preview, "Done", date_txt, "done"
        dl = parse_dt(note.get("deadline"))
        if dl is not None:
            delta = dl - datetime.now()
            if delta.total_seconds() > 0:
                return preview, f"In {human_delta(delta)}", f"{dl:%d %b %H:%M}", cat
            return preview, "Overdue", f"{dl:%d %b %H:%M}", "overdue"
        return preview, _read_time_label(note.get("content", "")), date_txt, cat

    def refresh(self) -> None:
        far = datetime.max
        query = self.search_edit.text().strip().lower()

        def matches(n: dict) -> bool:
            if self._tab == "reminders":
                if not n.get("deadline") or n.get("done"):
                    return False
            elif self._tab != "all":
                if (n.get("category") or "Personal") != self._tab:
                    return False
            if query:
                blob = f"{n.get('title', '')} {n.get('content', '')}".lower()
                return query in blob
            return True

        notes = [n for n in self.store.all() if matches(n)]
        notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
        if not self._sort_recent:
            def group(n: dict):
                if n.get("done"):
                    return (2, far)
                dl = parse_dt(n.get("deadline"))
                return (0, dl) if dl else (1, far)
            notes.sort(key=group)

        self.count_label.setText(f"{len(notes)} {'Note' if len(notes) == 1 else 'Notes'}")
        self.list.blockSignals(True)
        self.list.clear()
        if not notes:
            msg = ("No matching notes." if (query or self._tab != "all")
                   else "No notes yet.\nClick + or write on the right to start.")
            self.list.addItem(_hint_item(msg))
        for n in notes:
            preview, badge, date_txt, vcat = self._card_meta(n)
            item = QListWidgetItem(n.get("title") or "(Untitled)")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(ROLE_ID, n["id"])
            item.setData(ROLE_DONE, bool(n.get("done")))
            item.setData(ROLE_PREVIEW, preview)
            item.setData(ROLE_BADGE, badge)
            item.setData(ROLE_DATE, date_txt)
            item.setData(ROLE_CAT, vcat)
            self.list.addItem(item)
            if n["id"] == self._current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        """Refresh the compact To-do list card at the bottom-right."""
        if self.task_store is None or self._todo_list is None:
            return
        tasks = self.task_store.all()
        pending = [t for t in tasks if not t.get("done")]
        shown = pending if pending else tasks[-3:]
        self._todo_list.blockSignals(True)
        self._todo_list.clear()
        if not shown:
            self._todo_list.addItem(_hint_item("All clear! Add a quick task below."))
        for t in shown:
            item = QListWidgetItem(t.get("title", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(ROLE_ID, t["id"])
            item.setData(ROLE_DONE, bool(t.get("done")))
            item.setData(ROLE_DATE, _fmt_time_12h(t.get("created")))
            self._todo_list.addItem(item)
        self._todo_list.blockSignals(False)

    def _on_mini_task_changed(self, item: QListWidgetItem) -> None:
        if self.task_store is None:
            return
        tid = item.data(ROLE_ID)
        task = self.task_store.get(tid) if tid else None
        done = bool(item.data(ROLE_DONE))
        if task is None or bool(task.get("done")) == done:
            return
        self.task_store.set_done(tid, done)
        if self._todo_delegate is not None:
            if done:
                self._todo_delegate.start_check_anim(tid)
            else:
                self._todo_delegate.clear_anim(tid)
        self.tasks_changed.emit()
        QTimer.singleShot(420, self.refresh_tasks)

    def _add_mini_task(self) -> None:
        if self.task_store is None or self._todo_input is None:
            return
        task = self.task_store.add(self._todo_input.text())
        if task is None:
            return
        self._todo_input.clear()
        self.refresh_tasks()
        self.tasks_changed.emit()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        note_id = item.data(ROLE_ID)
        note = self.store.get(note_id) if note_id else None
        done = bool(item.data(ROLE_DONE))
        if note is None or bool(note.get("done")) == done:
            return
        self.store.set_done(note_id, done)
        if done:
            self._delegate.start_check_anim(note_id)
        else:
            self._delegate.clear_anim(note_id)
        if note_id == self._current_id:
            self._sync_done_btn(done)
        self._resort_timer.start()

    def _sync_done_btn(self, done: bool) -> None:
        self._done_btn.set_variant("mint" if done else "soft")
        self._done_btn.setText("Completed" if done else "Mark done")

    def _toggle_done(self) -> None:
        if self._loading or not self._current_id:
            return
        note = self.store.get(self._current_id)
        if note is None:
            return
        done = not bool(note.get("done"))
        self.store.set_done(self._current_id, done)
        self._sync_done_btn(done)
        if done:
            self._delegate.start_check_anim(self._current_id)
        self.refresh()

    # -- editor load / new -------------------------------------------------------
    def _on_select(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        note_id = items[0].data(ROLE_ID)
        if note_id is None or note_id == self._current_id:
            return
        self._autosave_if_dirty()
        note = self.store.get(note_id)
        if note is not None:
            self._load(note)
        QTimer.singleShot(0, self.refresh)

    def _load(self, note: dict) -> None:
        self._loading = True
        self._reset_delete_confirm()
        self._current_id = note["id"]
        self._category = note.get("category") or "Personal"
        self._sync_category_ui()
        self.title_edit.setText(note.get("title", ""))
        self.title_edit.setCursorPosition(0)
        self.content_edit.setPlainText(note.get("content", ""))
        self._read_pill.setText(_read_time_label(note.get("content", "")))
        dl = parse_dt(note.get("deadline"))
        self.deadline_check.setChecked(dl is not None)
        self.deadline_edit.setDateTime(_to_qdt(dl) if dl else self._default_deadline())
        self.deadline_box.setVisible(False)
        self._done_btn.setEnabled(True)
        self._sync_done_btn(bool(note.get("done")))
        self._loading = False
        self._dirty = False
        self._update_hint()
        self.status.setText("")

    def _new(self) -> None:
        self._autosave_if_dirty()
        self._loading = True
        self._reset_delete_confirm()
        self._current_id = None
        self._category = self._tab if self._tab in NOTE_CATEGORIES else "Personal"
        self._sync_category_ui()
        self.title_edit.clear()
        self.content_edit.clear()
        self._read_pill.setText("Note")
        self.deadline_check.setChecked(self._tab == "reminders")
        self.deadline_edit.setDateTime(self._default_deadline())
        self.deadline_box.setVisible(self._tab == "reminders")
        self._done_btn.setEnabled(False)
        self._sync_done_btn(False)
        self._loading = False
        self._dirty = False
        self._update_hint()
        self.list.clearSelection()
        self.refresh()
        self.status.setText("")
        self.title_edit.setFocus()

    # -- save / delete -----------------------------------------------------------
    def _autosave_if_dirty(self) -> None:
        if self._dirty:
            self._save(silent=True)

    def _save(self, silent: bool = False) -> bool:
        silent = silent is True
        title = self.title_edit.text().strip()
        content = self.content_edit.toPlainText().strip()
        if not title and not content:
            if not silent:
                self.status.setText("Enter a title or some content first.")
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
        self._done_btn.setEnabled(self._current_id is not None)
        self._sync_done_btn(bool(note.get("done")) if note else False)
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

    def _reset_delete_confirm(self) -> None:
        self._confirm_delete = False
        self._confirm_timer.stop()
        self._del_btn.set_variant("ghost")
        self._del_btn.setText("")

    def _delete(self) -> None:
        if not self._current_id:
            self.status.setText("Select a note first.")
            return
        if not self._confirm_delete:
            self._confirm_delete = True
            self._del_btn.set_variant("danger")
            self._del_btn.setText("Confirm?")
            self._confirm_timer.start()
            return
        self._reset_delete_confirm()
        self.store.delete(self._current_id)
        self._dirty = False
        self._new()
        self.status.setText("Note deleted.")


# ---------------------------------------------------------------------------
# SETTINGS DIALOG
# ---------------------------------------------------------------------------
class SettingsDialog(FluidPanel):
    saved = pyqtSignal()

    def __init__(self, store: SettingsStore) -> None:
        super().__init__(QSize(460, 570))
        self.store = store

        # Top bar
        topbar = _make_card("topbar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(12, 8, 10, 8)
        tb.setSpacing(8)
        tb.addWidget(_BadgeIcon("Meeting", 28))
        title = QLabel("Settings")
        title.setObjectName("heading")
        tb.addWidget(title)
        tb.addStretch(1)
        close_btn = FluidButton("", variant="ghost", icon="close", radius=14.0)
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.close_animated)
        tb.addWidget(close_btn)
        self.root.addWidget(topbar)

        # Card 1: Bedtime reminder
        c1 = _make_card()
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(16, 14, 16, 14)
        l1.setSpacing(8)
        h1 = QLabel("Bedtime Reminder")
        h1.setObjectName("section")
        l1.addWidget(h1)
        self.curfew_check = FluidCheckBox("Remind me to go to sleep")
        l1.addWidget(self.curfew_check)
        self.bed_edit = QTimeEdit()
        self.bed_edit.setDisplayFormat("HH:mm")
        self._row(l1, "Bedtime starts", self.bed_edit)
        self.wake_edit = QTimeEdit()
        self.wake_edit.setDisplayFormat("HH:mm")
        self._row(l1, "Reminder stops (wake up)", self.wake_edit)
        self.curfew_interval = QSpinBox()
        self.curfew_interval.setRange(1, 120)
        self.curfew_interval.setSuffix(" min")
        self._row(l1, "Morgana speaks every", self.curfew_interval)
        t1 = QLabel("After bedtime, Morgana will keep reminding you at this interval until wake time.")
        t1.setObjectName("tip")
        t1.setWordWrap(True)
        l1.addWidget(t1)
        self.root.addWidget(c1)

        # Card 2: Event reminders
        c2 = _make_card()
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(16, 14, 16, 14)
        l2.setSpacing(8)
        h2 = QLabel("Event & Deadline Reminders")
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
        t2 = QLabel("120 min = 2 hours before an event. Applies to Google Calendar and notes with deadlines.")
        t2.setObjectName("tip")
        t2.setWordWrap(True)
        l2.addWidget(t2)
        self.root.addWidget(c2)

        self.root.addStretch(1)

        # Footer buttons
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("tip")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        reset_btn = FluidButton("Reset", variant="soft", radius=10.0, px=12)
        reset_btn.clicked.connect(self._reset)
        save_btn = FluidButton("Save", variant="primary", radius=10.0, px=12)
        save_btn.setMinimumWidth(84)
        save_btn.clicked.connect(self._save)
        foot.addWidget(reset_btn)
        foot.addWidget(save_btn)
        self.root.addLayout(foot)

    @staticmethod
    def _row(layout: QVBoxLayout, text: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #363E52; font-size: 13px;")
        row.addWidget(lbl, 1)
        widget.setMinimumWidth(112)
        widget.setMinimumHeight(32)
        row.addWidget(widget)
        layout.addLayout(row)

    def _fill(self, values: dict) -> None:
        bed = QTime.fromString(values["bedtime"], "HH:mm")
        wake = QTime.fromString(values["wake"], "HH:mm")
        self.curfew_check.setChecked(bool(values["curfew_enabled"]))
        self.bed_edit.setTime(bed)
        self.wake_edit.setTime(wake)
        self.curfew_interval.setValue(int(values["curfew_interval_min"]))
        self.event_window.setValue(int(values["event_window_min"]))
        self.event_interval.setValue(int(values["event_interval_min"]))

    def refresh(self) -> None:
        self._fill(self.store.data)
        self.status.setText("")

    def _reset(self) -> None:
        self._fill(SETTINGS_DEFAULTS)
        self.status.setText("Defaults loaded. Click Save to apply.")

    def _save(self) -> None:
        b, w = self.bed_edit.time(), self.wake_edit.time()
        self.store.set(bedtime=f"{b.hour():02d}:{b.minute():02d}",
                       wake=f"{w.hour():02d}:{w.minute():02d}",
                       curfew_enabled=self.curfew_check.isChecked(),
                       curfew_interval_min=self.curfew_interval.value(),
                       event_window_min=self.event_window.value(),
                       event_interval_min=self.event_interval.value())
        self.saved.emit()
        if self.store.last_error:
            self.status.setText("Save failed: " + self.store.last_error)
        elif b == w:
            self.status.setText("Saved, but bedtime and wake times match—bedtime reminders won't trigger.")
        else:
            self.status.setText("Saved and applied.")