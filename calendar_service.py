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
    return CalendarEvent(id=item.get("id", ""), title=item.get("summary") or "(tanpa judul)",
                         start=start, end=end, all_day=all_day)


def human_delta(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    if mins < 1:
        return "kurang dari 1 menit"
    if mins < 60:
        return f"{mins} menit"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"{hours} jam {m} menit" if m else f"{hours} jam"
    days, h = divmod(hours, 24)
    return f"{days} hari {h} jam" if h else f"{days} hari"


def day_label(when: datetime, now: datetime) -> str:
    diff = (when.date() - now.date()).days
    if diff == 0:
        return "hari ini"
    if diff == 1:
        return "besok"
    return f"{when:%d %b}"


def format_agenda(result: CalendarResult, now: datetime | None = None) -> str:
    """Teks untuk balon dialog: acara terdekat + waktu mulai + sisa waktu."""
    now = now or datetime.now().astimezone()
    if result.status == "error":
        return f"Gagal mengambil jadwal: {result.message}"

    prefix = ""
    if result.status == "no_credentials":
        prefix = ("credentials.json belum ada, jadi ini jadwal contoh. "
                  "Panduan langkah demi langkah ada di bagian atas calendar_service.py.\n")
    elif result.status == "no_libs":
        prefix = f"Library Google belum terpasang, jadi ini jadwal contoh. Jalankan: {PIP_HINT}\n"

    timed = sorted((e for e in result.events if not e.all_day), key=lambda e: e.start)
    ongoing = [e for e in timed if e.start <= now and (e.end is None or e.end > now)]
    upcoming = [e for e in timed if e.start > now]
    all_day = [e for e in result.events if e.all_day]

    lines: list[str] = []
    if ongoing:
        ev = ongoing[0]
        until = f" (sampai {ev.end:%H:%M})" if ev.end else ""
        lines.append(f"Sedang berlangsung: {ev.title}{until}")
    if upcoming:
        ev = upcoming[0]
        lines.append(f"Acara berikutnya: {ev.title} dalam {human_delta(ev.start - now)}!")
        lines.append(f"Mulai {ev.start:%H:%M} ({day_label(ev.start, now)})")
    elif not ongoing:
        lines.append("Tidak ada acara tersisa sampai besok. Santai, atau cicil tugasmu!")
    if all_day:
        lines.append(f"Seharian: {all_day[0].title}")
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
    def get_upcoming(self, interactive: bool = False) -> CalendarResult:
        """Acara dari sekarang sampai akhir hari besok.

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
                return CalendarResult(self._fetch(libs, interactive), "ok")
        except AuthRequired:
            return CalendarResult([], "auth_needed",
                                  "Izin Google kedaluwarsa. Klik AGENDA untuk login ulang.")
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

    def _fetch(self, libs, interactive: bool) -> list[CalendarEvent]:
        build = libs[3]
        creds = self._credentials(libs, interactive)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now = datetime.now().astimezone()
        end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        resp = service.events().list(
            calendarId="primary", timeMin=now.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=25).execute()
        events = [parse_event(i) for i in resp.get("items", [])]
        return [e for e in events if e is not None]

    # -- dummy ----------------------------------------------------------------
    def _dummy_events(self) -> list[CalendarEvent]:
        """Jadwal contoh, dijangkarkan ke waktu pertama kali diminta (stabil selama app jalan)."""
        if self._dummy is None:
            t0 = datetime.now().astimezone().replace(second=0, microsecond=0)
            tomorrow9 = (t0 + timedelta(days=1)).replace(hour=9, minute=0)
            self._dummy = [
                CalendarEvent("dummy-1", "Kuliah Jarkom (contoh)", t0 + timedelta(minutes=25),
                              t0 + timedelta(minutes=125), is_dummy=True),
                CalendarEvent("dummy-2", "Diskusi tim (contoh)", t0 + timedelta(hours=4),
                              t0 + timedelta(hours=5), is_dummy=True),
                CalendarEvent("dummy-3", "Kumpul tugas (contoh)", tomorrow9,
                              tomorrow9 + timedelta(hours=1), is_dummy=True),
            ]
        now = datetime.now().astimezone()
        return [e for e in self._dummy if (e.end or e.start) > now]


def _import_google():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    return Request, Credentials, InstalledAppFlow, build


def _short(exc: Exception, limit: int = 110) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"