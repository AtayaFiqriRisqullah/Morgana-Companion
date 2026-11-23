#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Desktop Coding Pet - gaya Persona 5 (Tahap 3: UI cair berbasis pegas)
====================================================================

Instalasi : pip install PyQt6 psutil google-api-python-client google-auth-httplib2 google-auth-oauthlib
            (paket Google hanya diperlukan untuk AGENDA; tanpa itu dipakai jadwal contoh)
Jalankan  : python main.py            (opsi lain: python main.py --help)

Berkas proyek (taruh dalam satu folder):
  main.py              karakter, balon dialog, menu radial, controller
  theme.py             palet, font, bentuk (bintang), stylesheet Persona 5 (khusus UI)
  motion.py            mesin animasi pegas (Spring) + FrameTicker 60 FPS adaptif
  ui_dialogs.py        jendela popup TASKS dan NOTES
  storage_manager.py   tasks.json dan notes.json
  calendar_service.py  Google Calendar read-only + jadwal dummy (panduan credentials.json ada di sini)
  autostart.py         auto-start saat boot (Windows Registry / Linux XDG)
  lines.py             pembaca kalimat Morgana; isinya dari morgana_list.md (edit file itu, bukan kode)
  emotions.py          memilih ekspresi Morgana dari isi dialog
  sprites.py           menyusun sprite Morgana (badan + patch mata + patch mulut)

Berkas yang dibuat otomatis: tasks.json, notes.json, settings.json, token.json (login Google), pet.log (galat).
Sprite    : folder Morgana/ (badan, patch mata e1/e2, patch mulut m1-m3; lihat sprites.py).
            Tanpa folder itu dipakai placeholder bergaya P5.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from PyQt6.QtCore import (QDir, QLockFile, QObject, QPoint, QPointF, QRect, QRectF, Qt,
                          QTimer, pyqtSignal)
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QFontMetrics, QGuiApplication, QIcon,
                         QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRegion, QTransform)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

import autostart
from calendar_service import CalendarEvent, CalendarResult, CalendarService, format_agenda, human_delta
from emotions import EXPRESSIONS, STATE_EXPRESSION, emotion_for_text
from motion import FrameTicker, Spring, clamp
from lines import LineBag, greeting_pool, load_lines
import sprites
from sprites import EYE_CLOSED, EYE_HALF, EYE_OPEN, MOUTH_BASE, MorganaSprites
from storage_manager import NoteStore, SettingsStore, TaskStore, parse_dt
from theme import (BLACK, BLACK_BURST_SHARDS, BLUE, LIGHT_RED, MENU_QSS, RED,
                   RED_BURST_SHARDS, WHITE, lerp_color, p5_dialogue_font, p5_font, pen,
                   scale_polygon, star_polygon, tapered_wedge)
from ui_dialogs import NotesDialog, SettingsDialog, TasksDialog

try:
    import psutil
except ImportError:  # program tetap jalan; menu SYSTEM akan memberi tahu
    psutil = None

# ---------------------------------------------------------------------------
# 1. KONFIGURASI & HELPER
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

SCRIPT_PATH = Path(__file__).resolve()
APP_DIR = SCRIPT_PATH.parent
LOG_FILE = APP_DIR / "pet.log"

PET_NAME = "MORGANA"
STATES = ("idle", "talking", "sleepy", "action")
DEFAULT_SPRITE = 180          # px
SPRITE_SIZES = (120, 150, 180, 240)
PAD = 22                      # ruang di sekeliling sprite (untuk melayang, miring, squash & stretch)

LINES_FILE = APP_DIR / "morgana_list.md"   # kalimat Morgana; edit file ini untuk mengganti percakapan
NOTE_COUNTDOWN_REMINDERS = True           # catatan ber-deadline ikut diingatkan H-2 jam seperti acara kalender
EVENT_STARTED_GRACE_S = 90                # pesan "acara dimulai sekarang" dalam N detik setelah mulai
REMINDER_CHECK_MS = 30_000    # cek deadline catatan & acara tiap 30 detik
CALENDAR_REFRESH_MS = 10 * 60_000
STARTUP_TASK_LIMIT = 4        # maksimal tugas yang disebut di ringkasan startup

def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def install_crash_logger() -> None:
    """Catat galat ke pet.log. Penting saat auto-start (tanpa console), dan mencegah PyQt6
    mematikan aplikasi bila ada exception tak tertangani di dalam slot."""
    try:
        logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.WARNING)

    last = {"key": None, "t": 0.0}

    def hook(exc_type, exc, tb) -> None:
        key = (exc_type, str(exc), tb.tb_lineno if tb else 0)
        now = time.monotonic()
        if key == last["key"] and now - last["t"] < 5.0:      # galat sama berulang (mis. di paintEvent)
            return
        last["key"], last["t"] = key, now
        logging.getLogger("pet").error("Exception tak tertangani", exc_info=(exc_type, exc, tb))
        if sys.stderr:
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def play_beep() -> None:
    """Bunyi notifikasi sistem. Urutan: API OS -> suara tema freedesktop -> QApplication.beep()."""
    try:
        if IS_WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return
        if IS_LINUX:
            paplay = shutil.which("paplay")
            for name in ("message.oga", "bell.oga", "complete.oga"):
                sound = Path("/usr/share/sounds/freedesktop/stereo") / name
                if paplay and sound.is_file():
                    launch_detached([paplay, str(sound)])
                    return
            canberra = shutil.which("canberra-gtk-play")
            if canberra:
                launch_detached([canberra, "-i", "message"])
                return
    except Exception:
        pass
    QApplication.beep()


def setup_overlay(w: QWidget, on_top: bool = True) -> None:
    """Jendela frameless + transparan + tidak mencuri fokus dari editor."""
    flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
             | Qt.WindowType.WindowDoesNotAcceptFocus)
    if on_top:
        flags |= Qt.WindowType.WindowStaysOnTopHint
    w.setWindowFlags(flags)
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)


