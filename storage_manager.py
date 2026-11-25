# -*- coding: utf-8 -*-
"""Penyimpanan lokal (JSON): tasks.json, notes.json, settings.json.

Tanpa dependensi Qt, jadi mudah diuji terpisah. Fitur penting:
  * penulisan atomik (tulis ke file sementara lalu os.replace) -> file tidak rusak
    kalau komputer mati di tengah proses menyimpan;
  * file korup tidak membuat aplikasi crash: diganti nama jadi *.corrupt-<waktu>
    dan aplikasi mulai dari daftar kosong / pengaturan bawaan.
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import re
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
    """Tugas: {"id", "title", "done", "priority", "stat", "deadline", "notified", "rewarded", "created", "done_at", "updated_at"}."""

    @staticmethod
    def stat_of(task: dict | None) -> str:
        """Return valid Social Stat key for a task (defaults to 'proficiency' for legacy tasks)."""
        raw = str((task or {}).get("stat") or "").strip().lower()
        return raw if raw in SOCIAL_STATS else "proficiency"

    def add(self, title: str, priority: str = "normal",
            deadline: datetime | None = None,
            stat: str = "proficiency") -> dict | None:
        title = (title or "").strip()
        if not title:
            return None
        prio = "high" if priority == "high" else "normal"
        st = (stat or "").strip().lower()
        st = st if st in SOCIAL_STATS else "proficiency"
        dl_str = deadline.isoformat(timespec="minutes") if deadline else None
        ts = now_iso()
        task = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "done": False,
            "priority": prio,
            "stat": st,
            "deadline": dl_str,
            "notified": False,
            "rewarded": False,
            "created": ts,
            "done_at": None,
            "updated_at": ts,
        }
        self._items.append(task)
        self.save()
        return task

    def set_priority(self, task_id: str, priority: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        task["priority"] = "high" if priority == "high" else "normal"
        task["updated_at"] = now_iso()
        self.save()
        return True

    def set_stat(self, task_id: str, stat: str) -> bool:
        task = self.get(task_id)
        st = (stat or "").strip().lower()
        if task is None or st not in SOCIAL_STATS:
            return False
        task["stat"] = st
        task["updated_at"] = now_iso()
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
        task["updated_at"] = now_iso()
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
                copy = dict(item)
                copy["updated_at"] = now_iso()
                self._items.append(copy)
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
        task["updated_at"] = now_iso()
        self.save()
        return True

    def set_done(self, task_id: str, done: bool) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        ts = now_iso()
        task["done"] = bool(done)
        task["done_at"] = ts if done else None
        task["updated_at"] = ts
        self.save()
        return True

    def pending(self) -> list[dict]:
        return [t for t in self._items if not t.get("done")]

    def completed_today(self, now: datetime | None = None) -> list[dict]:
        """Return tasks marked done on today's local date."""
        today = (now or datetime.now()).date()
        res: list[dict] = []
        for t in self._items:
            if not t.get("done"):
                continue
            dt = parse_dt(t.get("done_at"))
            if dt is not None and dt.date() == today:
                res.append(t)
        return res

    def rollover_to_tomorrow(self, task_ids: list[str], now: datetime | None = None) -> int:
        """Move selected pending tasks' deadlines to tomorrow (preserving time or defaulting to 09:00)."""
        cur = now or datetime.now()
        tmrw = cur.date() + timedelta(days=1)
        wanted = set(task_ids)
        moved = 0
        ts = now_iso()
        for t in self._items:
            if t.get("id") not in wanted or t.get("done"):
                continue
            old_dl = parse_dt(t.get("deadline"))
            target_t = old_dl.time() if old_dl is not None else dtime(9, 0)
            new_dt = datetime.combine(tmrw, target_t)
            t["deadline"] = new_dt.isoformat(timespec="minutes")
            t["notified"] = False
            t["updated_at"] = ts
            moved += 1
        if moved:
            self.save()
        return moved

    def clear_done(self) -> int:
        before = len(self._items)
        self._items = [t for t in self._items if not t.get("done")]
        removed = before - len(self._items)
        if removed:
            self.save()
        return removed


_TAG_RE = re.compile(r"(?<![\w/#])#([A-Za-z][A-Za-z0-9_-]{0,23})")


def extract_tags(text: str) -> list[str]:
    """Extract unique lowercase `#tags` from note text (preserving order, ignoring Markdown headings)."""
    seen: list[str] = []
    for line in (text or "").splitlines():
        # Skip Markdown heading markers like `## Title`
        stripped = line.lstrip()
        if stripped.startswith("#") and (len(stripped) == 1 or stripped[1] in ("#", " ", "\t")):
            continue
        for m in _TAG_RE.finditer(line):
            tag = m.group(1).lower()
            if tag not in seen:
                seen.append(tag)
    return seen


def _merge_tags(title: str, content: str, extra: list[str] | None = None) -> list[str]:
    tags: list[str] = []
    for raw in (extra or []):
        clean = str(raw).strip().lstrip("#").lower()
        if clean and clean not in tags:
            tags.append(clean)
    for t in extract_tags(f"{title}\n{content}"):
        if t not in tags:
            tags.append(t)
    return tags[:12]


