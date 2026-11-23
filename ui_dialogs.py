# -*- coding: utf-8 -*-
"""Jendela popup bergaya Persona 5 (hitam-putih-merah): TASKS, NOTES, dan PENGATURAN.

Berbeda dengan karakter/balon (yang tidak mengambil fokus), jendela ini BOLEH menerima
fokus keyboard supaya bisa mengetik. Jendela dipakai ulang: tombol X hanya menyembunyikan.

Animasi (semua berbasis pegas, lihat motion.py):
  * panel muncul meluncur dari bawah sambil memudar masuk, bayangan merah "keluar",
    header merah menyapu dari kiri; tutup = kebalikannya tanpa overshoot;
  * tombol P5Button: sapuan putih diagonal saat hover, mengempis sedikit saat ditekan;
  * centang pada daftar "digambar" goresan demi goresan saat item diselesaikan.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import (QDate, QDateTime, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                          QTime, QTimer, pyqtSignal)
from PyQt6.QtGui import (QColor, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
                         QPainterPath, QPen, QPolygonF, QShortcut, QTransform)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDateTimeEdit, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                             QSizePolicy, QSpinBox, QStyle, QStyledItemDelegate, QTextEdit,
                             QTimeEdit, QVBoxLayout, QWidget)

from calendar_service import human_delta
from motion import FrameTicker, Spring, clamp
from storage_manager import (SETTINGS_DEFAULTS, NoteStore, SettingsStore, TaskStore, parse_dt)
from theme import BLACK, DIALOG_QSS, LIGHT_RED, RED, WHITE, p5_font, pen, ui_font

ROLE_ID = Qt.ItemDataRole.UserRole.value      # id item; None = baris penanda (mis. "belum ada tugas")
ROLE_DONE = ROLE_ID + 1                       # bool: sudah selesai
ROLE_SUB = ROLE_ID + 2                        # teks baris kedua (daftar catatan)
ROLE_WARN = ROLE_ID + 3                       # bool: baris kedua berwarna peringatan (deadline lewat)


def _to_qdt(dt: datetime) -> QDateTime:
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute))


def _from_qdt(q: QDateTime) -> datetime:
    d, t = q.date(), q.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())


# ---------------------------------------------------------------------------
# Tombol P5: jajargenjang merah, sapuan putih diagonal saat hover / aktif
# ---------------------------------------------------------------------------
class P5Button(QPushButton):
    SHEAR = -0.22

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)      # jangan rebut fokus dari kolom ketik
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFont(p5_font(13))
        self._hover = Spring(0.0, 360.0, 25.0)           # sedikit memantul
        self._press = Spring(0.0, 700.0, 36.0)
        self._active = Spring(0.0, 320.0, 26.0)          # tombol "terpilih" (mis. filter aktif)
        self._ticker = FrameTicker(self._frame, self)

    def set_active(self, on: bool) -> None:
        self._active.target = 1.0 if on else 0.0
        self._ticker.start()

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 44, 34)

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
        moving = self._hover.step(dt) | self._press.step(dt) | self._active.step(dt)
        self.update()
        return moving

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        hov = max(self._hover.value, self._active.value)
        wipe_amt = clamp(hov, 0.0, 1.25)
        box = QRectF(self.rect()).adjusted(7, 4, -7, -4)
        local = QRectF(-box.width() / 2, -box.height() / 2, box.width(), box.height())

        s = 1.0 + 0.05 * clamp(self._hover.value, 0.0, 1.2) - 0.07 * self._press.value
        t = QTransform()
        t.translate(box.center().x(), box.center().y())
        t.scale(s, s)
        t.shear(self.SHEAR, 0.0)
        p.setTransform(t)

        p.setPen(pen(WHITE, 2))
        p.setBrush(RED)
        p.drawRect(local)

        edge = local.left() + wipe_amt * (local.width() + 36) - 18      # tepi sapuan (miring)
        wipe = QPainterPath()
        wipe.addPolygon(QPolygonF([QPointF(local.left() - 4, local.top()),
                                   QPointF(edge + 14, local.top()),
                                   QPointF(edge - 14, local.bottom()),
                                   QPointF(local.left() - 4, local.bottom())]))
        wipe.closeSubpath()
        body = QPainterPath()
        body.addRect(local)
        wipe = wipe.intersected(body)

        if wipe_amt > 0.01:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(WHITE)
            p.drawPath(wipe)

        flags = Qt.AlignmentFlag.AlignCenter.value
        p.setFont(self.font())
        p.setClipPath(body.subtracted(wipe))               # teks putih di area merah
        p.setPen(WHITE)
        p.drawText(local, flags, self.text())
        if wipe_amt > 0.01:
            p.setClipPath(wipe)                            # teks merah di area sapuan putih
            p.setPen(RED)
            p.drawText(local, flags, self.text())


# ---------------------------------------------------------------------------
# Panel dasar: bingkai hitam + bayangan merah + header miring + animasi buka/tutup
# ---------------------------------------------------------------------------
class P5Panel(QWidget):
    M, SHADOW, HEADER, SLIDE = 10, 7, 34, 26

    def __init__(self, title: str, size: QSize) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(size)
        self.setStyleSheet(DIALOG_QSS)
        self._title = title
        self._title_font = p5_font(17)
        self._drag_offset: QPoint | None = None

        self._open = Spring(0.0, 250.0, 21.0)        # 0 = tertutup, 1 = terbuka (memantul sedikit)
        self._closing = False
        self._base_pos = QPoint()
        self._ticker = FrameTicker(self._frame, self)

        m = self.M
        self.body = QVBoxLayout(self)                # subclass mengisi layout ini
        self.body.setContentsMargins(m + 16, m + self.HEADER + 12,
                                     m + self.SHADOW + 16, m + self.SHADOW + 16)
        self.body.setSpacing(8)

        self._close_btn = P5Button("X", self)
        self._close_btn.setFixedSize(40, 26)
        self._close_btn.clicked.connect(self.close_animated)
        self._close_btn.move(size.width() - m - self.SHADOW - 46, m + (self.HEADER - 26) // 2)

    # -- animasi ------------------------------------------------------------------
    def _frame(self, dt: float) -> bool:
        moving = self._open.step(dt)
        o = self._open.value
        self.setWindowOpacity(clamp(o * 1.4))
        self.move(self._base_pos.x(), self._base_pos.y() + int(round((1.0 - clamp(o, 0.0, 1.3)) * self.SLIDE)))
        self.update()
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
        self._open.configure(340.0)                  # redaman kritis: menutup mulus tanpa memantul
        self._open.target = 0.0
        self._ticker.start()

    # -- tampilan ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        M, S, H = self.M, self.SHADOW, self.HEADER
        o = clamp(self._open.value, 0.0, 1.15)
        oc = clamp(self._open.value)
        body = QRectF(M, M, self.width() - 2 * M - S, self.height() - 2 * M - S)

        p.fillRect(body.translated(S * o, S * o), RED)          # bayangan "keluar" saat membuka
        p.fillRect(body, BLACK)
        cut = body.width() * 0.62 * o                           # header menyapu dari kiri
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(RED)
        p.drawPolygon(QPolygonF([QPointF(M, M), QPointF(M + cut, M),
                                 QPointF(M + cut - 16, M + H), QPointF(M, M + H)]))
        p.setPen(pen(RED, 2))
        p.drawLine(QPointF(M, M + H), QPointF(M + body.width() * oc, M + H))
        p.setPen(pen(WHITE, 3))                                 # bingkai putih
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(body)

        p.setOpacity(oc)
        p.setPen(WHITE)
        p.setFont(self._title_font)
        p.drawText(QRectF(M + 14 - (1.0 - oc) * 40.0, M, body.width(), H),
                   Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value,
                   self._title)

    # -- geser jendela lewat header ------------------------------------------------
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

    # -- hook untuk subclass -----------------------------------------------------
    def refresh(self) -> None:
        """Dipanggil tiap kali jendela ditampilkan; subclass memuat ulang datanya."""

    def on_shown(self) -> None:
        """Dipanggil setelah jendela tampil (mis. memfokuskan kolom input)."""

    def on_closing(self) -> None:
        """Dipanggil tepat sebelum jendela menutup (mis. simpan otomatis)."""

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()

    def show_near(self, anchor: QRect) -> None:
        if not self.isVisible():
            screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            if self.height() > area.height() - 24:             # layar pendek: jendela dipangkas
                self.setFixedSize(self.width(), max(420, area.height() - 24))
            w, h = self.width(), self.height()
            x = anchor.left() - w - 8
            if x < area.left():
                x = min(anchor.right() + 8, area.right() - w)
            y = max(area.top(), min(anchor.center().y() - h // 2, area.bottom() - h))
            self._base_pos = QPoint(x, y)
            self._open.snap(0.0)
            self.setWindowOpacity(0.0)
            self.move(x, y + self.SLIDE)
            self.show()
        self._closing = False
        self._open.configure(250.0, 21.0)
        self._open.target = 1.0
        self._ticker.start()
        self.raise_()
        self.activateWindow()
        self.on_shown()


# ---------------------------------------------------------------------------
# Daftar checklist: kotak miring + centang yang "digambar" saat item selesai
# ---------------------------------------------------------------------------
class ChecklistDelegate(QStyledItemDelegate):
    """Menggambar baris checklist ala Persona 5.

    Data item: ROLE_ID (id), ROLE_DONE (bool), teks tampilan = judul, dan opsional
    ROLE_SUB / ROLE_WARN untuk baris kedua. Klik pada kotak mengubah ROLE_DONE
    (QListWidget.itemChanged terpancar), jadi tidak perlu CheckStateRole bawaan Qt.
    """

    CHECK_POINTS = ((-6.0, 0.5), (-1.8, 5.0), (6.8, -5.6))      # goresan centang (koordinat lokal kotak)

    def __init__(self, view: QListWidget, two_line: bool = False) -> None:
        super().__init__(view)
        self._two_line = two_line
        self._springs: dict[str, Spring] = {}
        self._swallow_release = False
        self._title_font = ui_font(14)
        self._done_font = ui_font(14)
        self._done_font.setStrikeOut(True)
        self._sub_font = ui_font(11, bold=False)
        self._hint_font = ui_font(13, bold=False)
        self._hint_font.setItalic(True)
        self._ticker = FrameTicker(self._frame, self)

    # -- animasi centang -----------------------------------------------------------
    def start_check_anim(self, item_id: str) -> None:
        sp = Spring(0.0, 420.0, 24.0)
        sp.target = 1.0
        self._springs[item_id] = sp
        self._ticker.start()

    def clear_anim(self, item_id: str) -> None:
        self._springs.pop(item_id, None)

    def _frame(self, dt: float) -> bool:
        for item_id in list(self._springs):
            if not self._springs[item_id].step(dt):
                del self._springs[item_id]
        view = self.parent()
        if view is not None:
            view.viewport().update()
        return bool(self._springs)

    # -- geometri -------------------------------------------------------------------
    @staticmethod
    def _box_rect(r: QRectF) -> QRectF:
        return QRectF(r.left() + 14, r.center().y() - 11, 22, 22)

    def sizeHint(self, option, index) -> QSize:
        return QSize(120, 56 if self._two_line else 42)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect.adjusted(46, 5, -8, -5))

    # -- menggambar -----------------------------------------------------------------
    def _paint_checkbox(self, painter: QPainter, box: QRectF, done: bool, prog: float) -> None:
        painter.save()
        t = QTransform()
        t.translate(box.center().x(), box.center().y())
        t.shear(-0.18, 0.0)
        painter.setTransform(t, True)
        local = QRectF(-box.width() / 2, -box.height() / 2, box.width(), box.height())
        painter.setPen(pen(WHITE, 2))
        painter.setBrush(RED if done else BLACK)
        painter.drawRect(local)
        if done and prog > 0.01:
            scale = 1.0 + max(0.0, prog - 1.0) * 0.8                 # sedikit membesar saat overshoot
            painter.scale(scale, scale)
            pts = self.CHECK_POINTS
            seg1 = ((pts[1][0] - pts[0][0]) ** 2 + (pts[1][1] - pts[0][1]) ** 2) ** 0.5
            seg2 = ((pts[2][0] - pts[1][0]) ** 2 + (pts[2][1] - pts[1][1]) ** 2) ** 0.5
            reach = clamp(prog) * (seg1 + seg2)                        # panjang goresan yang sudah tergambar
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            if reach <= seg1:
                f = reach / seg1
                path.lineTo(pts[0][0] + (pts[1][0] - pts[0][0]) * f, pts[0][1] + (pts[1][1] - pts[0][1]) * f)
            else:
                f = (reach - seg1) / seg2
                path.lineTo(pts[1][0], pts[1][1])
                path.lineTo(pts[1][0] + (pts[2][0] - pts[1][0]) * f, pts[1][1] + (pts[2][1] - pts[1][1]) * f)
            painter.setPen(QPen(WHITE, 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.restore()

    def paint(self, painter, option, index) -> None:
        r = QRectF(option.rect)
        item_id = index.data(ROLE_ID)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        if item_id is None:                                         # baris penanda ("belum ada ...")
            painter.setPen(QColor("#8A8A8A"))
            painter.setFont(self._hint_font)
            painter.drawText(r.adjusted(14, 0, -14, 0),
                             Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value,
                             index.data(Qt.ItemDataRole.DisplayRole) or "")
            painter.restore()
            return

        done = bool(index.data(ROLE_DONE))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.fillRect(r, RED)
            painter.fillRect(QRectF(r.left(), r.top(), 4, r.height()), WHITE)
        elif hovered:
            painter.fillRect(r, QColor("#3A0008"))
        painter.setPen(QPen(QColor("#2A2A2A"), 1))
        painter.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))

        spring = self._springs.get(item_id)
        self._paint_checkbox(painter, self._box_rect(r), done, spring.value if spring else 1.0)

        x = self._box_rect(r).right() + 14
        width = int(r.right() - x - 8)
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        font = self._done_font if done else self._title_font
        fm = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(QColor("#8A8A8A") if (done and not selected) else WHITE)
        sub = index.data(ROLE_SUB) if self._two_line else None
        if sub:
            base = r.top() + 9 + fm.ascent()
            painter.drawText(QPointF(x, base), fm.elidedText(title, Qt.TextElideMode.ElideRight, width))
            sfm = QFontMetrics(self._sub_font)
            painter.setFont(self._sub_font)
            if selected:
                painter.setPen(WHITE)
            elif index.data(ROLE_WARN):
                painter.setPen(LIGHT_RED)
            else:
                painter.setPen(QColor("#B5B5B5"))
            painter.drawText(QPointF(x, base + fm.descent() + 4 + sfm.ascent()),
                             sfm.elidedText(sub, Qt.TextElideMode.ElideRight, width))
        else:
            base = r.center().y() + (fm.ascent() - fm.descent()) / 2.0
            painter.drawText(QPointF(x, base), fm.elidedText(title, Qt.TextElideMode.ElideRight, width))
        painter.restore()

    # -- klik pada kotak = ubah selesai ----------------------------------------------
    def editorEvent(self, event, model, option, index) -> bool:
        if index.data(ROLE_ID) is None:
            return False
        et = event.type()
        if et in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick) \
                and event.button() == Qt.MouseButton.LeftButton:
            hit = self._box_rect(QRectF(option.rect)).adjusted(-8, -8, 8, 8)
            if hit.contains(event.position()):
                if et == QEvent.Type.MouseButtonDblClick:       # klik dobel pada kotak: jangan buka editor
                    self._swallow_release = True
                    return True
                if self._swallow_release:                        # rilis kedua dari klik dobel: abaikan
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


# ---------------------------------------------------------------------------
# TASKS
# ---------------------------------------------------------------------------
class TasksDialog(P5Panel):
    FILTERS = (("SEMUA", "all"), ("BELUM", "todo"), ("SELESAI", "done"))

    def __init__(self, store: TaskStore) -> None:
        super().__init__("TASKS", QSize(430, 580))
        self.store = store
        self._filter = "all"

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tulis tugas baru, lalu tekan Enter...")
        self.input.returnPressed.connect(self._add)
        add_btn = P5Button("TAMBAH")
        add_btn.clicked.connect(self._add)
        row.addWidget(self.input, 1)
        row.addWidget(add_btn)
        self.body.addLayout(row)

        chips = QHBoxLayout()
        self._chips: dict[str, P5Button] = {}
        for label, key in self.FILTERS:
            chip = P5Button(label)
            chip.clicked.connect(lambda _checked=False, k=key: self._set_filter(k))
            self._chips[key] = chip
            chips.addWidget(chip)
        self._chips["all"].set_active(True)
        self.body.addLayout(chips)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)
        self.body.addWidget(self.progress)

        self.list = _DeleteAwareList()
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        self._delegate = ChecklistDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.delete_pressed.connect(self._delete_selected)
        self.body.addWidget(self.list, 1)

        self.status = QLabel("")
        self.body.addWidget(self.status)
        tip = QLabel("Klik kotak = selesai  |  Klik 2x = ubah nama  |  Del = hapus")
        tip.setObjectName("tip")
        self.body.addWidget(tip)

        buttons = QHBoxLayout()
        del_btn = P5Button("HAPUS")
        del_btn.clicked.connect(self._delete_selected)
        clear_btn = P5Button("BERSIHKAN SELESAI")
        clear_btn.clicked.connect(self._clear_done)
        buttons.addWidget(del_btn)
        buttons.addWidget(clear_btn)
        self.body.addLayout(buttons)

        self._resort_timer = QTimer(self)          # setelah dicentang, item pindah ke bawah dengan jeda
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(450)
        self._resort_timer.timeout.connect(self.refresh)

    def on_shown(self) -> None:
        QTimer.singleShot(0, self.input.setFocus)   # langsung bisa mengetik

    # -- tampilan data ------------------------------------------------------------
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
        text = f"{len(tasks) - done} belum selesai | {done} selesai"
        self.status.setText(f"{text}   {extra}".strip())

    def refresh(self) -> None:
        tasks = self.store.all()
        pending = [t for t in tasks if not t.get("done")]
        finished = [t for t in tasks if t.get("done")]
        shown = {"all": pending + finished, "todo": pending, "done": finished}[self._filter]
        self.list.blockSignals(True)
        self.list.clear()
        if not shown:
            if not tasks:
                msg = "Belum ada tugas.\nKetik di kolom atas lalu tekan Enter."
            elif self._filter == "todo":
                msg = "Semua tugas sudah selesai!"
            else:
                msg = "Belum ada tugas yang selesai."
            self.list.addItem(_hint_item(msg))
        for t in shown:
            item = QListWidgetItem(t.get("title", ""))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsEditable)
            item.setData(ROLE_ID, t["id"])
            item.setData(ROLE_DONE, bool(t.get("done")))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_status()

    # -- aksi ----------------------------------------------------------------------
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
            self._update_status("(GAGAL MENYIMPAN)")

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Sinkronkan perubahan dari daftar (centang / ganti nama) ke penyimpanan."""
        task_id = item.data(ROLE_ID)
        task = self.store.get(task_id) if task_id else None
        if task is None:
            return
        title = item.text().strip()
        if title and title != task["title"]:
            self.store.rename(task_id, title)
        elif not title:                                     # nama dikosongkan: kembalikan
            self.list.blockSignals(True)
            item.setText(task["title"])
            self.list.blockSignals(False)
        done = bool(item.data(ROLE_DONE))
        if done != bool(task.get("done")):
            self.store.set_done(task_id, done)
            if done:
                self._delegate.start_check_anim(task_id)    # centang tergambar
            else:
                self._delegate.clear_anim(task_id)
            self._resort_timer.start()                      # pindahkan ke bagian "selesai" sebentar lagi
        self._update_status()

    def _delete_selected(self) -> None:
        ids = [i.data(ROLE_ID) for i in self.list.selectedItems() if i.data(ROLE_ID)]
        for task_id in ids:
            self.store.delete(task_id)
        self.refresh()

    def _clear_done(self) -> None:
        removed = self.store.clear_done()
        self.refresh()
        self._update_status(f"({removed} dihapus)")


