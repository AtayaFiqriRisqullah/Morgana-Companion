# -*- coding: utf-8 -*-
"""Select & synchronize Morgana's expression and Japanese voiceline from dialog text & context.
No Qt dependency.

Expression keys -> sprites in `assets/Morgana/` (see sprites.py):

    neutral   b003_000   default smile (also peaceful closed eyes when Sleep Mode is on)
    wink      b003_001   happy closed/winking eyes ^_^
    serious   b003_002   furrowed brows, focused
    worried   b003_003   concerned / hesitant
    angry     b003_004   turned, annoyed (clenched teeth)
    sad       b003_005   tired / sweating / exhausted
    uneasy    b003_007   awkward / unsure / oops
    excited   b003_008   starry yellow eyes ★
    shout     b003_010   shouting / shocked / urgent
    smug      b003_011   confident fang grin
"""
from __future__ import annotations

EXPRESSIONS = ("neutral", "wink", "serious", "worried", "angry", "sad",
               "uneasy", "excited", "shout", "smug")

_EXPR_ALIASES: dict[str, str] = {
    "happy": "wink",
    "smile": "wink",
    "cheerful": "wink",
    "joy": "excited",
    "proud": "smug",
    "confident": "smug",
    "determined": "serious",
    "focused": "serious",
    "concerned": "worried",
    "anxious": "worried",
    "nervous": "uneasy",
    "confused": "uneasy",
    "awkward": "uneasy",
    "shocked": "shout",
    "surprised": "shout",
    "annoyed": "angry",
    "mad": "angry",
    "tired": "sad",
    "exhausted": "sad",
    "sleepy": "neutral",
}

# Voiceline keys -> files/pools in `audio_manager.py` (8 originals + 36 curated P5R clips + pools).
VOICES = (
    # Core / classic keys (also rotate via VOICE_POOLS)
    "dousuru", "hmm", "hoi", "naa", "oh_asking", "oh_excited", "uwooh", "yareyare",
    # Curated 36 P5R Japanese Morgana reaction clips
    "ohayou", "choushi", "taihen", "oi", "nyaa", "maana", "sigh", "oicho",
    "gokurou", "chigau", "maji", "sleepy_nyaa", "iizo", "wahaha", "ikuzo",
    "shigoto", "nyahaha", "kensan", "icchou", "yarujanee", "yawn", "asameshi",
    "kaigi_start", "kaigi_end", "naruhodo", "kangaero", "dekita", "kaketa",
    "kettei", "sugoi", "fumu", "unun", "sateto", "yoshi", "acchaa", "anosaa",
    # Contextual pool aliases
    "sleep_on", "task_done", "focus_start", "stat_log", "rank_up",
    "wink_happy", "smug_win", "worried_cue", "error_cue",
)

# Voice valence sets — used by `resolve_reaction()` so sprite & voice NEVER contradict!
POSITIVE_EXCITED_VOICES = frozenset({
    "oh_excited", "sugoi", "yarujanee", "wahaha", "yoshi", "icchou", "dekita",
    "rank_up", "task_done",
})
POSITIVE_UPBEAT_VOICES = frozenset({
    "hmm", "iizo", "maana", "asameshi", "gokurou", "ikuzo", "shigoto",
    "kensan", "kaketa", "kettei", "kaigi_start", "kaigi_end", "ohayou",
    "choushi", "nyahaha", "nyaa", "unun", "sateto", "focus_start", "stat_log",
    "wink_happy", "smug_win",
})
URGENT_ANGRY_VOICES = frozenset({"hoi", "oi", "kangaero", "chigau"})
SHOCK_VOICES = frozenset({"uwooh", "oicho", "maji"})
SAD_TIRED_VOICES = frozenset({"yareyare", "sigh", "yawn"})
SLEEP_COZY_VOICES = frozenset({"sleepy_nyaa", "sleep_on"})
UNEASY_WORRIED_VOICES = frozenset({"acchaa", "taihen", "anosaa", "error_cue", "worried_cue"})