class NoteStore(JsonListStore):
    """True note-taking store: {"id", "title", "content", "category", "tags", "pinned", "archived", "created", "updated"}.

    Retains optional legacy `deadline` / `done` / `notified` / `rewarded` fields for seamless
    backward compatibility with older `notes.json` files without losing user data.
    """

    def add(self, title: str, content: str, deadline: datetime | None = None,
            category: str = "Personal", tags: list[str] | None = None) -> dict:
        stamp = now_iso()
        clean_title = title.strip()
        clean_content = content.strip()
        note = {"id": uuid.uuid4().hex[:12], "title": clean_title, "content": clean_content,
                "category": (category or "Personal").strip() or "Personal",
                "tags": _merge_tags(clean_title, clean_content, tags),
                "pinned": False, "archived": False,
                "deadline": deadline.isoformat(timespec="minutes") if deadline else None,
                "done": False, "notified": False, "rewarded": False,
                "created": stamp, "updated": stamp}
        self._items.append(note)
        self.save()
        return note

    def update(self, note_id: str, title: str, content: str,
               deadline: datetime | None = None, category: str | None = None,
               tags: list[str] | None = None, *, preserve_deadline: bool = False) -> dict | None:
        note = self.get(note_id)
        if note is None:
            return None
        clean_title = title.strip()
        clean_content = content.strip()
        if preserve_deadline:
            new_deadline = note.get("deadline")
        else:
            new_deadline = deadline.isoformat(timespec="minutes") if deadline else None
            if new_deadline != note.get("deadline"):
                note["notified"] = False          # deadline baru -> boleh diingatkan lagi
        patch = dict(title=clean_title, content=clean_content,
                     tags=_merge_tags(clean_title, clean_content, tags),
                     deadline=new_deadline, updated=now_iso())
        if category is not None:
            patch["category"] = category.strip() or "Personal"
        note.update(patch)
        self.save()
        return note

    def set_archived(self, note_id: str, archived: bool) -> bool:
        note = self.get(note_id)
        if note is None:
            return False
        note["archived"] = bool(archived)
        if archived:
            note["pinned"] = False
        note["updated"] = now_iso()
        self.save()
        return True

    def clear_deadline(self, note_id: str) -> bool:
        """Clear a legacy deadline field (e.g. after promoting it into a real Task)."""
        note = self.get(note_id)
        if note is None:
            return False
        note["deadline"] = None
        note["notified"] = False
        note["updated"] = now_iso()
        self.save()
        return True

    def note_tags(self, note: dict) -> list[str]:
        stored = note.get("tags")
        if isinstance(stored, list) and stored:
            return [str(t).strip().lstrip("#").lower() for t in stored if str(t).strip()]
        return extract_tags(f"{note.get('title', '')}\n{note.get('content', '')}")

    def all_tags(self, include_archived: bool = False) -> list[tuple[str, int]]:
        """Return `[(tag, count), ...]` sorted by frequency then alphabetically."""
        counts: dict[str, int] = {}
        for n in self._items:
            if not include_archived and n.get("archived"):
                continue
            for t in self.note_tags(n):
                counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

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
        if pinned:
            note["archived"] = False
        self.save()
        return True

    def append_journal_reflection(self, text: str, mood: str = "",
                                  now: datetime | None = None) -> dict | None:
        """Append a 1-line reflection into today's Journal note (creating it if needed)."""
        clean = (text or "").strip()
        if not clean:
            return None
        cur = now or datetime.now()
        title = f"Daily Reflection — {cur:%d %b %Y}"
        mood_s = f" [{mood.strip().upper()}]" if (mood or "").strip() else ""
        entry = f"- [{cur:%H:%M}]{mood_s} {clean}"

        existing = next(
            (n for n in self._items if not n.get("archived") and (n.get("title") or "").strip() == title),
            None,
        )
        if existing is not None:
            body = (existing.get("content") or "").rstrip()
            if "#wrapup" in body:
                lines = body.splitlines()
                if lines and lines[-1].strip().startswith("#"):
                    lines.insert(-1, entry)
                    new_body = "\n".join(lines)
                else:
                    new_body = f"{body}\n{entry}"
            else:
                new_body = (
                    f"{body}\n{entry}\n\n#journal #wrapup"
                    if body else f"## Evening Reflection\n{entry}\n\n#journal #wrapup"
                )
            return self.update(existing["id"], title, new_body, category="Journal", preserve_deadline=True)

        body = f"## Evening Reflection\n{entry}\n\n#journal #wrapup"
        return self.add(title, body, deadline=None, category="Journal", tags=["journal", "wrapup"])

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
        """Catatan aktif (belum selesai/diarsip) yang masih punya legacy deadline."""
        return [n for n in self._items if n.get("deadline") and not n.get("done") and not n.get("archived")]

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

    def update(self, entry_id: str, stat: str | None = None,
               activity: str | None = None, points: int | None = None,
               block: str | None = None) -> tuple[dict | None, dict | None]:
        """Edit an existing stat log entry in place; returns (updated, previous_snapshot)."""
        entry = self.get(entry_id)
        if entry is None:
            return None, None
        prev = dict(entry)
        new_stat = entry.get("stat", "knowledge")
        if stat is not None:
            if stat not in SOCIAL_STATS:
                return None, None
            new_stat = stat
        new_pts = max(1, min(500, int(entry.get("points", DEFAULT_STAT_POINTS))))
        if points is not None:
            try:
                new_pts = max(1, min(500, int(points)))
            except (TypeError, ValueError):
                return None, None
        new_act = entry.get("activity", "")
        if activity is not None:
            new_act = (activity or "").strip() or (SOCIAL_STATS[new_stat]["label"] + " block")
        new_blk = entry.get("block", default_time_block())
        if block is not None and block in TIME_BLOCKS:
            new_blk = block
        entry["stat"] = new_stat
        entry["points"] = new_pts
        entry["activity"] = new_act
        entry["block"] = new_blk
        self.save()
        return entry, prev

    def restore(self, entry: dict) -> dict | None:
        if not isinstance(entry, dict) or not entry.get("id"):
            return None
        existing = self.get(entry["id"])
        if existing is None:
            copy = dict(entry)
            self._items.append(copy)
            self.save()
            return copy
        existing.clear()
        existing.update(dict(entry))
        self.save()
        return existing

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
# Catatan Keuangan (finance.json) — terinspirasi alur bot-wa
# ---------------------------------------------------------------------------
FINANCE_CATEGORIES: tuple[str, ...] = (
    "Jajan",
    "Alat",
    "Mingguan&Bulanan",
    "Hiburan",
)
FINANCE_CATEGORY_SHORT: dict[str, str] = {
    "Jajan": "JAJAN",
    "Alat": "ALAT",
    "Mingguan&Bulanan": "MINGGUAN",
    "Hiburan": "HIBURAN",
}
FINANCE_CATEGORY_ALIASES: dict[str, str] = {
    "jajan": "Jajan",
    "snack": "Jajan",
    "makan": "Jajan",
    "food": "Jajan",
    "kopi": "Jajan",
    "alat": "Alat",
    "gear": "Alat",
    "tools": "Alat",
    "mingguan": "Mingguan&Bulanan",
    "bulanan": "Mingguan&Bulanan",
    "mingguan&bulanan": "Mingguan&Bulanan",
    "essentials": "Mingguan&Bulanan",
    "hiburan": "Hiburan",
    "fun": "Hiburan",
    "entertainment": "Hiburan",
}
FINANCE_WALLETS: tuple[str, ...] = ("Tunai", "BNI", "Mandiri")
FINANCE_WALLET_ALIASES: dict[str, str] = {
    "tunai": "Tunai",
    "cash": "Tunai",
    "dompet": "Tunai",
    "bni": "BNI",
    "qris": "BNI",
    "mandiri": "Mandiri",
}
FINANCE_TX_TYPES: tuple[str, ...] = ("expense", "income", "transfer")
FINANCE_WARN_RATIO: float = 0.80
FINANCE_MAX_AMOUNT: int = 999_999_999_999

