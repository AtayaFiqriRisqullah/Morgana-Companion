# -*- coding: utf-8 -*-
"""Qt-free Quick Command parser, local conversational engine, and optional AI helper.

Powering Morgana's Spotlight-style **Ask & Command** bar:
  * Instant (<5 ms) natural-language commands in English & Indonesian:
    tasks with natural deadlines, quick notes, local events, focus/break/manual timers,
    Do Not Disturb, sleep/sound/visibility toggles, and panel navigation.
  * Context-aware 100% local companion chat (uses live tasks, screen time, active window,
    time of day, and favorite drink memory).
  * Strictly opt-in OpenAI-compatible AI fallback (e.g. local Ollama, LM Studio, or OpenAI)
    using only Python's standard library (`urllib.request`) — disabled by default.
"""
from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class CommandResult:
    kind: str                                   # action kind handled by PetController
    reply: str                                  # Morgana's companion reply (English)
    emotion: str = "neutral"                    # Morgana sprite expression
    voice: str = ""                             # Voice clip key ("" = auto)
    particle: str = ""                          # Particle effect name
    badge: str = ""                             # Short confirmation badge ("TASK ADDED", etc.)
    action_label: str = ""                      # Optional 1-click button label ("OPEN TASKS")
    action_target: str = ""                     # Optional 1-click jump target ("tasks", "notes", ...)
    payload: dict = field(default_factory=dict) # Structured arguments for execution


# ---------------------------------------------------------------------------
# Natural date/time extraction & Social Stat inference
# ---------------------------------------------------------------------------
_WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0, "senin": 0,
    "tuesday": 1, "tue": 1, "selasa": 1,
    "wednesday": 2, "wed": 2, "rabu": 2,
    "thursday": 3, "thu": 3, "kamis": 3,
    "friday": 4, "fri": 4, "jumat": 4,
    "saturday": 5, "sat": 5, "sabtu": 5,
    "sunday": 6, "sun": 6, "minggu": 6,
}

