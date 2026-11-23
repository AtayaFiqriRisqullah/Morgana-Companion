# -*- coding: utf-8 -*-
"""Mesin animasi untuk UI yang terasa "cair".

Kenapa pegas (spring) dan bukan animasi dengan durasi tetap?
  * Bisa diinterupsi kapan saja (menu dibuka lalu langsung ditutup) tanpa lompatan:
    posisi DAN kecepatan terakhir diteruskan.
  * Overshoot / pantulan alami, cukup atur kekakuan (stiffness) dan redaman (damping).
  * Memakai delta-time, jadi kecepatan animasi sama di 30 FPS maupun 60 FPS.

Pemakaian umum:
    s = Spring(0.0, stiffness=230, damping=15)   # kurang teredam -> memantul
    s.target = 1.0
    while s.step(dt): ...                        # step() False = sudah diam
"""
from __future__ import annotations

import logging
import math
import time

from PyQt6.QtCore import QObject, Qt, QTimer


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def critical_damping(stiffness: float) -> float:
    """Redaman kritis: menuju target secepat mungkin tanpa overshoot."""
    return 2.0 * math.sqrt(stiffness)


class Spring:
    """Pegas teredam 1 dimensi (integrasi semi-implicit Euler dengan sub-langkah)."""

    __slots__ = ("value", "target", "velocity", "stiffness", "damping")

    def __init__(self, value: float = 0.0, stiffness: float = 200.0,
                 damping: float | None = None) -> None:
        self.value = value
        self.target = value
        self.velocity = 0.0
        self.stiffness = stiffness
        self.damping = critical_damping(stiffness) if damping is None else damping

    def configure(self, stiffness: float, damping: float | None = None) -> None:
        self.stiffness = stiffness
        self.damping = critical_damping(stiffness) if damping is None else damping

    def snap(self, value: float) -> None:
        """Loncat langsung ke `value` dan diam (target ikut diubah)."""
        self.value = self.target = value
        self.velocity = 0.0

    @property
    def at_rest(self) -> bool:
        return self.velocity == 0.0 and self.value == self.target

    def step(self, dt: float) -> bool:
        """Majukan simulasi. Mengembalikan True selama masih bergerak."""
        if self.at_rest:
            return False
        steps = max(1, int(math.ceil(dt * 120.0)))      # sub-langkah <= 1/120 detik
        h = dt / steps
        for _ in range(steps):
            accel = self.stiffness * (self.target - self.value) - self.damping * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h
        if abs(self.velocity) < 0.02 and abs(self.target - self.value) < 0.0015:
            self.value = self.target
            self.velocity = 0.0
            return False
        return True


class FrameTicker(QObject):
    """Timer ~60 FPS yang hanya berjalan saat dibutuhkan.

    `on_frame(dt)` dipanggil tiap frame dengan dt dalam detik dan harus mengembalikan
    True selama masih ada yang beranimasi; saat False, timer berhenti sendiri (hemat CPU).
    """

    def __init__(self, on_frame, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_frame = on_frame
        self._last = 0.0
        self._interval = 16
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._interval)
        self._timer.timeout.connect(self._tick)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def set_interval(self, ms: int) -> None:
        if ms != self._interval:
            self._interval = ms
            self._timer.setInterval(ms)

    def start(self) -> None:
        if not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._last, 0.05)               # batasi lonjakan (mis. setelah laptop sleep)
        self._last = now
        try:
            keep_going = self._on_frame(dt)
        except Exception:                              # jangan spam log 60x per detik
            logging.getLogger("pet").exception("Animasi dihentikan karena galat")
            keep_going = False
        if not keep_going:
            self._timer.stop()