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
from datetime import date, datetime, timedelta
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
    """Tugas: {"id", "title", "done", "priority", "deadline", "notified", "rewarded", "created", "done_at"}."""

    def add(self, title: str, priority: str = "normal",
            deadline: datetime | None = None) -> dict | None:
        title = (title or "").strip()
        if not title:
            return None
        prio = "high" if priority == "high" else "normal"
        dl_str = deadline.isoformat(timespec="minutes") if deadline else None
        task = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "done": False,
            "priority": prio,
            "deadline": dl_str,
            "notified": False,
            "rewarded": False,
            "created": now_iso(),
            "done_at": None,
        }
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

    def set_deadline(self, task_id: str, deadline: datetime | None) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        new_dl = deadline.isoformat(timespec="minutes") if deadline else None
        if new_dl != task.get("deadline"):
            task["notified"] = False
        task["deadline"] = new_dl
        self.save()
        return True

    def mark_rewarded(self, task_id: str) -> None:
        task = self.get(task_id)
        if task is not None and not task.get("rewarded"):
            task["rewarded"] = True
            self.save()

    def with_deadline(self) -> list[dict]:
        """Pending tasks that have an active deadline."""
        return [t for t in self._items if t.get("deadline") and not t.get("done")]

    def due(self, now: datetime | None = None) -> list[dict]:
        """Pending tasks whose deadline has passed and haven't triggered a due alert yet."""
        now = now or datetime.now()
        result = []
        for t in self.with_deadline():
            dl = parse_dt(t.get("deadline"))
            if dl is not None and dl <= now and not t.get("notified"):
                result.append(t)
        return result

    def mark_notified(self, task_id: str) -> None:
        task = self.get(task_id)
        if task is not None and not task.get("notified"):
            task["notified"] = True
            self.save()

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
    """Catatan: {"id", "title", "content", "deadline", "done", "notified", "rewarded", "created", "updated"}.

    `deadline` berupa string ISO waktu lokal atau None. `done` = ditandai selesai (tidak lagi
    diingatkan). `notified` menandai pengingat "deadline sudah tiba" sudah dibunyikan.
    """

    def add(self, title: str, content: str, deadline: datetime | None,
            category: str = "Personal") -> dict:
        stamp = now_iso()
        note = {"id": uuid.uuid4().hex[:12], "title": title.strip(), "content": content.strip(),
                "category": (category or "Personal").strip() or "Personal", "pinned": False,
                "deadline": deadline.isoformat(timespec="minutes") if deadline else None,
                "done": False, "notified": False, "rewarded": False,
                "created": stamp, "updated": stamp}
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

    def mark_rewarded(self, note_id: str) -> None:
        note = self.get(note_id)
        if note is not None and not note.get("rewarded"):
            note["rewarded"] = True
            self.save()

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