def in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """True jika `now` ada di rentang [start, end), termasuk yang melewati tengah malam."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def fmt_mmss(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def launch_detached(cmd: list[str]) -> bool:
    """Jalankan program terpisah dari proses pet (lintas platform)."""
    kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
    if IS_WINDOWS:
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
        return True
    except OSError:
        return False


def find_vscode() -> list[str] | None:
    for name in ("code", "code-insiders", "codium", "vscodium"):
        path = shutil.which(name)
        if path:
            return [path]
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            exe = Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe"
            if exe.is_file():
                return [str(exe)]
    return None


def find_terminal() -> list[str] | None:
    if IS_WINDOWS:
        wt = shutil.which("wt")
        return [wt] if wt else ["cmd.exe", "/c", "start", "", "cmd.exe"]
    candidates = [os.environ.get("TERMINAL", ""), "konsole", "gnome-terminal",
                  "xfce4-terminal", "alacritty", "kitty", "xterm", "x-terminal-emulator"]
    for name in candidates:
        path = shutil.which(name) if name else None
        if path:
            return [path]
    return None


# ---------------------------------------------------------------------------
# 2. SPRITE: LOADER + PLACEHOLDER (QPainter)
# ---------------------------------------------------------------------------
def _star(cx: float, cy: float, r_out: float, r_in: float, points: int = 8) -> QPolygonF:
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / points - math.pi / 2
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


def make_placeholder(state: str, size: int, dpr: float = 1.0) -> QPixmap:
    """Kepala kucing hitam ala Morgana, digambar programatik (kanvas 100x100)."""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(size / 100.0, size / 100.0)

    def poly(points):
        return QPolygonF([QPointF(x, y) for x, y in points])

    outline = QPen(WHITE, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
    no_pen = Qt.PenStyle.NoPen

    # syal merah
    p.setPen(outline)
    p.setBrush(RED)
    p.drawPolygon(poly([(26, 72), (74, 72), (84, 92), (64, 87), (50, 97), (36, 87), (16, 92)]))
    # telinga
    p.setBrush(BLACK)
    p.drawPolygon(poly([(18, 42), (24, 6), (50, 26)]))
    p.drawPolygon(poly([(82, 42), (76, 6), (50, 26)]))
    p.setPen(no_pen)
    p.setBrush(RED)
    p.drawPolygon(poly([(25, 34), (27, 16), (40, 26)]))
    p.drawPolygon(poly([(75, 34), (73, 16), (60, 26)]))
    # kepala
    p.setPen(outline)
    p.setBrush(BLACK)
    p.drawEllipse(QRectF(15, 24, 70, 56))
    # moncong + hidung
    p.setPen(no_pen)
    p.setBrush(WHITE)
    p.drawEllipse(QRectF(34, 55, 32, 21))
    p.setBrush(RED)
    p.drawPolygon(poly([(46, 58), (54, 58), (50, 63)]))

    # mulut
    if state == "talking":
        p.setBrush(QColor("#3A0008"))
        p.drawEllipse(QRectF(44, 64, 12, 9))
        p.setBrush(RED)
        p.drawEllipse(QRectF(46, 69, 8, 4))
    else:
        p.setPen(QPen(BLACK, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if state == "action":     # nyengir
            path.moveTo(42, 65)
            path.quadTo(50, 76, 58, 65)
        else:
            path.moveTo(50, 63)
            path.lineTo(50, 66)
            path.moveTo(43, 66)
            path.quadTo(46.5, 70, 50, 66)
            path.quadTo(53.5, 70, 57, 66)
        p.drawPath(path)

    # mata
    eye_y = 45
    if state == "sleepy":
        p.setPen(QPen(BLUE, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for cx in (36, 64):
            path = QPainterPath()
            path.moveTo(cx - 8, eye_y)
            path.quadTo(cx, eye_y + 8, cx + 8, eye_y)
            p.drawPath(path)
    else:
        ry = 6.5 if state == "action" else 10
        for cx in (36, 64):
            p.setPen(QPen(WHITE, 1.4))
            p.setBrush(BLUE)
            p.drawEllipse(QRectF(cx - 8, eye_y - ry, 16, 2 * ry))
            p.setPen(no_pen)
            p.setBrush(BLACK)
            p.drawEllipse(QRectF(cx - 2.5, eye_y - ry * 0.6, 5, ry * 1.2))
            p.setBrush(WHITE)
            p.drawEllipse(QRectF(cx - 4.5, eye_y - ry * 0.7, 3.2, 3.2))

    # kumis
    p.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
    for sx in (-1, 1):
        x0 = 50 + sx * 15
        x1 = 50 + sx * 42
        p.drawLine(QPointF(x0, 62), QPointF(x1, 57))
        p.drawLine(QPointF(x0, 66), QPointF(x1, 68))

    # dekorasi per state
    if state == "sleepy":
        p.setPen(WHITE)
        p.setFont(p5_font(14, italic=False))
        p.drawText(QPointF(84, 32), "Z")
        p.setFont(p5_font(10, italic=False))
        p.drawText(QPointF(91, 19), "z")
    elif state == "action":
        p.setPen(QPen(RED, 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(26, 33), QPointF(44, 38))
        p.drawLine(QPointF(74, 33), QPointF(56, 38))
        p.setPen(pen(WHITE, 1.6))
        p.setBrush(RED)
        p.drawPolygon(_star(85, 20, 12, 6))
        p.setPen(WHITE)
        p.setFont(p5_font(13, italic=False))
        p.drawText(QRectF(73, 8, 24, 24), Qt.AlignmentFlag.AlignCenter.value, "!")

    p.end()
    return pm


class SpriteBank:
    """Sprite Morgana per ekspresi, lengkap dengan bingkai mata & mulut (lihat sprites.py).
    Jika folder Morgana/ tidak ada atau berkas korup -> placeholder. Di-cache per ukuran."""

    # gaya placeholder terdekat untuk tiap ekspresi
    PLACEHOLDER = {"neutral": "idle", "sad": "sleepy", "worried": "sleepy", "uneasy": "sleepy",
                   "wink": "action", "excited": "action", "smug": "action", "shout": "action"}

    def __init__(self) -> None:
        self._real = MorganaSprites() if sprites.available() else None
        self._cache: dict[tuple, QPixmap] = {}

    def get(self, expr: str, size: int, dpr: float = 1.0,
            eye: int = EYE_OPEN, mouth: int = MOUTH_BASE) -> QPixmap:
        if expr in STATES:                          # nama state lama -> ekspresi dasarnya
            expr = STATE_EXPRESSION[expr]
        if self._real is not None:
            pm = self._real.get(expr, size, dpr, eye, mouth)
            if pm is not None:
                return pm
        key = (expr, size, round(dpr, 2))
        if key not in self._cache:
            self._cache[key] = make_placeholder(self.PLACEHOLDER.get(expr, "talking"), size, dpr)
        return self._cache[key]


# ---------------------------------------------------------------------------
# 3. PET WINDOW (karakter)
# ---------------------------------------------------------------------------
class PetWindow(QWidget):
    """Karakter. Semua gerak (melayang, bernapas, miring saat di-drag, pantulan saat klik,
    meluncur setelah dilempar) memakai pegas supaya terasa cair."""

    clicked = pyqtSignal()                 # klik kiri tanpa drag
    moved = pyqtSignal()                   # posisi berubah (bubble & menu mengikuti)
    context_requested = pyqtSignal(QPoint)  # klik kanan

    DRAG_THRESHOLD = 5          # px; di bawah ini dianggap klik, bukan drag
    GLIDE_MIN_SPEED = 380.0     # px/detik; lebih lambat dari ini = tidak meluncur
    GLIDE_MAX_SPEED = 2600.0
    GLIDE_FRICTION = 5.0
    # kedipan: (bingkai mata, detik); jeda antar kedipan (detik)
    BLINK = ((EYE_HALF, 0.045), (EYE_CLOSED, 0.07), (EYE_HALF, 0.05))
    BLINK_GAP = (2.2, 5.5)
    BLINK_SLEEPY = ((EYE_CLOSED, 0.55),)
    BLINK_GAP_SLEEPY = (1.8, 4.0)
    # amp: tinggi melayang (px) | speed: rad/detik | breathe: kembang-kempis | droop: turun (px)
    STYLE = {
        "idle":    dict(amp=3.0, speed=1.9, breathe=0.018, droop=0.0),
        "talking": dict(amp=2.0, speed=2.4, breathe=0.022, droop=0.0),
        "sleepy":  dict(amp=1.6, speed=0.9, breathe=0.032, droop=5.0),
        "action":  dict(amp=4.5, speed=3.4, breathe=0.025, droop=-2.0),
    }

    def __init__(self, sprites: SpriteBank, size: int) -> None:
        super().__init__()
        setup_overlay(self)
        self._sprites = sprites
        self._sprite_size = size
        self._state = "idle"
        self._prev_state = "idle"
        self._expr = "neutral"                     # ekspresi wajah (kunci emotions.EXPRESSIONS)
        self._prev_expr = "neutral"
        self._speaking = False                     # mulut bergerak selama teks diketik
        self._eye = EYE_OPEN                       # bingkai mata/mulut yang sedang tampil
        self._mouth = MOUTH_BASE
        self._blink_in = random.uniform(1.5, 3.5)  # detik sampai kedipan berikutnya
        self._blink_t = -1.0                       # < 0 = tidak sedang berkedip
        self._mouth_t = 0.0
        self._badge = ""
        self._phase = 0.0

        self._tilt = Spring(0.0, 170.0, 11.0)      # derajat; memantul saat dilepas
        self._pop = Spring(0.0, 420.0, 15.0)       # squash & stretch
        self._hold = Spring(0.0, 300.0, 24.0)      # "diangkat" saat di-drag
        self._fade = Spring(1.0)                   # cross-fade antar state
        self._talk = Spring(0.0, 120.0, 22.0)      # energi saat bicara

        self._press_global: QPoint | None = None
        self._press_origin = QPoint()
        self._dragging = False
        self._last_gx = self._last_gy = 0.0
        self._last_t = 0.0
        self._vx = self._vy = 0.0                  # kecepatan kursor saat drag (px/detik)
        self._glide_active = False
        self._gx = self._gy = 0.0
        self._fpos = QPointF()

        side = size + 2 * PAD
        self.setFixedSize(side, side)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._ticker = FrameTicker(self._frame, self)

    # -- properti & setter ---------------------------------------------------
    @property
    def sprite_size(self) -> int:
        return self._sprite_size

    def set_state(self, state: str, emotion: str | None = None) -> None:
        """Ganti gaya gerak (state) dan ekspresi wajah. Tanpa `emotion`, dipakai ekspresi dasar state."""
        if state not in STATES:
            return
        expr = emotion if emotion in EXPRESSIONS else STATE_EXPRESSION[state]
        if state == self._state and expr == self._expr:
            return
        self._prev_state, self._prev_expr = self._state, self._expr
        self._state, self._expr = state, expr
        self._fade.snap(0.0)
        self._fade.target = 1.0
        self._fade.configure(150.0)
        self.bump(0.7)

    def set_speaking(self, on: bool) -> None:
        """Gerakkan mulut (m1-m3) selama `on`; kembali ke mulut gambar dasar saat dimatikan."""
        self._speaking = on
        if not on:
            self._mouth = MOUTH_BASE
        self._ticker.start()

    def set_talking(self, on: bool) -> None:
        self._talk.target = 1.0 if on else 0.0
        self._ticker.start()

    def bump(self, strength: float = 1.0) -> None:
        """Squash lalu memantul balik (dipakai saat klik, ganti state, pengingat)."""
        self._pop.value = -0.12 * strength
        self._pop.velocity = 0.0
        self._ticker.start()

    def set_badge(self, text: str) -> None:
        if text != self._badge:
            self._badge = text
            self.update()

    def set_sprite_size(self, size: int) -> None:
        center = self.geometry().center()
        self._sprite_size = size
        side = size + 2 * PAD
        self.setFixedSize(side, side)
        self.move(center.x() - side // 2, center.y() - side // 2)
        self.update()

    # -- loop animasi -----------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._ticker.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._ticker.stop()

    def _screen_area(self) -> QRect:
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _animate_face(self, dt: float) -> None:
        """Kedip (e2 -> e1 -> e2) dan gerak mulut (acak m1/m2/m3) selama berbicara."""
        sleepy = self._state == "sleepy"
        sequence = self.BLINK_SLEEPY if sleepy else self.BLINK
        eye = EYE_HALF if sleepy else EYE_OPEN      # mengantuk: kelopak setengah turun
        if self._blink_t < 0:
            self._blink_in -= dt
            if self._blink_in <= 0:
                self._blink_t = 0.0
        else:
            self._blink_t += dt
            acc = 0.0
            for frame, dur in sequence:
                acc += dur
                if self._blink_t < acc:
                    eye = frame
                    break
            else:                                    # kedipan selesai
                self._blink_t = -1.0
                if not sleepy and random.random() < 0.2:
                    self._blink_in = 0.2             # kedip ganda
                else:
                    self._blink_in = random.uniform(*(self.BLINK_GAP_SLEEPY if sleepy else self.BLINK_GAP))
        self._eye = eye
        if self._speaking:
            self._mouth_t -= dt
            if self._mouth_t <= 0:
                self._mouth_t = random.uniform(0.07, 0.14)
                options = [m for m in (1, 2, 3) if m != self._mouth]
                self._mouth = random.choices(options, weights=[1 if m == 1 else 2 for m in options])[0]

    def _frame(self, dt: float) -> bool:
        self._phase += dt
        self._animate_face(dt)
        moving = False
        if self._dragging:
            decay = math.exp(-dt * 7.0)            # kecepatan "basi" meluruh kalau kursor diam
            self._vx *= decay
            self._vy *= decay
            self._tilt.target = clamp(self._vx * 0.012, -11.0, 11.0)
            moving = True
        for spring in (self._tilt, self._pop, self._hold, self._fade, self._talk):
            moving |= spring.step(dt)
        if self._glide_active:
            moving |= self._glide_step(dt)
        # 60 FPS saat ada interaksi, 30 FPS saat sekadar melayang (hemat CPU)
        self._ticker.set_interval(16 if (moving or self._talk.value > 0.01) else 33)
        self.update()
        return self.isVisible()

    def _glide_step(self, dt: float) -> bool:
        """Meluncur setelah dilempar: gesekan eksponensial + memantul lembut di tepi layar."""
        speed = math.hypot(self._gx, self._gy)
        if speed < 25.0:
            self._glide_active = False
            return False
        k = math.exp(-dt * self.GLIDE_FRICTION)
        self._gx *= k
        self._gy *= k
        nx = self._fpos.x() + self._gx * dt
        ny = self._fpos.y() + self._gy * dt
        area = self._screen_area()
        x_min, x_max = area.left(), area.right() - self.width() + 1
        y_min, y_max = area.top(), area.bottom() - self.height() + 1
        hit = False
        if nx < x_min:
            nx, self._gx, hit = x_min, abs(self._gx) * 0.45, True
        elif nx > x_max:
            nx, self._gx, hit = x_max, -abs(self._gx) * 0.45, True
        if ny < y_min:
            ny, self._gy, hit = y_min, abs(self._gy) * 0.45, True
        elif ny > y_max:
            ny, self._gy, hit = y_max, -abs(self._gy) * 0.45, True
        self._fpos = QPointF(nx, ny)
        self.move(int(round(nx)), int(round(ny)))
        if hit and speed > 140.0:
            self.bump(min(1.0, speed / 900.0))
        return True

    # -- menggambar ----------------------------------------------------------
    def _draw_sprite(self, p: QPainter, expr: str) -> None:
        eye, mouth = (self._eye, self._mouth) if expr == self._expr else (EYE_OPEN, MOUTH_BASE)
        pm = self._sprites.get(expr, self._sprite_size, self.devicePixelRatioF(), eye, mouth)
        dpr = pm.devicePixelRatio()
        w, h = pm.width() / dpr, pm.height() / dpr
        x = PAD + (self._sprite_size - w) / 2
        y = PAD + (self._sprite_size - h) / 2
        p.drawPixmap(QPointF(x, y), pm)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        st = self.STYLE.get(self._state, self.STYLE["idle"])
        ph = self._phase
        hold = clamp(self._hold.value, 0.0, 1.3)
        pop = self._pop.value

        bob = math.sin(ph * st["speed"]) * st["amp"] + st["droop"]
        bob += math.sin(ph * 10.0) * 1.5 * clamp(self._talk.value)       # pantulan kecil saat bicara
        breathe = math.sin(ph * st["speed"] * 0.85 + 1.2) * st["breathe"]
        sy = 1.0 + breathe + pop + 0.05 * hold
        sx = 1.0 - breathe * 0.6 - pop * 0.55 + 0.05 * hold
        tilt = self._tilt.value + math.sin(ph * 0.8) * 0.9 * (1.0 - clamp(hold))

        px = self.width() / 2.0
        py = PAD + self._sprite_size * 0.94                               # pivot di "kaki"
        p.save()
        p.translate(px, py + bob - 7.0 * hold)
        p.rotate(tilt)
        p.scale(max(sx, 0.2), max(sy, 0.2))
        p.translate(-px, -py)
        f = clamp(self._fade.value)
        if f < 0.995 and self._prev_expr != self._expr:
            p.setOpacity(1.0 - f)
            self._draw_sprite(p, self._prev_expr)
            p.setOpacity(f)
        self._draw_sprite(p, self._expr)
        p.restore()
        p.setOpacity(1.0)
        if self._badge:
            self._paint_badge(p)

    def _paint_badge(self, p: QPainter) -> None:
        font = p5_font(12)
        p.setFont(font)
        tw = QFontMetrics(font).horizontalAdvance(self._badge)
        w, h = tw + 22, 22
        x = (self.width() - w) / 2
        y = self.height() - PAD - h + 2
        poly = QPolygonF([QPointF(x + 7, y), QPointF(x + w, y),
                          QPointF(x + w - 7, y + h), QPointF(x, y + h)])
        p.setPen(pen(WHITE, 2))
        p.setBrush(RED)
        p.drawPolygon(poly)
        p.setPen(WHITE)
        p.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter.value, self._badge)

    # -- mouse: bedakan klik vs drag; drag memiringkan, lempar meluncur ---------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            gp = e.globalPosition()
            self._press_global = gp.toPoint()
            self._press_origin = self.pos()
            self._dragging = False
            self._glide_active = False
            self._last_gx, self._last_gy = gp.x(), gp.y()
            self._last_t = time.perf_counter()
            self._vx = self._vy = 0.0
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e) -> None:
        if self._press_global is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        gp = e.globalPosition()
        now = time.perf_counter()
        dt = now - self._last_t
        if dt > 0.001:                              # EMA kecepatan kursor
            self._vx = self._vx * 0.45 + ((gp.x() - self._last_gx) / dt) * 0.55
            self._vy = self._vy * 0.45 + ((gp.y() - self._last_gy) / dt) * 0.55
            self._last_gx, self._last_gy, self._last_t = gp.x(), gp.y(), now
        delta = gp.toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() >= self.DRAG_THRESHOLD:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._hold.target = 1.0
            self._ticker.start()
        if self._dragging:
            self.move(self._press_origin + delta)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            was_drag = self._dragging
            self._press_global = None
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._hold.target = 0.0
            self._tilt.target = 0.0                 # miring kembali tegak sambil memantul
            if was_drag:
                speed = math.hypot(self._vx, self._vy)
                if speed >= self.GLIDE_MIN_SPEED:   # dilempar -> meluncur
                    k = min(1.0, self.GLIDE_MAX_SPEED / speed)
                    self._gx, self._gy = self._vx * k, self._vy * k
                    self._fpos = QPointF(self.pos())
                    self._glide_active = True
            else:
                self.bump(1.0)
                self.clicked.emit()
            self._ticker.start()

    def contextMenuEvent(self, e) -> None:
        self.context_requested.emit(e.globalPos())

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self.moved.emit()


# ---------------------------------------------------------------------------
# 4. BALON DIALOG
# ---------------------------------------------------------------------------
def wrap_text(text: str, measure, max_w: int) -> list[str]:
    """Bungkus teks per baris. `measure(str) -> lebar piksel`. Kata terlalu panjang dipecah."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            while len(word) > 1 and measure(word) > max_w:
                cut = len(word) - 1
                while cut > 1 and measure(word[:cut]) > max_w:
                    cut -= 1
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:cut])
                word = word[cut:]
            trial = f"{cur} {word}" if cur else word
            if cur and measure(trial) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines


