# -*- coding: utf-8 -*-
"""Persona 5 micro-particle engine (music notes, gold stars, sparkles, hearts, alerts, Zzz).

Used both by:
  * `ParticleOverlay` — transparent click-through overlay tethered to Morgana (0% CPU when idle).
  * `SocialPentagonWidget` — in-chart Persona 5 Royal music notes & golden star bursts on point gain
    and Rank Up.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

from motion import FrameTicker, clamp
from theme import BLACK, GOLD, RED, WHITE, p5_font, star_polygon

# Persona 5 Royal vibrant sparkle palette for Rank Up celebrations
P5_STAR_COLORS: tuple[QColor, ...] = (
    QColor("#FFE44D"),  # P5 Social Stat bright gold
    QColor("#F5C400"),  # Morgana scarf gold
    QColor("#FF2A3B"),  # Persona crimson
    QColor("#43E0FF"),  # Electric cyan sparkle
    QColor("#FF79C6"),  # Confidant pink sparkle
    QColor("#FFFFFF"),  # Pure flash white
)

P5_NOTE_GOLD = QColor("#FFE135")
P5_NOTE_GOLD_HI = QColor("#FFF8B8")
P5_HEART_RED = QColor("#FF3355")
P5_HEART_PINK = QColor("#FF6E99")
P5_CYAN = QColor("#53D8FF")


@dataclass
class Particle:
    kind: str                      # note | note2 | star | sparkle | heart | exclaim | question | zzz | anger | ring
    x: float
    y: float
    vx: float
    vy: float
    size: float
    life: float = 0.0
    max_life: float = 1.15
    rot: float = 0.0
    vrot: float = 0.0
    gravity: float = 0.0
    drag: float = 0.0
    wave_amp: float = 0.0
    wave_freq: float = 5.0
    phase: float = 0.0
    delay: float = 0.0
    color: QColor = field(default_factory=lambda: QColor(P5_NOTE_GOLD))
    outline: QColor = field(default_factory=lambda: QColor(BLACK))

    def step(self, dt: float) -> bool:
        if self.delay > 0.0:
            self.delay = max(0.0, self.delay - dt)
            return True
        self.life += dt
        if self.life >= self.max_life:
            return False
        if self.drag > 0.0:
            factor = max(0.0, 1.0 - self.drag * dt)
            self.vx *= factor
            self.vy *= factor
        self.vy += self.gravity * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.rot += self.vrot * dt
        return True

    @property
    def progress(self) -> float:
        return clamp(self.life / max(0.001, self.max_life))


def _diamond_star(cx: float, cy: float, r_out: float, r_in: float) -> QPolygonF:
    """4-point Persona 5 twinkle sparkle."""
    pts = []
    for i in range(8):
        r = r_out if i % 2 == 0 else r_in
        a = i * math.pi / 4.0 - math.pi / 2.0
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


def _draw_music_note(p: QPainter, size: float, color: QColor, outline: QColor, double: bool = False) -> None:
    """Crisp vector Persona 5 golden music note (♪ or ♫) with heavy ink outline & specular glint."""
    s = size
    out_w = max(1.6, s * 0.14)

    def _note_head(cx: float, cy: float, rx: float, ry: float) -> None:
        p.save()
        p.translate(cx, cy)
        p.rotate(-22.0)
        p.setPen(QPen(outline, out_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(color)
        p.drawEllipse(QPointF(0.0, 0.0), rx, ry)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(P5_NOTE_GOLD_HI)
        p.drawEllipse(QPointF(-rx * 0.28, -ry * 0.25), rx * 0.34, ry * 0.26)
        p.restore()

    if not double:
        # Stem + curved Persona flag
        stem_x = s * 0.20
        top_y = -s * 0.54
        bot_y = s * 0.28
        path = QPainterPath()
        path.moveTo(stem_x, bot_y)
        path.lineTo(stem_x, top_y)
        path.cubicTo(stem_x + s * 0.42, top_y + s * 0.08,
                     stem_x + s * 0.44, top_y + s * 0.42,
                     stem_x + s * 0.16, top_y + s * 0.56)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(outline, max(3.6, s * 0.28), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        p.setPen(QPen(color, max(1.8, s * 0.15), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)
        _note_head(-s * 0.08, s * 0.30, s * 0.32, s * 0.23)
    else:
        # Beamed eighth notes (♫)
        x1, x2 = -s * 0.14, s * 0.42
        y1_b, y2_b = s * 0.32, s * 0.20
        y1_t, y2_t = -s * 0.44, -s * 0.56

        # Thick black outline pass
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(outline, max(3.4, s * 0.26), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(x1, y1_b), QPointF(x1, y1_t))
        p.drawLine(QPointF(x2, y2_b), QPointF(x2, y2_t))
        beam = QPolygonF([
            QPointF(x1 - s * 0.04, y1_t - s * 0.06),
            QPointF(x2 + s * 0.04, y2_t - s * 0.06),
            QPointF(x2 + s * 0.04, y2_t + s * 0.14),
            QPointF(x1 - s * 0.04, y1_t + s * 0.14),
        ])
        p.setPen(QPen(outline, out_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(color)
        p.drawPolygon(beam)

        # Gold stem cores
        p.setPen(QPen(color, max(1.6, s * 0.13), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x1, y1_b), QPointF(x1, y1_t))
        p.drawLine(QPointF(x2, y2_b), QPointF(x2, y2_t))

        _note_head(x1 - s * 0.22, y1_b + s * 0.02, s * 0.25, s * 0.18)
        _note_head(x2 - s * 0.22, y2_b + s * 0.02, s * 0.25, s * 0.18)


def _draw_heart(p: QPainter, size: float, color: QColor, outline: QColor) -> None:
    s = size * 0.52
    path = QPainterPath()
    path.moveTo(0.0, s * 0.85)
    path.cubicTo(-s * 1.15, s * 0.15, -s * 1.10, -s * 0.88, 0.0, -s * 0.30)
    path.cubicTo(s * 1.10, -s * 0.88, s * 1.15, s * 0.15, 0.0, s * 0.85)
    p.setPen(QPen(outline, max(1.6, size * 0.11), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(color)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 175))
    p.drawEllipse(QPointF(-s * 0.38, -s * 0.28), s * 0.18, s * 0.12)


def _draw_comic_glyph(p: QPainter, text: str, size: float, color: QColor, outline: QColor) -> None:
    f = p5_font(max(12, int(round(size * 1.15))), italic=True)
    path = QPainterPath()
    p.setFont(f)
    fm = p.fontMetrics()
    w = fm.horizontalAdvance(text)
    h = fm.ascent()
    path.addText(QPointF(-w * 0.5, h * 0.38), f, text)
    p.setPen(QPen(outline, max(3.2, size * 0.20), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _draw_anger(p: QPainter, size: float, color: QColor, outline: QColor) -> None:
    """Anime/Persona comic anger vein symbol."""
    r = size * 0.46
    arms = (
        ((-r, -r * 0.22), (-r * 0.22, -r * 0.22), (-r * 0.22, -r)),
        ((r, -r * 0.22), (r * 0.22, -r * 0.22), (r * 0.22, -r)),
        ((-r, r * 0.22), (-r * 0.22, r * 0.22), (-r * 0.22, r)),
        ((r, r * 0.22), (r * 0.22, r * 0.22), (r * 0.22, r)),
    )
    for stroke_c, w in ((outline, max(3.8, size * 0.24)), (color, max(1.8, size * 0.12))):
        p.setPen(QPen(stroke_c, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for a, b, c in arms:
            path = QPainterPath()
            path.moveTo(QPointF(*a))
            path.quadTo(QPointF(*b), QPointF(*c))
            p.drawPath(path)


def draw_particle(p: QPainter, pt: Particle) -> None:
    """Render one Persona 5 micro-particle on `p`."""
    if pt.delay > 0.0:
        return
    t = pt.progress
    # Crisp pop-in (first 16%) and smooth fade-out (last 30%)
    if t < 0.16:
        sc = 0.25 + 0.90 * (t / 0.16)
        alpha = clamp(t / 0.10)
    elif t < 0.28:
        sc = 1.15 - 0.15 * ((t - 0.16) / 0.12)
        alpha = 1.0
    else:
        sc = 1.0 - 0.12 * ((t - 0.28) / 0.72)
        alpha = 1.0 if t < 0.70 else clamp((1.0 - t) / 0.30)

    if alpha <= 0.01:
        return

    wx = math.sin(pt.life * pt.wave_freq + pt.phase) * pt.wave_amp
    p.save()
    p.setOpacity(p.opacity() * alpha)
    p.translate(pt.x + wx, pt.y)

    if pt.kind == "ring":
        rad = pt.size * (0.35 + 1.35 * t)
        p.rotate(pt.rot)
        poly = star_polygon(0.0, 0.0, rad, rad * 0.52, 5)
        c = QColor(pt.color)
        c.setAlpha(int(230 * (1.0 - t)))
        fill = QColor(pt.color)
        fill.setAlpha(int(55 * (1.0 - t)))
        p.setPen(QPen(c, max(1.8, 3.6 * (1.0 - t * 0.5))))
        p.setBrush(fill)
        p.drawPolygon(poly)
        p.restore()
        return

    p.rotate(pt.rot)
    p.scale(sc, sc)

    if pt.kind == "note":
        _draw_music_note(p, pt.size, pt.color, pt.outline, double=False)
    elif pt.kind == "note2":
        _draw_music_note(p, pt.size, pt.color, pt.outline, double=True)
    elif pt.kind == "star":
        poly = star_polygon(0.0, 0.0, pt.size * 0.58, pt.size * 0.25, 5)
        p.setPen(QPen(pt.outline, max(1.4, pt.size * 0.11), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.MiterJoin))
        p.setBrush(pt.color)
        p.drawPolygon(poly)
    elif pt.kind == "sparkle":
        twinkle = 0.78 + 0.32 * math.sin(pt.life * 16.0 + pt.phase)
        r = pt.size * 0.55 * twinkle
        poly = _diamond_star(0.0, 0.0, r, r * 0.28)
        p.setPen(QPen(pt.outline, max(1.0, pt.size * 0.08)))
        p.setBrush(pt.color)
        p.drawPolygon(poly)
    elif pt.kind == "heart":
        _draw_heart(p, pt.size, pt.color, pt.outline)
    elif pt.kind == "exclaim":
        _draw_comic_glyph(p, "!", pt.size, pt.color, pt.outline)
    elif pt.kind == "question":
        _draw_comic_glyph(p, "?", pt.size, pt.color, pt.outline)
    elif pt.kind == "zzz":
        _draw_comic_glyph(p, "Z", pt.size, pt.color, pt.outline)
    elif pt.kind == "anger":
        _draw_anger(p, pt.size, pt.color, pt.outline)
    p.restore()


# ---------------------------------------------------------------------------
# Click-through Micro-Particle Overlay tethered to Morgana
# ---------------------------------------------------------------------------
class ParticleOverlay(QWidget):
    """Lightweight Persona 5 micro-particle overlay following Morgana (0% CPU when idle)."""

    BASE_W = 420
    BASE_H = 340

    def __init__(self, pet_widget: QWidget) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._pet = pet_widget
        self._enabled = True
        self._scale = 1.0
        self._particles: list[Particle] = []
        self._ticker = FrameTicker(self._frame, self)
        self._resize_canvas()

    def _resize_canvas(self) -> None:
        sc = self._scale
        self.setFixedSize(int(round(self.BASE_W * sc)), int(round(self.BASE_H * sc)))
        self.follow()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if not self._enabled:
            self._particles.clear()
            self._ticker.stop()
            self.hide()

    def set_ui_scale(self, scale: float) -> None:
        self._scale = max(0.75, min(1.5, float(scale)))
        self._resize_canvas()

    def set_on_top(self, on: bool) -> None:
        was = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was and self._particles:
            self.show()

    def follow(self) -> None:
        if hasattr(self._pet, "sprite_center"):
            c = self._pet.sprite_center()
        else:
            c = self._pet.geometry().center()
        self.move(QPoint(c.x() - self.width() // 2, c.y() - self.height() // 2))

    def _anchor(self) -> tuple[float, float, float]:
        """Return `(cx, head_y, scale)` inside overlay local coordinates."""
        cx = self.width() * 0.5
        sp = float(getattr(self._pet, "sprite_size", 180))
        head_y = self.height() * 0.5 - sp * 0.34
        return cx, head_y, self._scale

    def _spawn(self, items: list[Particle]) -> None:
        if not self._enabled or not items:
            return
        self._particles.extend(items)
        # Hard cap for smooth 60 FPS even during rapid bursts
        if len(self._particles) > 90:
            self._particles = self._particles[-90:]
        self.follow()
        if not self.isVisible():
            self.show()
            self.raise_()
        self._ticker.start()

    # -- Semantic Emitters ---------------------------------------------------
    def emit_stat_gain(self, points: int = 2, leveled_up: bool = False) -> None:
        """Persona 5 Royal golden music notes (1-3 notes) + sparkling colored stars."""
        cx, hy, sc = self._anchor()
        note_cnt = 3 if points >= 4 else (2 if points >= 2 else 1)
        out: list[Particle] = []

        for i in range(note_cnt):
            spread = (i - (note_cnt - 1) / 2.0) * 34.0 * sc
            out.append(Particle(
                kind="note2" if (points >= 3 and i == note_cnt - 1) else "note",
                x=cx + spread + random.uniform(-6, 6) * sc,
                y=hy + random.uniform(-6, 8) * sc,
                vx=spread * 0.45 + random.uniform(-12, 12) * sc,
                vy=-68.0 * sc - random.uniform(6, 22) * sc,
                size=(23.0 if points >= 3 else 20.0) * sc,
                max_life=1.55 if leveled_up else 1.25,
                rot=random.uniform(-14, 14),
                vrot=random.uniform(-18, 18),
                wave_amp=7.0 * sc,
                wave_freq=5.8,
                phase=i * 1.4,
                delay=i * 0.14,
                color=P5_NOTE_GOLD,
                outline=BLACK,
            ))

        # Colored sparkles accompanying P5R music notes (persist longer when ranking up!)
        spark_cnt = 10 if leveled_up else (6 if points >= 3 else 4)
        for i in range(spark_cnt):
            ang = random.uniform(-math.pi * 0.92, -math.pi * 0.08)
            spd = random.uniform(45, 115) * sc
            out.append(Particle(
                kind="star" if (leveled_up and i % 2 == 0) else "sparkle",
                x=cx + random.uniform(-36, 36) * sc,
                y=hy + random.uniform(-14, 18) * sc,
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd - 20.0 * sc,
                size=random.uniform(11, 17 if leveled_up else 14) * sc,
                max_life=1.45 if leveled_up else 0.95,
                rot=random.uniform(0, 360),
                vrot=random.uniform(-140, 140),
                drag=1.1,
                delay=0.05 + i * 0.04,
                color=P5_STAR_COLORS[i % len(P5_STAR_COLORS)],
                outline=BLACK,
            ))
        self._spawn(out)

    def emit_rank_up(self) -> None:
        """Full Persona 5 golden starburst + floating music notes celebration."""
        cx, hy, sc = self._anchor()
        cy = self.height() * 0.5
        out: list[Particle] = [
            Particle(
                kind="ring", x=cx, y=cy, vx=0.0, vy=0.0,
                size=88.0 * sc, max_life=0.85, rot=10.0, vrot=45.0,
                color=GOLD, outline=WHITE,
            ),
            Particle(
                kind="ring", x=cx, y=cy, vx=0.0, vy=0.0,
                size=66.0 * sc, max_life=0.75, rot=-18.0, vrot=-55.0,
                delay=0.12, color=WHITE, outline=GOLD,
            ),
        ]
        # 14 radiating golden & colored P5 stars
        for i in range(14):
            ang = (2.0 * math.pi * i / 14.0) + random.uniform(-0.15, 0.15)
            spd = random.uniform(95, 175) * sc
            out.append(Particle(
                kind="star" if i % 3 != 2 else "sparkle",
                x=cx + math.cos(ang) * 16 * sc,
                y=cy + math.sin(ang) * 16 * sc,
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd - 24.0 * sc,
                size=random.uniform(14, 24) * sc,
                max_life=random.uniform(1.15, 1.65),
                rot=random.uniform(0, 360),
                vrot=random.uniform(-220, 220),
                gravity=75.0 * sc,
                drag=1.4,
                delay=random.uniform(0.0, 0.14),
                color=P5_STAR_COLORS[i % len(P5_STAR_COLORS)],
                outline=BLACK,
            ))
        # 4 rising golden music notes
        for i in range(4):
            sx = cx + (i - 1.5) * 38.0 * sc
            out.append(Particle(
                kind="note2" if i % 2 == 1 else "note",
                x=sx, y=hy + 12.0 * sc,
                vx=(i - 1.5) * 18.0 * sc,
                vy=-82.0 * sc - i * 8.0 * sc,
                size=24.0 * sc,
                max_life=1.75,
                rot=random.uniform(-12, 12),
                vrot=random.uniform(-16, 16),
                wave_amp=9.0 * sc,
                wave_freq=6.2,
                phase=i * 1.1,
                delay=0.06 + i * 0.11,
                color=P5_NOTE_GOLD,
                outline=BLACK,
            ))
        self._spawn(out)

    def emit_hearts(self, count: int = 4) -> None:
        cx, hy, sc = self._anchor()
        out = []
        for i in range(max(1, count)):
            out.append(Particle(
                kind="heart",
                x=cx + random.uniform(-34, 34) * sc,
                y=hy + random.uniform(-6, 16) * sc,
                vx=random.uniform(-22, 22) * sc,
                vy=-random.uniform(52, 82) * sc,
                size=random.uniform(15, 22) * sc,
                max_life=random.uniform(1.1, 1.45),
                rot=random.uniform(-16, 16),
                vrot=random.uniform(-20, 20),
                wave_amp=8.0 * sc,
                wave_freq=5.2,
                phase=i * 1.3,
                delay=i * 0.10,
                color=P5_HEART_RED if i % 2 == 0 else P5_HEART_PINK,
                outline=WHITE,
            ))
        self._spawn(out)

    def emit_music(self, count: int = 3) -> None:
        cx, hy, sc = self._anchor()
        out = []
        for i in range(max(1, count)):
            out.append(Particle(
                kind="note2" if i % 2 == 1 else "note",
                x=cx + (i - (count - 1) / 2.0) * 30.0 * sc,
                y=hy + random.uniform(-4, 10) * sc,
                vx=random.uniform(-14, 14) * sc,
                vy=-60.0 * sc - random.uniform(5, 18) * sc,
                size=random.uniform(18, 22) * sc,
                max_life=1.25,
                rot=random.uniform(-12, 12),
                wave_amp=7.0 * sc,
                wave_freq=5.6,
                phase=i * 1.5,
                delay=i * 0.13,
                color=P5_NOTE_GOLD,
                outline=BLACK,
            ))
        self._spawn(out)

    def emit_alert(self) -> None:
        cx, hy, sc = self._anchor()
        self._spawn([
            Particle(
                kind="exclaim", x=cx + 22 * sc, y=hy - 4 * sc,
                vx=16 * sc, vy=-46 * sc, size=28 * sc, max_life=0.95,
                rot=10.0, vrot=8.0, drag=1.8, color=RED, outline=WHITE,
            ),
            Particle(
                kind="sparkle", x=cx + 38 * sc, y=hy - 12 * sc,
                vx=36 * sc, vy=-48 * sc, size=14 * sc, max_life=0.75,
                vrot=160.0, drag=1.5, color=GOLD, outline=BLACK,
            ),
        ])

    def emit_question(self) -> None:
        cx, hy, sc = self._anchor()
        self._spawn([
            Particle(
                kind="question", x=cx + 24 * sc, y=hy - 2 * sc,
                vx=12 * sc, vy=-40 * sc, size=26 * sc, max_life=1.1,
                rot=8.0, wave_amp=5.0 * sc, wave_freq=5.0, color=P5_CYAN, outline=BLACK,
            ),
        ])

    def emit_anger(self) -> None:
        cx, hy, sc = self._anchor()
        self._spawn([
            Particle(
                kind="anger", x=cx + 30 * sc, y=hy - 2 * sc,
                vx=10 * sc, vy=-26 * sc, size=24 * sc, max_life=1.0,
                rot=12.0, vrot=-15.0, drag=1.6, color=RED, outline=WHITE,
            ),
            Particle(
                kind="exclaim", x=cx - 28 * sc, y=hy,
                vx=-14 * sc, vy=-36 * sc, size=22 * sc, max_life=0.9,
                rot=-12.0, drag=1.6, delay=0.06, color=GOLD, outline=BLACK,
            ),
        ])

    def emit_zzz(self) -> None:
        cx, hy, sc = self._anchor()
        out = []
        for i in range(3):
            out.append(Particle(
                kind="zzz",
                x=cx + (18 + i * 11) * sc,
                y=hy + (6 - i * 8) * sc,
                vx=14 * sc,
                vy=-30 * sc,
                size=(14 + i * 4) * sc,
                max_life=1.6,
                rot=-8.0 + i * 5.0,
                wave_amp=5.0 * sc,
                wave_freq=3.4,
                phase=i * 0.9,
                delay=i * 0.28,
                color=WHITE if i % 2 == 0 else P5_CYAN,
                outline=BLACK,
            ))
        self._spawn(out)

    def emit_sparkle(self, count: int = 5) -> None:
        cx, hy, sc = self._anchor()
        out = []
        for i in range(max(1, count)):
            ang = random.uniform(-math.pi, 0.0)
            spd = random.uniform(38, 86) * sc
            out.append(Particle(
                kind="star" if i % 2 == 0 else "sparkle",
                x=cx + random.uniform(-34, 34) * sc,
                y=hy + random.uniform(-10, 14) * sc,
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd - 16 * sc,
                size=random.uniform(12, 18) * sc,
                max_life=random.uniform(0.85, 1.25),
                rot=random.uniform(0, 360),
                vrot=random.uniform(-150, 150),
                drag=1.2,
                delay=i * 0.05,
                color=P5_STAR_COLORS[i % len(P5_STAR_COLORS)],
                outline=BLACK,
            ))
        self._spawn(out)

    def emit_by_name(self, name: str) -> None:
        key = (name or "").strip().lower()
        if not key:
            return
        if key == "heart":
            self.emit_hearts()
        elif key == "music":
            self.emit_music()
        elif key == "alert":
            self.emit_alert()
        elif key == "question":
            self.emit_question()
        elif key == "anger":
            self.emit_anger()
        elif key == "zzz":
            self.emit_zzz()
        elif key == "sparkle":
            self.emit_sparkle()
        elif key == "rank_up":
            self.emit_rank_up()

    # -- Frame loop & Paint --------------------------------------------------
    def _frame(self, dt: float) -> bool:
        if not self._particles:
            self.hide()
            return False
        alive = [pt for pt in self._particles if pt.step(dt)]
        self._particles = alive
        if not alive:
            self.hide()
            return False
        self.update()
        return True

    def paintEvent(self, _event) -> None:
        if not self._particles:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for pt in self._particles:
            draw_particle(p, pt)
        p.end()