# Base expression for each motion state (idle / talking / sleepy / action).
# Note: during Sleep Mode, PetWindow renders `neutral` with peaceful closed eyes;
# during curfew-while-awake, bedtime lines explicitly express tired/scolding reactions.
STATE_EXPRESSION = {
    "idle": "neutral",
    "talking": "neutral",
    "sleepy": "neutral",
    "action": "smug",
}

# Order matters: first matching rule wins. Positive / celebratory cues come BEFORE
# worried/sad so upbeat messages mentioning rest or tasks NEVER show a sad face!
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shout", ("hurry", "leave without us", "due now:", "starting now:", "go to bed!",
               "lights out", "almost morning", "time's up", "uwooh", "watch where you're tossing",
               "not a stress ball")),
    ("angry", ("shouldn't be staying up", "shouldn't skip meals", "usage is high",
               "closing apps", "not letting this slide", "past bedtime", "not a cat",
               "bukan kucing", "way over budget", "over budget")),
    ("excited", ("rank up", "leveled up", "level up", "ooh", "whoa", "paying off",
                 "awesome", "amazing", "great effort", "great work", "great job",
                 "you made it", "you're doing great", "you're doing awesome", "you've got this",
                 "rooting for you", "get some good work", "boom!", "crushed it", "on fire",
                 "unstoppable", "looking cool", "all tasks cleared", "every single",
                 "huge progress", "spotless", "mantap", "keren", "hebat", "semangat",
                 "berhasil", "sukses")),
    ("uneasy", ("are you sure", "cats can't", "couldn't find", "couldn't load", "couldn't update",
                "couldn't read", "couldn't save", "isn't installed", "expired", "failed",
                "error", "invalid", "missing")),
    ("smug", ("focus session", "focused work", "lock in", "locked in", "momentum",
              "steady progress", "timer set", "logged +", "auto-reward", "books balanced",
              "let's make", "piece of cake", "sharp", "streak", "game plan",
              "strategy", "on the board", "tackle", "ready to knock", "saved")),
    ("wink", ("good morning", "welcome back", "movie", "is open", "i'm awake", "wide awake",
              "sleep mode is off", "disabled", "paused", "weather", "plenty of time",
              "schedule ahead", "clear until tomorrow", "take it easy", "enjoy your rest",
              "you earned", "guilt-free", "start on boot is enabled", "back online",
              "nice!", "good night", "sweet dreams", "all closed", "tucked away",
              "thanks for sharing", "recharged", "well-rested")),
    ("worried", ("overdue", "test tomorrow", "watch out", "priority alert", "running low",
                 "don't forget", "part-time", "raining", "upcoming event", "careful")),
    ("sad", ("aren't you tired", "must be tired", "look pretty tired", "already so worn out",
             "eyes are getting heavy", "good grief", "yawn...", "rough day",
             "feeling down", "capek", "lelah", "ngantuk", "sedih")),
    ("serious", ("shouldn't you", "study", "read a book", "guard down",
                 "money", "gym", "water your plant", "high priority", "deadline")),
)