class BubbleWindow(QWidget):
    """Balon dialog: muncul membesar dari kepala karakter (pegas + sedikit putar), teks
    mengetik per huruf, tutup dengan menyusut. Bingkai memakai Morgana/textbox.png (dipotong 3
    supaya melebar mengikuti teks, nama MORGANA ditulis miring di dalam kotak nama putihnya).
    Jika textbox.png tidak ada: kotak miring hitam + bayangan merah + ekor runcing."""

    dismissed = pyqtSignal()
    typing_finished = pyqtSignal()          # semua huruf sudah tampil (mulut karakter berhenti)

    MAX_TEXT_W = 320
    M, PADX, PADY = 22, 16, 8
    TAIL_H, TAG_OVER, SHADOW, SLANT = 16, 12, 7, 10   # hanya untuk bingkai cadangan
    # textbox.png (600x173): kotak nama putih miring di kiri atas; area hitam teks mulai x=114
    # sampai x=460 (spike putih kanan mulai masuk di ~470). Dipotong di IMG_L & IMG_R supaya
    # bagian tengah direntangkan tanpa merusak sudut kiri/kanan.
    IMG_W, IMG_H = 600, 173
    IMG_L, IMG_R = 225, 460
    TEXT_L = 114
    IMG_TOP, IMG_BOT = 62, 124

    def __init__(self, pet: PetWindow) -> None:
        super().__init__()
        setup_overlay(self)
        self._pet = pet
        self._lines: list[str] = []
        self._total = 0
        self._reveal = 0.0
        self._speed = 60.0
        self._bw = self._bh = 0
        self._line_h = 18
        self._ascent = 12
        self._below = False
        self._tail_x = 0
        self._closing = False
        self._font = p5_dialogue_font(14)
        self._tag_font = p5_font(10)
        self._box = QPixmap(str(sprites.SPRITE_DIR / "textbox.png"))
        self._use_img = not self._box.isNull()
        self._tail_h = 0 if self._use_img else self.TAIL_H
        self._sx = self._sy = 1.0                  # skala horizontal sisi & skala vertikal bingkai
        self._cap_l = self._cap_r = 0.0
        self._mid_w = 0.0
        self._text_h = 0
        self._show = Spring(0.0, 300.0, 19.0)      # 0 = hilang, 1 = tampil (overshoot sedikit)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self._ticker = FrameTicker(self._frame, self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def show_message(self, text: str, ms: int) -> None:
        fm = QFontMetrics(self._font)
        self._lines = wrap_text(text, fm.horizontalAdvance, self.MAX_TEXT_W)
        self._line_h = fm.height() + 5
        self._ascent = fm.ascent()
        tw = max((fm.horizontalAdvance(ln) for ln in self._lines), default=0) + 6
        self._text_h = len(self._lines) * self._line_h - 5
        if self._use_img:
            self._sy = max(0.76, (self._text_h + 18) / (self.IMG_BOT - self.IMG_TOP))
            self._sx = min(self._sy, 0.96)         # sisi spike & kotak nama tetap proporsional
            self._cap_l = self.IMG_L * self._sx
            self._cap_r = (self.IMG_W - self.IMG_R) * self._sx
            left_usable = (self.IMG_L - self.TEXT_L) * self._sx
            self._mid_w = max(36.0, tw + self.PADX - left_usable)
            self._bw = int(self._cap_l + self._mid_w + self._cap_r)
            self._bh = int(self.IMG_H * self._sy)
        else:
            self._bw = max(tw, 60) + 2 * self.PADX
            self._bh = len(self._lines) * self._line_h + 2 * self.PADY - 2
        self._total = sum(len(ln) for ln in self._lines)
        self._reveal = 0.0
        self._speed = max(55.0, self._total / 1.15)            # teks panjang tetap selesai ~1 detik
        tag_over = 0 if self._use_img else self.TAG_OVER
        shadow = 0 if self._use_img else self.SHADOW
        self.setFixedSize(self._bw + 2 * self.M + shadow,
                          self._bh + tag_over + self._tail_h + 2 * self.M + shadow)

        self._closing = False
        self._show.configure(300.0, 19.0)
        if self.isVisible():                                    # pesan baru: pantul ulang
            self._show.value = min(self._show.value, 0.8)
            self._show.velocity = 0.0
        else:
            self._show.snap(0.0)
        self._show.target = 1.0
        self._place()
        self.show()
        self.raise_()
        self._ticker.start()
        self._hide_timer.start(ms + int(1000 * self._total / self._speed))

    def dismiss(self) -> None:
        self._hide_timer.stop()
        if self.isVisible() and not self._closing:
            self._closing = True
            self._show.configure(360.0)                         # redaman kritis: menyusut mulus
            self._show.target = 0.0
            self._ticker.start()
            self.dismissed.emit()                               # karakter langsung kembali ke state dasar

    def follow(self) -> None:
        if self.isVisible():
            self._place()

    def _frame(self, dt: float) -> bool:
        moving = self._show.step(dt)
        if self._reveal < self._total:
            self._reveal = min(float(self._total), self._reveal + self._speed * dt)
            moving = True
            if self._reveal >= self._total:
                self.typing_finished.emit()
        self.update()
        if self._closing and self._show.value < 0.04:
            self._closing = False
            self._show.snap(0.0)
            self.hide()
            return False
        return moving or self._closing

    def _place(self) -> None:
        pet = self._pet.geometry()
        screen = QGuiApplication.screenAt(pet.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w = self.width()
        size = self._pet.sprite_size
        tag_over = 0 if self._use_img else self.TAG_OVER

        head_y = pet.top() + PAD + int(size * 0.10)          # titik acuan di atas kepala
        tip_local = self.M + tag_over + self._bh + self._tail_h
        y = head_y - tip_local
        self._below = y < area.top()
        if self._below:                                      # tidak muat di atas -> taruh di bawah
            foot_y = pet.bottom() - PAD - int(size * 0.04)
            y = foot_y - (self.M + tag_over)
        x = pet.center().x() - w // 2
        x = max(area.left(), min(x, area.right() - w))
        self._tail_x = max(self.M + 24, min(pet.center().x() - x, w - self.M - self.SHADOW - 34))
        self.move(x, y)

    def _paint_box(self, p: QPainter, x0: float, y0: float) -> None:
        """Gambar textbox.png (3 potongan) lalu tulis PET_NAME di dalam kotak nama putih miring."""
        pm = self._box
        h = self.IMG_H * self._sy
        L, R = self.IMG_L, self.IMG_R
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(x0 + self._cap_l - 0.6, y0, self._mid_w + 1.2, h), pm,
                     QRectF(L, 0, R - L, self.IMG_H))
        p.drawPixmap(QRectF(x0, y0, self._cap_l, h), pm, QRectF(0, 0, L, self.IMG_H))
        p.drawPixmap(QRectF(x0 + self._cap_l + self._mid_w, y0, self._cap_r, h), pm,
                     QRectF(R, 0, self.IMG_W - R, self.IMG_H))

        # Kotak nama putih miring bawaan textbox.png: tepi atas (60,44)->(185,11), pusat (129,38)
        p.save()
        p.translate(x0 + 129.0 * self._sx, y0 + 38.0 * self._sy)
        angle = math.degrees(math.atan2((11.0 - 44.0) * self._sy, (185.0 - 60.0) * self._sx))
        p.rotate(angle)
        tag_f = p5_font(max(11, int(17 * min(self._sx, self._sy))), italic=False)
        p.setFont(tag_f)
        p.setPen(BLACK)
        p.drawText(QRectF(-65.0 * self._sx, -14.0 * self._sy, 130.0 * self._sx, 28.0 * self._sy),
                   Qt.AlignmentFlag.AlignCenter.value, PET_NAME)
        p.restore()

    def _paint_box_fallback(self, p: QPainter, x0: float, y0: float, tx: float, sv: float) -> None:
        s = self.SLANT
        x1, y1 = x0 + self._bw, y0 + self._bh
        body = QPolygonF([QPointF(x0 + s, y0), QPointF(x1, y0), QPointF(x1 - s, y1), QPointF(x0, y1)])
        if self._below:
            tail = QPolygonF([QPointF(tx - 16, y0 + 1), QPointF(tx, y0 - self.TAIL_H), QPointF(tx + 5, y0 + 1)])
        else:
            tail = QPolygonF([QPointF(tx - 16, y1 - 1), QPointF(tx, y1 + self.TAIL_H), QPointF(tx + 5, y1 - 1)])
        path = QPainterPath()
        path.addPolygon(body)
        path.closeSubpath()
        tail_path = QPainterPath()
        tail_path.addPolygon(tail)
        tail_path.closeSubpath()
        path = path.united(tail_path)

        p.save()                                  # bayangan merah miring
        p.translate(self.SHADOW, self.SHADOW)
        p.fillPath(path, RED)
        p.restore()
        p.setPen(pen(WHITE, 3))
        p.setBrush(BLACK)
        p.drawPath(path)

        p.save()                                  # bintang bergerigi di sudut kanan atas
        p.translate(x1 + 1, y0 - 1)
        p.rotate((1.0 - clamp(sv)) * 90.0 + 12.0)
        p.setPen(pen(WHITE, 2))
        p.setBrush(RED)
        p.drawPolygon(star_polygon(0, 0, 11, 5, 5))
        p.restore()

        p.setFont(self._tag_font)                 # name tag merah cadangan
        tag_w = QFontMetrics(self._tag_font).horizontalAdvance(PET_NAME) + 20
        tx0, ty0, th = x0 + 4, y0 - self.TAG_OVER, 20
        tag = QPolygonF([QPointF(tx0 + 7, ty0), QPointF(tx0 + tag_w, ty0),
                         QPointF(tx0 + tag_w - 7, ty0 + th), QPointF(tx0, ty0 + th)])
        p.setPen(pen(WHITE, 2))
        p.setBrush(RED)
        p.drawPolygon(tag)
        p.setPen(WHITE)
        p.drawText(QRectF(tx0, ty0, tag_w, th), Qt.AlignmentFlag.AlignCenter.value, PET_NAME)

    def paintEvent(self, _event) -> None:
        sv = self._show.value
        if clamp(sv * 1.8) <= 0.01 or not self._lines:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setOpacity(clamp(sv * 1.8))
        M = self.M
        tag_over = 0 if self._use_img else self.TAG_OVER
        top = M + tag_over + (self._tail_h if self._below else 0)
        x0, y0, y1 = M, top, top + self._bh
        tx = self._tail_x
        tip_y = (y0 - self._tail_h) if self._below else (y1 + self._tail_h)

        # pop: membesar dari titik di atas kepala karakter, sedikit berputar lalu tegak
        scale = 0.55 + 0.45 * max(sv, 0.0)
        p.translate(tx, tip_y)
        p.rotate((1.0 - clamp(sv, 0.0, 1.5)) * -7.0)
        p.scale(scale, scale)
        p.translate(-tx, -tip_y)

        if self._use_img:
            self._paint_box(p, x0, y0)
            text_x = x0 + self.TEXT_L * self._sx
            inner_h = (self.IMG_BOT - self.IMG_TOP) * self._sy      # tengah vertikal area hitam
            text_y = y0 + self.IMG_TOP * self._sy + (inner_h - self._text_h) / 2 + self._ascent
        else:
            self._paint_box_fallback(p, x0, y0, tx, sv)
            text_x = x0 + self.PADX
            text_y = y0 + self.PADY + self._ascent

        p.setFont(self._font)                     # teks, muncul per huruf
        p.setPen(WHITE)
        remaining = int(self._reveal)
        ty = text_y
        for line in self._lines:
            if remaining <= 0:
                break
            p.drawText(QPointF(text_x, ty), line[:remaining])
            remaining -= len(line)
            ty += self._line_h

    def mousePressEvent(self, e) -> None:
        self.dismiss()