# ---------------------------------------------------------------------------
# NOTES
# ---------------------------------------------------------------------------
class NotesDialog(P5Panel):
    def __init__(self, store: NoteStore) -> None:
        super().__init__("NOTES", QSize(480, 700))
        self.store = store
        self._current_id: str | None = None
        self._loading = False                 # True saat mengisi formulir secara program
        self._dirty = False                   # ada perubahan yang belum disimpan
        self._confirm_delete = False

        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setMaximumHeight(190)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._delegate = ChecklistDelegate(self.list, two_line=True)
        self.list.setItemDelegate(self._delegate)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemChanged.connect(self._on_item_changed)
        self.body.addWidget(self.list)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Judul catatan")
        self.title_edit.textChanged.connect(self._mark_dirty)
        self.body.addWidget(self.title_edit)

        self.content_edit = QTextEdit()
        self.content_edit.setAcceptRichText(False)
        self.content_edit.setPlaceholderText("Isi catatan...")
        self.content_edit.textChanged.connect(self._mark_dirty)
        self.body.addWidget(self.content_edit, 1)

        self.done_check = QCheckBox("Tandai selesai (tidak diingatkan lagi)")
        self.done_check.setEnabled(False)
        self.done_check.toggled.connect(self._on_done_toggled)
        self.body.addWidget(self.done_check)

        self.deadline_check = QCheckBox("Set Deadline Pengingat")
        self.deadline_check.toggled.connect(self._on_deadline_toggled)
        self.body.addWidget(self.deadline_check)

        self.deadline_box = QWidget()             # tampil hanya saat checkbox deadline dicentang
        box = QVBoxLayout(self.deadline_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        presets = QHBoxLayout()
        for text, kind in (("+1 JAM", "1h"), ("+3 JAM", "3h"), ("BESOK 09:00", "tomorrow")):
            btn = P5Button(text)
            btn.clicked.connect(lambda _checked=False, k=kind: self._apply_preset(k))
            presets.addWidget(btn)
        box.addLayout(presets)
        self.deadline_edit = QDateTimeEdit()
        self.deadline_edit.setCalendarPopup(True)
        self.deadline_edit.setDisplayFormat("dd MMM yyyy   HH:mm")
        self.deadline_edit.setDateTime(self._default_deadline())
        self.deadline_edit.dateTimeChanged.connect(self._on_deadline_changed)
        box.addWidget(self.deadline_edit)
        self.deadline_hint = QLabel("")
        self.deadline_hint.setObjectName("tip")
        box.addWidget(self.deadline_hint)
        self.deadline_box.setVisible(False)
        self.body.addWidget(self.deadline_box)

        buttons = QHBoxLayout()
        new_btn = P5Button("BARU")
        new_btn.clicked.connect(self._new)
        save_btn = P5Button("SIMPAN")
        save_btn.clicked.connect(self._save)
        self._del_btn = P5Button("HAPUS")
        self._del_btn.clicked.connect(self._delete)
        for b in (new_btn, save_btn, self._del_btn):
            buttons.addWidget(b)
        self.body.addLayout(buttons)

        self.status = QLabel("")
        self.body.addWidget(self.status)
        tip = QLabel("Ctrl+S = simpan  |  Klik kotak di daftar = tandai selesai  |  Pindah catatan = simpan otomatis")
        tip.setObjectName("tip")
        tip.setWordWrap(True)
        self.body.addWidget(tip)

        save_key = QShortcut(QKeySequence("Ctrl+S"), self)
        save_key.activated.connect(self._save)

        self._resort_timer = QTimer(self)
        self._resort_timer.setSingleShot(True)
        self._resort_timer.setInterval(450)
        self._resort_timer.timeout.connect(self.refresh)
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(2500)
        self._confirm_timer.timeout.connect(self._reset_delete_confirm)

    # -- hook panel ---------------------------------------------------------------
    def on_shown(self) -> None:
        if self._current_id is None:
            QTimer.singleShot(0, self.title_edit.setFocus)

    def on_closing(self) -> None:
        self._autosave_if_dirty()
        self.refresh()

    # -- util --------------------------------------------------------------------
    @staticmethod
    def _default_deadline() -> QDateTime:
        return _to_qdt(datetime.now() + timedelta(hours=1))

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.status.setText("Perubahan belum disimpan (Ctrl+S)")

    def _on_deadline_toggled(self, on: bool) -> None:
        self.deadline_box.setVisible(on)
        self._update_hint()
        self._mark_dirty()

    def _on_deadline_changed(self, *_args) -> None:
        self._update_hint()
        self._mark_dirty()

    def _update_hint(self) -> None:
        if not self.deadline_check.isChecked():
            self.deadline_hint.setText("")
            return
        delta = _from_qdt(self.deadline_edit.dateTime()) - datetime.now()
        if delta.total_seconds() > 0:
            self.deadline_hint.setText(f"Tersisa {human_delta(delta)}")
        else:
            self.deadline_hint.setText("Waktu ini sudah lewat")

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

    # -- daftar catatan --------------------------------------------------------------
    @staticmethod
    def _sub_text(note: dict) -> tuple[str, bool]:
        """(teks baris kedua, apakah berwarna peringatan)."""
        if note.get("done"):
            return "Selesai", False
        dl = parse_dt(note.get("deadline"))
        if dl is not None:
            delta = dl - datetime.now()
            if delta.total_seconds() > 0:
                return f"{dl:%d %b %H:%M}  -  {human_delta(delta)} lagi", False
            return f"{dl:%d %b %H:%M}  -  LEWAT", True
        first = " ".join((note.get("content") or "").split())
        return (first[:60] if first else "Tanpa deadline"), False

    def refresh(self) -> None:
        far = datetime.max

        def group(n: dict):
            if n.get("done"):
                return (2, far)
            dl = parse_dt(n.get("deadline"))
            return (0, dl) if dl else (1, far)

        notes = sorted(self.store.all(), key=lambda n: n.get("updated", ""), reverse=True)
        notes.sort(key=group)                                  # stabil: belum selesai (deadline terdekat) dulu
        self.list.blockSignals(True)
        self.list.clear()
        if not notes:
            self.list.addItem(_hint_item("Belum ada catatan.\nTulis di bawah lalu tekan SIMPAN."))
        for n in notes:
            sub, warn = self._sub_text(n)
            item = QListWidgetItem(n.get("title") or "(tanpa judul)")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(ROLE_ID, n["id"])
            item.setData(ROLE_DONE, bool(n.get("done")))
            item.setData(ROLE_SUB, sub)
            item.setData(ROLE_WARN, warn)
            self.list.addItem(item)
            if n["id"] == self._current_id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Kotak di daftar diklik: ubah status selesai."""
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
            self.done_check.blockSignals(True)
            self.done_check.setChecked(done)
            self.done_check.blockSignals(False)
        self._resort_timer.start()

    def _on_done_toggled(self, done: bool) -> None:
        if self._loading or not self._current_id:
            return
        self.store.set_done(self._current_id, done)
        if done:
            self._delegate.start_check_anim(self._current_id)
        self.refresh()

    # -- memuat / mengosongkan formulir -----------------------------------------------
    def _on_select(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        note_id = items[0].data(ROLE_ID)
        if note_id is None or note_id == self._current_id:
            return
        self._autosave_if_dirty()                              # catatan sebelumnya tidak hilang
        note = self.store.get(note_id)
        if note is not None:
            self._load(note)
        QTimer.singleShot(0, self.refresh)                     # bangun ulang daftar di luar handler seleksi

    def _load(self, note: dict) -> None:
        self._loading = True
        self._reset_delete_confirm()
        self._current_id = note["id"]
        self.title_edit.setText(note.get("title", ""))
        self.content_edit.setPlainText(note.get("content", ""))
        dl = parse_dt(note.get("deadline"))
        self.deadline_check.setChecked(dl is not None)
        self.deadline_edit.setDateTime(_to_qdt(dl) if dl else self._default_deadline())
        self.done_check.setEnabled(True)
        self.done_check.setChecked(bool(note.get("done")))
        self._loading = False
        self._dirty = False
        self._update_hint()
        self.status.setText("")

    def _new(self) -> None:
        self._autosave_if_dirty()
        self._loading = True
        self._reset_delete_confirm()
        self._current_id = None
        self.title_edit.clear()
        self.content_edit.clear()
        self.deadline_check.setChecked(False)
        self.deadline_edit.setDateTime(self._default_deadline())
        self.done_check.setChecked(False)
        self.done_check.setEnabled(False)
        self._loading = False
        self._dirty = False
        self.list.clearSelection()
        self.refresh()
        self.status.setText("")
        self.title_edit.setFocus()

    # -- simpan / hapus ----------------------------------------------------------------
    def _autosave_if_dirty(self) -> None:
        if self._dirty:
            self._save(silent=True)

    def _save(self, silent: bool = False) -> bool:
        silent = silent is True                                # sinyal clicked mengirim bool False
        title = self.title_edit.text().strip()
        content = self.content_edit.toPlainText().strip()
        if not title and not content:
            if not silent:
                self.status.setText("Isi judul atau catatan dulu.")
            return False
        if not title:
            title = content.splitlines()[0][:40]
        deadline = _from_qdt(self.deadline_edit.dateTime()) if self.deadline_check.isChecked() else None
        if self._current_id and self.store.get(self._current_id):
            note = self.store.update(self._current_id, title, content, deadline)
        else:
            note = self.store.add(title, content, deadline)
        self._current_id = note["id"] if note else None
        self._dirty = False
        self.done_check.setEnabled(self._current_id is not None)
        if silent:
            return True
        self.refresh()
        if self.store.last_error:
            self.status.setText("GAGAL MENYIMPAN: " + self.store.last_error)
        elif deadline:
            self.status.setText(f"Tersimpan. Aku ingatkan mulai H-2 jam sebelum {deadline:%d %b %H:%M}.")
        else:
            self.status.setText("Tersimpan.")
        return True

    def _reset_delete_confirm(self) -> None:
        self._confirm_delete = False
        self._confirm_timer.stop()
        self._del_btn.setText("HAPUS")

    def _delete(self) -> None:
        if not self._current_id:
            self.status.setText("Pilih catatan di daftar dulu.")
            return
        if not self._confirm_delete:                           # klik pertama: minta konfirmasi
            self._confirm_delete = True
            self._del_btn.setText("YAKIN? KLIK LAGI")
            self._confirm_timer.start()
            return
        self._reset_delete_confirm()
        self.store.delete(self._current_id)
        self._dirty = False
        self._new()
        self.status.setText("Catatan dihapus.")


# ---------------------------------------------------------------------------
# PENGATURAN (waktu tidur & pengingat acara)
# ---------------------------------------------------------------------------
class SettingsDialog(P5Panel):
    saved = pyqtSignal()

    def __init__(self, store: SettingsStore) -> None:
        super().__init__("PENGATURAN", QSize(450, 540))
        self.store = store

        self._section("WAKTU TIDUR")
        self.curfew_check = QCheckBox("Ingatkan aku untuk tidur")
        self.body.addWidget(self.curfew_check)
        self.bed_edit = QTimeEdit()
        self.bed_edit.setDisplayFormat("HH:mm")
        self._row("Waktu tidur mulai", self.bed_edit)
        self.wake_edit = QTimeEdit()
        self.wake_edit.setDisplayFormat("HH:mm")
        self._row("Pengingat berhenti (bangun)", self.wake_edit)
        self.curfew_interval = QSpinBox()
        self.curfew_interval.setRange(1, 120)
        self.curfew_interval.setSuffix(" menit")
        self._row("Morgana bicara tiap", self.curfew_interval)
        self._tip("Setelah waktu tidur, Morgana terus bicara dengan jeda ini sampai pengingat berhenti.")

        self._section("PENGINGAT ACARA")
        self.event_window = QSpinBox()
        self.event_window.setRange(5, 1440)
        self.event_window.setSuffix(" menit")
        self._row("Mulai mengingatkan", self.event_window)
        self.event_interval = QSpinBox()
        self.event_interval.setRange(1, 120)
        self.event_interval.setSuffix(" menit")
        self._row("Ulangi tiap", self.event_interval)
        self._tip("120 menit = H-2 jam sebelum acara. Berlaku untuk Google Calendar dan catatan ber-deadline.")

        self.body.addStretch(1)
        buttons = QHBoxLayout()
        save_btn = P5Button("SIMPAN")
        save_btn.clicked.connect(self._save)
        reset_btn = P5Button("BAWAAN")
        reset_btn.clicked.connect(self._reset)
        buttons.addWidget(save_btn)
        buttons.addWidget(reset_btn)
        self.body.addLayout(buttons)
        self.status = QLabel("")
        self.body.addWidget(self.status)

    # -- pembangun baris --------------------------------------------------------------
    def _section(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("section")
        self.body.addWidget(label)

    def _row(self, text: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        row.addWidget(QLabel(text), 1)
        row.addWidget(widget)
        self.body.addLayout(row)

    def _tip(self, text: str) -> None:
        tip = QLabel(text)
        tip.setObjectName("tip")
        tip.setWordWrap(True)
        self.body.addWidget(tip)

    # -- data ----------------------------------------------------------------------
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
        self.status.setText("Nilai bawaan dimuat. Tekan SIMPAN untuk menerapkan.")

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
            self.status.setText("GAGAL MENYIMPAN: " + self.store.last_error)
        elif b == w:
            self.status.setText("Tersimpan, tapi waktu mulai dan berhenti sama: pengingat tidur tidak akan aktif.")
        else:
            self.status.setText("Tersimpan dan langsung berlaku.")