# -*- coding: utf-8 -*-
"""Select Morgana's expression and voiceline from dialog text. No Qt dependency.

Expression keys -> sprites in `assets/Morgana/` (see sprites.py):

    neutral   b003_000   default smile
    wink      b003_001   happy closed/winking eyes
    serious   b003_002   furrowed brows, focused
    worried   b003_003   concerned / hesitant
    angry     b003_004   turned, annoyed (clenched teeth)
    sad       b003_005   tired / sweating
    uneasy    b003_007   awkward / unsure
    excited   b003_008   starry eyes
    shout     b003_010   shouting / urgent
    smug      b003_011   confident grin
"""
from __future__ import annotations

EXPRESSIONS = ("neutral", "wink", "serious", "worried", "angry", "sad",
               "uneasy", "excited", "shout", "smug")

# Voiceline keys -> files in `assets/audio/` (see audio_manager.py).
VOICES = ("dousuru", "hmm", "hoi", "naa", "oh_asking", "oh_excited", "uwooh", "yareyare")

# Base expression for each motion state (idle / talking / sleepy / action).
STATE_EXPRESSION = {
    "idle": "neutral",
    "talking": "neutral",
    "sleepy": "sad",
    "action": "smug",
}

# Order matters: first matching rule wins. All keywords are lowercase.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shout", ("hurry", "leave without us", "due now:", "starting now:", "go to bed!", "almost morning", "time's up")),
    ("angry", ("shouldn't be staying up", "shouldn't skip meals", "usage is high", "closing apps")),
    ("uneasy", ("are you sure", "cats can't", "couldn't find", "couldn't load", "couldn't update",
                "isn't installed", "expired", "failed", "error")),
    ("excited", ("ooh", "whoa", "paying off", "awesome work", "great effort", "you made it",
                 "you're doing great", "you're doing awesome", "you've got this",
                 "rooting for you", "get some good work", "rank up", "leveled up", "level up")),
    ("smug", ("focus session", "focused work", "lock in", "momentum", "steady progress", "timer set", "logged +")),
    ("wink", ("good morning", "movie", "is open", "i'm awake", "disabled", "paused", "weather",
              "plenty of time", "schedule ahead", "clear until tomorrow", "take it easy",
              "enjoy your rest", "start on boot is enabled")),
    ("worried", ("test tomorrow", "don't forget", "deadline", "part-time", "raining",
                 "time flies", "upcoming event", "next up:", "pending task")),
    ("sad", ("tired", "worn out", "sleep", "rest up", "well-rested", "bath", "sleep mode is on")),
    ("serious", ("shouldn't you", "study", "read a book", "guard down",
                 "money", "gym", "water your plant")),
)

# Voice rules matched by text cues before falling back to expression.
_VOICE_TEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hoi", ("time's up", "due now:", "starting now:", "hurry", "leave without us",
             "shouldn't be staying up", "usage is high", "go to bed!")),
    ("uwooh", ("whoa!", "almost morning", "crash")),
    ("oh_excited", ("rank up", "leveled up", "level up", "ooh", "whoa", "awesome",
                    "great effort", "you made it", "paying off", "you're doing great",
                    "you're doing awesome", "rooting for you")),
    ("yareyare", ("tired", "worn out", "sleep mode", "rest up", "well-rested",
                  "shouldn't skip meals", "go to sleep", "bedtime", "good grief")),
    ("dousuru", ("what should", "what's next", "ready for another", "ready for this",
                 "shouldn't you", "plan")),
    ("oh_asking", ("are you sure", "checking", "couldn't find", "couldn't load",
                   "couldn't update", "isn't installed", "expired", "failed", "error")),
    ("hmm", ("logged +", "timer set", "focus session", "focused work", "lock in",
             "momentum", "steady progress", "is open", "i'm awake", "start on boot",
             "take it easy", "enjoy your rest", "clear for the next", "good work")),
)

_EMOTION_VOICE = {
    "excited": "oh_excited",
    "shout": "uwooh",
    "angry": "hoi",
    "sad": "yareyare",
    "uneasy": "oh_asking",
    "worried": "naa",
    "smug": "hmm",
    "wink": "hmm",
    "serious": "dousuru",
    "neutral": "naa",
}


def emotion_for_text(text: str, default: str = "neutral") -> str:
    """Return an expression key for `text`, or `default` if nothing matches."""
    low = text.lower()
    for emotion, keywords in _RULES:
        if any(k in low for k in keywords):
            return emotion
    return default if default in EXPRESSIONS else "neutral"


def voice_for_dialogue(text: str, emotion: str = "neutral", context: str = "") -> str:
    """Select the best Morgana voiceline key for `text`, `emotion`, and `context`."""
    low = (text or "").lower()
    if context == "menu_open":
        return "dousuru"
    if context == "bump_wall":
        return "uwooh"
    if context == "rank_up":
        return "oh_excited"
    if context == "timer_up":
        return "hoi"
    if context == "bedtime":
        return "hoi" if emotion in ("shout", "angry") else "yareyare"
    if context == "startup":
        return "naa"
    if context == "stat_log":
        return "hmm"
    if context == "reminder":
        return "hoi" if ("due now:" in low or "starting now:" in low) else "naa"

    for voice, keywords in _VOICE_TEXT_RULES:
        if any(k in low for k in keywords):
            return voice
    if "?" in low and emotion in ("neutral", "worried", "serious", "uneasy"):
        return "oh_asking"
    return _EMOTION_VOICE.get(emotion, "naa")