# ---------------------------------------------------------------------------
# 5. MENU RADIAL (gaya menu battle Persona 5)
# ---------------------------------------------------------------------------
@dataclass
class MenuButton:
    key: str
    label: str
    sub: str
    side: int          # -1 kiri / +1 kanan karakter
    ox: float          # posisi pangkal (relatif terhadap pusat ledakan, skala dasar 180 px)
    oy: float
    rot: float         # sudut kipas (derajat)
    length: float      # panjang bilah trapesium (skala dasar 180 px)
    h_in: float        # tinggi di sisi karakter (sempit)
    h_out: float       # tinggi di sisi luar (melebar)
    hi: int            # indeks huruf yang dibalik hitam-putih (gaya cutout Persona 5)
    phase: float       # fase gerak melayang
    pop: Spring = field(default_factory=lambda: Spring(0.0, 230.0, 15.0))     # muncul (memantul)
    hover: Spring = field(default_factory=lambda: Spring(0.0, 380.0, 26.0))   # sapuan merah + bintang
    press: Spring = field(default_factory=lambda: Spring(0.0, 700.0, 36.0))   # mengempis saat ditekan


class MenuBackdrop(QWidget):
    """Lapisan serpihan spike merah-hitam yang memancar dari BELAKANG karakter.
    Tembus klik (WindowTransparentForInput) dan selalu berada di bawah PetWindow."""

    def __init__(self) -> None:
        super().__init__()
        setup_overlay(self)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self._u = 1.0
        self._burst = 0.0
        self._t = 0.0
        self._red: list[QPolygonF] = []
        self._black: list[QPolygonF] = []

    def configure(self, w: int, h: int, u: float) -> None:
        self.setFixedSize(w, h)
        self._u = u
        cx, cy = w / 2.0, h / 2.0 + 18.0 * u
        self._red = [scale_polygon(pts, cx, cy, u) for pts in RED_BURST_SHARDS]
        self._black = [scale_polygon(pts, cx, cy, u) for pts in BLACK_BURST_SHARDS]

    def set_anim(self, burst: float, t: float) -> None:
        self._burst = burst
        self._t = t
        self.update()

    def paintEvent(self, _event) -> None:
        opacity = clamp(self._burst * 1.8)
        if opacity <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(opacity)
        cx = self.width() / 2.0
        cy = self.height() / 2.0 + 18.0 * self._u
        live = clamp(self._burst)
        pulse = 1.0 + 0.016 * math.sin(self._t * 2.4) * live
        sc = (0.18 + 0.82 * max(self._burst, 0.0)) * pulse
        p.translate(cx, cy)
        p.rotate((1.0 - live) * -14.0)
        p.scale(sc, sc)
        p.translate(-cx, -cy)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(RED)
        for poly in self._red:
            p.drawPolygon(poly)
        p.setBrush(BLACK)
        for poly in self._black:
            p.drawPolygon(poly)