# Voice rules matched by text cues before falling back to expression.
_VOICE_TEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chigau", ("not a cat", "bukan kucing", "i am not a cat", "don't call me a cat")),
    ("uwooh", ("uwooh", "watch where you're tossing", "stress ball", "almost morning")),
    ("hoi", ("time's up", "due now:", "starting now:", "hurry", "leave without us",
             "shouldn't be staying up", "usage is high", "go to bed!", "lights out",
             "not letting this slide", "past bedtime")),
    ("kangaero", ("over budget", "spend your money", "closing apps", "usage is high")),
    ("ohayou", ("good morning", "selamat pagi", "i'm awake", "wide awake", "sleep mode is off")),
    ("sleep_on", ("good night", "sweet dreams", "sleep mode is on", "assume you're asleep",
                  "selamat tidur")),
    ("rank_up", ("rank up", "leveled up", "level up")),
    ("oh_excited", ("awesome", "amazing", "great effort", "great work", "great job",
                    "you made it", "paying off", "you're doing great", "you're doing awesome",
                    "rooting for you", "crushed it", "on fire", "unstoppable", "boom!",
                    "ooh", "whoa", "mantap", "keren", "hebat", "semangat")),
    ("kaketa", ("saved your reflection", "journal", "notebook", "wrote", "note saved")),
    ("kensan", ("social stat", "confidant", "pentagon", "daily habit")),
    ("kaigi_start", ("morning briefing", "daily briefing", "afternoon briefing", "game plan")),
    ("kaigi_end", ("evening wrap-up", "day wrapped up", "tucked away", "all closed")),
    ("focus_start", ("focus session started", "focused work", "lock in", "locked in",
                     "coding sprint", "focus sprint")),
    ("gokurou", ("earned this breather", "enjoy a ", "sleep guilt-free", "earned it",
                 "well done", "good work")),
    ("task_done", ("checked off", "task done", "cleared", "completed", "finished")),
    ("kettei", ("settings saved", "timer set", "scheduled", "decided", "start on boot")),
    ("acchaa", ("couldn't find", "couldn't load", "couldn't update", "couldn't read",
                "couldn't save", "isn't installed", "expired", "failed", "error", "invalid")),
    ("yareyare", ("aren't you tired", "must be tired", "look pretty tired", "worn out",
                  "shouldn't skip meals", "go to sleep", "bedtime", "good grief",
                  "pillow", "yawn", "eyes are getting heavy", "lie down")),
    ("taihen", ("overdue", "priority alert", "test tomorrow", "running low")),
    ("dousuru", ("what should", "what's next", "what's the move", "what's the plan",
                 "first move", "ready for another", "ready for this", "shouldn't you")),
    ("oh_asking", ("are you sure", "checking")),
    ("hmm", ("logged +", "auto-reward", "momentum", "steady progress", "is open",
             "take it easy", "enjoy your rest", "clear for the next", "back online")),
)

_EMOTION_VOICE: dict[str, str] = {
    "excited": "oh_excited",
    "shout": "uwooh",
    "angry": "hoi",
    "sad": "yareyare",
    "uneasy": "acchaa",
    "worried": "taihen",
    "smug": "hmm",
    "wink": "wink_happy",
    "serious": "dousuru",
    "neutral": "naa",
}


def normalize_expression(emotion: str | None, default: str = "neutral") -> str:
    """Return a guaranteed valid key from `EXPRESSIONS`."""
    fallback = default if default in EXPRESSIONS else "neutral"
    if not emotion:
        return fallback
    low = str(emotion).strip().lower()
    if low in EXPRESSIONS:
        return low
    mapped = _EXPR_ALIASES.get(low)
    return mapped if mapped in EXPRESSIONS else fallback


def emotion_for_text(text: str, default: str = "neutral") -> str:
    """Return an expression key for `text`, or `default` if nothing matches."""
    low = (text or "").lower()
    for emotion, keywords in _RULES:
        if any(k in low for k in keywords):
            return emotion
    return normalize_expression(default, "neutral")