DEFAULT_FINANCE_SHORTCUTS: tuple[dict, ...] = (
    {"name": "Galon",    "category": "Mingguan&Bulanan", "amount": 7000},
    {"name": "Kopi",     "category": "Jajan",            "amount": None},
    {"name": "Tempe",    "category": "Jajan",            "amount": None},
    {"name": "Billiard", "category": "Hiburan",          "amount": None},
)

_RUPIAH_SUFFIX_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(jt|juta|m|rb|ribu|k)$", re.IGNORECASE)
_RUPIAH_THOUS_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_RUPIAH_PLAIN_RE = re.compile(r"^\d+$")


def parse_rupiah(text: str | int | float | None) -> int | None:
    """Parse Indonesian Rupiah shorthand ('15rb', '1.5jt', '25k', 'Rp 25.000', '15000') -> int."""
    if isinstance(text, bool) or text is None:
        return None
    if isinstance(text, int):
        return text if 0 < text <= FINANCE_MAX_AMOUNT else None
    if isinstance(text, float):
        if text != text or text <= 0 or text > FINANCE_MAX_AMOUNT:
            return None
        val = int(round(text))
        return val if 0 < val <= FINANCE_MAX_AMOUNT else None
    raw = str(text).strip()
    if not raw:
        return None
    raw = re.sub(r"^rp\.?\s*", "", raw, flags=re.IGNORECASE).strip()
    if not raw or raw.startswith("-"):
        return None
    m = _RUPIAH_SUFFIX_RE.match(raw)
    if m:
        num_s = m.group(1).replace(",", ".")
        suf = m.group(2).lower()
        mult = 1_000_000 if suf in ("jt", "juta", "m") else 1_000
        try:
            val = int(round(float(num_s) * mult))
        except ValueError:
            return None
        return val if 0 < val <= FINANCE_MAX_AMOUNT else None
    if _RUPIAH_THOUS_RE.match(raw):
        val = int(raw.replace(".", "").replace(",", ""))
        return val if 0 < val <= FINANCE_MAX_AMOUNT else None
    if _RUPIAH_PLAIN_RE.match(raw):
        val = int(raw)
        return val if 0 < val <= FINANCE_MAX_AMOUNT else None
    return None


