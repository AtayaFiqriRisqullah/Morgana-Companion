# -*- coding: utf-8 -*-
"""Persona 5 multi-turn branching dialogue engine for Morgana's Choice Prompts.

No Qt dependency so it can be unit-tested cleanly. Features:
  * 14 multi-turn branching conversation trees (2-4 turns each, ~110 unique nodes).
  * Context-aware tree selection (time of day, active app category, screen time,
    overdue tasks, pending workload, lowest social stat, streak).
  * Persistent light memory (`dialogue_memory.json`) that remembers user preferences
    (favorite drink, work style, "not a cat" tease count, break snooze streak) and
    prevents repeating recent conversations.
  * Actionable choice effects (start Focus/Break/Manual timers, open Notes with a
    template, open Tasks/Stats/Briefing, toggle Sleep Mode, or award a daily-capped
    Social Stat bonus).
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from storage_manager import backup_corrupt, write_json_atomic

log = logging.getLogger("pet.dialogue")

MAX_DAILY_CHAT_REWARDS = 2


@dataclass(frozen=True)
class DialogueChoice:
    label: str
    next_node: str | None = None
    action: dict | None = None          # e.g. {"type": "start_focus", "minutes": 25}
    memory_set: dict | None = None      # e.g. {"drink": "coffee"}
    memory_inc: tuple[str, int] | None = None  # e.g. ("cat_tease_count", 1)
    stat_reward: tuple[str, int, str] | None = None  # (stat_key, pts, activity_label)


@dataclass(frozen=True)
class DialogueNode:
    text: str
    emotion: str = "neutral"
    voice: str = "naa"
    particle: str = ""
    choices: tuple[DialogueChoice, ...] = ()


@dataclass(frozen=True)
class ConversationTree:
    tree_id: str
    title: str
    tags: tuple[str, ...]
    start: str
    nodes: dict[str, DialogueNode]
    priority: int = 0


# ---------------------------------------------------------------------------
# Persistent light memory & anti-repetition store
# ---------------------------------------------------------------------------
class DialogueMemoryStore:
    """Stores light conversation preferences, recent tree history, and daily reward cap."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict = {
            "recent_trees": [],
            "prefs": {},
            "counters": {},
            "reward_date": "",
            "rewards_today": 0,
            "total_chats": 0,
        }
        self.last_error = ""
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data["recent_trees"] = [str(x) for x in raw.get("recent_trees", []) if x][-12:]
                self.data["prefs"] = dict(raw.get("prefs") or {})
                self.data["counters"] = {
                    str(k): int(v) for k, v in (raw.get("counters") or {}).items()
                    if isinstance(v, (int, float))
                }
                self.data["reward_date"] = str(raw.get("reward_date") or "")
                self.data["rewards_today"] = int(raw.get("rewards_today") or 0)
                self.data["total_chats"] = int(raw.get("total_chats") or 0)
        except (OSError, ValueError) as exc:
            log.warning("Failed reading %s: %s", self.path.name, exc)
            backup_corrupt(self.path)

    def save(self) -> bool:
        ok, err = write_json_atomic(self.path, self.data)
        self.last_error = err
        return ok

    def record_tree(self, tree_id: str) -> None:
        rec = [x for x in self.data["recent_trees"] if x != tree_id]
        rec.append(tree_id)
        self.data["recent_trees"] = rec[-8:]
        self.data["total_chats"] = int(self.data.get("total_chats", 0)) + 1
        self.save()

    def set_pref(self, key: str, value: str) -> None:
        self.data["prefs"][str(key)] = str(value)
        self.save()

    def get_pref(self, key: str, default: str = "") -> str:
        return str(self.data["prefs"].get(key, default))

    def inc_counter(self, key: str, delta: int = 1) -> int:
        val = int(self.data["counters"].get(key, 0)) + int(delta)
        self.data["counters"][key] = val
        self.save()
        return val

    def get_counter(self, key: str) -> int:
        return int(self.data["counters"].get(key, 0))

    def can_award_chat_stat(self, now: datetime | None = None) -> bool:
        today = (now or datetime.now()).strftime("%Y-%m-%d")
        if self.data.get("reward_date") != today:
            return True
        return int(self.data.get("rewards_today", 0)) < MAX_DAILY_CHAT_REWARDS

    def consume_chat_stat_reward(self, now: datetime | None = None) -> bool:
        today = (now or datetime.now()).strftime("%Y-%m-%d")
        if self.data.get("reward_date") != today:
            self.data["reward_date"] = today
            self.data["rewards_today"] = 0
        if int(self.data["rewards_today"]) >= MAX_DAILY_CHAT_REWARDS:
            return False
        self.data["rewards_today"] = int(self.data["rewards_today"]) + 1
        self.save()
        return True


