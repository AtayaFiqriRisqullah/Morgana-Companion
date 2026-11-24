# -*- coding: utf-8 -*-
"""Local-only Active App/Window Awareness and Screen-Time tracking (Windows & Linux).

Privacy & reliability design:
  * 100% local — window titles and process names never leave the machine.
  * Zero external dependencies: uses `ctypes` Win32 APIs on Windows and
    graceful `xprop`/`xprintidle` (with cursor-delta fallback) on Linux.
  * Never crashes under headless Linux, Wayland, or restricted permissions;
    simply returns `None` when window inspection is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from storage_manager import backup_corrupt, write_json_atomic

log = logging.getLogger("pet.awareness")

# Friendly display names & category labels
APP_CATEGORY_LABELS: dict[str, str] = {
    "coding":   "Coding & Dev",
    "study":    "Study & Research",
    "writing":  "Writing & Docs",
    "creative": "Creative & Design",
    "gaming":   "Gaming",
    "media":    "Video & Music",
    "social":   "Chat & Social",
    "meeting":  "Calls & Meetings",
    "browsing": "Web Browsing",
    "other":    "Desktop",
}

# Process-name classification (lowercase stem without .exe)
_PROC_MAP: dict[str, tuple[str, str]] = {
    # Coding & Dev
    "code": ("coding", "VS Code"),
    "code - insiders": ("coding", "VS Code"),
    "codium": ("coding", "VSCodium"),
    "cursor": ("coding", "Cursor"),
    "windsurf": ("coding", "Windsurf"),
    "zed": ("coding", "Zed"),
    "pycharm64": ("coding", "PyCharm"),
    "pycharm": ("coding", "PyCharm"),
    "idea64": ("coding", "IntelliJ IDEA"),
    "webstorm64": ("coding", "WebStorm"),
    "clion64": ("coding", "CLion"),
    "goland64": ("coding", "GoLand"),
    "rider64": ("coding", "Rider"),
    "studio64": ("coding", "Android Studio"),
    "devenv": ("coding", "Visual Studio"),
    "sublime_text": ("coding", "Sublime Text"),
    "nvim": ("coding", "Neovim"),
    "vim": ("coding", "Vim"),
    "windowsterminal": ("coding", "Terminal"),
    "wt": ("coding", "Terminal"),
    "powershell": ("coding", "PowerShell"),
    "pwsh": ("coding", "PowerShell"),
    "cmd": ("coding", "Command Prompt"),
    "alacritty": ("coding", "Terminal"),
    "kitty": ("coding", "Terminal"),
    "wezterm-gui": ("coding", "Terminal"),
    "gnome-terminal-server": ("coding", "Terminal"),
    "konsole": ("coding", "Terminal"),
    "xfce4-terminal": ("coding", "Terminal"),
    "postman": ("coding", "Postman"),
    "docker desktop": ("coding", "Docker"),
    # Study & Knowledge
    "obsidian": ("study", "Obsidian"),
    "notion": ("study", "Notion"),
    "anki": ("study", "Anki"),
    "logseq": ("study", "Logseq"),
    "zotero": ("study", "Zotero"),
    "sumatrapdf": ("study", "PDF Reader"),
    "acrord32": ("study", "Acrobat Reader"),
    "acrobat": ("study", "Acrobat"),
    "evince": ("study", "Document Viewer"),
    "okular": ("study", "Okular"),
    # Writing & Office
    "winword": ("writing", "Word"),
    "soffice.bin": ("writing", "LibreOffice"),
    "libreoffice": ("writing", "LibreOffice"),
    "excel": ("writing", "Excel"),
    "powerpnt": ("writing", "PowerPoint"),
    "typora": ("writing", "Typora"),
    "notepad++": ("writing", "Notepad++"),
    "notepad": ("writing", "Notepad"),
    # Creative & Design
    "figma": ("creative", "Figma"),
    "blender": ("creative", "Blender"),
    "photoshop": ("creative", "Photoshop"),
    "illustrator": ("creative", "Illustrator"),
    "afterfx": ("creative", "After Effects"),
    "adobe premiere pro": ("creative", "Premiere Pro"),
    "resolve": ("creative", "DaVinci Resolve"),
    "krita": ("creative", "Krita"),
    "gimp-2.10": ("creative", "GIMP"),
    "gimp": ("creative", "GIMP"),
    "aseprite": ("creative", "Aseprite"),
    "clipstudiopaint": ("creative", "Clip Studio"),
    "inkscape": ("creative", "Inkscape"),
    "audacity": ("creative", "Audacity"),
    "fl64": ("creative", "FL Studio"),
    "obs64": ("creative", "OBS Studio"),
    "obs": ("creative", "OBS Studio"),
    # Gaming
    "steam": ("gaming", "Steam"),
    "steamwebhelper": ("gaming", "Steam"),
    "epicgameslauncher": ("gaming", "Epic Games"),
    "valorant-win64-shipping": ("gaming", "VALORANT"),
    "league of legends": ("gaming", "League of Legends"),
    "leagueclientux": ("gaming", "League of Legends"),
    "genshinimpact": ("gaming", "Genshin Impact"),
    "starrail": ("gaming", "Honkai: Star Rail"),
    "zenlesszonezero": ("gaming", "Zenless Zone Zero"),
    "p5r": ("gaming", "Persona 5 Royal"),
    "p3r": ("gaming", "Persona 3 Reload"),
    "minecraft": ("gaming", "Minecraft"),
    "javaw": ("gaming", "Minecraft / Java"),
    "robloxplayerbeta": ("gaming", "Roblox"),
    "cs2": ("gaming", "Counter-Strike 2"),
    "dota2": ("gaming", "Dota 2"),
    "eldenring": ("gaming", "Elden Ring"),
    "cyberpunk2077": ("gaming", "Cyberpunk 2077"),
    # Media
    "spotify": ("media", "Spotify"),
    "vlc": ("media", "VLC"),
    "mpv": ("media", "mpv"),
    "mpc-hc64": ("media", "Media Player"),
    # Social & Chat
    "discord": ("social", "Discord"),
    "telegram": ("social", "Telegram"),
    "whatsapp": ("social", "WhatsApp"),
    "slack": ("social", "Slack"),
    "line": ("social", "LINE"),
    # Meetings
    "zoom": ("meeting", "Zoom"),
    "teams": ("meeting", "Microsoft Teams"),
    "ms-teams": ("meeting", "Microsoft Teams"),
    # Browsers (further refined by window title)
    "chrome": ("browsing", "Chrome"),
    "msedge": ("browsing", "Edge"),
    "firefox": ("browsing", "Firefox"),
    "brave": ("browsing", "Brave"),
    "opera": ("browsing", "Opera"),
    "vivaldi": ("browsing", "Vivaldi"),
    "arc": ("browsing", "Arc"),
    "chromium": ("browsing", "Chromium"),
}

_BROWSER_PROCS = frozenset({
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "chromium",
    "google-chrome", "firefox-esr", "brave-browser",
})

# Title keywords used primarily inside browsers (or unknown processes)
_TITLE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("coding", "GitHub", ("github", "gitlab", "bitbucket", "pull request", "stack overflow",
                          "stackoverflow", "localhost:", "127.0.0.1:", "codepen", "codesandbox",
                          "replit", "leetcode", "hackerrank", "kaggle", "visual studio code")),
    ("study", "AI Research", ("chatgpt", "claude", "gemini", "perplexity", "notebooklm")),
    ("study", "Study", ("wikipedia", "google scholar", "coursera", "udemy", "edx", "khan academy",
                        "overleaf", "arxiv", "canvas", "classroom", "quizlet", "duolingo",
                        "mdn web docs", "documentation", "docs.python.org", ".pdf")),
    ("writing", "Docs", ("google docs", "google sheets", "google slides", "notion", "medium",
                         "substack", "wordpress", "grammarly")),
    ("creative", "Design", ("figma", "canva", "dribbble", "behance", "photopea", "excalidraw", "miro")),
    ("meeting", "Meeting", ("google meet", "zoom meeting", "microsoft teams", "webex", "jitsi meet", "huddle")),
    ("media", "YouTube", ("youtube",)),
    ("media", "Twitch", ("twitch",)),
    ("media", "Netflix", ("netflix", "disney+", "crunchyroll", "bilibili", "prime video", "spotify", "soundcloud")),
    ("social", "Social", ("discord", "whatsapp", "telegram", "reddit", "x.com", "/ x", "twitter",
                          "instagram", "tiktok", "facebook", "messenger", "linkedin")),
    ("gaming", "Gaming", ("persona 5", "persona 3", "persona 4", "steam", "genshin", "valorant",
                          "minecraft", "honkai", "league of legends", "elden ring", "cyberpunk")),
)

_SELF_TITLES = frozenset({
    "morgana", "morgana companion", "tasks", "notes", "events", "social stats",
    "timer", "settings", "daily briefing", "evening wrap-up", "ask & command", "quick command",
})

_STRIP_SUFFIXES = (
    " - google chrome", " — google chrome", " - microsoft edge", " — microsoft edge",
    " - mozilla firefox", " — mozilla firefox", " - brave", " — brave",
    " - opera", " - vivaldi", " - arc", " - chromium",
    " - visual studio code", " — visual studio code", " - cursor", " - windsurf",
    " - youtube", " - twitch", " - google docs", " - notion", " - figma",
    " | discord", " - discord", " - slack", " - spotify", " - vlc media player",
    " - adobe acrobat reader", " - adobe acrobat", " - sumatrapdf",
    " - word", " - excel", " - powerpoint", " - notepad++", " - notepad",
)
_GENERIC_TOPICS = frozenset({
    "", "new tab", "home", "start", "start page", "untitled", "about:blank",
    "settings", "extensions", "downloads", "history", "bookmarks",
    "youtube", "twitch", "netflix", "spotify", "discord", "whatsapp", "telegram",
    "google", "google search", "bing", "duckduckgo", "github", "chatgpt", "claude", "gemini",
    "chrome", "edge", "firefox", "brave", "steam", "epic games launcher",
})
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_NOTIF_PREFIX_RE = re.compile(r"^(?:\(\d+\+?\)|\[\d+\+?\]|[●•*]+\s*)+\s*")


def extract_window_topic(process: str, title: str, display_name: str, category: str) -> str:
    """Extract a clean, privacy-safe local topic (file, project, video, doc, or page title).

    Strips browser/app suffixes, notification counts, URLs, and emails, returning `""`
    when only a generic tab/app name is visible.
    """
    t = (title or "").strip()
    if not t:
        return ""
    t = _NOTIF_PREFIX_RE.sub("", t).strip()
    t = _URL_RE.sub("", t).strip()
    t = _EMAIL_RE.sub("", t).strip()

    # Iteratively strip known app/browser suffixes
    for _ in range(3):
        low = t.lower()
        stripped = False
        for suf in _STRIP_SUFFIXES:
            if low.endswith(suf):
                t = t[:len(t) - len(suf)].strip(" -—|·")
                stripped = True
                break
        if not stripped:
            break

    # Strip trailing " - <display_name>" if repeated
    dn_low = (display_name or "").lower()
    if dn_low and t.lower().endswith(" - " + dn_low):
        t = t[:len(t) - len(dn_low) - 3].strip()
    elif dn_low and t.lower().endswith(" | " + dn_low):
        t = t[:len(t) - len(dn_low) - 3].strip()

    # For IDEs like "main.py - Morgana-Companion", prefer concise "main.py (Morgana-Companion)"
    if category == "coding" and (" - " in t or " — " in t):
        sep = " — " if " — " in t else " - "
        parts = [p.strip() for p in t.split(sep) if p.strip()]
        if len(parts) >= 2:
            left, right = parts[0], parts[1]
            # Strip common VS Code dirty indicator
            left = left.lstrip("●•* ").strip()
            if left.lower() not in _GENERIC_TOPICS and right.lower() not in _GENERIC_TOPICS:
                cand = f"{left} ({right})" if len(left) + len(right) <= 30 else left
                t = cand
            elif left:
                t = left

    # Clean leading/trailing punctuation and collapse spaces
    t = re.sub(r"\s+", " ", t).strip(" -—|:·")
    low_t = t.lower()
    if len(t) < 2 or low_t in _GENERIC_TOPICS or low_t == dn_low:
        return ""
    if len(t) > 36:
        t = t[:35].rstrip() + "…"
    return t


@dataclass(frozen=True)
class ActiveWindowInfo:
    process: str
    title: str
    category: str
    display_name: str
    pid: int = 0
    topic: str = ""
    is_fullscreen: bool = False

    @property
    def phrase(self) -> str:
        """Human-friendly reference combining the active topic and app name when available."""
        if self.topic and self.topic.lower() != self.display_name.lower():
            return f"'{self.topic}' in {self.display_name}"
        return self.display_name


def format_duration(seconds: float) -> str:
    """Format elapsed seconds compactly: `2h 15m`, `42m`, or `<1m`."""
    total_s = max(0, int(seconds))
    mins = total_s // 60
    if mins <= 0:
        return "<1m" if total_s > 0 else "0m"
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def classify_window(process: str, title: str, pid: int = 0,
                    is_fullscreen: bool = False) -> ActiveWindowInfo | None:
    """Classify a foreground `(process, title)` pair into an `ActiveWindowInfo`.

    Returns `None` for Morgana Companion's own windows or empty desktop chrome.
    """
    if pid and pid == os.getpid():
        return None
    raw_proc = (process or "").strip()
    stem = Path(raw_proc).stem.lower() if raw_proc else ""
    clean_title = (title or "").strip()
    low_title = clean_title.lower()

    if not stem and not clean_title:
        return None
    if low_title in _SELF_TITLES or "morgana companion" in low_title:
        return None
    if stem in ("explorer", "searchhost", "shellexperiencehost", "startmenuexperiencehost",
                "lockapp", "applicationframehost", "dwm", "taskmgr"):
        if not clean_title or low_title in ("program manager", "task switching", ""):
            return None

    def _make(cat: str, label: str) -> ActiveWindowInfo:
        topic = extract_window_topic(stem, clean_title, label, cat)
        return ActiveWindowInfo(
            stem or "app", clean_title, cat, label, pid,
            topic=topic, is_fullscreen=bool(is_fullscreen),
        )

    # 1) Browsers: inspect page title first for specific activity (GitHub, YouTube, Study, etc.)
    if stem in _BROWSER_PROCS:
        for cat, label, keywords in _TITLE_RULES:
            if any(kw in low_title for kw in keywords):
                return _make(cat, label)
        disp = _PROC_MAP.get(stem, ("browsing", stem.title()))[1]
        return _make("browsing", disp)

    # 2) Known native applications by process stem
    if stem in _PROC_MAP:
        cat, disp = _PROC_MAP[stem]
        return _make(cat, disp)

    # 3) Fallback: inspect title keywords for unknown processes (e.g. games, web apps, Linux apps)
    for cat, label, keywords in _TITLE_RULES:
        if any(kw in low_title for kw in keywords):
            return _make(cat, label)

    disp = stem.replace("_", " ").replace("-", " ").title() if stem else clean_title[:24]
    return _make("other", disp[:28])


# ---------------------------------------------------------------------------
# OS-level foreground window & idle-time inspection
# ---------------------------------------------------------------------------
_WIN32_SHELL_CLASSES = frozenset({
    "progman", "workerw", "shell_traywnd", "shell_secondarytraywnd", "windows.ui.core.corewindow",
})

if sys.platform.startswith("win"):
    import ctypes as _ctypes
    from ctypes import wintypes as _wintypes

    class _WIN_MONITORINFO(_ctypes.Structure):
        _fields_ = [
            ("cbSize", _wintypes.DWORD),
            ("rcMonitor", _wintypes.RECT),
            ("rcWork", _wintypes.RECT),
            ("dwFlags", _wintypes.DWORD),
        ]

    class _WIN_LASTINPUTINFO(_ctypes.Structure):
        _fields_ = [
            ("cbSize", _wintypes.UINT),
            ("dwTime", _wintypes.DWORD),
        ]
else:
    _WIN_MONITORINFO = None
    _WIN_LASTINPUTINFO = None


def _win32_is_fullscreen(user32, hwnd) -> bool:
    """Return True if `hwnd` covers its full monitor or Windows reports Presentation/Fullscreen state."""
    if _WIN_MONITORINFO is None:
        return False
    import ctypes
    from ctypes import wintypes

    try:
        # 1) Windows Shell notification state (catches D3D fullscreen games & PowerPoint presentation mode)
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            # 2=QUNS_BUSY, 3=QUNS_RUNNING_D3D_FULL_SCREEN, 4=QUNS_PRESENTATION_MODE
            if state.value in (2, 3, 4):
                return True
    except Exception:
        pass

    try:
        # 2) Foreground window rect vs monitor rect (exclude desktop shell chrome)
        cls_buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls_buf, len(cls_buf))
        if cls_buf.value.strip().lower() in _WIN32_SHELL_CLASSES:
            return False

        wrect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(wrect)):
            return False

        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return False

        mi = _WIN_MONITORINFO()
        mi.cbSize = ctypes.sizeof(_WIN_MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        if (m.right - m.left) <= 0 or (m.bottom - m.top) <= 0:
            return False
        tol = 2
        return (
            wrect.left <= m.left + tol
            and wrect.top <= m.top + tol
            and wrect.right >= m.right - tol
            and wrect.bottom >= m.bottom - tol
        )
    except Exception:
        return False


def _win32_active_window() -> tuple[str, str, int, bool] | None:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(min(length + 1, 512))
            user32.GetWindowTextW(hwnd, buf, len(buf))
            title = buf.value

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        if pid.value:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if hproc:
                try:
                    pbuf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(len(pbuf))
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                        proc_name = pbuf.value
                finally:
                    kernel32.CloseHandle(hproc)
        is_fs = _win32_is_fullscreen(user32, hwnd)
        return proc_name, title, int(pid.value), is_fs
    except Exception:
        return None


def _linux_active_window() -> tuple[str, str, int, bool] | None:
    if not os.environ.get("DISPLAY") or not shutil.which("xprop"):
        return None
    try:
        r1 = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True, text=True, timeout=0.4, check=False,
        )
        m = re.search(r"0x[0-9a-fA-F]+", r1.stdout or "")
        if not m or m.group(0) == "0x0":
            return None
        wid = m.group(0)
        r2 = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS", "_NET_WM_NAME", "WM_NAME", "_NET_WM_PID", "_NET_WM_STATE"],
            capture_output=True, text=True, timeout=0.4, check=False,
        )
        out = r2.stdout or ""
        proc_name = ""
        mc = re.search(r'WM_CLASS\(STRING\)\s*=\s*"([^"]*)"(?:,\s*"([^"]*)")?', out)
        if mc:
            proc_name = (mc.group(1) or mc.group(2) or "").strip()
        title = ""
        mt = re.search(r'_NET_WM_NAME\(UTF8_STRING\)\s*=\s*"(.*)"', out)
        if not mt:
            mt = re.search(r'WM_NAME\(STRING\)\s*=\s*"(.*)"', out)
        if mt:
            title = mt.group(1).strip()
        pid = 0
        mp = re.search(r"_NET_WM_PID\(CARDINAL\)\s*=\s*(\d+)", out)
        if mp:
            pid = int(mp.group(1))
        is_fs = "_NET_WM_STATE_FULLSCREEN" in out
        return proc_name, title, pid, is_fs
    except Exception:
        return None


def get_active_window() -> ActiveWindowInfo | None:
    """Inspect the currently focused OS window locally and return classified info."""
    raw = None
    if sys.platform.startswith("win"):
        raw = _win32_active_window()
    elif sys.platform.startswith("linux"):
        raw = _linux_active_window()
    if raw is None:
        return None
    return classify_window(raw[0], raw[1], raw[2], is_fullscreen=raw[3])


def _win32_idle_seconds() -> float | None:
    if _WIN_LASTINPUTINFO is None:
        return None
    try:
        import ctypes

        lii = _WIN_LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_WIN_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        elapsed_ms = (tick - lii.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception:
        return None


def _linux_idle_seconds() -> float | None:
    if not os.environ.get("DISPLAY"):
        return None
    if shutil.which("xprintidle"):
        try:
            r = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=0.35, check=False)
            if r.returncode == 0 and r.stdout.strip().isdigit():
                return int(r.stdout.strip()) / 1000.0
        except Exception:
            pass
    return None


def get_idle_seconds() -> float | None:
    """Return seconds since last keyboard/mouse OS input, or None if unavailable."""
    if sys.platform.startswith("win"):
        return _win32_idle_seconds()
    if sys.platform.startswith("linux"):
        return _linux_idle_seconds()
    return None


# ---------------------------------------------------------------------------
# Screen-Time Store (daily active time, continuous session, app breakdown)
# ---------------------------------------------------------------------------
class ScreenTimeStore:
    """Persists lightweight local daily screen-time metrics (`screen_time.json`).

    Counts active time only while the user is not AFK (idle < `IDLE_CUTOFF_S`),
    tracks continuous sitting sessions for healthy break nudges, and retains
    the last 7 days of summary data.
    """

    IDLE_CUTOFF_S = 120.0          # >= 2 min without input -> AFK (not added to active time)
    BREAK_RESET_IDLE_S = 180.0     # >= 3 min AFK automatically counts as a natural break
    HISTORY_DAYS = 7

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.last_error = ""
        self._days: dict[str, dict] = {}
        self._continuous_s = 0.0
        self._last_nudge_t = 0.0
        self._snooze_until_t = 0.0
        self._dirty_s = 0.0
        self._last_cursor_pos: tuple[int, int] | None = None
        self._last_cursor_move_t = time.monotonic()
        self.load()

    @staticmethod
    def _today_key(now: datetime | None = None) -> str:
        return (now or datetime.now()).strftime("%Y-%m-%d")

    def _ensure_day(self, day_key: str) -> dict:
        entry = self._days.get(day_key)
        if not isinstance(entry, dict):
            entry = {
                "active_s": 0,
                "longest_streak_s": 0,
                "breaks": 0,
                "categories": {},
            }
            self._days[day_key] = entry
        entry.setdefault("active_s", 0)
        entry.setdefault("longest_streak_s", 0)
        entry.setdefault("breaks", 0)
        if not isinstance(entry.get("categories"), dict):
            entry["categories"] = {}
        return entry

    def _prune_old(self, now: datetime | None = None) -> None:
        cutoff = ((now or datetime.now()).date() - timedelta(days=self.HISTORY_DAYS)).isoformat()
        stale = [k for k in self._days if k < cutoff]
        for k in stale:
            self._days.pop(k, None)

    def load(self) -> None:
        self._days = {}
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("days"), dict):
                for k, v in raw["days"].items():
                    if isinstance(k, str) and isinstance(v, dict):
                        self._days[k] = v
            self._prune_old()
        except (OSError, ValueError) as exc:
            log.warning("Failed reading %s: %s", self.path.name, exc)
            backup_corrupt(self.path)

    def save(self) -> bool:
        self._prune_old()
        ok, err = write_json_atomic(self.path, {"days": self._days})
        self.last_error = err
        if ok:
            self._dirty_s = 0.0
        return ok

    def record_tick(self, dt_s: float, *, category: str = "other",
                    idle_s: float | None = None,
                    cursor_pos: tuple[int, int] | None = None,
                    now: datetime | None = None) -> bool:
        """Advance daily screen-time by `dt_s` if user is active. Returns True if active."""
        dt_s = max(0.0, min(60.0, float(dt_s)))
        if dt_s <= 0.0:
            return False

        # Fallback idle estimate via cursor movement if OS idle API is unavailable
        if idle_s is None and cursor_pos is not None:
            mono = time.monotonic()
            if self._last_cursor_pos is None or cursor_pos != self._last_cursor_pos:
                self._last_cursor_pos = cursor_pos
                self._last_cursor_move_t = mono
            idle_s = mono - self._last_cursor_move_t

        eff_idle = 0.0 if idle_s is None else float(idle_s)
        day_key = self._today_key(now)
        rec = self._ensure_day(day_key)

        if eff_idle >= self.IDLE_CUTOFF_S:
            if eff_idle >= self.BREAK_RESET_IDLE_S and self._continuous_s >= 300.0:
                rec["breaks"] = int(rec.get("breaks", 0)) + 1
                self._continuous_s = 0.0
                self.save()
            return False

        rec["active_s"] = int(rec.get("active_s", 0)) + int(round(dt_s))
        self._continuous_s += dt_s
        if self._continuous_s > float(rec.get("longest_streak_s", 0)):
            rec["longest_streak_s"] = int(round(self._continuous_s))

        cat = category if category in APP_CATEGORY_LABELS else "other"
        cats = rec["categories"]
        cats[cat] = int(cats.get(cat, 0)) + int(round(dt_s))

        self._dirty_s += dt_s
        if self._dirty_s >= 30.0:
            self.save()
        return True

    def record_break(self, now: datetime | None = None) -> None:
        """Reset continuous session counter when user takes an intentional break."""
        rec = self._ensure_day(self._today_key(now))
        if self._continuous_s >= 60.0:
            rec["breaks"] = int(rec.get("breaks", 0)) + 1
        self._continuous_s = 0.0
        self._snooze_until_t = 0.0
        self.save()

    def snooze_nudge(self, minutes: int = 15) -> None:
        self._snooze_until_t = time.monotonic() + max(1, int(minutes)) * 60.0

    def should_nudge_break(self, threshold_min: int, repeat_min: int = 15,
                           now_mono: float | None = None) -> bool:
        """True when continuous active session exceeds `threshold_min` and cooldown elapsed."""
        mono = time.monotonic() if now_mono is None else float(now_mono)
        if mono < self._snooze_until_t:
            return False
        if self._continuous_s < max(5, int(threshold_min)) * 60.0:
            return False
        if self._last_nudge_t > 0.0 and (mono - self._last_nudge_t) < max(5, int(repeat_min)) * 60.0:
            return False
        return True

    def mark_nudged(self, now_mono: float | None = None) -> None:
        self._last_nudge_t = time.monotonic() if now_mono is None else float(now_mono)

    def today_summary(self, now: datetime | None = None) -> dict:
        rec = self._ensure_day(self._today_key(now))
        cats = dict(rec.get("categories") or {})
        ranked = sorted(
            ((k, int(v)) for k, v in cats.items() if int(v) >= 60),
            key=lambda kv: kv[1],
            reverse=True,
        )
        return {
            "active_s": int(rec.get("active_s", 0)),
            "active_text": format_duration(rec.get("active_s", 0)),
            "continuous_s": int(round(self._continuous_s)),
            "continuous_text": format_duration(self._continuous_s),
            "longest_streak_s": int(rec.get("longest_streak_s", 0)),
            "longest_text": format_duration(rec.get("longest_streak_s", 0)),
            "breaks": int(rec.get("breaks", 0)),
            "top_categories": [
                (k, APP_CATEGORY_LABELS.get(k, k.title()), secs, format_duration(secs))
                for k, secs in ranked[:4]
            ],
        }


# ---------------------------------------------------------------------------
# Anti-Spam Active App Awareness State Machine
# ---------------------------------------------------------------------------
class AppAwarenessMonitor:
    """Tracks the user's current foreground category and decides when Morgana should react.

    Prevents noisy chatter by requiring the user to stay in a newly entered category for
    at least `DWELL_S` seconds, enforcing both a global remark cooldown and a per-category
    cooldown, and ignoring generic `"other"`/`"browsing"` desktop switching.
    """

    DWELL_S = 8.0                  # stay in a new category >= 8s before reacting
    GLOBAL_COOLDOWN_S = 240.0      # at most 1 awareness remark every 4 minutes
    CATEGORY_COOLDOWN_S = 720.0    # at most 1 remark per category every 12 minutes
    REMARKABLE = frozenset({"coding", "study", "writing", "creative", "gaming", "media", "social", "meeting"})

    def __init__(self) -> None:
        self.current: ActiveWindowInfo | None = None
        self.previous: ActiveWindowInfo | None = None
        self._app_since_t = 0.0
        self._switched_at_t = 0.0
        self._cat_since_t = 0.0
        self._last_cat = ""
        self._commented_for_visit = False
        self._last_global_t = 0.0
        self._last_by_cat: dict[str, float] = {}

    def note_window(self, info: ActiveWindowInfo | None, now_mono: float | None = None) -> None:
        """Record the latest external window without triggering a remark (keeps Talk context fresh)."""
        if info is None:
            return
        mono = time.monotonic() if now_mono is None else float(now_mono)
        prev = self.current
        if prev is None:
            self.current = info
            self._app_since_t = mono
            return
        if (prev.display_name, prev.category) != (info.display_name, info.category):
            self.previous = prev
            self._app_since_t = mono
            self._switched_at_t = mono
        self.current = info

    def app_dwell_s(self, now_mono: float | None = None) -> float:
        if self.current is None or self._app_since_t <= 0.0:
            return 0.0
        mono = time.monotonic() if now_mono is None else float(now_mono)
        return max(0.0, mono - self._app_since_t)

    def app_dwell_text(self, now_mono: float | None = None) -> str:
        secs = self.app_dwell_s(now_mono)
        return format_duration(secs) if secs >= 60.0 else ""

    def switched_recently(self, now_mono: float | None = None, window_s: float = 240.0) -> bool:
        if self.previous is None or self._switched_at_t <= 0.0:
            return False
        mono = time.monotonic() if now_mono is None else float(now_mono)
        return (mono - self._switched_at_t) <= window_s

    def update(self, info: ActiveWindowInfo | None,
               now_mono: float | None = None) -> ActiveWindowInfo | None:
        """Feed the latest `ActiveWindowInfo`. Returns `info` ONLY when Morgana should comment."""
        mono = time.monotonic() if now_mono is None else float(now_mono)
        if info is None:
            return None
        self.note_window(info, now_mono=mono)
        cat = info.category
        if cat != self._last_cat:
            self._last_cat = cat
            self._cat_since_t = mono
            self._commented_for_visit = False
            return None

        if self._commented_for_visit or cat not in self.REMARKABLE:
            return None
        if (mono - self._cat_since_t) < self.DWELL_S:
            return None
        if self._last_global_t > 0.0 and (mono - self._last_global_t) < self.GLOBAL_COOLDOWN_S:
            return None
        last_cat_t = self._last_by_cat.get(cat, 0.0)
        if last_cat_t > 0.0 and (mono - last_cat_t) < self.CATEGORY_COOLDOWN_S:
            return None

        self._commented_for_visit = True
        self._last_global_t = mono
        self._last_by_cat[cat] = mono
        return info