def format_rupiah(amount: int | float) -> str:
    """Format integer Rupiah cleanly with Indonesian dot thousand separators (e.g. Rp 15.000)."""
    val = int(round(float(amount)))
    sign = "-" if val < 0 else ""
    digits = f"{abs(val):,}".replace(",", ".")
    return f"{sign}Rp {digits}"


def normalize_finance_category(value: str | None) -> str:
    if not value:
        return "Jajan"
    low = str(value).strip().lower()
    if low in FINANCE_CATEGORY_ALIASES:
        return FINANCE_CATEGORY_ALIASES[low]
    for cat in FINANCE_CATEGORIES:
        if cat.lower() == low:
            return cat
    return "Jajan"


def normalize_finance_wallet(value: str | None) -> str:
    if not value:
        return "Tunai"
    low = str(value).strip().lower()
    if low in FINANCE_WALLET_ALIASES:
        return FINANCE_WALLET_ALIASES[low]
    for w in FINANCE_WALLETS:
        if w.lower() == low:
            return w
    return "Tunai"


class FinanceStore:
    """Local personal finance ledger (transactions, budgets, shortcuts & monthly bills)."""

    MAX_TRANSACTIONS = 3000

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._transactions: list[dict] = []
        self._budgets: dict[str, int] = {}
        self._shortcuts: list[dict] = [dict(s) for s in DEFAULT_FINANCE_SHORTCUTS]
        self._bills: list[dict] = []
        self.last_error = ""
        self.load()

    def load(self) -> None:
        self._transactions = []
        self._budgets = {}
        self._shortcuts = [dict(s) for s in DEFAULT_FINANCE_SHORTCUTS]
        self._bills = []
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

        tx_list = raw.get("transactions")
        if isinstance(tx_list, list):
            for item in tx_list:
                cleaned = self._clean_tx(item)
                if cleaned is not None:
                    self._transactions.append(cleaned)
            self._transactions = self._transactions[-self.MAX_TRANSACTIONS:]

        raw_budgets = raw.get("budgets")
        if isinstance(raw_budgets, dict):
            for k, v in raw_budgets.items():
                cat = normalize_finance_category(k)
                amt = parse_rupiah(v)
                if amt is not None:
                    self._budgets[cat] = amt

        raw_sc = raw.get("shortcuts")
        if isinstance(raw_sc, list):
            cleaned_sc: list[dict] = []
            seen: set[str] = set()
            for item in raw_sc:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()[:40]
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                amt = parse_rupiah(item.get("amount")) if item.get("amount") is not None else None
                cleaned_sc.append({
                    "name": name,
                    "category": normalize_finance_category(item.get("category")),
                    "amount": amt,
                })
            self._shortcuts = cleaned_sc

        raw_bills = raw.get("bills")
        if isinstance(raw_bills, list):
            for item in raw_bills:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()[:60]
                amt = parse_rupiah(item.get("amount"))
                if not name or amt is None:
                    continue
                try:
                    due_day = max(1, min(31, int(item.get("day", 1))))
                except (TypeError, ValueError):
                    due_day = 1
                paid_raw = item.get("paid_months")
                paid_months = sorted({
                    str(m).strip() for m in (paid_raw if isinstance(paid_raw, list) else [])
                    if re.match(r"^\d{4}-\d{2}$", str(m).strip())
                })[-24:]
                self._bills.append({
                    "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                    "name": name,
                    "day": due_day,
                    "amount": amt,
                    "category": normalize_finance_category(item.get("category")),
                    "wallet": normalize_finance_wallet(item.get("wallet")),
                    "paid_months": paid_months,
                })

    @staticmethod
    def _clean_tx(raw: object) -> dict | None:
        if not isinstance(raw, dict):
            return None
        tx_type = str(raw.get("type") or "expense").strip().lower()
        if tx_type not in FINANCE_TX_TYPES:
            return None
        amt = parse_rupiah(raw.get("amount"))
        if amt is None:
            return None
        day_s = str(raw.get("date") or "").strip()
        try:
            date.fromisoformat(day_s)
        except ValueError:
            day_s = date.today().isoformat()
        wallet = normalize_finance_wallet(raw.get("wallet"))
        to_wallet = normalize_finance_wallet(raw.get("to_wallet")) if tx_type == "transfer" else ""
        if tx_type == "transfer" and to_wallet == wallet:
            return None
        category = normalize_finance_category(raw.get("category")) if tx_type == "expense" else ""
        title = str(raw.get("title") or "").strip()[:120]
        if not title:
            title = f"Transfer {wallet} -> {to_wallet}" if tx_type == "transfer" else ("Income" if tx_type == "income" else category)
        return {
            "id": str(raw.get("id") or uuid.uuid4().hex[:8]),
            "type": tx_type,
            "amount": amt,
            "title": title,
            "category": category,
            "wallet": wallet,
            "to_wallet": to_wallet,
            "date": day_s,
            "created": str(raw.get("created") or now_iso()),
            "source": str(raw.get("source") or "manual")[:24],
        }

    def save(self) -> bool:
        payload = {
            "transactions": self._transactions[-self.MAX_TRANSACTIONS:],
            "budgets": dict(self._budgets),
            "shortcuts": list(self._shortcuts),
            "bills": list(self._bills),
        }
        ok, err = write_json_atomic(self.path, payload)
        self.last_error = err
        return ok

    # -- Transactions --------------------------------------------------------
    def all_transactions(self) -> list[dict]:
        return [dict(t) for t in self._transactions]

    def get_transaction(self, tx_id: str) -> dict | None:
        for t in self._transactions:
            if t["id"] == tx_id:
                return dict(t)
        return None

    def add_transaction(
        self,
        tx_type: str,
        amount: int | str,
        title: str,
        category: str = "Jajan",
        wallet: str = "Tunai",
        to_wallet: str = "",
        day: str | None = None,
        source: str = "manual",
        _save: bool = True,
    ) -> dict | None:
        kind = str(tx_type or "expense").strip().lower()
        if kind not in FINANCE_TX_TYPES:
            return None
        amt = parse_rupiah(amount)
        if amt is None:
            return None
        w_from = normalize_finance_wallet(wallet)
        w_to = normalize_finance_wallet(to_wallet) if kind == "transfer" else ""
        if kind == "transfer" and w_to == w_from:
            return None
        cat = normalize_finance_category(category) if kind == "expense" else ""
        day_s = (day or date.today().isoformat()).strip()
        try:
            date.fromisoformat(day_s)
        except ValueError:
            day_s = date.today().isoformat()
        clean_title = (title or "").strip()[:120]
        if not clean_title:
            if kind == "transfer":
                clean_title = f"Transfer {w_from} -> {w_to}"
            elif kind == "income":
                clean_title = f"Income ({w_from})"
            else:
                clean_title = cat
        entry = {
            "id": uuid.uuid4().hex[:8],
            "type": kind,
            "amount": amt,
            "title": clean_title,
            "category": cat,
            "wallet": w_from,
            "to_wallet": w_to,
            "date": day_s,
            "created": now_iso(),
            "source": str(source or "manual")[:24],
        }
        self._transactions.append(entry)
        if len(self._transactions) > self.MAX_TRANSACTIONS:
            self._transactions = self._transactions[-self.MAX_TRANSACTIONS:]
        if _save:
            self.save()
        return dict(entry)

    def update_transaction(
        self,
        tx_id: str,
        tx_type: str,
        amount: int | str,
        title: str,
        category: str = "Jajan",
        wallet: str = "Tunai",
        to_wallet: str = "",
        day: str | None = None,
    ) -> tuple[dict | None, dict | None]:
        """Update an existing transaction in-place; returns (updated, prev_snapshot)."""
        kind = str(tx_type or "expense").strip().lower()
        amt = parse_rupiah(amount)
        if kind not in FINANCE_TX_TYPES or amt is None:
            return None, None
        w_from = normalize_finance_wallet(wallet)
        w_to = normalize_finance_wallet(to_wallet) if kind == "transfer" else ""
        if kind == "transfer" and w_to == w_from:
            return None, None
        cat = normalize_finance_category(category) if kind == "expense" else ""
        for entry in self._transactions:
            if entry["id"] != tx_id:
                continue
            prev = dict(entry)
            day_s = (day or entry["date"]).strip()
            try:
                date.fromisoformat(day_s)
            except ValueError:
                day_s = entry["date"]
            clean_title = (title or "").strip()[:120]
            if not clean_title:
                clean_title = f"Transfer {w_from} -> {w_to}" if kind == "transfer" else (cat or "Income")
            entry["type"] = kind
            entry["amount"] = amt
            entry["title"] = clean_title
            entry["category"] = cat
            entry["wallet"] = w_from
            entry["to_wallet"] = w_to
            entry["date"] = day_s
            self.save()
            return dict(entry), prev
        return None, None

    def delete_transaction(self, tx_id: str) -> dict | None:
        for i, entry in enumerate(self._transactions):
            if entry["id"] == tx_id:
                removed = self._transactions.pop(i)
                self.save()
                return dict(removed)
        return None

    def restore_transaction(self, entry: dict) -> dict | None:
        cleaned = self._clean_tx(entry)
        if cleaned is None:
            return None
        for i, existing in enumerate(self._transactions):
            if existing["id"] == cleaned["id"]:
                self._transactions[i] = cleaned
                self.save()
                return dict(cleaned)
        self._transactions.append(cleaned)
        self._transactions.sort(key=lambda x: (x.get("date", ""), x.get("created", "")))
        self.save()
        return dict(cleaned)

    def month_transactions(self, year: int, month: int) -> list[dict]:
        prefix = f"{int(year):04d}-{int(month):02d}-"
        items = [dict(t) for t in self._transactions if t["date"].startswith(prefix)]
        items.sort(key=lambda x: (x.get("date", ""), x.get("created", "")), reverse=True)
        return items

    def wallet_balances(self) -> dict[str, int]:
        bal: dict[str, int] = {w: 0 for w in FINANCE_WALLETS}
        for t in self._transactions:
            amt = t["amount"]
            kind = t["type"]
            w = t["wallet"]
            if kind == "income":
                bal[w] = bal.get(w, 0) + amt
            elif kind == "expense":
                bal[w] = bal.get(w, 0) - amt
            elif kind == "transfer":
                bal[w] = bal.get(w, 0) - amt
                tw = t.get("to_wallet") or ""
                if tw in bal:
                    bal[tw] += amt
        bal["Total"] = sum(bal[w] for w in FINANCE_WALLETS)
        return bal

    def month_summary(self, year: int, month: int, today: date | None = None) -> dict:
        prefix = f"{int(year):04d}-{int(month):02d}-"
        today_s = (today or date.today()).isoformat()
        income = 0
        expense = 0
        today_expense = 0
        count = 0
        by_cat: dict[str, int] = {c: 0 for c in FINANCE_CATEGORIES}
        for t in self._transactions:
            if not t["date"].startswith(prefix):
                continue
            count += 1
            amt = t["amount"]
            if t["type"] == "income":
                income += amt
            elif t["type"] == "expense":
                expense += amt
                cat = t.get("category") or "Jajan"
                by_cat[cat] = by_cat.get(cat, 0) + amt
                if t["date"] == today_s:
                    today_expense += amt
        return {
            "income": income,
            "expense": expense,
            "net": income - expense,
            "today_expense": today_expense,
            "by_category": by_cat,
            "count": count,
        }

    # -- Monthly Budgets (/batas) --------------------------------------------
    def budgets(self) -> dict[str, int]:
        return dict(self._budgets)

    def set_budget(self, category: str, amount: int | str | None) -> bool:
        cat = normalize_finance_category(category)
        if amount is None or str(amount).strip() == "":
            self._budgets.pop(cat, None)
            self.save()
            return True
        amt = parse_rupiah(amount)
        if amt is None:
            return False
        self._budgets[cat] = amt
        self.save()
        return True

    def budget_warning(
        self, category: str, year: int, month: int, spent_override: int | None = None,
    ) -> tuple[str, float, int, int] | None:
        """Return ('over'|'warn', ratio, spent, limit) when category spend >= 80% of budget."""
        cat = normalize_finance_category(category)
        limit = self._budgets.get(cat)
        if not limit or limit <= 0:
            return None
        spent = int(spent_override) if spent_override is not None else self.month_summary(year, month)["by_category"].get(cat, 0)
        ratio = spent / float(limit)
        if ratio >= 1.0:
            return ("over", ratio, spent, limit)
        if ratio >= FINANCE_WARN_RATIO:
            return ("warn", ratio, spent, limit)
        return None

    # -- Quick Shortcuts (/pintasan) -----------------------------------------
    def shortcuts(self) -> list[dict]:
        return [dict(s) for s in self._shortcuts]

    def add_shortcut(self, name: str, category: str = "Jajan",
                     amount: int | str | None = None) -> dict | None:
        clean = (name or "").strip()[:40]
        if not clean:
            return None
        cat = normalize_finance_category(category)
        amt = parse_rupiah(amount) if (amount is not None and str(amount).strip() != "") else None
        if amount is not None and str(amount).strip() != "" and amt is None:
            return None
        entry = {"name": clean, "category": cat, "amount": amt}
        for i, sc in enumerate(self._shortcuts):
            if sc["name"].lower() == clean.lower():
                self._shortcuts[i] = entry
                self.save()
                return dict(entry)
        self._shortcuts.append(entry)
        self.save()
        return dict(entry)

    def delete_shortcut(self, name: str) -> bool:
        low = (name or "").strip().lower()
        before = len(self._shortcuts)
        self._shortcuts = [s for s in self._shortcuts if s["name"].lower() != low]
        if len(self._shortcuts) != before:
            self.save()
            return True
        return False

    # -- Recurring Monthly Bills (/tagihan) ----------------------------------
    def bills(self) -> list[dict]:
        items = [dict(b, paid_months=list(b.get("paid_months", []))) for b in self._bills]
        items.sort(key=lambda b: (b["day"], b["name"].lower()))
        return items

    @staticmethod
    def is_bill_paid(bill: dict, year: int, month: int) -> bool:
        ym = f"{int(year):04d}-{int(month):02d}"
        return ym in (bill.get("paid_months") or ())

    def add_bill(
        self, name: str, day: int, amount: int | str,
        category: str = "Mingguan&Bulanan", wallet: str = "BNI",
    ) -> dict | None:
        clean = (name or "").strip()[:60]
        amt = parse_rupiah(amount)
        if not clean or amt is None:
            return None
        try:
            due_day = max(1, min(31, int(day)))
        except (TypeError, ValueError):
            due_day = 1
        bill = {
            "id": uuid.uuid4().hex[:8],
            "name": clean,
            "day": due_day,
            "amount": amt,
            "category": normalize_finance_category(category),
            "wallet": normalize_finance_wallet(wallet),
            "paid_months": [],
        }
        self._bills.append(bill)
        self.save()
        return dict(bill)

    def delete_bill(self, bill_id: str) -> bool:
        before = len(self._bills)
        self._bills = [b for b in self._bills if b["id"] != bill_id]
        if len(self._bills) != before:
            self.save()
            return True
        return False

    def get_bill(self, bill_id: str) -> dict | None:
        for bill in self._bills:
            if bill["id"] == bill_id:
                return dict(bill, paid_months=list(bill.get("paid_months", [])))
        return None

    def mark_bill_paid(self, bill_id: str, year: int, month: int) -> dict | None:
        """Mark a recurring bill as paid for `(year, month)` (used when written directly to Google Sheets)."""
        ym = f"{int(year):04d}-{int(month):02d}"
        for bill in self._bills:
            if bill["id"] != bill_id:
                continue
            paid = list(bill.get("paid_months", []))
            if ym not in paid:
                paid.append(ym)
                bill["paid_months"] = sorted(paid)[-24:]
                self.save()
            return dict(bill, paid_months=list(bill.get("paid_months", [])))
        return None

    def pay_bill(
        self, bill_id: str, year: int, month: int,
        amount_override: int | str | None = None,
    ) -> tuple[dict | None, dict | None]:
        """Record an expense transaction for a bill and mark it paid for (year, month)."""
        ym = f"{int(year):04d}-{int(month):02d}"
        for bill in self._bills:
            if bill["id"] != bill_id:
                continue
            amt = parse_rupiah(amount_override) if amount_override is not None else bill["amount"]
            if amt is None:
                return None, None
            today = date.today()
            if today.year == year and today.month == month:
                pay_day = today.isoformat()
            else:
                last_d = calendar.monthrange(year, month)[1]
                pay_day = date(year, month, min(bill["day"], last_d)).isoformat()
            tx = self.add_transaction(
                tx_type="expense",
                amount=amt,
                title=bill["name"],
                category=bill["category"],
                wallet=bill["wallet"],
                day=pay_day,
                source="bill",
                _save=False,
            )
            if tx is None:
                return None, None
            paid = list(bill.get("paid_months", []))
            if ym not in paid:
                paid.append(ym)
                bill["paid_months"] = sorted(paid)[-24:]
            self.save()
            return dict(bill), tx
        return None, None


