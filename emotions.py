# -*- coding: utf-8 -*-
"""Pemilihan ekspresi Morgana dari isi dialog. Tanpa dependensi Qt.

Ekspresi (kunci) -> sprite di folder `Morgana/` (lihat sprites.py):

    neutral   b003_000   senyum biasa (default)
    wink      b003_001   mata memicing/kedip senang
    serious   b003_002   alis turun, serius
    worried   b003_003   ragu / cemas
    angry     b003_004   menoleh, geram (gigi terkatup)
    sad       b003_005   lesu, berkeringat
    uneasy    b003_007   tidak nyaman / bingung
    excited   b003_008   mata bintang
    shout     b003_010   berteriak / panik
    smug      b003_011   menyeringai percaya diri

`emotion_for_text` membaca kalimat (kalimat Morgana berbahasa Inggris dan pesan
program berbahasa Indonesia) lalu memilih ekspresi yang paling cocok.
"""
from __future__ import annotations

EXPRESSIONS = ("neutral", "wink", "serious", "worried", "angry", "sad",
               "uneasy", "excited", "shout", "smug")

# Ekspresi dasar tiap state gerak (idle / talking / sleepy / action).
STATE_EXPRESSION = {
    "idle": "neutral",
    "talking": "neutral",
    "sleepy": "sad",
    "action": "smug",
}

# Urutan penting: aturan pertama yang cocok menang. Kata kunci ditulis huruf kecil.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shout", ("hurry", "leave without us", "sekarang:", "go to bed!", "almost morning")),
    ("angry", ("shouldn't be staying up", "shouldn't skip meals", "tutup aplikasi",
               "cpu/ram tinggi")),
    ("uneasy", ("are you sure", "cats can't", "tidak ditemukan", "belum terpasang",
                "gagal", "galat", "error")),
    ("smug", ("looking cool", "phantom thief", "sojiro would be proud", "infiltration",
              "ngoding", "fokus")),
    ("excited", ("ooh", "whoa", "button mash", "paying off", "kerja bagus", "ayo mulai")),
    ("wink", ("good morning", "movie", "big bang burger", "stroll", "swing by", "leblanc",
              "dibuka.", "dimatikan", "dihentikan", "weather", "plenty of time",
              "schedule ahead")),
    ("worried", ("test tomorrow", "don't forget", "deadline", "part-time", "raining",
                 "time flies", "acara berikutnya", "acara mendekat")),
    ("sad", ("tired", "worn out", "sleep", "rest up", "well-rested", "bath", "mode tidur")),
    ("serious", ("shouldn't you", "study", "read a book", "time wisely", "gear",
                 "guard down", "stock up", "money", "gym", "water your plant", "tugas")),
)


def emotion_for_text(text: str, default: str = "neutral") -> str:
    """Kembalikan kunci ekspresi untuk `text`; `default` jika tak ada yang cocok."""
    low = text.lower()
    for emotion, keywords in _RULES:
        if any(k in low for k in keywords):
            return emotion
    return default if default in EXPRESSIONS else "neutral"