class LocalEventStore(JsonListStore):
    """Local calendar events (`local_events.json`): works with or without Google Calendar.

    Schema: {"id", "title", "start", "end", "all_day", "location", "notes", "created", "updated"}.
    """

    def add(self, title: str, start: datetime, end: datetime | None = None,
            all_day: bool = False, location: str = "", notes: str = "") -> dict | None:
        title = (title or "").strip()
        if not title or start is None:
            return None
        if all_day:
            s_iso = start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
            e_dt = (start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            e_iso = e_dt.isoformat(timespec="minutes")
        else:
            s_clean = start.replace(second=0, microsecond=0)
            if end is not None:
                e_clean = end.replace(second=0, microsecond=0)
                if e_clean <= s_clean:
                    e_clean = s_clean + timedelta(hours=1)
                e_iso = e_clean.isoformat(timespec="minutes")
            else:
                e_iso = None
            s_iso = s_clean.isoformat(timespec="minutes")
        stamp = now_iso()
        ev = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "start": s_iso,
            "end": e_iso,
            "all_day": bool(all_day),
            "location": (location or "").strip(),
            "notes": (notes or "").strip(),
            "created": stamp,
            "updated": stamp,
        }
        self._items.append(ev)
        self.save()
        return ev

    def update(self, event_id: str, title: str, start: datetime, end: datetime | None = None,
               all_day: bool = False, location: str = "", notes: str = "") -> dict | None:
        ev = self.get(event_id)
        title = (title or "").strip()
        if ev is None or not title or start is None:
            return None
        if all_day:
            s_iso = start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
            e_iso = (start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat(timespec="minutes")
        else:
            s_clean = start.replace(second=0, microsecond=0)
            if end is not None:
                e_clean = end.replace(second=0, microsecond=0)
                if e_clean <= s_clean:
                    e_clean = s_clean + timedelta(hours=1)
                e_iso = e_clean.isoformat(timespec="minutes")
            else:
                e_iso = None
            s_iso = s_clean.isoformat(timespec="minutes")
        ev.update(
            title=title,
            start=s_iso,
            end=e_iso,
            all_day=bool(all_day),
            location=(location or "").strip(),
            notes=(notes or "").strip(),
            updated=now_iso(),
        )
        self.save()
        return ev

    def restore(self, event: dict) -> dict | None:
        if not isinstance(event, dict) or not event.get("id"):
            return None
        if self.get(event["id"]) is None:
            copy = dict(event)
            self._items.append(copy)
            self.save()
            return copy
        return None



# ---------------------------------------------------------------------------
# Social Stats (social_stats.json) — Persona 5 Royal progressive rank curve
# ---------------------------------------------------------------------------
# Escalating thresholds for Ranks 1..5 (Lv 1: 0, Lv 2: 30, Lv 3: 85, Lv 4: 175, Lv 5: 310),
# followed by unlimited Mastery tiers every +180 pts (Lv 6 ★1: 490, Lv 7 ★2: 670, ...).
STAT_RANK_THRESHOLDS: tuple[int, ...] = (0, 30, 85, 175, 310)
STAT_MASTERY_STEP = 180
STAT_POINTS_PER_LEVEL = STAT_RANK_THRESHOLDS[1]   # legacy compatibility constant
DEFAULT_STAT_POINTS = 2
QUICK_STAT_POINTS = (1, 2, 3, 5)
DAILY_BLOCK_TARGET = 3
TIME_BLOCKS = ("Daytime", "After School", "Evening")

# Pentagon order (clockwise starting from top)
SOCIAL_STAT_KEYS = ("knowledge", "charm", "kindness", "proficiency", "guts")

SOCIAL_STATS: dict[str, dict] = {
    "knowledge": {
        "label": "Knowledge",
        "short": "KNO",
        "examples": "Study, lectures, coursework, deep focus",
        "placeholder": "e.g. Lecture review, 2h study session...",
        "ranks": ("Oblivious", "Learned", "Scholarly", "Encyclopedic", "Erudite"),
    },
    "guts": {
        "label": "Guts",
        "short": "GUT",
        "examples": "Gym, workouts, tough challenges, physical training",
        "placeholder": "e.g. Gym session, strength workout...",
        "ranks": ("Milquetoast", "Bold", "Staunch", "Dauntless", "Lionhearted"),
    },
    "proficiency": {
        "label": "Proficiency",
        "short": "PRO",
        "examples": "Craft, coding projects, sports technique, tasks",
        "placeholder": "e.g. Badminton match, coding sprint...",
        "ranks": ("Bumbling", "Decent", "Skilled", "Expert", "Transcendent"),
    },
    "charm": {
        "label": "Charm",
        "short": "CHA",
        "examples": "Movies, books, art, 3D, gamedev, presentation",
        "placeholder": "e.g. Watched a movie, 3D modeling, gamedev...",
        "ranks": ("Existent", "Head-Turning", "Suave", "Charismatic", "Debonair"),
    },
    "kindness": {
        "label": "Kindness",
        "short": "KIN",
        "examples": "Cleaning, laundry, groceries, helping others",
        "placeholder": "e.g. Room cleaning, laundry, grocery run...",
        "ranks": ("Inoffensive", "Considerate", "Empathetic", "Selfless", "Angelic"),
    },
}


def stat_level(points: int) -> int:
    """Progressive P5R rank curve: Lv1=0..29, Lv2=30..84, Lv3=85..174, Lv4=175..309,
    Lv5=310..489 (Max Rank), then Mastery Lv6+ every +180 pts."""
    pts = max(0, int(points))
    max_base = len(STAT_RANK_THRESHOLDS)
    top_req = STAT_RANK_THRESHOLDS[-1]
    if pts >= top_req:
        return max_base + (pts - top_req) // STAT_MASTERY_STEP
    lv = 1
    for idx in range(1, max_base):
        if pts >= STAT_RANK_THRESHOLDS[idx]:
            lv = idx + 1
        else:
            break
    return lv


def stat_level_progress(points: int) -> tuple[int, int, int, int]:
    """Return `(level, into_level, band_span, to_next_level)` for progress displays."""
    pts = max(0, int(points))
    lv = stat_level(pts)
    max_base = len(STAT_RANK_THRESHOLDS)
    if lv < max_base:
        band_start = STAT_RANK_THRESHOLDS[lv - 1]
        next_at = STAT_RANK_THRESHOLDS[lv]
    else:
        mastery_idx = lv - max_base
        band_start = STAT_RANK_THRESHOLDS[-1] + mastery_idx * STAT_MASTERY_STEP
        next_at = band_start + STAT_MASTERY_STEP
    band_span = max(1, next_at - band_start)
    return lv, pts - band_start, band_span, max(0, next_at - pts)


def stat_pentagon_fraction(points: float) -> float:
    """Map raw points smoothly onto the 5 concentric pentagon rings (0.16..1.00).

    Ring 1 = Lv 1 start (0.20), Ring 2 = Lv 2 (0.40), Ring 3 = Lv 3 (0.60),
    Ring 4 = Lv 4 (0.80), Ring 5 = Lv 5 Max Rank (1.00).
    """
    pts = max(0.0, float(points))
    top_req = float(STAT_RANK_THRESHOLDS[-1])
    if pts >= top_req:
        return 1.0
    max_base = len(STAT_RANK_THRESHOLDS)
    for idx in range(max_base - 1):
        lo = float(STAT_RANK_THRESHOLDS[idx])
        hi = float(STAT_RANK_THRESHOLDS[idx + 1])
        if pts < hi:
            frac_in_band = (pts - lo) / max(1.0, hi - lo)
            rank_float = (idx + 1) + frac_in_band   # 1.0 .. 5.0
            return max(0.16, min(1.0, rank_float / 5.0))
    return 1.0


def stat_rank_title(stat: str, level: int) -> str:
    """Persona 5 Royal rank title for `stat` at `level` (appends `★N` for Mastery Lv 6+)."""
    ranks = SOCIAL_STATS.get(stat, {}).get("ranks", ("Rank 1",))
    lv = max(1, int(level))
    if lv <= len(ranks):
        return ranks[lv - 1]
    return f"{ranks[-1]} ★{lv - len(ranks)}"


def overall_confidant_level(totals: dict[str, int]) -> tuple[int, int]:
    """Return `(total_rank_sum, max_base_rank_sum)` across all 5 stats (starts at 5 / 25)."""
    total_lv = sum(stat_level(totals.get(k, 0)) for k in SOCIAL_STAT_KEYS)
    return total_lv, len(SOCIAL_STAT_KEYS) * len(STAT_RANK_THRESHOLDS)


def default_time_block(hour: int | None = None) -> str:
    """Suggest a P5 time block from the current local hour."""
    h = datetime.now().hour if hour is None else int(hour)
    if h < 12:
        return TIME_BLOCKS[0]
    if h < 18:
        return TIME_BLOCKS[1]
    return TIME_BLOCKS[2]


class SocialStatsStore(JsonListStore):
    """Activity log: {"id", "stat", "activity", "points", "date", "block", "source", "ref_id", "created"}."""

    def add(self, stat: str, activity: str, points: int = DEFAULT_STAT_POINTS,
            block: str | None = None, day: str | None = None,
            source: str = "manual", ref_id: str = "") -> tuple[dict | None, int, int]:
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
            "source": (source or "manual").strip().lower(),
            "ref_id": str(ref_id or ""),
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

    def today_points(self, day: str | None = None) -> int:
        return sum(max(0, int(i.get("points", 0))) for i in self.today_entries(day))

    def streak_days(self, today: date | None = None) -> int:
        """Consecutive calendar days with at least one logged activity (active through yesterday or today)."""
        active_dates: set[date] = set()
        for item in self._items:
            raw = item.get("date")
            if not raw:
                continue
            try:
                active_dates.add(date.fromisoformat(str(raw)))
            except ValueError:
                continue
        if not active_dates:
            return 0
        ref = today or date.today()
        if ref not in active_dates:
            ref = ref - timedelta(days=1)
            if ref not in active_dates:
                return 0
        count = 0
        cur = ref
        while cur in active_dates:
            count += 1
            cur -= timedelta(days=1)
        return count

    def recent(self, limit: int = 50, stat_filter: str = "all") -> list[dict]:
        items = self._items if stat_filter == "all" else [i for i in self._items if i.get("stat") == stat_filter]
        return list(reversed(items))[:max(1, limit)]


# ---------------------------------------------------------------------------
# Custom Daily Habits (habits.json) — user-defined daily checklist + Social Stat auto-reward
# ---------------------------------------------------------------------------
class HabitStore(JsonListStore):
    """User-customizable daily habits linked to Social Stats.

    Schema: {"id", "title", "stat", "points", "done_dates": [YYYY-MM-DD], "log_ids": {date: entry_id}, "created"}.
    Completion resets automatically each new day while tracking consecutive streaks.
    """

    def add(self, title: str, stat: str = "knowledge", points: int = 2) -> dict | None:
        title = (title or "").strip()
        if not title:
            return None
        st = stat if stat in SOCIAL_STATS else "knowledge"
        pts = max(1, min(10, int(points)))
        habit = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "stat": st,
            "points": pts,
            "done_dates": [],
            "log_ids": {},
            "created": now_iso(),
        }
        self._items.append(habit)
        self.save()
        return habit

    def update(self, habit_id: str, title: str, stat: str, points: int) -> dict | None:
        h = self.get(habit_id)
        title = (title or "").strip()
        if h is None or not title:
            return None
        h["title"] = title
        h["stat"] = stat if stat in SOCIAL_STATS else h.get("stat", "knowledge")
        h["points"] = max(1, min(10, int(points)))
        self.save()
        return h

    def restore(self, habit: dict) -> dict | None:
        if not isinstance(habit, dict) or not habit.get("id"):
            return None
        if self.get(habit["id"]) is None:
            copy = dict(habit)
            copy["done_dates"] = list(habit.get("done_dates") or [])
            copy["log_ids"] = dict(habit.get("log_ids") or {})
            self._items.append(copy)
            self.save()
            return copy
        return None

    def is_done_on(self, habit: dict, day: date | str | None = None) -> bool:
        if isinstance(day, date):
            key = day.isoformat()
        elif isinstance(day, str) and day:
            key = day
        else:
            key = date.today().isoformat()
        return key in (habit.get("done_dates") or [])

    def toggle_today(self, habit_id: str, stats_store: SocialStatsStore | None = None,
                     today: date | None = None) -> tuple[bool, dict | None, int, int]:
        """Toggle today's completion for `habit_id`.

        When checked: awards Social Stat points via `stats_store` (idempotent per day).
        When unchecked: reverts today's linked stat entry so toggling cannot farm points.
        Returns `(now_done, stat_entry, old_lv, new_lv)`.
        """
        habit = self.get(habit_id)
        if habit is None:
            return False, None, 1, 1
        ref = today or date.today()
        day_str = ref.isoformat()
        dates = list(habit.get("done_dates") or [])
        log_ids = dict(habit.get("log_ids") or {})

        if day_str in dates:
            dates = [d for d in dates if d != day_str]
            old_entry_id = log_ids.pop(day_str, None)
            habit["done_dates"] = dates
            habit["log_ids"] = log_ids
            self.save()
            if stats_store is not None and old_entry_id:
                stats_store.delete(old_entry_id)
            return False, None, 1, 1

        dates.append(day_str)
        # Keep unique sorted dates
        habit["done_dates"] = sorted(set(dates))
        entry, old_lv, new_lv = None, 1, 1
        if stats_store is not None:
            stat = habit.get("stat", "knowledge")
            pts = max(1, min(10, int(habit.get("points", 2))))
            entry, old_lv, new_lv = stats_store.add(
                stat=stat,
                activity=f"Habit: {habit.get('title', 'Daily Habit')}",
                points=pts,
                day=day_str,
                source="habit",
                ref_id=habit_id,
            )
            if entry is not None:
                log_ids[day_str] = entry["id"]
        habit["log_ids"] = log_ids
        self.save()
        return True, entry, old_lv, new_lv

    def streak(self, habit: dict, today: date | None = None) -> int:
        done_set: set[date] = set()
        for raw in habit.get("done_dates") or ():
            try:
                done_set.add(date.fromisoformat(str(raw)))
            except ValueError:
                continue
        if not done_set:
            return 0
        ref = today or date.today()
        if ref not in done_set:
            ref = ref - timedelta(days=1)
            if ref not in done_set:
                return 0
        n = 0
        cur = ref
        while cur in done_set:
            n += 1
            cur -= timedelta(days=1)
        return n

    def best_streak(self, habit: dict) -> int:
        days: list[date] = []
        for raw in habit.get("done_dates") or ():
            try:
                days.append(date.fromisoformat(str(raw)))
            except ValueError:
                continue
        if not days:
            return 0
        days = sorted(set(days))
        best = cur = 1
        for i in range(1, len(days)):
            if (days[i] - days[i - 1]).days == 1:
                cur += 1
                best = max(best, cur)
            else:
                cur = 1
        return best

    def today_summary(self, today: date | None = None) -> tuple[int, int]:
        ref = today or date.today()
        done = sum(1 for h in self._items if self.is_done_on(h, ref))
        return done, len(self._items)


