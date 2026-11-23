# -*- coding: utf-8 -*-
"""Penyimpanan lokal (JSON): tasks.json, notes.json, settings.json.

Tanpa dependensi Qt, jadi mudah diuji terpisah. Fitur penting:
  * penulisan atomik (tulis ke file sementara lalu os.replace) -> file tidak rusak
    kalau komputer mati di tengah proses menyimpan;
  * file korup tidak membuat aplikasi crash: diganti nama jadi *.corrupt-<waktu>
    dan aplikasi mulai dari daftar kosong / pengaturan bawaan.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from datetime import time as dtime
from pathlib import Path

log = logging.getLogger("pet.storage")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    """ISO string -> datetime naif (waktu lokal); None jika kosong/rusak."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def write_json_atomic(path: Path, payload) -> tuple[bool, str]:
    """Tulis JSON secara atomik. Mengembalikan (berhasil, pesan_galat)."""
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
        return True, ""
    except OSError as exc:
        log.error("Gagal menyimpan %s: %s", path.name, exc)
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return False, str(exc)


def backup_corrupt(path: Path) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        path.rename(path.with_name(f"{path.name}.corrupt-{stamp}"))
    except OSError:
        pass


class JsonListStore:
    """Daftar dict (masing-masing punya "id") yang disimpan sebagai {"version": 1, "items": [...]}."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._items: list[dict] = []
        self.last_error = ""
        self.load()

    # -- baca / tulis -----------------------------------------------------------
    def load(self) -> None:
        self._items = []
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data["items"] if isinstance(data, dict) else data
            self._items = [i for i in raw if isinstance(i, dict) and i.get("id")]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Gagal membaca %s: %s", self.path.name, exc)
            backup_corrupt(self.path)

    def save(self) -> bool:
        """Simpan ke disk. Mengembalikan False (dan mengisi last_error) bila gagal."""
        ok, err = write_json_atomic(self.path, {"version": 1, "items": self._items})
        self.last_error = err
        return ok

    # -- util -----------------------------------------------------------------
    def all(self) -> list[dict]:
        return list(self._items)

    def get(self, item_id: str) -> dict | None:
        return next((i for i in self._items if i["id"] == item_id), None)

    def delete(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [i for i in self._items if i["id"] != item_id]
        if len(self._items) != before:
            self.save()
            return True
        return False


class TaskStore(JsonListStore):
    """Tugas: {"id", "title", "done", "created", "done_at"}."""

    def add(self, title: str, priority: str = "normal") -> dict | None:
        title = (title or "").strip()
        if not title:
            return None
        prio = "high" if priority == "high" else "normal"
        task = {"id": uuid.uuid4().hex[:12], "title": title, "done": False,
                "priority": prio, "created": now_iso(), "done_at": None}
        self._items.append(task)
        self.save()
        return task

    def set_priority(self, task_id: str, priority: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        task["priority"] = "high" if priority == "high" else "normal"
        self.save()
        return True

    def restore(self, items: list[dict]) -> None:
        existing = {i.get("id") for i in self._items}
        added = False
        for item in items:
            if isinstance(item, dict) and item.get("id") and item["id"] not in existing:
                self._items.append(dict(item))
                existing.add(item["id"])
                added = True
        if added:
            self.save()

    def rename(self, task_id: str, title: str) -> bool:
        task = self.get(task_id)
        title = (title or "").strip()
        if task is None or not title:
            return False
        task["title"] = title
        self.save()
        return True

    def set_done(self, task_id: str, done: bool) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        task["done"] = bool(done)
        task["done_at"] = now_iso() if done else None
        self.save()
        return True

    def pending(self) -> list[dict]:
        return [t for t in self._items if not t.get("done")]

    def clear_done(self) -> int:
        before = len(self._items)
        self._items = [t for t in self._items if not t.get("done")]
        removed = before - len(self._items)
        if removed:
            self.save()
        return removed


class NoteStore(JsonListStore):
    """Catatan: {"id", "title", "content", "deadline", "done", "notified", "created", "updated"}.

    `deadline` berupa string ISO waktu lokal atau None. `done` = ditandai selesai (tidak lagi
    diingatkan). `notified` menandai pengingat "deadline sudah tiba" sudah dibunyikan.
    """

    def add(self, title: str, content: str, deadline: datetime | None,
            category: str = "Personal") -> dict:
        stamp = now_iso()
        note = {"id": uuid.uuid4().hex[:12], "title": title.strip(), "content": content.strip(),
                "category": (category or "Personal").strip() or "Personal", "pinned": False,
                "deadline": deadline.isoformat(timespec="minutes") if deadline else None,
                "done": False, "notified": False, "created": stamp, "updated": stamp}
        self._items.append(note)
        self.save()
        return note

    def update(self, note_id: str, title: str, content: str,
               deadline: datetime | None, category: str | None = None) -> dict | None:
        note = self.get(note_id)
        if note is None:
            return None
        new_deadline = deadline.isoformat(timespec="minutes") if deadline else None
        if new_deadline != note.get("deadline"):
            note["notified"] = False          # deadline baru -> boleh diingatkan lagi
        patch = dict(title=title.strip(), content=content.strip(),
                     deadline=new_deadline, updated=now_iso())
        if category is not None:
            patch["category"] = category.strip() or "Personal"
        note.update(patch)
        self.save()
        return note

    def set_done(self, note_id: str, done: bool) -> bool:
        note = self.get(note_id)
        if note is None:
            return False
        note["done"] = bool(done)
        note["updated"] = now_iso()
        self.save()
        return True

    def set_pinned(self, note_id: str, pinned: bool) -> bool:
        note = self.get(note_id)
        if note is None:
            return False
        note["pinned"] = bool(pinned)
        self.save()
        return True

    def restore(self, note: dict) -> dict | None:
        if not isinstance(note, dict) or not note.get("id"):
            return None
        if self.get(note["id"]) is None:
            copy = dict(note)
            self._items.append(copy)
            self.save()
            return copy
        return None

    def with_deadline(self) -> list[dict]:
        """Catatan aktif (belum selesai) yang punya deadline."""
        return [n for n in self._items if n.get("deadline") and not n.get("done")]

    def due(self, now: datetime | None = None) -> list[dict]:
        """Catatan aktif yang deadline-nya sudah tiba dan belum diingatkan."""
        now = now or datetime.now()
        result = []
        for n in self.with_deadline():
            dl = parse_dt(n.get("deadline"))
            if dl is not None and dl <= now and not n.get("notified"):
                result.append(n)
        return result

    def mark_notified(self, note_id: str) -> None:
        note = self.get(note_id)
        if note is not None and not note.get("notified"):
            note["notified"] = True
            self.save()


# ---------------------------------------------------------------------------
# Social Stats (social_stats.json) — Persona 5 Royal style, 20 pts per level
# ---------------------------------------------------------------------------
STAT_POINTS_PER_LEVEL = 20
DEFAULT_STAT_POINTS = 3
QUICK_STAT_POINTS = (1, 2, 3, 5)
DAILY_BLOCK_TARGET = 3
TIME_BLOCKS = ("Daytime", "After School", "Evening")

# Pentagon order (clockwise starting from top)
SOCIAL_STAT_KEYS = ("knowledge", "charm", "kindness", "proficiency", "guts")

SOCIAL_STATS: dict[str, dict] = {
    "knowledge": {
        "label": "Knowledge",
        "short": "KNO",
        "examples": "Study, lectures, coursework",
        "placeholder": "e.g. Lecture review, 2h study session...",
        "ranks": ("Oblivious", "Learned", "Scholarly", "Encyclopedic", "Erudite"),
    },
    "guts": {
        "label": "Guts",
        "short": "GUT",
        "examples": "Gym, workouts, physical training",
        "placeholder": "e.g. Gym session, strength workout...",
        "ranks": ("Milquetoast", "Bold", "Staunch", "Dauntless", "Lionhearted"),
    },
    "proficiency": {
        "label": "Proficiency",
        "short": "PRO",
        "examples": "Badminton, volleyball, technique sports",
        "placeholder": "e.g. Badminton match, volleyball practice...",
        "ranks": ("Bumbling", "Decent", "Skilled", "Expert", "Transcendent"),
    },
    "charm": {
        "label": "Charm",
        "short": "CHA",
        "examples": "Movies, books, drawing, 3D, gamedev",
        "placeholder": "e.g. Watched a movie, 3D modeling, gamedev...",
        "ranks": ("Existent", "Head-Turning", "Suave", "Charismatic", "Debonair"),
    },
    "kindness": {
        "label": "Kindness",
        "short": "KIN",
        "examples": "Cleaning, laundry, groceries, room chores",
        "placeholder": "e.g. Room cleaning, laundry, grocery run...",
        "ranks": ("Inoffensive", "Considerate", "Empathetic", "Selfless", "Angelic"),
    },
}


def stat_level(points: int) -> int:
    """0-20 -> Lv 1, 21-40 -> Lv 2, 41-60 -> Lv 3, ... (unlimited)."""
    pts = max(0, int(points))
    if pts <= STAT_POINTS_PER_LEVEL:
        return 1
    return ((pts - 1) // STAT_POINTS_PER_LEVEL) + 1


def stat_level_progress(points: int) -> tuple[int, int, int, int]:
    """Return `(level, into_level, band_span, to_next_level)` for progress displays."""
    pts = max(0, int(points))
    lv = stat_level(pts)
    next_at = lv * STAT_POINTS_PER_LEVEL + 1
    if lv == 1:
        band_start = 0
        band_span = STAT_POINTS_PER_LEVEL + 1
    else:
        band_start = (lv - 1) * STAT_POINTS_PER_LEVEL + 1
        band_span = STAT_POINTS_PER_LEVEL
    return lv, pts - band_start, band_span, max(0, next_at - pts)


def stat_rank_title(stat: str, level: int) -> str:
    """Persona 5 Royal rank title for `stat` at `level`."""
    ranks = SOCIAL_STATS.get(stat, {}).get("ranks", ("Rank 1",))
    idx = max(0, min(len(ranks) - 1, int(level) - 1))
    return ranks[idx]


def default_time_block(hour: int | None = None) -> str:
    """Suggest a P5 time block from the current local hour."""
    h = datetime.now().hour if hour is None else int(hour)
    if h < 12:
        return TIME_BLOCKS[0]
    if h < 18:
        return TIME_BLOCKS[1]
    return TIME_BLOCKS[2]


class SocialStatsStore(JsonListStore):
    """Activity log: {"id", "stat", "activity", "points", "date", "block", "created"}."""

    def add(self, stat: str, activity: str, points: int = DEFAULT_STAT_POINTS,
            block: str | None = None, day: str | None = None) -> tuple[dict | None, int, int]:
        if stat not in SOCIAL_STATS:
            return None, 1, 1
        pts = max(1, min(500, int(points)))
        text = (activity or "").strip() or SOCIAL_STATS[stat]["label"] + " block"
        blk = block if block in TIME_BLOCKS else default_time_block()
        date_str = day or datetime.now().strftime("%Y-%m-%d")
        old_lv = stat_level(self.total_for(stat))
        entry = {
            "id": uuid.uuid4().hex[:12],
            "stat": stat,
            "activity": text,
            "points": pts,
            "date": date_str,
            "block": blk,
            "created": now_iso(),
        }
        self._items.append(entry)
        self.save()
        new_lv = stat_level(self.total_for(stat))
        return entry, old_lv, new_lv

    def restore(self, entry: dict) -> dict | None:
        if not isinstance(entry, dict) or not entry.get("id"):
            return None
        if self.get(entry["id"]) is None:
            copy = dict(entry)
            self._items.append(copy)
            self.save()
            return copy
        return None

    def total_for(self, stat: str) -> int:
        return sum(max(0, int(i.get("points", 0))) for i in self._items if i.get("stat") == stat)

    def totals(self) -> dict[str, int]:
        res = {k: 0 for k in SOCIAL_STAT_KEYS}
        for i in self._items:
            k = i.get("stat")
            if k in res:
                res[k] += max(0, int(i.get("points", 0)))
        return res

    def today_entries(self, day: str | None = None) -> list[dict]:
        target = day or datetime.now().strftime("%Y-%m-%d")
        return [i for i in self._items if i.get("date") == target]

    def recent(self, limit: int = 50, stat_filter: str = "all") -> list[dict]:
        items = self._items if stat_filter == "all" else [i for i in self._items if i.get("stat") == stat_filter]
        return list(reversed(items))[:max(1, limit)]


# ---------------------------------------------------------------------------
# Pengaturan (settings.json)
# ---------------------------------------------------------------------------
SETTINGS_DEFAULTS = {
    "bedtime": "23:30",            # bedtime reminder start
    "wake": "05:00",               # bedtime reminder stop
    "curfew_enabled": True,
    "curfew_interval_min": 1,      # interval between bedtime remarks (min)
    "event_window_min": 120,       # start reminding before event/deadline (min)
    "event_interval_min": 10,      # repeat interval for event reminders (min)
    "focus_min": 25,               # Pomodoro focus duration (min)
    "rest_min": 5,                 # Pomodoro break duration (min)
    "ui_scale": 100,               # UI size percentage (75-150)
    "sound_enabled": True,         # UI SFX, Social Stats fanfare, and Morgana voice
    "sound_volume": 80,            # Master audio volume percentage (0-100)
}
_INT_RANGES = {
    "curfew_interval_min": (1, 120),
    "event_window_min": (5, 1440),
    "event_interval_min": (1, 120),
    "focus_min": (1, 180),
    "rest_min": (1, 60),
    "ui_scale": (75, 150),
    "sound_volume": (0, 100),
}


def _valid_hhmm(value) -> bool:
    try:
        datetime.strptime(str(value), "%H:%M")
        return True
    except ValueError:
        return False


class SettingsStore:
    """Pengaturan pengguna; nilai tidak valid diabaikan dan diganti bawaan."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict = dict(SETTINGS_DEFAULTS)
        self.last_error = ""
        self.load()

    def load(self) -> None:
        self.data = dict(SETTINGS_DEFAULTS)
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("bukan objek JSON")
        except (OSError, ValueError) as exc:
            log.warning("Gagal membaca %s: %s", self.path.name, exc)
            backup_corrupt(self.path)
            return
        self.set(save=False, **{k: v for k, v in raw.items() if k in SETTINGS_DEFAULTS})

    def set(self, save: bool = True, **values) -> None:
        for key, value in values.items():
            if key not in SETTINGS_DEFAULTS:
                continue
            if key in ("bedtime", "wake"):
                if _valid_hhmm(value):
                    self.data[key] = datetime.strptime(str(value), "%H:%M").strftime("%H:%M")
            elif key in ("curfew_enabled", "sound_enabled"):
                if isinstance(value, bool):
                    self.data[key] = value
            else:
                lo, hi = _INT_RANGES[key]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.data[key] = max(lo, min(hi, int(value)))
        if save:
            self.save()

    def save(self) -> bool:
        ok, err = write_json_atomic(self.path, self.data)
        self.last_error = err
        return ok

    def __getitem__(self, key: str):
        return self.data[key]

    @property
    def bedtime(self) -> dtime:
        return datetime.strptime(self.data["bedtime"], "%H:%M").time()

    @property
    def wake(self) -> dtime:
        return datetime.strptime(self.data["wake"], "%H:%M").time()