# ---------------------------------------------------------------------------
# Pengaturan (settings.json)
# ---------------------------------------------------------------------------
RADIAL_TABS: tuple[tuple[str, str], ...] = (
    ("events",  "Events (7-Day Calendar)"),
    ("tasks",   "Tasks (To-Do List)"),
    ("timer",   "Timer (Focus & Manual)"),
    ("notes",   "Notes (Knowledge Base)"),
    ("talk",    "Talk (Choice Chat)"),
    ("stats",   "Social Stats"),
    ("finance", "Finance (Money & Budget)"),
    ("system",  "System Menu"),
)
RADIAL_TAB_KEYS: tuple[str, ...] = tuple(k for k, _ in RADIAL_TABS)
_LEGACY_6_TABS = frozenset({"events", "tasks", "timer", "notes", "stats", "system"})
_LEGACY_7_TABS = frozenset({"events", "tasks", "timer", "notes", "talk", "stats", "system"})

QUICK_HOTKEY_PRESETS: tuple[str, ...] = (
    "Ctrl+Shift+M",
    "Ctrl+Alt+M",
    "Ctrl+Shift+Space",
    "Alt+Space",
    "Off",
)
AI_PROVIDER_PRESETS: tuple[str, ...] = ("gemini", "openai")
FOCUS_STAT_OPTIONS: tuple[str, ...] = SOCIAL_STAT_KEYS + ("none",)

