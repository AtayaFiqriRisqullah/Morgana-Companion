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
  audio_manager.py     efek suara UI, sekuens Social Stats P5R, dan voiceline Morgana (`assets/audio/`)
  lines.py             pembaca kalimat Morgana; isinya dari morgana_list.md (edit file itu, bukan kode)
  emotions.py          memilih ekspresi & voiceline Morgana dari isi dialog
  sprites.py           menyusun sprite Morgana (badan + patch mata + patch mulut)

Berkas yang dibuat otomatis: tasks.json, notes.json, social_stats.json, settings.json, token.json, pet.log.
Aset      : folder assets/ (Morgana/, fonts/, audio/).
            Tanpa folder assets/Morgana/ dipakai placeholder bergaya P5.
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
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from PyQt6.QtCore import (QDir, QLockFile, QObject, QPoint, QPointF, QRect, QRectF, Qt,
                          QTimer, pyqtSignal)
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QCursor, QFontMetrics, QGuiApplication,
                         QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                         QRegion, QShortcut, QTransform)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

import app_paths
import autostart
from audio_manager import init_audio, play_ui
from awareness import (AppAwarenessMonitor, ScreenTimeStore, format_duration, get_active_window,
                       get_idle_seconds)
from calendar_service import (CalendarEvent, CalendarResult, CalendarService, events_by_day,
                              format_agenda, human_delta, local_calendar_events,
                              merge_companion_items, note_deadline_events, task_deadline_events)
from command_engine import CommandResult, parse_and_plan
from dialogue_engine import DialogueEngine, DialogueMemoryStore
from emotions import EXPRESSIONS, STATE_EXPRESSION, emotion_for_text, voice_for_dialogue
from gemini_ai import (ACTION_LABELS, AIConfig, AIReply, AITalkScene, ConversationBuffer,
                       ask_morgana_chat, continue_joker_talk_choice, generate_ambient_reaction,
                       generate_joker_talk_scene, test_ai_connection)
from hotkeys import GlobalHotkeyManager
from motion import FrameTicker, Spring, clamp
from lines import (ContextualDialogue, LineBag, build_daily_briefing, build_evening_wrapup,
                   dnd_recovery_line, greeting_pool, load_lines)
from particles import ParticleOverlay
import sprites
from sprites import EYE_CLOSED, EYE_HALF, EYE_OPEN, MOUTH_BASE, MorganaSprites
from storage_manager import (RADIAL_TAB_KEYS, SOCIAL_STAT_KEYS, SOCIAL_STATS, FinanceStore,
                             HabitStore, LocalEventStore, NoteStore, SettingsStore,
                             SocialStatsStore, TaskStore, format_rupiah, overall_confidant_level,
                             parse_dt, stat_level, stat_rank_title)
from theme import (BLACK, BLUE, GOLD, LIGHT_RED, MENU_QSS, P5_MINT_FG,
                   P5_SURFACE, RED, UI_SCALE_PRESETS, WHITE, lerp_color,
                   load_fonts, menu_qss, p5_dialogue_font, p5_font, pen, scale_polygon,
                   star_polygon, tapered_wedge)
from ui_dialogs import (BriefingDialog, EventsDialog, FinanceDialog, NotesDialog,
                        QuickCommandDialog, SettingsDialog, SocialStatsDialog, TasksDialog,
                        TimerDialog, WrapUpDialog)
from sheets_sync import (SheetsPushResult, SheetsSyncConfig, push_tasks_to_sheets,
                         serialize_tasks_to_rows, test_sheets_connection)

try:
    import psutil
except ImportError:  # program tetap jalan; menu SYSTEM akan memberi tahu
    psutil = None

# ---------------------------------------------------------------------------
# 1. KONFIGURASI & HELPER
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

SCRIPT_PATH = Path(sys.executable).resolve() if app_paths.is_frozen() else Path(__file__).resolve()
APP_DIR = app_paths.app_dir()
LOG_FILE = APP_DIR / "pet.log"

PET_NAME = "MORGANA"
STATES = ("idle", "talking", "sleepy", "action")
DEFAULT_SPRITE = 180          # px
SPRITE_SIZES = (120, 150, 180, 240)
PAD = 22                      # ruang di sekeliling sprite (untuk melayang, miring, squash & stretch)

LINES_FILE = app_paths.resource_path("morgana_list.md")   # custom beside exe, else bundled
NOTE_COUNTDOWN_REMINDERS = True           # catatan ber-deadline ikut diingatkan H-2 jam seperti acara kalender
EVENT_STARTED_GRACE_S = 90                # pesan "acara dimulai sekarang" dalam N detik setelah mulai
REMINDER_CHECK_MS = 30_000    # cek deadline catatan & acara tiap 30 detik
CALENDAR_REFRESH_MS = 10 * 60_000
SHEETS_SYNC_STARTUP_DELAY_MS = 6_000              # push once after boot if enabled
SHEETS_SYNC_DEBOUNCE_MS = 2_500                   # batch rapid task edits into one Sheets write
SHEETS_SYNC_RETRY_DELAYS_MS = (15_000, 30_000, 60_000)  # backoff only while offline/dirty
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


