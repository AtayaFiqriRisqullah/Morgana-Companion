# -*- coding: utf-8 -*-
"""Sprite Morgana dari folder `Morgana/` (butuh PyQt6).

Tiap ekspresi punya:
  b003_XXX_00.png        badan penuh (mata terbuka, mulut sesuai pose)
  b003_XXX_00_e1.png     patch mata TERTUTUP        (opsional)
  b003_XXX_00_e2.png     patch mata SETENGAH tertutup (opsional)
  b003_XXX_00_m1/2/3.png patch mulut: tertutup / setengah / terbuka (opsional)

Patch hanya potongan wajah, jadi harus ditempel di koordinat yang tepat pada gambar
penuh. Koordinat di bawah ini dihitung dengan template matching terhadap gambar dasar,
dalam piksel gambar sumber. Ada tiga ukuran/pose:

  A  768 px   pose menghadap depan (000, 001, 002, 008, 011)
  B  768 px   pose kepala besar (003, 005, 007)
  C  1536 px  pose menoleh (004, 010)

Semua pose dinormalisasi ke kanvas 768 px, lalu dipotong ke "panggung" yang sama
(STAGE) supaya badan tidak melompat saat ekspresi berganti.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap

import app_paths

SPRITE_DIR = app_paths.resource_path("Morgana")

# Indeks bingkai. eye: 0 = terbuka (gambar dasar), 1 = setengah, 2 = tertutup.
#                 mouth: 0 = gambar dasar, 1 = tertutup, 2 = setengah, 3 = terbuka.
EYE_OPEN, EYE_HALF, EYE_CLOSED = 0, 1, 2
MOUTH_BASE, MOUTH_CLOSED, MOUTH_HALF, MOUTH_OPEN = 0, 1, 2, 3
_EYE_FILE = {EYE_HALF: "e2", EYE_CLOSED: "e1"}
_MOUTH_FILE = {MOUTH_CLOSED: "m1", MOUTH_HALF: "m2", MOUTH_OPEN: "m3"}

# Area gabungan semua pose pada kanvas 768 px (x0, y0, x1, y1) + sedikit margin.
STAGE = (81, 83, 674, 724)


@dataclass(frozen=True)
class Layout:
    eye: tuple[int, int]     # pojok kiri-atas patch mata pada gambar sumber
    mouth: tuple[int, int]   # pojok kiri-atas patch mulut


LAYOUTS = {
    "A": Layout(eye=(194, 176), mouth=(207, 451)),
    "B": Layout(eye=(151, 213), mouth=(182, 470)),
    "C": Layout(eye=(456, 280), mouth=(448, 662)),
}

# kunci ekspresi -> (nomor berkas, layout). Kunci sama dengan emotions.EXPRESSIONS.
EXPRESSION_FILES = {
    "neutral": ("000", "A"),
    "wink": ("001", "A"),
    "serious": ("002", "A"),
    "worried": ("003", "B"),
    "angry": ("004", "C"),
    "sad": ("005", "B"),
    "uneasy": ("007", "B"),
    "excited": ("008", "A"),
    "shout": ("010", "C"),
    "smug": ("011", "A"),
}


def _path(num: str, suffix: str = "") -> Path:
    return SPRITE_DIR / f"b003_{num}_00{suffix}.png"


def available() -> bool:
    """True jika folder sprite lengkap untuk minimal ekspresi 'neutral'."""
    return _path(EXPRESSION_FILES["neutral"][0]).is_file()


def has_eyes(expr: str) -> bool:
    """Apakah ekspresi punya patch mata (wink & excited tidak punya, matanya sudah gaya)."""
    entry = EXPRESSION_FILES.get(expr)
    return bool(entry) and _path(entry[0], "_e1").is_file()


def has_mouth(expr: str) -> bool:
    entry = EXPRESSION_FILES.get(expr)
    return bool(entry) and _path(entry[0], "_m1").is_file()


class MorganaSprites:
    """Menyusun gambar dasar + patch mata + patch mulut, di-cache per ukuran.

    `get()` mengembalikan None jika berkas tidak ada, supaya pemanggil bisa memakai
    placeholder. Gambar sumber tidak disimpan di memori; hanya hasil skala yang di-cache.
    """

    def __init__(self) -> None:
        self._base: dict[tuple, tuple[QImage, float, int, int] | None] = {}
        self._patch: dict[tuple, QImage | None] = {}
        self._final: dict[tuple, QPixmap | None] = {}

    # -- skala -----------------------------------------------------------------------
    @staticmethod
    def _factors(src_w: int, size: int, dpr: float) -> tuple[float, float, float]:
        """(sumber -> kanvas 768, kanvas 768 -> piksel target, total)."""
        to768 = 768.0 / src_w
        to_px = (size * dpr) / (STAGE[3] - STAGE[1])
        return to768, to_px, to768 * to_px

    def _scaled_base(self, expr: str, size: int, dpr: float):
        key = (expr, size, round(dpr, 2))
        if key not in self._base:
            self._base[key] = self._load_base(expr, size, dpr)
        return self._base[key]

    def _load_base(self, expr: str, size: int, dpr: float):
        num, _ = EXPRESSION_FILES[expr]
        img = QImage(str(_path(num)))
        if img.isNull():
            return None
        to768, _, total = self._factors(img.width(), size, dpr)
        # potong ke panggung (koordinat sumber), lalu skala dengan rata-rata area
        sx0, sy0 = round(STAGE[0] / to768), round(STAGE[1] / to768)
        sx1, sy1 = round(STAGE[2] / to768), round(STAGE[3] / to768)
        crop = img.copy(QRect(sx0, sy0, sx1 - sx0, sy1 - sy0))
        out = crop.scaled(max(1, round(crop.width() * total)), max(1, round(crop.height() * total)),
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        return out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), total, sx0, sy0

    def _scaled_patch(self, expr: str, suffix: str, size: int, dpr: float, total: float):
        key = (expr, suffix, size, round(dpr, 2))
        if key not in self._patch:
            img = QImage(str(_path(EXPRESSION_FILES[expr][0], f"_{suffix}")))
            if img.isNull():
                self._patch[key] = None
            else:
                out = img.scaled(max(1, round(img.width() * total)), max(1, round(img.height() * total)),
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                self._patch[key] = out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return self._patch[key]

    # -- publik ----------------------------------------------------------------------
    def get(self, expr: str, size: int, dpr: float = 1.0,
            eye: int = EYE_OPEN, mouth: int = MOUTH_BASE) -> QPixmap | None:
        if expr not in EXPRESSION_FILES:
            return None
        if not has_eyes(expr):
            eye = EYE_OPEN
        if not has_mouth(expr):
            mouth = MOUTH_BASE
        key = (expr, size, round(dpr, 2), eye, mouth)
        if key not in self._final:
            self._final[key] = self._compose(expr, size, dpr, eye, mouth)
        return self._final[key]

    def _compose(self, expr: str, size: int, dpr: float, eye: int, mouth: int) -> QPixmap | None:
        base = self._scaled_base(expr, size, dpr)
        if base is None:
            return None
        img, total, sx0, sy0 = base
        layout = LAYOUTS[EXPRESSION_FILES[expr][1]]
        if eye != EYE_OPEN or mouth != MOUTH_BASE:
            img = img.copy()                                   # jangan merusak gambar dasar di cache
            p = QPainter(img)
            for idx, files, pos in ((eye, _EYE_FILE, layout.eye),
                                           (mouth, _MOUTH_FILE, layout.mouth)):
                if idx and idx in files:
                    patch = self._scaled_patch(expr, files[idx], size, dpr, total)
                    if patch is not None:
                        p.drawImage(QPointF((pos[0] - sx0) * total, (pos[1] - sy0) * total), patch)
            p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        return pm
