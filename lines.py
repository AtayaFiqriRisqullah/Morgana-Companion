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
        "Hey! Still staring at that screen? Lights out—go to bed!",
        "Good grief, your eyes are getting heavy. Close your apps and hit the pillow!",
        "Everything else can wait until tomorrow. Seriously, go get some sleep!",
        "I'm not letting this slide—it's way past bedtime! Time to rest!",
        "A sharp mind needs proper rest. Wrap it up and lie down already!",
        "Yawn... Even I'm getting sleepy watching you stay up this late. Bedtime!",
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


def _clip(text: str, n: int = 34) -> str:
    s = (text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


class ContextualDialogue:
    """Qt-free contextual Morgana dialogue generator.

    Produces `(text, emotion, voice)` triples tailored to the user's live state
    (bedtime, active timers, tasks, notes, calendar events, daily stats blocks,
    time of day, and enabled radial tabs) while rotating naturally across all 8
    Morgana voicelines without immediate repeats.
    """

    def __init__(self) -> None:
        self._last: dict[str, str] = {}

    def _pick(self, slot: str, options: list[tuple[str, str, str]]) -> tuple[str, str, str]:
        if not options:
            return ("What's our next move?", "neutral", "dousuru")
        last = self._last.get(slot)
        pool = [o for o in options if o[0] != last] if len(options) > 1 else options
        choice = random.choice(pool or options)
        self._last[slot] = choice[0]
        return choice

    def radial_open(self, ctx: dict) -> tuple[str, str, str]:
        """Relevant line when the user opens Morgana's radial menu."""
        tabs = set(ctx.get("enabled_tabs") or ())
        hour = int(ctx.get("hour", 12))

        if ctx.get("curfew_active"):
            return self._pick("radial_open_bed", [
                ("Hey! It's past bedtime—what are you opening menus for? Go get some sleep!", "angry", "hoi"),
                ("Good grief, you're still awake? Only check what you must, then hit the bed!", "sad", "yareyare"),
                ("You're going to wear yourself out! Wrap up quick and call it a night.", "worried", "yareyare"),
                ("Oi, it's late! Don't tell me you're starting a whole new project right now...", "angry", "hoi"),
            ])

        if ctx.get("sleep_mode"):
            return self._pick("radial_open_sleep", [
                ("Mm? Need something while I'm napping? Go ahead and pick a menu.", "sad", "oh_asking"),
                ("Oh? Waking me up? Let me know what you need!", "neutral", "oh_asking"),
                ("Still on duty even in Sleep Mode—what's the plan?", "wink", "dousuru"),
            ])

        pool: list[tuple[str, str, str]] = []

        # Live timer context
        if ctx.get("focus_running"):
            rem = ctx.get("focus_rem", "")
            if ctx.get("focus_phase") == "rest":
                pool.append((f"Break time! {rem} left to recharge—what do you want to check?", "wink", "hmm"))
                pool.append((f"Enjoying your breather? {rem} left before we lock back in!", "neutral", "naa"))
            else:
                pool.append((f"Focus session is rolling with {rem} left! Need a quick look at something?", "smug", "hmm"))
                pool.append((f"Stay sharp—{rem} left on the focus clock. What's the move?", "serious", "dousuru"))
        elif ctx.get("manual_nearest_label"):
            lbl = _clip(ctx["manual_nearest_label"], 28)
            rem = ctx.get("manual_nearest_rem", "")
            pool.append((f"Your '{lbl}' timer has {rem} left! Anything else on your mind?", "neutral", "naa"))

        # Live calendar event context (only if Events tab is enabled)
        if "events" in tabs:
            if ctx.get("ongoing_event_title"):
                title = _clip(ctx["ongoing_event_title"], 30)
                pool.append((f"Hey, '{title}' is happening right now! Need to check Events?", "shout", "hoi"))
            elif ctx.get("next_event_title"):
                title = _clip(ctx["next_event_title"], 28)
                delta = ctx.get("next_event_delta", "")
                pool.append((f"Heads up—'{title}' starts in {delta}! Want to peek at Events?", "worried", "naa"))
                pool.append((f"Don't forget '{title}' coming up in {delta}. How should we play this?", "serious", "dousuru"))

        # Live tasks context (only if Tasks tab is enabled)
        pending = int(ctx.get("pending_tasks", 0))
        if "tasks" in tabs and pending > 0:
            top = _clip(ctx.get("top_task", ""), 28)
            word = "task" if pending == 1 else "tasks"
            if ctx.get("high_tasks", 0) > 0 and top:
                pool.append((f"'{top}' is marked high priority! Ready to knock it out in Tasks?", "serious", "hoi"))
            elif top:
                pool.append((f"You've got {pending} pending {word}—maybe tackle '{top}' next?", "neutral", "dousuru"))
                pool.append((f"Hey, '{top}' is still waiting on your list. What's the plan?", "neutral", "naa"))

        # Live due notes context (only if Notes tab is enabled)
        due_notes = int(ctx.get("due_notes", 0))
        if "notes" in tabs and due_notes > 0:
            word = "note" if due_notes == 1 else "notes"
            pool.append((f"You have {due_notes} {word} with an active deadline—want to check Notes?", "worried", "oh_asking"))

        # Live Social Stats & Daily Habits context (only if Stats tab is enabled)
        blocks = int(ctx.get("today_blocks", 0))
        h_done = int(ctx.get("habits_done", 0))
        h_total = int(ctx.get("habits_total", 0))
        if "stats" in tabs:
            if h_total > 0 and h_done < h_total:
                left = h_total - h_done
                hw = "habit" if left == 1 else "habits"
                pool.append((f"You still have {left} daily {hw} left today ({h_done}/{h_total})! Want to check Stats?", "neutral", "naa"))
            elif h_total > 0 and h_done >= h_total:
                pool.append((f"All {h_total} daily habits cleared today! Your Social Stats are thriving!", "excited", "oh_excited"))
            if blocks >= 3:
                pool.append((f"Whoa, {blocks} blocks logged today! Look at you leveling up!", "excited", "oh_excited"))
            elif blocks == 0 and hour >= 13:
                pool.append(("Haven't logged a Social Stat block yet today! What have you been training?", "neutral", "oh_asking"))
            elif blocks > 0:
                pool.append((f"Nice—{blocks}/3 time blocks logged today! Going for another stat boost?", "smug", "hmm"))

        # Time-of-day conversational variety (so even with empty lists, Morgana feels fresh!)
        if 5 <= hour < 12:
            pool.extend([
                ("Fresh morning! What's our first move today?", "wink", "dousuru"),
                ("Ooh, ready to kick off the day? Pick a tab!", "excited", "oh_excited"),
                ("Hey hey, got a plan for this morning?", "neutral", "naa"),
            ])
        elif 12 <= hour < 18:
            pool.extend([
                ("Afternoon check-in! What are we tackling next?", "neutral", "dousuru"),
                ("Oh? Need something? Let's see what's on the board!", "neutral", "oh_asking"),
                ("Keeping up a good pace today—where to next?", "smug", "hmm"),
            ])
        elif 18 <= hour < 23:
            pool.extend([
                ("Evening already! Checking what's left before you wind down?", "neutral", "oh_asking"),
                ("Hey, let's review your evening game plan!", "neutral", "naa"),
                ("Alright, what's the next move for tonight?", "smug", "dousuru"),
            ])
        else:
            pool.extend([
                ("Burning the midnight oil? Don't push yourself too hard!", "sad", "yareyare"),
                ("Oh, still up? Pick what you need and remember to rest!", "worried", "oh_asking"),
            ])

        return self._pick("radial_open", pool)

    def radial_close(self, ctx: dict) -> tuple[str, str, str]:
        """Short natural line when the user closes the radial menu without opening a window."""
        if ctx.get("curfew_active"):
            return self._pick("radial_close_bed", [
                ("Good call—now close your eyes and get some sleep!", "wink", "hmm"),
                ("Alright, menu's closed! Now go hit the pillow already!", "sad", "yareyare"),
            ])
        if ctx.get("focus_running") and ctx.get("focus_phase") != "rest":
            return self._pick("radial_close_focus", [
                ("Back to the grind! I'll keep an eye on your focus clock.", "smug", "hmm"),
                ("Locked back in! Just tap me if you need a break.", "wink", "hmm"),
            ])
        return self._pick("radial_close", [
            ("Changed your mind? I'll be right here if you need me!", "wink", "hmm"),
            ("Alright then! Just tap me whenever you're ready.", "neutral", "hmm"),
            ("Nothing for now? Fair enough—take it at your own pace!", "wink", "naa"),
            ("Oh, all set? Holler if you need a hand!", "neutral", "oh_asking"),
            ("Gotcha! I'll keep watch over here.", "smug", "hmm"),
        ])

    def window_open(self, kind: str, ctx: dict) -> tuple[str, str, str]:
        """Relevant reaction when opening a specific dialog window or System menu."""
        if kind == "tasks":
            pending = int(ctx.get("pending_tasks", 0))
            total = int(ctx.get("total_tasks", pending))
            top = _clip(ctx.get("top_task", ""), 30)
            if total == 0:
                return self._pick("open_tasks_empty", [
                    ("Blank slate! Add a task and let's start knocking 'em down.", "neutral", "dousuru"),
                    ("No tasks on the board yet! What's the first goal?", "neutral", "oh_asking"),
                ])
            if pending == 0:
                return self._pick("open_tasks_clear", [
                    ("Whoa, your task list is completely clear! Awesome work!", "excited", "oh_excited"),
                    ("Not a single pending task left! You really crushed it!", "wink", "hmm"),
                ])
            word = "task" if pending == 1 else "tasks"
            opts = [
                (f"Let's check your tasks! {pending} {word} left—which one's first?", "neutral", "dousuru"),
                (f"Alright, {pending} pending {word} on the board. One step at a time!", "smug", "hmm"),
            ]
            if ctx.get("high_tasks", 0) > 0 and top:
                opts.append((f"'{top}' is high priority! Let's focus on that one!", "serious", "hoi"))
            elif top:
                opts.append((f"Hey, '{top}' is right up top—ready to cross it off?", "neutral", "naa"))
            return self._pick("open_tasks", opts)

        if kind == "notes":
            cnt = int(ctx.get("notes_count", 0))
            if cnt == 0:
                return self._pick("open_notes_empty", [
                    ("Fresh notebook! Catch any thought, add #tags, and preview in Markdown.", "wink", "naa"),
                    ("Blank canvas! Jot down ideas or study notes—everything auto-saves as you type.", "neutral", "hmm"),
                ])
            opts = [
                (f"Opening your notebook! {cnt} notes saved—ready to capture a new idea?", "neutral", "oh_asking"),
                ("Notebook's open! Use #tags to group thoughts or hit '→ TASK' on anything actionable.", "wink", "hmm"),
                (f"Let's review your {cnt} notes! Everything auto-saves right as you write.", "smug", "naa"),
            ]
            return self._pick("open_notes", opts)

        if kind == "events":
            ongoing = ctx.get("ongoing_event_title")
            nxt = ctx.get("next_event_title")
            delta = ctx.get("next_event_delta", "")
            week_cnt = int(ctx.get("week_events_count", 0))
            if ongoing:
                return (f"Hey, '{_clip(ongoing, 32)}' is happening right now! Check the schedule!", "shout", "hoi")
            if nxt:
                return self._pick("open_events_next", [
                    (f"Let's check the week! Next up is '{_clip(nxt, 30)}' in {delta}.", "worried", "naa"),
                    (f"Here's your 7-day calendar—'{_clip(nxt, 30)}' is coming up in {delta}!", "serious", "dousuru"),
                ])
            if week_cnt == 0:
                return self._pick("open_events_clear", [
                    ("Ooh, your 7-day schedule is wide open! Plenty of free time!", "excited", "oh_excited"),
                    ("Nothing on the calendar this week! Nice and peaceful.", "wink", "hmm"),
                ])
            return self._pick("open_events", [
                ("Here's your 7-day calendar! Let's plan the week ahead.", "smug", "dousuru"),
                ("Checking the schedule? Smart—staying ahead always pays off!", "wink", "hmm"),
            ])

        if kind == "stats":
            blocks = int(ctx.get("today_blocks", 0))
            if blocks >= 3:
                return self._pick("open_stats_full", [
                    (f"Whoa, {blocks} activities logged today already! Your gold star is shining!", "excited", "oh_excited"),
                    (f"All {blocks} entries in today! Let's check how close you are to the next rank!", "smug", "hmm"),
                ])
            if blocks == 0:
                return self._pick("open_stats_zero", [
                    ("Social Stats board is open! Check a Daily Habit or log an activity to grow your star!", "neutral", "dousuru"),
                    ("Let's see your Confidant star! Which stat are we leveling up today?", "neutral", "oh_asking"),
                ])
            return self._pick("open_stats", [
                (f"Social Stats time! {blocks} activities logged today—let's keep that star expanding!", "smug", "oh_asking"),
                (f"Nice momentum with {blocks} logs today! Ready to push for a Rank Up?", "wink", "hmm"),
            ])

        if kind == "finance":
            return self._pick("open_finance", [
                ("Finance ledger open! Let's keep those wallets and budgets sharp!", "smug", "oh_asking"),
                ("Checking your cash flow? Smart—Phantom Thieves always count their yen!", "wink", "hmm"),
            ])

        if kind == "timer":
            if ctx.get("focus_running"):
                rem = ctx.get("focus_rem", "")
                if ctx.get("focus_phase") == "rest":
                    return (f"Break timer is ticking with {rem} left—take it easy!", "wink", "hmm")
                return (f"Focus Mode is live with {rem} left! Keep that streak going!", "smug", "hmm")
            mcnt = int(ctx.get("manual_count", 0))
            if mcnt > 0:
                word = "timer" if mcnt == 1 else "timers"
                return (f"You've got {mcnt} manual {word} ticking! Need another reminder?", "neutral", "oh_asking")
            return self._pick("open_timer", [
                ("Ready to lock in? Start a Focus session or set a quick manual timer!", "smug", "dousuru"),
                ("Timer's up! Pick a Pomodoro block or set a custom countdown.", "neutral", "hmm"),
            ])

        if kind == "settings":
            return self._pick("open_settings", [
                ("Tuning the controls? Let's get everything set just how you like it!", "wink", "hmm"),
                ("Oh, tweaking Settings? Don't forget to hit Save when you're done!", "neutral", "oh_asking"),
                ("Customizing your setup? Toggle awareness, screen-time nudges, particles, and tabs here!", "neutral", "naa"),
            ])

        if kind == "system":
            return self._pick("open_system", [
                ("System menu's up! Need a chat, Briefing, CPU status, or Sleep Mode?", "neutral", "oh_asking"),
                ("Looking under the hood? Pick whatever you need!", "smug", "dousuru"),
            ])

        return ("Let's take a look!", "neutral", "hmm")

    def window_close(self, kind: str, ctx: dict) -> tuple[str, str, str]:
        """Short contextual reaction when the user closes a dialog window."""
        if ctx.get("curfew_active"):
            return self._pick("close_bed", [
                ("Done with that? Great—now seriously, go get some sleep!", "sad", "yareyare"),
                ("Window's closed! Now close your eyes too—it's bedtime!", "angry", "hoi"),
            ])

        if kind == "tasks":
            pending = int(ctx.get("pending_tasks", 0))
            total = int(ctx.get("total_tasks", pending))
            if total > 0 and pending == 0:
                return ("All tasks cleared! You're on fire today!", "excited", "oh_excited")
            word = "task" if pending == 1 else "tasks"
            return self._pick("close_tasks", [
                (f"Tasks tucked away! {pending} {word} left whenever you're ready." if pending else "Tasks closed! Let me know when you add a new goal.", "wink", "hmm"),
                ("Alright, back to action! Keep chipping away at that list.", "smug", "hmm"),
            ])

        if kind == "notes":
            return self._pick("close_notes", [
                ("Notebook safely tucked away! Every idea is auto-saved.", "wink", "hmm"),
                ("All thoughts captured! Tap NOTES anytime inspiration strikes.", "smug", "naa"),
            ])

        if kind == "events":
            return self._pick("close_events", [
                ("Schedule checked! Don't worry, I'll remind you before events start.", "wink", "hmm"),
                ("Got the week mapped out? Let's make it count!", "smug", "oh_excited"),
            ])

        if kind == "stats":
            return self._pick("close_stats", [
                ("Keep stacking those habits and wins—that gold star will be maxed in no time!", "smug", "hmm"),
                ("Nice work tracking your growth! Every point counts.", "wink", "hmm"),
            ])

        if kind == "finance":
            return self._pick("close_finance", [
                ("Books balanced! Tap FINANCE anytime you want to log a transaction.", "wink", "hmm"),
                ("Ledger safely tucked away—watch that budget!", "smug", "hmm"),
            ])

        if kind == "timer":
            if ctx.get("focus_running") or int(ctx.get("manual_count", 0)) > 0:
                return self._pick("close_timer_active", [
                    ("I've got the countdown right on my HUD—stay focused!", "smug", "hmm"),
                    ("Timer's ticking! I'll shout as soon as time's up.", "wink", "naa"),
                ])
            return self._pick("close_timer", [
                ("Timer closed! Pop it back open whenever you need a countdown.", "neutral", "hmm"),
                ("Alright, no clock running for now. Take a breather!", "wink", "hmm"),
            ])

        if kind == "settings":
            return self._pick("close_settings", [
                ("Settings closed! Let me know if you want to tweak anything else.", "wink", "hmm"),
                ("All tuned up! Ready when you are.", "smug", "hmm"),
            ])

        if kind == "briefing":
            return self._pick("close_briefing", [
                ("Briefing locked in! Let's make today count!", "excited", "oh_excited"),
                ("You've got the game plan—let's tackle the day together!", "smug", "hmm"),
            ])

        if kind == "wrapup":
            return self._pick("close_wrapup", [
                ("Day wrapped up cleanly! Get some real rest tonight—you earned it!", "wink", "hmm"),
                ("Tomorrow's board is all set. Recharge well tonight, partner!", "smug", "naa"),
            ])

        return ("Alright, all closed!", "neutral", "hmm")

    def task_completed(self, title: str, remaining: int, reward_note: str = "") -> tuple[str, str, str]:
        t = _clip(title, 32)
        suffix = f" ({reward_note})" if reward_note else ""
        if remaining == 0:
            return self._pick("task_done_all", [
                (f"Ooh, '{t}' was the last one{suffix}! Every single task is done—awesome work!", "excited", "oh_excited"),
                (f"Boom! '{t}' cleared{suffix}—your whole task list is spotless!", "excited", "oh_excited"),
            ])
        word = "more" if remaining == 1 else "left"
        return self._pick("task_done", [
            (f"Nice! '{t}' checked off{suffix}—{remaining} {word} to go!", "wink", "hmm"),
            (f"Whoa, look at that progress! '{t}' is done{suffix}!", "excited", "oh_excited"),
            (f"One down! '{t}' is off the board{suffix}—keep that momentum rolling!", "smug", "hmm"),
        ])

    def note_sent_to_task(self, title: str) -> tuple[str, str, str]:
        t = _clip(title, 34)
        return self._pick("note_to_task", [
            (f"Nice! Turned '{t}' into a real Task—now it's on the mission board!", "smug", "hmm"),
            (f"Idea -> Action! '{t}' is now queued up in Tasks!", "excited", "oh_excited"),
        ])

    def habit_completed(self, title: str, stat_label: str, pts: int, streak: int,
                        done_today: int, total_habits: int) -> tuple[str, str, str]:
        t = _clip(title, 30)
        s_txt = f" ({streak}-day streak!)" if streak >= 2 else ""
        if total_habits > 0 and done_today >= total_habits:
            return self._pick("habit_done_all", [
                (f"Whoa! '{t}' done (+{pts} {stat_label}){s_txt}—ALL daily habits cleared today!", "excited", "oh_excited"),
                (f"Every single daily habit finished! '{t}' (+{pts} {stat_label}){s_txt}—unstoppable!", "excited", "oh_excited"),
            ])
        return self._pick("habit_done", [
            (f"Nice habit check-in! '{t}' earned +{pts} {stat_label}{s_txt}!", "smug", "hmm"),
            (f"Consistency pays off! +{pts} {stat_label} for '{t}'{s_txt}!", "wink", "hmm"),
            (f"Ooh, '{t}' checked off—+{pts} {stat_label}{s_txt}! Keep the streak alive!", "excited", "oh_excited"),
        ])

    def app_awareness(self, category: str, app_name: str, ctx: dict) -> tuple[str, str, str, str]:
        """Context-aware remark when the user settles into an active app category.

        Returns `(text, emotion, voice, particle)`.
        """
        raw_phrase = str(ctx.get("active_app_phrase") or "").strip()
        app = _clip(raw_phrase or app_name or "that app", 38)
        overdue = int(ctx.get("overdue_count", 0))
        pending = int(ctx.get("pending_tasks", 0))
        hour = int(ctx.get("hour", 12))

        if category == "coding":
            return self._pick("app_coding", [
                (f"Back in {app}! Let's sharpen your Proficiency—holler if you want a Focus block!", "smug", "hmm", "sparkle"),
                (f"Ooh, coding time in {app}! Keep those commits clean and remember to stretch!", "excited", "oh_excited", "music"),
                (f"Spotted {app}! If you hit a sneaky bug, double-click me for rubber-duck duty!", "wink", "naa", "sparkle"),
            ])
        if category == "study":
            return self._pick("app_study", [
                (f"Hitting the books in {app}? That's pure Knowledge points in the making!", "smug", "hmm", "music"),
                (f"Study mode in {app}! Try summarizing key points in NOTES so they really stick!", "wink", "naa", "sparkle"),
            ])
        if category == "writing":
            return self._pick("app_writing", [
                (f"Drafting in {app}! Let the ideas flow first—you can polish later!", "wink", "hmm", "music"),
                (f"Writing session in {app}! Need a quiet Pomodoro timer while you draft?", "neutral", "oh_asking", "sparkle"),
            ])
        if category == "creative":
            return self._pick("app_creative", [
                (f"Ooh, creative craft in {app}! Looking sharp and stylish today!", "excited", "oh_excited", "sparkle"),
                (f"Designing in {app}! Don't forget to save often and rest your eyes!", "wink", "hmm", "music"),
            ])
        if category == "gaming":
            if overdue > 0:
                return self._pick("app_gaming_overdue", [
                    (f"Whoa—playing {app} while you've got {overdue} overdue deadline(s)? Bold Guts, partner!", "worried", "hoi", "alert"),
                    (f"Psst! {app} looks fun, just don't forget your overdue items waiting on the board!", "uneasy", "oh_asking", "question"),
                ])
            if hour >= 22 or hour < 4:
                return self._pick("app_gaming_late", [
                    (f"Late-night {app} run? Just one more round—don't let it eat your sleep!", "sad", "yareyare", "zzz"),
                ])
            return self._pick("app_gaming", [
                (f"Ooh, {app}! Everyone deserves a victory break—have fun!", "excited", "oh_excited", "music"),
                (f"Gaming break in {app}! Enjoy it guilt-free—just tap me if you want a break cap timer!", "wink", "hmm", "sparkle"),
            ])
        if category == "media":
            if overdue > 0 or pending >= 4:
                return self._pick("app_media_busy", [
                    (f"Taking a quick breather on {app}? Remember you still have {pending} tasks on deck!", "smug", "oh_asking", "question"),
                ])
            return self._pick("app_media", [
                (f"Chilling with {app}! Good background vibes—or taking a well-earned break?", "wink", "hmm", "music"),
                (f"Ooh, {app} time! Relax those eyes and recharge for the next round!", "neutral", "naa", "music"),
            ])
        if category == "social":
            return self._pick("app_social", [
                (f"Catching up on {app}! Building up that Charm and Kindness, huh?", "wink", "hmm", "heart"),
                (f"Checking {app}? Don't let chat pings ambush your focus if you're in the middle of work!", "smug", "naa", "question"),
            ])
        if category == "meeting":
            return self._pick("app_meeting", [
                (f"On a call in {app}? I'll stay out of your way—open NOTES if you need quick meeting jots!", "neutral", "hmm", "sparkle"),
            ])
        return (f"Working in {app}—I'm right here if you need anything!", "neutral", "hmm", "")

    def wall_bump(self) -> tuple[str, str, str]:
        return self._pick("wall_bump", [
            ("Uwooh! Hey, watch where you're tossing me!", "shout", "uwooh"),
            ("Whoa! Easy there—I'm a companion, not a stress ball!", "shout", "uwooh"),
            ("Uwooh! Careful! My scarf almost flew off!", "uneasy", "uwooh"),
        ])


# ---------------------------------------------------------------------------
# Daily Morning Briefing & Confidant Summary (Qt-free)
# ---------------------------------------------------------------------------
def build_daily_briefing(ctx: dict) -> dict:
    """Build structured Persona 5 Daily Morning Briefing & Confidant Summary data.

    Expects keys in `ctx`:
      hour (int), date_banner (str), today_events (list[str]), overdue_count (int),
      overdue_top (str), due_today_count (int), pending_tasks (int), high_tasks (int),
      habits_done (int), habits_total (int), streak_days (int), lowest_stat_label (str),
      top_stat_label (str), confidant_lv (int), confidant_max (int).
    """
    hour = int(ctx.get("hour", 9))
    if 4 <= hour < 12:
        period = "MORNING"
        headline = "GOOD MORNING! READY TO SEIZE THE DAY?"
    elif 12 <= hour < 17:
        period = "AFTERNOON"
        headline = "AFTERNOON BRIEFING — STAY SHARP!"
    elif 17 <= hour < 22:
        period = "EVENING"
        headline = "EVENING CHECK-IN — STRONG FINISH!"
    else:
        period = "LATE NIGHT"
        headline = "LATE-NIGHT BRIEFING — WRAP UP & REST!"

    ev_count = len(ctx.get("today_events") or ())
    overdue = int(ctx.get("overdue_count", 0))
    overdue_top = _clip(ctx.get("overdue_top", ""), 30)
    due_today = int(ctx.get("due_today_count", 0))
    pending_tasks = int(ctx.get("pending_tasks", 0))
    habits_done = int(ctx.get("habits_done", 0))
    habits_total = int(ctx.get("habits_total", 0))
    habits_left = max(0, habits_total - habits_done)
    streak = int(ctx.get("streak_days", 0))
    lowest_stat = ctx.get("lowest_stat_label", "Knowledge")
    conf_lv = int(ctx.get("confidant_lv", 5))
    conf_max = int(ctx.get("confidant_max", 25))

    # Build concise spoken summary for Morgana's dialogue bubble
    bits: list[str] = []
    if ev_count:
        bits.append(f"{ev_count} {'event' if ev_count == 1 else 'events'} today")
    if overdue:
        bits.append(f"{overdue} overdue")
    elif due_today:
        bits.append(f"{due_today} due today")
    if pending_tasks:
        bits.append(f"{pending_tasks} pending {'task' if pending_tasks == 1 else 'tasks'}")
    if habits_total:
        bits.append(f"{habits_done}/{habits_total} daily habits")

    if overdue:
        emotion, voice = "worried", "hoi"
        spoken = (f"{headline.split('—')[0].strip()} Watch out: you have {', '.join(bits)}—"
                  f"let's clear '{overdue_top}' first!" if overdue_top
                  else f"Heads up! Today's briefing: {', '.join(bits)}. Let's handle those overdue items first!")
    elif bits:
        emotion, voice = ("excited", "oh_excited") if streak >= 3 else ("wink", "naa")
        spoken = f"Daily briefing! You've got {', '.join(bits)}."
        if streak >= 2:
            spoken += f" Awesome {streak}-day streak—let's keep it alive!"
        else:
            spoken += " Let's make today count!"
    else:
        emotion, voice = "wink", "naa"
        spoken = (f"Clear board today! Confidant Rank {conf_lv}/{conf_max}—"
                  f"great chance to build a custom habit or train your {lowest_stat}!")

    # Build personalized Confidant Advice inside the Briefing panel
    if overdue:
        advice = (f"Priority alert! '{overdue_top or 'An overdue item'}' is waiting on you. "
                  f"Knock out your overdue deadlines first, then protect your {streak}-day streak!"
                  if streak >= 2 else
                  f"Priority alert! Clear '{overdue_top or 'your overdue deadlines'}' early so the rest of your day stays stress-free.")
    elif habits_total > 0 and habits_left > 0:
        advice = (f"You have {habits_left} daily {'habit' if habits_left == 1 else 'habits'} left today! "
                  f"Your {lowest_stat} could use a boost—check off a habit or run a Focus block to level it up.")
    elif habits_total > 0 and habits_left == 0:
        advice = (f"All {habits_total} daily habits cleared today! Incredible consistency—"
                  f"your {lowest_stat} is next in line if you want a bonus rank push.")
    elif streak >= 3:
        advice = (f"You're on a {streak}-day activity streak (Confidant Lv {conf_lv}/{conf_max})! "
                  f"Train {lowest_stat} today to keep your pentagon balanced.")
    else:
        advice = (f"Your {lowest_stat} is currently trailing your other stats. "
                  f"Add a custom Daily Habit or finish a Focus session today to earn automatic Social Stat points!")

    return {
        "period": period,
        "date_banner": ctx.get("date_banner", ""),
        "headline": headline,
        "spoken": spoken,
        "emotion": emotion,
        "voice": voice,
        "advice": advice,
    }


# ---------------------------------------------------------------------------
# Evening Wrap-up Recap & Auto-DND Recovery (Qt-free)
# ---------------------------------------------------------------------------
def build_evening_wrapup(ctx: dict) -> dict:
    """Build structured Persona 5 Evening Wrap-up recap copy from today's activity."""
    done_today = int(ctx.get("done_today_count", 0))
    pending = int(ctx.get("pending_count", 0))
    habits_done = int(ctx.get("habits_done", 0))
    habits_total = int(ctx.get("habits_total", 0))
    focus_done = int(ctx.get("focus_sessions", 0))
    scr_text = str(ctx.get("screen_time_text") or "0m")
    today_pts = int(ctx.get("today_points", 0))

    wins = done_today + habits_done + focus_done
    if wins >= 5:
        headline = "WHAT A RUN TODAY — HUGE PROGRESS!"
        emotion, voice = "excited", "oh_excited"
    elif wins >= 2:
        headline = "SOLID WORK TODAY — TIME TO UNWIND!"
        emotion, voice = "wink", "hmm"
    elif wins == 1:
        headline = "NICE STEP FORWARD — EASY EVENING!"
        emotion, voice = "smug", "naa"
    else:
        headline = "QUIET DAY — READY TO RESET FOR TOMORROW?"
        emotion, voice = "neutral", "hmm"

    bits: list[str] = []
    if done_today:
        bits.append(f"{done_today} {'task' if done_today == 1 else 'tasks'} finished")
    if habits_total:
        bits.append(f"{habits_done}/{habits_total} daily habits")
    if focus_done:
        bits.append(f"{focus_done} focus {'sprint' if focus_done == 1 else 'sprints'}")
    if today_pts:
        bits.append(f"+{today_pts} Social Stat pts")

    if bits:
        summary = "Today you logged " + ", ".join(bits) + f" across {scr_text} of active screen time."
    else:
        summary = f"You spent {scr_text} active today. Even quiet days count—reset cleanly for tomorrow!"

    if pending > 0:
        advice = (
            f"{summary} You still have {pending} unfinished {'task' if pending == 1 else 'tasks'}—"
            "roll any over to tomorrow in one click, jot a 1-line reflection, and get real rest!"
        )
    else:
        advice = (
            f"{summary} Zero pending tasks left on the board! "
            "Drop a quick 1-line reflection into your Journal and enjoy a stress-free night."
        )

    spoken = (
        f"Evening wrap-up time! {summary} "
        + ("Let's roll over what's left to tomorrow and jot a quick note!" if pending else "Everything's clear—save a quick reflection and rest up!")
    )
    return {
        "date_banner": ctx.get("date_banner", ""),
        "headline": headline,
        "advice": advice,
        "spoken": spoken,
        "emotion": emotion,
        "voice": voice,
    }


def dnd_recovery_line(reason: str, duration_text: str, deferred_count: int = 0) -> tuple[str, str, str]:
    """Gentle catch-up remark after Auto-DND disengages following a meeting or fullscreen session."""
    if reason == "meeting":
        base = f"Welcome back from your call ({duration_text} in quiet mode)!"
    else:
        base = f"Back from fullscreen ({duration_text} in quiet mode)!"
    if deferred_count > 0:
        word = "reminder" if deferred_count == 1 else "reminders"
        return (f"{base} I held {deferred_count} {word} while you were busy:", "wink", "hmm")
    return (f"{base} Hope that went smoothly—grab some water and stretch!", "wink", "naa")