# ---------------------------------------------------------------------------
# Pengaturan (settings.json)
# ---------------------------------------------------------------------------
RADIAL_TABS: tuple[tuple[str, str], ...] = (
    ("events", "Events (7-Day Calendar)"),
    ("tasks",  "Tasks (To-Do List)"),
    ("timer",  "Timer (Focus & Manual)"),
    ("notes",  "Notes (Quick Notes)"),
    ("stats",  "Social Stats"),
    ("system", "System Menu"),
)
RADIAL_TAB_KEYS: tuple[str, ...] = tuple(k for k, _ in RADIAL_TABS)

SETTINGS_DEFAULTS = {
    "bedtime": "23:30",                 # bedtime reminder start
    "wake": "05:00",                    # bedtime reminder stop
    "curfew_enabled": True,
    "curfew_interval_sec": 30,          # interval between bedtime remarks (sec)
    "event_window_min": 120,            # start reminding before event/deadline (min)
    "event_interval_min": 10,           # repeat interval for event reminders (min)
    "focus_min": 25,                    # Pomodoro focus duration (min)
    "rest_min": 5,                      # Pomodoro break duration (min)
    "ui_scale": 100,                    # UI size percentage (75-150)
    "sound_enabled": True,              # Master audio toggle
    "sound_volume": 80,                 # Legacy master audio volume (kept for migration)
    "voice_volume": 85,                 # Morgana voiceline volume percentage (0-100)
    "sfx_volume": 75,                   # UI & Social Stats SFX volume percentage (0-100)
    "morning_briefing": True,           # Show Daily Briefing & Confidant Summary once daily
    "auto_stat_rewards": True,          # Auto-award Social Stats from tasks, notes & focus
    "last_briefing_date": "",           # YYYY-MM-DD of last auto-delivered Daily Briefing
    "enabled_tabs": list(RADIAL_TAB_KEYS),  # Active tabs shown in Morgana's radial menu
}
_INT_RANGES = {
    "curfew_interval_sec": (10, 3600),
    "event_window_min": (5, 1440),
    "event_interval_min": (1, 120),
    "focus_min": (1, 180),
    "rest_min": (1, 60),
    "ui_scale": (75, 150),
    "sound_volume": (0, 100),
    "voice_volume": (0, 100),
    "sfx_volume": (0, 100),
}
_BOOL_KEYS = ("curfew_enabled", "sound_enabled", "morning_briefing", "auto_stat_rewards")