_REL_EN_RE = re.compile(
    r"\b(?:in\s+)?(?:(\d{1,3})\s*(?:h|hr|hrs|hour|hours)\s*(?:(\d{1,3})\s*(?:m|min|mins|minute|minutes))?"
    r"|(\d{1,3})\s*(?:m|min|mins|minute|minutes))\b",
    re.IGNORECASE,
)
_REL_ID_RE = re.compile(
    r"\b(?:(?:dalam\s+)?(\d{1,3})\s*(?:jam)\s*(?:(\d{1,3})\s*(?:menit|mnt))?(?:\s+lagi)?"
    r"|(?:dalam\s+)?(\d{1,3})\s*(?:menit|mnt)(?:\s+lagi)?)\b",
    re.IGNORECASE,
)
_TIME_EXPLICIT_RE = re.compile(
    r"(?:\b(?:at|jam|pukul)\s+|@)(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)
_TIME_BARE_AMPM_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_TIME_BARE_24H_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_DAY_WORD_RE = re.compile(
    r"\b(tomorrow|besok|today|hari\s+ini|tonight|malam\s+ini|"
    + "|".join(sorted(_WEEKDAYS.keys(), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

_STAT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("knowledge", (
        "study", "learn", "read", "book", "lecture", "exam", "quiz", "research", "paper",
        "thesis", "course", "docs", "article", "math", "physics", "history", "algorithm",
        "belajar", "baca", "buku", "kuliah", "ujian", "tugas kuliah", "riset", "skripsi",
    )),
    ("guts", (
        "gym", "workout", "run", "jog", "exercise", "pushup", "dentist", "doctor", "pitch",
        "present", "presentation", "speech", "interview", "difficult", "hard", "cold",
        "olahraga", "lari", "presentasi", "dokter",
    )),
    ("charm", (
        "call", "meet", "meeting", "sync", "email", "reply", "message", "chat", "network",
        "coffee with", "lunch with", "hangout", "client", "team", "social", "post", "publish",
        "rapat", "telepon", "balas", "kirim email", "ketemu",
    )),
    ("kindness", (
        "help", "clean", "tidy", "cook", "family", "mom", "dad", "friend", "gift", "water",
        "plant", "pet", "care", "donate", "thank", "laundry", "groceries",
        "bantu", "bersih", "masak", "keluarga", "ibu", "ayah", "teman", "hadiah",
    )),
)


def guess_task_stat(title: str) -> str:
    """Infer a sensible Persona 5 Social Stat category from a task title."""
    low = (title or "").lower()
    for stat, keywords in _STAT_KEYWORDS:
        if any(kw in low for kw in keywords):
            return stat
    return "proficiency"


def parse_natural_datetime(text: str, now: datetime | None = None) -> tuple[str, datetime | None]:
    """Extract a natural date/time expression from `text`, returning `(cleaned_text, dt_or_None)`.

    Examples:
      * ``"review PR in 30m"`` -> ``("review PR", now + 30m)``
      * ``"team sync tomorrow at 10:30"`` -> ``("team sync", tomorrow 10:30)``
      * ``"meeting klien jam 15:00"`` -> ``("meeting klien", today/tomorrow 15:00)``
    """
    cur = (now or datetime.now()).replace(second=0, microsecond=0)
    work = " " + (text or "").strip() + " "

    # 1) Relative offsets (`in 30m`, `in 1h 20m`, `dalam 45 menit`, `20 menit lagi`)
    for rx in (_REL_EN_RE, _REL_ID_RE):
        # Require either `in`/`dalam`/`lagi` cue or match near start/end so bare numbers in titles aren't eaten
        for m in rx.finditer(work):
            span = m.group(0).lower()
            has_cue = any(w in span for w in ("in ", "dalam ", "lagi"))
            near_edge = (m.start() <= 2) or (m.end() >= len(work) - 2)
            if not (has_cue or near_edge):
                continue
            hrs = int(m.group(1) or 0)
            mins = int(m.group(2) or m.group(3) or 0)
            total_m = hrs * 60 + mins
            if total_m > 0:
                dt = cur + timedelta(minutes=total_m)
                cleaned = (work[:m.start()] + " " + work[m.end():]).strip()
                cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-:")
                return cleaned, dt

    # 2) Day anchor (`tomorrow`, `besok`, `today`, `tonight`, weekday)
    target_date = None
    explicit_today = False
    tonight_default = False
    dm = _DAY_WORD_RE.search(work)
    if dm:
        token = re.sub(r"\s+", " ", dm.group(1).lower())
        if token in ("tomorrow", "besok"):
            target_date = cur.date() + timedelta(days=1)
        elif token in ("today", "hari ini"):
            target_date = cur.date()
            explicit_today = True
        elif token in ("tonight", "malam ini"):
            target_date = cur.date()
            explicit_today = True
            tonight_default = True
        elif token in _WEEKDAYS:
            want_wd = _WEEKDAYS[token]
            diff = (want_wd - cur.weekday()) % 7
            if diff == 0:
                diff = 7
            target_date = cur.date() + timedelta(days=diff)
        work = work[:dm.start()] + " " + work[dm.end():]

    # 3) Explicit time (`at 15:00`, `jam 14.30`, `3:30pm`, `16:00`)
    parsed_time: dtime | None = None
    tm = _TIME_EXPLICIT_RE.search(work)
    if tm:
        hh = int(tm.group(1))
        mm = int(tm.group(2) or 0)
        ampm = (tm.group(3) or "").lower()
        if ampm == "pm" and hh < 12:
            hh += 12
        elif ampm == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            parsed_time = dtime(hh, mm)
            work = work[:tm.start()] + " " + work[tm.end():]
    else:
        tm2 = _TIME_BARE_AMPM_RE.search(work)
        if tm2:
            hh = int(tm2.group(1))
            mm = int(tm2.group(2) or 0)
            ampm = tm2.group(3).lower()
            if ampm == "pm" and hh < 12:
                hh += 12
            elif ampm == "am" and hh == 12:
                hh = 0
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                parsed_time = dtime(hh, mm)
                work = work[:tm2.start()] + " " + work[tm2.end():]
        else:
            tm3 = _TIME_BARE_24H_RE.search(work)
            if tm3:
                hh, mm = int(tm3.group(1)), int(tm3.group(2))
                parsed_time = dtime(hh, mm)
                work = work[:tm3.start()] + " " + work[tm3.end():]

    cleaned = re.sub(r"\s{2,}", " ", work).strip(" ,.-:")
    if target_date is None and parsed_time is None:
        return cleaned, None

    if target_date is None:
        dt = datetime.combine(cur.date(), parsed_time)  # type: ignore[arg-type]
        if dt <= cur and not explicit_today:
            dt += timedelta(days=1)
        return cleaned, dt

    if parsed_time is None:
        parsed_time = dtime(20, 0) if tonight_default else dtime(9, 0)
    return cleaned, datetime.combine(target_date, parsed_time)


# ---------------------------------------------------------------------------
# Command & Conversational Router
# ---------------------------------------------------------------------------
_TASK_PREFIX_RE = re.compile(
    r"^(?:/?task|/?todo|add\s+task|remind\s+me\s+to|remind\s+me|remind|"
    r"tambah\s+tugas|tugas|ingetin(?:\s+aku)?|ingatkan(?:\s+aku)?)\s+(.+)$",
    re.IGNORECASE,
)
_NOTE_PREFIX_RE = re.compile(
    r"^(?:/?note|/?jot|/?memo|add\s+note|catat|catatan)\s+(.+)$",
    re.IGNORECASE,
)
_EVENT_PREFIX_RE = re.compile(
    r"^(?:/?event|/?schedule|add\s+event|meeting|jadwal|acara)\s+(.+)$",
    re.IGNORECASE,
)
_FOCUS_RE = re.compile(
    r"^(?:start\s+)?(?:/?focus|/?pomodoro|/?pomo|fokus)(?:\s+(\d{1,3})\s*(?:m|min|mins|menit)?)?$",
    re.IGNORECASE,
)
_BREAK_RE = re.compile(
    r"^(?:start\s+|take\s+(?:a\s+)?)?(?:/?break|/?rest|istirahat)(?:\s+(\d{1,2})\s*(?:m|min|mins|menit)?)?$",
    re.IGNORECASE,
)
_TIMER_RE = re.compile(
    r"^(?:/?timer|countdown|set\s+timer)\s+(\d{1,3})\s*(s|sec|secs|m|min|mins|menit|h|hr|hrs|jam)?(?:\s+(.+))?$",
    re.IGNORECASE,
)

_NAV_MAP: dict[str, tuple[str, str, str]] = {
    "tasks": ("tasks", "Opening Tasks! Let's check what's on the board.", "OPENING TASKS"),
    "todo": ("tasks", "Opening Tasks! Let's check what's on the board.", "OPENING TASKS"),
    "notes": ("notes", "Opening Notes! Your notebook is ready.", "OPENING NOTES"),
    "notebook": ("notes", "Opening Notes! Your notebook is ready.", "OPENING NOTES"),
    "events": ("events", "Opening Events! Let's look over your schedule.", "OPENING EVENTS"),
    "calendar": ("events", "Opening Events! Let's look over your schedule.", "OPENING EVENTS"),
    "schedule": ("events", "Opening Events! Let's look over your schedule.", "OPENING EVENTS"),
    "stats": ("stats", "Opening Social Stats & Habits!", "OPENING STATS"),
    "habits": ("stats", "Opening Social Stats & Habits!", "OPENING STATS"),
    "timer": ("timer", "Opening Timer & Focus!", "OPENING TIMER"),
    "briefing": ("briefing", "Pulling up your Daily Briefing right now!", "DAILY BRIEFING"),
    "morning": ("briefing", "Pulling up your Daily Briefing right now!", "DAILY BRIEFING"),
    "wrapup": ("wrapup", "Let's wrap up today and review how far you've come!", "EVENING WRAP-UP"),
    "wrap-up": ("wrapup", "Let's wrap up today and review how far you've come!", "EVENING WRAP-UP"),
    "wrap up": ("wrapup", "Let's wrap up today and review how far you've come!", "EVENING WRAP-UP"),
    "recap": ("wrapup", "Let's wrap up today and review how far you've come!", "EVENING WRAP-UP"),
    "evening": ("wrapup", "Let's wrap up today and review how far you've come!", "EVENING WRAP-UP"),
    "talk": ("talk", "Sure thing—let's chat!", "STARTING TALK"),
    "settings": ("settings", "Opening Settings!", "OPENING SETTINGS"),
}


def _guess_note_category(text: str) -> str:
    low = text.lower()
    if "#study" in low or any(w in low for w in ("study", "lecture", "chapter", "belajar")):
        return "Study"
    if "#work" in low or any(w in low for w in ("meeting", "client", "sprint", "deploy", "kerja")):
        return "Work"
    if "#idea" in low or "#ideas" in low or any(w in low for w in ("idea:", "brainstorm", "ide ")):
        return "Ideas"
    if "#journal" in low or "#wrapup" in low:
        return "Journal"
    return "Personal"


def _status_reply(ctx: dict) -> CommandResult:
    pending = int(ctx.get("pending_tasks", 0))
    overdue = int(ctx.get("overdue_count", 0))
    scr = ctx.get("screen_time_text") or "0m"
    cont = ctx.get("continuous_time_text") or "0m"
    h_done = int(ctx.get("habits_done", 0))
    h_tot = int(ctx.get("habits_total", 0))
    top_task = ctx.get("top_task") or ""
    nxt_ev = ctx.get("next_event_title") or ""
    nxt_dl = ctx.get("next_event_delta") or ""

    parts: list[str] = []
    if overdue:
        parts.append(f"{pending} pending tasks ({overdue} overdue — start with '{top_task[:34]}')")
    elif pending:
        parts.append(f"{pending} pending {'task' if pending == 1 else 'tasks'} (top: '{top_task[:34]}')")
    else:
        parts.append("all tasks cleared")
    if h_tot:
        parts.append(f"{h_done}/{h_tot} daily habits done")
    parts.append(f"{scr} active screen time ({cont} nonstop)")
    if nxt_ev and nxt_dl:
        parts.append(f"next up: '{nxt_ev[:28]}' in {nxt_dl}")

    reply = "Here's your live snapshot: " + "; ".join(parts) + "."
    return CommandResult(
        kind="status_query",
        reply=reply,
        emotion="smug" if pending == 0 else ("worried" if overdue else "happy"),
        voice="hmm",
        badge="LIVE STATUS",
        action_label="OPEN BRIEFING",
        action_target="briefing",
    )


def _local_chat_reply(raw: str, ctx: dict) -> CommandResult:
    """Rich, context-aware 100% local Morgana companion reply for free-form chat."""
    low = raw.lower().strip()
    app_phrase = ctx.get("active_app_phrase") or ""
    pending = int(ctx.get("pending_tasks", 0))
    top_task = ctx.get("top_task") or ""
    scr = ctx.get("screen_time_text") or "0m"
    cont = ctx.get("continuous_time_text") or "0m"
    drink = ctx.get("favorite_drink") or "water or coffee"
    hour = int(ctx.get("hour", 12))

    # 1) Classic "not a cat" joke
    if re.search(r"\b(cat|kitty|meow|kucing|meong|neko)\b", low):
        return CommandResult(
            kind="chat",
            reply=random.choice([
                "Hey! For the last time, I am NOT a cat! I'm Morgana—your sharpest desktop companion!",
                "Did you just call me a cat?! Watch it! I'm a dignified partner who keeps your whole schedule running!",
            ]),
            emotion="angry",
            voice="hoi",
            particle="anger",
            badge="NOT A CAT",
        )

    # 2) Tired / exhausted / sleepy
    if re.search(r"\b(tired|exhausted|sleepy|drained|capek|cape|lelah|ngantuk|letih)\b", low):
        extra = f" You've already logged {scr} today ({cont} nonstop)." if scr not in ("0m", "<1m") else ""
        return CommandResult(
            kind="chat",
            reply=(
                f"Hey, go easy on yourself!{extra} Take a quick 5-minute break, stretch your shoulders, "
                f"and grab some {drink}. Type 'break 5' if you want me to time it!"
            ),
            emotion="worried",
            voice="naa",
            particle="heart",
            badge="TAKE A BREATHER",
            action_label="5M BREAK",
            action_target="break_5",
        )

    # 3) Stressed / overwhelmed / anxious / burned out
    if re.search(r"\b(stress\w*|anxious|overwhelm\w*|burnout|burned\s+out|panic\w*|stres|pusing|kewalahan)\b", low):
        if top_task:
            focus_tip = f"Ignore the whole list for a minute—just do 10 small minutes on '{top_task[:34]}' or move the rest to tomorrow."
        else:
            focus_tip = "Breathe in slow, unclench your jaw, and pick just ONE tiny step—nothing else matters right now."
        return CommandResult(
            kind="chat",
            reply=f"Deep breath—I've got your back. {focus_tip}",
            emotion="worried",
            voice="naa",
            particle="heart",
            badge="ONE STEP AT A TIME",
            action_label="EVENING WRAP-UP" if hour >= 19 else "OPEN TASKS",
            action_target="wrapup" if hour >= 19 else "tasks",
        )

    # 4) Stuck / bored / procrastinating
    if re.search(r"\b(bored|stuck|procrastinat\w*|lazy|unmotivated|bosan|bosen|males|malas|buntu)\b", low):
        ctx_bit = f" while looking at {app_phrase}" if app_phrase else ""
        return CommandResult(
            kind="chat",
            reply=(
                f"Feeling stuck{ctx_bit}? Don't aim for perfection—start a tiny 10-minute focus sprint "
                "just to break the ice! Type 'focus 10' and let's roll."
            ),
            emotion="smug",
            voice="dousuru",
            particle="sparkle",
            badge="QUICK SPARK",
            action_label="FOCUS 10M",
            action_target="focus_10",
        )

    # 5) Happy / proud / accomplished / finished
    if re.search(r"\b(finished|did\s+it|yay|happy|proud|awesome|great|berhasil|selesai|senang)\b|done!", low):
        return CommandResult(
            kind="chat",
            reply=random.choice([
                "Ooh, look at you go! That's the spirit—make sure you log it in Tasks or save a quick win in your Journal!",
                "Nice work!! I knew you had it in you. Keep that momentum—or reward yourself with a short breather!",
            ]),
            emotion="excited",
            voice="oh_excited",
            particle="music",
            badge="AWESOME!",
            action_label="OPEN STATS",
            action_target="stats",
        )

    # 6) Hungry / coffee / drink
    if re.search(r"\b(hungry|food|eat|eating|coffee|tea|water|thirsty|lapar|laper|makan|kopi|haus|minum)\b", low):
        return CommandResult(
            kind="chat",
            reply=f"Fuel check! Step away from the screen for a real snack and some {drink}—your brain runs way better refueled!",
            emotion="wink",
            voice="hmm",
            particle="heart",
            badge="FUEL CHECK",
        )

    # 7) Gratitude
    if re.search(r"\b(thanks|thank\s+you|thx|ty|makasih|terima\s+kasih|appreciate)\b", low):
        return CommandResult(
            kind="chat",
            reply=random.choice([
                "Heh, anytime! That's what a top-tier companion is for.",
                "Don't mention it! We make a pretty unstoppable team, huh?",
            ]),
            emotion="smug",
            voice="oh_excited",
            particle="heart",
            badge="ANYTIME",
        )

    # 8) Greetings / time-of-day check-in
    if re.search(r"\b(hi|hello|hey|yo|halo|hai|pagi|siang|sore|malam|good\s+morning|good\s+evening|good\s+night)\b", low):
        if re.search(r"\b(night|malam|sleep|tidur)\b", low):
            return CommandResult(
                kind="chat",
                reply="Winding down for the night? Let's do a quick Evening Wrap-up so tomorrow starts stress-free!",
                emotion="sad",
                voice="yareyare",
                particle="zzz",
                badge="GOOD NIGHT",
                action_label="EVENING WRAP-UP",
                action_target="wrapup",
            )
        if hour < 12:
            greet = "Morning! Ready to make today count?"
        elif hour < 18:
            greet = "Hey there! How's your afternoon shaping up?"
        else:
            greet = "Good evening! Still cruising, or getting ready to wrap up?"
        tail = f" You've got {pending} pending {'task' if pending == 1 else 'tasks'} waiting." if pending else " Your task list is totally clear!"
        return CommandResult(
            kind="chat",
            reply=greet + tail,
            emotion="wink",
            voice="hoi",
            particle="sparkle",
            badge="HEY THERE",
        )

    # 9) What should I do / advice / next step
    if re.search(r"\b(what\s+should\s+i\s+do|what\s+next|what\s+to\s+do|help\s+me\s+focus|what'?s\s+the\s+plan|harus\s+ngapain|bingung\s+mulai|bingung)\b", low):
        if top_task:
            return CommandResult(
                kind="chat",
                reply=f"Start with '{top_task[:42]}'—kick off a 25-minute Focus sprint ('focus 25') and knock it out cleanly!",
                emotion="smug",
                voice="dousuru",
                particle="sparkle",
                badge="NEXT MOVE",
                action_label="START FOCUS",
                action_target="focus_25",
            )
        return CommandResult(
            kind="chat",
            reply="Your task list is wide open! Type 'task <goal>' to add one, or 'note <thought>' to capture an idea.",
            emotion="wink",
            voice="hmm",
            badge="OPEN SLATE",
        )

    # 10) General supportive fallback grounded in live context
    if app_phrase:
        reply = (
            f"Gotcha! I see you're over in {app_phrase}. Want me to save that as a note ('note ...'), "
            "turn it into a task ('task ...'), or start a Focus timer ('focus 25')?"
        )
    elif pending:
        reply = (
            f"I'm right here with you! You've got {pending} pending {'task' if pending == 1 else 'tasks'} "
            f"and {scr} logged today. Type 'task ...', 'note ...', 'focus 25', or 'help' anytime!"
        )
    else:
        reply = (
            "I hear you! Remember you can type commands like 'task ...', 'note ...', 'event ...', "
            "'focus 25', 'dnd', or 'wrapup'—or just check in whenever you want company."
        )
    return CommandResult(
        kind="chat",
        reply=reply,
        emotion="wink",
        voice="hmm",
        badge="MORGANA",
    )


def parse_and_plan(raw_text: str, ctx: dict | None = None,
                   now: datetime | None = None, ai_enabled: bool = False) -> CommandResult:
    """Parse user input from the Ask & Command bar into a structured `CommandResult`."""
    ctx = ctx or {}
    cur = now or datetime.now()
    text = (raw_text or "").strip()
    if not text:
        return CommandResult(
            kind="noop",
            reply="Type a command like 'task review PR at 15:00', 'note idea #work', 'focus 25', or just chat with me!",
            emotion="neutral",
            badge="READY",
        )
    low = text.lower().strip()

    # 1) Help cheatsheet & chat reset
    if low in ("help", "/help", "?", "commands"):
        return CommandResult(
            kind="help",
            reply=(
                "Quick commands: 'task <title> [at 15:00 / tomorrow / in 30m]' · "
                "'note <text> [#tag]' · 'event <title> at 16:00' · 'focus 25' · 'break 5' · "
                "'timer 10m tea' · 'dnd on/off' · 'status' · 'briefing' · 'wrapup' · 'clear chat' · or just talk!"
            ),
            emotion="wink",
            voice="hmm",
            badge="COMMANDS",
        )
    if low in ("clear chat", "/clear", "reset chat", "new chat"):
        return CommandResult(
            kind="clear_chat",
            reply="Fresh slate! What's our next move?",
            emotion="wink",
            voice="hmm",
            badge="CHAT CLEARED",
        )

    # 2) Quick Task / Reminder
    m_task = _TASK_PREFIX_RE.match(text)
    if m_task:
        body = m_task.group(1).strip()
        high = False
        if re.search(r"(?:^|\s)(?:!high|#high|urgent|penting)\b|!$", body, re.IGNORECASE):
            high = True
            body = re.sub(r"(?:^|\s)(?:!high|#high|urgent|penting)\b", "", body, flags=re.IGNORECASE).rstrip("! ").strip()
        title, dl = parse_natural_datetime(body, now=cur)
        title = title or body
        stat = guess_task_stat(title)
        when_s = f" (due {dl:%d %b %H:%M})" if dl else ""
        return CommandResult(
            kind="add_task",
            reply=f"Added to Tasks: '{title[:52]}'{when_s}!",
            emotion="wink",
            voice="oh_excited",
            particle="sparkle",
            badge="TASK ADDED",
            action_label="OPEN TASKS",
            action_target="tasks",
            payload={"title": title[:140], "deadline": dl, "priority": "high" if high else "normal", "stat": stat},
        )

    # 3) Quick Note
    m_note = _NOTE_PREFIX_RE.match(text)
    if m_note:
        body = m_note.group(1).strip()
        cat = _guess_note_category(body)
        first_line = body.splitlines()[0].strip()
        title = (first_line[:44].rstrip() + ("…" if len(first_line) > 44 else "")) if len(body) > 44 else first_line
        return CommandResult(
            kind="add_note",
            reply=f"Jotted down in {cat} Notes: '{title}'!",
            emotion="wink",
            voice="oh_excited",
            particle="sparkle",
            badge="NOTE SAVED",
            action_label="OPEN NOTES",
            action_target="notes",
            payload={"title": title, "content": body, "category": cat},
        )

    # 4) Quick Local Event
    m_ev = _EVENT_PREFIX_RE.match(text)
    if m_ev:
        body = m_ev.group(1).strip()
        title, dt = parse_natural_datetime(body, now=cur)
        title = title or body
        if dt is None:
            # Default cleanly to the next rounded hour
            dt = (cur + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return CommandResult(
            kind="add_event",
            reply=f"Scheduled '{title[:48]}' for {dt:%a %d %b at %H:%M}!",
            emotion="wink",
            voice="oh_excited",
            particle="sparkle",
            badge="EVENT ADDED",
            action_label="OPEN EVENTS",
            action_target="events",
            payload={"title": title[:120], "start": dt},
        )

    # 5) Focus / Break / Manual Timer / Stop Timer
    if low in ("stop timer", "stop focus", "pause timer", "cancel focus", "berhenti fokus"):
        return CommandResult(
            kind="stop_timer",
            reply="Timer stopped! Take a breath whenever you need.",
            emotion="neutral",
            badge="TIMER STOPPED",
        )

    m_foc = _FOCUS_RE.match(text)
    if m_foc:
        mins = int(m_foc.group(1)) if m_foc.group(1) else 0
        mins = max(1, min(180, mins)) if mins else 0
        lbl = f"{mins}-minute" if mins else "your default"
        return CommandResult(
            kind="start_focus",
            reply=f"Locking in! Starting {lbl} Focus sprint right now!",
            emotion="excited",
            voice="uwooh",
            particle="sparkle",
            badge="FOCUS STARTED",
            action_label="OPEN TIMER",
            action_target="timer",
            payload={"minutes": mins},
        )

    m_brk = _BREAK_RE.match(text)
    if m_brk:
        mins = int(m_brk.group(1)) if m_brk.group(1) else 5
        mins = max(1, min(60, mins))
        return CommandResult(
            kind="start_break",
            reply=f"{mins}-minute break timer started! Step back, stretch, and hydrate!",
            emotion="wink",
            voice="hmm",
            particle="heart",
            badge=f"{mins}M BREAK",
            payload={"minutes": mins},
        )

    m_tm = _TIMER_RE.match(text)
    if m_tm:
        qty = int(m_tm.group(1))
        unit = (m_tm.group(2) or "m").lower()
        label = (m_tm.group(3) or "Quick Timer").strip()
        if unit.startswith("s"):
            secs = max(5, min(86400, qty))
        elif unit.startswith(("h", "j")):
            secs = max(60, min(86400, qty * 3600))
        else:
            secs = max(60, min(86400, qty * 60))
        return CommandResult(
            kind="start_timer",
            reply=f"Countdown set for '{label[:36]}'!",
            emotion="smug",
            voice="oh_excited",
            particle="sparkle",
            badge="TIMER SET",
            payload={"seconds": secs, "label": label[:60]},
        )

    # 6) DND / Sound / Sleep / Show-Hide toggles
    if re.match(r"^(?:/?dnd|do\s+not\s+disturb|quiet(?:\s+mode)?)(?:\s+(on|off|toggle))?$", low):
        parts = low.split()
        arg = parts[-1] if parts[-1] in ("on", "off") else "toggle"
        state = True if arg == "on" else (False if arg == "off" else None)
        return CommandResult(
            kind="toggle_dnd",
            reply="Do Not Disturb updated!" if state is None else (
                "Do Not Disturb is ON. I'll stay completely quiet until you turn it off."
                if state else "Do Not Disturb is OFF. I'm back online!"
            ),
            emotion="neutral" if state else "wink",
            badge="DND ON" if state else ("DND OFF" if state is False else "DND"),
            payload={"state": state},
        )

    if low in ("mute", "sound off", "quiet sound", "bisu"):
        return CommandResult(kind="toggle_sound", reply="Sounds muted!", badge="MUTED", payload={"state": False})
    if low in ("unmute", "sound on"):
        return CommandResult(kind="toggle_sound", reply="Sounds back on!", emotion="wink", badge="SOUND ON", payload={"state": True})
    if low in ("sleep", "sleep mode", "nap"):
        return CommandResult(kind="toggle_sleep", reply="Toggling Sleep Mode.", emotion="sad", badge="SLEEP", payload={"state": True})
    if low in ("wake", "wake up"):
        return CommandResult(kind="toggle_sleep", reply="I'm wide awake!", emotion="excited", badge="AWAKE", payload={"state": False})
    if low in ("hide", "hide morgana", "tray"):
        return CommandResult(kind="toggle_visibility", reply="Hiding to the system tray—summon me anytime!", badge="IN TRAY", payload={"visible": False})
    if low in ("show", "show morgana", "summon"):
        return CommandResult(kind="toggle_visibility", reply="Reporting for duty!", emotion="excited", badge="VISIBLE", payload={"visible": True})

    # 7) Direct Panel Navigation (`open notes`, `wrapup`, `briefing`, `tasks`, ...)
    nav_clean = re.sub(r"^(?:open|show|go\s+to|buka)\s+", "", low).lstrip("/").strip()
    if nav_clean in _NAV_MAP:
        target, reply, badge = _NAV_MAP[nav_clean]
        return CommandResult(kind="open_panel", reply=reply, emotion="wink", badge=badge, payload={"target": target})

    # 8) Live Workspace Status Queries
    if any(k in low for k in (
        "status", "how's my day", "how is my day", "how am i doing", "screen time", "screentime",
        "pending tasks", "my tasks", "my stats", "summary", "ringkasan", "berapa tugas",
    )):
        return _status_reply(ctx)

    # 9) Free-form companion chat (local engine, or opt-in Gemini/OpenAI with instant local fallback)
    local = _local_chat_reply(text, ctx)
    if ai_enabled:
        return CommandResult(
            kind="needs_ai",
            reply=local.reply,
            emotion=local.emotion,
            voice=local.voice,
            particle=local.particle,
            badge="GEMINI · MORGANA",
            action_label=local.action_label,
            action_target=local.action_target,
            payload={"prompt": text, "fallback": local},
        )
    return local


# ---------------------------------------------------------------------------
# Backwards-compatible wrapper delegating to `gemini_ai.py`
# ---------------------------------------------------------------------------
def query_ai_endpoint(prompt: str, ctx: dict, endpoint: str, model: str,
                      api_key: str = "", timeout: float = 14.0,
                      provider: str = "openai", call_user: str = "Joker") -> tuple[bool, str]:
    """Backwards-compatible wrapper around `gemini_ai.ask_morgana_chat`."""
    from gemini_ai import AIConfig, ask_morgana_chat

    cfg = AIConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        endpoint=endpoint,
        call_user=call_user,
    )
    res = ask_morgana_chat(prompt, ctx, cfg, timeout=timeout)
    return (True, res.text) if res.ok else (False, res.error or "AI request failed")