# ---------------------------------------------------------------------------
# 14 Rich Multi-Turn Persona 5 Conversation Trees
# ---------------------------------------------------------------------------
def _build_trees() -> dict[str, ConversationTree]:
    trees: list[ConversationTree] = [
        # 1) Daily Energy & Battle Plan (3 turns)
        ConversationTree(
            tree_id="energy_strategy",
            title="Energy & Game Plan",
            tags=("any", "morning", "afternoon"),
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Hey! Quick desk check-in—how's your energy level right now?",
                    emotion="neutral", voice="dousuru", particle="question",
                    choices=(
                        DialogueChoice("Fired up! Ready to crush it!", "high_energy"),
                        DialogueChoice("Steady, just need a clear target.", "steady"),
                        DialogueChoice("Honestly running on fumes...", "low_energy"),
                    ),
                ),
                "high_energy": DialogueNode(
                    text="Ooh, I love that spark! With {pending_summary}, how do you want to channel this momentum?",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Lock into a 25m Focus Sprint!", "start_sprint",
                                       action={"type": "start_focus", "minutes": 25},
                                       memory_set={"work_style": "sprint"}),
                        DialogueChoice("Open Tasks & pick the biggest priority!", "pick_boss",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Brain-dump ideas in Notes first.", "open_brainstorm",
                                       action={"type": "open_new_note", "template": "idea"}),
                    ),
                ),
                "start_sprint": DialogueNode(
                    text="That's the spirit! Focus timer is rolling—I'll guard the clock while you sweep through it!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "pick_boss": DialogueNode(
                    text="Opening Tasks! Knock out the toughest one first and everything after it will feel effortless.",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "open_brainstorm": DialogueNode(
                    text="Smart call! Catching ideas before they vanish keeps your head clear. Let's jot it down!",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "steady": DialogueNode(
                    text="Steady wins the long game! Want to focus on your {lowest_stat} or clear something off the board?",
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Let's check my Social Stats & Habits.", "go_stats",
                                       action={"type": "open_stats"},
                                       memory_set={"work_style": "steady"}),
                        DialogueChoice("Show me today's full Briefing.", "go_brief",
                                       action={"type": "open_briefing"}),
                        DialogueChoice("Just stay here and keep me company.", "company_end",
                                       stat_reward=("charm", 1, "Chat with Morgana")),
                    ),
                ),
                "go_stats": DialogueNode(
                    text="Pulling up Social Stats! One steady habit today pushes that gold star even brighter.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "go_brief": DialogueNode(
                    text="Here's our tactical overview! Pick one win and we'll call today a victory.",
                    emotion="neutral", voice="naa",
                ),
                "company_end": DialogueNode(
                    text="Heh, you got it! No pressure—I'll be right here keeping watch on your desktop.",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "low_energy": DialogueNode(
                    text="Whoa, don't push yourself until you burn out! What would recharge you fastest right now?",
                    emotion="worried", voice="yareyare", particle="question",
                    choices=(
                        DialogueChoice("5-minute water & stretch break.", "recharge_break",
                                       action={"type": "start_break", "minutes": 5},
                                       stat_reward=("kindness", 1, "Self-care break")),
                        DialogueChoice("10-minute power rest—no screens.", "recharge_long",
                                       action={"type": "start_break", "minutes": 10}),
                        DialogueChoice("I'll just do one tiny easy win.", "tiny_win",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "recharge_break": DialogueNode(
                    text="5-minute break timer started! Step away, sip some water, and loosen your shoulders—orders from Mona!",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "recharge_long": DialogueNode(
                    text="10 minutes of pure recovery! Close your eyes or grab {fav_drink}—I'll call you back when time's up.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "tiny_win": DialogueNode(
                    text="One small step still counts! Pick the easiest item on the list and get a quick spark going.",
                    emotion="smug", voice="naa", particle="sparkle",
                ),
            },
        ),

        # 2) Screen-Time Interactive Break Nudge (2-3 turns, triggered automatically or manually)
        ConversationTree(
            tree_id="screen_time_nudge",
            title="Screen-Time Wellness Check",
            tags=("screen_long",),
            priority=10,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Hey! You've been glued to the screen for {continuous_time} straight ({screen_time} today). Time for a breather?",
                    emotion="worried", voice="hoi", particle="alert",
                    choices=(
                        DialogueChoice("Good catch—start a 5m break!", "break_5",
                                       action={"type": "start_break", "minutes": 5},
                                       stat_reward=("kindness", 1, "Healthy screen break")),
                        DialogueChoice("I'll stretch & drink water right now.", "stretch_now",
                                       action={"type": "record_break"},
                                       stat_reward=("guts", 1, "Desk stretch & hydrate")),
                        DialogueChoice("Just 15 more minutes, I'm in the zone!", "snooze_15",
                                       action={"type": "snooze_break", "minutes": 15},
                                       memory_inc=("break_snoozes", 1)),
                    ),
                ),
                "break_5": DialogueNode(
                    text="Yes! Look 20 feet away for 20 seconds, grab water, and shake out your wrists. I'll watch the timer!",
                    emotion="excited", voice="oh_excited", particle="heart",
                ),
                "stretch_now": DialogueNode(
                    text="Awesome! Shoulders back, deep breath, big sip of water—your future self is already thanking you!",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "snooze_15": DialogueNode(
                    text="Sheesh, alright—15 minutes! Promise me you'll at least blink and sip some water while you finish up?",
                    emotion="uneasy", voice="yareyare",
                    choices=(
                        DialogueChoice("Deal! Sipping water right now.", "deal_ok"),
                        DialogueChoice("Fine, fine—make it a 15m countdown.", "deal_timer",
                                       action={"type": "start_timer", "minutes": 15, "label": "Break Time!"}),
                    ),
                ),
                "deal_ok": DialogueNode(
                    text="Good! I'll check back in 15 minutes—no sneaking past Mona's watch!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "deal_timer": DialogueNode(
                    text="15-minute countdown locked in! Finish that thought strong, then we stretch!",
                    emotion="smug", voice="hmm", particle="music",
                ),
            },
        ),

        # 3) Active App: Coding Rubber-Duck Partner (3 turns)
        ConversationTree(
            tree_id="coding_partner",
            title="Rubber-Duck Debug Partner",
            tags=("coding",),
            priority=7,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Spotted you deep in {app_name}! How's the code treating you right now?",
                    emotion="smug", voice="naa", particle="sparkle",
                    choices=(
                        DialogueChoice("Smooth sailing—building something cool!", "smooth"),
                        DialogueChoice("Stuck on a sneaky bug...", "stuck_bug"),
                        DialogueChoice("Designing / planning architecture.", "designing"),
                    ),
                ),
                "smooth": DialogueNode(
                    text="Whoa, nice flow! Don't forget to commit or jot down your next step before you lose the thread!",
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Let's sprint 25m while the flow is hot!", "code_sprint",
                                       action={"type": "start_focus", "minutes": 25},
                                       stat_reward=("proficiency", 1, "Coding flow session")),
                        DialogueChoice("Open Notes to log what I figured out.", "code_note",
                                       action={"type": "open_new_note", "template": "study"}),
                    ),
                ),
                "code_sprint": DialogueNode(
                    text="Locked in! Show that codebase what top-tier Proficiency looks like!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "code_note": DialogueNode(
                    text="Documenting while it's fresh is pro-level Proficiency! Opening a clean Study note for you.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "stuck_bug": DialogueNode(
                    text="Ooh, rubber-duck time! Explain it to me—what's the sneaky culprit acting like?",
                    emotion="serious", voice="oh_asking", particle="question",
                    choices=(
                        DialogueChoice("Weird state / logic not matching expectations.", "bug_logic"),
                        DialogueChoice("Crash or error I can't trace yet.", "bug_trace"),
                        DialogueChoice("My brain's fried—need fresh eyes.", "bug_break"),
                    ),
                ),
                "bug_logic": DialogueNode(
                    text="Classic hidden-assumption trap! Write down the one thing you're sure *can't* be wrong—9 times out of 10, that's where the bug lives!",
                    emotion="smug", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Open Notes to trace it step by step.", "bug_note",
                                       action={"type": "open_new_note", "template": "idea"},
                                       stat_reward=("knowledge", 1, "Rubber-duck debugging")),
                        DialogueChoice("Aha! Wait—I just spotted something!", "bug_aha",
                                       stat_reward=("knowledge", 1, "Rubber-duck breakthrough")),
                    ),
                ),
                "bug_trace": DialogueNode(
                    text="Isolate the smallest input that triggers it first! Cut the problem in half twice and it has nowhere left to hide.",
                    emotion="serious", voice="dousuru",
                    choices=(
                        DialogueChoice("Good idea, let's isolate & test!", "bug_aha",
                                       stat_reward=("proficiency", 1, "Bug isolation")),
                        DialogueChoice("Let's add a Task to revisit it cleanly.", "bug_task",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "bug_break": DialogueNode(
                    text="Walking away for 5 minutes fixes more bugs than staring for an hour! Want a quick reset timer?",
                    emotion="wink", voice="yareyare",
                    choices=(
                        DialogueChoice("Yes, 5m fresh-eyes break!", "bug_break_go",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("Nah, one more try first!", "bug_try"),
                    ),
                ),
                "bug_note": DialogueNode(
                    text="Writing the bug down almost always reveals the flaw halfway through. Go get 'em!",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "bug_aha": DialogueNode(
                    text="Whoa, really?! Rubber-duck Morgana strikes again! Go squash that bug!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "bug_task": DialogueNode(
                    text="Smart—park it in Tasks so it doesn't hijack your whole day.",
                    emotion="neutral", voice="hmm",
                ),
                "bug_break_go": DialogueNode(
                    text="5-minute brain reset rolling! Watch the answer pop into your head while getting water.",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "bug_try": DialogueNode(
                    text="Heh, stubborn—I respect the Guts! Holler if you want a break timer.",
                    emotion="smug", voice="hmm",
                ),
                "designing": DialogueNode(
                    text="Ooh, planning before typing saves hours later! Want to sketch the plan in Notes?",
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Open a Project Idea note!", "design_note",
                                       action={"type": "open_new_note", "template": "idea"}),
                        DialogueChoice("I've got the blueprint in my head.", "design_ok",
                                       stat_reward=("knowledge", 1, "System design session")),
                    ),
                ),
                "design_note": DialogueNode(
                    text="Smart architect move! Lay out the core pieces in Notes, then turn milestones into Tasks.",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "design_ok": DialogueNode(
                    text="Ooh, confident! Keep it simple and clean—you've got this!",
                    emotion="wink", voice="hmm", particle="music",
                ),
            },
        ),

        # 4) Active App: Gaming / Media Banter (3 turns)
        ConversationTree(
            tree_id="play_banter",
            title="Break or Procrastination?",
            tags=("gaming", "media"),
            priority=7,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Hold on—is that {app_name} I see? Taking a well-earned break, or dodging {pending_summary}?",
                    emotion="smug", voice="oh_asking", particle="question",
                    choices=(
                        DialogueChoice("Hey, it's a well-earned reward break!", "earned"),
                        DialogueChoice("Guilty... I got distracted.", "guilty"),
                        DialogueChoice("Shh, it's strictly 'research'!", "research"),
                    ),
                ),
                "earned": DialogueNode(
                    text="Fair enough! Everyone needs real downtime. Want a timer so the break doesn't accidentally eat 3 hours?",
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Good idea—set a 20m break cap.", "cap_20",
                                       action={"type": "start_timer", "minutes": 20, "label": "Back from Break"}),
                        DialogueChoice("No timers—I'm fully off-duty!", "off_duty"),
                    ),
                ),
                "cap_20": DialogueNode(
                    text="20-minute fun timer set! Enjoy it guilt-free—I'll tap your shoulder when it's time to switch back!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "off_duty": DialogueNode(
                    text="Roger that! Recharge those batteries—just don't stay up past bedtime!",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "guilty": DialogueNode(
                    text="Haha, points for honesty! Want an easy bridge back into focus—no guilt trip, I promise?",
                    emotion="wink", voice="naa",
                    choices=(
                        DialogueChoice("Hit me with a short 15m Focus warm-up!", "warmup_15",
                                       action={"type": "start_focus", "minutes": 15},
                                       stat_reward=("guts", 1, "Beat procrastination")),
                        DialogueChoice("Open Tasks so I pick 1 tiny thing.", "open_t",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("5 more minutes, then I'm back!", "five_more",
                                       action={"type": "start_timer", "minutes": 5, "label": "Back to Work"}),
                    ),
                ),
                "warmup_15": DialogueNode(
                    text="15-minute warm-up started! Starting is 80% of the battle—you've already won the hardest part!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "open_t": DialogueNode(
                    text="Here's your Task board! Pick just one tiny target to break the inertia.",
                    emotion="smug", voice="hmm",
                ),
                "five_more": DialogueNode(
                    text="5-minute countdown running! Wrap up this clip or match and then we roll!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "research": DialogueNode(
                    text="'Research,' huh? Sure, sure—and I suppose Fatty Tuna is 'brain fuel' too!",
                    emotion="uneasy", voice="yareyare", particle="question",
                    choices=(
                        DialogueChoice("Hey, inspiration counts as creative fuel!", "inspo",
                                       stat_reward=("charm", 1, "Smooth talker")),
                        DialogueChoice("Okay fine, back to business!", "guilty"),
                    ),
                ),
                "inspo": DialogueNode(
                    text="Pfft—smooth talker! Alright, if any good ideas spark from your 'research', drop them in Notes!",
                    emotion="smug", voice="hmm", particle="music",
                ),
            },
        ),

        # 5) "I Am NOT a Cat!" Classic Banter (3 turns)
        ConversationTree(
            tree_id="not_a_cat",
            title="The Great Feline Debate",
            tags=("any", "afternoon", "evening"),
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Wait a second... you're looking at me with that 'aww, cute kitty' face again, aren't you?!",
                    emotion="angry", voice="hoi", particle="anger",
                    choices=(
                        DialogueChoice("You literally have ears and a tail, Mona.", "tease_1",
                                       memory_inc=("cat_tease_count", 1)),
                        DialogueChoice("No way! You're a classy desk strategist!", "praise_1"),
                        DialogueChoice("Only if I get to offer you Fatty Tuna.", "tuna_1"),
                    ),
                ),
                "tease_1": DialogueNode(
                    text="I AM NOT A CAT! I'm Morgana—your suave, tactical desk partner! Say it back!",
                    emotion="shout", voice="uwooh", particle="anger",
                    choices=(
                        DialogueChoice("Okay, okay—suave partner Morgana!", "truce"),
                        DialogueChoice("Sure thing, little kitty~", "tease_2",
                                       memory_inc=("cat_tease_count", 1)),
                    ),
                ),
                "truce": DialogueNode(
                    text="Hmph! Much better! ...Though I guess I don't mind hanging around on your desktop.",
                    emotion="smug", voice="hmm", particle="heart",
                ),
                "tease_2": DialogueNode(
                    text="Gah!! Unbelievable! Keep that up and I'm setting your bedtime to 8:00 PM! ...Hmph!",
                    emotion="angry", voice="yareyare", particle="anger",
                ),
                "praise_1": DialogueNode(
                    text="W-well, obviously! Someone around here has sharp judgment! What can this gentleman help you tackle?",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Help me boost my {lowest_stat}!", "help_stat",
                                       action={"type": "open_stats"},
                                       stat_reward=("charm", 1, "Flattered Morgana")),
                        DialogueChoice("Just stay awesome on my desk!", "stay_awesome",
                                       stat_reward=("kindness", 1, "Appreciated Morgana")),
                    ),
                ),
                "help_stat": DialogueNode(
                    text="Leave it to me! Opening Social Stats—let's polish your {lowest_stat} to rank up!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "stay_awesome": DialogueNode(
                    text="Heh, effortless for me! Anytime you need a partner, just click!",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "tuna_1": DialogueNode(
                    text="F-Fatty Tuna?! ...Ahem! Bribery doesn't work on a professional companion—unless it's fresh toro sushi!",
                    emotion="excited", voice="oh_excited", particle="heart",
                    choices=(
                        DialogueChoice("Deal—after I clear my next Task!", "tuna_deal",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Virtual Fatty Tuna, served!", "tuna_serve",
                                       stat_reward=("kindness", 1, "Shared Fatty Tuna")),
                    ),
                ),
                "tuna_deal": DialogueNode(
                    text="Ooh, now THAT'S motivation! Clear a task and we'll celebrate in style!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "tuna_serve": DialogueNode(
                    text="WHOA—melts right in my mouth!! You're the best partner a guy could ask for!",
                    emotion="excited", voice="oh_excited", particle="heart",
                ),
            },
        ),

        # 6) Cozy Café & Drink Ritual Chat (3 turns, saves favorite drink!)
        ConversationTree(
            tree_id="cafe_break",
            title="Cozy Café Break",
            tags=("any", "morning", "afternoon", "evening"),
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Whew, feels like a cozy café kind of day! What's your go-to desk drink when you're working?",
                    emotion="wink", voice="naa", particle="music",
                    choices=(
                        DialogueChoice("Rich dark coffee—hand-brewed style.", "drink_coffee",
                                       memory_set={"drink": "coffee"}),
                        DialogueChoice("Tea or matcha—calm and focused.", "drink_tea",
                                       memory_set={"drink": "tea"}),
                        DialogueChoice("Ice-cold water all the way.", "drink_water",
                                       memory_set={"drink": "ice water"}),
                    ),
                ),
                "drink_coffee": DialogueNode(
                    text="Ah, a connoisseur! Bold roast and cozy aroma—just don't drink it too close to bedtime!",
                    emotion="smug", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Pouring a cup & starting Focus!", "cafe_focus",
                                       action={"type": "start_focus", "minutes": 25},
                                       stat_reward=("charm", 1, "Cozy coffee ritual")),
                        DialogueChoice("Actually, let me grab a quick 5m cup.", "cafe_break",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "drink_tea": DialogueNode(
                    text="Ooh, classy and calm! Perfect for long study sessions or clean note-taking.",
                    emotion="wink", voice="hmm", particle="music",
                    choices=(
                        DialogueChoice("Open Notes for a quiet thinking session.", "cafe_notes",
                                       action={"type": "open_notes"},
                                       stat_reward=("knowledge", 1, "Calm tea study")),
                        DialogueChoice("Let's brew a cup—5m break timer!", "cafe_break",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "drink_water": DialogueNode(
                    text="Pure hydration! Honestly the smartest stat buff in real life—have you taken a sip recently?",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Taking a big sip right now!", "water_sip",
                                       stat_reward=("kindness", 1, "Hydration check")),
                        DialogueChoice("Oops—going to refill my bottle (5m).", "cafe_break",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "cafe_focus": DialogueNode(
                    text="Focus session started! Imagine cozy rain outside the window while you crush your work.",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "cafe_notes": DialogueNode(
                    text="Opening Notes! Take your time and let your thoughts flow cleanly onto the page.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "cafe_break": DialogueNode(
                    text="5-minute café break running! Stretch your legs while you grab your drink.",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "water_sip": DialogueNode(
                    text="Nice!! Hydrated brain = faster Proficiency and sharper Knowledge!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
            },
        ),

        # 7) Overdue Rescue / Tactical Debrief (3 turns)
        ConversationTree(
            tree_id="overdue_rescue",
            title="Overdue Rescue Plan",
            tags=("overdue",),
            priority=9,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Whoa—hold on! We've got {overdue_count} overdue item(s), starting with '{top_task}'. Want to tackle it together?",
                    emotion="worried", voice="hoi", particle="alert",
                    choices=(
                        DialogueChoice("Let's crush it right now—25m sprint!", "rescue_sprint",
                                       action={"type": "start_focus", "minutes": 25},
                                       stat_reward=("guts", 1, "Faced overdue head-on")),
                        DialogueChoice("It's overwhelming—help me break it down.", "rescue_split"),
                        DialogueChoice("Open Tasks so I can reschedule cleanly.", "rescue_open",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "rescue_sprint": DialogueNode(
                    text="NOW we're talking! 25 minutes on the clock—even 50% progress breaks the spell!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "rescue_split": DialogueNode(
                    text="Totally normal! Big tasks look scary until you slice them into a 5-minute first step. Want to sketch step 1 in Notes?",
                    emotion="neutral", voice="naa",
                    choices=(
                        DialogueChoice("Yes, open a quick breakdown Note!", "split_note",
                                       action={"type": "open_new_note", "template": "idea"}),
                        DialogueChoice("Just give me a tiny 10m starter sprint.", "split_10",
                                       action={"type": "start_focus", "minutes": 10},
                                       stat_reward=("guts", 1, "10m starter step")),
                    ),
                ),
                "split_note": DialogueNode(
                    text="Write 3 tiny bullet steps in Notes, then hit '→ TASK' on step 1. Easy infiltration!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "split_10": DialogueNode(
                    text="10-minute stealth sprint started! Just do the first tiny piece—you don't have to finish everything at once.",
                    emotion="wink", voice="hmm", particle="sparkle",
                ),
                "rescue_open": DialogueNode(
                    text="Good call—adjusting deadlines honestly beats letting them pile up. Opening Tasks!",
                    emotion="neutral", voice="hmm",
                ),
            },
        ),

        # 8) Confidant Growth & Lowest Social Stat Coaching (3 turns)
        ConversationTree(
            tree_id="confidant_coach",
            title="Confidant Stat Coaching",
            tags=("any", "afternoon", "evening", "streak"),
            start="root",
            nodes={
                "root": DialogueNode(
                    text="I checked your Confidant star—your {lowest_stat} is currently trailing behind {top_stat}! Want a quick mission to boost it?",
                    emotion="smug", voice="dousuru", particle="sparkle",
                    choices=(
                        DialogueChoice("Give me a real-life mission for {lowest_stat}!", "mission"),
                        DialogueChoice("Let's add a custom Daily Habit for it!", "add_habit",
                                       action={"type": "open_stats"}),
                        DialogueChoice("I'd rather double down on {top_stat}!", "double_down"),
                    ),
                ),
                "mission": DialogueNode(
                    text="Mission briefing: spend just 15 focused minutes today on something that exercises {lowest_stat}. Ready to lock it in?",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Accepted! Start a 15m timer!", "mission_go",
                                       action={"type": "start_focus", "minutes": 15},
                                       stat_reward=("guts", 1, "Accepted Mona's mission")),
                        DialogueChoice("Log it in Tasks for later today.", "mission_task",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "mission_go": DialogueNode(
                    text="Target locked! 15 minutes on the clock—let's make that gold star shine!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "mission_task": DialogueNode(
                    text="Opening Tasks! Put it on the board so we don't forget.",
                    emotion="wink", voice="hmm",
                ),
                "add_habit": DialogueNode(
                    text="Ooh, smart! Daily Habits stack up way faster than occasional cramming. Opening Social Stats!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "double_down": DialogueNode(
                    text="Going for Mastery ★ on {top_stat}, huh? I like that ambition—specialists are formidable too!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
            },
        ),

        # 9) Late-Night Curfew Negotiation (3 turns)
        ConversationTree(
            tree_id="late_night_pact",
            title="Midnight Curfew Pact",
            tags=("late_night",),
            priority=9,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Yawn... It's getting seriously late! What's still keeping you up at the desk?",
                    emotion="sad", voice="yareyare", particle="zzz",
                    choices=(
                        DialogueChoice("Finishing one last thing—give me 15m!", "fifteen_more"),
                        DialogueChoice("Writing down thoughts so I can sleep.", "night_journal"),
                        DialogueChoice("You're right—time to wind down!", "wind_down"),
                    ),
                ),
                "fifteen_more": DialogueNode(
                    text="Alright, ONE 15-minute final lap! Should I set a hard stop timer so midnight doesn't sneak up on you?",
                    emotion="serious", voice="hoi",
                    choices=(
                        DialogueChoice("Yes, set a 15m 'Lights Out' timer!", "lights_out_timer",
                                       action={"type": "start_timer", "minutes": 15, "label": "Lights Out!"}),
                        DialogueChoice("I'll wrap up right after this step.", "wrap_promise"),
                    ),
                ),
                "lights_out_timer": DialogueNode(
                    text="15-minute Lights Out timer set! Finish strong, save your work, then straight to bed!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "wrap_promise": DialogueNode(
                    text="I'm holding you to that! Tomorrow's energy starts with tonight's sleep.",
                    emotion="worried", voice="yareyare", particle="zzz",
                ),
                "night_journal": DialogueNode(
                    text="Smart trick! Dumping tomorrow's thoughts into Notes stops your brain from looping on the pillow.",
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Open a Daily Journal note!", "open_journal",
                                       action={"type": "open_new_note", "template": "journal"},
                                       stat_reward=("kindness", 1, "Evening brain-dump")),
                        DialogueChoice("All clear in my head now—good night!", "wind_down"),
                    ),
                ),
                "open_journal": DialogueNode(
                    text="Opening a fresh Journal note! Jot down 3 quick thoughts, close the screen, and rest easy.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "wind_down": DialogueNode(
                    text="Now THAT'S a wise decision! Want me to switch into Sleep Mode while you head to bed?",
                    emotion="wink", voice="hmm", particle="heart",
                    choices=(
                        DialogueChoice("Good night, Morgana! (Sleep Mode)", "go_sleep",
                                       action={"type": "sleep_mode", "enabled": True}),
                        DialogueChoice("Good night! Stay awake for a minute.", "good_night"),
                    ),
                ),
                "go_sleep": DialogueNode(
                    text="Sweet dreams! Entering Sleep Mode—see you bright and sharp tomorrow!",
                    emotion="sad", voice="yareyare", particle="zzz",
                ),
                "good_night": DialogueNode(
                    text="Rest well! You earned a full night's recovery.",
                    emotion="wink", voice="hmm", particle="heart",
                ),
            },
        ),

        # 10) Quick Study Pop Quiz (3 turns — Knowledge challenge!)
        ConversationTree(
            tree_id="study_quiz",
            title="Quick Pop Quiz",
            tags=("any", "study", "morning", "afternoon"),
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Psst! Surprise pop quiz! Which study habit boosts long-term memory the MOST according to cognitive science?",
                    emotion="smug", voice="oh_asking", particle="question",
                    choices=(
                        DialogueChoice("Highlighting & re-reading 5 times.", "q_wrong_1"),
                        DialogueChoice("Active recall + spaced repetition.", "q_right"),
                        DialogueChoice("Pulling an all-nighter before the test.", "q_wrong_2"),
                    ),
                ),
                "q_right": DialogueNode(
                    text="DING DING DING! Active recall + spaced repetition! Testing yourself from memory wires it in way stronger than re-reading!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Give me a bonus question!", "bonus_q",
                                       stat_reward=("knowledge", 1, "Study Pop Quiz")),
                        DialogueChoice("Sweet! Time to jot study notes.", "quiz_notes",
                                       action={"type": "open_new_note", "template": "study"},
                                       stat_reward=("knowledge", 1, "Study Pop Quiz")),
                    ),
                ),
                "bonus_q": DialogueNode(
                    text="Bonus round: What's the 20-20-20 rule for protecting your eyes at a computer?",
                    emotion="smug", voice="dousuru", particle="question",
                    choices=(
                        DialogueChoice("Every 20m, look 20ft away for 20s.", "bonus_right"),
                        DialogueChoice("Drink 20 coffees in 20 minutes.", "bonus_joke"),
                    ),
                ),
                "bonus_right": DialogueNode(
                    text="Spot on! Top of the class! Now don't forget to actually give your eyes that break today!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "bonus_joke": DialogueNode(
                    text="Haha, your heart would launch into orbit! It's every 20 minutes, look 20 feet away for 20 seconds—give those eyes a break!",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "quiz_notes": DialogueNode(
                    text="Opening a Study Summary template in Notes—summarize in your own words without peeking first!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "q_wrong_1": DialogueNode(
                    text="Ooh, tricky trap! Re-reading feels smooth, but Active Recall (closing the book and explaining it yourself) wins by a mile!",
                    emotion="wink", voice="naa",
                    choices=(
                        DialogueChoice("Good to know! Let's try it in Notes.", "quiz_notes",
                                       action={"type": "open_new_note", "template": "study"}),
                        DialogueChoice("Haha, I'll remember that next time!", "quiz_ok"),
                    ),
                ),
                "q_wrong_2": DialogueNode(
                    text="NOOO! All-nighters wreck memory consolidation—sleep is literally when your brain hits 'Save'!",
                    emotion="shout", voice="hoi", particle="alert",
                    choices=(
                        DialogueChoice("Message received—no all-nighters!", "quiz_ok"),
                        DialogueChoice("Remind me when bedtime is.", "quiz_brief",
                                       action={"type": "open_briefing"}),
                    ),
                ),
                "quiz_ok": DialogueNode(
                    text="Heh, now you're armed with top-tier study tactics!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "quiz_brief": DialogueNode(
                    text="Opening your Briefing so you can pace the rest of the day without late-night crunches!",
                    emotion="wink", voice="hmm",
                ),
            },
        ),

        # 11) Daily One-Target Setter (3 turns)
        ConversationTree(
            tree_id="daily_target",
            title="Today's Main Target",
            tags=("any", "morning", "afternoon"),
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Let's lock in today's #1 target! If you could only finish ONE meaningful thing today, what category is it?",
                    emotion="serious", voice="dousuru", particle="sparkle",
                    choices=(
                        DialogueChoice("Deep work / coding / building.", "cc_build"),
                        DialogueChoice("Studying / learning something new.", "cc_study"),
                        DialogueChoice("Life admin / clearing small chores.", "cc_admin"),
                    ),
                ),
                "cc_build": DialogueNode(
                    text="Target acquired: Deep Craft! Want to drop distractions and run a dedicated Focus block for it?",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Lock it in! (25m Focus)", "cc_go_25",
                                       action={"type": "start_focus", "minutes": 25},
                                       stat_reward=("proficiency", 1, "Daily Main Target")),
                        DialogueChoice("Go big—45m Deep Focus!", "cc_go_45",
                                       action={"type": "start_focus", "minutes": 45},
                                       stat_reward=("guts", 1, "45m Deep Target")),
                    ),
                ),
                "cc_study": DialogueNode(
                    text="Target acquired: Knowledge Upgrade! Pairing a Study Note with a 25m focus block works wonders.",
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Open Study Notes & let's learn!", "cc_note",
                                       action={"type": "open_new_note", "template": "study"},
                                       stat_reward=("knowledge", 1, "Study Main Target")),
                        DialogueChoice("Start 25m Study Focus!", "cc_go_25",
                                       action={"type": "start_focus", "minutes": 25}),
                    ),
                ),
                "cc_admin": DialogueNode(
                    text="Target acquired: Clean Slate! Clearing 3 tiny chores in a row feels ridiculously satisfying.",
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Open Tasks and sweep 'em!", "cc_tasks",
                                       action={"type": "open_tasks"},
                                       stat_reward=("kindness", 1, "Life admin sweep")),
                        DialogueChoice("Set a 15m speed-run timer!", "cc_15",
                                       action={"type": "start_timer", "minutes": 15, "label": "Chore Speedrun"}),
                    ),
                ),
                "cc_go_25": DialogueNode(
                    text="Mission underway! 25 minutes on the clock—go make real progress!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "cc_go_45": DialogueNode(
                    text="WHOA—45-minute deep dive! Remember to take a real break afterward. Let's roll!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "cc_note": DialogueNode(
                    text="Study template ready! Capture the key ideas in your own words.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "cc_tasks": DialogueNode(
                    text="Opening Tasks! Knock them down one by one—watch the Proficiency roll in!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "cc_15": DialogueNode(
                    text="15-minute chore speedrun GO! Beat the clock!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
            },
        ),

        # 12) Creative / Study / Writing Flow Companion (3 turns)
        ConversationTree(
            tree_id="creative_companion",
            title="Creative & Study Check-In",
            tags=("creative", "study", "writing"),
            priority=6,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Ooh, deep work in {app_name}! Are you feeling inspired right now, or hitting a blank-page wall?",
                    emotion="neutral", voice="naa", particle="music",
                    choices=(
                        DialogueChoice("Ideas are flowing—feeling great!", "cr_flow"),
                        DialogueChoice("Hitting a creative/focus wall...", "cr_wall"),
                        DialogueChoice("Almost done, just polishing!", "cr_polish"),
                    ),
                ),
                "cr_flow": DialogueNode(
                    text="Whoa, don't let me interrupt the magic! Want me to quietly run a focus clock so you earn Knowledge/Proficiency too?",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Yes, quiet 25m Focus block!", "cr_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("No clock—just cheer from the corner!", "cr_cheer",
                                       stat_reward=("charm", 1, "Creative flow")),
                    ),
                ),
                "cr_wall": DialogueNode(
                    text="Blank walls hate messy rough drafts! Write the worst, ugliest version first—you can't polish an empty canvas.",
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Let's do a messy brain-dump in Notes!", "cr_dump",
                                       action={"type": "open_new_note", "template": "idea"}),
                        DialogueChoice("I'll walk around for 5 minutes first.", "cr_walk",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "cr_polish": DialogueNode(
                    text="The home stretch! Watch out for perfectionism—finished and shipped beats 'forever 99%'!",
                    emotion="wink", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("True—let's finish it in 15m!", "cr_15",
                                       action={"type": "start_timer", "minutes": 15, "label": "Final Polish"},
                                       stat_reward=("proficiency", 1, "Finishing strong")),
                        DialogueChoice("Thanks Mona, almost there!", "cr_cheer"),
                    ),
                ),
                "cr_focus": DialogueNode(
                    text="25 minutes started! I'll stay quiet and root for you from right here.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "cr_cheer": DialogueNode(
                    text="You've got this! Can't wait to see what you create!",
                    emotion="excited", voice="oh_excited", particle="heart",
                ),
                "cr_dump": DialogueNode(
                    text="Opening a fresh Idea note! No filter, no editing—just get every messy thought out!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "cr_walk": DialogueNode(
                    text="5-minute walk timer set! Your subconscious solves creative knots best while moving.",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "cr_15": DialogueNode(
                    text="15-minute polish sprint! Wrap it with pride!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
            },
        ),

        # 13) All-Clear Celebration & Reflection (2-3 turns)
        ConversationTree(
            tree_id="victory_lap",
            title="Victory Lap Reflection",
            tags=("all_done", "streak", "evening"),
            priority=5,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Look at you—{screen_time} active today and a {streak}-day streak! How do you feel about today so far?",
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Proud! Got solid work done.", "v_proud"),
                        DialogueChoice("Could've been better, honestly.", "v_meh"),
                        DialogueChoice("Ready to log a quick reflection!", "v_journal",
                                       action={"type": "open_new_note", "template": "journal"}),
                    ),
                ),
                "v_proud": DialogueNode(
                    text="YES! Give yourself credit—momentum builds when you actually celebrate your wins!",
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Check my Social Stats star!", "v_stats",
                                       action={"type": "open_stats"},
                                       stat_reward=("charm", 1, "Celebrated daily win")),
                        DialogueChoice("Couldn't have done it without you, Mona!", "v_thanks"),
                    ),
                ),
                "v_meh": DialogueNode(
                    text="Hey, don't be too harsh on yourself! Showing up on tough days takes way more Guts than easy days.",
                    emotion="wink", voice="naa", particle="heart",
                    choices=(
                        DialogueChoice("Thanks... let's pick 1 small win.", "v_one",
                                       action={"type": "open_tasks"},
                                       stat_reward=("guts", 1, "Resilience check-in")),
                        DialogueChoice("Tomorrow's a fresh start!", "v_tomorrow"),
                    ),
                ),
                "v_journal": DialogueNode(
                    text="Opening a Daily Journal note! Write one win and one lesson—takes 60 seconds and sharpens tomorrow.",
                    emotion="wink", voice="hmm", particle="music",
                ),
                "v_stats": DialogueNode(
                    text="Pulling up your Social Stats—admire that progress!",
                    emotion="smug", voice="hmm", particle="music",
                ),
                "v_thanks": DialogueNode(
                    text="H-heh, well, of course I'm an S-tier partner! Same team tomorrow too!",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "v_one": DialogueNode(
                    text="One small check-off flips the whole mood. Let's see what's quick on Tasks!",
                    emotion="smug", voice="hmm",
                ),
                "v_tomorrow": DialogueNode(
                    text="That's the winning mindset! Rest well and we'll hit tomorrow running.",
                    emotion="wink", voice="hmm", particle="sparkle",
                ),
            },
        ),

        # 14) Meeting / Social Decompress (2-3 turns)
        ConversationTree(
            tree_id="social_battery",
            title="Social Battery Check",
            tags=("social", "meeting"),
            priority=6,
            start="root",
            nodes={
                "root": DialogueNode(
                    text="Busy on {app_name}! How's your social battery holding up right now?",
                    emotion="neutral", voice="naa", particle="question",
                    choices=(
                        DialogueChoice("Good! Capturing notes from a chat/call.", "sb_notes"),
                        DialogueChoice("Getting distracted by notifications...", "sb_distract"),
                        DialogueChoice("Social battery's drained—need quiet!", "sb_drained"),
                    ),
                ),
                "sb_notes": DialogueNode(
                    text="Smart move! Want a clean Meeting Notes template so action items don't get buried in chat?",
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Yes, open Meeting Notes!", "sb_open_meet",
                                       action={"type": "open_new_note", "template": "meeting"},
                                       stat_reward=("charm", 1, "Captured meeting notes")),
                        DialogueChoice("Got it covered—thanks Mona!", "sb_ok"),
                    ),
                ),
                "sb_distract": DialogueNode(
                    text="Notification pings are the ultimate Shadow ambush! Want a 25m Focus bubble to tune them out?",
                    emotion="serious", voice="dousuru", particle="alert",
                    choices=(
                        DialogueChoice("Yes—25m deep-focus shield!", "sb_shield",
                                       action={"type": "start_focus", "minutes": 25},
                                       stat_reward=("guts", 1, "Blocked distractions")),
                        DialogueChoice("Just finishing one reply, then back!", "sb_ok"),
                    ),
                ),
                "sb_drained": DialogueNode(
                    text="Oof, calls and chats drain stamina fast! Take 5 quiet minutes—no talking, just breathe.",
                    emotion="worried", voice="yareyare", particle="heart",
                    choices=(
                        DialogueChoice("Start a 5m quiet recharge.", "sb_break",
                                       action={"type": "start_break", "minutes": 5},
                                       stat_reward=("kindness", 1, "Quiet recharge")),
                        DialogueChoice("Thanks, just needed to vent a sec!", "sb_ok"),
                    ),
                ),
                "sb_open_meet": DialogueNode(
                    text="Meeting Notes template open! Remember you can highlight any action item and hit '→ TASK'!",
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "sb_shield": DialogueNode(
                    text="Focus shield UP! Mute those pings for 25 minutes and reclaim your attention!",
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "sb_break": DialogueNode(
                    text="5-minute quiet recharge rolling—enjoy the silence!",
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "sb_ok": DialogueNode(
                    text="Gotcha! Holler anytime you want a focus timer or quick note.",
                    emotion="wink", voice="hmm",
                ),
            },
        ),
    ]
    return {t.tree_id: t for t in trees}


