# -*- coding: utf-8 -*-
"""Kalimat Morgana. Tanpa dependensi Qt.

Sumber utama: `morgana_list.md` di folder proyek (edit file itu untuk mengganti kalimat,
tanpa menyentuh kode). Format yang dikenali:

    ## 1. <judul kategori>      <- angka di depan menentukan kategori
    * "kalimat"                 <- satu kalimat per baris (tanda - juga boleh)

Kategori:  1 = awal program / pagi   2 = lewat jam tidur   3 = mendekati agenda   4 = umum
Jika file tidak ada atau sebuah kategori kosong, dipakai salinan bawaan di bawah ini.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

CATEGORY_BY_NUMBER = {1: "greeting", 2: "bedtime", 3: "agenda", 4: "general"}

# Salinan bawaan (isi morgana_list.md saat dibuat).
_DEFAULTS: dict[str, list[str]] = {
    "greeting": [
        'Good morning! Ready to tackle another day?',
        "You've got plenty of time today. How should we spend it?",
        "You've got quite the schedule ahead of you today!",
        "Let's check the weather forecast before heading out.",
    ],
    "bedtime": [
        "Aren't you tired? Let's call it a day and get some sleep.",
        "You must be tired after today. Let's go to sleep.",
        "You shouldn't be staying up so late. Go to bed!",
        "It's almost morning! You need to get some sleep.",
        "We've got a big day tomorrow, so you need to rest up.",
        'Hmm, you look pretty tired. Maybe you should take a bath?',
        "You're already so worn out, you wouldn't be able to concentrate.",
        "Make sure you're well-rested before the deadline.",
    ],
    "agenda": [
        "If we don't hurry, the train's gonna leave without us!",
        "Don't forget to check your phone! Someone might've messaged you.",
        "Shouldn't you be heading to your part-time job?",
        "You have a test tomorrow, right? Shouldn't you be studying?",
        "Time flies when you're busy. It's already evening!",
    ],
    "general": [
        "Why don't you read a book or study?",
        'Reading on the train is a great way to pass the time.',
        "It's raining today... A good day to stay indoors and study, maybe?",
        "Don't let your guard down just because it's a day off.",
        'Ooh, we can do this!',
        "Whoa, you're really putting in the effort!",
        'You seem a lot sharper lately. All that studying is paying off!',
        "Don't forget to water your plant!",
        'How about hitting the gym or working out in your room?',
        "You shouldn't skip meals, you know. Grab something to eat!",
        "That coffee looks delicious! Let me have a sip—wait, cats can't drink coffee...",
        "Let's go see a movie or hang out with someone!",
        'Are you sure you want to spend your money on that?',
    ],
}


_HEADING = re.compile(r"^##\s*(\d+)\.")
_BULLET = re.compile(r'^\s*[*-]\s*["\u201c](.+)["\u201d]\s*$')


def parse_markdown(text: str) -> dict[str, list[str]]:
    """Baca isi morgana_list.md -> {kategori: [kalimat, ...]} (hanya kategori yang terisi)."""
    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        h = _HEADING.match(line)
        if h:
            current = CATEGORY_BY_NUMBER.get(int(h.group(1)))
            continue
        b = _BULLET.match(line)
        if b and current:
            result.setdefault(current, []).append(b.group(1).strip())
    return result


def load_lines(path: Path | None) -> dict[str, list[str]]:
    """Kalimat dari file .md; kategori yang hilang/kosong diisi bawaan."""
    parsed: dict[str, list[str]] = {}
    if path is not None:
        try:
            parsed = parse_markdown(Path(path).read_text(encoding="utf-8"))
        except OSError:
            parsed = {}
    return {name: (parsed.get(name) or list(default)) for name, default in _DEFAULTS.items()}


class LineBag:
    """Mengambil kalimat acak tanpa pengulangan sampai semuanya terpakai (seperti mengocok kartu).
    Penting untuk pengingat yang muncul tiap menit: tidak ada kalimat yang sama berturut-turut."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines) or [""]
        self._queue: list[str] = []
        self._last: str | None = None

    def next(self) -> str:
        if not self._queue:
            self._queue = self._lines[:]
            random.shuffle(self._queue)
            if len(self._queue) > 1 and self._queue[-1] == self._last:   # hindari kembar di batas putaran
                self._queue[0], self._queue[-1] = self._queue[-1], self._queue[0]
        self._last = self._queue.pop()
        return self._last


def greeting_pool(lines: list[str], hour: int) -> list[str]:
    """Sapaan "Good morning" hanya dipakai pukul 04:00-10:59; sisanya boleh kapan saja."""
    if 4 <= hour < 11:
        return list(lines)
    pool = [ln for ln in lines if not ln.lower().startswith("good morning")]
    return pool or list(lines)