def _valid_hhmm(value) -> bool:
    try:
        datetime.strptime(str(value), "%H:%M")
        return True
    except ValueError:
        return False


def _valid_iso_date(value) -> bool:
    if value == "":
        return True
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def normalize_enabled_tabs(value) -> list[str] | None:
    """Validate and order radial menu tab keys; returns None if empty or invalid."""
    if not isinstance(value, (list, tuple, set)):
        return None
    wanted = {str(v).strip().lower() for v in value}
    cleaned = [k for k in RADIAL_TAB_KEYS if k in wanted]
    return cleaned if cleaned else None


class SettingsStore:
    """Pengaturan pengguna; nilai tidak valid diabaikan dan diganti bawaan."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict = dict(SETTINGS_DEFAULTS)
        self.data["enabled_tabs"] = list(RADIAL_TAB_KEYS)
        self.last_error = ""
        self.load()

    def load(self) -> None:
        self.data = dict(SETTINGS_DEFAULTS)
        self.data["enabled_tabs"] = list(RADIAL_TAB_KEYS)
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
        patch = {k: v for k, v in raw.items() if k in SETTINGS_DEFAULTS}
        # Migrate legacy curfew_interval_min -> curfew_interval_sec (preserve >1 min custom values;
        # upgrade the old 1-minute default cleanly to the new 30-second cadence).
        if "curfew_interval_sec" not in patch and "curfew_interval_min" in raw:
            old_m = raw.get("curfew_interval_min")
            if isinstance(old_m, (int, float)) and not isinstance(old_m, bool) and int(old_m) > 1:
                patch["curfew_interval_sec"] = int(old_m) * 60
        # Migrate legacy single sound_volume -> separate voice_volume & sfx_volume
        if "sound_volume" in raw:
            old_v = raw.get("sound_volume")
            if isinstance(old_v, (int, float)) and not isinstance(old_v, bool):
                if "voice_volume" not in raw:
                    patch["voice_volume"] = int(old_v)
                if "sfx_volume" not in raw:
                    patch["sfx_volume"] = int(old_v)
        self.set(save=False, **patch)

    def set(self, save: bool = True, **values) -> None:
        # Accept legacy curfew_interval_min keyword cleanly
        if "curfew_interval_min" in values and "curfew_interval_sec" not in values:
            old_m = values.pop("curfew_interval_min")
            if isinstance(old_m, (int, float)) and not isinstance(old_m, bool):
                values["curfew_interval_sec"] = int(old_m) * 60
        for key, value in values.items():
            if key not in SETTINGS_DEFAULTS:
                continue
            if key in ("bedtime", "wake"):
                if _valid_hhmm(value):
                    self.data[key] = datetime.strptime(str(value), "%H:%M").strftime("%H:%M")
            elif key in _BOOL_KEYS:
                if isinstance(value, bool):
                    self.data[key] = value
            elif key == "last_briefing_date":
                if isinstance(value, str) and _valid_iso_date(value):
                    self.data[key] = value
            elif key == "enabled_tabs":
                cleaned = normalize_enabled_tabs(value)
                if cleaned is not None:
                    self.data["enabled_tabs"] = cleaned
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