def fmt_hms(seconds: int) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}" if h > 0 else f"{m:02d}:{sec:02d}"


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
    double_clicked = pyqtSignal()          # double-click kiri: ajak ngobrol interaktif
    moved = pyqtSignal()                   # posisi berubah (bubble & menu mengikuti)
    context_requested = pyqtSignal(QPoint)  # klik kanan
    wall_bounced = pyqtSignal()            # memantul kencang di tepi layar

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
        self._hud: tuple[str, str, float, str, str] | None = None
        self._ui_scale = 1.0
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

        self._resize_window(keep_center=False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._ticker = FrameTicker(self._frame, self)

    # -- properti & setter ---------------------------------------------------
    @property
    def sprite_size(self) -> int:
        return self._sprite_size

    def hud_top_local(self) -> int:
        return PAD + int(round(self._sprite_size * 0.97)) + 4

    def hud_height(self) -> int:
        return max(22, int(round(27 * self._ui_scale)))

    def _resize_window(self, keep_center: bool = True) -> None:
        old_center = self.sprite_center() if keep_center else None
        w = max(self._sprite_size + 2 * PAD, int(round(176 * self._ui_scale)) + 16)
        h = self.hud_top_local() + self.hud_height() + 10
        self.setFixedSize(w, h)
        if old_center is not None:
            self.move(old_center.x() - w // 2, old_center.y() - (PAD + self._sprite_size // 2))

    def sprite_center(self) -> QPoint:
        return QPoint(self.x() + self.width() // 2, self.y() + PAD + self._sprite_size // 2)

    def visual_top(self) -> int:
        return self.y() + PAD + int(round(self._sprite_size * 0.08))

    def visual_bottom(self) -> int:
        if self._hud or self._badge:
            return self.y() + self.hud_top_local() + self.hud_height() + 2
        return self.y() + PAD + int(round(self._sprite_size * 0.93))

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

    def set_hud(self, tag: str, clock: str, progress: float = 0.0,
                accent: str = "red", extra: str = "") -> None:
        state = (tag, clock, round(clamp(progress, 0.0, 1.0), 3), accent, extra)
        if state != self._hud:
            self._hud = state
            self.update()

    def clear_hud(self) -> None:
        if self._hud is not None or self._badge:
            self._hud = None
            self._badge = ""
            self.update()

    def set_ui_scale(self, scale: float) -> None:
        self._ui_scale = max(0.75, min(1.5, float(scale)))
        self._resize_window(keep_center=True)
        self.update()

    def set_sprite_size(self, size: int) -> None:
        self._sprite_size = size
        self._resize_window(keep_center=True)
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
            if speed > 360.0:
                self.wall_bounced.emit()
        return True

    # -- menggambar ----------------------------------------------------------
    def _draw_sprite(self, p: QPainter, expr: str) -> None:
        eye, mouth = (self._eye, self._mouth) if expr == self._expr else (EYE_OPEN, MOUTH_BASE)
        pm = self._sprites.get(expr, self._sprite_size, self.devicePixelRatioF(), eye, mouth)
        dpr = pm.devicePixelRatio()
        w, h = pm.width() / dpr, pm.height() / dpr
        x = (self.width() - w) / 2.0
        y = PAD + (self._sprite_size - h) / 2.0
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
        if self._hud or self._badge:
            self._paint_badge(p)

    def _paint_badge(self, p: QPainter) -> None:
        if self._hud:
            tag, clock, prog, accent_key, extra = self._hud
        else:
            parts = self._badge.split(" ", 1)
            tag = parts[0]
            clock = parts[1] if len(parts) > 1 else ""
            prog, accent_key, extra = 0.0, "red", ""

        accent = P5_MINT_FG if accent_key == "mint" else (GOLD if accent_key == "gold" else RED)
        tag_fg = BLACK if accent_key in ("mint", "gold") else WHITE

        sc = self._ui_scale
        for _ in range(3):
            tag_f = p5_font(max(9, int(round(11 * sc))), italic=False)
            clk_f = p5_font(max(13, int(round(16 * sc))), italic=False)
            ext_f = p5_font(max(9, int(round(10 * sc))), italic=False)
            tw_tag = QFontMetrics(tag_f).horizontalAdvance(tag) + int(round(14 * sc))
            tw_clk = QFontMetrics(clk_f).horizontalAdvance(clock) + int(round(16 * sc))
            tw_ext = (QFontMetrics(ext_f).horizontalAdvance(extra) + int(round(10 * sc))) if extra else 0
            skew = max(5.0, 7.0 * sc)
            w = tw_tag + tw_clk + tw_ext + skew
            if w <= self.width() - 10 or sc <= 0.65:
                break
            sc *= (self.width() - 12) / max(1.0, w)

        h = float(self.hud_height())
        x = (self.width() - w) / 2.0
        y = float(self.hud_top_local())

        outer = QPolygonF([
            QPointF(x + skew, y),
            QPointF(x + w, y),
            QPointF(x + w - skew, y + h),
            QPointF(x, y + h),
        ])
        # Signature Persona 5 offset accent shadow
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawPolygon(outer.translated(3.0, 3.0))

        # Obsidian main body
        p.setBrush(BLACK)
        p.drawPolygon(outer)

        # Clip interior for slanted phase slash + progress track
        clip = QPainterPath()
        clip.addPolygon(outer)
        clip.closeSubpath()
        p.save()
        p.setClipPath(clip)

        slash_x = x + skew + tw_tag
        slash = QPolygonF([
            QPointF(x, y),
            QPointF(slash_x + 2.0, y),
            QPointF(slash_x - skew + 2.0, y + h),
            QPointF(x, y + h),
        ])
        p.setBrush(accent)
        p.drawPolygon(slash)

        # Bottom progress strip inside HUD
        bar_h = max(2.5, 3.5 * sc)
        bar_y = y + h - bar_h - 0.5
        p.setBrush(P5_SURFACE)
        p.drawRect(QRectF(x, bar_y, w, bar_h))
        if prog > 0.005:
            p.setBrush(accent)
            p.drawRect(QRectF(x, bar_y, w * prog, bar_h))
        p.restore()

        # Crisp white Persona 5 border
        p.setPen(pen(WHITE, 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(outer)

        # Phase tag text
        text_h = h - bar_h
        p.setFont(tag_f)
        p.setPen(tag_fg)
        p.drawText(QRectF(x + skew * 0.5, y, tw_tag, text_h),
                   Qt.AlignmentFlag.AlignCenter.value, tag)

        # Countdown digits
        clk_x = x + skew * 0.5 + tw_tag
        p.setFont(clk_f)
        p.setPen(WHITE)
        p.drawText(QRectF(clk_x, y, tw_clk, text_h),
                   Qt.AlignmentFlag.AlignCenter.value, clock)

        # Optional extra counter (+1)
        if extra:
            p.setFont(ext_f)
            p.setPen(GOLD)
            p.drawText(QRectF(clk_x + tw_clk - 4 * sc, y, tw_ext, text_h),
                       Qt.AlignmentFlag.AlignCenter.value, extra)
        p.restore()

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

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._ignore_next_release = True
            self._press_global = None
            self._dragging = False
            self.bump(1.1)
            self.double_clicked.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if getattr(self, "_ignore_next_release", False):
            self._ignore_next_release = False
            e.accept()
            return
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


def paginate_text(text: str, measure, max_w: int, max_lines: int = 3) -> list[list[str]]:
    """Wrap `text` and split into Persona 5 dialogue pages of at most `max_lines` lines."""
    blocks = [blk.strip() for blk in text.split("\n\n") if blk.strip()]
    if not blocks:
        return [[""]]
    pages: list[list[str]] = []
    cur: list[str] = []
    for blk in blocks:
        wrapped = [ln for ln in wrap_text(blk, measure, max_w) if ln != ""]
        if not wrapped:
            continue
        if cur and len(cur) + len(wrapped) <= max_lines:
            cur.extend(wrapped)
            continue
        if cur:
            pages.append(cur)
            cur = []
        while len(wrapped) > max_lines:
            pages.append(wrapped[:max_lines])
            wrapped = wrapped[max_lines:]
        cur = wrapped
    if cur:
        pages.append(cur)
    return pages or [[""]]


class BubbleWindow(QWidget):
    """Persona 5 dialogue bubble using `Morgana/textbox.png`.

    Always scales `textbox.png` UNIFORMLY (sx == sy, capped at 1.0) so the silhouette,
    white nameplate, fangs, and right accent triangle NEVER stretch vertically.
    Messages longer than 3 lines automatically paginate like Persona 5 dialogue
    (click or wait to advance pages). Falls back to a drawn P5 box if the PNG is missing.
    """

    dismissed = pyqtSignal()
    typing_finished = pyqtSignal()          # current page finished typing
    page_started = pyqtSignal()             # new page started typing (resumes lip-sync)

    MAX_TEXT_W = 355
    MAX_PAGE_LINES = 3
    M, PADX, PADY = 22, 18, 8
    TAIL_H, TAG_OVER, SHADOW, SLANT = 16, 12, 7, 10   # fallback box only
    # textbox.png (600x173): white tilted nameplate at top-left, black text area x=126..460,
    # y=56..126. Only the middle horizontal slice [IMG_L..IMG_R] stretches horizontally;
    # height & side caps always share one uniform scale `self._s` (0.86..1.0).
    IMG_W, IMG_H = 600, 173
    IMG_L, IMG_R = 225, 460
    TEXT_L = 126
    IMG_TOP, IMG_BOT = 61, 130

    def __init__(self, pet: PetWindow) -> None:
        super().__init__()
        setup_overlay(self)
        self._pet = pet
        self._menu = None
        self._avoid_rects_fn = None
        self._pages: list[list[str]] = []
        self._page_idx = 0
        self._lines: list[str] = []
        self._total = 0
        self._reveal = 0.0
        self._speed = 60.0
        self._per_page_ms = 4000
        self._bw = self._bh = 0
        self._line_h = 20
        self._ascent = 14
        self._below = True
        self._tail_x = 0
        self._closing = False
        self._ui_scale = 1.0
        self._font = p5_dialogue_font(15)
        self._tag_font = p5_font(11, italic=False)
        self._page_font = p5_font(11, italic=False)
        self._box = QPixmap(str(sprites.SPRITE_DIR / "textbox.png"))
        self._use_img = not self._box.isNull()
        self._tail_h = 0 if self._use_img else self.TAIL_H
        self._s = 0.9                              # uniform scale for both X caps and Y height
        self._cap_l = self._cap_r = 0.0
        self._mid_w = 0.0
        self._text_h = 0
        self._show = Spring(0.0, 300.0, 19.0)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_page_timeout)
        self._ticker = FrameTicker(self._frame, self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_menu(self, menu) -> None:
        self._menu = menu

    def set_avoid_rects(self, fn) -> None:
        self._avoid_rects_fn = fn

    def set_ui_scale(self, scale: float) -> None:
        sc = max(0.75, min(1.5, float(scale)))
        if abs(sc - self._ui_scale) < 0.001:
            return
        self._ui_scale = sc
        self._font = p5_dialogue_font(max(11, int(round(15 * sc))))
        self._tag_font = p5_font(max(9, int(round(11 * sc))), italic=False)
        self._page_font = p5_font(max(9, int(round(11 * sc))), italic=False)
        if self.isVisible() and self._pages:
            self._apply_page(self._page_idx, pop_bounce=False)

    def _apply_page(self, idx: int, pop_bounce: bool = False) -> None:
        self._page_idx = idx
        self._lines = self._pages[idx]
        fm = QFontMetrics(self._font)
        self._line_h = fm.height() + 1
        self._ascent = fm.ascent()
        tw = max((fm.horizontalAdvance(ln) for ln in self._lines), default=0) + 8
        badge_pad = 54 if len(self._pages) > 1 else 0
        self._text_h = len(self._lines) * self._line_h - 1
        if self._use_img:
            # Uniform scale ONLY — never distort vertically!
            self._s = (0.88 if len(self._lines) <= 2 else 1.04) * self._ui_scale
            self._cap_l = self.IMG_L * self._s
            self._cap_r = (self.IMG_W - self.IMG_R) * self._s
            left_usable = (self.IMG_L - self.TEXT_L) * self._s
            self._mid_w = max(48.0 * self._ui_scale, tw + self.PADX * self._ui_scale + badge_pad * self._ui_scale - left_usable)
            self._bw = int(self._cap_l + self._mid_w + self._cap_r)
            self._bh = int(self.IMG_H * self._s)
        else:
            self._bw = max(tw, 60) + 2 * self.PADX
            self._bh = len(self._lines) * self._line_h + 2 * self.PADY - 2
        self._total = sum(len(ln) for ln in self._lines)
        self._reveal = 0.0
        self._speed = max(55.0, self._total / 1.05)
        tag_over = 0 if self._use_img else self.TAG_OVER
        shadow = 0 if self._use_img else self.SHADOW
        self.setFixedSize(self._bw + 2 * self.M + shadow,
                          self._bh + tag_over + self._tail_h + 2 * self.M + shadow)
        if pop_bounce and self.isVisible():
            self._show.value = min(self._show.value, 0.86)
            self._show.velocity = 0.0
        self._place()
        self._ticker.start()
        if getattr(self, "_hold", False) and self._page_idx + 1 >= len(self._pages):
            # Safety auto-dismiss (45s idle) so an ignored Choice Prompt never freezes Morgana's face
            self._hide_timer.start(45_000)
        else:
            self._hide_timer.start(self._per_page_ms + int(1000 * self._total / self._speed))

    def bump_hold_timeout(self) -> None:
        """Refresh the 45s safety timeout while the user is actively hovering/navigating choices."""
        if getattr(self, "_hold", False) and self.isVisible() and not self._closing:
            if self._page_idx + 1 >= len(self._pages):
                self._hide_timer.start(45_000)

    def show_message(self, text: str, ms: int, hold: bool = False) -> None:
        self._hold = bool(hold)
        self._need_dismiss_notify = True
        fm = QFontMetrics(self._font)
        max_w = max(240, int(round(self.MAX_TEXT_W * self._ui_scale)))
        if self._use_img:
            self._pages = paginate_text(text, fm.horizontalAdvance, max_w, self.MAX_PAGE_LINES)
        else:
            self._pages = [wrap_text(text, fm.horizontalAdvance, max_w)]
        self._per_page_ms = max(3500, ms // max(1, len(self._pages)))
        self._closing = False
        self._show.configure(300.0, 19.0)
        was_visible = self.isVisible()
        if not was_visible:
            self._show.snap(0.0)
        self._show.target = 1.0
        self._apply_page(0, pop_bounce=was_visible)
        self.show()
        self.raise_()

    def _on_page_timeout(self) -> None:
        if self._page_idx + 1 < len(self._pages):
            self._apply_page(self._page_idx + 1, pop_bounce=True)
            self.page_started.emit()
        else:
            self.dismiss()

    def dismiss(self) -> None:
        self._hold = False
        self._hide_timer.stop()
        if self.isVisible() and not self._closing:
            self._closing = True
            self._need_dismiss_notify = False
            self._show.configure(360.0)
            self._show.target = 0.0
            self._ticker.start()
            self.dismissed.emit()
        elif getattr(self, "_need_dismiss_notify", False):
            self._need_dismiss_notify = False
            self._closing = False
            self.dismissed.emit()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._hold = False
        self._closing = False
        self._hide_timer.stop()
        if getattr(self, "_need_dismiss_notify", False):
            self._need_dismiss_notify = False
            self.dismissed.emit()

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

    def visible_global_rect(self) -> QRect:
        """Exact visible bounding rect of the dialogue box in global screen coordinates."""
        tag_over = 0 if self._use_img else self.TAG_OVER
        shadow = 0 if self._use_img else self.SHADOW
        top = self.M + tag_over + (self._tail_h if self._below else 0)
        return QRect(self.x() + self.M, self.y() + top - tag_over,
                     self._bw + shadow, self._bh + tag_over + shadow)

    def _place(self) -> None:
        center = self._pet.sprite_center()
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w = self.width()
        tag_over = 0 if self._use_img else self.TAG_OVER
        shadow = 0 if self._use_img else self.SHADOW
        gap = 4
        menu_open = self._menu is not None and self._menu.is_open
        has_hud = bool(self._pet._hud or self._pet._badge)

        # Default: strictly BELOW Morgana (clearing HUD badge AND open radial menu wedges)
        bottom_barrier = self._pet.visual_bottom()
        top_barrier = self._pet.visual_top()
        if menu_open:
            bottom_barrier = max(bottom_barrier, self._menu.wedges_global_bottom())
            top_barrier = min(top_barrier, self._menu.wedges_global_top())

        vis_top_below = self.M + (self._tail_h if not self._use_img else 0)
        vis_bot_below = self.M + tag_over + self._tail_h + self._bh + shadow
        vis_top_above = self.M
        vis_bot_above = self.M + tag_over + self._bh + self._tail_h + shadow
        vis_h = self._bh + tag_over + shadow

        x = max(area.left(), min(center.x() - w // 2, area.right() - w))

        def _optical_tuck(cand_x: int) -> int:
            """Tuck slightly into textbox.png's transparent top-center notch ONLY when the
            left-side tilted MORGANA nameplate stays completely clear of Morgana & HUD."""
            if not self._use_img or menu_open:
                return 0
            nameplate_right = cand_x + self.M + int(round(212.0 * self._s))
            if has_hud:
                hud_left = center.x() - int(round(92.0 * self._ui_scale))
                return int(round(14.0 * self._s)) if nameplate_right <= hud_left else 0
            pet_left = center.x() - int(round(self._pet.sprite_size * 0.34))
            return int(round(20.0 * self._s)) if nameplate_right <= pet_left else 0

        def _y_below_for(cand_x: int) -> int:
            return bottom_barrier + gap - vis_top_below - _optical_tuck(cand_x)

        y_below = _y_below_for(x)
        y_above = top_barrier - gap - vis_bot_above

        if y_below + vis_bot_below <= area.bottom() - 4:
            self._below = True
            y = y_below
        elif y_above + vis_top_above >= area.top() + 4:
            self._below = False
            y = y_above
        else:
            space_below = area.bottom() - bottom_barrier
            space_above = top_barrier - area.top()
            self._below = space_below >= space_above
            y = max(area.top() - vis_top_above,
                    min(y_below if self._below else y_above, area.bottom() - vis_bot_below))

        # Avoid overlapping open dialog windows while STAYING TETHERED to Morgana
        # (never teleport across a wide dialog far away from Morgana).
        obstacles = self._avoid_rects_fn() if self._avoid_rects_fn is not None else []
        if obstacles:
            def _tethered(cand_x: int) -> bool:
                left_e = cand_x + self.M
                right_e = left_e + self._bw + shadow
                return (left_e - 24) <= center.x() <= (right_e + 24)

            def _vis_rect(cand_x: int, cand_y: int, below: bool) -> QRect:
                vy = cand_y + (vis_top_below if below else vis_top_above)
                return QRect(cand_x + self.M, vy, self._bw + shadow, vis_h)

            def _clear(cand_x: int, cand_y: int, below: bool) -> bool:
                if cand_x < area.left() or cand_x + w > area.right():
                    return False
                if below and cand_y + vis_bot_below > area.bottom() - 2:
                    return False
                if not below and cand_y + vis_top_above < area.top() + 2:
                    return False
                vr = _vis_rect(cand_x, cand_y, below)
                return all(not vr.intersects(ob) for ob in obstacles)

            if not _clear(x, y, self._below):
                hit = next((ob for ob in obstacles if _vis_rect(x, y, self._below).intersects(ob)), None)
                placed = False
                if hit is not None:
                    rx = hit.right() + gap - self.M
                    lx = hit.left() - gap - self._bw - shadow - self.M
                    # 1) Try small tethered horizontal shift on the current vertical side
                    for cx in (rx, lx):
                        if _tethered(cx):
                            cy = _y_below_for(cx) if self._below else y_above
                            if _clear(cx, cy, self._below):
                                x, y = cx, cy
                                placed = True
                                break
                    # 2) Try flipping to the opposite side of Morgana (above <-> below)
                    if not placed:
                        alt_below = not self._below
                        for cx in (x, rx, lx):
                            if _tethered(cx):
                                cy = _y_below_for(cx) if alt_below else y_above
                                if _clear(cx, cy, alt_below):
                                    x, y, self._below = cx, cy, alt_below
                                    placed = True
                                    break
                    # 3) Fallback: stay tightly anchored to Morgana, nudged cleanly away from dialog
                    if not placed:
                        if center.x() >= hit.center().x():
                            cx = min(area.right() - w, max(x, rx))
                        else:
                            cx = max(area.left(), min(x, lx))
                        if _tethered(cx):
                            x = cx
                            if self._below:
                                y = min(_y_below_for(x), area.bottom() - vis_bot_below)

        self._tail_x = max(self.M + 24, min(center.x() - x, w - self.M - self.SHADOW - 34))
        self.move(x, y)

    def _paint_box(self, p: QPainter, x0: float, y0: float) -> None:
        """Draw uniformly-scaled textbox.png (3 horizontal slices) + MORGANA inside its white nameplate."""
        pm = self._box
        s_ = self._s
        h = self.IMG_H * s_
        L, R = self.IMG_L, self.IMG_R
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(x0 + self._cap_l - 0.6, y0, self._mid_w + 1.2, h), pm,
                     QRectF(L, 0, R - L, self.IMG_H))
        p.drawPixmap(QRectF(x0, y0, self._cap_l, h), pm, QRectF(0, 0, L, self.IMG_H))
        p.drawPixmap(QRectF(x0 + self._cap_l + self._mid_w, y0, self._cap_r, h), pm,
                     QRectF(R, 0, self.IMG_W - R, self.IMG_H))

        # White tilted nameplate inside textbox.png: constant -14.8 deg angle since sx == sy
        p.save()
        p.translate(x0 + 129.0 * s_, y0 + 38.0 * s_)
        p.rotate(math.degrees(math.atan2(11.0 - 44.0, 185.0 - 60.0)))
        tag_f = p5_font(max(16, int(21 * s_)), italic=False)
        p.setFont(tag_f)
        p.setPen(BLACK)
        p.drawText(QRectF(-64.0 * s_, -15.0 * s_, 128.0 * s_, 30.0 * s_),
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

        p.save()
        p.translate(self.SHADOW, self.SHADOW)
        p.fillPath(path, RED)
        p.restore()
        p.setPen(pen(WHITE, 3))
        p.setBrush(BLACK)
        p.drawPath(path)

        p.save()
        p.translate(x1 + 1, y0 - 1)
        p.rotate((1.0 - clamp(sv)) * 90.0 + 12.0)
        p.setPen(pen(WHITE, 2))
        p.setBrush(RED)
        p.drawPolygon(star_polygon(0, 0, 11, 5, 5))
        p.restore()

        p.setFont(self._tag_font)
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

        scale = 0.55 + 0.45 * max(sv, 0.0)
        p.translate(tx, tip_y)
        p.rotate((1.0 - clamp(sv, 0.0, 1.5)) * -7.0)
        p.scale(scale, scale)
        p.translate(-tx, -tip_y)

        if self._use_img:
            self._paint_box(p, x0, y0)
            text_x = x0 + self.TEXT_L * self._s
            inner_h = (self.IMG_BOT - self.IMG_TOP) * self._s
            text_y = y0 + self.IMG_TOP * self._s + (inner_h - self._text_h) / 2 + self._ascent
        else:
            self._paint_box_fallback(p, x0, y0, tx, sv)
            text_x = x0 + self.PADX
            text_y = y0 + self.PADY + self._ascent

        p.setFont(self._font)
        p.setPen(WHITE)
        remaining = int(self._reveal)
        ty = text_y
        for line in self._lines:
            if remaining <= 0:
                break
            p.drawText(QPointF(text_x, ty), line[:remaining])
            remaining -= len(line)
            ty += self._line_h

        # Multi-page P5 badge (bottom-right of black interior)
        if self._use_img and len(self._pages) > 1:
            label = f"{self._page_idx + 1}/{len(self._pages)} ▼"
            p.setFont(self._page_font)
            bw = QFontMetrics(self._page_font).horizontalAdvance(label) + 12
            bh = 17
            bx = x0 + self._cap_l + self._mid_w - bw - 4
            by = y0 + (self.IMG_BOT + 10) * self._s - bh
            p.setPen(pen(WHITE, 1.4))
            p.setBrush(RED)
            p.drawRect(QRectF(bx, by, bw, bh))
            p.setPen(WHITE)
            p.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter.value, label)

    def mousePressEvent(self, e) -> None:
        if self._reveal < self._total:
            self._reveal = float(self._total)
            self.typing_finished.emit()
            self.update()
            return
        if self._page_idx + 1 < len(self._pages):
            self._apply_page(self._page_idx + 1, pop_bounce=True)
            self.page_started.emit()
        else:
            self.dismiss()


class ChoiceOverlay(QWidget):
    """Persona 5 slanted dialogue choice panel tethered cleanly to `BubbleWindow`.

    Displays 2-3 slanted black-and-white P5 response strips with crimson hover sweeps,
    spinning selection stars, keyboard shortcuts (`1`/`2`/`3`, arrows, `Enter`, `Esc`),
    and smart placement that never covers Morgana's sprite or the dialogue box.
    """

    choice_selected = pyqtSignal(int)
    cancelled = pyqtSignal()

    def __init__(self, bubble: BubbleWindow) -> None:
        super().__init__()
        setup_overlay(self)
        # ChoiceOverlay intentionally accepts keyboard focus (1/2/3, arrows, Enter, Esc)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._bubble = bubble
        self._choices: list[str] = []
        self._ui_scale = 1.0
        self._hovered = -1
        self._close_hovered = False
        self._pops: list[Spring] = []
        self._hovers: list[Spring] = []
        self._t = 0.0
        self._open = False
        self._row_rects: list[QRectF] = []
        self._close_rect = QRectF()
        self._ticker = FrameTicker(self._frame, self)

    @property
    def is_open(self) -> bool:
        return self._open and self.isVisible()

    def set_on_top(self, on: bool) -> None:
        was = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was and self._open:
            self.show()

    def set_ui_scale(self, scale: float) -> None:
        self._ui_scale = max(0.75, min(1.5, float(scale)))
        if self._open and self._choices:
            self._layout_choices()
            self.follow()

    def show_choices(self, choices: list[str]) -> None:
        if not choices:
            self.close_choices(silent=True)
            return
        self._choices = list(choices)
        self._hovered = -1
        self._close_hovered = False
        self._t = 0.0
        self._open = True
        self._pops = [Spring(0.0, 260.0, 17.0) for _ in self._choices]
        self._hovers = [Spring(0.0, 380.0, 26.0) for _ in self._choices]
        self._layout_choices()
        self.follow()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._ticker.start()

    def close_choices(self, silent: bool = True) -> None:
        if not self._open and not self.isVisible():
            return
        self._open = False
        self._hovered = -1
        self._close_hovered = False
        if not silent:
            play_ui("close")
        for sp in self._pops:
            sp.configure(380.0)
            sp.target = 0.0
        self._ticker.start()

    def _layout_choices(self) -> None:
        sc = self._ui_scale
        font = p5_dialogue_font(max(11, int(round(13.5 * sc))))
        fm = QFontMetrics(font)
        max_tw = max((fm.horizontalAdvance(c) for c in self._choices), default=140)
        row_w = max(int(248 * sc), min(int(430 * sc), max_tw + int(76 * sc)))
        row_h = max(28, int(round(34 * sc)))
        gap = max(5, int(round(7 * sc)))
        pad_l = int(round(28 * sc))
        pad_r = int(round(18 * sc))
        pad_t = int(round(8 * sc))
        close_h = max(18, int(round(20 * sc)))
        pad_b = int(round(10 * sc))

        self._row_rects = []
        y = float(pad_t)
        for i in range(len(self._choices)):
            # Subtle Persona 5 cascade slant across rows
            x_off = float(pad_l + i * int(round(6 * sc)))
            self._row_rects.append(QRectF(x_off, y, float(row_w), float(row_h)))
            y += row_h + gap

        total_w = int(pad_l + row_w + len(self._choices) * int(round(6 * sc)) + pad_r)
        close_w = int(round(76 * sc))
        self._close_rect = QRectF(total_w - pad_r - close_w, y + 1.0, float(close_w), float(close_h))
        total_h = int(y + close_h + pad_b)
        self.setFixedSize(total_w, total_h)

    @staticmethod
    def _overlap_area(a: QRect, b: QRect) -> int:
        inter = a.intersected(b)
        return max(0, inter.width()) * max(0, inter.height()) if inter.isValid() else 0

    def follow(self) -> None:
        if not self.isVisible() and not self._open:
            return
        brect = self._bubble.visible_global_rect()
        pet = self._bubble._pet
        pet_rect = QRect(
            pet.x() + PAD - 6,
            pet.visual_top() - 6,
            pet.sprite_size + 12,
            max(pet.sprite_size, pet.visual_bottom() - pet.visual_top()) + 12,
        )
        screen = QGuiApplication.screenAt(pet.sprite_center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        gap = 8

        def _clamp_x(cx: int) -> int:
            return max(area.left() + 6, min(cx, area.right() - w - 6))

        def _clamp_y(cy: int) -> int:
            return max(area.top() + 6, min(cy, area.bottom() - h - 6))

        cx_mid = _clamp_x(brect.center().x() - w // 2)
        x_left_both = min(brect.left(), pet_rect.left()) - gap - w
        x_right_both = max(brect.right(), pet_rect.right()) + gap
        x_left_pet = pet_rect.left() - gap - w
        x_right_pet = pet_rect.right() + gap
        if pet.sprite_center().x() >= area.center().x():
            side_xs = (x_left_both, x_left_pet, x_right_both, x_right_pet)
        else:
            side_xs = (x_right_both, x_right_pet, x_left_both, x_left_pet)

        y_above_b = brect.top() - gap - h
        y_below_b = brect.bottom() + gap
        y_above_p = pet_rect.top() - gap - h
        y_below_p = pet_rect.bottom() + gap
        y_beside_top = _clamp_y(brect.top())
        y_beside_mid = _clamp_y(brect.center().y() - h // 2)

        candidates: list[tuple[int, int]] = []
        if not self._bubble._below:
            # Bubble is ABOVE Morgana (typical bottom-corner layout):
            # stack choices ABOVE the bubble first so Morgana below is 100% unobstructed!
            candidates.append((cx_mid, y_above_b))
            for sx in side_xs:
                candidates.append((sx, y_beside_top))
                candidates.append((sx, y_beside_mid))
            candidates.append((cx_mid, y_below_p))
        else:
            # Bubble is BELOW Morgana (top/upper screen layout):
            # stack choices BELOW the bubble first so Morgana above is 100% unobstructed!
            candidates.append((cx_mid, y_below_b))
            for sx in side_xs:
                candidates.append((sx, y_beside_top))
                candidates.append((sx, y_beside_mid))
            candidates.append((cx_mid, y_above_p))

        # 1) Pick the first candidate cleanly inside the screen with ZERO overlap on Morgana & bubble
        for tx, ty in candidates:
            if (tx >= area.left() + 4 and tx + w <= area.right() - 4
                    and ty >= area.top() + 4 and ty + h <= area.bottom() - 4):
                cand = QRect(tx, ty, w, h)
                if self._overlap_area(cand, pet_rect) == 0 and self._overlap_area(cand, brect) == 0:
                    self.move(int(tx), int(ty))
                    return

        # 2) Fallback on very small screens: clamp into viewport while heavily protecting Morgana's sprite
        best_pos = (cx_mid, _clamp_y(y_above_b if not self._bubble._below else y_below_b))
        best_cost = float("inf")
        for tx, ty in candidates:
            cx, cy = _clamp_x(tx), _clamp_y(ty)
            cand = QRect(cx, cy, w, h)
            cost = 20 * self._overlap_area(cand, pet_rect) + self._overlap_area(cand, brect)
            if cost < best_cost:
                best_cost = cost
                best_pos = (cx, cy)
        self.move(int(best_pos[0]), int(best_pos[1]))

    def _frame(self, dt: float) -> bool:
        self._t += dt
        moving = False
        for i, sp in enumerate(self._pops):
            if self._open:
                sp.target = 1.0 if self._t >= i * 0.055 else 0.0
            else:
                sp.target = 0.0
            moving |= sp.step(dt)
        for hs in self._hovers:
            moving |= hs.step(dt)
        self.update()
        if not self._open and all(sp.value < 0.05 for sp in self._pops):
            self.hide()
            return False
        return moving or (self._open and self._hovered >= 0)

    def _hit_row(self, pos: QPointF) -> int:
        for i, r in enumerate(self._row_rects):
            if r.adjusted(-6, -2, 6, 2).contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e) -> None:
        if not self._open:
            return
        self._bubble.bump_hold_timeout()
        pos = e.position()
        idx = self._hit_row(pos)
        close_hit = self._close_rect.adjusted(-4, -2, 4, 2).contains(pos)
        if idx != self._hovered:
            self._hovered = idx
            if idx >= 0:
                play_ui("hover")
                self._ticker.start()
        self._close_hovered = close_hit
        for i, hs in enumerate(self._hovers):
            hs.target = 1.0 if i == idx else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor if (idx >= 0 or close_hit) else Qt.CursorShape.ArrowCursor)
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hovered = -1
        self._close_hovered = False
        for hs in self._hovers:
            hs.target = 0.0
        self.update()

    def mousePressEvent(self, e) -> None:
        if not self._open or e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        if self._close_rect.adjusted(-4, -2, 4, 2).contains(pos):
            self.close_choices(silent=False)
            self.cancelled.emit()
            return
        idx = self._hit_row(pos)
        if idx >= 0:
            play_ui("select")
            self.close_choices(silent=True)
            self.choice_selected.emit(idx)

    def keyPressEvent(self, e) -> None:
        if not self._open:
            return
        self._bubble.bump_hold_timeout()
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close_choices(silent=False)
            self.cancelled.emit()
            return
        if k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._choices:
            step = -1 if k == Qt.Key.Key_Up else 1
            nxt = (self._hovered + step) % len(self._choices) if self._hovered >= 0 else (0 if step > 0 else len(self._choices) - 1)
            self._hovered = nxt
            play_ui("hover")
            for i, hs in enumerate(self._hovers):
                hs.target = 1.0 if i == nxt else 0.0
            self._ticker.start()
            self.update()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and 0 <= self._hovered < len(self._choices):
            idx = self._hovered
            play_ui("select")
            self.close_choices(silent=True)
            self.choice_selected.emit(idx)
            return
        if Qt.Key.Key_1 <= k <= Qt.Key.Key_9:
            idx = int(k - Qt.Key.Key_1)
            if 0 <= idx < len(self._choices):
                play_ui("select")
                self.close_choices(silent=True)
                self.choice_selected.emit(idx)
                return
        super().keyPressEvent(e)

    def paintEvent(self, _event) -> None:
        if not self._choices:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        sc = self._ui_scale
        txt_font = p5_dialogue_font(max(11, int(round(13.5 * sc))))
        num_font = p5_font(max(11, int(round(13 * sc))), italic=False)
        close_font = p5_font(max(9, int(round(10 * sc))), italic=False)
        skew = 9.0 * sc

        for i, (label, r) in enumerate(zip(self._choices, self._row_rects)):
            pv = self._pops[i].value if i < len(self._pops) else 1.0
            hv = clamp(self._hovers[i].value if i < len(self._hovers) else 0.0)
            alpha = clamp(pv * 1.6)
            if alpha <= 0.02:
                continue

            p.save()
            p.setOpacity(alpha)
            slide_x = (1.0 - clamp(pv)) * -26.0 * sc + hv * 7.0 * sc
            p.translate(slide_x, 0.0)

            poly = QPolygonF([
                QPointF(r.left() + skew, r.top()),
                QPointF(r.right(), r.top()),
                QPointF(r.right() - skew, r.bottom()),
                QPointF(r.left(), r.bottom()),
            ])

            # Hard Persona shadow
            p.save()
            p.translate(3.5 * sc, 4.0 * sc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(lerp_color(RED, WHITE, hv))
            p.drawPolygon(poly)
            p.restore()

            # Slanted black strip with crimson hover sweep
            p.setPen(QPen(WHITE if hv > 0.2 else QColor("#D8D8E6"), (2.0 if hv > 0.2 else 1.6) * sc))
            p.setBrush(lerp_color(BLACK, RED, hv * 0.92))
            p.drawPolygon(poly)

            # White number badge (`1`, `2`, `3`)
            badge_w = 22.0 * sc
            badge = QPolygonF([
                QPointF(r.left() + skew + 4 * sc, r.top() + 4 * sc),
                QPointF(r.left() + skew + 4 * sc + badge_w, r.top() + 4 * sc),
                QPointF(r.left() + 4 * sc + badge_w, r.bottom() - 4 * sc),
                QPointF(r.left() + 4 * sc, r.bottom() - 4 * sc),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(GOLD if hv > 0.35 else WHITE)
            p.drawPolygon(badge)
            p.setFont(num_font)
            p.setPen(BLACK)
            p.drawText(badge.boundingRect(), Qt.AlignmentFlag.AlignCenter.value, str(i + 1))

            # Choice text
            p.setFont(txt_font)
            p.setPen(WHITE)
            tx = r.left() + skew + badge_w + 12.0 * sc
            tw = max(40.0, r.right() - tx - skew - 6.0 * sc)
            elided = QFontMetrics(txt_font).elidedText(label, Qt.TextElideMode.ElideRight, int(tw))
            p.drawText(QRectF(tx, r.top(), tw, r.height()),
                       Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value, elided)

            # Spinning Persona star on hover
            if hv > 0.05:
                p.save()
                p.translate(r.left() - 8.0 * sc, r.center().y())
                p.rotate(self._t * 150.0)
                st_sc = 0.3 + 0.7 * hv
                p.scale(st_sc, st_sc)
                p.setPen(QPen(BLACK, 1.8 * sc))
                p.setBrush(GOLD)
                p.drawPolygon(star_polygon(0.0, 0.0, 10.5 * sc, 4.8 * sc, 5))
                p.restore()

            p.restore()

        # Compact ESC / CLOSE pill
        if self._open and self._pops and self._pops[0].value > 0.35:
            p.save()
            p.setOpacity(clamp(self._pops[0].value))
            cr = self._close_rect
            p.setPen(QPen(WHITE if self._close_hovered else QColor("#666678"), 1.2 * sc))
            p.setBrush(RED if self._close_hovered else QColor(14, 14, 20, 225))
            p.drawRoundedRect(cr, 4.0, 4.0)
            p.setFont(close_font)
            p.setPen(WHITE)
            p.drawText(cr, Qt.AlignmentFlag.AlignCenter.value, "ESC · CLOSE")
            p.restore()
        p.end()


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


# Non-overlapping base fan slots per side count: (oy_base, rot_up_deg)
# Positive rot_up_deg tilts the outer tip upward (-Y) on both left and right sides.
_RADIAL_BASE_SLOTS: dict[int, tuple[tuple[float, float], ...]] = {
    1: ((   0.0,  +2.0),),
    2: (( -28.0, +10.0), ( +28.0,  -8.0)),
    3: (( -52.0, +18.0), (  -2.0,  +1.0), ( +50.0, -14.0)),
    4: (( -74.0, +24.0), ( -24.0,  +8.0), ( +24.0,  -7.0), ( +72.0, -21.0)),
    5: (( -96.0, +22.0), ( -48.0, +11.0), (   0.0,   0.0), ( +48.0, -11.0), ( +96.0, -22.0)),
    6: ((-112.5, +20.0), ( -67.5, +12.0), ( -22.5,  +4.0), ( +22.5,  -4.0), ( +67.5, -12.0), (+112.5, -20.0)),
    7: ((-126.0, +22.5), ( -84.0, +15.0), ( -42.0,  +7.5), (   0.0,   0.0), ( +42.0,  -7.5), ( +84.0, -15.0), (+126.0, -22.5)),
    8: ((-136.5, +24.5), ( -97.5, +17.5), ( -58.5, +10.5), ( -19.5,  +3.5), ( +19.5,  -3.5), ( +58.5, -10.5), ( +97.5, -17.5), (+136.5, -24.5)),
}


def _radial_side_ax(oy_base: float) -> float:
    """Curved horizontal clearance from Morgana's center (tighter above head, HUD-safe below)."""
    if oy_base < 0.0:
        a = abs(oy_base)
        if a <= 115.0:
            return 92.0 - 20.0 * (a / 115.0) ** 2
        return max(46.0, 72.0 - 0.22 * (a - 115.0))
    if oy_base <= 115.0:
        return 92.0 - 8.0 * (oy_base / 115.0) ** 2
    return max(56.0, 84.0 - 0.22 * (oy_base - 115.0))


def _radial_button_extents(
    side: int, ox_px: float, oy_px: float, rot_deg: float, u_btn: float,
    length: float = 176.0, h_in: float = 25.0, h_out: float = 48.0,
) -> tuple[float, float, float, float]:
    """Conservative pixel extents (min_x, max_x, min_y, max_y) of one wedge incl. hover/shadow/subtitle."""
    L = length * u_btn
    hi = h_in * u_btn
    ho = h_out * u_btn
    s = 1.08
    slide = 10.0 * u_btn
    if side > 0:
        x_in = -14.0 * u_btn
        x_out = slide + (L + 6.0 * u_btn) * s + 9.0 * u_btn
    else:
        x_in = +14.0 * u_btn
        x_out = -(slide + (L + 6.0 * u_btn) * s + 9.0 * u_btn)
    corners = (
        (x_in,  -hi * 0.56 * s - 4.0 * u_btn),
        (x_in,  +hi * 0.52 * s + 7.0 * u_btn),
        (x_out, -ho * 0.58 * s - 4.0 * u_btn),
        (x_out, +ho * 0.50 * s + 10.0 * u_btn),
    )
    ang = math.radians(rot_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    xs = [ox_px + lx * ca - ly * sa for lx, ly in corners]
    ys = [oy_px + lx * sa + ly * ca for lx, ly in corners]
    return min(xs), max(xs), min(ys), max(ys)


def _ray_clip_polygon(
    pts: tuple[tuple[float, float], ...],
    x_lo: float, x_hi: float, y_lo: float, y_hi: float,
    min_scale: float = 0.22,
) -> tuple[tuple[float, float], ...] | None:
    """Scale a spike polygon radially from (0, 0) so every tip stays sharp and inside screen bounds."""
    s = 1.0
    for vx, vy in pts:
        if vx < x_lo and vx < -1e-6:
            s = min(s, x_lo / vx)
        elif vx > x_hi and vx > 1e-6:
            s = min(s, x_hi / vx)
        if vy < y_lo and vy < -1e-6:
            s = min(s, y_lo / vy)
        elif vy > y_hi and vy > 1e-6:
            s = min(s, y_hi / vy)
    if s < min_scale:
        return None
    if s >= 0.999:
        return pts
    return tuple((vx * s, vy * s) for vx, vy in pts)


def _oriented_poly(
    angle: float, local_pts: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    """Rotate local (along, perp) points around (0, 0) to screen angle `angle` (radians)."""
    ca, sa = math.cos(angle), math.sin(angle)
    return tuple((u * ca - v * sa, u * sa + v * ca) for u, v in local_pts)


def _stepped_slash_poly(
    angle: float, length: float, half_w: float, step_sign: float = 1.0,
    notch_t: float = 0.60, back_anchor: float = 28.0,
) -> tuple[tuple[float, float], ...]:
    """Iconic Persona 5 diagonal crimson beam: straight outer edge + abrupt inward notch -> razor needle."""
    s = step_sign
    pts = (
        (-back_anchor,              -0.44 * half_w * s),
        (length * 0.42,             -0.48 * half_w * s),
        (length,                    -0.12 * half_w * s),   # ultra-sharp needle tip
        (length * (notch_t - 0.03), +0.06 * half_w * s),   # razor spear underside
        (length * (notch_t + 0.03), +0.62 * half_w * s),   # abrupt Persona 5 comic step!
        (length * 0.24,             +1.00 * half_w * s),   # broad angular crimson belly
        (-back_anchor,              +0.52 * half_w * s),
    )
    return _oriented_poly(angle, pts)


def _needle_poly(
    angle: float, length: float, half_w: float,
    back_anchor: float = 22.0, offset_v: float = 0.0,
) -> tuple[tuple[float, float], ...]:
    """Long sleek Persona 5 razor needle (used for lower-left crimson tail & piercing black spears)."""
    pts = (
        (-back_anchor,  offset_v - 0.55 * half_w),
        (length * 0.22, offset_v - 0.85 * half_w),
        (length,        offset_v),
        (length * 0.20, offset_v + 0.72 * half_w),
        (-back_anchor,  offset_v + 0.48 * half_w),
    )
    return _oriented_poly(angle, pts)


def _impact_wing_poly(
    side: int, phi_top: float, phi_bot: float, r_top: float, r_bot: float,
    u_sprite: float,
) -> tuple[tuple[float, float], ...]:
    """Bold irregular Persona 5 crimson cutout wing backing a side's button cluster (no repetitive spokes)."""
    r_in = 14.0 * u_sprite
    span = phi_bot - phi_top
    a_top = max(-1.38, phi_top - (0.14 if span < 0.25 else 0.10))
    a_bot = min(+1.38, phi_bot + (0.14 if span < 0.25 else 0.10))
    a_mid = 0.5 * (a_top + a_bot)
    r_mid = 0.5 * (r_top + r_bot)

    def _pt(a: float, r: float) -> tuple[float, float]:
        return (side * r * math.cos(a), r * math.sin(a))

    if span < 0.25:
        # Compact 1-tab angular cutout slash
        return (
            _pt(a_top, r_in),
            _pt(a_top, r_top * 0.90),
            _pt(a_mid - 0.04, r_mid * 0.72),
            _pt(a_mid + 0.05, r_mid * 1.02),
            _pt(a_bot, r_bot * 0.84),
            _pt(a_bot, r_in),
        )
    # Asymmetric 3-corner comic-cutout wing with one crisp inward notch
    a_q1 = a_top + 0.34 * (a_bot - a_top)
    a_q2 = a_top + 0.58 * (a_bot - a_top)
    return (
        _pt(a_top, r_in),
        _pt(a_top, r_top * 0.96),
        _pt(a_q1,  r_mid * 0.66),          # sharp comic inward notch
        _pt(a_q2,  r_mid * 0.98),          # bold outer angular corner
        _pt(a_bot, r_bot * 0.88),
        _pt(a_bot, r_in),
    )


def build_radial_burst_shards(
    placed: list[tuple[int, float, float, float, float]],
    u_sprite: float,
    u_btn: float,
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
) -> tuple[list[tuple[tuple[float, float], ...]], list[tuple[tuple[float, float], ...]]]:
    """Generate Persona 5 stepped diagonal crimson slash & piercing black counter-spears."""
    red: list[tuple[tuple[float, float], ...]] = []
    black: list[tuple[tuple[float, float], ...]] = []

    def _add(target: list[tuple[tuple[float, float], ...]],
             pts: tuple[tuple[float, float], ...], min_s: float = 0.22) -> None:
        clipped = _ray_clip_polygon(pts, x_lo, x_hi, y_lo, y_hi, min_scale=min_s)
        if clipped is not None:
            target.append(clipped)

    by_side: dict[int, list[tuple[float, float]]] = {-1: [], +1: []}
    for side, ox_px, oy_px, rot_deg, length_px in placed:
        ang = math.radians(rot_deg)
        tx = ox_px + side * math.cos(ang) * (0.78 * length_px)
        ty = oy_px + math.sin(ang) * (0.78 * length_px)
        phi = math.atan2(ty, max(1.0, side * tx))
        dist = math.hypot(tx, ty)
        by_side[side].append((phi, dist))

    active_sides = [s for s in (-1, +1) if by_side[s]]
    if not active_sides:
        return red, black

    max_r = 395.0 * u_btn
    # 1) Bold asymmetric crimson impact wing behind each active side's button cluster
    side_info: dict[int, tuple[float, float, float, float, int]] = {}
    for side in active_sides:
        rays = sorted(by_side[side], key=lambda item: item[0])
        phi_min, r_top = rays[0]
        phi_max, r_bot = rays[-1]
        r_top = min(r_top, max_r)
        r_bot = min(r_bot, max_r)
        side_info[side] = (phi_min, phi_max, r_top, r_bot, len(rays))
        _add(red, _impact_wing_poly(side, phi_min, phi_max, r_top, r_bot, u_sprite), min_s=0.30)

    # 2) Hero Stepped Diagonal Crimson Lightning-Slash + Piercing Black Counter-Spears
    if len(active_sides) == 2:
        # Classic Persona 5 Royal diagonal (reference image.png):
        # lower-left razor needle + black spear -> bold stepped crimson beam shooting upper-right!
        l_min, l_max, l_rt, l_rb, l_n = side_info[-1]
        r_min, r_max, r_rt, r_rb, r_n = side_info[+1]
        compact = (l_n + r_n) <= 2
        hero_ang = clamp(r_min - (0.14 if compact else 0.16), -0.82, -0.26)
        hero_len = min(max(r_rt * 1.44, 330.0 * u_btn), 460.0 * u_btn)
        hero_w = (30.0 if compact else 44.0) * u_btn
        tail_len = min(max(l_rb * 1.18, 255.0 * u_btn), 360.0 * u_btn)

        # Upper-right hero stepped beam (straight top edge, stepped bottom edge -> needle tip)
        _add(red, _stepped_slash_poly(hero_ang, hero_len, hero_w, step_sign=+1.0,
                                      notch_t=0.62, back_anchor=26.0 * u_sprite), min_s=0.35)
        # Lower-left razor crimson tail along exact opposite diagonal
        _add(red, _needle_poly(hero_ang + math.pi, tail_len, (14.0 if compact else 18.0) * u_btn,
                               back_anchor=24.0 * u_sprite), min_s=0.30)
        # Sharp upper-left crimson flare wing (like behind NETWORK in reference image)
        if not compact:
            ul_ang = math.pi - clamp(l_min - 0.12, -0.95, -0.18)
            _add(red, _needle_poly(ul_ang, l_rt * 1.08, 22.0 * u_btn, back_anchor=18.0 * u_sprite), min_s=0.30)

        # Piercing obsidian-black lower-left counter-spear (signature P5 contrast from image.png)
        _add(black, _needle_poly(hero_ang + math.pi + 0.075, tail_len * 0.94,
                                 (13.0 if compact else 18.0) * u_btn,
                                 back_anchor=20.0 * u_sprite, offset_v=-5.0 * u_btn), min_s=0.28)
        # Sleek secondary black accent dagger slicing under the right wing
        br_ang = clamp(r_max + 0.08, 0.08, 0.92)
        _add(black, _needle_poly(br_ang, r_rb * 0.90, 12.0 * u_btn,
                                 back_anchor=16.0 * u_sprite), min_s=0.28)
    else:
        # Single-sided mode (edge / corner / single tab): orient hero stepped slash into open quadrant!
        side = active_sides[0]
        phi_min, phi_max, r_top, r_bot, n = side_info[side]
        phi_mid = 0.5 * (phi_min + phi_max)
        go_down = phi_mid > 0.14   # Top corners: fan hangs downward -> shoot slash diagonally DOWN-OUTWARD
        if go_down:
            local_phi = clamp(phi_max + (0.14 if n == 1 else 0.02), +0.18, +1.08)
            lead_r = r_bot
            flank_phi = clamp(phi_min - 0.12, -1.20, +0.90)
            flank_r = r_top
            step_sign = -1.0 if side > 0 else +1.0
        else:
            local_phi = clamp(phi_min - (0.14 if n == 1 else 0.02), -1.08, -0.18)
            lead_r = r_top
            flank_phi = clamp(phi_max + 0.12, -0.90, +1.20)
            flank_r = r_bot
            step_sign = +1.0 if side > 0 else -1.0

        hero_ang = math.atan2(math.sin(local_phi), side * math.cos(local_phi))
        flank_ang = math.atan2(math.sin(flank_phi), side * math.cos(flank_phi))
        hero_len = min(max(lead_r * 1.46, 330.0 * u_btn), 520.0 * u_btn)
        hero_w = (28.0 if n == 1 else 44.0) * u_btn

        # Hero stepped crimson lightning-spear shooting past the leading outer tab
        _add(red, _stepped_slash_poly(hero_ang, hero_len, hero_w, step_sign=step_sign,
                                      notch_t=0.61, back_anchor=24.0 * u_sprite), min_s=0.35)
        # Compact opposite tail needle behind Morgana (auto ray-clipped cleanly inside corner/edge!)
        _add(red, _needle_poly(hero_ang + math.pi, 130.0 * u_sprite, 15.0 * u_sprite,
                               back_anchor=14.0 * u_sprite), min_s=0.35)
        # Secondary crimson counter-slash framing the opposite edge of the fan
        if n >= 2:
            _add(red, _needle_poly(flank_ang, flank_r * 1.04, 18.0 * u_btn,
                                   back_anchor=16.0 * u_sprite), min_s=0.30)

        # Piercing obsidian-black counter-spears (one along tail/back, one slicing across flank)
        _add(black, _needle_poly(hero_ang + math.pi + (0.09 if side > 0 else -0.09),
                                 118.0 * u_sprite, 14.0 * u_sprite,
                                 back_anchor=14.0 * u_sprite), min_s=0.35)
        _add(black, _needle_poly(flank_ang + (-0.06 if go_down else +0.06),
                                 flank_r * 0.88, 12.0 * u_btn,
                                 back_anchor=16.0 * u_sprite), min_s=0.28)

    return red, black


def compute_radial_layout(
    active_defs: list[tuple[str, str, str, int, int]],
    space_left: float,
    space_right: float,
    space_top: float,
    space_bottom: float,
    u_sprite: float,
    u_pos: float,
    u_btn: float,
    u_y: float,
    preferred_mode: str | None = None,
    hysteresis: float = 0.0,
) -> dict:
    """Compute screen-adaptive, balanced Persona 5 radial menu geometry (pure Qt-free math)."""
    if not active_defs:
        return {
            "mode": "both", "buttons": [], "u_pos": u_pos, "u_btn": u_btn, "u_y": u_y,
            "dy_px": 0.0, "fit_scale": 1.0, "red_shards": [], "black_shards": [],
            "w": 80, "h": 80, "cx": 40.0, "cy": 40.0, "btn_bounds": (0.0, 0.0, 0.0, 0.0),
        }
    margin = 10.0
    need_x = 92.0 * u_pos + 204.0 * u_btn + margin

    # 1) Select horizontal distribution mode: both sides, right-only, or left-only
    if len(active_defs) == 1:
        pref = "left" if active_defs[0][3] < 0 else "right"
        if preferred_mode == "left" and space_left >= need_x - hysteresis:
            mode = "left"
        elif preferred_mode == "right" and space_right >= need_x - hysteresis:
            mode = "right"
        elif pref == "left":
            mode = "left" if space_left >= need_x else ("right" if space_right >= space_left else "left")
        else:
            mode = "right" if space_right >= need_x else ("left" if space_left >= space_right else "right")
    else:
        l_need = need_x - hysteresis if preferred_mode in ("both", "left") else need_x + hysteresis
        r_need = need_x - hysteresis if preferred_mode in ("both", "right") else need_x + hysteresis
        fits_l = space_left >= l_need
        fits_r = space_right >= r_need
        if fits_l and fits_r:
            mode = "both"
        elif fits_r and not fits_l:
            mode = "right"
        elif fits_l and not fits_r:
            mode = "left"
        else:
            mode = "right" if space_right >= space_left else "left"

    # 2) Partition & balance active tabs across sides (keeps |L - R| <= 1 in two-sided mode)
    if mode == "right":
        left_defs: list[tuple[str, str, str, int, int]] = []
        right_defs: list[tuple[str, str, str, int, int]] = list(active_defs)
    elif mode == "left":
        left_defs = list(active_defs)
        right_defs = []
    else:
        left_defs = [d for d in active_defs if d[3] < 0]
        right_defs = [d for d in active_defs if d[3] > 0]
        order = {d[0]: i for i, d in enumerate(active_defs)}
        if len(left_defs) - len(right_defs) > 1:
            move = (len(left_defs) - len(right_defs)) // 2
            right_defs = sorted(right_defs + left_defs[-move:], key=lambda d: order[d[0]])
            left_defs = left_defs[:-move]
        elif len(right_defs) - len(left_defs) > 1:
            move = (len(right_defs) - len(left_defs)) // 2
            left_defs = sorted(left_defs + right_defs[-move:], key=lambda d: order[d[0]])
            right_defs = right_defs[:-move]

    def _build_placed(up: float, ub: float, uy: float, rot_bias: float, dy_px: float):
        dy_b = dy_px / max(0.01, uy)
        sides_data = ((-1, left_defs), (+1, right_defs))
        per_side: dict[int, list[dict]] = {-1: [], +1: []}
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for side, defs in sides_data:
            if not defs:
                continue
            slots = _RADIAL_BASE_SLOTS[min(8, len(defs))]
            for (key, label, sub, _pref_side, hi), (oy_b, rot_up) in zip(defs, slots):
                oy_final = oy_b + dy_b
                ax = _radial_side_ax(oy_final)
                ox_b = -ax if side < 0 else (ax - 2.0)
                rot_eff = clamp(rot_up + rot_bias, -30.0, 30.0)
                rot_deg = rot_eff if side < 0 else -rot_eff
                bmin_x, bmax_x, bmin_y, bmax_y = _radial_button_extents(
                    side, ox_b * up, oy_final * uy, rot_deg, ub,
                )
                min_x = min(min_x, bmin_x)
                max_x = max(max_x, bmax_x)
                min_y = min(min_y, bmin_y)
                max_y = max(max_y, bmax_y)
                per_side[side].append({
                    "key": key, "label": label, "sub": sub, "side": side,
                    "ox": ox_b, "oy": oy_final, "rot": rot_deg, "hi": hi,
                })
        return per_side, (min_x, max_x, min_y, max_y)

    # 3) Fit scale gracefully if screen is exceptionally small for the current UI scale
    up, ub, uy = u_pos, u_btn, u_y
    fit_scale = 1.0
    _, (min_x, max_x, min_y, max_y) = _build_placed(up, ub, uy, 0.0, 0.0)
    avail_h = max(160.0, (space_top + space_bottom) - 2.0 * margin)
    fan_h = max(1.0, max_y - min_y)
    v_fit = avail_h / fan_h if fan_h > avail_h else 1.0
    h_fit = 1.0
    if mode == "right" and max_x > max(120.0, space_right - margin):
        h_fit = max(120.0, space_right - margin) / max(1.0, max_x)
    elif mode == "left" and -min_x > max(120.0, space_left - margin):
        h_fit = max(120.0, space_left - margin) / max(1.0, -min_x)
    fit = clamp(min(v_fit, h_fit), 0.62, 1.0)
    if fit < 0.995:
        fit_scale = fit
        up *= fit
        ub *= fit
        uy *= fit
        _, (min_x, max_x, min_y, max_y) = _build_placed(up, ub, uy, 0.0, 0.0)

    # 4) Quadrant angle bias (keeps corners tightly hugged & radiating organically away from edges)
    max_base_rot = max(
        (abs(r) for n in (len(left_defs), len(right_defs)) if n > 0
         for _, r in _RADIAL_BASE_SLOTS[min(8, n)]),
        default=0.0,
    )
    max_bias = max(0.0, min(8.0, 30.0 - max_base_rot))
    over_bot = max(0.0, max_y - (space_bottom - margin))
    over_top = max(0.0, -min_y - (space_top - margin))
    if over_bot > 0.0 and over_top == 0.0:
        rot_bias = clamp(over_bot / max(1.0, 16.0 * ub), 0.0, max_bias)
    elif over_top > 0.0 and over_bot == 0.0:
        rot_bias = -clamp(over_top / max(1.0, 16.0 * ub), 0.0, max_bias)
    else:
        rot_bias = 0.0

    if abs(rot_bias) > 0.01:
        _, (min_x, max_x, min_y, max_y) = _build_placed(up, ub, uy, rot_bias, 0.0)

    # 5) Vertical shift so every wedge stays cleanly inside [area.top + margin, area.bottom - margin]
    y_lo_lim = -(space_top - margin)
    y_hi_lim = +(space_bottom - margin)
    dy_px = 0.0
    if max_y > y_hi_lim:
        dy_px = y_hi_lim - max_y
        if min_y + dy_px < y_lo_lim:
            dy_px = 0.5 * (y_lo_lim + y_hi_lim) - 0.5 * (min_y + max_y)
    elif min_y < y_lo_lim:
        dy_px = y_lo_lim - min_y
        if max_y + dy_px > y_hi_lim:
            dy_px = 0.5 * (y_lo_lim + y_hi_lim) - 0.5 * (min_y + max_y)

    per_side, btn_bounds = _build_placed(up, ub, uy, rot_bias, dy_px)
    # Interleave left/right so stagger opens symmetrically top-to-bottom
    ordered: list[dict] = []
    l_list, r_list = per_side[-1], per_side[+1]
    for idx in range(max(len(l_list), len(r_list))):
        if idx < len(l_list):
            ordered.append(l_list[idx])
        if idx < len(r_list):
            ordered.append(r_list[idx])

    # 6) Adaptive Persona 5 crimson & obsidian shards matched to placed buttons & screen edges
    shard_margin = 10.0
    sx_lo = -max(14.0, space_left - shard_margin)
    sx_hi = +max(14.0, space_right - shard_margin)
    sy_lo = -max(14.0, space_top - shard_margin)
    sy_hi = +max(14.0, space_bottom - shard_margin)
    placed_for_shards = [
        (b["side"], b["ox"] * up, b["oy"] * uy, b["rot"], 176.0 * ub)
        for b in ordered
    ]
    red_shards, black_shards = build_radial_burst_shards(
        placed_for_shards, u_sprite * fit_scale, ub, sx_lo, sx_hi, sy_lo, sy_hi,
    )

    # 7) Snug widget bounds & Morgana-center pivot (cx, cy) staying cleanly inside the screen
    win_min_x, win_max_x, win_min_y, win_max_y = btn_bounds
    core_pad = 32.0 * u_sprite
    win_min_x = min(win_min_x, -core_pad)
    win_max_x = max(win_max_x, +core_pad)
    win_min_y = min(win_min_y, -core_pad)
    win_max_y = max(win_max_y, +core_pad)
    # Include shard vertices with slight burst spring-overshoot headroom (1.07x)
    for poly in (*red_shards, *black_shards):
        for vx, vy in poly:
            win_min_x = min(win_min_x, vx * 1.07)
            win_max_x = max(win_max_x, vx * 1.07)
            win_min_y = min(win_min_y, vy * 1.07)
            win_max_y = max(win_max_y, vy * 1.07)

    pad = 8.0 * ub
    win_min_x -= pad
    win_max_x += pad
    win_min_y -= pad
    win_max_y += pad
    # Keep widget edges flush within the screen without clipping any button extent
    win_min_x = max(win_min_x, min(btn_bounds[0] - 2.0, -max(14.0, space_left)))
    win_max_x = min(win_max_x, max(btn_bounds[1] + 2.0, +max(14.0, space_right)))
    win_min_y = max(win_min_y, min(btn_bounds[2] - 2.0, -max(14.0, space_top)))
    win_max_y = min(win_max_y, max(btn_bounds[3] + 2.0, +max(14.0, space_bottom)))

    cx = float(math.ceil(-win_min_x))
    cy = float(math.ceil(-win_min_y))
    w = max(80, int(math.ceil(cx + win_max_x)))
    h = max(80, int(math.ceil(cy + win_max_y)))

    return {
        "mode": mode,
        "buttons": ordered,
        "u_pos": up,
        "u_btn": ub,
        "u_y": uy,
        "dy_px": dy_px,
        "fit_scale": fit_scale,
        "red_shards": red_shards,
        "black_shards": black_shards,
        "w": w,
        "h": h,
        "cx": cx,
        "cy": cy,
        "btn_bounds": btn_bounds,
    }


class MenuBackdrop(QWidget):
    """Lapisan serpihan spike merah-hitam yang memancar dari BELAKANG karakter.
    Tembus klik (WindowTransparentForInput) dan selalu berada di bawah PetWindow."""

    def __init__(self) -> None:
        super().__init__()
        setup_overlay(self)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self._cx = 0.0
        self._cy = 0.0
        self._spin_dir = -1.0
        self._burst = 0.0
        self._t = 0.0
        self._red: list[QPolygonF] = []
        self._black: list[QPolygonF] = []

    def configure(
        self, w: int, h: int, cx: float, cy: float,
        red_shards: list[tuple[tuple[float, float], ...]],
        black_shards: list[tuple[tuple[float, float], ...]],
        spin_dir: float = -1.0,
    ) -> None:
        if self.width() != w or self.height() != h:
            self.setFixedSize(w, h)
        self._cx = cx
        self._cy = cy
        self._spin_dir = spin_dir
        self._red = [scale_polygon(pts, cx, cy, 1.0) for pts in red_shards]
        self._black = [scale_polygon(pts, cx, cy, 1.0) for pts in black_shards]

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
        cx = self._cx
        cy = self._cy
        live = clamp(self._burst)
        pulse = 1.0 + 0.016 * math.sin(self._t * 2.4) * live
        sc = (0.18 + 0.82 * max(self._burst, 0.0)) * pulse
        p.translate(cx, cy)
        p.rotate((1.0 - live) * (14.0 * self._spin_dir))
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
      * Lapisan belakang (MenuBackdrop): ledakan serpihan spike merah-hitam adaptif di belakang karakter.
      * Lapisan depan (jendela ini): tombol trapesium adaptif yang menyeimbangkan tab aktif
        dan berpindah otomatis menjauhi tepi/pojok layar agar selalu terjangkau.
      * Hover: bilah bergeser keluar, sapuan merah diagonal + bayangan putih + bintang berputar.
      * Tutup dengan redaman kritis (tanpa pantulan)."""

    triggered = pyqtSignal(str, QPoint)   # key tombol, titik global di dekat tombol
    closed = pyqtSignal()
    user_closed = pyqtSignal()            # emitted when user dismisses menu without launching a tab

    STAGGER = 0.05                        # jeda antar tombol saat membuka (detik)
    OPEN_STIFFNESS, OPEN_DAMPING = 230.0, 15.0
    CLOSE_STIFFNESS = 340.0

    # Master tab definitions: (key, label, subtitle, preferred_side, hi_idx)
    _TAB_DEFS: tuple[tuple[str, str, str, int, int], ...] = (
        ("events",  "EVENTS",  "This Week",       -1, 2),
        ("tasks",   "TASKS",   "To-Do List",      +1, 2),
        ("timer",   "TIMER",   "Focus & Timers",  -1, 2),
        ("notes",   "NOTES",   "Notebook",        +1, 2),
        ("talk",    "TALK",    "Chat with Mona",  -1, 1),
        ("stats",   "STATS",   "Social Stats",    +1, 2),
        ("finance", "FINANCE", "Money & Budget",  +1, 2),
        ("system",  "SYSTEM",  "Options & Sleep", -1, 2),
    )

    def __init__(self, pet: PetWindow) -> None:
        super().__init__()
        setup_overlay(self)
        self.setMouseTracking(True)
        self._pet = pet
        self._backdrop = MenuBackdrop()
        self._enabled_keys: tuple[str, ...] = tuple(RADIAL_TAB_KEYS)
        self._buttons: list[MenuButton] = []
        self._u = 1.0                                    # button & font scale
        self._u_pos = 1.0                                # horizontal clearance from Morgana & HUD
        self._u_y = 1.0                                  # vertical separation scale
        self._ui_scale = 1.0
        self._cx = 420.0
        self._cy = 270.0
        self._layout_mode = "both"
        self._dy_px = 0.0
        self._fit_scale = 1.0
        self._btn_bounds = (-300.0, 300.0, -180.0, 180.0)
        self._anchor_center: QPoint | None = None
        self._burst = Spring(0.0, 260.0, 17.0)           # animasi ledakan spike di belakang
        self._geom: dict[str, tuple] = {}                # cache poligon, huruf, dan path subjudul
        self._geom_meta: dict[str, tuple[int, float]] = {}
        self._t = 0.0
        self._open = False
        self._mask_on = False
        self._pressed = -1
        self._hovered_idx = -1
        self.set_enabled_tabs(list(RADIAL_TAB_KEYS))

        self._ticker = FrameTicker(self._frame, self)
        self._auto_close = QTimer(self)                  # menutup sendiri kalau dibiarkan
        self._auto_close.setSingleShot(True)
        self._auto_close.setInterval(15000)
        self._auto_close.timeout.connect(lambda: self.close_menu(silent=False, emit_user_close=False))

    def _active_tab_defs(self) -> list[tuple[str, str, str, int, int]]:
        wanted = set(self._enabled_keys)
        defs = [d for d in self._TAB_DEFS if d[0] in wanted]
        return defs if defs else list(self._TAB_DEFS)

    def set_enabled_tabs(self, enabled_keys: list[str] | tuple[str, ...]) -> None:
        """Store active radial tabs and rebuild default balanced buttons."""
        wanted = set(enabled_keys or RADIAL_TAB_KEYS)
        ordered_keys = tuple(d[0] for d in self._TAB_DEFS if d[0] in wanted)
        self._enabled_keys = ordered_keys if ordered_keys else tuple(d[0] for d in self._TAB_DEFS)
        # Build balanced default buttons immediately so state is valid even before first open
        default_plan = compute_radial_layout(
            self._active_tab_defs(), 800.0, 800.0, 500.0, 500.0,
            1.0, 1.0, 1.0, 1.0,
        )
        self._buttons = [
            MenuButton(
                b["key"], b["label"], b["sub"], b["side"],
                b["ox"], b["oy"], b["rot"], 176, 25, 48, b["hi"], float(i),
            )
            for i, b in enumerate(default_plan["buttons"])
        ]
        self._hovered_idx = -1
        self._pressed = -1
        if self.isVisible():
            self.hide()
            self._open = False

    # -- buka / tutup & z-order ----------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._open and self.isVisible()

    def set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self._backdrop.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)

    def set_ui_scale(self, scale: float) -> None:
        self._ui_scale = max(0.75, min(1.5, float(scale)))
        if self.isVisible():
            self.hide()

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
            f = p5_font(max(17, int(h_here * 0.84)), italic=False)
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
        sub_f = p5_font(max(11, int(14 * u)), italic=True)
        tw = QFontMetrics(sub_f).horizontalAdvance(b.sub)
        tx = (-L * 0.54 if b.side < 0 else L * 0.54) - tw * 0.5
        ty = (h_in + h_out) * 0.24 + 5.0 * u
        sub_path = QPainterPath()
        sub_path.addText(QPointF(tx, ty), sub_f, b.sub)
        return L, h_in, h_out, poly, shape, glyphs, sub_path

    def _screen_spaces(self, c: QPoint) -> tuple[float, float, float, float]:
        screen = QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()
        if screen is None:
            return 800.0, 800.0, 500.0, 500.0
        area = screen.availableGeometry()
        return (
            float(c.x() - area.left()),
            float(area.right() - c.x()),
            float(c.y() - area.top()),
            float(area.bottom() - c.y()),
        )

    def _layout(self, preserve_springs: bool = False, hysteresis: float = 0.0) -> None:
        c = self._pet.sprite_center()
        space_l, space_r, space_t, space_b = self._screen_spaces(c)
        u_sprite = max(0.72, self._pet.sprite_size / 180.0)
        u_btn = max(0.70, u_sprite * self._ui_scale)
        u_x = max(u_sprite, u_btn, self._ui_scale)
        u_y = max(u_sprite, u_btn)

        plan = compute_radial_layout(
            self._active_tab_defs(),
            space_l, space_r, space_t, space_b,
            u_sprite, u_x, u_btn, u_y,
            preferred_mode=self._layout_mode if preserve_springs else None,
            hysteresis=hysteresis if preserve_springs else 0.0,
        )

        prev_by_key = {b.key: b for b in self._buttons} if preserve_springs else {}
        rebuilt: list[MenuButton] = []
        for i, spec in enumerate(plan["buttons"]):
            key = spec["key"]
            old = prev_by_key.get(key)
            if old is not None:
                old.side = spec["side"]
                old.ox = spec["ox"]
                old.oy = spec["oy"]
                old.rot = spec["rot"]
                old.phase = float(i)
                rebuilt.append(old)
            else:
                rebuilt.append(MenuButton(
                    key, spec["label"], spec["sub"], spec["side"],
                    spec["ox"], spec["oy"], spec["rot"], 176, 25, 48, spec["hi"], float(i),
                ))

        self._buttons = rebuilt
        self._hovered_idx = -1
        self._pressed = -1
        self._u_pos = plan["u_pos"]
        self._u = plan["u_btn"]
        self._u_y = plan["u_y"]
        self._cx = plan["cx"]
        self._cy = plan["cy"]
        self._layout_mode = plan["mode"]
        self._dy_px = plan["dy_px"]
        self._fit_scale = plan["fit_scale"]
        self._btn_bounds = plan["btn_bounds"]
        self._anchor_center = QPoint(c)

        w, h = plan["w"], plan["h"]
        if self.width() != w or self.height() != h:
            self.setFixedSize(w, h)
        spin_dir = +1.0 if plan["mode"] == "left" else -1.0
        self._backdrop.configure(
            w, h, self._cx, self._cy, plan["red_shards"], plan["black_shards"], spin_dir=spin_dir,
        )

        # Rebuild button glyph cache only when (side, scale) changes; prune inactive keys
        u_key = round(self._u, 4)
        active_keys = {b.key for b in self._buttons}
        for stale in list(self._geom.keys()):
            if stale not in active_keys:
                self._geom.pop(stale, None)
                self._geom_meta.pop(stale, None)
        for b in self._buttons:
            meta = (b.side, u_key)
            if self._geom_meta.get(b.key) != meta:
                self._geom[b.key] = self._build_button_cache(b, self._u)
                self._geom_meta[b.key] = meta

    def open_menu(self) -> None:
        self._layout(preserve_springs=self.isVisible())
        self._hovered_idx = -1
        if self.isVisible():
            self._t = 10.0                      # sedang menutup lalu dibuka lagi: tanpa jeda stagger
        else:
            play_ui("open_radial")
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
        # Urutan tumpukan: spike belakang -> karakter Morgana -> tombol di depan
        self._backdrop.show()
        self._pet.raise_()
        self.show()
        self.raise_()
        self._auto_close.start()
        self._ticker.start()

    def close_menu(self, silent: bool = False, emit_user_close: bool = False) -> None:
        if not self.isVisible() or not self._open:
            return
        self._open = False
        self._hovered_idx = -1
        self._mask_on = False
        self.clearMask()
        self._auto_close.stop()
        if not silent:
            play_ui("close")
        self._burst.configure(self.CLOSE_STIFFNESS)
        self._burst.target = 0.0
        for b in self._buttons:
            b.pop.configure(self.CLOSE_STIFFNESS)       # kritis: menutup tanpa memantul
            b.hover.target = 0.0
        self._ticker.start()
        if emit_user_close:
            self.user_closed.emit()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._backdrop.hide()

    def _needs_live_relayout(self, c: QPoint) -> bool:
        """O(1) edge/corner check during live drag so open radial menu never clips off-screen."""
        space_l, space_r, space_t, space_b = self._screen_spaces(c)
        min_x, max_x, min_y, max_y = self._btn_bounds
        if (min_x < -(space_l - 4.0) or max_x > (space_r - 4.0)
                or min_y < -(space_t - 4.0) or max_y > (space_b - 4.0)):
            return True
        adapted = (
            (self._layout_mode != "both" and len(self._buttons) >= 2)
            or abs(self._dy_px) > 1.0
            or self._fit_scale < 0.995
        )
        if adapted and self._anchor_center is not None:
            if (c - self._anchor_center).manhattanLength() >= 28:
                return True
        return False

    def follow(self, force: bool = False) -> None:
        if self.isVisible() or force:
            c = self._pet.sprite_center()
            if self._open and not force and self._needs_live_relayout(c):
                self._layout(preserve_springs=True, hysteresis=24.0)
                if self._mask_on:
                    self.setMask(self._build_mask())
                self.update()
                self._backdrop.update()
            pos = QPoint(c.x() - int(round(self._cx)), c.y() - int(round(self._cy)))
            self.move(pos)
            self._backdrop.move(pos)

    def wedges_global_bottom(self) -> int:
        """Lowest global Y reached by any radial wedge (including hover/shadow/subtitle)."""
        if not self._geom:
            return self._pet.visual_bottom()
        u = self._u
        max_y = self._cy
        for b in self._buttons:
            if b.key not in self._geom:
                continue
            poly = self._geom[b.key][3]
            for hv in (0.0, 1.0):
                br = self._xf(b, pop=1.0, hover=hv, wobble=False).map(poly).boundingRect()
                max_y = max(max_y, br.bottom() + 4.0 * u)
        return self.y() + int(math.ceil(max_y))

    def wedges_global_top(self) -> int:
        """Highest global Y reached by any radial wedge (including hover)."""
        if not self._geom:
            return self._pet.visual_top()
        u = self._u
        min_y = self._cy
        for b in self._buttons:
            if b.key not in self._geom:
                continue
            poly = self._geom[b.key][3]
            for hv in (0.0, 1.0):
                br = self._xf(b, pop=1.0, hover=hv, wobble=False).map(poly).boundingRect()
                min_y = min(min_y, br.top() - 6.0 * u)
        return self.y() + int(math.floor(min_y))

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
        cx = self._cx
        cy = self._cy
        t = QTransform()
        t.translate(cx + b.ox * self._u_pos * pop + fx, cy + b.oy * self._u_y * pop + fy)
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
        if idx != self._hovered_idx:
            self._hovered_idx = idx
            if idx >= 0:
                play_ui("hover")
        for i, b in enumerate(self._buttons):
            b.hover.target = 1.0 if i == idx else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
        if self._auto_close.isActive():
            self._auto_close.start()

    def leaveEvent(self, e) -> None:
        self._hovered_idx = -1
        for b in self._buttons:
            b.hover.target = 0.0

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._hit(e.position())
        if idx < 0:
            self.close_menu(silent=False, emit_user_close=True)      # klik area kosong
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
class ManualTimerItem:
    id: str
    label: str
    total_s: int
    deadline: float

    def remaining(self, now: float | None = None) -> int:
        ref = time.monotonic() if now is None else now
        return max(0, math.ceil(self.deadline - ref))


class ManualTimerManager(QObject):
    """Multiple concurrent user-set countdown timers backed by time.monotonic()."""

    changed = pyqtSignal()
    fired = pyqtSignal(str, int)        # label, total_s

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[ManualTimerItem] = []
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

    def add(self, seconds: int, label: str = "") -> ManualTimerItem:
        total = max(1, int(seconds))
        title = (label or "").strip() or "Manual Timer"
        item = ManualTimerItem(
            id=uuid.uuid4().hex[:10],
            label=title,
            total_s=total,
            deadline=time.monotonic() + total,
        )
        self._items.append(item)
        self._items.sort(key=lambda x: x.deadline)
        if not self._timer.isActive():
            self._timer.start()
        self.changed.emit()
        return item

    def cancel(self, timer_id: str) -> bool:
        before = len(self._items)
        self._items = [i for i in self._items if i.id != timer_id]
        if len(self._items) != before:
            if not self._items:
                self._timer.stop()
            self.changed.emit()
            return True
        return False

    def active(self) -> list[ManualTimerItem]:
        return list(self._items)

    def nearest(self) -> ManualTimerItem | None:
        return self._items[0] if self._items else None

    def snapshot(self) -> list[dict]:
        now = time.monotonic()
        return [
            {"id": i.id, "label": i.label, "total_s": i.total_s, "remaining_s": i.remaining(now)}
            for i in self._items
        ]

    def _on_tick(self) -> None:
        now = time.monotonic()
        done: list[ManualTimerItem] = []
        keep: list[ManualTimerItem] = []
        for item in self._items:
            if item.deadline <= now + 0.05:
                done.append(item)
            else:
                keep.append(item)
        self._items = keep
        if not self._items:
            self._timer.stop()
        self.changed.emit()
        for item in done:
            self.fired.emit(item.label, item.total_s)


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


class AIFetcher(QObject):
    """Non-blocking background worker for Gemini / OpenAI Morgana <-> Joker AI dialogue."""

    chat_done = pyqtSignal(object, object)        # (AIReply, fallback_CommandResult)
    talk_scene_done = pyqtSignal(object, dict)    # (AITalkScene, ctx)
    talk_reply_done = pyqtSignal(object, str, str)  # (AIReply, chosen_label, action)
    ambient_done = pyqtSignal(object, object)     # (AIReply, fallback_tuple)
    test_done = pyqtSignal(bool, str)             # (ok, status_message)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._chat_busy = False
        self._talk_busy = False
        self._ambient_busy = False
        self._test_busy = False

    def ask_chat(self, prompt: str, ctx: dict, cfg: AIConfig,
                 history: list[dict[str, str]], fallback: CommandResult) -> bool:
        if self._chat_busy:
            return False
        self._chat_busy = True

        def _run() -> None:
            try:
                res = ask_morgana_chat(prompt, ctx, cfg, history=history)
            except Exception as exc:
                res = AIReply(ok=False, text="", error=str(exc))
            self._chat_busy = False
            self.chat_done.emit(res, fallback)

        threading.Thread(target=_run, daemon=True, name="morgana-ai-chat").start()
        return True

    def ask_talk_scene(self, ctx: dict, cfg: AIConfig,
                       history: list[dict[str, str]], trigger_hint: str = "talk") -> bool:
        if self._talk_busy:
            return False
        self._talk_busy = True

        def _run() -> None:
            try:
                scene = generate_joker_talk_scene(ctx, cfg, history=history, trigger_hint=trigger_hint)
            except Exception as exc:
                scene = AITalkScene(ok=False, line="", error=str(exc))
            self._talk_busy = False
            self.talk_scene_done.emit(scene, ctx)

        threading.Thread(target=_run, daemon=True, name="morgana-ai-talk").start()
        return True

    def ask_talk_reply(self, opening_line: str, chosen_label: str, action: str,
                       ctx: dict, cfg: AIConfig, history: list[dict[str, str]]) -> bool:
        if self._talk_busy:
            return False
        self._talk_busy = True

        def _run() -> None:
            try:
                res = continue_joker_talk_choice(opening_line, chosen_label, ctx, cfg, history=history)
            except Exception as exc:
                res = AIReply(ok=False, text="", error=str(exc))
            self._talk_busy = False
            self.talk_reply_done.emit(res, chosen_label, action)

        threading.Thread(target=_run, daemon=True, name="morgana-ai-talk-reply").start()
        return True

    def ask_ambient(self, event_desc: str, ctx: dict, cfg: AIConfig,
                    fallback_tuple: tuple[str, str, str, str]) -> bool:
        if self._ambient_busy:
            return False
        self._ambient_busy = True

        def _run() -> None:
            try:
                res = generate_ambient_reaction(event_desc, ctx, cfg)
            except Exception as exc:
                res = AIReply(ok=False, text="", error=str(exc))
            self._ambient_busy = False
            self.ambient_done.emit(res, fallback_tuple)

        threading.Thread(target=_run, daemon=True, name="morgana-ai-ambient").start()
        return True

    def test_key(self, cfg: AIConfig) -> bool:
        if self._test_busy:
            return False
        self._test_busy = True

        def _run() -> None:
            try:
                ok, msg = test_ai_connection(cfg)
            except Exception as exc:
                ok, msg = False, str(exc)
            self._test_busy = False
            self.test_done.emit(ok, msg)

        threading.Thread(target=_run, daemon=True, name="morgana-ai-test").start()
        return True


class SheetsSyncWorker(QObject):
    """Non-blocking single-flight background worker for write-only Google Sheets sync."""

    push_done = pyqtSignal(object)                # SheetsPushResult
    test_done = pyqtSignal(bool, str)             # (ok, status_message)

    def __init__(self, base_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._base_dir = Path(base_dir)
        self._sync_busy = False
        self._test_busy = False

    @property
    def busy(self) -> bool:
        return self._sync_busy

    def push(self, cfg: SheetsSyncConfig, rows: list[list[str]]) -> bool:
        if self._sync_busy:
            return False
        self._sync_busy = True

        def _run() -> None:
            try:
                res = push_tasks_to_sheets(cfg, self._base_dir, rows)
            except Exception as exc:
                res = SheetsPushResult(ok=False, error=f"Google Sheets sync failed ({type(exc).__name__}).")
            self._sync_busy = False
            self.push_done.emit(res)

        threading.Thread(target=_run, daemon=True, name="morgana-sheets-push").start()
        return True

    def test_connection(self, cfg: SheetsSyncConfig) -> bool:
        if self._test_busy:
            return False
        self._test_busy = True

        def _run() -> None:
            try:
                ok, msg = test_sheets_connection(cfg, self._base_dir)
            except Exception as exc:
                ok, msg = False, f"Sheets connection test failed ({type(exc).__name__})."
            self._test_busy = False
            self.test_done.emit(ok, msg)

        threading.Thread(target=_run, daemon=True, name="morgana-sheets-test").start()
        return True


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
        load_fonts()
        self.args = args
        self.sprites = SpriteBank()
        self.pet = PetWindow(self.sprites, args.size)
        self.bubble = BubbleWindow(self.pet)
        self.particles = ParticleOverlay(self.pet)
        self.choices = ChoiceOverlay(self.bubble)
        self.menu = RadialMenu(self.pet)
        self.tasks = TaskStore(APP_DIR / "tasks.json")
        self.notes = NoteStore(APP_DIR / "notes.json")
        self.social_stats = SocialStatsStore(APP_DIR / "social_stats.json")
        self.local_events = LocalEventStore(APP_DIR / "local_events.json")
        self.habits = HabitStore(APP_DIR / "habits.json")
        self.finance = FinanceStore(APP_DIR / "finance.json")
        self.screen_time = ScreenTimeStore(APP_DIR / "screen_time.json")
        self.awareness = AppAwarenessMonitor()
        self.dialogue_memory = DialogueMemoryStore(APP_DIR / "dialogue_memory.json")
        self.dialogue_engine = DialogueEngine(self.dialogue_memory)
        self.settings = SettingsStore(APP_DIR / "settings.json")
        if args.curfew is not None:               # CLI flags override for this session only
            self.settings.set(save=False, bedtime=args.curfew.strftime("%H:%M"))
        if args.wake is not None:
            self.settings.set(save=False, wake=args.wake.strftime("%H:%M"))
        focus_m = args.focus if args.focus is not None else self.settings["focus_min"]
        rest_m = args.rest if args.rest is not None else self.settings["rest_min"]
        self.pomodoro = PomodoroTimer(focus_m, rest_m, self)
        self.manual_timers = ManualTimerManager(self)
        self.monitor = SystemMonitor(self)
        lines = load_lines(LINES_FILE)            # kalimat Morgana dari morgana_list.md
        self._greetings = lines["greeting"]
        self._bag_bedtime = LineBag(lines["bedtime"])
        self._bag_agenda = LineBag(lines["agenda"])
        self._bag_general = LineBag(lines["general"])
        self.dialogue = ContextualDialogue()
        self.calendar = CalendarService(APP_DIR)
        self.fetcher = CalendarFetcher(self.calendar, self)
        self.ai_fetcher = AIFetcher(self)
        self.ai_conversation = ConversationBuffer(max_turns=12)
        self._ai_talk_scene: AITalkScene | None = None
        self._ai_ambient_last_t = 0.0
        self.sheets_sync = SheetsSyncWorker(APP_DIR, self)
        self._sheets_dirty = False
        self._sheets_fail_streak = 0
        self._sheets_manual_sync = False
        self._sheets_last_pushed_rows: tuple[str, str, list[list[str]]] | None = None
        self._sheets_inflight_rows: tuple[str, str, list[list[str]]] | None = None
        self.hotkeys = GlobalHotkeyManager(self)
        self.tasks_dialog: TasksDialog | None = None
        self.notes_dialog: NotesDialog | None = None
        self.events_dialog: EventsDialog | None = None
        self.stats_dialog: SocialStatsDialog | None = None
        self.finance_dialog: FinanceDialog | None = None
        self.timer_dialog: TimerDialog | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.briefing_dialog: BriefingDialog | None = None
        self.quick_cmd_dialog: QuickCommandDialog | None = None
        self.wrapup_dialog: WrapUpDialog | None = None
        self._cal_events: list[CalendarEvent] = []
        self._last_cal_result: CalendarResult | None = None
        self._remind_last: dict[str, float] = {}    # kunci acara -> waktu (monotonic) pengingat terakhir
        self._remind_started: set[str] = set()      # acara yang sudah diberi pesan "dimulai sekarang"
        self._deferred_reminders: list[str] = []    # reminders queued while Do Not Disturb is active
        self._agenda_wanted = False            # pengguna menunggu hasil EVENTS
        self._rebuilding_ui = False            # suppress open/close chatter during UI scale/tab sync
        self._last_bump_t = 0.0
        self._last_awareness_tick = time.monotonic()
        self._pending_task_reward_note = ""
        self._pending_choices: list[str] = []
        self._menu_hover_action: dict[int, object] = {}

        # state karakter & Do Not Disturb
        self.forced_state: str | None = None   # override manual dari menu klik kanan
        self.forced_expression: str | None = None
        self.sleep_mode = False
        self.manual_dnd = bool(self.settings["manual_dnd"])
        self._auto_dnd_reason = ""             # "", "meeting", or "fullscreen"
        self._auto_dnd_since: float | None = None
        self.curfew_enabled = bool(self.settings["curfew_enabled"])
        self.curfew_active = False
        self._last_curfew_msg: datetime | None = None
        self.on_top = True
        self.ui_scale = self.settings["ui_scale"] / 100.0
        self.audio = init_audio(
            self,
            enabled=bool(self.settings["sound_enabled"]),
            volume_pct=int(self.settings["sound_volume"]),
            voice_volume_pct=int(self.settings["voice_volume"]),
            sfx_volume_pct=int(self.settings["sfx_volume"]),
        )
        self.pet.set_ui_scale(self.ui_scale)
        self.bubble.set_ui_scale(self.ui_scale)
        self.particles.set_ui_scale(self.ui_scale)
        self.particles.set_enabled(bool(self.settings["particles_enabled"]))
        self.choices.set_ui_scale(self.ui_scale)
        self.menu.set_ui_scale(self.ui_scale)
        self.menu.set_enabled_tabs(self.settings["enabled_tabs"])

        self._build_context_menu()
        self._build_system_menu()
        self._build_tray()

        # In-app Quick Command fallback shortcut (works whenever Morgana sprite has focus)
        self._inapp_cmd_sc = QShortcut(QKeySequence("Ctrl+K"), self.pet)
        self._inapp_cmd_sc.activated.connect(self._open_quick_command)

        self.pet.clicked.connect(self._on_pet_clicked)
        self.pet.double_clicked.connect(self._on_pet_double_clicked)
        self.pet.moved.connect(self._on_pet_moved)
        self.pet.context_requested.connect(self.context_menu.popup)
        self.pet.wall_bounced.connect(self._on_wall_bounced)
        self.bubble.dismissed.connect(self._on_bubble_dismissed)
        self.bubble.typing_finished.connect(self._on_bubble_typing_finished)
        self.bubble.page_started.connect(lambda: self.pet.set_speaking(True))
        self.bubble.set_menu(self.menu)
        self.bubble.set_avoid_rects(self._visible_dialog_rects)
        self.choices.choice_selected.connect(self._on_choice_selected)
        self.choices.cancelled.connect(self._on_choice_cancelled)
        self.menu.triggered.connect(self._on_menu_action)
        self.menu.user_closed.connect(self._on_menu_user_closed)
        self.menu.closed.connect(self.bubble.follow)
        self.menu.closed.connect(self.choices.follow)
        self.pomodoro.tick.connect(self._on_pomo_tick)
        self.pomodoro.phase_started.connect(self._on_pomo_phase)
        self.pomodoro.finished.connect(self._on_pomo_finished)
        self.pomodoro.stopped.connect(self._sync_timer_ui)
        self.manual_timers.changed.connect(self._sync_timer_ui)
        self.manual_timers.fired.connect(self._on_manual_timer_fired)
        self.fetcher.done.connect(self._on_calendar_result)
        self.ai_fetcher.chat_done.connect(self._on_ai_chat_reply)
        self.ai_fetcher.talk_scene_done.connect(self._on_ai_talk_scene)
        self.ai_fetcher.talk_reply_done.connect(self._on_ai_talk_reply)
        self.ai_fetcher.ambient_done.connect(self._on_ai_ambient_reply)
        self.ai_fetcher.test_done.connect(self._on_ai_test_done)
        self.sheets_sync.push_done.connect(self._on_sheets_push_done)
        self.sheets_sync.test_done.connect(self._on_sheets_test_done)
        self.hotkeys.activated.connect(lambda: self._open_quick_command(toggle=True))

        self._curfew_timer = QTimer(self)
        self._curfew_timer.setInterval(5_000)      # 5s precision check for 30s bedtime cadence
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
        self._awareness_timer = QTimer(self)       # 5s local active-app & screen-time sampler
        self._awareness_timer.setInterval(5_000)
        self._awareness_timer.timeout.connect(self._poll_awareness_and_screen_time)
        self._sleep_zzz_timer = QTimer(self)       # gentle Zzz particles during sleep/curfew
        self._sleep_zzz_timer.setInterval(6_500)
        self._sleep_zzz_timer.timeout.connect(self._sleep_zzz_tick)
        self._sheets_debounce_timer = QTimer(self) # debounce rapid task edits (write-only output)
        self._sheets_debounce_timer.setSingleShot(True)
        self._sheets_debounce_timer.timeout.connect(self._trigger_sheets_sync)
        self._sheets_retry_timer = QTimer(self)    # single-shot startup push / offline retry backoff
        self._sheets_retry_timer.setSingleShot(True)
        self._sheets_retry_timer.timeout.connect(self._trigger_sheets_sync)

    # -- siklus hidup -------------------------------------------------------
    def start(self) -> None:
        self._move_to_corner(QGuiApplication.primaryScreen())
        self.pet.show()
        self.particles.follow()
        self.hotkeys.register(str(self.settings["quick_hotkey"]))
        self._sync_timer_ui()
        self._update_tray_tooltip()
        self._check_curfew()
        self._curfew_timer.start()
        self._reminder_timer.start()
        self._calendar_timer.start()
        self._awareness_timer.start()
        self._sleep_zzz_timer.start()
        self._configure_sheets_sync(trigger_initial=True)
        self._schedule_chatter()
        QTimer.singleShot(900, self._startup_report)
        QTimer.singleShot(3000, self._refresh_calendar)

    def quit(self) -> None:
        self._sheets_debounce_timer.stop()
        self._sheets_retry_timer.stop()
        self.hotkeys.unregister()
        self.screen_time.save()
        self.audio.stop_all()
        self.particles.hide()
        self.choices.hide()
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

    def _ensure_state_healthy(self) -> None:
        """Self-healing guard: if no bubble or choice prompt is visible, Morgana MUST return to base state."""
        if not self.bubble.isVisible() and not self.choices.is_open:
            if self._pending_choices:
                self._pending_choices = []
            if self.dialogue_engine.is_active:
                self.dialogue_engine.cancel()
            want_state = self.base_state()
            want_expr = self.forced_expression or STATE_EXPRESSION[want_state]
            if (self.pet._state != want_state or self.pet._expr != want_expr
                    or self.pet._speaking or self.pet._talk.target > 0.01):
                self._restore_state()
        if self.pet.isVisible() and not self.pet._ticker.active:
            self.pet._ticker.start()

    def _on_bubble_dismissed(self) -> None:
        self._pending_choices = []
        if self.choices.is_open:
            self.choices.close_choices(silent=True)
        self.dialogue_engine.cancel()
        self._restore_state()

    def _on_bubble_typing_finished(self) -> None:
        self.pet.set_speaking(False)
        if self._pending_choices and self.bubble.isVisible() and not self.bubble._closing:
            if self.bubble._page_idx + 1 >= len(self.bubble._pages):
                opts = list(self._pending_choices)
                self._pending_choices = []
                self.choices.show_choices(opts)

    def say(self, text: str, state: str = "talking", ms: int | None = None,
            emotion: str | None = None, voice: str | bool | None = None,
            context: str = "", *, hold: bool = False, keep_choices: bool = False,
            particle: str = "") -> None:
        """Ucapkan `text`. Ekspresi & voiceline dipilih dari isi kalimat (atau parameter eksplisit);
        mulut bergerak selama balon mengetik."""
        if not keep_choices:
            self._pending_choices = []
            if self.choices.is_open:
                self.choices.close_choices(silent=True)
            self.dialogue_engine.cancel()
        if emotion is None:
            emotion = emotion_for_text(text, default=STATE_EXPRESSION.get(state, "neutral"))
        if self.forced_state:
            self.pet.set_state(self.forced_state, self.forced_expression)
        else:
            self.pet.set_state(state, self.forced_expression or emotion)
        self.pet.set_talking(True)
        self.pet.set_speaking(True)
        if voice is not False:
            vkey = voice if isinstance(voice, str) else voice_for_dialogue(text, emotion, context)
            self.audio.play_voice(vkey)
        if particle:
            self.particles.emit_by_name(particle)
        self.bubble.show_message(text, ms or max(4000, 2500 + 55 * len(text)), hold=hold)

    def _say_tuple(self, triple: tuple[str, str, str], state: str = "talking",
                   ms: int | None = None, particle: str = "") -> None:
        text, emotion, voice = triple
        self.say(text, state=state, ms=ms, emotion=emotion, voice=voice, particle=particle)

    def _ctx(self) -> dict:
        """Gather live workspace state for context-aware Morgana dialogue & Choice Prompts."""
        now = datetime.now()
        aware_now = now.astimezone()
        all_tasks = self.tasks.all()
        pending = [t for t in all_tasks if not t.get("done")]
        high_pending = [t for t in pending if t.get("priority") == "high"]
        overdue_tasks = [t for t in pending if parse_dt(t.get("deadline")) is not None and parse_dt(t.get("deadline")) <= now]
        top_task = (overdue_tasks[0]["title"] if overdue_tasks else
                    (high_pending[0]["title"] if high_pending else (pending[0]["title"] if pending else "")))

        notes_all = [n for n in self.notes.all() if not n.get("archived")]
        due_notes = self.notes.with_deadline()
        h_done, h_total = self.habits.today_summary()
        totals = self.social_stats.totals()
        lowest_k = min(SOCIAL_STAT_KEYS, key=lambda k: totals.get(k, 0))
        top_k = max(SOCIAL_STAT_KEYS, key=lambda k: totals.get(k, 0))
        scr = self.screen_time.today_summary(now)
        top_cats = scr.get("top_categories") or []
        top_cat_summary = f"{top_cats[0][1]} ({top_cats[0][3]})" if top_cats else ""
        cur_app = self.awareness.current
        prev_app = self.awareness.previous
        now_mono = time.monotonic()

        manual = self.manual_timers.snapshot()
        nearest = manual[0] if manual else None

        # Calendar + Local Events + Note & Task deadlines snapshot
        base_cal = self._cal_events or (self._last_cal_result.events if self._last_cal_result else [])
        cal_list = (list(base_cal)
                    + local_calendar_events(self.local_events.all(), now=aware_now)
                    + note_deadline_events(notes_all, now=aware_now)
                    + task_deadline_events(all_tasks, now=aware_now))
        timed = sorted((e for e in cal_list if not e.all_day), key=lambda e: e.start)
        ongoing = next((e for e in timed if e.source not in ("note", "task")
                        and e.start <= aware_now and (e.end is None or e.end > aware_now)), None)
        next_ev = next((e for e in timed if e.start > aware_now), None)
        next_title, next_delta = "", ""
        if next_ev is not None and (next_ev.start - aware_now) <= timedelta(hours=8):
            next_title = next_ev.title
            next_delta = human_delta(next_ev.start - aware_now)

        top_tasks = [str(t.get("title") or "")[:44] for t in pending[:3] if t.get("title")]
        stat_parts = []
        try:
            totals = self.social_stats.totals()
            for sk in SOCIAL_STAT_KEYS:
                lv = stat_level(int(totals.get(sk, 0)))
                stat_parts.append(f"{SOCIAL_STATS[sk]['label']} Lv{lv}")
        except Exception:
            pass

        return {
            "curfew_active": self.curfew_active,
            "sleep_mode": self.sleep_mode,
            "hour": now.hour,
            "enabled_tabs": list(self.settings["enabled_tabs"]),
            "total_tasks": len(all_tasks),
            "pending_tasks": len(pending),
            "overdue_count": len(overdue_tasks),
            "high_tasks": len(high_pending),
            "top_task": top_task,
            "top_tasks": top_tasks,
            "notes_count": len(notes_all),
            "due_notes": len(due_notes),
            "habits_done": h_done,
            "habits_total": h_total,
            "today_blocks": len(self.social_stats.today_entries()),
            "streak_days": self.social_stats.streak_days(),
            "lowest_stat_label": SOCIAL_STATS[lowest_k]["label"],
            "top_stat_label": SOCIAL_STATS[top_k]["label"],
            "social_stats_summary": ", ".join(stat_parts),
            "active_app_cat": cur_app.category if cur_app else "",
            "active_app_category": cur_app.category if cur_app else "",
            "active_app_name": cur_app.display_name if cur_app else "",
            "active_app_topic": cur_app.topic if cur_app else "",
            "active_window_title": cur_app.title if cur_app else "",
            "active_app_phrase": cur_app.phrase if cur_app else "",
            "active_app_dwell": self.awareness.app_dwell_text(now_mono),
            "prev_app_name": prev_app.display_name if prev_app else "",
            "prev_app_cat": prev_app.category if prev_app else "",
            "switched_app_recently": self.awareness.switched_recently(now_mono),
            "screen_active_s": scr["active_s"],
            "screen_time_text": scr["active_text"],
            "continuous_time_text": scr["continuous_text"],
            "continuous_s": scr["continuous_s"],
            "top_category_summary": top_cat_summary,
            "focus_running": self.pomodoro.running,
            "focus_phase": self.pomodoro.phase,
            "focus_rem": fmt_mmss(self.pomodoro.remaining()) if self.pomodoro.running else "",
            "manual_count": len(manual),
            "manual_nearest_label": nearest["label"] if nearest else "",
            "manual_nearest_rem": fmt_hms(nearest["remaining_s"]) if nearest else "",
            "ongoing_event_title": ongoing.title if ongoing else "",
            "next_event_title": next_title,
            "next_event_delta": next_delta,
            "week_events_count": len(cal_list),
            "favorite_drink": str(self.dialogue_memory.get_pref("drink") or ""),
            "dnd_active": self.is_dnd_active(),
            "dnd_reason": self.dnd_reason(),
        }

    def _ai_config(self) -> AIConfig:
        return AIConfig(
            provider=str(self.settings["ai_provider"] or "gemini"),
            api_key=str(self.settings["ai_api_key"] or ""),
            model=str(self.settings["ai_model"] or "gemini-3.5-flash-lite"),
            endpoint=str(self.settings["ai_endpoint"] or ""),
            call_user=str(self.settings["ai_call_user"] or "Joker"),
        )

    def _ai_ready(self) -> bool:
        return bool(self.settings["ai_enabled"]) and self._ai_config().is_ready()

    def _ai_dialogue_ready(self) -> bool:
        return self._ai_ready() and bool(self.settings["ai_dialogue_enabled"])

    # -- Do Not Disturb (Manual + Auto-DND for Meetings & Fullscreen) --------
    def is_dnd_active(self) -> bool:
        return bool(self.manual_dnd or self._auto_dnd_reason)

    def dnd_reason(self) -> str:
        if self.manual_dnd:
            return "manual"
        return self._auto_dnd_reason

    def set_manual_dnd(self, on: bool, announce: bool = True) -> None:
        on = bool(on)
        changed = (on != self.manual_dnd)
        self.manual_dnd = on
        self.settings.set(manual_dnd=on)
        for act in (getattr(self, "act_dnd", None), getattr(self, "act_tray_dnd", None),
                    getattr(self, "act_sys_dnd", None)):
            if act is not None:
                act.blockSignals(True)
                act.setChecked(on)
                act.blockSignals(False)
        if on:
            if self.choices.is_open:
                self.choices.close_choices(silent=True)
            if self.bubble.isVisible() and not announce:
                self.bubble.dismiss()
        self._sync_timer_ui()
        self._update_tray_tooltip()
        self._sync_quick_cmd_badge()
        if changed and not on and self._deferred_reminders:
            self._flush_deferred_reminders()
        elif changed and announce:
            if on:
                self.say("Do Not Disturb is ON. I'll stay completely quiet until you turn it back off.",
                         "neutral", ms=4200, voice=False)
            else:
                self.say("Do Not Disturb is OFF—I'm back online!", "talking", ms=3600, particle="sparkle")

    def _update_auto_dnd(self, info, now_mono: float) -> None:
        want_meet = bool(self.settings["auto_dnd_meetings"])
        want_fs = bool(self.settings["auto_dnd_fullscreen"])
        new_reason = ""
        if info is not None:
            if want_meet and info.category == "meeting":
                new_reason = "meeting"
            elif want_fs and bool(getattr(info, "is_fullscreen", False)):
                new_reason = "fullscreen"

        prev = self._auto_dnd_reason
        if new_reason == prev:
            return

        if new_reason and not prev:
            # Entering Auto-DND: quietly dismiss unsolicited overlays so calls/presentations stay clean
            self._auto_dnd_reason = new_reason
            self._auto_dnd_since = now_mono
            if self.choices.is_open:
                self.choices.close_choices(silent=True)
            if self.bubble.isVisible():
                self.bubble.dismiss()
            self._sync_timer_ui()
            self._update_tray_tooltip()
            self._sync_quick_cmd_badge()
        elif not new_reason and prev:
            # Exiting Auto-DND: gentle welcome back after long sessions (>= 5 min) or flush deferred alerts
            elapsed = (now_mono - self._auto_dnd_since) if self._auto_dnd_since else 0.0
            self._auto_dnd_reason = ""
            self._auto_dnd_since = None
            self._sync_timer_ui()
            self._update_tray_tooltip()
            self._sync_quick_cmd_badge()
            if self.manual_dnd or self.sleep_mode or self.curfew_active:
                return
            if self._deferred_reminders:
                self._flush_deferred_reminders(prev_reason=prev, elapsed_s=elapsed)
            elif elapsed >= 300.0 and self.pet.isVisible():
                text, emo, voice = dnd_recovery_line(prev, format_duration(elapsed), 0)
                self.say(text, "talking", ms=6000, emotion=emo, voice=voice, particle="sparkle")
        else:
            # Switching between meeting <-> fullscreen
            self._auto_dnd_reason = new_reason
            self._sync_timer_ui()
            self._update_tray_tooltip()
            self._sync_quick_cmd_badge()

    def _flush_deferred_reminders(self, prev_reason: str = "", elapsed_s: float = 0.0) -> None:
        if not self._deferred_reminders or self.is_dnd_active():
            return
        queued = list(self._deferred_reminders)
        self._deferred_reminders.clear()
        if prev_reason:
            head, emo, voice = dnd_recovery_line(prev_reason, format_duration(max(60.0, elapsed_s)), len(queued))
            body = head + "\n\n" + "\n\n".join(queued[:4])
            self.say(body, "talking", ms=12000, emotion=emo, voice=voice, context="reminder", particle="alert")
        else:
            body = "While Do Not Disturb was on:\n\n" + "\n\n".join(queued[:4])
            self.say(body, "talking", ms=12000, context="reminder", particle="alert")
        if self.pet.isVisible():
            self.pet.bump(1.2)

    def _update_tray_tooltip(self) -> None:
        if self.tray is None:
            return
        hk = str(self.settings["quick_hotkey"])
        hk_s = f" · {hk}" if hk and hk != "Off" else ""
        if self.manual_dnd:
            state = "Do Not Disturb (Manual)"
        elif self._auto_dnd_reason == "meeting":
            state = "Auto-DND (Meeting)"
        elif self._auto_dnd_reason == "fullscreen":
            state = "Auto-DND (Fullscreen)"
        elif not self.pet.isVisible():
            state = "Hidden in Tray"
        else:
            state = "Active"
        self.tray.setToolTip(f"Morgana Companion — {state}{hk_s}")

    def _sync_quick_cmd_badge(self) -> None:
        if self.quick_cmd_dialog is None:
            return
        if self.manual_dnd:
            self.quick_cmd_dialog.set_header_badge("DND · MANUAL", alert=True)
        elif self._auto_dnd_reason == "meeting":
            self.quick_cmd_dialog.set_header_badge("AUTO-DND · CALL", alert=True)
        elif self._auto_dnd_reason == "fullscreen":
            self.quick_cmd_dialog.set_header_badge("AUTO-DND · FULL", alert=True)
        else:
            hk = str(self.settings["quick_hotkey"])
            hk_s = hk if hk and hk != "Off" else "CTRL+K"
            if self._ai_ready():
                prov = "GEMINI" if str(self.settings["ai_provider"]) == "gemini" else "AI"
                self.quick_cmd_dialog.set_header_badge(f"{prov} · {hk_s}", alert=False)
            else:
                self.quick_cmd_dialog.set_header_badge(hk_s, alert=False)
        self.quick_cmd_dialog.set_has_chat_history(len(self.ai_conversation) > 0)

    def _schedule_chatter(self) -> None:
        base_ms = max(0.05, self.args.chatter) * 60_000
        self._chatter_timer.start(int(base_ms * random.uniform(0.75, 1.5)))

    def _chatter(self) -> None:
        self._ensure_state_healthy()
        busy = (self.sleep_mode or self.curfew_active or self.is_dnd_active()
                or not self.pet.isVisible() or self.menu.is_open
                or self.bubble.isVisible() or self.choices.is_open or self.dialogue_engine.is_active)
        if not busy:
            # 30% chance to spark a multi-turn interactive Choice Prompt if enabled & not in Focus sprint
            want_choice = (
                bool(self.settings["spontaneous_choices_enabled"])
                and not (self.pomodoro.running and self.pomodoro.phase == PomodoroTimer.FOCUS)
                and random.random() < 0.30
            )
            if want_choice:
                self._start_talk()
            else:
                self.say(self._bag_general.next())
        self._schedule_chatter()

    # -- Active App Awareness, Screen Time, Auto-DND & Micro-Particles --------
    def _sleep_zzz_tick(self) -> None:
        if (self.sleep_mode or self.curfew_active) and not self.bubble.isVisible() and not self.is_dnd_active():
            self.particles.emit_zzz()

    def _poll_awareness_and_screen_time(self) -> None:
        self._ensure_state_healthy()
        now_mono = time.monotonic()
        dt_s = max(0.0, min(30.0, now_mono - self._last_awareness_tick))
        self._last_awareness_tick = now_mono

        want_app = bool(self.settings["app_awareness_enabled"])
        want_scr = bool(self.settings["screen_time_enabled"])
        want_dnd = bool(self.settings["auto_dnd_meetings"]) or bool(self.settings["auto_dnd_fullscreen"])
        if not (want_app or want_scr or want_dnd):
            self._update_auto_dnd(None, now_mono)
            return

        info = get_active_window() if (want_app or want_dnd or want_scr) else None
        self._update_auto_dnd(info, now_mono)
        if info is not None and want_app:
            self.awareness.note_window(info, now_mono=now_mono)
        if want_scr:
            idle_s = get_idle_seconds()
            cpos = QCursor.pos()
            self.screen_time.record_tick(
                dt_s,
                category=(info.category if info else "other"),
                idle_s=idle_s,
                cursor_pos=(cpos.x(), cpos.y()),
            )
            if self.timer_dialog is not None and self.timer_dialog.isVisible():
                self.timer_dialog.update_screen_time(self.screen_time.today_summary())
            if self.briefing_dialog is not None and self.briefing_dialog.isVisible():
                self._sync_briefing_view()
            if self.wrapup_dialog is not None and self.wrapup_dialog.isVisible():
                self._sync_wrapup_view()

        # Avoid interrupting DND, hidden tray mode, active dialogs, sleep/curfew, or ongoing conversations
        can_speak = not (
            self.is_dnd_active() or not self.pet.isVisible()
            or self.sleep_mode or self.curfew_active or self.menu.is_open
            or self.bubble.isVisible() or self.choices.is_open or self.dialogue_engine.is_active
        )
        if not can_speak:
            return

        # 1) Screen-Time Break Nudge (launches interactive wellness Choice Prompt!)
        if want_scr and not (self.pomodoro.running and self.pomodoro.phase == PomodoroTimer.REST):
            threshold = int(self.settings["break_nudge_min"])
            if self.screen_time.should_nudge_break(threshold, now_mono=now_mono):
                self.screen_time.mark_nudged(now_mono)
                self.pet.bump(1.1)
                self._start_talk(tree_id="screen_time_nudge")
                return

        # 2) Active App / Window Awareness contextual comment (quiet during Focus sprint)
        if want_app and info is not None:
            if self.pomodoro.running and self.pomodoro.phase == PomodoroTimer.FOCUS:
                return
            trigger = self.awareness.update(info, now_mono=now_mono)
            if trigger is not None:
                ctx = self._ctx()
                fb = self.dialogue.app_awareness(trigger.category, trigger.display_name, ctx)
                if self._ai_dialogue_ready() and (now_mono - self._ai_ambient_last_t) >= 90.0:
                    self._ai_ambient_last_t = now_mono
                    desc = f"User is currently in {trigger.phrase} (window: {trigger.title[:60]})"
                    started = self.ai_fetcher.ask_ambient(desc, ctx, self._ai_config(), fb)
                    if started:
                        return
                text, emotion, voice, particle = fb
                self.say(text, "talking", ms=5600, emotion=emotion, voice=voice, particle=particle)

    def _on_ai_ambient_reply(self, res: AIReply, fallback_tuple: tuple[str, str, str, str]) -> None:
        if self.is_dnd_active() or self.sleep_mode or not self.pet.isVisible():
            return
        if self.bubble.isVisible() or self.choices.is_open or self.menu.is_open:
            return
        if res.ok and res.text:
            self.ai_conversation.add_model(res.text)
            self.say(res.text, "talking", ms=6000, emotion=res.emotion, voice=res.voice, particle=res.particle)
        else:
            text, emotion, voice, particle = fallback_tuple
            self.say(text, "talking", ms=5600, emotion=emotion, voice=voice, particle=particle)

    # -- Interactive Multi-Turn Persona 5 Choice Prompts ----------------------
    def _start_talk(self, tree_id: str | None = None) -> None:
        if self.menu.is_open:
            self.menu.close_menu(silent=True, emit_user_close=False)
        self._ai_talk_scene = None
        # Live-sample the foreground external window before building Talk context
        if bool(self.settings["app_awareness_enabled"]):
            fresh = get_active_window()
            if fresh is not None:
                self.awareness.note_window(fresh)
        ctx = self._ctx()
        # Use dynamic Gemini Morgana <-> Joker Choice Scene when enabled and no scripted tree is forced
        if tree_id is None and self._ai_dialogue_ready():
            self.dialogue_engine.cancel()
            call_user = str(self.settings["ai_call_user"] or "Joker").strip() or "Joker"
            self.say(
                f"Hey {call_user}... give me a sec!",
                state="talking",
                ms=12000,
                emotion="neutral",
                voice="oh_asking",
                hold=True,
                keep_choices=False,
            )
            started = self.ai_fetcher.ask_talk_scene(
                ctx=ctx,
                cfg=self._ai_config(),
                history=self.ai_conversation.snapshot(),
                trigger_hint="interactive talk",
            )
            if started:
                return
        node = (self.dialogue_engine.start_tree(tree_id, ctx) if tree_id
                else self.dialogue_engine.start_contextual(ctx))
        if node is None:
            return
        self._present_choice_node(node)

    def _on_ai_talk_scene(self, scene: AITalkScene, ctx: dict) -> None:
        if self.is_dnd_active() or not self.pet.isVisible():
            return
        if scene.ok and scene.line and scene.choices:
            self._ai_talk_scene = scene
            self.ai_conversation.add_model(scene.line)
            labels = [c.label for c in scene.choices] + ["Chat freely..."]
            self._present_choice_node({
                "text": scene.line,
                "emotion": scene.emotion,
                "voice": scene.voice,
                "particle": scene.particle,
                "has_choices": True,
                "choices": labels,
            })
            return
        # Seamless fallback to local DialogueEngine if offline / quota limited
        node = self.dialogue_engine.start_contextual(ctx)
        if node is not None:
            self._present_choice_node(node)
        else:
            self.bubble.dismiss()
            self._restore_state()

    def _present_choice_node(self, node: dict) -> None:
        has_choices = bool(node.get("has_choices"))
        self._pending_choices = list(node.get("choices") or []) if has_choices else []
        self.say(
            node["text"],
            state="talking",
            ms=7500 if not has_choices else 12000,
            emotion=node.get("emotion"),
            voice=node.get("voice"),
            hold=has_choices,
            keep_choices=True,
            particle=node.get("particle", ""),
        )

    def _on_choice_selected(self, index: int) -> None:
        # Handle dynamic Gemini Morgana <-> Joker Talk choice!
        if self._ai_talk_scene is not None:
            scene = self._ai_talk_scene
            self._ai_talk_scene = None
            self._pending_choices = []
            # Last appended option ("Chat freely...") transitions smoothly into Ask & Command
            if index >= len(scene.choices):
                self.bubble.dismiss()
                self._restore_state()
                self._open_quick_command(toggle=False)
                return
            chosen = scene.choices[max(0, index)]
            self.ai_conversation.add_user(chosen.label)
            self.say(
                "Ooh...!",
                state="talking",
                ms=10000,
                emotion="wink",
                voice="hmm",
                hold=True,
                keep_choices=False,
            )
            started = self.ai_fetcher.ask_talk_reply(
                opening_line=scene.line,
                chosen_label=chosen.label,
                action=chosen.action,
                ctx=self._ctx(),
                cfg=self._ai_config(),
                history=self.ai_conversation.snapshot(),
            )
            if not started:
                self.say("Heh, you got it! I'm right behind you.", "talking", ms=4800, emotion="smug", voice="hmm")
                if chosen.action:
                    self._execute_ai_action(chosen.action)
            return

        res = self.dialogue_engine.choose(index)
        if res is None:
            self.bubble.dismiss()
            self._restore_state()
            return
        action = res.get("action")
        node = res.get("node")

        if node is not None:
            self._present_choice_node(node)
        else:
            self.bubble.dismiss()
            self._restore_state()

        if action:
            self._execute_choice_action(action)

    def _on_ai_talk_reply(self, res: AIReply, chosen_label: str, action: str) -> None:
        if res.ok and res.text:
            self.ai_conversation.add_model(res.text)
            self._sync_quick_cmd_badge()
            self.say(
                res.text,
                state="talking",
                ms=7200,
                emotion=res.emotion,
                voice=res.voice,
                particle=res.particle,
            )
            eff_action = action or res.suggested_action
        else:
            self.say(
                "Heh, leave it to me! Let's make our next move count.",
                state="talking",
                ms=5000,
                emotion="smug",
                voice="hmm",
                particle="sparkle",
            )
            eff_action = action
        if eff_action:
            QTimer.singleShot(260, lambda a=eff_action: self._execute_ai_action(a))

    def _execute_ai_action(self, action: str) -> None:
        mapping = {
            "focus_25": "focus_25",
            "break_5": "break_5",
            "open_tasks": "tasks",
            "open_notes": "notes",
            "open_events": "events",
            "open_stats": "stats",
            "open_briefing": "briefing",
            "open_wrapup": "wrapup",
        }
        target = mapping.get((action or "").strip().lower(), "")
        if target:
            self._on_quick_command_action(target)

    def _on_choice_cancelled(self) -> None:
        self._ai_talk_scene = None
        self._pending_choices = []
        self.dialogue_engine.cancel()
        self.bubble.dismiss()
        self._restore_state()

    def _execute_choice_action(self, action: dict) -> None:
        kind = str(action.get("type") or "")
        if kind == "start_focus":
            mins = max(1, int(action.get("minutes", self.settings["focus_min"])))
            rest = int(self.settings["rest_min"])
            self._start_focus_from_dialog(mins, rest, silent_say=True)
        elif kind == "start_break":
            mins = max(1, int(action.get("minutes", 5)))
            self._start_break_timer(mins, silent_say=True)
        elif kind == "record_break":
            self.screen_time.record_break()
            if self.timer_dialog is not None:
                self.timer_dialog.update_screen_time(self.screen_time.today_summary())
            self._sync_briefing_view()
        elif kind == "snooze_break":
            self.screen_time.snooze_nudge(int(action.get("minutes", 15)))
        elif kind == "start_timer":
            mins = max(1, int(action.get("minutes", 15)))
            lbl = str(action.get("label") or "Reminder")
            self.manual_timers.add(mins * 60, lbl)
            self._sync_timer_ui()
        elif kind == "open_tasks":
            QTimer.singleShot(140, lambda: self._open_tasks(announce=False))
        elif kind == "open_notes":
            QTimer.singleShot(140, lambda: self._open_notes(announce=False))
        elif kind == "open_new_note":
            tmpl = str(action.get("template") or "blank")
            note_title = str(action.get("title") or "")
            def _launch_note(t=tmpl, nt=note_title) -> None:
                self._open_notes(announce=False)
                if self.notes_dialog is not None:
                    self.notes_dialog.create_from_template(t, title=nt)
            QTimer.singleShot(140, _launch_note)
        elif kind == "open_stats":
            QTimer.singleShot(140, lambda: self._open_stats(announce=False))
        elif kind == "open_briefing":
            QTimer.singleShot(140, lambda: self._open_briefing(announce=False))
        elif kind == "sleep_mode":
            want = bool(action.get("enabled", True))
            if want != self.sleep_mode:
                self.sleep_mode = want
                self._restore_state()

    # -- Daily Morning Briefing & Confidant Summary ---------------------------
    def _build_briefing_payload(self) -> dict:
        now = datetime.now()
        aware_now = now.astimezone()
        today = now.date()

        combined = self._combined_events_result()
        grouped = events_by_day(combined.events, now=aware_now, days=1)
        today_items = grouped[0][1] if grouped else []

        agenda_rows: list[tuple[str, str, str]] = []
        today_events: list[str] = []
        overdue_titles: list[str] = []
        due_today_count = 0

        for ev in today_items:
            is_dl = ev.source in ("note", "task")
            is_ov = is_dl and ev.start <= aware_now
            if is_ov:
                overdue_titles.append(ev.title)
                t_s = f"{ev.start:%H:%M}" if ev.start.date() == today else f"{ev.start:%d %b}"
                agenda_rows.append(("OVERDUE", t_s, ev.title))
            elif is_dl:
                due_today_count += 1
                tag = "TASK" if ev.source == "task" else "NOTE"
                agenda_rows.append((tag, f"{ev.start:%H:%M}", ev.title))
            else:
                today_events.append(ev.title)
                tag = "LOCAL" if ev.source == "local" else "EVENT"
                t_s = "ALL DAY" if ev.all_day else f"{ev.start:%H:%M}"
                agenda_rows.append((tag, t_s, ev.title))

        pending = self.tasks.pending()
        high_pending = [t for t in pending if t.get("priority") == "high"]
        h_done, h_total = self.habits.today_summary()
        totals = self.social_stats.totals()
        conf_lv, conf_max = overall_confidant_level(totals)
        streak = self.social_stats.streak_days()

        # Find lowest & highest social stat for personalized Confidant advice
        lowest_k = min(SOCIAL_STAT_KEYS, key=lambda k: totals.get(k, 0))
        top_k = max(SOCIAL_STAT_KEYS, key=lambda k: totals.get(k, 0))

        return {
            "hour": now.hour,
            "date_banner": f"{now:%a %d %b}".upper(),
            "today_events": today_events,
            "overdue_count": len(overdue_titles),
            "overdue_top": overdue_titles[0] if overdue_titles else "",
            "due_today_count": due_today_count,
            "pending_tasks": len(pending),
            "high_tasks": len(high_pending),
            "habits_done": h_done,
            "habits_total": h_total,
            "streak_days": streak,
            "lowest_stat_label": SOCIAL_STATS[lowest_k]["label"],
            "top_stat_label": SOCIAL_STATS[top_k]["label"],
            "confidant_lv": conf_lv,
            "confidant_max": conf_max,
            "stat_totals": totals,
            "today_points": self.social_stats.today_points(),
            "agenda_rows": agenda_rows,
            "screen_time": self.screen_time.today_summary(now),
        }

    def _sync_briefing_view(self) -> None:
        if self.briefing_dialog is not None and self.briefing_dialog.isVisible():
            self.briefing_dialog.set_data(self._build_briefing_payload())

    def _open_briefing(self, announce: bool = True) -> None:
        was_open = self.briefing_dialog is not None and self.briefing_dialog.isVisible()
        if self.briefing_dialog is None:
            self.briefing_dialog = BriefingDialog(on_top=self.on_top, ui_scale=self.ui_scale)
            self.briefing_dialog.open_events.connect(self._open_events)
            self.briefing_dialog.open_tasks.connect(self._open_tasks)
            self.briefing_dialog.open_stats.connect(self._open_stats)
            self.briefing_dialog.open_wrapup.connect(lambda: self._open_wrapup(announce=False))
            self.briefing_dialog.closed.connect(lambda: self._on_dialog_closed("briefing"))
        payload = self._build_briefing_payload()
        self.briefing_dialog.set_data(payload)
        self._sync_dialog_links()
        self.briefing_dialog.show_near(self.pet.geometry())
        if announce and not was_open and not self._rebuilding_ui:
            brief = build_daily_briefing(payload)
            self.say(brief["spoken"], "talking", ms=7500, emotion=brief["emotion"], voice=brief["voice"])

    # -- Evening Wrap-up (End-of-Day Recap + Task Rollover + 1-Line Journal) --
    def _build_wrapup_payload(self) -> dict:
        now = datetime.now()
        today = now.date()
        done_today = self.tasks.completed_today(now)
        pending = self.tasks.pending()
        h_done, h_tot = self.habits.today_summary(now)
        scr = self.screen_time.today_summary(now)

        # Prioritize overdue & due-today pending tasks first (preselected for rollover)
        pending_items: list[dict] = []
        for t in pending:
            dl = parse_dt(t.get("deadline"))
            tag = ""
            preselect = False
            sort_rank = 3
            if dl is not None:
                if dl <= now:
                    tag = "OVERDUE"
                    preselect = True
                    sort_rank = 0
                elif dl.date() == today:
                    tag = f"DUE {dl:%H:%M}"
                    preselect = True
                    sort_rank = 1
                elif dl.date() == today + timedelta(days=1):
                    tag = f"TOMORROW {dl:%H:%M}"
                    sort_rank = 4
                else:
                    tag = f"{dl:%d %b}"
                    sort_rank = 4
            elif t.get("priority") == "high":
                tag = "HIGH"
                sort_rank = 2
            pending_items.append({
                "id": t["id"],
                "title": t.get("title") or "Untitled Task",
                "tag": tag,
                "preselect": preselect,
                "_rank": sort_rank,
            })
        pending_items.sort(key=lambda x: x["_rank"])

        return {
            "date_banner": f"{now:%a %d %b}".upper(),
            "done_today_count": len(done_today),
            "done_today_titles": [t.get("title") or "Task" for t in done_today],
            "pending_count": len(pending),
            "pending_items": pending_items,
            "habits_done": h_done,
            "habits_total": h_tot,
            "focus_sessions": int(self.pomodoro.completed),
            "screen_time_text": scr.get("active_text", "0m"),
            "top_categories": scr.get("top_categories", []),
            "breaks": int(scr.get("breaks", 0)),
            "today_points": int(self.social_stats.today_points()),
        }

    def _sync_wrapup_view(self) -> None:
        if self.wrapup_dialog is not None and self.wrapup_dialog.isVisible():
            self.wrapup_dialog.set_data(self._build_wrapup_payload())

    def _open_wrapup(self, announce: bool = True) -> None:
        was_open = self.wrapup_dialog is not None and self.wrapup_dialog.isVisible()
        if self.wrapup_dialog is None:
            self.wrapup_dialog = WrapUpDialog(on_top=self.on_top, ui_scale=self.ui_scale)
            self.wrapup_dialog.rollover_requested.connect(self._on_wrapup_rollover)
            self.wrapup_dialog.reflection_saved.connect(self._on_wrapup_reflection)
            self.wrapup_dialog.open_notes.connect(self._open_notes)
            self.wrapup_dialog.open_tasks.connect(self._open_tasks)
            self.wrapup_dialog.open_stats.connect(self._open_stats)
            self.wrapup_dialog.closed.connect(lambda: self._on_dialog_closed("wrapup"))
        payload = self._build_wrapup_payload()
        self.wrapup_dialog.set_data(payload)
        self._sync_dialog_links()
        self.wrapup_dialog.show_near(self.pet.geometry())
        if announce and not was_open and not self._rebuilding_ui and self.pet.isVisible():
            recap = build_evening_wrapup(payload)
            self.say(recap["spoken"], "talking", ms=7500, emotion=recap["emotion"], voice=recap["voice"])

    def _on_wrapup_rollover(self, task_ids: list) -> None:
        moved = self.tasks.rollover_to_tomorrow([str(i) for i in task_ids])
        if moved > 0:
            if self.tasks_dialog is not None:
                self.tasks_dialog.refresh()
            self._sync_events_view()
            self._sync_briefing_view()
            self._sync_wrapup_view()
            self._schedule_sheets_sync()
            play_ui("select")
            word = "task" if moved == 1 else "tasks"
            if self.pet.isVisible() and not self.is_dnd_active():
                self.say(f"Moved {moved} {word} cleanly to tomorrow—sleep guilt-free tonight!",
                         "talking", ms=4800, particle="sparkle")

    def _on_wrapup_reflection(self, text: str, mood: str) -> None:
        note = self.notes.append_journal_reflection(text, mood=mood)
        if note is not None:
            if self.notes_dialog is not None:
                self.notes_dialog.refresh()
            play_ui("select")
            if self.pet.isVisible() and not self.is_dnd_active():
                self.say("Saved your reflection to your Journal in Notes! Thanks for sharing today with me.",
                         "talking", ms=4800, particle="heart")

    # -- Quick Command & Free-Form Ask Bar (Spotlight / Global Hotkey) --------
    def _ensure_quick_cmd_dialog(self) -> QuickCommandDialog:
        if self.quick_cmd_dialog is None:
            self.quick_cmd_dialog = QuickCommandDialog(on_top=self.on_top, ui_scale=self.ui_scale)
            self.quick_cmd_dialog.submitted.connect(self._on_quick_command_submitted)
            self.quick_cmd_dialog.action_requested.connect(self._on_quick_command_action)
            self.quick_cmd_dialog.clear_requested.connect(self._clear_ai_chat)
        self._sync_quick_cmd_badge()
        return self.quick_cmd_dialog

    def _open_quick_command(self, toggle: bool = False) -> None:
        dlg = self._ensure_quick_cmd_dialog()
        if toggle and dlg.isVisible() and not dlg._closing and dlg.isActiveWindow():
            dlg.close_animated()
            return
        was_visible = dlg.isVisible() and not dlg._closing
        dlg.show_spotlight()
        if not was_visible:
            play_ui("open_window")

    def _clear_ai_chat(self) -> None:
        self.ai_conversation.clear()
        self._sync_quick_cmd_badge()
        if self.quick_cmd_dialog is not None:
            call_user = str(self.settings["ai_call_user"] or "Joker").strip() or "Joker"
            self.quick_cmd_dialog.show_reply(
                f"Fresh slate, {call_user}! What's our next move?",
                badge="NEW CHAT",
                accent_red=False,
            )

    def _on_quick_command_submitted(self, raw_text: str) -> None:
        dlg = self._ensure_quick_cmd_dialog()
        ctx = self._ctx()
        ai_on = self._ai_ready()
        res = parse_and_plan(raw_text, ctx=ctx, ai_enabled=ai_on)

        if res.kind == "clear_chat":
            self._clear_ai_chat()
            return

        # Multi-turn Gemini / OpenAI Morgana <-> Joker free chat (non-blocking)
        if res.kind == "needs_ai":
            prompt_txt = str(res.payload.get("prompt") or raw_text)
            hist_before = self.ai_conversation.snapshot()
            self.ai_conversation.add_user(prompt_txt)
            self._sync_quick_cmd_badge()
            dlg.show_thinking(prompt_txt)
            started = self.ai_fetcher.ask_chat(
                prompt=prompt_txt,
                ctx=ctx,
                cfg=self._ai_config(),
                history=hist_before,
                fallback=res.payload.get("fallback", res),
            )
            if not started:
                fb: CommandResult = res.payload.get("fallback", res)
                self._present_quick_result(fb)
            return

        # Execute structured local command immediately
        if res.kind == "add_task":
            p = res.payload
            self.tasks.add(
                p["title"],
                priority=p.get("priority", "normal"),
                deadline=p.get("deadline"),
                stat=p.get("stat", "proficiency"),
            )
            if self.tasks_dialog is not None:
                self.tasks_dialog.refresh()
            self._sync_events_view()
            self._sync_briefing_view()
            self._sync_wrapup_view()
            self._schedule_sheets_sync()
        elif res.kind == "add_note":
            p = res.payload
            self.notes.add(p["title"], p["content"], category=p.get("category", "Personal"))
            if self.notes_dialog is not None:
                self.notes_dialog.refresh()
        elif res.kind == "add_event":
            p = res.payload
            self.local_events.add(p["title"], p["start"])
            self._sync_events_view()
            self._sync_briefing_view()
        elif res.kind == "add_finance":
            p = res.payload
            tx = self.finance.add_transaction(
                tx_type=p.get("type", "expense"),
                amount=p["amount"],
                title=p.get("title", ""),
                category=p.get("category", "Jajan"),
                wallet=p.get("wallet", "Tunai"),
                source="quick_cmd",
            )
            if self.finance_dialog is not None:
                self.finance_dialog.refresh()
            if tx is not None and tx["type"] == "expense":
                today = date.today()
                warn = self.finance.budget_warning(tx["category"], today.year, today.month)
                if warn is not None:
                    kind_w, ratio, spent, limit = warn
                    pct = int(round(ratio * 100))
                    res.reply += (
                        f" Watch out—{tx['category']} is at {pct}% ({format_rupiah(spent)} / {format_rupiah(limit)})!"
                        if kind_w == "warn"
                        else f" Heads up—{tx['category']} just crossed its budget ({format_rupiah(spent)} / {format_rupiah(limit)})!"
                    )
        elif res.kind == "start_focus":
            stat_arg = str(res.payload.get("stat") or "")
            if stat_arg:
                self._set_focus_stat(stat_arg)
            mins = int(res.payload.get("minutes") or 0)
            f_m = mins if mins > 0 else int(self.settings["focus_min"])
            r_m = int(self.settings["rest_min"])
            self._start_focus_from_dialog(f_m, r_m, silent_say=True)
        elif res.kind == "start_break":
            mins = int(res.payload.get("minutes") or 5)
            self._start_break_timer(mins, silent_say=True)
        elif res.kind == "start_timer":
            self.manual_timers.add(int(res.payload["seconds"]), str(res.payload["label"]))
            self._sync_timer_ui()
        elif res.kind == "stop_timer":
            self.pomodoro.stop()
            self._sync_timer_ui()
        elif res.kind == "toggle_dnd":
            want = res.payload.get("state")
            new_state = (not self.manual_dnd) if want is None else bool(want)
            self.set_manual_dnd(new_state, announce=False)
            res.reply = (
                "Do Not Disturb is ON. Popups, nudges, and chatter are paused."
                if new_state else "Do Not Disturb is OFF. Morgana is back online!"
            )
            res.badge = "DND ON" if new_state else "DND OFF"
        elif res.kind == "toggle_sound":
            self.set_sound_enabled(bool(res.payload.get("state", True)))
        elif res.kind == "toggle_sleep":
            want = bool(res.payload.get("state", True))
            if want != self.sleep_mode:
                self.sleep_mode = want
                self._restore_state()
        elif res.kind == "toggle_visibility":
            self._set_pet_visible(bool(res.payload.get("visible", True)))
        elif res.kind == "open_panel":
            target = str(res.payload.get("target") or "")
            self._present_quick_result(res)
            dlg.close_animated()
            self._on_quick_command_action(target)
            return

        self._present_quick_result(res)

    def _on_ai_chat_reply(self, ai_res: AIReply, fallback: CommandResult) -> None:
        if ai_res.ok and ai_res.text:
            self.ai_conversation.add_model(ai_res.text)
            self._sync_quick_cmd_badge()
            prov = "GEMINI" if str(self.settings["ai_provider"]) == "gemini" else "AI"
            act_map = {
                "focus_25": "focus_25",
                "break_5": "break_5",
                "open_tasks": "tasks",
                "open_notes": "notes",
                "open_events": "events",
                "open_stats": "stats",
                "open_briefing": "briefing",
                "open_wrapup": "wrapup",
            }
            act_key = ai_res.suggested_action
            res = CommandResult(
                kind="chat",
                reply=ai_res.text,
                emotion=ai_res.emotion,
                voice=ai_res.voice,
                particle=ai_res.particle,
                badge=f"{prov} · MORGANA",
                action_label=ACTION_LABELS.get(act_key, fallback.action_label),
                action_target=act_map.get(act_key, fallback.action_target),
            )
        else:
            res = fallback
            res.badge = "LOCAL FALLBACK"
            if ai_res.error:
                log.debug("AI chat fallback: %s", ai_res.error)
        self._present_quick_result(res)

    def _on_test_ai_requested(self, data: dict) -> None:
        cfg = AIConfig(
            provider=str(data.get("provider") or "gemini"),
            api_key=str(data.get("api_key") or ""),
            model=str(data.get("model") or "gemini-3.5-flash-lite"),
            endpoint=str(data.get("endpoint") or ""),
            call_user=str(data.get("call_user") or "Joker"),
        )
        if not self.ai_fetcher.test_key(cfg) and self.settings_dialog is not None:
            self.settings_dialog.set_ai_test_result(False, "Already testing — please wait a second.")

    def _on_ai_test_done(self, ok: bool, msg: str) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.set_ai_test_result(ok, msg)

    # -- Google Sheets Sync (Write-Only Cloud Relay for WhatsApp Bot) ---------
    def _sheets_sync_config(self, override: dict | None = None) -> SheetsSyncConfig:
        if override is not None:
            return SheetsSyncConfig(
                enabled=bool(override.get("enabled", True)),
                spreadsheet_id=str(override.get("spreadsheet_id") or ""),
                tab_name=str(override.get("tab_name") or "Morgana"),
                key_path=str(override.get("key_path") or ""),
            )
        return SheetsSyncConfig(
            enabled=bool(self.settings["sheets_sync_enabled"]),
            spreadsheet_id=str(self.settings["sheets_sync_id"] or ""),
            tab_name=str(self.settings["sheets_sync_tab"] or "Morgana"),
            key_path=str(self.settings["sheets_sync_key_path"] or ""),
        )

    def _configure_sheets_sync(self, trigger_initial: bool = False) -> None:
        cfg = self._sheets_sync_config()
        if not cfg.is_ready(APP_DIR):
            self._sheets_debounce_timer.stop()
            self._sheets_retry_timer.stop()
            self._sheets_dirty = False
            self._sheets_fail_streak = 0
            self._sheets_last_pushed_rows = None
            return
        if trigger_initial:
            self._sheets_dirty = True
            self._sheets_retry_timer.start(SHEETS_SYNC_STARTUP_DELAY_MS)

    def _schedule_sheets_sync(self) -> None:
        if not self._sheets_sync_config().is_ready(APP_DIR):
            return
        self._sheets_dirty = True
        self._sheets_debounce_timer.start(SHEETS_SYNC_DEBOUNCE_MS)

    def _trigger_sheets_sync(self, cfg_override: SheetsSyncConfig | None = None,
                             manual: bool = False) -> None:
        cfg = cfg_override if cfg_override is not None else self._sheets_sync_config()
        if not cfg.is_ready(APP_DIR):
            if manual and self.settings_dialog is not None:
                if not cfg.normalized_sheet_id():
                    msg = "Enter a valid Google Spreadsheet ID or URL."
                else:
                    msg = "Select your Service Account JSON key file (e.g. kunci-google.json)."
                self.settings_dialog.set_sheets_status(False, msg)
            return
        if self.sheets_sync.busy:
            self._sheets_dirty = True
            if manual and self.settings_dialog is not None:
                self.settings_dialog.set_sheets_status(False, "Sync already in progress — please wait a moment.")
            return

        rows = serialize_tasks_to_rows(self.tasks.all())
        push_key = (cfg.normalized_sheet_id(), cfg.normalized_tab(), rows)
        if not manual and self._sheets_last_pushed_rows is not None and push_key == self._sheets_last_pushed_rows:
            self._sheets_dirty = False
            self._sheets_fail_streak = 0
            return

        self._sheets_dirty = False
        self._sheets_manual_sync = bool(manual)
        self._sheets_inflight_rows = push_key
        self.sheets_sync.push(cfg, rows)

    def _on_test_sheets_sync_requested(self, data: dict) -> None:
        cfg = self._sheets_sync_config(override=data)
        if not self.sheets_sync.test_connection(cfg) and self.settings_dialog is not None:
            self.settings_dialog.set_sheets_status(False, "Already testing — please wait a second.")

    def _on_sync_sheets_now_requested(self, data: dict) -> None:
        cfg = self._sheets_sync_config(override=data)
        self._trigger_sheets_sync(cfg_override=cfg, manual=True)

    def _on_sheets_test_done(self, ok: bool, msg: str) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.set_sheets_status(ok, msg)

    def _on_sheets_push_done(self, res: SheetsPushResult) -> None:
        manual = self._sheets_manual_sync
        self._sheets_manual_sync = False
        if not res.ok:
            log.debug("Google Sheets sync push failed: %s", res.error)
            self._sheets_inflight_rows = None
            self._sheets_dirty = True
            if manual and self.settings_dialog is not None:
                self.settings_dialog.set_sheets_status(False, res.error or "Google Sheets sync failed.")
            if self._sheets_sync_config().is_ready(APP_DIR):
                idx = self._sheets_fail_streak
                self._sheets_fail_streak += 1
                if idx < len(SHEETS_SYNC_RETRY_DELAYS_MS):
                    self._sheets_retry_timer.start(SHEETS_SYNC_RETRY_DELAYS_MS[idx])
            return

        self._sheets_last_pushed_rows = self._sheets_inflight_rows
        self._sheets_inflight_rows = None
        self._sheets_fail_streak = 0
        self._sheets_retry_timer.stop()
        if manual and self.settings_dialog is not None:
            noun = "deadline" if res.synced_count == 1 else "deadlines"
            self.settings_dialog.set_sheets_status(True, f"Pushed {res.synced_count} active {noun} to Google Sheets.")
        if self._sheets_dirty and self._sheets_sync_config().is_ready(APP_DIR):
            self._sheets_debounce_timer.start(SHEETS_SYNC_DEBOUNCE_MS)

    def _present_quick_result(self, res: CommandResult) -> None:
        if self.quick_cmd_dialog is not None:
            self.quick_cmd_dialog.show_reply(
                res.reply,
                badge=res.badge or "MORGANA",
                action_label=res.action_label,
                action_target=res.action_target,
                accent_red=(res.emotion in ("angry", "worried")),
            )
        if self.pet.isVisible() and not self.is_dnd_active():
            self.say(
                res.reply,
                "talking",
                ms=5200,
                emotion=res.emotion or None,
                voice=res.voice or None,
                particle=res.particle,
            )
        else:
            play_ui("select")

    def _on_quick_command_action(self, target: str) -> None:
        if self.quick_cmd_dialog is not None and self.quick_cmd_dialog.isVisible():
            self.quick_cmd_dialog.hide()
        if target == "tasks":
            self._open_tasks(announce=False)
        elif target == "notes":
            self._open_notes(announce=False)
        elif target == "events":
            self._open_events()
        elif target == "stats":
            self._open_stats(announce=False)
        elif target == "finance":
            self._open_finance(announce=False)
        elif target == "timer":
            self._open_timer()
        elif target == "briefing":
            self._open_briefing(announce=False)
        elif target == "wrapup":
            self._open_wrapup(announce=False)
        elif target == "talk":
            self._set_pet_visible(True)
            self._start_talk()
        elif target == "settings":
            self._open_settings_deferred()
        elif target == "break_5":
            self._start_break_timer(5)
        elif target == "focus_10":
            self._start_focus_from_dialog(10, int(self.settings["rest_min"]))
        elif target == "focus_25":
            self._start_focus_from_dialog(25, int(self.settings["rest_min"]))

    def _set_pet_visible(self, visible: bool) -> None:
        if visible:
            self.pet.show()
            self.pet.raise_()
            self.particles.follow()
        else:
            self.menu.hide()
            self.choices.close_choices(silent=True)
            self.particles.hide()
            self.bubble.dismiss()
            self.pet.hide()
        self._update_tray_tooltip()

    def _toggle_pet_visibility(self) -> None:
        self._set_pet_visible(not self.pet.isVisible())

    # -- startup: Daily Morning Briefing (once/day) atau ringkasan singkat ----
    def _startup_report(self) -> None:
        if self.curfew_active or self.is_dnd_active():
            return
        today_str = datetime.now().strftime("%Y-%m-%d")
        if bool(self.settings["morning_briefing"]) and self.settings["last_briefing_date"] != today_str:
            self.settings.set(last_briefing_date=today_str)
            self._open_briefing(announce=True)
            return

        pending = self.tasks.pending()
        greeting = random.choice(greeting_pool(self._greetings, datetime.now().hour))
        if not pending or "tasks" not in self.settings["enabled_tabs"]:
            self.say(greeting, context="startup")
            return
        shown = pending[:STARTUP_TASK_LIMIT]
        lines = [f"{i}. {clip(t['title'], 44)}" for i, t in enumerate(shown, 1)]
        if len(pending) > len(shown):
            lines.append(f"...and {len(pending) - len(shown)} more")
        word = "task" if len(pending) == 1 else "tasks"
        self.say(f"{greeting}\n\nYou have {len(pending)} pending {word} today:\n" + "\n".join(lines),
                 "talking", ms=13000, context="startup")

    # -- Unified Persona 5 Social Stats Reward Presentation (Particles + Gold Star) --
    def _ensure_stats_dialog(self) -> SocialStatsDialog:
        if self.stats_dialog is None:
            self.stats_dialog = SocialStatsDialog(
                self.social_stats, habits=self.habits, on_top=self.on_top, ui_scale=self.ui_scale
            )
            self.stats_dialog.stat_logged.connect(self._on_stat_logged)
            self.stats_dialog.stats_changed.connect(self._on_stats_modified)
            self.stats_dialog.habit_toggled.connect(self._on_habit_toggled)
            self.stats_dialog.open_briefing.connect(lambda: self._open_briefing(announce=False))
            self.stats_dialog.open_settings.connect(self._open_settings_deferred)
            self.stats_dialog.closed.connect(lambda: self._on_dialog_closed("stats"))
        return self.stats_dialog

    def _present_stat_reward(self, stat: str, activity: str, points: int,
                             old_lv: int, new_lv: int,
                             prev_totals: dict[str, int] | None = None,
                             on_normal_speech=None,
                             auto_open: bool = True) -> None:
        """Play Persona 5 music-note particles around Morgana, open Social Stats to show the
        gold star expanding, and fire Rank Up golden starburst + music notes on level up."""
        leveled_up = new_lv > old_lv
        label = SOCIAL_STATS.get(stat, {}).get("label", stat.title())

        # Stage 1 (immediate): golden music notes + sparkles around Morgana!
        self.particles.emit_stat_gain(points=points, leveled_up=leveled_up)

        # Auto-open Social Stats window staged at `prev_totals` so the gold star grows smoothly
        if auto_open and bool(self.settings["auto_open_stats_on_reward"]) and not self._rebuilding_ui:
            dlg = self._ensure_stats_dialog()
            dlg.prepare_reward_animation(stat, prev_totals=prev_totals)
            if not dlg.isVisible():
                dlg.show_near(self.pet.geometry())
            else:
                dlg.raise_()
        elif self.stats_dialog is not None and self.stats_dialog.isVisible():
            self.stats_dialog.prepare_reward_animation(stat, prev_totals=prev_totals)

        def _on_point_anim() -> None:
            if self.stats_dialog is not None:
                self.stats_dialog.animate_point_gain(stat=stat, points=points, activity=activity)
            self._sync_briefing_view()
            if not leveled_up:
                self.pet.bump(0.85)
                if on_normal_speech is not None:
                    on_normal_speech()

        def _on_level_up() -> None:
            rank = stat_rank_title(stat, new_lv)
            if self.stats_dialog is not None:
                self.stats_dialog.animate_rank_up(stat=stat, old_lv=old_lv, new_lv=new_lv)
            self._sync_briefing_view()
            self.particles.emit_rank_up()
            self.pet.bump(1.35)
            self.say(f"RANK UP! Your {label} leveled up to Lv {new_lv} ({rank})! "
                     "All that consistency is really paying off!", "action", ms=7500, context="rank_up")

        self.audio.play_stats_sequence(
            points=points,
            leveled_up=leveled_up,
            on_point_anim=_on_point_anim,
            on_level_up=_on_level_up if leveled_up else None,
        )

    def _award_auto_stat(self, stat: str, activity: str, points: int,
                         source: str, ref_id: str = "",
                         announce: bool = True) -> tuple[int, int, int]:
        """Award Social Stat points automatically (if enabled), triggering P5 gold-star animation."""
        if not bool(self.settings["auto_stat_rewards"]):
            return 0, 1, 1
        prev_totals = self.social_stats.totals()
        entry, old_lv, new_lv = self.social_stats.add(
            stat=stat, activity=activity, points=points, source=source, ref_id=ref_id
        )
        if entry is None:
            return 0, 1, 1
        label = SOCIAL_STATS.get(stat, {}).get("label", stat.title())

        def _speech() -> None:
            if announce:
                self.say(f"Auto-reward: +{points} {label} ({clip(activity, 34)})!",
                         "talking", ms=4800, context="stat_log")

        self._present_stat_reward(
            stat=stat,
            activity=activity,
            points=points,
            old_lv=old_lv,
            new_lv=new_lv,
            prev_totals=prev_totals,
            on_normal_speech=_speech if announce else None,
            auto_open=True,
        )
        return points, old_lv, new_lv

    # -- interaksi pet ---------------------------------------------------------
    def _on_pet_clicked(self) -> None:
        had_choice = self.choices.is_open or self.dialogue_engine.is_active
        if had_choice:
            self._pending_choices = []
            self.choices.close_choices(silent=True)
            self.dialogue_engine.cancel()
        if self.menu.is_open:
            self.menu.close_menu(silent=False, emit_user_close=True)
            if had_choice:
                self.bubble.dismiss()
        else:
            self.menu.open_menu()
            self._say_tuple(self.dialogue.radial_open(self._ctx()), ms=5200)

    def _on_pet_double_clicked(self) -> None:
        self._start_talk()

    def _on_menu_user_closed(self) -> None:
        if not self._rebuilding_ui:
            self._say_tuple(self.dialogue.radial_close(self._ctx()), ms=3400)

    def _on_wall_bounced(self) -> None:
        now = time.monotonic()
        self.particles.emit_anger()
        if now - self._last_bump_t >= 2.5:
            self._last_bump_t = now
            self._say_tuple(self.dialogue.wall_bump(), state="action", ms=3600)
        else:
            self.audio.play_voice("uwooh")

    def _on_task_reward_eligible(self, task_id: str) -> None:
        self._pending_task_reward_note = ""
        task = self.tasks.get(task_id)
        if task is None or task.get("rewarded"):
            return
        if not bool(self.settings["auto_stat_rewards"]):
            return
        dl = parse_dt(task.get("deadline"))
        on_time = dl is not None and dl >= datetime.now()
        pts = 2 if (task.get("priority") == "high" or on_time) else 1
        stat_k = self.tasks.stat_of(task)
        stat_label = SOCIAL_STATS[stat_k]["label"]
        self.tasks.mark_rewarded(task_id)
        awarded, old_lv, new_lv = self._award_auto_stat(
            stat=stat_k,
            activity=f"Task: {task.get('title', 'Task')}",
            points=pts,
            source="task",
            ref_id=task_id,
            announce=False,
        )
        if awarded > 0 and new_lv <= old_lv:
            self._pending_task_reward_note = f"+{awarded} {stat_label}"
        elif new_lv > old_lv:
            # Rank Up celebration takes over speech cleanly
            self._pending_task_reward_note = "__RANK_UP__"

    def _on_task_completed(self, title: str, remaining: int) -> None:
        self.pet.bump(0.75)
        note = self._pending_task_reward_note
        self._pending_task_reward_note = ""
        if note == "__RANK_UP__":
            return
        if not note:
            self.particles.emit_sparkle(5)
        self._say_tuple(self.dialogue.task_completed(title, remaining, reward_note=note), ms=4600)

    def _on_note_completed(self, note_id: str) -> None:
        note = self.notes.get(note_id)
        if note is None or note.get("rewarded") or not note.get("deadline"):
            return
        if not bool(self.settings["auto_stat_rewards"]):
            return
        dl = parse_dt(note.get("deadline"))
        pts = 2 if (dl is not None and dl >= datetime.now()) else 1
        self.notes.mark_rewarded(note_id)
        self._award_auto_stat(
            stat="knowledge",
            activity=f"Note: {note.get('title', 'Note')}",
            points=pts,
            source="note",
            ref_id=note_id,
            announce=True,
        )

    def _on_note_sent_to_task(self, title: str, deadline_obj) -> None:
        """Create a real Task promoted directly from Notes (`→ TASK` bridge)."""
        dl = deadline_obj if isinstance(deadline_obj, datetime) else None
        # Match a natural default Social Stat to the current notebook (editable anytime in Tasks)
        cat_stat = {
            "Study": "knowledge",
            "Work": "proficiency",
            "Ideas": "charm",
            "Journal": "kindness",
            "Personal": "kindness",
        }
        note_cat = getattr(self.notes_dialog, "_category", "") if self.notes_dialog is not None else ""
        stat_k = cat_stat.get(note_cat, "proficiency")
        task = self.tasks.add(title, priority="normal", deadline=dl, stat=stat_k)
        if task is None:
            return
        if self.tasks_dialog is not None:
            self.tasks_dialog.refresh()
        self._sync_events_view()
        self._sync_briefing_view()
        self._schedule_sheets_sync()
        self.pet.bump(0.8)
        self._say_tuple(self.dialogue.note_sent_to_task(task["title"]), ms=4400, particle="sparkle")

    def _on_habit_toggled(self, habit_id: str, now_done: bool, entry, old_lv: int, new_lv: int) -> None:
        self._sync_briefing_view()
        if not now_done or entry is None:
            return
        habit = self.habits.get(habit_id)
        title = habit.get("title", "Daily Habit") if habit else entry.get("activity", "Daily Habit")
        stat = entry.get("stat", "knowledge")
        pts = int(entry.get("points", 2))
        label = SOCIAL_STATS.get(stat, {}).get("label", stat.title())
        streak = self.habits.streak(habit) if habit else 1
        done_today, total_habits = self.habits.today_summary()

        # Reconstruct totals prior to this habit check-in so the gold star animates smoothly
        prev_totals = self.social_stats.totals()
        prev_totals[stat] = max(0, prev_totals.get(stat, 0) - pts)

        def _habit_speech() -> None:
            self._say_tuple(
                self.dialogue.habit_completed(title, label, pts, streak, done_today, total_habits),
                ms=5000,
            )

        self._present_stat_reward(
            stat=stat,
            activity=title,
            points=pts,
            old_lv=old_lv,
            new_lv=new_lv,
            prev_totals=prev_totals,
            on_normal_speech=_habit_speech,
            auto_open=False,   # already triggered inside SocialStatsDialog
        )

    def _on_dialog_closed(self, kind: str) -> None:
        if self._rebuilding_ui:
            return
        self._say_tuple(self.dialogue.window_close(kind, self._ctx()), ms=3600)

    def _on_pet_moved(self) -> None:
        self.menu.follow()
        self.bubble.follow()
        self.particles.follow()
        self.choices.follow()

    def _sync_dialog_links(self) -> None:
        enabled = set(self.settings["enabled_tabs"])
        if self.tasks_dialog is not None:
            self.tasks_dialog._notes_btn.setVisible("notes" in enabled)
        if self.notes_dialog is not None:
            self.notes_dialog._tasks_btn.setVisible("tasks" in enabled)
        if self.events_dialog is not None:
            self.events_dialog._tasks_btn.setVisible("tasks" in enabled)
            self.events_dialog._notes_btn.setVisible("notes" in enabled)
        if self.briefing_dialog is not None:
            self.briefing_dialog._btn_ev.setVisible("events" in enabled)
            self.briefing_dialog._btn_tk.setVisible("tasks" in enabled)
            self.briefing_dialog._btn_st.setVisible("stats" in enabled)
        if self.wrapup_dialog is not None:
            self.wrapup_dialog._btn_nt.setVisible("notes" in enabled)
            self.wrapup_dialog._btn_tk.setVisible("tasks" in enabled)
            self.wrapup_dialog._btn_st.setVisible("stats" in enabled)

    def _open_tasks(self, announce: bool = True) -> None:
        was_open = self.tasks_dialog is not None and self.tasks_dialog.isVisible()
        if self.tasks_dialog is None:
            self.tasks_dialog = TasksDialog(self.tasks, on_top=self.on_top, ui_scale=self.ui_scale)
            self.tasks_dialog.open_settings.connect(self._open_settings_deferred)
            self.tasks_dialog.open_notes.connect(self._open_notes)
            self.tasks_dialog.tasks_changed.connect(self._sync_events_view)
            self.tasks_dialog.tasks_changed.connect(self._sync_briefing_view)
            self.tasks_dialog.tasks_changed.connect(self._sync_wrapup_view)
            self.tasks_dialog.tasks_changed.connect(self._schedule_sheets_sync)
            self.tasks_dialog.task_reward_eligible.connect(self._on_task_reward_eligible)
            self.tasks_dialog.task_completed.connect(self._on_task_completed)
            self.tasks_dialog.closed.connect(lambda: self._on_dialog_closed("tasks"))
        self._sync_dialog_links()
        self.tasks_dialog.show_near(self.pet.geometry())
        if announce and not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("tasks", self._ctx()), ms=4800)

    def _open_notes(self, announce: bool = True) -> None:
        was_open = self.notes_dialog is not None and self.notes_dialog.isVisible()
        if self.notes_dialog is None:
            self.notes_dialog = NotesDialog(self.notes, self.tasks, on_top=self.on_top, ui_scale=self.ui_scale)
            self.notes_dialog.open_settings.connect(self._open_settings_deferred)
            self.notes_dialog.open_tasks.connect(self._open_tasks)
            self.notes_dialog.notes_changed.connect(self._sync_events_view)
            self.notes_dialog.notes_changed.connect(self._sync_briefing_view)
            self.notes_dialog.note_completed.connect(self._on_note_completed)
            self.notes_dialog.send_to_task.connect(self._on_note_sent_to_task)
            self.notes_dialog.closed.connect(lambda: self._on_dialog_closed("notes"))
        self._sync_dialog_links()
        self.notes_dialog.show_near(self.pet.geometry())
        if announce and not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("notes", self._ctx()), ms=4800)

    def _combined_events_result(self, base: CalendarResult | None = None) -> CalendarResult:
        src = base if base is not None else (self._last_cal_result or CalendarResult([], "ok"))
        return merge_companion_items(
            src,
            notes=self.notes.all(),
            tasks=self.tasks.all(),
            local_events=self.local_events.all(),
        )

    def _sync_events_view(self) -> None:
        if self.events_dialog is not None:
            self.events_dialog.set_result(self._combined_events_result())

    def _open_events(self) -> None:
        was_open = self.events_dialog is not None and self.events_dialog.isVisible()
        if self.events_dialog is None:
            self.events_dialog = EventsDialog(local_events=self.local_events,
                                              on_top=self.on_top, ui_scale=self.ui_scale)
            self.events_dialog.refresh_requested.connect(self._refresh_events)
            self.events_dialog.local_events_changed.connect(self._sync_events_view)
            self.events_dialog.local_events_changed.connect(self._sync_briefing_view)
            self.events_dialog.open_tasks.connect(self._open_tasks)
            self.events_dialog.open_notes.connect(self._open_notes)
            self.events_dialog.open_settings.connect(self._open_settings_deferred)
            self.events_dialog.closed.connect(lambda: self._on_dialog_closed("events"))
        self._sync_dialog_links()
        has_local_or_due = (self._last_cal_result is not None or self.notes.with_deadline()
                            or self.tasks.with_deadline() or bool(self.local_events.all()))
        if has_local_or_due:
            self.events_dialog.set_result(self._combined_events_result())
        self.events_dialog.show_near(self.pet.geometry())
        if not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("events", self._ctx()), ms=5000)
        self._refresh_events(interactive=not self.calendar.has_token())

    def _open_stats(self, announce: bool = True) -> None:
        was_open = self.stats_dialog is not None and self.stats_dialog.isVisible()
        self._ensure_stats_dialog()
        self.stats_dialog.show_near(self.pet.geometry())
        if announce and not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("stats", self._ctx()), ms=4800)

    def _on_stats_modified(self) -> None:
        self._sync_briefing_view()
        self._sync_wrapup_view()

    def _open_finance(self, announce: bool = True) -> None:
        was_open = self.finance_dialog is not None and self.finance_dialog.isVisible()
        if self.finance_dialog is None:
            self.finance_dialog = FinanceDialog(self.finance, on_top=self.on_top, ui_scale=self.ui_scale)
            self.finance_dialog.finance_changed.connect(self._on_finance_changed)
            self.finance_dialog.open_settings.connect(self._open_settings_deferred)
            self.finance_dialog.closed.connect(lambda: self._on_dialog_closed("finance"))
        self.finance_dialog.refresh()
        self.finance_dialog.show_near(self.pet.geometry())
        if announce and not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("finance", self._ctx()), ms=4800)

    def _on_finance_changed(self, kind: str, tx: dict) -> None:
        if self._rebuilding_ui or kind not in ("add", "bill"):
            return
        if tx.get("type") == "expense" and tx.get("category"):
            try:
                d = date.fromisoformat(str(tx.get("date") or ""))
            except ValueError:
                d = date.today()
            warn = self.finance.budget_warning(tx["category"], d.year, d.month)
            if warn is not None:
                level, ratio, spent, limit = warn
                pct = int(round(ratio * 100))
                if level == "over":
                    self._say_tuple((
                        f"Whoa! {tx['category']} just passed its monthly budget ({format_rupiah(spent)} / {format_rupiah(limit)})!",
                        "surprised", "uwooh",
                    ), ms=5600)
                else:
                    self._say_tuple((
                        f"Careful! {tx['category']} is at {pct}% of this month's budget ({format_rupiah(spent)} / {format_rupiah(limit)}).",
                        "neutral", "naa",
                    ), ms=5000)

    def _open_timer(self) -> None:
        was_open = self.timer_dialog is not None and self.timer_dialog.isVisible()
        if self.timer_dialog is None:
            self.timer_dialog = TimerDialog(self.settings, on_top=self.on_top, ui_scale=self.ui_scale)
            self.timer_dialog.focus_start_requested.connect(self._start_focus_from_dialog)
            self.timer_dialog.focus_stop_requested.connect(self._stop_pomodoro)
            self.timer_dialog.focus_stat_changed.connect(self._set_focus_stat)
            self.timer_dialog.manual_add_requested.connect(self._add_manual_timer)
            self.timer_dialog.manual_cancel_requested.connect(self._cancel_manual_timer)
            self.timer_dialog.break_requested.connect(self._start_break_timer)
            self.timer_dialog.open_settings.connect(self._open_settings_deferred)
            self.timer_dialog.closed.connect(lambda: self._on_dialog_closed("timer"))
        self._sync_timer_ui()
        self.timer_dialog.update_screen_time(self.screen_time.today_summary())
        self.timer_dialog.show_near(self.pet.geometry())
        if not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("timer", self._ctx()), ms=4800)

    def _visible_dialog_rects(self) -> list[QRect]:
        rects: list[QRect] = []
        for dlg in (self.tasks_dialog, self.notes_dialog, self.events_dialog,
                    self.stats_dialog, self.finance_dialog, self.timer_dialog,
                    self.settings_dialog, self.briefing_dialog, self.wrapup_dialog,
                    self.quick_cmd_dialog):
            if dlg is not None and dlg.isVisible():
                rects.append(dlg.geometry())
        return rects

    def _open_settings_deferred(self) -> None:
        """Open Settings after any QMenu/button release finishes (prevents Windows popup grab race)."""
        QTimer.singleShot(60, self._open_settings)

    def _on_menu_action(self, key: str, anchor: QPoint) -> None:
        self.menu.close_menu(silent=True, emit_user_close=False)
        if key in ("events", "agenda"):
            self._open_events()
        elif key == "tasks":
            self._open_tasks()
        elif key == "notes":
            self._open_notes()
        elif key == "talk":
            self._start_talk()
        elif key == "stats":
            self._open_stats()
        elif key == "finance":
            self._open_finance()
        elif key == "timer":
            self._open_timer()
        elif key == "system":
            play_ui("open_window")
            self._say_tuple(self.dialogue.window_open("system", self._ctx()), ms=4200)
            self.system_menu.popup(anchor)

    # -- EVENTS: 7-day Google + Local Events + Task & Note Deadlines --------------
    def _refresh_events(self, interactive: bool = True) -> None:
        if not self.calendar.is_configured():
            result = self.calendar.get_upcoming()
            self._last_cal_result = result
            if self.args.demo_reminders:
                self._cal_events = result.events
            if self.events_dialog is not None:
                self.events_dialog.set_result(self._combined_events_result(result))
            self._sync_briefing_view()
            return
        self._agenda_wanted = interactive
        has_local = bool(self.notes.with_deadline() or self.tasks.with_deadline() or self.local_events.all())
        if self.events_dialog is not None and self._last_cal_result is None and not has_local:
            self.events_dialog.set_loading(True)
        if interactive and not self.calendar.has_token():
            self.say("Opening your browser to connect Google Calendar (one-time setup). "
                     "Finish signing in there!", ms=9000)
        self.fetcher.fetch(interactive=interactive)

    def _refresh_calendar(self) -> None:
        """Pembaruan latar (tanpa membuka browser). Dipakai untuk pengingat acara & cache mingguan."""
        if not self.calendar.is_configured():
            result = self.calendar.get_upcoming()
            self._last_cal_result = result
            if self.args.demo_reminders:
                self._cal_events = result.events
            self._sync_briefing_view()
            return
        if self.calendar.has_token():
            self.fetcher.fetch(interactive=False)

    def _on_calendar_result(self, result: CalendarResult) -> None:
        self._last_cal_result = result
        if result.status == "ok" or (result.is_dummy and self.args.demo_reminders):
            self._cal_events = result.events
        if self.events_dialog is not None:
            self.events_dialog.set_result(self._combined_events_result(result))
        self._sync_briefing_view()
        if not self._agenda_wanted:
            return
        if result.status == "auth_needed":     # fetch latar gagal login -> ulangi interaktif
            if self.fetcher.fetch(interactive=True):
                self.say("Your Google authorization expired. Opening your browser to sign in again...", ms=9000)
            return
        self._agenda_wanted = False

    # -- SOCIAL STATS -------------------------------------------------------------
    def _on_stat_logged(self, stat: str, activity: str, pts: int, old_lv: int, new_lv: int) -> None:
        label = SOCIAL_STATS.get(stat, {}).get("label", stat.title())
        self._sync_briefing_view()
        self._sync_wrapup_view()

        prev_totals = self.social_stats.totals()
        prev_totals[stat] = max(0, prev_totals.get(stat, 0) - pts)

        def _manual_speech() -> None:
            self.say(f"Logged +{pts} {label} ({clip(activity, 36)})! Keep building your momentum!",
                     "talking", ms=4500, context="stat_log")

        self._present_stat_reward(
            stat=stat,
            activity=activity,
            points=pts,
            old_lv=old_lv,
            new_lv=new_lv,
            prev_totals=prev_totals,
            on_normal_speech=_manual_speech,
            auto_open=False,   # already logged inside SocialStatsDialog
        )

    # -- pengingat: acara H-2 jam, acara lokal, deadline tugas & catatan ----------
    def _check_reminders(self) -> None:
        messages: list[str] = []
        now = datetime.now()
        aware_now = now.astimezone()

        # 1) catatan yang deadline-nya sudah tiba (termasuk yang terlewat saat aplikasi mati)
        for note in self.notes.due(now):
            self.notes.mark_notified(note["id"])
            title = note.get("title") or "Note"
            line = f"NOTE DUE NOW: {title}"
            snippet = clip(note.get("content", ""), 70)
            if snippet and snippet != title:
                line += f"\n{snippet}"
            messages.append(line)

        # 2) tugas yang deadline-nya sudah tiba
        for task in self.tasks.due(now):
            self.tasks.mark_notified(task["id"])
            title = task.get("title") or "Task"
            messages.append(f"TASK DUE NOW: {title}")

        # 3) hitung mundur: mulai H-<jendela>, ulangi tiap <interval> menit sampai acara dimulai
        window = timedelta(minutes=self.settings["event_window_min"])
        interval_s = self.settings["event_interval_min"] * 60 - 15      # toleransi 15 dtk karena timer 30 dtk
        upcoming: list[tuple[str, str, datetime]] = []                  # (kunci, judul, waktu mulai)
        for ev in self._cal_events:
            if not ev.all_day:
                upcoming.append((f"cal:{ev.id}@{ev.start.isoformat()}", ev.title, ev.start))
        for lev in local_calendar_events(self.local_events.all(), now=aware_now, days=2):
            if not lev.all_day:
                upcoming.append((f"local:{lev.id}@{lev.start.isoformat()}", lev.title, lev.start))
        if NOTE_COUNTDOWN_REMINDERS:
            for n in self.notes.with_deadline():
                dl = parse_dt(n.get("deadline"))
                if dl is not None:
                    upcoming.append((f"note:{n['id']}@{n['deadline']}", n.get("title") or "Note",
                                     dl.astimezone()))
            for t in self.tasks.with_deadline():
                dl = parse_dt(t.get("deadline"))
                if dl is not None:
                    upcoming.append((f"task:{t['id']}@{t['deadline']}", t.get("title") or "Task",
                                     dl.astimezone()))
        countdown: list[str] = []
        for key, title, start in upcoming:
            delta = start - aware_now
            if timedelta(0) < delta <= window:
                last = self._remind_last.get(key)
                if last is None or time.monotonic() - last >= interval_s:
                    self._remind_last[key] = time.monotonic()
                    countdown.append(f"{title} ({start:%H:%M}, in {human_delta(delta)})")
            elif ((key.startswith("cal:") or key.startswith("local:")) and key not in self._remind_started
                  and -timedelta(seconds=EVENT_STARTED_GRACE_S) < delta <= timedelta(0)):
                self._remind_started.add(key)
                messages.append(f"STARTING NOW: {title} ({start:%H:%M})")
        if countdown:
            head = "Upcoming:" if len(countdown) == 1 else "Upcoming events:"
            messages.append(f"{head} " + " · ".join(countdown) + "\n" + self._bag_agenda.next())

        if messages:
            if self.is_dnd_active():
                # Queue important due/starting alerts cleanly until DND ends
                for m in messages:
                    if m not in self._deferred_reminders:
                        self._deferred_reminders.append(m)
                self._deferred_reminders = self._deferred_reminders[-8:]
                return
            joined = "\n\n".join(messages)
            if self.pet.isVisible():
                self.say(joined, "talking", ms=12000, context="reminder", particle="alert")
                self.pet.bump(1.3)                                      # melompat menarik perhatian
            else:
                self._notify_tray("Morgana Reminder", joined)
            if not self.audio.enabled:
                play_beep()

    def _notify_tray(self, title: str, message: str) -> None:
        if self.tray is not None and not self.is_dnd_active():
            try:
                self.tray.showMessage(title, message[:240], QSystemTrayIcon.MessageIcon.Information, 6000)
            except Exception:
                pass

    # -- SYSTEM: opsi cepat & mode tidur ---------------------------------------
    def _build_system_menu(self) -> None:
        m = QMenu()
        m.setStyleSheet(menu_qss(self.ui_scale))
        self.system_menu = m
        self._attach_menu_sfx(m)
        self.act_sys_cmd = m.addAction("ASK & COMMAND...", self._open_quick_command)
        m.addAction("TALK WITH MORGANA...", lambda: self._start_talk())
        m.addAction("DAILY BRIEFING...", lambda: self._open_briefing(announce=True))
        m.addAction("EVENING WRAP-UP...", lambda: self._open_wrapup(announce=True))
        m.addSeparator()
        self.act_sys_status = m.addAction("SYSTEM STATUS (CPU / RAM)", self._act_item)
        self.act_sys_pomo = m.addAction("TIMER & FOCUS...", self._open_timer)
        self.act_sys_dnd = m.addAction("DO NOT DISTURB")
        self.act_sys_dnd.setCheckable(True)
        self.act_sys_dnd.setChecked(self.manual_dnd)
        self.act_sys_dnd.toggled.connect(self.set_manual_dnd)
        self.act_sys_sleep = m.addAction("SLEEP MODE", self._toggle_sleep_mode)
        self.act_sys_bed = m.addAction("BEDTIME")
        self.act_sys_bed.setEnabled(False)
        m.addAction("SETTINGS...", self._open_settings_deferred)
        m.addSeparator()
        m.addAction("OPEN VS CODE", self._launch_vscode)
        m.addAction("OPEN TERMINAL", self._launch_terminal)
        m.aboutToShow.connect(self._refresh_system_menu)

    def _refresh_system_menu(self) -> None:
        hk = str(self.settings["quick_hotkey"])
        self.act_sys_cmd.setText(f"ASK & COMMAND...  ({hk})" if hk and hk != "Off" else "ASK & COMMAND...  (Ctrl+K)")
        self.act_sys_pomo.setVisible("timer" in self.settings["enabled_tabs"])
        if self.pomodoro.running:
            self.act_sys_pomo.setText(f"TIMER & FOCUS  ({fmt_mmss(self.pomodoro.remaining())} left)")
        elif self.manual_timers.nearest() is not None:
            nxt = self.manual_timers.nearest()
            self.act_sys_pomo.setText(f"TIMER & FOCUS  ({fmt_hms(nxt.remaining())} left)")
        else:
            self.act_sys_pomo.setText("TIMER & FOCUS...")
        self.act_sys_dnd.blockSignals(True)
        self.act_sys_dnd.setChecked(self.manual_dnd)
        self.act_sys_dnd.blockSignals(False)
        if self._auto_dnd_reason and not self.manual_dnd:
            self.act_sys_dnd.setText(f"DO NOT DISTURB  (Auto: {self._auto_dnd_reason.title()})")
        else:
            self.act_sys_dnd.setText("DO NOT DISTURB")
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

    def _on_settings_closed(self, dlg: SettingsDialog) -> None:
        """Dispose closed SettingsDialog so every reopen creates a clean native window."""
        if self.settings_dialog is dlg:
            self.settings_dialog = None
        dlg.deleteLater()
        self._on_dialog_closed("settings")

    def _open_settings(self) -> None:
        was_open = self.settings_dialog is not None and self.settings_dialog.isVisible() and not self.settings_dialog._closing
        if self.settings_dialog is not None and (not self.settings_dialog.isVisible() or self.settings_dialog._closing):
            old = self.settings_dialog
            self.settings_dialog = None
            old.hide()
            old.deleteLater()
        if self.settings_dialog is None:
            dlg = SettingsDialog(
                self.settings,
                get_on_top=lambda: self.on_top,
                get_autostart=autostart.is_enabled,
                get_hotkey_status=lambda: self.hotkeys.status_text,
                on_top=self.on_top,
                ui_scale=self.ui_scale,
            )
            dlg.saved.connect(self.apply_settings)
            dlg.always_on_top_changed.connect(self.set_always_on_top)
            dlg.autostart_changed.connect(self._toggle_autostart)
            dlg.test_ai_requested.connect(self._on_test_ai_requested)
            dlg.test_sheets_sync_requested.connect(self._on_test_sheets_sync_requested)
            dlg.sync_sheets_now_requested.connect(self._on_sync_sheets_now_requested)
            dlg.closed.connect(lambda d=dlg: self._on_settings_closed(d))
            self.settings_dialog = dlg
        # If any panel is open, center Settings over it so it's never buried
        shown = False
        for panel in (self.notes_dialog, self.tasks_dialog, self.events_dialog,
                      self.stats_dialog, self.finance_dialog, self.timer_dialog,
                      self.briefing_dialog, self.wrapup_dialog):
            if panel is not None and panel.isVisible():
                self.settings_dialog.show_near(panel.geometry(), prefer_center=True)
                shown = True
                break
        if not shown:
            self.settings_dialog.show_near(self.pet.geometry())
        if not was_open and not self._rebuilding_ui:
            self._say_tuple(self.dialogue.window_open("settings", self._ctx()), ms=4500)

    def apply_settings(self) -> None:
        """Apply saved settings immediately without restarting."""
        self.curfew_enabled = bool(self.settings["curfew_enabled"])
        self.act_curfew.blockSignals(True)
        self.act_curfew.setChecked(self.curfew_enabled)
        self.act_curfew.blockSignals(False)
        sound_on = bool(self.settings["sound_enabled"])
        self.audio.set_enabled(sound_on)
        self.audio.set_volumes(
            int(self.settings["voice_volume"]),
            int(self.settings["sfx_volume"]),
        )
        self.act_sound.blockSignals(True)
        self.act_sound.setChecked(sound_on)
        self.act_sound.blockSignals(False)
        if getattr(self, "act_tray_sound", None) is not None:
            self.act_tray_sound.blockSignals(True)
            self.act_tray_sound.setChecked(sound_on)
            self.act_tray_sound.blockSignals(False)
        self.particles.set_enabled(bool(self.settings["particles_enabled"]))
        self.set_manual_dnd(bool(self.settings["manual_dnd"]), announce=False)
        self.hotkeys.register(str(self.settings["quick_hotkey"]))
        self._update_tray_tooltip()
        self._sync_quick_cmd_badge()
        enabled_tabs = list(self.settings["enabled_tabs"])
        self.menu.set_enabled_tabs(enabled_tabs)
        self._sync_dialog_links()
        # Close any currently visible panel whose tab was just disabled
        enabled_set = set(enabled_tabs)
        self._rebuilding_ui = True
        try:
            for key, attr in (("events", "events_dialog"), ("tasks", "tasks_dialog"),
                              ("notes", "notes_dialog"), ("stats", "stats_dialog"),
                              ("finance", "finance_dialog"), ("timer", "timer_dialog")):
                dlg = getattr(self, attr)
                if key not in enabled_set and dlg is not None and dlg.isVisible():
                    dlg.on_closing()
                    dlg.hide()
        finally:
            self._rebuilding_ui = False
        self.pomodoro.focus_s = max(1, int(self.settings["focus_min"] * 60))
        self.pomodoro.rest_s = max(1, int(self.settings["rest_min"] * 60))
        if self.timer_dialog is not None:
            self.timer_dialog.refresh()
        new_pct = int(self.settings["ui_scale"])
        if abs(new_pct / 100.0 - self.ui_scale) > 0.001:
            self.set_ui_scale(new_pct, save=False, reopen_settings=True)
        self._sheets_fail_streak = 0
        self._sheets_last_pushed_rows = None
        self._configure_sheets_sync(trigger_initial=False)
        if self._sheets_sync_config().is_ready(APP_DIR):
            self._schedule_sheets_sync()
        self._last_curfew_msg = None
        self._check_curfew()

    def _toggle_sleep_mode(self) -> None:
        self.sleep_mode = not self.sleep_mode
        if self.sleep_mode:
            self.say("Sleep Mode is on. Reminders will still run—open SYSTEM again "
                     "whenever you want to wake me up.", "sleepy", 6000, particle="zzz")
        else:
            self.say("I'm awake! Sleep Mode is off.", "talking", 3000, particle="sparkle")
        self._restore_state()

    def _act_item(self) -> None:
        snap = self.monitor.snapshot()
        if snap is None:
            self.say("The psutil package isn't installed yet. Run: pip install psutil")
            return
        scr = self.screen_time.today_summary()
        text = (f"SYSTEM STATUS\nCPU : {snap.cpu:.0f}%\n"
                f"RAM : {snap.ram_percent:.0f}% ({snap.ram_used_gb:.1f} / {snap.ram_total_gb:.1f} GB)\n"
                f"SCREEN TIME : {scr['active_text']} today ({scr['continuous_text']} nonstop)")
        heavy = snap.cpu >= 85 or snap.ram_percent >= 90
        if heavy:
            text += "\nCPU/RAM usage is high. Try closing apps you don't need."
        self.say(text, "action" if heavy else "talking", ms=7000)

    # -- Pomodoro & Manual Timers (Persona 5 Timer + HUD + DND badge) ------------
    def _sync_timer_ui(self) -> None:
        had_hud = self.pet._hud is not None
        manual = self.manual_timers.snapshot()
        if self.pomodoro.running:
            is_rest = self.pomodoro.phase == PomodoroTimer.REST
            total_s = self.pomodoro.rest_s if is_rest else self.pomodoro.focus_s
            left_s = self.pomodoro.remaining()
            extra = f"+{len(manual)}" if manual else ""
            self.pet.set_hud(
                "REST" if is_rest else "FOCUS",
                fmt_mmss(left_s),
                progress=left_s / float(max(1, total_s)),
                accent="mint" if is_rest else "red",
                extra=extra,
            )
        elif manual:
            first = manual[0]
            extra = f"+{len(manual) - 1}" if len(manual) > 1 else ""
            self.pet.set_hud(
                "TIMER",
                fmt_hms(first["remaining_s"]),
                progress=first["remaining_s"] / float(max(1, first["total_s"])),
                accent="gold",
                extra=extra,
            )
        elif self.is_dnd_active():
            reason = self.dnd_reason()
            sub = "CALL" if reason == "meeting" else ("FULL" if reason == "fullscreen" else "QUIET")
            self.pet.set_hud("DND", sub, progress=1.0, accent="red")
        else:
            self.pet.clear_hud()

        if (self.pet._hud is not None) != had_hud:
            self.bubble.follow()

        if self.timer_dialog is not None:
            total_s = self.pomodoro.rest_s if self.pomodoro.phase == PomodoroTimer.REST else self.pomodoro.focus_s
            self.timer_dialog.update_focus(
                running=self.pomodoro.running,
                phase=self.pomodoro.phase,
                remaining_s=self.pomodoro.remaining(),
                total_s=total_s,
                completed=self.pomodoro.completed,
            )
            self.timer_dialog.update_manual_timers(manual)

    def _set_focus_stat(self, stat: str) -> None:
        if stat != self.settings["focus_stat"]:
            self.settings.set(focus_stat=stat)
        if self.timer_dialog is not None:
            self.timer_dialog._select_focus_stat(str(self.settings["focus_stat"] or "knowledge"), emit=False)

    def _start_focus_from_dialog(self, focus_min: int, rest_min: int, silent_say: bool = False) -> None:
        if focus_min != self.settings["focus_min"] or rest_min != self.settings["rest_min"]:
            self.settings.set(focus_min=focus_min, rest_min=rest_min)
        self.pomodoro.focus_s = max(1, int(focus_min * 60))
        self.pomodoro.rest_s = max(1, int(rest_min * 60))
        self._suppress_focus_start_say = bool(silent_say)
        try:
            self.pomodoro.start()
        finally:
            self._suppress_focus_start_say = False

    def _start_break_timer(self, minutes: int = 5, silent_say: bool = False) -> None:
        mins = max(1, int(minutes))
        self.screen_time.record_break()
        self.manual_timers.add(mins * 60, f"Break ({mins}m)")
        self._sync_timer_ui()
        if self.timer_dialog is not None:
            self.timer_dialog.update_screen_time(self.screen_time.today_summary())
        self._sync_briefing_view()
        self._sync_wrapup_view()
        self.pet.bump(0.85)
        self.particles.emit_hearts(3)
        if not silent_say:
            self.say(f"{mins}-minute break timer started! Step away, stretch, and hydrate!", "talking", ms=4800)

    def _add_manual_timer(self, total_s: int, label: str) -> None:
        item = self.manual_timers.add(total_s, label)
        self.pet.bump(0.8)
        self.say(f"Timer set: {clip(item.label, 38)} ({fmt_hms(item.total_s)}). "
                 "I'll remind you as soon as time's up!", "talking", ms=4500, particle="sparkle")

    def _cancel_manual_timer(self, timer_id: str) -> None:
        self.manual_timers.cancel(timer_id)

    def _on_manual_timer_fired(self, label: str, total_s: int) -> None:
        msg = f"Time's up! Your timer '{clip(label, 42)}' ({fmt_hms(total_s)}) just finished!"
        if self.pet.isVisible():
            self.say(msg, "action", ms=11000, context="timer_up", particle="alert")
            self.pet.bump(1.4)
        else:
            self._notify_tray("Timer Finished", msg)
        play_beep()

    def _on_pomo_tick(self, _phase: str, _left: int) -> None:
        self._sync_timer_ui()

    def _on_pomo_phase(self, phase: str) -> None:
        self._sync_timer_ui()
        if phase == PomodoroTimer.FOCUS:
            if getattr(self, "_suppress_focus_start_say", False):
                return
            t = fmt_mmss(self.pomodoro.focus_s)
            if self.pet.isVisible():
                self.say(random.choice(_FOCUS_START).format(t=t), "action", particle="sparkle")
        else:
            t = fmt_mmss(self.pomodoro.rest_s)
            self.screen_time.record_break()
            if self.timer_dialog is not None:
                self.timer_dialog.update_screen_time(self.screen_time.today_summary())
            self._sync_wrapup_view()
            if self.pet.isVisible():
                self.pet.bump(1.2)
            play_beep()
            focus_mins = max(1, int(round(self.pomodoro.focus_s / 60.0)))
            pts = 3 if focus_mins >= 45 else 2
            fstat = str(self.settings["focus_stat"] or "knowledge")
            awarded, old_lv, new_lv = 0, 1, 1
            if fstat in SOCIAL_STATS:
                awarded, old_lv, new_lv = self._award_auto_stat(
                    stat=fstat,
                    activity=f"Focus session ({focus_mins} min)",
                    points=pts,
                    source="focus",
                    announce=False,
                )
            if new_lv > old_lv:
                return
            msg = random.choice(_FOCUS_REST).format(t=t)
            if awarded > 0 and fstat in SOCIAL_STATS:
                msg += f" (+{awarded} {SOCIAL_STATS[fstat]['label']}!)"
            if self.pet.isVisible():
                self.say(msg, particle="music")
            else:
                self._notify_tray("Focus Complete", msg)

    def _on_pomo_finished(self) -> None:
        self._sync_timer_ui()
        self._sync_wrapup_view()
        if self.pet.isVisible():
            self.pet.bump(1.3)
        play_beep()
        n = self.pomodoro.completed
        word = "session" if n == 1 else "sessions"
        msg = (f"Break's over! That's {n} focus {word} completed today—you're doing awesome! "
               "Open TIMER whenever you're ready for another round.")
        if self.pet.isVisible():
            self.say(msg, "action")
        else:
            self._notify_tray("Break Finished", msg)

    def _stop_pomodoro(self) -> None:
        self.pomodoro.stop()
        self.say("Timer paused! Rest up if you need to—start a new session from TIMER whenever you're ready.")

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

    # -- curfew & Evening Wrap-up auto-trigger -----------------------------------
    def _check_curfew(self) -> None:
        self._ensure_state_healthy()
        now = datetime.now()
        active = self.curfew_enabled and in_window(now.time(), self.settings.bedtime, self.settings.wake)
        if active:
            if not self.curfew_active:
                self.curfew_active = True
                self._restore_state()
            interval_s = max(10, int(self.settings["curfew_interval_sec"]))
            if (not self.is_dnd_active() and self.pet.isVisible()
                    and (self._last_curfew_msg is None
                         or (now - self._last_curfew_msg).total_seconds() >= interval_s - 1.5)):
                self._last_curfew_msg = now
                self.say(self._bag_bedtime.next(), "sleepy", 10000, context="bedtime")
            return
        elif self.curfew_active:
            self.curfew_active = False
            self._last_curfew_msg = None
            self._restore_state()

        # Gentle Evening Wrap-up once per evening (never interrupts Focus sprints or DND)
        today_str = now.strftime("%Y-%m-%d")
        if (bool(self.settings["evening_wrapup"])
                and self.settings["last_wrapup_date"] != today_str
                and now.time() >= self.settings.wrapup_time
                and not self.sleep_mode
                and not self.is_dnd_active()
                and self.pet.isVisible()
                and not (self.pomodoro.running and self.pomodoro.phase == PomodoroTimer.FOCUS)
                and not (self.wrapup_dialog is not None and self.wrapup_dialog.isVisible())):
            idle_s = get_idle_seconds()
            if idle_s is None or idle_s < 300.0:
                self.settings.set(last_wrapup_date=today_str)
                self._open_wrapup(announce=True)

    def _attach_menu_sfx(self, menu: QMenu) -> None:
        """Play subtle Persona 5 hover & select SFX on QMenu interactions."""
        key = id(menu)
        self._menu_hover_action[key] = None

        def _on_hover(act) -> None:
            if act is not None and act.isEnabled() and not act.isSeparator():
                if self._menu_hover_action.get(key) is not act:
                    self._menu_hover_action[key] = act
                    play_ui("ui_hover")

        def _on_trigger(act) -> None:
            if act is not None and act.isEnabled():
                play_ui("select")

        def _on_hide() -> None:
            self._menu_hover_action[key] = None

        menu.hovered.connect(_on_hover)
        menu.triggered.connect(_on_trigger)
        menu.aboutToHide.connect(_on_hide)

    # -- menu klik kanan & dedicated rich system tray ---------------------------
    def _build_context_menu(self) -> None:
        m = QMenu()
        m.setStyleSheet(menu_qss(self.ui_scale))
        self.context_menu = m
        self._attach_menu_sfx(m)

        self.act_ctx_cmd = m.addAction("ASK & COMMAND...", self._open_quick_command)
        m.addAction("TALK WITH MORGANA...", lambda: self._start_talk())
        m.addAction("DAILY BRIEFING...", lambda: self._open_briefing(announce=True))
        m.addAction("EVENING WRAP-UP...", lambda: self._open_wrapup(announce=True))
        m.addSeparator()

        state_menu = m.addMenu("STATE")
        self._attach_menu_sfx(state_menu)
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
        self._attach_menu_sfx(expr_menu)
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
        self._attach_menu_sfx(size_menu)
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

        ui_menu = m.addMenu("UI SIZE")
        self._attach_menu_sfx(ui_menu)
        self._ui_size_group = QActionGroup(m)
        self._ui_size_group.setExclusive(True)
        labels = {85: "85% (COMPACT)", 100: "100% (NORMAL)", 115: "115% (LARGE)", 130: "130% (XLARGE)"}
        cur_pct = int(self.settings["ui_scale"])
        for pct in UI_SCALE_PRESETS:
            act = QAction(labels.get(pct, f"{pct}%"), m)
            act.setCheckable(True)
            act.setData(pct)
            act.setChecked(pct == cur_pct)
            self._ui_size_group.addAction(act)
            ui_menu.addAction(act)
        self._ui_size_group.triggered.connect(lambda a: self.set_ui_scale(int(a.data())))

        m.addSeparator()
        self.act_dnd = m.addAction("DO NOT DISTURB")
        self.act_dnd.setCheckable(True)
        self.act_dnd.setChecked(self.manual_dnd)
        self.act_dnd.toggled.connect(self.set_manual_dnd)

        self.act_top = m.addAction("ALWAYS ON TOP")
        self.act_top.setCheckable(True)
        self.act_top.setChecked(True)
        self.act_top.toggled.connect(self.set_always_on_top)

        self.act_sound = m.addAction("SOUND EFFECTS")
        self.act_sound.setCheckable(True)
        self.act_sound.setChecked(bool(self.settings["sound_enabled"]))
        self.act_sound.toggled.connect(self.set_sound_enabled)

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
        m.addAction("HIDE TO TRAY", lambda: self._set_pet_visible(False))
        m.addAction("RESET POSITION", self.reset_position)
        m.addSeparator()
        m.addAction("QUIT", self.quit)
        m.aboutToShow.connect(self._refresh_context_menu)

    def _refresh_context_menu(self) -> None:
        hk = str(self.settings["quick_hotkey"])
        self.act_ctx_cmd.setText(f"ASK & COMMAND...  ({hk})" if hk and hk != "Off" else "ASK & COMMAND...  (Ctrl+K)")
        self.act_dnd.blockSignals(True)
        self.act_dnd.setChecked(self.manual_dnd)
        self.act_dnd.blockSignals(False)
        if self._auto_dnd_reason and not self.manual_dnd:
            self.act_dnd.setText(f"DO NOT DISTURB  (Auto: {self._auto_dnd_reason.title()})")
        else:
            self.act_dnd.setText("DO NOT DISTURB")
        self.act_stop_pomo.setEnabled(self.pomodoro.running)
        self.act_autostart.setChecked(autostart.is_enabled())    # sinkron bila diubah dari luar

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.act_tray_cmd: QAction | None = None
        self.act_tray_toggle: QAction | None = None
        self.act_tray_dnd: QAction | None = None
        self.act_tray_sound: QAction | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        tm = QMenu()
        tm.setStyleSheet(menu_qss(self.ui_scale))
        self.tray_menu = tm
        self._attach_menu_sfx(tm)

        self.act_tray_cmd = tm.addAction("ASK & COMMAND...", self._open_quick_command)
        self.act_tray_toggle = tm.addAction("HIDE MORGANA TO TRAY", self._toggle_pet_visibility)
        self.act_tray_dnd = tm.addAction("DO NOT DISTURB")
        self.act_tray_dnd.setCheckable(True)
        self.act_tray_dnd.setChecked(self.manual_dnd)
        self.act_tray_dnd.toggled.connect(self.set_manual_dnd)
        tm.addSeparator()

        tm.addAction("DAILY BRIEFING...", lambda: self._open_briefing(announce=True))
        tm.addAction("EVENING WRAP-UP...", lambda: self._open_wrapup(announce=True))
        tm.addSeparator()

        tm.addAction("TASKS...", lambda: self._open_tasks(announce=False))
        tm.addAction("NOTES...", lambda: self._open_notes(announce=False))
        tm.addAction("EVENTS...", self._open_events)
        tm.addAction("TIMER & FOCUS...", self._open_timer)
        tm.addAction("SOCIAL STATS...", lambda: self._open_stats(announce=False))
        tm.addAction("FINANCE...", lambda: self._open_finance(announce=False))
        tm.addSeparator()

        self.act_tray_sound = tm.addAction("SOUND EFFECTS")
        self.act_tray_sound.setCheckable(True)
        self.act_tray_sound.setChecked(bool(self.settings["sound_enabled"]))
        self.act_tray_sound.toggled.connect(self.set_sound_enabled)
        tm.addAction("SETTINGS...", self._open_settings_deferred)
        tm.addSeparator()
        tm.addAction("QUIT", self.quit)
        tm.aboutToShow.connect(self._refresh_tray_menu)

        self.tray = QSystemTrayIcon(QIcon(self.sprites.get("neutral", 64, 1.0)), self)
        self.tray.setContextMenu(tm)
        self.tray.activated.connect(self._on_tray_activated)
        self._update_tray_tooltip()
        self.tray.show()

    def _refresh_tray_menu(self) -> None:
        if self.act_tray_cmd is not None:
            hk = str(self.settings["quick_hotkey"])
            self.act_tray_cmd.setText(f"ASK & COMMAND...  ({hk})" if hk and hk != "Off" else "ASK & COMMAND...  (Ctrl+K)")
        if self.act_tray_toggle is not None:
            self.act_tray_toggle.setText("HIDE MORGANA TO TRAY" if self.pet.isVisible() else "SHOW MORGANA")
        if self.act_tray_dnd is not None:
            self.act_tray_dnd.blockSignals(True)
            self.act_tray_dnd.setChecked(self.manual_dnd)
            self.act_tray_dnd.blockSignals(False)
            if self._auto_dnd_reason and not self.manual_dnd:
                self.act_tray_dnd.setText(f"DO NOT DISTURB  (Auto: {self._auto_dnd_reason.title()})")
            else:
                self.act_tray_dnd.setText("DO NOT DISTURB")
        if self.act_tray_sound is not None:
            self.act_tray_sound.blockSignals(True)
            self.act_tray_sound.setChecked(bool(self.settings["sound_enabled"]))
            self.act_tray_sound.blockSignals(False)

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:          # left-click toggles Show / Hide
            self._toggle_pet_visibility()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:    # middle-click opens Ask & Command
            self._open_quick_command()

    # -- aksi menu klik kanan ----------------------------------------------------
    def _toggle_autostart(self, checked: bool) -> None:
        ok, info = autostart.set_enabled(checked, SCRIPT_PATH)
        self.act_autostart.blockSignals(True)
        self.act_autostart.setChecked(autostart.is_enabled())
        self.act_autostart.blockSignals(False)
        if ok:
            self.say("Start on boot is enabled! I'll show up automatically next time you turn on your PC." if checked
                     else "Start on boot is disabled.", ms=5000)
        else:
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
        self.particles.follow()
        self.choices.follow()

    def set_ui_scale(self, pct: int, save: bool = True, reopen_settings: bool = False) -> None:
        """Scale dialogue textbox, radial menu, popup menus, and dialog windows live."""
        pct = max(75, min(150, int(pct)))
        if save:
            self.settings.set(ui_scale=pct)
        self.ui_scale = pct / 100.0
        for act in self._ui_size_group.actions():
            act.blockSignals(True)
            act.setChecked(int(act.data()) == pct)
            act.blockSignals(False)

        self.pet.set_ui_scale(self.ui_scale)
        self.bubble.set_ui_scale(self.ui_scale)
        self.particles.set_ui_scale(self.ui_scale)
        self.choices.set_ui_scale(self.ui_scale)
        self.menu.set_ui_scale(self.ui_scale)
        qss = menu_qss(self.ui_scale)
        self.system_menu.setStyleSheet(qss)
        self.context_menu.setStyleSheet(qss)
        if getattr(self, "tray_menu", None) is not None:
            self.tray_menu.setStyleSheet(qss)

        # Rebuild cached dialogs at the new scale (suppress open/close chatter during rebuild)
        self._rebuilding_ui = True
        try:
            for attr, opener, closing in (
                ("tasks_dialog", self._open_tasks, False),
                ("notes_dialog", self._open_notes, True),
                ("events_dialog", self._open_events, False),
                ("stats_dialog", self._open_stats, False),
                ("finance_dialog", self._open_finance, True),
                ("timer_dialog", self._open_timer, False),
                ("briefing_dialog", lambda: self._open_briefing(announce=False), False),
                ("wrapup_dialog", lambda: self._open_wrapup(announce=False), False),
                ("quick_cmd_dialog", self._open_quick_command, False),
            ):
                dlg = getattr(self, attr)
                if dlg is not None:
                    was = dlg.isVisible()
                    if closing:
                        dlg.on_closing()
                    dlg.hide()
                    dlg.deleteLater()
                    setattr(self, attr, None)
                    if was:
                        opener()
            if self.settings_dialog is not None:
                was = self.settings_dialog.isVisible()
                if reopen_settings or was:
                    old = self.settings_dialog
                    self.settings_dialog = None
                    def _reopen(o=old) -> None:
                        self._rebuilding_ui = True
                        try:
                            o.hide()
                            o.deleteLater()
                            self._open_settings()
                        finally:
                            self._rebuilding_ui = False
                    QTimer.singleShot(0, _reopen)
        finally:
            self._rebuilding_ui = False

    def set_always_on_top(self, on: bool) -> None:
        on = bool(on)
        changed = (on != self.on_top)
        self.on_top = on
        self.act_top.blockSignals(True)
        self.act_top.setChecked(on)
        self.act_top.blockSignals(False)
        if not changed:
            return
        was_visible = self.pet.isVisible()
        if self.choices.is_open:
            self.choices.close_choices(silent=True)
        if self.bubble.isVisible():
            self.bubble.dismiss()
        for w in (self.pet, self.bubble):
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.menu.set_on_top(on)
        self.particles.set_on_top(on)
        self.choices.set_on_top(on)
        if was_visible:
            self.pet.show()
        self._ensure_state_healthy()
        for dlg in (self.tasks_dialog, self.notes_dialog, self.events_dialog,
                    self.stats_dialog, self.finance_dialog, self.timer_dialog,
                    self.settings_dialog, self.briefing_dialog, self.wrapup_dialog,
                    self.quick_cmd_dialog):
            if dlg is not None:
                dlg.set_on_top(on)

    def set_curfew_enabled(self, on: bool) -> None:
        self.curfew_enabled = on
        self.settings.set(curfew_enabled=on)
        self._check_curfew()

    def set_sound_enabled(self, on: bool) -> None:
        on = bool(on)
        self.settings.set(sound_enabled=on)
        self.audio.set_enabled(on)
        self.act_sound.blockSignals(True)
        self.act_sound.setChecked(on)
        self.act_sound.blockSignals(False)
        if getattr(self, "act_tray_sound", None) is not None:
            self.act_tray_sound.blockSignals(True)
            self.act_tray_sound.setChecked(on)
            self.act_tray_sound.blockSignals(False)

    def reset_position(self) -> None:
        screen = QGuiApplication.screenAt(self.pet.geometry().center()) or QGuiApplication.primaryScreen()
        self._move_to_corner(screen)

    def _move_to_corner(self, screen) -> None:
        area = screen.availableGeometry()
        bubble_reserve = min(area.height() // 2, int(round(195 * self.ui_scale)) + 20)
        right_reserve = min(area.width() // 3, max(48, int(round(180 * self.ui_scale))))
        x = max(area.left() + 24, area.right() - self.pet.width() - right_reserve)
        y = max(area.top() + 24, area.bottom() - self.pet.height() - bubble_reserve)
        self.pet.move(x, y)


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
    load_fonts()
    app.setApplicationName("Morgana Companion")
    ico_path = sprites.SPRITE_DIR / "morgana.ico"
    if ico_path.is_file():
        app.setWindowIcon(QIcon(str(ico_path)))
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