class DialogueEngine:
    """Manages active multi-turn choice conversations and context-aware selection."""

    def __init__(self, memory: DialogueMemoryStore) -> None:
        self.memory = memory
        self.trees: dict[str, ConversationTree] = _build_trees()
        self.active_tree: ConversationTree | None = None
        self.active_node_id: str | None = None
        self._ctx_snapshot: dict = {}

    @property
    def is_active(self) -> bool:
        return self.active_tree is not None and self.active_node_id is not None

    def cancel(self) -> None:
        self.active_tree = None
        self.active_node_id = None

    def _format_text(self, template: str) -> str:
        c = self._ctx_snapshot
        pending = int(c.get("pending_tasks", 0))
        pending_summary = (
            f"{pending} pending {'task' if pending == 1 else 'tasks'}"
            if pending else "a spotless task list"
        )
        mapping = {
            "app_name": c.get("active_app_name") or "your desktop",
            "screen_time": c.get("screen_time_text") or "a bit",
            "continuous_time": c.get("continuous_time_text") or "a while",
            "pending_tasks": str(pending),
            "pending_summary": pending_summary,
            "overdue_count": str(int(c.get("overdue_count", 0))),
            "top_task": (c.get("top_task") or "your top task")[:32],
            "lowest_stat": c.get("lowest_stat_label") or "Knowledge",
            "top_stat": c.get("top_stat_label") or "Proficiency",
            "streak": str(int(c.get("streak_days", 0))),
            "fav_drink": self.memory.get_pref("drink", "some coffee or water"),
        }
        try:
            return template.format(**mapping)
        except Exception:
            return template

    def _render_node(self, node: DialogueNode) -> dict:
        return {
            "text": self._format_text(node.text),
            "emotion": node.emotion,
            "voice": node.voice,
            "particle": node.particle,
            "choices": [self._format_text(ch.label) for ch in node.choices],
            "has_choices": bool(node.choices),
        }

    def start_tree(self, tree_id: str, ctx: dict) -> dict | None:
        tree = self.trees.get(tree_id)
        if tree is None:
            return None
        self._ctx_snapshot = dict(ctx or {})
        self.active_tree = tree
        self.active_node_id = tree.start
        self.memory.record_tree(tree.tree_id)
        return self._render_node(tree.nodes[tree.start])

    def start_contextual(self, ctx: dict) -> dict:
        """Pick the most relevant non-repeating conversation tree for `ctx`."""
        self._ctx_snapshot = dict(ctx or {})
        hour = int(ctx.get("hour", datetime.now().hour))
        active_cat = str(ctx.get("active_app_cat") or "")
        overdue = int(ctx.get("overdue_count", 0))
        pending = int(ctx.get("pending_tasks", 0))
        streak = int(ctx.get("streak_days", 0))
        continuous_s = int(ctx.get("continuous_s", 0))

        active_tags: set[str] = {"any"}
        if 5 <= hour < 12:
            active_tags.add("morning")
        elif 12 <= hour < 17:
            active_tags.add("afternoon")
        elif 17 <= hour < 22:
            active_tags.add("evening")
        else:
            active_tags.add("late_night")
        if active_cat:
            active_tags.add(active_cat)
        if overdue > 0:
            active_tags.add("overdue")
        if pending == 0:
            active_tags.add("all_done")
        if streak >= 2:
            active_tags.add("streak")
        if continuous_s >= 45 * 60:
            active_tags.add("screen_long")

        recent = list(self.memory.data.get("recent_trees") or [])
        recent_set = set(recent[-5:])

        scored: list[tuple[float, ConversationTree]] = []
        for tree in self.trees.values():
            matched = active_tags.intersection(tree.tags)
            if not matched:
                continue
            # Do not randomly pick screen_time_nudge unless screen_long actually matched
            if tree.tree_id == "screen_time_nudge" and "screen_long" not in matched:
                continue
            specific = matched - {"any"}
            score = float(tree.priority) + len(specific) * 3.0 + random.uniform(0.0, 2.8)
            if tree.tree_id in recent_set:
                score -= 12.0
            if recent and tree.tree_id == recent[-1]:
                score -= 20.0
            scored.append((score, tree))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        chosen = scored[0][1] if scored else self.trees["energy_strategy"]
        return self.start_tree(chosen.tree_id, ctx)

    def choose(self, index: int) -> dict | None:
        """Advance the active conversation by selecting choice `index`.

        Returns a dict with keys: `node` (rendered next node or None), `action` (dict or None),
        `stat_reward` (tuple or None, already checked against daily anti-farming cap), `ended` (bool).
        """
        if not self.is_active or self.active_tree is None or self.active_node_id is None:
            return None
        cur_node = self.active_tree.nodes.get(self.active_node_id)
        if cur_node is None or index < 0 or index >= len(cur_node.choices):
            self.cancel()
            return None

        choice = cur_node.choices[index]
        if choice.memory_set:
            for k, v in choice.memory_set.items():
                self.memory.set_pref(k, v)
        if choice.memory_inc:
            self.memory.inc_counter(choice.memory_inc[0], choice.memory_inc[1])

        reward = None
        if choice.stat_reward and self.memory.consume_chat_stat_reward():
            reward = choice.stat_reward

        next_id = choice.next_node
        if next_id and next_id in self.active_tree.nodes:
            self.active_node_id = next_id
            nxt = self.active_tree.nodes[next_id]
            rendered = self._render_node(nxt)
            ended = not rendered["has_choices"]
            if ended:
                self.cancel()
            return {
                "node": rendered,
                "action": dict(choice.action) if choice.action else None,
                "stat_reward": reward,
                "ended": ended,
            }

        self.cancel()
        return {
            "node": None,
            "action": dict(choice.action) if choice.action else None,
            "stat_reward": reward,
            "ended": True,
        }