SETTINGS_DEFAULTS = {
    "bedtime": "23:30",                 # bedtime reminder start
    "wake": "05:00",                    # bedtime reminder stop
    "curfew_enabled": True,
    "curfew_interval_sec": 30,          # interval between bedtime remarks (sec)
    "event_window_min": 120,            # start reminding before event/deadline (min)
    "event_interval_min": 10,           # repeat interval for event reminders (min)
    "focus_min": 25,                    # Pomodoro focus duration (min)
    "rest_min": 5,                      # Pomodoro break duration (min)
    "focus_stat": "knowledge",          # Social Stat rewarded when Focus finishes (or "none")
    "ui_scale": 100,                    # UI size percentage (75-150)
    "sound_enabled": True,              # Master audio toggle
    "sound_volume": 80,                 # Legacy master audio volume (kept for migration)
    "voice_volume": 85,                 # Morgana voiceline volume percentage (0-100)
    "sfx_volume": 75,                   # UI & Social Stats SFX volume percentage (0-100)
    "morning_briefing": True,           # Show Daily Briefing & Confidant Summary once daily
    "evening_wrapup": True,             # Show Evening Wrap-up once per evening
    "wrapup_time": "21:00",             # Target evening wrap-up trigger time (HH:MM)
    "auto_stat_rewards": True,          # Auto-award Social Stats from tasks, notes & focus
    "auto_open_stats_on_reward": True,  # Auto-open Social Stats star animation on activity rewards
    "app_awareness_enabled": True,      # Local-only active app/window awareness remarks
    "screen_time_enabled": True,        # Daily active screen-time tracking & break nudges
    "break_nudge_min": 60,              # Continuous active minutes before Morgana nudges a break
    "particles_enabled": True,          # Persona 5 micro-particles around Morgana
    "spontaneous_choices_enabled": True,  # Occasional interactive choice prompts while idle
    "auto_dnd_meetings": True,          # Auto-DND during video calls & meetings
    "auto_dnd_fullscreen": True,        # Auto-DND during fullscreen apps, games & presentations
    "manual_dnd": False,                # Manual Do Not Disturb mode
    "quick_hotkey": "Ctrl+Shift+M",     # Global shortcut for Quick Command & Capture
    "ai_enabled": False,                # Opt-in AI companion (Google Gemini or OpenAI-compatible)
    "ai_provider": "gemini",            # "gemini" (Google AI Studio) or "openai" (Ollama/OpenAI)
    "ai_endpoint": "http://localhost:11434/v1/chat/completions",
    "ai_model": "gemini-3.5-flash-lite",
    "ai_api_key": "",
    "ai_call_user": "Joker",            # How Morgana addresses you in AI conversations
    "ai_dialogue_enabled": True,        # Use AI for Talk / Choice Prompts & live app reactions
    "sheets_sync_enabled": False,       # Opt-in write-only Google Sheets task deadline sync (WhatsApp relay)
    "finance_sheets_enabled": False,    # Opt-in Google Sheets Finance sync (bot-wa Monthly Budget tabs)
    "sheets_sync_id": "",               # Google Spreadsheet ID or URL shared with Service Account
    "sheets_sync_tab": "Morgana",       # Dedicated worksheet tab name for synced deadlines
    "sheets_sync_key_path": "",         # Path to Service Account JSON (optional if placed beside app)
    "last_briefing_date": "",           # YYYY-MM-DD of last auto-delivered Daily Briefing
    "last_wrapup_date": "",             # YYYY-MM-DD of last auto-delivered Evening Wrap-up
    "finance_tab_migrated": True,       # Tracks one-time upgrade from legacy 7-tab radial defaults
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
    "break_nudge_min": (15, 240),
}
_BOOL_KEYS = (
    "curfew_enabled",
    "sound_enabled",
    "morning_briefing",
    "evening_wrapup",
    "auto_stat_rewards",
    "auto_open_stats_on_reward",
    "app_awareness_enabled",
    "screen_time_enabled",
    "particles_enabled",
    "spontaneous_choices_enabled",
    "auto_dnd_meetings",
    "auto_dnd_fullscreen",
    "manual_dnd",
    "ai_enabled",
    "ai_dialogue_enabled",
    "sheets_sync_enabled",
    "finance_sheets_enabled",
    "finance_tab_migrated",
)
_STR_KEYS = (
    "ai_endpoint",
    "ai_model",
    "ai_api_key",
    "ai_call_user",
    "sheets_sync_id",
    "sheets_sync_tab",
    "sheets_sync_key_path",
)


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
        # Upgrade default 6-tab / 7-tab configs cleanly to include new radial tabs once
        if "app_awareness_enabled" not in raw and isinstance(patch.get("enabled_tabs"), list):
            if set(str(x).lower() for x in patch["enabled_tabs"]) == _LEGACY_6_TABS:
                patch["enabled_tabs"] = list(RADIAL_TAB_KEYS)
        if "finance_tab_migrated" not in raw and isinstance(patch.get("enabled_tabs"), list):
            if set(str(x).lower() for x in patch["enabled_tabs"]) == _LEGACY_7_TABS:
                patch["enabled_tabs"] = list(RADIAL_TAB_KEYS)
        # Smooth upgrade from v1.4.0/v1.4.1 -> current Gemini 3.5 Flash-Lite
        if "ai_provider" not in raw:
            if bool(raw.get("ai_enabled")):
                patch["ai_provider"] = "openai"
            elif str(raw.get("ai_model") or "").strip().lower() in ("llama3.2", "gemini-2.5-flash", "gemini-2.0-flash"):
                patch["ai_provider"] = "gemini"
                patch["ai_model"] = "gemini-3.5-flash-lite"
        elif str(patch.get("ai_provider") or raw.get("ai_provider") or "gemini").strip().lower() == "gemini":
            if str(patch.get("ai_model") or "").strip().lower() in ("gemini-2.5-flash", "gemini-2.0-flash"):
                patch["ai_model"] = "gemini-3.5-flash-lite"
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
            if key in ("bedtime", "wake", "wrapup_time"):
                if _valid_hhmm(value):
                    self.data[key] = datetime.strptime(str(value), "%H:%M").strftime("%H:%M")
            elif key in _BOOL_KEYS:
                if isinstance(value, bool):
                    self.data[key] = value
            elif key in ("last_briefing_date", "last_wrapup_date"):
                if isinstance(value, str) and _valid_iso_date(value):
                    self.data[key] = value
            elif key == "quick_hotkey":
                if isinstance(value, str) and value.strip() in QUICK_HOTKEY_PRESETS:
                    self.data[key] = value.strip()
            elif key == "ai_provider":
                if isinstance(value, str) and value.strip().lower() in AI_PROVIDER_PRESETS:
                    self.data[key] = value.strip().lower()
            elif key == "focus_stat":
                if isinstance(value, str) and value.strip().lower() in FOCUS_STAT_OPTIONS:
                    self.data[key] = value.strip().lower()
            elif key in _STR_KEYS:
                if isinstance(value, str):
                    self.data[key] = value.strip()[:512]
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

    @property
    def wrapup_time(self) -> dtime:
        return datetime.strptime(self.data["wrapup_time"], "%H:%M").time()