# -*- coding: utf-8 -*-
"""Select Morgana's expression from dialog text. No Qt dependency.

Expression keys -> sprites in `Morgana/` (see sprites.py):

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

# Base expression for each motion state (idle / talking / sleepy / action).
STATE_EXPRESSION = {
    "idle": "neutral",
    "talking": "neutral",
    "sleepy": "sad",
    "action": "smug",
}

# Order matters: first matching rule wins. All keywords are lowercase.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shout", ("hurry", "leave without us", "due now:", "starting now:", "go to bed!", "almost morning")),
    ("angry", ("shouldn't be staying up", "shouldn't skip meals", "usage is high", "closing apps")),
    ("uneasy", ("are you sure", "cats can't", "couldn't find", "couldn't load", "couldn't update",
                "isn't installed", "expired", "failed", "error")),
    ("excited", ("ooh", "whoa", "paying off", "awesome work", "great effort", "you made it",
                 "you're doing great", "you're doing awesome", "you've got this",
                 "rooting for you", "get some good work")),
    ("smug", ("focus session", "focused work", "lock in", "momentum", "steady progress")),
    ("wink", ("good morning", "movie", "is open", "i'm awake", "disabled", "paused", "weather",
              "plenty of time", "schedule ahead", "clear until tomorrow", "take it easy",
              "enjoy your rest", "start on boot is enabled")),
    ("worried", ("test tomorrow", "don't forget", "deadline", "part-time", "raining",
                 "time flies", "upcoming event", "next up:", "pending task")),
    ("sad", ("tired", "worn out", "sleep", "rest up", "well-rested", "bath", "sleep mode is on")),
    ("serious", ("shouldn't you", "study", "read a book", "guard down",
                 "money", "gym", "water your plant")),
)


def emotion_for_text(text: str, default: str = "neutral") -> str:
    """Return an expression key for `text`, or `default` if nothing matches."""
    low = text.lower()
    for emotion, keywords in _RULES:
        if any(k in low for k in keywords):
            return emotion
    return default if default in EXPRESSIONS else "neutral"