def voice_for_dialogue(text: str, emotion: str = "neutral", context: str = "",
                       hour: int | None = None) -> str:
    """Select the best Morgana voiceline key for `text`, `emotion`, and `context`."""
    low = (text or "").lower()
    emo = normalize_expression(emotion, "neutral")

    if context == "bump_wall":
        return "uwooh"
    if context == "rank_up":
        return "rank_up"
    if context == "timer_up":
        return "hoi"
    if context == "sleep_on":
        return "sleep_on"
    if context == "wake_up":
        return "ohayou" if (hour is not None and 4 <= hour < 12) else "choushi"
    if context == "bedtime":
        return "hoi" if emo in ("shout", "angry") else "yareyare"
    if context == "startup":
        return "ohayou" if (hour is not None and 4 <= hour < 12) else "choushi"
    if context == "stat_log":
        return "stat_log"
    if context == "task_done":
        return "task_done"
    if context == "note_save":
        return "kaketa"
    if context == "focus_start":
        return "focus_start"
    if context == "briefing":
        return "kaigi_start"
    if context == "wrapup":
        return "kaigi_end"
    if context == "cat_tease":
        return "chigau"
    if context == "reminder":
        return "hoi" if ("due now:" in low or "starting now:" in low) else "anosaa"

    for voice, keywords in _VOICE_TEXT_RULES:
        if any(k in low for k in keywords):
            return voice
    if "?" in low and emo in ("neutral", "worried", "serious", "uneasy"):
        return "oh_asking"
    return _EMOTION_VOICE.get(emo, "naa")


def resolve_reaction(
    text: str,
    *,
    emotion: str | None = None,
    voice: str | bool | None = None,
    state: str = "talking",
    context: str = "",
    hour: int | None = None,
) -> tuple[str, str | bool]:
    """Resolve and cross-validate `(emotion, voice)` so sprite, sentence, and audio ALWAYS agree.

    Fixes mismatches where:
      - User task titles appended to a startup greeting accidentally triggered `sad`/`worried`
      - An excited/upbeat voice clip played alongside a `sad`/`worried`/`uneasy` sprite
      - A tired/sigh voice clip (`yareyare`/`sigh`) played on an upbeat `excited`/`wink`/`smug` line
      - Invalid aliases (e.g. `"happy"`) fell back to the wrong base state expression
    """
    # For multi-line startup greetings ("Good morning!\n\nYou have 3 pending tasks:\n..."),
    # judge mood strictly from the greeting headline so user task titles don't hijack expression!
    probe_text = (text or "").splitlines()[0] if context == "startup" and text else (text or "")
    base_default = STATE_EXPRESSION.get(state, "neutral")
    if base_default == "sad" and context not in ("bedtime",):
        base_default = "neutral"

    if emotion is not None:
        emo = normalize_expression(emotion, base_default)
    else:
        emo = emotion_for_text(probe_text, default=base_default)

    if voice is False:
        return emo, False

    if isinstance(voice, str) and voice.strip():
        vkey = voice.strip()
    else:
        vkey = voice_for_dialogue(probe_text, emo, context=context, hour=hour)

    # --- Bidirectional Emotion <-> Voice Coherence Guard ---
    # 1) Never show a sad/worried/uneasy face when playing an excited or upbeat voice!
    if vkey in POSITIVE_EXCITED_VOICES:
        if emo in ("sad", "worried", "uneasy", "neutral"):
            emo = "excited"
    elif vkey in POSITIVE_UPBEAT_VOICES:
        if emo in ("sad", "worried", "uneasy"):
            emo = "wink" if vkey in ("ohayou", "choushi", "nyahaha", "nyaa", "unun", "wink_happy", "gokurou") else "smug"
    elif vkey in SLEEP_COZY_VOICES:
        if emo == "sad":
            emo = "wink"
    elif vkey in URGENT_ANGRY_VOICES:
        if emo in ("sad",):
            emo = "angry"
    elif vkey in SHOCK_VOICES:
        if emo in ("sad",):
            emo = "shout"

    # 2) Conversely, if the expression is explicitly upbeat/excited and voice was auto-selected
    #    (not explicitly forced), never allow a sad/tired voice to dampen it!
    if not isinstance(voice, str):
        if emo == "excited" and vkey in (SAD_TIRED_VOICES | UNEASY_WORRIED_VOICES):
            vkey = "oh_excited"
        elif emo in ("wink", "smug") and vkey in (SAD_TIRED_VOICES | UNEASY_WORRIED_VOICES):
            vkey = "wink_happy" if emo == "wink" else "smug_win"

    return emo, vkey