class RadialMenu(QWidget):
    """Menu radial bergaya menu battle Persona 5:
      * Lapisan belakang (MenuBackdrop): ledakan serpihan spike merah-hitam di belakang karakter.
      * Lapisan depan (jendela ini): 4 tombol trapesium meruncing yang mengipas dari kiri & kanan
        karakter, huruf besar bergaya cutout dengan subjudul bergaris hitam di tepi bawah.
      * Hover: bilah bergeser keluar, sapuan merah diagonal + bayangan putih + bintang berputar.
      * Tutup dengan redaman kritis (tanpa pantulan)."""

    triggered = pyqtSignal(str, QPoint)   # key tombol, titik global di dekat tombol
    closed = pyqtSignal()

    STAGGER = 0.05                        # jeda antar tombol saat membuka (detik)
    OPEN_STIFFNESS, OPEN_DAMPING = 230.0, 15.0
    CLOSE_STIFFNESS = 340.0

    def __init__(self, pet: PetWindow) -> None:
        super().__init__()
        setup_overlay(self)
        self.setMouseTracking(True)
        self._pet = pet
        self._backdrop = MenuBackdrop()
        # Susunan mengipas seperti menu battle P5 (kiri: ORDER/GUN, kanan: PERSONA/GUARD)
        self._buttons = [
            MenuButton("agenda", "AGENDA", "Next Event",      -1, -64, -14, +25.0, 184, 28, 54, 3, 0.0),
            MenuButton("tasks",  "TASKS",  "To-Do List",      +1, +64,  -8, -21.0, 180, 27, 54, 3, 1.7),
            MenuButton("notes",  "NOTES",  "Quick Notes",     +1, +66, +38, +19.0, 178, 27, 52, 2, 3.4),
            MenuButton("system", "SYSTEM", "Options & Sleep", -1, -68, +34,  -7.5, 184, 26, 52, 2, 5.1),
        ]
        self._u = 1.0                                    # faktor skala terhadap ukuran dasar 180 px
        self._burst = Spring(0.0, 260.0, 17.0)           # animasi ledakan spike di belakang
        self._geom: dict[str, tuple] = {}                # cache poligon, huruf, dan path subjudul
        self._t = 0.0
        self._open = False
        self._mask_on = False
        self._pressed = -1

        self._ticker = FrameTicker(self._frame, self)
        self._auto_close = QTimer(self)                  # menutup sendiri kalau dibiarkan
        self._auto_close.setSingleShot(True)
        self._auto_close.setInterval(15000)
        self._auto_close.timeout.connect(self.close_menu)

    # -- buka / tutup & z-order ----------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._open and self.isVisible()

    def set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self._backdrop.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)

    def _build_button_cache(self, b: MenuButton, u: float) -> tuple:
        """Hitung sekali poligon bilah, tata letak huruf cutout, dan path subjudul untuk skala `u`."""
        L, h_in, h_out = b.length * u, b.h_in * u, b.h_out * u
        poly = tapered_wedge(b.side, L, h_in, h_out)
        shape = QPainterPath()
        shape.addPolygon(poly)
        shape.closeSubpath()

        # Susun huruf satu per satu: membesar mengikuti pelebaran trapesium (kecil di dekat karakter)
        pad_out, pad_in = 14.0 * u, 14.0 * u
        x_start, x_end = (-L + pad_out, -pad_in) if b.side < 0 else (pad_in, L - pad_out)
        n = len(b.label)
        raw = []
        total_w = 0.0
        for i, ch in enumerate(b.label):
            t = i / max(1, n - 1)
            frac_outer = (1.0 - t) if b.side < 0 else t
            h_here = h_in + (h_out - h_in) * frac_outer
            f = p5_font(max(13, int(h_here * 0.78)), italic=False)
            f.setStretch(92)
            fm = QFontMetrics(f)
            adv = fm.horizontalAdvance(ch)
            tb = fm.tightBoundingRect(ch)
            extra = (5.0 * u) if i == b.hi else 0.0      # ruang ekstra agar kotak cutout tidak menyentuh tetangga
            step = adv * 0.92 + extra
            raw.append((ch, f, tb, step))
            total_w += step

        avail = abs(x_end - x_start)
        xscale = min(1.0, avail / max(1.0, total_w))
        x = x_start + (avail - total_w * xscale) * (0.35 if b.side < 0 else 0.55)
        glyphs = []
        for i, (ch, f, tb, step) in enumerate(raw):
            w_step = step * xscale
            cx_ch = x + w_step * 0.5
            jy = (-1.6 if i % 2 == 0 else 1.4) * u - 3.0 * u
            tilt = -4.0 if i % 2 == 0 else 3.5
            pad = 3.0 * u
            box = QRectF(-tb.width() / 2 - pad, -tb.height() / 2 - pad,
                         tb.width() + 2 * pad, tb.height() + 2 * pad)
            glyphs.append((ch, f, cx_ch, jy, tilt, xscale, -tb.center().x(), -tb.center().y(), box, i == b.hi))
            x += w_step

        # Subjudul di tepi bawah (path di-cache supaya 60 FPS ringan)
        sub_f = p5_font(max(9, int(12 * u)), italic=True)
        tw = QFontMetrics(sub_f).horizontalAdvance(b.sub)
        tx = (-L * 0.54 if b.side < 0 else L * 0.54) - tw * 0.5
        ty = (h_in + h_out) * 0.24 + 5.0 * u
        sub_path = QPainterPath()
        sub_path.addText(QPointF(tx, ty), sub_f, b.sub)
        return L, h_in, h_out, poly, shape, glyphs, sub_path

    def _layout(self) -> None:
        u = max(0.72, self._pet.sprite_size / 180.0)
        self._u = u
        w, h = int(700 * u), int(380 * u)
        self.setFixedSize(w, h)
        self._backdrop.configure(w, h, u)
        self._geom = {b.key: self._build_button_cache(b, u) for b in self._buttons}

    def open_menu(self) -> None:
        self._layout()
        if self.isVisible():
            self._t = 10.0                      # sedang menutup lalu dibuka lagi: tanpa jeda stagger
        else:
            self._t = 0.0
            self._burst.snap(0.0)
            for b in self._buttons:
                b.pop.snap(0.0)
                b.hover.snap(0.0)
                b.press.snap(0.0)
        self._open = True
        self._mask_on = False
        self.clearMask()
        self._burst.configure(260.0, 17.0)
        self._burst.target = 1.0
        for b in self._buttons:
            b.pop.configure(self.OPEN_STIFFNESS, self.OPEN_DAMPING)
        self.follow(force=True)
        # Urutan tumpukan: spike belakang -> karakter Morgana -> 4 tombol di depan
        self._backdrop.show()
        self._pet.raise_()
        self.show()
        self.raise_()
        self._auto_close.start()
        self._ticker.start()

    def close_menu(self) -> None:
        if not self.isVisible() or not self._open:
            return
        self._open = False
        self._mask_on = False
        self.clearMask()
        self._auto_close.stop()
        self._burst.configure(self.CLOSE_STIFFNESS)
        self._burst.target = 0.0
        for b in self._buttons:
            b.pop.configure(self.CLOSE_STIFFNESS)       # kritis: menutup tanpa memantul
            b.hover.target = 0.0
        self._ticker.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._backdrop.hide()

    def follow(self, force: bool = False) -> None:
        if self.isVisible() or force:
            c = self._pet.geometry().center()
            pos = QPoint(c.x() - self.width() // 2, c.y() - self.height() // 2)
            self.move(pos)
            self._backdrop.move(pos)

    # -- loop animasi ---------------------------------------------------------
    def _frame(self, dt: float) -> bool:
        if not self.isVisible():                       # disembunyikan dari luar -> berhenti
            self._open = False
            self._backdrop.hide()
            return False
        self._t += dt
        self._burst.step(dt)
        for i, b in enumerate(self._buttons):
            if self._open:
                b.pop.target = 1.0 if self._t >= i * self.STAGGER else 0.0
            else:
                b.pop.target = 0.0
            b.pop.step(dt)
            b.hover.step(dt)
            b.press.step(dt)
        self._backdrop.set_anim(self._burst.value, self._t)
        self.update()
        if self._open:
            if not self._mask_on and all(b.pop.at_rest and b.pop.target == 1.0 for b in self._buttons):
                self.setMask(self._build_mask())       # setelah menetap: hanya tombol yang menangkap klik
                self._mask_on = True
            return True                                # gerak melayang berjalan selama menu terbuka
        if self._burst.value < 0.04 and all(b.pop.value < 0.04 for b in self._buttons):
            self._burst.snap(0.0)
            for b in self._buttons:
                b.pop.snap(0.0)
            self.hide()                                # hideEvent ikut menyembunyikan _backdrop
            self.closed.emit()
            return False
        return True

    # -- geometri tombol -----------------------------------------------------
    def _xf(self, b: MenuButton, pop: float | None = None, hover: float | None = None,
            wobble: bool = True) -> QTransform:
        pop = b.pop.value if pop is None else pop
        hov = clamp(b.hover.value, 0.0, 1.3) if hover is None else hover
        prs = b.press.value
        live = clamp(pop)
        u = self._u
        fx = math.sin(self._t * 1.6 + b.phase) * 2.0 * u * live if wobble else 0.0
        fy = math.cos(self._t * 1.25 + b.phase * 1.3) * 2.4 * u * live if wobble else 0.0
        sway = math.sin(self._t * 1.1 + b.phase) * 0.7 * live if wobble else 0.0
        cx = self.width() / 2.0
        cy = self.height() / 2.0 + 18.0 * u
        t = QTransform()
        t.translate(cx + b.ox * u * pop + fx, cy + b.oy * u * pop + fy)
        t.rotate(b.rot + (1.0 - live) * 20.0 * -b.side + sway)      # mengipas masuk saat muncul
        t.translate(b.side * 10.0 * u * hov, 0.0)                   # meluncur ke luar searah bilah saat hover
        s = (0.35 + 0.65 * max(pop, 0.0)) * (1.0 + 0.08 * hov) * (1.0 - 0.07 * prs)
        t.scale(s, s)
        return t

    def _hit(self, pos: QPointF) -> int:
        for i in reversed(range(len(self._buttons))):
            b = self._buttons[i]
            if b.pop.value < 0.5 or b.key not in self._geom:
                continue
            poly = self._geom[b.key][3]
            if self._xf(b).map(poly).containsPoint(pos, Qt.FillRule.OddEvenFill):
                return i
        return -1

    def _build_mask(self) -> QRegion:
        region = QRegion()
        u = self._u
        for b in self._buttons:
            L, _, h_out = self._geom[b.key][:3]
            # kotak longgar mencakup bilah, bayangan hover, subjudul bawah, dan bintang di ujung dalam
            if b.side < 0:
                box = QPolygonF(QRectF(-L - 22 * u, -h_out * 0.72, L + 46 * u, h_out * 1.55))
            else:
                box = QPolygonF(QRectF(-24 * u, -h_out * 0.72, L + 46 * u, h_out * 1.55))
            for hv in (0.0, 1.0):
                poly = self._xf(b, pop=1.0, hover=hv, wobble=False).map(box)
                region = region.united(QRegion(poly.toPolygon()))
        return region

    # -- event ---------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        idx = self._hit(e.position())
        for i, b in enumerate(self._buttons):
            b.hover.target = 1.0 if i == idx else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
        if self._auto_close.isActive():
            self._auto_close.start()

    def leaveEvent(self, e) -> None:
        for b in self._buttons:
            b.hover.target = 0.0

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._hit(e.position())
        if idx < 0:
            self.close_menu()      # klik area kosong (jika mask tidak didukung platform)
            return
        self._pressed = idx
        self._buttons[idx].press.target = 1.0
        self._ticker.start()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._pressed < 0:
            return
        idx, self._pressed = self._pressed, -1
        b = self._buttons[idx]
        b.press.target = 0.0
        if self._hit(e.position()) == idx:
            L, _, h_out = self._geom[b.key][:3]
            anchor = self._xf(b, pop=1.0, hover=0.0, wobble=False).map(
                QPointF(b.side * L * 0.45, h_out * 0.5))
            self.triggered.emit(b.key, self.mapToGlobal(anchor.toPoint()))

    # -- menggambar ------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for b in sorted(self._buttons, key=lambda x: x.hover.value):   # yang di-hover digambar paling atas
            self._paint_button(p, b)

    def _paint_button(self, p: QPainter, b: MenuButton) -> None:
        opacity = clamp(b.pop.value * 1.7)
        if opacity <= 0.01 or b.key not in self._geom:
            return
        L, _, h_out, poly, shape, glyphs, sub_path = self._geom[b.key]
        u = self._u
        hv = b.hover.value
        hov = clamp(hv)

        p.save()
        p.setOpacity(opacity)
        p.setTransform(self._xf(b))

        # Bilah bayangan miring: merah saat diam, putih mencolok saat hover
        p.save()
        p.translate(b.side * (4.0 + 5.0 * hov) * u, (5.0 + 2.0 * hov) * u)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(lerp_color(RED, WHITE, hov))
        p.drawPolygon(poly)
        p.restore()

        # Badan tombol: trapesium hitam pekat (tanpa garis tepi, sesuai menu battle P5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BLACK)
        p.drawPath(shape)

        if hov > 0.01:
            p.save()                                                 # sapuan merah diagonal keluar dari karakter
            p.setClipPath(shape)
            p.setBrush(RED)
            if b.side > 0:
                edge = -10.0 * u + hv * (L + 44.0 * u)
                p.drawPolygon(QPolygonF([
                    QPointF(-18.0 * u, -h_out), QPointF(edge + 18.0 * u, -h_out),
                    QPointF(edge - 18.0 * u, h_out), QPointF(-18.0 * u, h_out),
                ]))
                p.setPen(pen(WHITE, 2.8 * u))
                p.drawLine(QPointF(edge + 18.0 * u, -h_out), QPointF(edge - 18.0 * u, h_out))
            else:
                edge = 10.0 * u - hv * (L + 44.0 * u)
                p.drawPolygon(QPolygonF([
                    QPointF(18.0 * u, -h_out), QPointF(edge - 18.0 * u, -h_out),
                    QPointF(edge + 18.0 * u, h_out), QPointF(18.0 * u, h_out),
                ]))
                p.setPen(pen(WHITE, 2.8 * u))
                p.drawLine(QPointF(edge - 18.0 * u, -h_out), QPointF(edge + 18.0 * u, h_out))
            p.restore()

        # Huruf utama bergaya cutout Persona 5 (membesar ke arah ujung luar trapesium)
        for ch, f, cx_ch, jy, tilt, xscale, gx, gy, box, is_hi in glyphs:
            p.save()
            p.translate(cx_ch, jy)
            p.rotate(tilt)
            p.scale(xscale, 1.0)
            p.setFont(f)
            if is_hi:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(WHITE)
                p.drawRect(box)
                p.setPen(BLACK)
            else:
                p.setPen(WHITE)
            p.drawText(QPointF(gx, gy), ch)
            p.restore()

        # Subjudul putih dengan garis tepi hitam tebal menempel di tepi bawah bilah
        p.save()
        p.setPen(QPen(BLACK, 4.5 * u, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(sub_path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(WHITE)
        p.drawPath(sub_path)
        p.restore()

        # Bintang penanda berputar saat hover (di ujung dalam bilah)
        if hov > 0.01:
            p.save()
            p.translate(b.side * 2.0 * u, -1.0 * u)
            p.rotate(hov * 160.0)
            sc = 0.2 + 0.8 * hov
            p.scale(sc, sc)
            p.setPen(pen(BLACK, 2.4 * u))
            p.setBrush(WHITE)
            p.drawPolygon(star_polygon(0, 0, 15.0 * u, 6.5 * u, 5))
            p.restore()

        p.restore()


# ---------------------------------------------------------------------------
# 6. POMODORO & SYSTEM MONITOR
# ---------------------------------------------------------------------------
class PomodoroTimer(QObject):
    FOCUS, REST = "focus", "rest"
    tick = pyqtSignal(str, int)         # fase, sisa detik
    phase_started = pyqtSignal(str)
    finished = pyqtSignal()             # siklus selesai alami
    stopped = pyqtSignal()              # dihentikan manual

    def __init__(self, focus_min: float, rest_min: float, parent=None) -> None:
        super().__init__(parent)
        self.focus_s = max(1, int(focus_min * 60))
        self.rest_s = max(1, int(rest_min * 60))
        self.phase = self.FOCUS
        self.completed = 0
        self._deadline = 0.0            # pakai jam monotonic -> tidak melenceng saat laptop sleep
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def remaining(self) -> int:
        return self._left() if self.running else 0

    def _left(self) -> int:
        return max(0, math.ceil(self._deadline - time.monotonic()))

    def start(self) -> None:
        self._timer.start()
        self._begin(self.FOCUS)

    def stop(self) -> None:
        was_running = self.running
        self._timer.stop()
        if was_running:
            self.stopped.emit()

    def _begin(self, phase: str) -> None:
        self.phase = phase
        self._deadline = time.monotonic() + (self.focus_s if phase == self.FOCUS else self.rest_s)
        self.phase_started.emit(phase)
        self.tick.emit(phase, self._left())

    def _on_tick(self) -> None:
        left = self._left()
        if left > 0:
            self.tick.emit(self.phase, left)
        elif self.phase == self.FOCUS:
            self.completed += 1
            self._begin(self.REST)
        else:
            self._timer.stop()
            self.finished.emit()


@dataclass
class SysSnapshot:
    cpu: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float


class SystemMonitor(QObject):
    """Sampel CPU tiap 2 detik di latar (non-blocking); RAM dibaca saat diminta."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cpu: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._sample)
        if psutil is not None:
            psutil.cpu_percent(interval=None)   # prime: panggilan pertama selalu 0.0
            self._timer.start()

    def _sample(self) -> None:
        self._cpu = psutil.cpu_percent(interval=None)

    def snapshot(self) -> SysSnapshot | None:
        if psutil is None:
            return None
        if self._cpu is None:                   # diklik sebelum sampel pertama
            self._cpu = psutil.cpu_percent(interval=0.2)
        vm = psutil.virtual_memory()
        return SysSnapshot(self._cpu, vm.percent, vm.used / 2**30, vm.total / 2**30)


# ---------------------------------------------------------------------------
# 7. KALENDER ASINKRON
# ---------------------------------------------------------------------------
class CalendarFetcher(QObject):
    """Menjalankan CalendarService.get_upcoming() di thread latar agar UI tidak membeku
    (panggilan jaringan dan login browser bisa memakan waktu beberapa detik)."""

    done = pyqtSignal(object)           # CalendarResult

    def __init__(self, service: CalendarService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def fetch(self, interactive: bool = False) -> bool:
        if self._busy:
            return False
        self._busy = True
        threading.Thread(target=self._run, args=(interactive,), daemon=True,
                         name="calendar-fetch").start()
        return True

    def _run(self, interactive: bool) -> None:
        try:
            result = self._service.get_upcoming(interactive=interactive)
        except Exception as exc:        # get_upcoming sudah menangkap semuanya; sekadar jaga-jaga
            result = CalendarResult([], "error", str(exc))
        self._busy = False
        self.done.emit(result)          # diterima di thread UI (queued connection)


# ---------------------------------------------------------------------------
# 8. CONTROLLER
# ---------------------------------------------------------------------------
_FOCUS_START = (
    "Focus session started ({t})! Take it one step at a time—you've got this!",
    "All right, {t} of focused work! Stay steady at your own pace—I'm rooting for you!",
    "Time to lock in for {t}! Small progress still counts, so let's make it a great one.",
    "Here we go—{t} of focus! Take a deep breath; you're more than ready for this.",
)
_FOCUS_PROGRESS = (
    "You're doing great! {t} left in this focus session—keep that momentum going!",
    "Hang in there! Only {t} left to go—you've got this!",
    "Steady progress! {t} remaining in your focus session. Keep it up!",
)
_REST_PROGRESS = (
    "Take it easy—{t} left on your break. You earned this breather!",
    "Enjoy your rest! {t} left before we get back to it.",
)
_FOCUS_REST = (
    "Awesome work! Take a {t} break—drink some water, stretch, and let your mind recharge.",
    "You made it through! Enjoy a {t} break—you really earned it!",
    "Great effort! Step back for {t}, breathe, and recharge your energy.",
)


class PetController(QObject):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.sprites = SpriteBank()
        self.pet = PetWindow(self.sprites, args.size)
        self.bubble = BubbleWindow(self.pet)
        self.menu = RadialMenu(self.pet)
        self.tasks = TaskStore(APP_DIR / "tasks.json")
        self.notes = NoteStore(APP_DIR / "notes.json")
        self.settings = SettingsStore(APP_DIR / "settings.json")
        if args.curfew is not None:               # CLI flags override for this session only
            self.settings.set(save=False, bedtime=args.curfew.strftime("%H:%M"))
        if args.wake is not None:
            self.settings.set(save=False, wake=args.wake.strftime("%H:%M"))
        focus_m = args.focus if args.focus is not None else self.settings["focus_min"]
        rest_m = args.rest if args.rest is not None else self.settings["rest_min"]
        self.pomodoro = PomodoroTimer(focus_m, rest_m, self)
        self.monitor = SystemMonitor(self)
        lines = load_lines(LINES_FILE)            # kalimat Morgana dari morgana_list.md
        self._greetings = lines["greeting"]
        self._bag_bedtime = LineBag(lines["bedtime"])
        self._bag_agenda = LineBag(lines["agenda"])
        self._bag_general = LineBag(lines["general"])
        self.calendar = CalendarService(APP_DIR)
        self.fetcher = CalendarFetcher(self.calendar, self)
        self.tasks_dialog: TasksDialog | None = None
        self.notes_dialog: NotesDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self._cal_events: list[CalendarEvent] = []
        self._remind_last: dict[str, float] = {}    # kunci acara -> waktu (monotonic) pengingat terakhir
        self._remind_started: set[str] = set()      # acara yang sudah diberi pesan "dimulai sekarang"
        self._agenda_wanted = False            # pengguna menunggu hasil AGENDA

        # state karakter
        self.forced_state: str | None = None   # override manual dari menu klik kanan
        self.forced_expression: str | None = None
        self.sleep_mode = False
        self.curfew_enabled = bool(self.settings["curfew_enabled"])
        self.curfew_active = False
        self._last_curfew_msg: datetime | None = None
        self.on_top = True

        self._build_context_menu()
        self._build_system_menu()
        self._build_tray()

        self.pet.clicked.connect(self._on_pet_clicked)
        self.pet.moved.connect(self._on_pet_moved)
        self.pet.context_requested.connect(self.context_menu.popup)
        self.bubble.dismissed.connect(self._restore_state)
        self.bubble.typing_finished.connect(lambda: self.pet.set_speaking(False))
        self.menu.triggered.connect(self._on_menu_action)
        self.pomodoro.tick.connect(self._on_pomo_tick)
        self.pomodoro.phase_started.connect(self._on_pomo_phase)
        self.pomodoro.finished.connect(self._on_pomo_finished)
        self.pomodoro.stopped.connect(lambda: self.pet.set_badge(""))
        self.fetcher.done.connect(self._on_calendar_result)

        self._curfew_timer = QTimer(self)
        self._curfew_timer.setInterval(30_000)
        self._curfew_timer.timeout.connect(self._check_curfew)
        self._reminder_timer = QTimer(self)        # deadline catatan + acara kalender
        self._reminder_timer.setInterval(REMINDER_CHECK_MS)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._calendar_timer = QTimer(self)
        self._calendar_timer.setInterval(CALENDAR_REFRESH_MS)
        self._calendar_timer.timeout.connect(self._refresh_calendar)
        self._chatter_timer = QTimer(self)
        self._chatter_timer.setSingleShot(True)
        self._chatter_timer.timeout.connect(self._chatter)

    # -- siklus hidup -------------------------------------------------------
    def start(self) -> None:
        self._move_to_corner(QGuiApplication.primaryScreen())
        self.pet.show()
        self._check_curfew()
        self._curfew_timer.start()
        self._reminder_timer.start()
        self._calendar_timer.start()
        self._schedule_chatter()
        QTimer.singleShot(900, self._startup_report)
        QTimer.singleShot(3000, self._refresh_calendar)

    def quit(self) -> None:
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    # -- state & dialog -------------------------------------------------------
    def base_state(self) -> str:
        if self.forced_state:
            return self.forced_state
        return "sleepy" if (self.sleep_mode or self.curfew_active) else "idle"

    def _restore_state(self) -> None:
        self.pet.set_state(self.base_state(), self.forced_expression)
        self.pet.set_talking(False)
        self.pet.set_speaking(False)

    def say(self, text: str, state: str = "talking", ms: int | None = None,
            emotion: str | None = None) -> None:
        """Ucapkan `text`. Ekspresi dipilih dari isi kalimat (atau `emotion` bila diberikan);
        mulut bergerak selama balon mengetik."""
        if emotion is None:
            emotion = emotion_for_text(text, default=STATE_EXPRESSION.get(state, "neutral"))
        if self.forced_state:
            self.pet.set_state(self.forced_state, self.forced_expression)
        else:
            self.pet.set_state(state, self.forced_expression or emotion)
        self.pet.set_talking(True)
        self.pet.set_speaking(True)
        self.bubble.show_message(text, ms or max(4000, 2500 + 55 * len(text)))

    def _schedule_chatter(self) -> None:
        base_ms = max(0.05, self.args.chatter) * 60_000
        self._chatter_timer.start(int(base_ms * random.uniform(0.75, 1.5)))

    def _chatter(self) -> None:
        if not self.sleep_mode and not self.menu.is_open and not self.bubble.isVisible():
            self.say(self._bag_general.next())
        self._schedule_chatter()

    # -- startup: ringkasan tugas ---------------------------------------------
    def _startup_report(self) -> None:
        if self.curfew_active:                  # pengingat tidur lebih penting
            return
        pending = self.tasks.pending()
        greeting = random.choice(greeting_pool(self._greetings, datetime.now().hour))
        if not pending:
            self.say(greeting)
            return
        shown = pending[:STARTUP_TASK_LIMIT]
        lines = [f"{i}. {clip(t['title'], 44)}" for i, t in enumerate(shown, 1)]
        if len(pending) > len(shown):
            lines.append(f"...and {len(pending) - len(shown)} more")
        word = "task" if len(pending) == 1 else "tasks"
        self.say(f"{greeting}\n\nYou have {len(pending)} pending {word} today:\n" + "\n".join(lines),
                 "talking", ms=13000)

    # -- interaksi pet ---------------------------------------------------------
    def _on_pet_clicked(self) -> None:
        if self.menu.is_open:
            self.menu.close_menu()
        else:
            self.menu.open_menu()
            self.say(self._bag_general.next(), ms=4500)

    def _on_pet_moved(self) -> None:
        self.bubble.follow()
        self.menu.follow()

    def _open_tasks(self) -> None:
        if self.tasks_dialog is None:
            self.tasks_dialog = TasksDialog(self.tasks)
            self.tasks_dialog.set_on_top(self.on_top)
            self.tasks_dialog.open_settings.connect(self._open_settings_deferred)
            self.tasks_dialog.open_notes.connect(self._open_notes)
        self.tasks_dialog.show_near(self.pet.geometry())

    def _open_notes(self) -> None:
        if self.notes_dialog is None:
            self.notes_dialog = NotesDialog(self.notes, self.tasks)
            self.notes_dialog.set_on_top(self.on_top)
            self.notes_dialog.open_settings.connect(self._open_settings_deferred)
            self.notes_dialog.open_tasks.connect(self._open_tasks)
        self.notes_dialog.show_near(self.pet.geometry())

    def _open_settings_deferred(self) -> None:
        """Open Settings after any menu/button release finishes (prevents Windows focus burial)."""
        QTimer.singleShot(0, self._open_settings)

    def _on_menu_action(self, key: str, anchor: QPoint) -> None:
        self.menu.close_menu()
        if key == "agenda":
            self._act_agenda()
        elif key == "tasks":
            self._open_tasks()
        elif key == "notes":
            self._open_notes()
        elif key == "system":
            self.system_menu.popup(anchor)

    # -- AGENDA: Google Calendar ---------------------------------------------------
    def _act_agenda(self) -> None:
        if not self.calendar.is_configured():
            # Tanpa credentials.json: tampilkan panduan + jadwal dummy (instan, tanpa jaringan)
            result = self.calendar.get_upcoming()
            self._cal_events = result.events if self.args.demo_reminders else self._cal_events
            self._show_agenda(result)
            return
        self._agenda_wanted = True
        if self.calendar.has_token():
            self.say("Checking your calendar...", ms=5000)
        else:
            self.say("Opening your browser to connect Google Calendar (one-time setup). "
                     "Finish signing in there!", ms=9000)
        self.fetcher.fetch(interactive=True)   # jika sedang sibuk, hasilnya tetap ditampilkan nanti

    def _show_agenda(self, result: CalendarResult) -> None:
        text = format_agenda(result)
        self.say(text, "talking", ms=max(8000, 2500 + 50 * len(text)))

    def _refresh_calendar(self) -> None:
        """Pembaruan latar (tanpa membuka browser). Dipakai untuk pengingat 15 menit sebelum acara."""
        if not self.calendar.is_configured():
            if self.args.demo_reminders:
                self._cal_events = self.calendar.get_upcoming().events
            return
        if self.calendar.has_token():
            self.fetcher.fetch(interactive=False)

    def _on_calendar_result(self, result: CalendarResult) -> None:
        if result.status == "ok" or (result.is_dummy and self.args.demo_reminders):
            self._cal_events = result.events
        if not self._agenda_wanted:
            return
        if result.status == "auth_needed":     # fetch latar gagal login -> ulangi interaktif
            if self.fetcher.fetch(interactive=True):
                self.say("Your Google authorization expired. Opening your browser to sign in again...", ms=9000)
            return
        self._agenda_wanted = False
        self._show_agenda(result)

    # -- pengingat: acara H-2 jam (jeda 10 menit) & deadline catatan -------------------
    def _check_reminders(self) -> None:
        messages: list[str] = []
        now = datetime.now()
        aware_now = now.astimezone()

        # 1) catatan yang deadline-nya sudah tiba (termasuk yang terlewat saat aplikasi mati)
        for note in self.notes.due(now):
            self.notes.mark_notified(note["id"])
            title = note.get("title") or "Note"
            line = f"DUE NOW: {title}"
            snippet = clip(note.get("content", ""), 70)
            if snippet and snippet != title:
                line += f"\n{snippet}"
            messages.append(line)

        # 2) hitung mundur: mulai H-<jendela>, ulangi tiap <interval> menit sampai acara dimulai
        window = timedelta(minutes=self.settings["event_window_min"])
        interval_s = self.settings["event_interval_min"] * 60 - 15      # toleransi 15 dtk karena timer 30 dtk
        upcoming: list[tuple[str, str, datetime]] = []                  # (kunci, judul, waktu mulai)
        for ev in self._cal_events:
            if not ev.all_day:
                upcoming.append((f"cal:{ev.id}@{ev.start.isoformat()}", ev.title, ev.start))
        if NOTE_COUNTDOWN_REMINDERS:
            for n in self.notes.with_deadline():
                dl = parse_dt(n.get("deadline"))
                if dl is not None:
                    upcoming.append((f"note:{n['id']}@{n['deadline']}", n.get("title") or "Note",
                                     dl.astimezone()))
        countdown: list[str] = []
        for key, title, start in upcoming:
            delta = start - aware_now
            if timedelta(0) < delta <= window:
                last = self._remind_last.get(key)
                if last is None or time.monotonic() - last >= interval_s:
                    self._remind_last[key] = time.monotonic()
                    countdown.append(f"{title}\nStarts at {start:%H:%M}, in {human_delta(delta)}")
            elif (key.startswith("cal:") and key not in self._remind_started
                  and -timedelta(seconds=EVENT_STARTED_GRACE_S) < delta <= timedelta(0)):
                self._remind_started.add(key)
                messages.append(f"STARTING NOW: {title} ({start:%H:%M})")
        if countdown:
            head = "Upcoming event:" if len(countdown) == 1 else "Upcoming events:"
            messages.append(head + "\n" + "\n\n".join(countdown) + "\n\n" + self._bag_agenda.next())

        if messages:
            self.say("\n\n".join(messages), "talking", ms=12000)   # state talking + balon
            self.pet.bump(1.3)                                      # melompat menarik perhatian
            play_beep()

    # -- SYSTEM: opsi cepat & mode tidur ---------------------------------------
    def _build_system_menu(self) -> None:
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        self.system_menu = m
        self.act_sys_status = m.addAction("SYSTEM STATUS (CPU / RAM)", self._act_item)
        self.act_sys_pomo = m.addAction("FOCUS TIMER", self._act_heal)
        self.act_sys_sleep = m.addAction("SLEEP MODE", self._toggle_sleep_mode)
        self.act_sys_bed = m.addAction("BEDTIME")
        self.act_sys_bed.setEnabled(False)
        m.addAction("SETTINGS...", self._open_settings_deferred)
        m.addSeparator()
        m.addAction("OPEN VS CODE", self._launch_vscode)
        m.addAction("OPEN TERMINAL", self._launch_terminal)
        m.aboutToShow.connect(self._refresh_system_menu)

    def _refresh_system_menu(self) -> None:
        if self.pomodoro.running:
            self.act_sys_pomo.setText(f"FOCUS TIMER  ({fmt_mmss(self.pomodoro.remaining())} left)")
        else:
            mins = max(1, round(self.pomodoro.focus_s / 60))
            self.act_sys_pomo.setText(f"START FOCUS  ({mins} min)")
        self.act_sys_sleep.setText("WAKE UP  (Disable Sleep Mode)" if self.sleep_mode
                                   else "SLEEP MODE  (Take a Break)")
        self.act_sys_bed.setText(self._bedtime_text())

    def _bedtime_text(self) -> str:
        if not self.curfew_enabled:
            return "BEDTIME: Off"
        bed = self.settings.bedtime
        if self.curfew_active:
            return f"BEDTIME: Past {bed:%H:%M}"
        now = datetime.now()
        target = datetime.combine(now.date(), bed)
        if target <= now:
            target += timedelta(days=1)
        return f"BEDTIME: {bed:%H:%M}  (in {human_delta(target - now)})"

    def _open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(
                self.settings,
                get_on_top=lambda: self.on_top,
                get_autostart=autostart.is_enabled,
            )
            self.settings_dialog.set_on_top(self.on_top)
            self.settings_dialog.saved.connect(self.apply_settings)
            self.settings_dialog.always_on_top_changed.connect(self.set_always_on_top)
            self.settings_dialog.autostart_changed.connect(self._toggle_autostart)
        # If Notes or Tasks is currently open, center Settings directly over it so it's never buried
        for dlg in (self.notes_dialog, self.tasks_dialog):
            if dlg is not None and dlg.isVisible():
                self.settings_dialog.show_near(dlg.geometry(), prefer_center=True)
                return
        self.settings_dialog.show_near(self.pet.geometry())

    def apply_settings(self) -> None:
        """Apply saved settings immediately without restarting."""
        self.curfew_enabled = bool(self.settings["curfew_enabled"])
        self.act_curfew.blockSignals(True)
        self.act_curfew.setChecked(self.curfew_enabled)
        self.act_curfew.blockSignals(False)
        self.pomodoro.focus_s = max(1, int(self.settings["focus_min"] * 60))
        self.pomodoro.rest_s = max(1, int(self.settings["rest_min"] * 60))
        self._last_curfew_msg = None
        self._check_curfew()

    def _toggle_sleep_mode(self) -> None:
        self.sleep_mode = not self.sleep_mode
        if self.sleep_mode:
            self.say("Sleep Mode is on. Reminders will still run—open SYSTEM again "
                     "whenever you want to wake me up.", "sleepy", 6000)
        else:
            self.say("I'm awake! Sleep Mode is off.", "talking", 3000)
        self._restore_state()

    def _act_item(self) -> None:
        snap = self.monitor.snapshot()
        if snap is None:
            self.say("The psutil package isn't installed yet. Run: pip install psutil")
            return
        text = (f"SYSTEM STATUS\nCPU : {snap.cpu:.0f}%\n"
                f"RAM : {snap.ram_percent:.0f}% ({snap.ram_used_gb:.1f} / {snap.ram_total_gb:.1f} GB)")
        heavy = snap.cpu >= 85 or snap.ram_percent >= 90
        if heavy:
            text += "\nCPU/RAM usage is high. Try closing apps you don't need."
        self.say(text, "action" if heavy else "talking", ms=6500)

    # -- Pomodoro (supportive focus companion) -----------------------------------
    def _act_heal(self) -> None:
        if self.pomodoro.running:
            t = fmt_mmss(self.pomodoro.remaining())
            pool = _FOCUS_PROGRESS if self.pomodoro.phase == PomodoroTimer.FOCUS else _REST_PROGRESS
            self.say(random.choice(pool).format(t=t))
        else:
            self.pomodoro.start()

    def _on_pomo_tick(self, phase: str, left: int) -> None:
        name = "FOCUS" if phase == PomodoroTimer.FOCUS else "REST"
        self.pet.set_badge(f"{name} {fmt_mmss(left)}")

    def _on_pomo_phase(self, phase: str) -> None:
        if phase == PomodoroTimer.FOCUS:
            t = fmt_mmss(self.pomodoro.focus_s)
            self.say(random.choice(_FOCUS_START).format(t=t), "action")
        else:
            t = fmt_mmss(self.pomodoro.rest_s)
            self.say(random.choice(_FOCUS_REST).format(t=t))

    def _on_pomo_finished(self) -> None:
        self.pet.set_badge("")
        n = self.pomodoro.completed
        word = "session" if n == 1 else "sessions"
        self.say(f"Break's over! That's {n} focus {word} completed today—you're doing awesome! "
                 "Open SYSTEM whenever you're ready for another round.", "action")

    def _stop_pomodoro(self) -> None:
        self.pomodoro.stop()
        self.say("Timer paused! Rest up if you need to—start a new session from SYSTEM whenever you're ready.")

    # -- launchers ---------------------------------------------------------------
    def _launch_vscode(self) -> None:
        cmd = find_vscode()
        if cmd and launch_detached(cmd):
            self.say("VS Code is open! Let's get some good work done.", "action")
        else:
            self.say("Couldn't find VS Code. Make sure the 'code' command is on your PATH.")

    def _launch_terminal(self) -> None:
        cmd = find_terminal()
        if cmd and launch_detached(cmd):
            self.say("Terminal is open!", "action")
        else:
            self.say("Couldn't find a terminal. Set the TERMINAL environment variable or edit find_terminal().")

    # -- curfew ------------------------------------------------------------------
    def _check_curfew(self) -> None:
        now = datetime.now()
        active = self.curfew_enabled and in_window(now.time(), self.settings.bedtime, self.settings.wake)
        if active:
            if not self.curfew_active:
                self.curfew_active = True
                self._restore_state()
            interval_s = self.settings["curfew_interval_min"] * 60 - 15    # toleransi timer 30 dtk
            if (self._last_curfew_msg is None
                    or (now - self._last_curfew_msg).total_seconds() >= interval_s):
                self._last_curfew_msg = now
                self.say(self._bag_bedtime.next(), "sleepy", 12000)
        elif self.curfew_active:
            self.curfew_active = False
            self._last_curfew_msg = None
            self._restore_state()

    # -- menu klik kanan & tray ------------------------------------------------
    def _build_context_menu(self) -> None:
        m = QMenu()
        m.setStyleSheet(MENU_QSS)
        self.context_menu = m

        state_menu = m.addMenu("STATE")
        state_group = QActionGroup(m)
        state_group.setExclusive(True)
        for label, key in (("AUTO", "auto"), ("IDLE", "idle"), ("TALKING", "talking"),
                           ("SLEEPY", "sleepy"), ("ACTION", "action")):
            act = QAction(label, m)
            act.setCheckable(True)
            act.setData(key)
            act.setChecked(key == "auto")
            state_group.addAction(act)
            state_menu.addAction(act)
        state_group.triggered.connect(lambda a: self.set_forced_state(a.data()))

        expr_menu = m.addMenu("EXPRESSION")
        expr_group = QActionGroup(m)
        expr_group.setExclusive(True)
        for key in ("auto",) + tuple(EXPRESSIONS):
            act = QAction(key.upper(), m)
            act.setCheckable(True)
            act.setData(key)
            act.setChecked(key == "auto")
            expr_group.addAction(act)
            expr_menu.addAction(act)
        expr_group.triggered.connect(lambda a: self.set_forced_expression(a.data()))

        size_menu = m.addMenu("SPRITE SIZE")
        size_group = QActionGroup(m)
        size_group.setExclusive(True)
        for px in SPRITE_SIZES:
            act = QAction(f"{px} PX", m)
            act.setCheckable(True)
            act.setData(px)
            act.setChecked(px == self.args.size)
            size_group.addAction(act)
            size_menu.addAction(act)
        size_group.triggered.connect(lambda a: self.set_size(int(a.data())))

        m.addSeparator()
        self.act_top = m.addAction("ALWAYS ON TOP")
        self.act_top.setCheckable(True)
        self.act_top.setChecked(True)
        self.act_top.toggled.connect(self.set_always_on_top)

        self.act_curfew = m.addAction("BEDTIME REMINDER")
        self.act_curfew.setCheckable(True)
        self.act_curfew.setChecked(self.curfew_enabled)
        self.act_curfew.toggled.connect(self.set_curfew_enabled)
        m.addAction("SETTINGS...", self._open_settings_deferred)

        self.act_autostart = m.addAction("START ON BOOT")
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(autostart.is_enabled())
        self.act_autostart.triggered.connect(self._toggle_autostart)

        self.act_stop_pomo = m.addAction("STOP FOCUS TIMER", self._stop_pomodoro)
        m.addAction("RESET POSITION", self.reset_position)
        m.addSeparator()
        m.addAction("QUIT", self.quit)
        m.aboutToShow.connect(self._refresh_context_menu)

    def _refresh_context_menu(self) -> None:
        self.act_stop_pomo.setEnabled(self.pomodoro.running)
        self.act_autostart.setChecked(autostart.is_enabled())    # sinkron bila diubah dari luar

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(self.sprites.get("neutral", 64, 1.0)), self)
        self.tray.setToolTip(f"{PET_NAME.title()} - Coding Pet")
        self.tray.setContextMenu(self.context_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:   # klik kiri pada ikon tray
            if self.pet.isVisible():
                self.menu.hide()
                self.bubble.dismiss()
                self.pet.hide()
            else:
                self.pet.show()

    # -- aksi menu klik kanan ----------------------------------------------------
    def _toggle_autostart(self, checked: bool) -> None:
        ok, info = autostart.set_enabled(checked, SCRIPT_PATH)
        if ok:
            self.say("Start on boot is enabled! I'll show up automatically next time you turn on your PC." if checked
                     else "Start on boot is disabled.", ms=5000)
        else:
            self.act_autostart.setChecked(autostart.is_enabled())
            self.say(f"Couldn't update start on boot: {clip(info, 90)}", ms=8000)

    def set_forced_state(self, key: str) -> None:
        self.forced_state = None if key == "auto" else key
        self._restore_state()

    def set_forced_expression(self, key: str) -> None:
        self.forced_expression = None if key == "auto" else key
        self._restore_state()

    def set_size(self, px: int) -> None:
        self.menu.hide()
        self.pet.set_sprite_size(px)
        self.bubble.follow()

    def set_always_on_top(self, on: bool) -> None:
        self.on_top = on
        self.act_top.blockSignals(True)
        self.act_top.setChecked(on)
        self.act_top.blockSignals(False)
        was_visible = self.pet.isVisible()
        for w in (self.pet, self.bubble):
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.menu.set_on_top(on)
        for dlg in (self.tasks_dialog, self.notes_dialog, self.settings_dialog):
            if dlg is not None:
                dlg.set_on_top(on)
        if was_visible:
            self.pet.show()

    def set_curfew_enabled(self, on: bool) -> None:
        self.curfew_enabled = on
        self.settings.set(curfew_enabled=on)
        self._check_curfew()

    def reset_position(self) -> None:
        screen = QGuiApplication.screenAt(self.pet.geometry().center()) or QGuiApplication.primaryScreen()
        self._move_to_corner(screen)

    def _move_to_corner(self, screen) -> None:
        area = screen.availableGeometry()
        self.pet.move(area.right() - self.pet.width() - 24, area.bottom() - self.pet.height() - 24)


# ---------------------------------------------------------------------------
# 9. MAIN
# ---------------------------------------------------------------------------
def hhmm(value: str) -> dtime:
    return datetime.strptime(value, "%H:%M").time()


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    ap = argparse.ArgumentParser(description="Morgana Companion - Persona 5 desktop pet")
    ap.add_argument("--size", type=int, default=DEFAULT_SPRITE, help="sprite size in px (default 180)")
    ap.add_argument("--focus", type=float, default=None, help="Pomodoro focus duration in minutes for this session (default: Settings)")
    ap.add_argument("--rest", type=float, default=None, help="Pomodoro break duration in minutes for this session (default: Settings)")
    ap.add_argument("--curfew", type=hhmm, default=None, help="bedtime start HH:MM for this session (normally set in Settings)")
    ap.add_argument("--wake", type=hhmm, default=None, help="bedtime reminder stop HH:MM for this session")
    ap.add_argument("--chatter", type=float, default=4.0, help="average minutes between idle remarks (default 4)")
    ap.add_argument("--demo-reminders", action="store_true",
                    help="test: sample schedule (without credentials.json) also triggers upcoming reminders")
    ap.add_argument("--multi", action="store_true", help="allow multiple instances (for testing)")
    args, qt_args = ap.parse_known_args(argv[1:])
    return args, [argv[0]] + qt_args


def main() -> int:
    install_crash_logger()
    args, qt_argv = parse_args(sys.argv)

    # Wayland tidak mengizinkan aplikasi menentukan posisi / always-on-top jendelanya sendiri.
    # Di KDE Plasma (Wayland) kita pakai XWayland (xcb) supaya drag & always-on-top bekerja.
    # Matikan dengan: PET_NATIVE_WAYLAND=1 python main.py
    if (IS_LINUX and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            and "QT_QPA_PLATFORM" not in os.environ and not os.environ.get("PET_NATIVE_WAYLAND")):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    app = QApplication(qt_argv)
    app.setApplicationName("Desktop Coding Pet")
    app.setQuitOnLastWindowClosed(False)           # semua jendela bertipe Tool/overlay
    signal.signal(signal.SIGINT, signal.SIG_DFL)   # Ctrl+C di terminal tetap bisa menutup app

    # Satu instance saja (penting karena ada auto-start + menjalankan manual).
    lock = QLockFile(os.path.join(QDir.tempPath(), "desktop-coding-pet.lock"))
    lock.setStaleLockTime(0)                       # 0 = jangan anggap basi hanya karena lama
    if not args.multi and not lock.tryLock(300):
        msg = "Morgana Companion is already running (lock: desktop-coding-pet.lock in temp folder)."
        logging.getLogger("pet").info(msg)
        if sys.stderr:
            print(msg, file=sys.stderr)
        return 0

    controller = PetController(args)
    controller.start()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())