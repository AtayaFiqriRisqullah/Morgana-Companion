# -*- coding: utf-8 -*-
"""Persona 5 themed popup windows for Tasks, Notes, and Settings.

Design principles:
  * Cohesive Persona 5 dark palette: Obsidian Black, Persona Crimson (#E60012),
    Crisp White, and subtle Morgana Scarf Gold (#F5C400).
  * Strict typography: heavy p5_font() for headers, badges & action buttons;
    clean upright ui_font() for note bodies, task titles, and form controls.
  * Restrained, critically-damped motion (smooth slide, hover tint, checkmark draw).
  * Hover states never alter text color.
  * Practical desktop UX: instant search, task priority, pinned notes, 1-click
    category chips, debounced autosave, keyboard shortcuts, and 5-second Undo bars.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (QDate, QDateTime, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                          QTime, QTimer, pyqtSignal)
from PyQt6.QtGui import (QColor, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF, QShortcut, QTextCursor)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDateTimeEdit, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                             QPushButton, QSpinBox, QStyle, QStyledItemDelegate, QTextEdit,
                             QTimeEdit, QVBoxLayout, QWidget)

from calendar_service import human_delta
from motion import FrameTicker, Spring, clamp
from storage_manager import (SETTINGS_DEFAULTS, NoteStore, SettingsStore, TaskStore, parse_dt)
from theme import (BLACK, DEEP_RED, DIALOG_QSS, GOLD, LIGHT_RED, P5_BG, P5_BORDER, P5_BORDER_HI,
                   P5_CARD, P5_GOLD_BG, P5_GOLD_FG, P5_HOVER, P5_MINT_BG, P5_MINT_FG, P5_MUTED,
                   P5_RED_BG, P5_RED_FG, P5_SELECTED, P5_SURFACE, P5_TEXT, RED, RED_HOVER, WHITE,
                   lerp_color, load_fonts, p5_font, pen, ui_font)

ROLE_ID = Qt.ItemDataRole.UserRole.value      # item id; None = empty-state hint row
ROLE_DONE = ROLE_ID + 1                       # bool: completed
ROLE_PREVIEW = ROLE_ID + 2                    # preview snippet (notes)
ROLE_BADGE = ROLE_ID + 3                      # status chip text ("IN 2H", "OVERDUE", "DONE")
ROLE_DATE = ROLE_ID + 4                       # date / time label
ROLE_CAT = ROLE_ID + 5                        # category / tone
ROLE_FLAG = ROLE_ID + 6                       # bool: high priority (tasks) or pinned (notes)

NOTE_CATEGORIES = ("Personal", "Work", "Study")
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

    sprite = Path(__file__).resolve().parent / "Morgana" / "b003_000_00.png"
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
    p.restore()


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
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._variant = variant
        self._base_variant = variant
        self._icon = icon
        self._radius = radius
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFont(p5_font(px + 2, italic=False) if display else ui_font(px + 1, bold=True))
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
        iw = 15 if self._icon else 0
        gap = 6 if (self._icon and self.text()) else 0
        pad = 22 if self.text() else 14
        return QSize(max(30, tw + iw + gap + pad), 30)

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
            total = 14 + 6 + tw
            x0 = r.center().x() - total / 2.0
            _draw_icon(p, self._icon, QRectF(x0, r.center().y() - 7.5, 15, 15), fg)
            p.drawText(QRectF(x0 + 20, r.top(), tw + 4, r.height()),
                       Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, txt)
        elif self._icon:
            _draw_icon(p, self._icon, QRectF(r.center().x() - 8.5, r.center().y() - 8.5, 17, 17), fg)
        else:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter.value, txt)


class P5CheckBox(QCheckBox):
    """Crisp Persona 5 dark checkbox with crimson fill and painted white checkmark."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(14, bold=True))

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        return QSize(28 + fm.horizontalAdvance(self.text()) + 6, 26)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        box = QRectF(1.0, (self.height() - 18.0) / 2.0, 18.0, 18.0)
        if self.isChecked():
            p.setPen(QPen(WHITE, 1.5))
            p.setBrush(RED)
            p.drawRoundedRect(box, 4.0, 4.0)
            c = box.center()
            p.setPen(QPen(WHITE, 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawLine(QPointF(c.x() - 4.4, c.y() + 0.4), QPointF(c.x() - 1.2, c.y() + 3.6))
            p.drawLine(QPointF(c.x() - 1.2, c.y() + 3.6), QPointF(c.x() + 4.8, c.y() - 3.8))
        else:
            p.setPen(QPen(QColor("#8E8EA0"), 1.6))
            p.setBrush(P5_SURFACE)
            p.drawRoundedRect(box, 4.0, 4.0)
        p.setFont(self.font())
        p.setPen(P5_TEXT)
        p.drawText(QRectF(27.0, 0.0, self.width() - 27.0, self.height()),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                   self.text())


# ---------------------------------------------------------------------------
# P5FluidPanel: obsidian + white-bordered Persona 5 frame with Windows-safe reveal
# ---------------------------------------------------------------------------
class P5FluidPanel(QWidget):
    M, SHADOW, HEADER, SLIDE = 10, 6, 48, 12

    def __init__(self, title: str, size: QSize) -> None:
        super().__init__()
        load_fonts()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(size)
        self.setStyleSheet(DIALOG_QSS)
        self._title = title
        self._on_top = True
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
        header_w.setFixedHeight(self.HEADER)
        self.header_bar = QHBoxLayout(header_w)
        self.header_bar.setContentsMargins(14, 6, 10, 6)
        self.header_bar.setSpacing(8)
        av = QLabel()
        av.setPixmap(_morgana_avatar(30))
        self.header_bar.addWidget(av)
        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName("heading")
        self._title_lbl.setStyleSheet(
            'color: #FFFFFF; background: transparent; font-family: "Anton", "Barlow Condensed", "Impact", sans-serif; font-size: 22px;'
        )
        self.header_bar.addWidget(self._title_lbl)
        self.header_bar.addStretch(1)
        self.outer.addWidget(header_w)

        # Main content area
        content_w = QWidget()
        self.body = QVBoxLayout(content_w)
        self.body.setContentsMargins(14, 12, 14, 14)
        self.body.setSpacing(10)
        self.outer.addWidget(content_w, 1)

    def _add_close_button(self) -> None:
        close_btn = P5FluidButton("", variant="ghost", icon="close", radius=6.0)
        close_btn.setFixedSize(30, 30)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.close_animated)
        self.header_bar.addWidget(close_btn)

    def set_on_top(self, on: bool) -> None:
        self._on_top = on
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
            QTimer.singleShot(0, self._bring_to_front)

    def _bring_to_front(self) -> None:
        if self.isVisible() and not self._closing:
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
            return False
        return moving

    def close_animated(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self.on_closing()
        self._closing = True
        self._open.configure(420.0)
        self._open.target = 0.0
        self._ticker.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        M, S, H = self.M, self.SHADOW, self.HEADER
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
        cut = min(body.width() * 0.38, 195.0) * o
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
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= self.M + self.HEADER:
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
            self._open.snap(0.0)
            self.move(self._base_pos.x(), self._base_pos.y() + self.SLIDE)
            self.show()
        else:
            self.move(self._base_pos)
            self.refresh()
        self._open.configure(340.0)
        self._open.target = 1.0
        self._ticker.start()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._bring_to_front)
        self.on_shown()


# ---------------------------------------------------------------------------
# UndoBar: non-blocking 5-second undo strip for Tasks & Notes
# ---------------------------------------------------------------------------
class UndoBar(QFrame):
    undo_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("undoBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(8)
        self._lbl = QLabel("")
        self._lbl.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: bold;")
        lay.addWidget(self._lbl, 1)
        btn = P5FluidButton("UNDO", variant="primary", display=True, px=11, radius=5.0)
        btn.setFixedHeight(26)
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

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._springs: dict[str, Spring] = {}
        self._swallow_release = False
        self._title_font = ui_font(15, bold=True)
        self._done_font = ui_font(15, bold=True)
        self._done_font.setStrikeOut(True)
        self._pill_font = p5_font(11, italic=False)
        self._time_font = ui_font(11, bold=False)
        self._hint_font = ui_font(13, bold=False)
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
        return QRectF(card.left() + 14, card.top() + 12, 20, 20)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(120, 74)
        return QSize(120, 62)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect.adjusted(46, 7, -10, -24))

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

        # Left accent bar for high priority or selection
        if high and not done:
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

        # Second line: optional HIGH chip + status chip + creation time
        py = card.top() + 33
        x_cur = tx
        pfm = QFontMetrics(self._pill_font)
        if high and not done:
            pw = pfm.horizontalAdvance("HIGH") + 14
            pill = QRectF(x_cur, py, pw, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(P5_RED_BG)
            painter.drawRoundedRect(pill, 4.0, 4.0)
            painter.setFont(self._pill_font)
            painter.setPen(P5_RED_FG)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter.value, "HIGH")
            x_cur = pill.right() + 6

        st_txt = "DONE" if done else "PENDING"
        sw = pfm.horizontalAdvance(st_txt) + 14
        spill = QRectF(x_cur, py, sw, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(P5_MINT_BG if done else P5_GOLD_BG)
        painter.drawRoundedRect(spill, 4.0, 4.0)
        painter.setFont(self._pill_font)
        painter.setPen(P5_MINT_FG if done else P5_GOLD_FG)
        painter.drawText(spill, Qt.AlignmentFlag.AlignCenter.value, st_txt)
        x_cur = spill.right() + 8

        time_txt = index.data(ROLE_DATE) or ""
        if time_txt:
            painter.setFont(self._time_font)
            painter.setPen(P5_MUTED)
            painter.drawText(QRectF(x_cur, py, max(0.0, card.right() - x_cur - 8), 16),
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
    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._title_font = ui_font(15, bold=True)
        self._done_font = ui_font(15, bold=True)
        self._done_font.setStrikeOut(True)
        self._preview_font = ui_font(12, bold=False)
        self._chip_font = p5_font(11, italic=False)
        self._date_font = ui_font(11, bold=False)
        self._hint_font = ui_font(13, bold=False)

    def sizeHint(self, option, index) -> QSize:
        if index.data(ROLE_ID) is None:
            return QSize(180, 90)
        return QSize(180, 94)

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
        y_p = card.top() + 28 + pfm.ascent()
        painter.drawText(QPointF(tx, y_p), pfm.elidedText(line1, Qt.TextElideMode.ElideRight, max_w))
        if rest:
            painter.drawText(QPointF(tx, y_p + pfm.height() + 1),
                             pfm.elidedText(" ".join(rest), Qt.TextElideMode.ElideRight, max_w))

        # Bottom strip: category chip + status badge + date
        by = card.bottom() - 21
        x_cur = tx
        cfm = QFontMetrics(self._chip_font)
        cat = (index.data(ROLE_CAT) or "Personal").upper()
        cw = cfm.horizontalAdvance(cat) + 12
        cpill = QRectF(x_cur, by, cw, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#242430"))
        painter.drawRoundedRect(cpill, 4.0, 4.0)
        painter.setFont(self._chip_font)
        painter.setPen(QColor("#C8C8D6"))
        painter.drawText(cpill, Qt.AlignmentFlag.AlignCenter.value, cat)
        x_cur = cpill.right() + 5

        badge = index.data(ROLE_BADGE) or ""
        if badge:
            bw = cfm.horizontalAdvance(badge) + 12
            bpill = QRectF(x_cur, by, bw, 16)
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
            painter.drawText(QRectF(x_cur, by, max(0.0, card.right() - x_cur - 8), 16),
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
    FILTERS = (("ALL", "all"), ("PENDING", "todo"), ("HIGH", "high"), ("DONE", "done"))
    open_settings = pyqtSignal()
    open_notes = pyqtSignal()
    tasks_changed = pyqtSignal()

    def __init__(self, store: TaskStore) -> None:
        super().__init__("TASKS", QSize(500, 610))
        self.store = store
        self._filter = "all"
        self._new_high = False
        self._undo_items: list[dict] = []

        # Header quick actions
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("subhead")
        self.header_bar.addWidget(self._count_lbl)

        notes_btn = P5FluidButton("NOTES", variant="soft", display=True, px=10)
        notes_btn.clicked.connect(lambda _c=False: self.open_notes.emit())
        self.header_bar.addWidget(notes_btn)

        set_btn = P5FluidButton("", variant="soft", icon="gear")
        set_btn.setFixedSize(30, 30)
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.body.addWidget(self.progress)

        # Quick-add bar + High-priority toggle
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a task and press Enter...")
        self.input.returnPressed.connect(self._add)
        add_row.addWidget(self.input, 1)

        self._prio_btn = P5FluidButton("HIGH", variant="soft", icon="bolt", display=True, px=10)
        self._prio_btn.setToolTip("Mark new task as high priority")
        self._prio_btn.clicked.connect(self._toggle_new_priority)
        add_row.addWidget(self._prio_btn)

        add_btn = P5FluidButton("ADD", variant="primary", icon="plus", display=True, px=11)
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        self.body.addLayout(add_row)

        # Filter chips + compact inline search
        bar = QHBoxLayout()
        bar.setSpacing(5)
        self._chips: dict[str, P5FluidButton] = {}
        for label, key in self.FILTERS:
            chip = P5FluidButton(label, variant="soft", display=True, px=10)
            chip.setFixedHeight(26)
            chip.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            self._chips[key] = chip
            bar.addWidget(chip)
        self._chips["all"].set_active(True)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaximumWidth(150)
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
        self._delegate = TaskRowDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.delete_pressed.connect(self._delete_selected)
        self.list.space_pressed.connect(self._toggle_selected_done)
        cl.addWidget(self.list)
        self.body.addWidget(card, 1)

        # Undo strip (hidden unless something was deleted)
        self._undo = UndoBar()
        self._undo.undo_clicked.connect(self._undo_delete)
        self.body.addWidget(self._undo)

        # Footer actions
        foot = QHBoxLayout()
        foot.setSpacing(6)
        tip = QLabel("Space: done · F2: rename · Del")
        tip.setObjectName("tip")
        foot.addWidget(tip, 1)

        tog_prio = P5FluidButton("PRIORITY", variant="gold", icon="bolt", display=True, px=10)
        tog_prio.setFixedHeight(28)
        tog_prio.setToolTip("Toggle High priority on selected task(s)")
        tog_prio.clicked.connect(self._toggle_selected_priority)
        foot.addWidget(tog_prio)

        del_btn = P5FluidButton("DELETE", variant="danger", display=True, px=10)
        del_btn.setFixedHeight(28)
        del_btn.clicked.connect(self._delete_selected)
        foot.addWidget(del_btn)

        clear_btn = P5FluidButton("CLEAR DONE", variant="soft", display=True, px=10)
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._clear_done)
        foot.addWidget(clear_btn)
        self.body.addLayout(foot)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.search_edit.setFocus)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(lambda: self.open_settings.emit())

        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(420)
        self._resort_timer.timeout.connect(self.refresh)

    def on_shown(self) -> None:
        QTimer.singleShot(20, self.input.setFocus)

    def _toggle_new_priority(self) -> None:
        self._new_high = not self._new_high
        self._prio_btn.set_active(self._new_high, active_variant="gold")

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

    def refresh(self) -> None:
        query = self.search_edit.text().strip().lower()
        tasks = self.store.all()
        pending = [t for t in tasks if not t.get("done")]
        pending.sort(key=lambda t: (0 if t.get("priority") == "high" else 1))
        finished = [t for t in tasks if t.get("done")]
        pools = {
            "all": pending + finished,
            "todo": pending,
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
            elif self._filter == "high":
                msg = "No high-priority tasks right now."
            else:
                msg = "No completed tasks yet."
            self.list.addItem(_hint_item(msg))
        for t in shown:
            item = QListWidgetItem(t.get("title", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsEditable)
            item.setData(ROLE_ID, t["id"])
            item.setData(ROLE_DONE, bool(t.get("done")))
            item.setData(ROLE_FLAG, t.get("priority") == "high")
            item.setData(ROLE_DATE, _fmt_time_12h(t.get("created")))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_status()

    def _add(self) -> None:
        task = self.store.add(self.input.text(), priority="high" if self._new_high else "normal")
        if task is None:
            return
        self.input.clear()
        if self._new_high:
            self._toggle_new_priority()
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
                self._delegate.start_check_anim(task_id)
            else:
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

    def __init__(self, store: NoteStore, task_store: TaskStore | None = None) -> None:
        super().__init__("NOTES", QSize(820, 600))
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
        tasks_btn = P5FluidButton("TASKS", variant="soft", display=True, px=10)
        tasks_btn.clicked.connect(lambda _c=False: self.open_tasks.emit())
        self.header_bar.addWidget(tasks_btn)

        set_btn = P5FluidButton("", variant="soft", icon="gear")
        set_btn.setFixedSize(30, 30)
        set_btn.setToolTip("Settings (Ctrl+,)")
        set_btn.clicked.connect(lambda _c=False: self.open_settings.emit())
        self.header_bar.addWidget(set_btn)
        self._add_close_button()

        # Two-column body
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # -- Left sidebar: New button, search, filters, note list --
        left_card = _make_card()
        left_card.setFixedWidth(284)
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(8)

        new_btn = P5FluidButton("NEW NOTE", variant="primary", icon="plus", display=True, px=11)
        new_btn.setFixedHeight(32)
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
            fb = P5FluidButton(label, variant="soft", display=True, px=9)
            fb.setFixedHeight(24)
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
        self._sort_btn = P5FluidButton("DEADLINES", variant="ghost", icon="sort", px=10)
        self._sort_btn.setFixedHeight(22)
        self._sort_btn.setToolTip("Switch sort order (Deadlines / Recent)")
        self._sort_btn.clicked.connect(self._toggle_sort)
        mrow.addWidget(self._sort_btn)
        ll.addLayout(mrow)

        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._delegate = NoteCardDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.itemSelectionChanged.connect(self._on_select)
        ll.addWidget(self.list, 1)
        cols.addWidget(left_card)

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

        self._pin_btn = P5FluidButton("", variant="soft", icon="pin")
        self._pin_btn.setFixedSize(30, 30)
        self._pin_btn.setToolTip("Pin note to top")
        self._pin_btn.setEnabled(False)
        self._pin_btn.clicked.connect(self._toggle_pin)
        eh.addWidget(self._pin_btn)

        self._done_btn = P5FluidButton("DONE", variant="soft", icon="check", display=True, px=10)
        self._done_btn.setFixedHeight(30)
        self._done_btn.setEnabled(False)
        self._done_btn.clicked.connect(self._toggle_done)
        eh.addWidget(self._done_btn)

        self._del_btn = P5FluidButton("", variant="danger", icon="trash")
        self._del_btn.setFixedSize(30, 30)
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
            cb = P5FluidButton(cat.upper(), variant="soft", display=True, px=9)
            cb.setFixedHeight(24)
            cb.clicked.connect(lambda _c=False, c=cat: self._set_category(c))
            self._cat_btns[cat] = cb
            meta.addWidget(cb)
        self._sync_category_ui()

        self._rem_btn = P5FluidButton("REMINDER", variant="soft", icon="clock", display=True, px=9)
        self._rem_btn.setFixedHeight(24)
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
        r1.setSpacing(5)
        self.deadline_check = P5CheckBox("Remind me")
        self.deadline_check.toggled.connect(self._on_deadline_toggled)
        r1.addWidget(self.deadline_check)
        r1.addStretch(1)
        for text, kind in (("+30m", "30m"), ("+1h", "1h"), ("+3h", "3h"), ("Tomorrow 9:00", "tomorrow")):
            pb = P5FluidButton(text, variant="soft", px=11)
            pb.setFixedHeight(25)
            pb.clicked.connect(lambda _c=False, k=kind: self._apply_preset(k))
            r1.addWidget(pb)
        db.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.deadline_edit = QDateTimeEdit()
        self.deadline_edit.setCalendarPopup(True)
        self.deadline_edit.setDisplayFormat("dd MMM yyyy   HH:mm")
        self.deadline_edit.setDateTime(self._default_deadline())
        self.deadline_edit.setMinimumHeight(30)
        self.deadline_edit.dateTimeChanged.connect(self._on_deadline_changed)
        r2.addWidget(self.deadline_edit)
        self.deadline_hint = QLabel("")
        self.deadline_hint.setObjectName("tip")
        r2.addWidget(self.deadline_hint, 1)
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
        self._undo = UndoBar()
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
            tb_btn = P5FluidButton(label, variant="ghost", icon=ic, px=12)
            tb_btn.setFixedSize(30, 26)
            tb_btn.setToolTip(tip)
            tb_btn.clicked.connect(lambda _c=False, f=fn: f())
            pl.addWidget(tb_btn)
        bf.addWidget(pill)

        self.status = QLabel("")
        self.status.setObjectName("tip")
        bf.addWidget(self.status, 1)

        save_btn = P5FluidButton("SAVE", variant="primary", display=True, px=11)
        save_btn.setMinimumWidth(82)
        save_btn.clicked.connect(self._save)
        bf.addWidget(save_btn)
        el.addLayout(bf)

        cols.addWidget(editor_card, 1)
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
            self.deadline_hint.setText("No reminder set")
            self._rem_btn.set_variant("soft")
            self._rem_btn.setText("REMINDER")
            return
        dt = _from_qdt(self.deadline_edit.dateTime())
        delta = dt - datetime.now()
        if delta.total_seconds() > 0:
            self.deadline_hint.setText(f"Triggers in {human_delta(delta)}")
            self._rem_btn.set_variant("gold")
            self._rem_btn.setText(f"DUE {dt:%d %b %H:%M}".upper())
        else:
            self.deadline_hint.setText("This time has already passed")
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
        self._undo.trigger(f"Deleted '{title[:28]}'.")

    def _undo_delete(self) -> None:
        if self._undo_note is None:
            return
        restored = self.store.restore(self._undo_note)
        self._undo_note = None
        if restored is not None:
            self._load(restored)
            self.refresh()
            self.status.setText("Note restored.")


# ---------------------------------------------------------------------------
# SETTINGS DIALOG (Persona 5 dark theme, Bedtime + Reminders + Focus + Behavior)
# ---------------------------------------------------------------------------
class SettingsDialog(P5FluidPanel):
    saved = pyqtSignal()
    always_on_top_changed = pyqtSignal(bool)
    autostart_changed = pyqtSignal(bool)

    def __init__(self, store: SettingsStore, get_on_top=None, get_autostart=None) -> None:
        super().__init__("SETTINGS", QSize(480, 625))
        self.store = store
        self._get_on_top = get_on_top
        self._get_autostart = get_autostart
        self._add_close_button()

        # Card 1: Bedtime reminder
        c1 = _make_card()
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(14, 12, 14, 12)
        l1.setSpacing(7)
        h1 = QLabel("BEDTIME REMINDER")
        h1.setObjectName("section")
        l1.addWidget(h1)
        self.curfew_check = P5CheckBox("Remind me to go to sleep")
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
        self.body.addWidget(c1)

        # Card 2: Event & note reminders
        c2 = _make_card()
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(14, 12, 14, 12)
        l2.setSpacing(7)
        h2 = QLabel("EVENT & NOTE REMINDERS")
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
        self.body.addWidget(c2)

        # Card 3: Focus timer & behavior
        c3 = _make_card()
        l3 = QVBoxLayout(c3)
        l3.setContentsMargins(14, 12, 14, 12)
        l3.setSpacing(7)
        h3 = QLabel("FOCUS TIMER & APP")
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
        self.on_top_check = P5CheckBox("Keep Morgana always on top")
        self.autostart_check = P5CheckBox("Start Morgana automatically on boot")
        l3.addWidget(self.on_top_check)
        l3.addWidget(self.autostart_check)
        self.body.addWidget(c3)

        self.body.addStretch(1)

        # Footer
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.status = QLabel("")
        self.status.setObjectName("tip")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)

        reset_btn = P5FluidButton("RESET", variant="soft", display=True, px=11)
        reset_btn.clicked.connect(self._reset)
        foot.addWidget(reset_btn)

        save_btn = P5FluidButton("SAVE", variant="primary", display=True, px=11)
        save_btn.setMinimumWidth(88)
        save_btn.clicked.connect(self._save)
        foot.addWidget(save_btn)
        self.body.addLayout(foot)

        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save)

    @staticmethod
    def _row(layout: QVBoxLayout, text: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #D6D6E2; font-size: 14px;")
        row.addWidget(lbl, 1)
        widget.setMinimumWidth(112)
        widget.setMinimumHeight(30)
        row.addWidget(widget)
        layout.addLayout(row)

    def _fill(self, values: dict) -> None:
        self.curfew_check.setChecked(bool(values["curfew_enabled"]))
        self.bed_edit.setTime(QTime.fromString(values["bedtime"], "HH:mm"))
        self.wake_edit.setTime(QTime.fromString(values["wake"], "HH:mm"))
        self.curfew_interval.setValue(int(values["curfew_interval_min"]))
        self.event_window.setValue(int(values["event_window_min"]))
        self.event_interval.setValue(int(values["event_interval_min"]))
        self.focus_spin.setValue(int(values.get("focus_min", SETTINGS_DEFAULTS["focus_min"])))
        self.rest_spin.setValue(int(values.get("rest_min", SETTINGS_DEFAULTS["rest_min"])))
        if self._get_on_top is not None:
            self.on_top_check.setChecked(bool(self._get_on_top()))
        if self._get_autostart is not None:
            self.autostart_check.setChecked(bool(self._get_autostart()))

    def refresh(self) -> None:
        self._fill(self.store.data)
        self.status.setText("")

    def _reset(self) -> None:
        self._fill(SETTINGS_DEFAULTS)
        self.status.setText("Defaults loaded. Click SAVE to apply.")

    def _save(self) -> None:
        b, w = self.bed_edit.time(), self.wake_edit.time()
        self.store.set(
            bedtime=f"{b.hour():02d}:{b.minute():02d}",
            wake=f"{w.hour():02d}:{w.minute():02d}",
            curfew_enabled=self.curfew_check.isChecked(),
            curfew_interval_min=self.curfew_interval.value(),
            event_window_min=self.event_window.value(),
            event_interval_min=self.event_interval.value(),
            focus_min=self.focus_spin.value(),
            rest_min=self.rest_spin.value(),
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