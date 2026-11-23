# -*- coding: utf-8 -*-
"""Google Calendar (read-only) + fallback jadwal dummy. Tanpa dependensi Qt.

Library tambahan:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

PANDUAN credentials.json (sekali saja)
--------------------------------------
 1. Buka https://console.cloud.google.com lalu buat project baru (mis. "Coding Pet").
 2. Menu > APIs & Services > Library > cari "Google Calendar API" > Enable.
 3. Menu > Google Auth platform > Branding (atau "OAuth consent screen"): isi nama app
    dan email dukungan. Di bagian Audience pilih External.
 4. Google Auth platform > Audience > Test users > Add users > masukkan emailmu sendiri.
 5. Google Auth platform > Clients > Create Client > Application type: Desktop app >
    Create > Download JSON.
 6. Ganti nama file hasil unduhan menjadi `credentials.json`, taruh di folder yang sama
    dengan main.py.
 7. Jalankan pet, klik AGENDA. Browser terbuka: pilih akunmu dan izinkan akses baca
    kalender. Jika muncul "Google hasn't verified this app", klik Advanced > Continue
    (wajar untuk aplikasi pribadi). Setelah itu `token.json` dibuat otomatis.

Catatan: selama status app "Testing" dan tipe External, Google membuat token kedaluwarsa
tiap 7 hari. Saat itu pet minta kamu klik AGENDA lagi untuk login ulang. Agar tidak
kedaluwarsa, ubah Publishing status ke "In production" (aman untuk pemakaian pribadi).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("pet.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
PIP_HINT = "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"


@dataclass
class CalendarEvent:
    id: str
    title: str
    start: datetime               # timezone-aware, zona waktu lokal
    end: datetime | None = None
    all_day: bool = False
    is_dummy: bool = False


@dataclass
class CalendarResult:
    events: list[CalendarEvent] = field(default_factory=list)
    # "ok" | "no_credentials" | "no_libs" | "auth_needed" | "error"
    status: str = "ok"
    message: str = ""
    is_dummy: bool = False


class AuthRequired(Exception):
    """Token Google belum ada / kedaluwarsa dan login interaktif tidak diizinkan."""


# ---------------------------------------------------------------------------
# Parsing & format teks (murni, mudah diuji)
# ---------------------------------------------------------------------------
def _parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()


def parse_event(item: dict) -> CalendarEvent | None:
    """Item JSON Google Calendar -> CalendarEvent (None jika dibatalkan/tak valid)."""
    if item.get("status") == "cancelled":
        return None
    s, e = item.get("start") or {}, item.get("end") or {}
    try:
        if "dateTime" in s:
            start = _parse_dt(s["dateTime"])
            end = _parse_dt(e["dateTime"]) if e.get("dateTime") else None
            all_day = False
        elif "date" in s:
            d = date.fromisoformat(s["date"])
            start = datetime(d.year, d.month, d.day).astimezone()
            ed = date.fromisoformat(e["date"]) if e.get("date") else None
            end = datetime(ed.year, ed.month, ed.day).astimezone() if ed else None
            all_day = True
        else:
            return None
    except (ValueError, KeyError):
        return None
    return CalendarEvent(id=item.get("id", ""), title=item.get("summary") or "(untitled)",
                         start=start, end=end, all_day=all_day)


def human_delta(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    if mins < 1:
        return "less than 1 min"
    if mins < 60:
        return f"{mins} min"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {m}m" if m else f"{hours}h"
    days, h = divmod(hours, 24)
    return f"{days}d {h}h" if h else f"{days}d"


def day_label(when: datetime, now: datetime) -> str:
    diff = (when.date() - now.date()).days
    if diff == 0:
        return "today"
    if diff == 1:
        return "tomorrow"
    return f"{when:%a, %d %b}"


def events_by_day(events: list[CalendarEvent], now: datetime | None = None,
                  days: int = 7) -> list[tuple[date, list[CalendarEvent]]]:
    """Group `events` into `days` consecutive day buckets starting from today."""
    now = now or datetime.now().astimezone()
    start_day = now.date()
    day_list = [start_day + timedelta(days=i) for i in range(max(1, days))]
    buckets: dict[date, list[CalendarEvent]] = {d: [] for d in day_list}
    for ev in events:
        if ev.all_day:
            s_d = ev.start.date()
            e_d = ev.end.date() if ev.end else s_d + timedelta(days=1)
            if e_d <= s_d:
                e_d = s_d + timedelta(days=1)
            for d in day_list:
                if s_d <= d < e_d:
                    buckets[d].append(ev)
        else:
            d = ev.start.date()
            if d in buckets:
                buckets[d].append(ev)
    for d in day_list:
        buckets[d].sort(key=lambda e: (0 if e.all_day else 1, e.start))
    return [(d, buckets[d]) for d in day_list]


def morgana_briefing(result: CalendarResult, now: datetime | None = None) -> tuple[str, str]:
    """Return `(status_tag, morgana_line)` about the next event for the Events window."""
    now = now or datetime.now().astimezone()
    if result.status == "error":
        return ("SYNC ERROR",
                f"I couldn't reach your calendar right now ({result.message}). Hit Refresh to try again!")
    if result.status == "auth_needed":
        return ("SIGN IN",
                "Your Google Calendar session expired. Click Sign In and I'll grab your week's schedule!")

    timed = sorted((e for e in result.events if not e.all_day), key=lambda e: e.start)
    ongoing = [e for e in timed if e.start <= now and (e.end is None or e.end > now)]
    upcoming = [e for e in timed if e.start > now]
    today_all_day = [
        e for e in result.events
        if e.all_day and e.start.date() <= now.date() < (e.end.date() if e.end else e.start.date() + timedelta(days=1))
    ]
    sample_note = ""
    if result.status == "no_credentials":
        sample_note = " (Showing a sample week—add credentials.json to sync Google Calendar.)"
    elif result.status == "no_libs":
        sample_note = " (Showing a sample week—install the Google Calendar packages to sync.)"

    if ongoing:
        ev = ongoing[0]
        until = f" until {ev.end:%H:%M}" if ev.end else ""
        extra = ""
        if upcoming:
            nxt = upcoming[0]
            extra = f" After that, {nxt.title} starts at {nxt.start:%H:%M} ({day_label(nxt.start, now)})."
        return ("HAPPENING NOW",
                f"You've got {ev.title} happening right now{until}! Stay focused—you've got this.{extra}{sample_note}")
    if upcoming:
        ev = upcoming[0]
        tag = "SAMPLE · NEXT UP" if result.is_dummy else "NEXT UP"
        msg = (f"Next up is {ev.title} in {human_delta(ev.start - now)}! "
               f"It starts at {ev.start:%H:%M} ({day_label(ev.start, now)}).")
        if today_all_day:
            msg += f" Also on today's calendar: {today_all_day[0].title}."
        return (tag, msg + sample_note)
    if today_all_day:
        tag = "SAMPLE · ALL DAY" if result.is_dummy else "ALL DAY"
        return (tag,
                f"Today's all-day event is {today_all_day[0].title}. "
                f"No timed events left this week—enjoy the breathing room!{sample_note}")
    tag = "SAMPLE · CLEAR" if result.is_dummy else "ALL CLEAR"
    return (tag,
            f"Your schedule is completely clear for the next 7 days! Relax, work on your tasks, or level up your stats.{sample_note}")


def format_agenda(result: CalendarResult, now: datetime | None = None) -> str:
    """Dialog bubble text: nearest event + start time + remaining time."""
    now = now or datetime.now().astimezone()
    if result.status == "error":
        return f"Couldn't load your schedule: {result.message}"

    prefix = ""
    if result.status == "no_credentials":
        prefix = ("credentials.json isn't set up yet, so here's a sample schedule. "
                  "See the setup guide at the top of calendar_service.py.\n")
    elif result.status == "no_libs":
        prefix = f"Google Calendar packages aren't installed yet, so here's a sample schedule. Run: {PIP_HINT}\n"

    timed = sorted((e for e in result.events if not e.all_day), key=lambda e: e.start)
    ongoing = [e for e in timed if e.start <= now and (e.end is None or e.end > now)]
    upcoming = [e for e in timed if e.start > now]
    all_day = [e for e in result.events if e.all_day]

    lines: list[str] = []
    if ongoing:
        ev = ongoing[0]
        until = f" (until {ev.end:%H:%M})" if ev.end else ""
        lines.append(f"Happening now: {ev.title}{until}")
    if upcoming:
        ev = upcoming[0]
        lines.append(f"Next up: {ev.title} in {human_delta(ev.start - now)}!")
        lines.append(f"Starts at {ev.start:%H:%M} ({day_label(ev.start, now)})")
    elif not ongoing:
        lines.append("Your schedule is clear for the next 7 days! Relax, or chip away at your tasks.")
    if all_day:
        lines.append(f"All day: {all_day[0].title}")
    return prefix + "\n".join(lines)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class CalendarService:
    def __init__(self, base_dir: Path) -> None:
        self.credentials_path = Path(base_dir) / "credentials.json"
        self.token_path = Path(base_dir) / "token.json"
        self._lock = threading.Lock()          # cegah dua login/fetch bersamaan
        self._dummy: list[CalendarEvent] | None = None

    def is_configured(self) -> bool:
        return self.credentials_path.is_file()

    def has_token(self) -> bool:
        return self.token_path.is_file()

    # -- publik ----------------------------------------------------------------
    def get_upcoming(self, interactive: bool = False, days: int = 7) -> CalendarResult:
        """Acara selama `days` hari ke depan (bawaan 7 hari / 1 minggu).

        interactive=True mengizinkan membuka browser untuk login Google (hanya saat
        pengguna sendiri yang meminta). Fungsi ini TIDAK pernah melempar exception.
        """
        if not self.is_configured():
            return CalendarResult(self._dummy_events(), "no_credentials", is_dummy=True)
        try:
            libs = _import_google()
        except ImportError:
            return CalendarResult(self._dummy_events(), "no_libs", is_dummy=True)
        try:
            with self._lock:
                return CalendarResult(self._fetch(libs, interactive, days=days), "ok")
        except AuthRequired:
            return CalendarResult([], "auth_needed",
                                  "Google authorization expired. Click EVENTS to sign in again.")
        except Exception as exc:                       # jaringan, kuota, login dibatalkan, dll.
            log.warning("Gagal mengambil kalender: %s", exc)
            return CalendarResult([], "error", _short(exc))

    # -- Google ---------------------------------------------------------------
    def _credentials(self, libs, interactive: bool):
        Request, Credentials, InstalledAppFlow, _build = libs
        creds = None
        if self.token_path.is_file():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception:
                creds = None
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                return creds
            except Exception as exc:                   # token dicabut / kedaluwarsa 7 hari
                log.info("Refresh token gagal: %s", exc)
        if not interactive:
            raise AuthRequired()
        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)          # membuka browser, menunggu persetujuan
        self._save_token(creds)
        return creds

    def _save_token(self, creds) -> None:
        self.token_path.write_text(creds.to_json(), encoding="utf-8")

    def _fetch(self, libs, interactive: bool, days: int = 7) -> list[CalendarEvent]:
        build = libs[3]
        creds = self._credentials(libs, interactive)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=max(1, days))
        resp = service.events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100).execute()
        events = [parse_event(i) for i in resp.get("items", [])]
        return [e for e in events if e is not None]

    # -- dummy ----------------------------------------------------------------
    def _dummy_events(self) -> list[CalendarEvent]:
        """Jadwal contoh 1 minggu, dijangkarkan ke waktu pertama kali diminta."""
        if self._dummy is None:
            t0 = datetime.now().astimezone().replace(second=0, microsecond=0)
            day0 = t0.replace(hour=0, minute=0)
            d1_9 = (day0 + timedelta(days=1)).replace(hour=9, minute=0)
            d1_16 = (day0 + timedelta(days=1)).replace(hour=16, minute=0)
            d2_10 = (day0 + timedelta(days=2)).replace(hour=10, minute=30)
            d3_all = day0 + timedelta(days=3)
            d4_18 = (day0 + timedelta(days=4)).replace(hour=18, minute=0)
            d6_14 = (day0 + timedelta(days=6)).replace(hour=14, minute=0)
            self._dummy = [
                CalendarEvent("dummy-1", "Study Session (sample)", t0 + timedelta(minutes=25),
                              t0 + timedelta(minutes=125), is_dummy=True),
                CalendarEvent("dummy-2", "Gym Workout (sample)", t0 + timedelta(hours=4),
                              t0 + timedelta(hours=5, minutes=30), is_dummy=True),
                CalendarEvent("dummy-3", "Project Sync (sample)", d1_9,
                              d1_9 + timedelta(hours=1), is_dummy=True),
                CalendarEvent("dummy-4", "Badminton Practice (sample)", d1_16,
                              d1_16 + timedelta(hours=2), is_dummy=True),
                CalendarEvent("dummy-5", "Lecture & Notes Review (sample)", d2_10,
                              d2_10 + timedelta(hours=2), is_dummy=True),
                CalendarEvent("dummy-6", "Weekly Reset & Laundry (sample)", d3_all,
                              d3_all + timedelta(days=1), all_day=True, is_dummy=True),
                CalendarEvent("dummy-7", "GameDev Hobby Block (sample)", d4_18,
                              d4_18 + timedelta(hours=2), is_dummy=True),
                CalendarEvent("dummy-8", "Movie Night (sample)", d6_14,
                              d6_14 + timedelta(hours=2), is_dummy=True),
            ]
        today_start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return [e for e in self._dummy if (e.end or e.start) >= today_start]


def _import_google():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    return Request, Credentials, InstalledAppFlow, build


def _short(exc: Exception, limit: int = 110) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"