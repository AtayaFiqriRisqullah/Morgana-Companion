# -*- coding: utf-8 -*-
"""Multi-turn, app-aware Persona 5 Choice Prompt engine & memory store (Qt-free).

Powers Morgana's interactive branching conversations (2-4 turns each) with:
  * Deep local Active App / Window Awareness: weaves in the actual app, parsed window
    topic (file/project/doc/video/page/game), dwell duration, recent app switches,
    daily screen-time breakdown, and pending/overdue tasks.
  * Dynamic non-scripted feel: multi-variant natural phrasings per node with anti-repeat
    memory, context-filtered choices, and memory-aware reactions (`fav_drink`,
    `work_style`, `cat_tease_count`).
  * Real actionable outcomes: launches Focus sprints, Break timers, pre-titled Notes,
    Tasks, Social Stats, Daily Briefing, or Sleep Mode—without handing out free
    Social Stat points just for chatting.
  * Zero Qt dependency so the entire conversation graph can be validated headless.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storage_manager import write_json_atomic

log = logging.getLogger("pet.dialogue_engine")

_WORK_CATS = frozenset({"coding", "study", "writing", "creative"})
_LEISURE_CATS = frozenset({"gaming", "media", "social"})


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DialogueChoice:
    label: str
    next_node: str
    action: dict[str, Any] | None = None            # e.g. {"type": "start_focus", "minutes": 25}
    memory_set: dict[str, Any] | None = None        # e.g. {"drink": "coffee"}
    memory_inc: tuple[str, int] | None = None       # e.g. ("cat_tease_count", 1)
    require_flags: tuple[str, ...] = ()             # choice shown only if ALL flags are present
    exclude_flags: tuple[str, ...] = ()             # choice hidden if ANY flag is present


@dataclass(frozen=True)
class DialogueNode:
    texts: tuple[str, ...]                          # 1+ organic phrasings (rotated without repeat)
    emotion: str = "neutral"
    voice: str = "naa"
    particle: str = ""
    choices: tuple[DialogueChoice, ...] = ()


@dataclass(frozen=True)
class ConversationTree:
    tree_id: str
    title: str
    tags: tuple[str, ...]                           # context tags ("coding", "browsing", "app_distracted", ...)
    start: str
    nodes: dict[str, DialogueNode]
    priority: int = 0
    app_aware: bool = False                         # boosted when matching live active window context


# ---------------------------------------------------------------------------
# Persistent Lightweight Companion Memory (`dialogue_memory.json`)
# ---------------------------------------------------------------------------
class DialogueMemoryStore:
    """Remembers lightweight conversational preferences, counters, and anti-repeat history."""

    DEFAULT_DATA = {
        "prefs": {},
        "counters": {},
        "recent_trees": [],
        "recent_variants": [],
        "total_chats": 0,
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        raw: Any = None
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("Failed to load %s: %s", self.path.name, e)
                raw = None
        if not isinstance(raw, dict):
            return {
                "prefs": {},
                "counters": {},
                "recent_trees": [],
                "recent_variants": [],
                "total_chats": 0,
            }
        return {
            "prefs": dict(raw.get("prefs") or {}) if isinstance(raw.get("prefs"), dict) else {},
            "counters": dict(raw.get("counters") or {}) if isinstance(raw.get("counters"), dict) else {},
            "recent_trees": list(raw.get("recent_trees") or [])[-12:],
            "recent_variants": list(raw.get("recent_variants") or [])[-24:],
            "total_chats": int(raw.get("total_chats") or 0),
        }

    def save(self) -> None:
        write_json_atomic(self.path, self._data)

    def get_pref(self, key: str, default: Any = None) -> Any:
        return self._data["prefs"].get(key, default)

    def set_pref(self, key: str, value: Any) -> None:
        self._data["prefs"][str(key)] = value
        self.save()

    def get_counter(self, key: str) -> int:
        try:
            return int(self._data["counters"].get(key, 0))
        except (TypeError, ValueError):
            return 0

    def inc_counter(self, key: str, delta: int = 1) -> int:
        val = self.get_counter(key) + int(delta)
        self._data["counters"][str(key)] = val
        self.save()
        return val

    def recent_trees(self) -> list[str]:
        return list(self._data["recent_trees"])

    def record_tree(self, tree_id: str) -> None:
        rec = [t for t in self._data["recent_trees"] if t != tree_id]
        rec.append(tree_id)
        self._data["recent_trees"] = rec[-10:]
        self._data["total_chats"] = int(self._data.get("total_chats") or 0) + 1
        self.save()

    def pick_variant(self, node_key: str, variants: tuple[str, ...]) -> str:
        """Choose a phrasing from `variants` while avoiding recently spoken phrasings."""
        if not variants:
            return ""
        if len(variants) == 1:
            return variants[0]
        recent = set(self._data.get("recent_variants") or [])
        indexed = [(f"{node_key}:{i}", txt) for i, txt in enumerate(variants)]
        fresh = [pair for pair in indexed if pair[0] not in recent]
        chosen_id, chosen_txt = random.choice(fresh or indexed)
        hist = [v for v in self._data["recent_variants"] if v != chosen_id]
        hist.append(chosen_id)
        self._data["recent_variants"] = hist[-24:]
        return chosen_txt


# ---------------------------------------------------------------------------
# Safe Template Formatter
# ---------------------------------------------------------------------------
class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _fmt(template: str, variables: dict[str, Any]) -> str:
    try:
        return template.format_map(_SafeFormat(variables))
    except Exception:
        return template


def _clip(text: str, limit: int = 28) -> str:
    s = (text or "").strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# 16 Multi-Turn, Live App-Aware & Companion Conversation Trees
# ---------------------------------------------------------------------------
def _build_trees() -> list[ConversationTree]:
    return [
        # ===================================================================
        # LIVE ACTIVE APP / WINDOW AWARENESS TREES (1-8)
        # ===================================================================

        # 1) Live Coding Partner (VS Code, Cursor, PyCharm, GitHub, Terminal)
        ConversationTree(
            tree_id="coding_live",
            title="Live Coding Partner",
            tags=("coding",),
            priority=9,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Spotted you in {app_phrase}{dwell_clause}! Shipping something new, or hunting down a tricky bug?",
                        "Deep in {app_phrase}{switch_clause}! How's the code behaving right now?",
                        "Looks like a solid session in {app_phrase}{dwell_clause}. Smooth sailing, or stuck on a weird edge case?",
                        "Peeking at {app_phrase}! Are you in the zone right now, or could you use a rubber-duck partner?",
                    ),
                    emotion="smug", voice="naa", particle="sparkle",
                    choices=(
                        DialogueChoice("In flow—building something cool!", "smooth"),
                        DialogueChoice("Stuck on a sneaky bug...", "stuck_bug"),
                        DialogueChoice("Planning architecture / refactoring.", "designing"),
                    ),
                ),
                "smooth": DialogueNode(
                    texts=(
                        "Nice!! Momentum in {app_name} is gold—don't forget to save or commit before context-switching!",
                        "Love to hear it! Want to protect that flow in {app_name} with a timer, or jot down notes while it's fresh?",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Lock in a 25m Focus sprint!", "code_sprint",
                                       action={"type": "start_focus", "minutes": 25},
                                       memory_set={"work_style": "sprint"}),
                        DialogueChoice("Jot '{topic_or_code}' notes while it's fresh.", "code_note",
                                       action={"type": "open_new_note", "template": "study", "title_key": "note_title_app"}),
                        DialogueChoice("No interruption—just keep cheering!", "code_cheer"),
                    ),
                ),
                "code_sprint": DialogueNode(
                    texts=(
                        "25-minute coding sprint started! I'll keep an eye on the clock while you crush {app_phrase}!",
                        "Focus clock running! Stay locked into {app_name}—we'll celebrate when the bell rings!",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "code_note": DialogueNode(
                    texts=(
                        "Opening a fresh Note titled '{note_title_app}'! Writing down why something works saves hours later.",
                        "Smart habit—opening Notes for '{note_title_app}' so future-you doesn't have to re-learn it!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "code_cheer": DialogueNode(
                    texts=(
                        "Gotcha—stealth cheerleader mode! Shout if you hit a snag in {app_name}.",
                        "Say no more! I'll keep quiet in the corner while you work your magic.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "stuck_bug": DialogueNode(
                    texts=(
                        "Rubber-duck duty! Tell me what's acting weird in {app_phrase}—is it logic, a crash, or brain fog?",
                        "Ooh, sneaky bug in {app_name}! What kind of trouble is it giving you?",
                    ),
                    emotion="serious", voice="oh_asking", particle="question",
                    choices=(
                        DialogueChoice("State / logic doesn't match what I expect.", "bug_logic"),
                        DialogueChoice("Crash or error I can't pinpoint yet.", "bug_trace"),
                        DialogueChoice("Honestly stared too long—need fresh eyes.", "bug_break"),
                    ),
                ),
                "bug_logic": DialogueNode(
                    texts=(
                        "Classic hidden-assumption trap! Write down the ONE thing in {topic_or_code} you're sure can't be wrong—9 times out of 10, the bug is hiding right there!",
                        "Try explaining the exact input and expected output out loud! Half the time the bug shows itself mid-sentence.",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Trace it step-by-step in Notes.", "bug_note",
                                       action={"type": "open_new_note", "template": "idea", "title_key": "note_title_debug"}),
                        DialogueChoice("Wait—I just thought of what to check!", "bug_aha"),
                    ),
                ),
                "bug_trace": DialogueNode(
                    texts=(
                        "Shrink the reproduction first! Cut the problem in half twice—once you know which half fails, the bug has nowhere to hide.",
                        "Check the exact line right before the error—not just where it explodes. Want to log a task or trace it in Notes?",
                    ),
                    emotion="serious", voice="dousuru",
                    choices=(
                        DialogueChoice("Good call—going to isolate it now!", "bug_aha"),
                        DialogueChoice("Open Tasks so I track this fix cleanly.", "bug_task",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "bug_break": DialogueNode(
                    texts=(
                        "100% real—fresh eyes find in 2 minutes what tired eyes miss for an hour! Grab {fav_drink} and step back for 5 minutes?",
                        "Your brain solves bugs in the background the second you look away from {app_name}. Want a 5-minute reset?",
                    ),
                    emotion="wink", voice="naa",
                    choices=(
                        DialogueChoice("Yes—start a 5m fresh-eyes break!", "bug_rest",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("One more quick check, then I'll rest.", "bug_aha"),
                    ),
                ),
                "bug_note": DialogueNode(
                    texts=(
                        "Opening '{note_title_debug}' in Notes! Bullet out what you expected vs what actually happened.",
                        "Debug scratchpad ready! Mapping out the flow usually exposes the culprit fast.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "bug_aha": DialogueNode(
                    texts=(
                        "AHA! Look at that spark—go squash it!",
                        "Nice!! Go get that fix—I'm rooting for you!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "bug_task": DialogueNode(
                    texts=(
                        "Opening Tasks! Put it on the board so you can tackle it cleanly without losing track.",
                        "Good move—capture the fix in Tasks and come back with a clear head.",
                    ),
                    emotion="smug", voice="hmm",
                ),
                "bug_rest": DialogueNode(
                    texts=(
                        "5-minute fresh-eyes timer running! Step away from the keyboard—the answer might hit you before the timer even ends!",
                        "Break started! Stretch your shoulders and grab {fav_drink}—I'll call you back in 5.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "designing": DialogueNode(
                    texts=(
                        "Ooh, architecture mode in {app_phrase}! Sketching the rough pieces in Notes first keeps code way cleaner later.",
                        "Big-picture thinking! Always feels great when a clean design clicks before writing a single line.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                    choices=(
                        DialogueChoice("Sketch '{topic_or_code}' design in Notes!", "design_note",
                                       action={"type": "open_new_note", "template": "idea", "title_key": "note_title_app"}),
                        DialogueChoice("Break milestones into Tasks!", "bug_task",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "design_note": DialogueNode(
                    texts=(
                        "Opening an Idea sketch titled '{note_title_app}'! Highlight any milestone later and hit '→ TASK' to promote it!",
                        "Notes is ready for '{note_title_app}'! Outline the moving parts first, then build step by step.",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
            },
        ),

        # 2) Distraction / Context-Switch Pivot (Gaming / Media / Social while tasks wait OR right after work!)
        ConversationTree(
            tree_id="distraction_pivot",
            title="Context-Switch Check-In",
            tags=("app_distracted",),
            priority=11,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Hey—noticed you over in {app_phrase}{switch_clause}! Intentional breather, or is '{top_task_short}' making you dodge a bit?",
                        "Whoa, {app_phrase} while {pending_summary} on deck! Planned break, or did distraction ambush you?",
                        "Spotted {app_phrase}{switch_clause}! Taking a quick recharge before '{top_task_short}', or did you wander off?",
                    ),
                    emotion="smug", voice="oh_asking", particle="question",
                    choices=(
                        DialogueChoice("Earned break—just recharging!", "earned"),
                        DialogueChoice("Caught me! Help me get back to '{top_task_short}'.", "caught",
                                       require_flags=("has_top_task",)),
                        DialogueChoice("Guilty... I got sidetracked.", "caught",
                                       exclude_flags=("has_top_task",)),
                        DialogueChoice("Actually, it's related to what I'm working on!", "related"),
                    ),
                ),
                "earned": DialogueNode(
                    texts=(
                        "Fair enough—rest actually keeps your brain sharp! Want a quick break cap so {app_name} doesn't quietly eat 2 hours?",
                        "Totally valid! You've already got {screen_time} on the clock today. Want a gentle timer so you switch back smoothly?",
                    ),
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Smart—set a 15m break cap!", "cap_15",
                                       action={"type": "start_timer", "minutes": 15, "label": "Back from Break"}),
                        DialogueChoice("Make it 25m—proper recharge.", "cap_25",
                                       action={"type": "start_timer", "minutes": 25, "label": "Back from Break"}),
                        DialogueChoice("No timer—fully off-duty right now!", "off_duty"),
                    ),
                ),
                "cap_15": DialogueNode(
                    texts=(
                        "15-minute cap set! Enjoy {app_name} guilt-free—I'll tap your shoulder when time's up!",
                        "Got it, 15 minutes on the clock! Relax now, crush '{top_task_short}' right after!",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "cap_25": DialogueNode(
                    texts=(
                        "25-minute recharge timer locked in! Enjoy the break—I'll call you back smoothly.",
                        "Roger that, 25 minutes off the clock! Have fun in {app_name}!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "off_duty": DialogueNode(
                    texts=(
                        "Copy that—off-duty mode! Enjoy {app_name}, just don't stay up past bedtime!",
                        "No clock, no pressure! Have a good unwind!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "caught": DialogueNode(
                    texts=(
                        "Haha, zero guilt-trip—starting is always the hardest jump! Want a tiny friction-free bridge back in?",
                        "Points for honesty! How do you want to break the inertia cleanly?",
                    ),
                    emotion="wink", voice="naa",
                    choices=(
                        DialogueChoice("15m tiny warm-up sprint—let's go!", "warmup_15",
                                       action={"type": "start_focus", "minutes": 15}),
                        DialogueChoice("Open Tasks so I pick 1 easy win.", "open_t",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("5 more minutes in {app_name}, then back!", "five_more",
                                       action={"type": "start_timer", "minutes": 5, "label": "Back to Work"}),
                    ),
                ),
                "warmup_15": DialogueNode(
                    texts=(
                        "15-minute warm-up rolling! Once you get past minute 3, momentum does the heavy lifting!",
                        "Locked in for 15 minutes! Small start, big comeback!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "open_t": DialogueNode(
                    texts=(
                        "Opening Tasks! Pick just ONE small item to flip the switch back on.",
                        "Here's your Task list—knock out one quick target and build from there!",
                    ),
                    emotion="smug", voice="hmm",
                ),
                "five_more": DialogueNode(
                    texts=(
                        "5-minute countdown started! Finish up this clip or match, then we're back in action!",
                        "5 minutes on the clock! Wrap it cleanly in {app_name} and let's roll!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "related": DialogueNode(
                    texts=(
                        "Aha, fair play—good tutorials and references live in {app_name} too! Want to save key takeaways in Notes so you don't lose the tab?",
                        "Gotcha—research mode! Want a quick Note titled '{note_title_app}' to capture anything useful?",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Yes, open a Note for '{topic_or_app}'!", "rel_note",
                                       action={"type": "open_new_note", "template": "study", "title_key": "note_title_app"}),
                        DialogueChoice("All good—just watching & absorbing!", "off_duty"),
                    ),
                ),
                "rel_note": DialogueNode(
                    texts=(
                        "Opening '{note_title_app}' in Notes! Grab the gold nuggets before closing the tab!",
                        "Note ready! Capturing 3 bullet takeaways makes watching 10× more useful.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
            },
        ),

        # 3) Live Browser Awareness (Chrome, Edge, Firefox, Brave on general web pages!)
        ConversationTree(
            tree_id="browsing_live",
            title="Live Browser Check-In",
            tags=("browsing",),
            priority=8,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Noticed you browsing {app_phrase}{dwell_clause}! Researching something interesting, or going down a tab rabbit hole?",
                        "Peeking over at {app_phrase}{switch_clause}—looking up something for work, or just exploring?",
                        "Looks like {app_phrase} caught your attention! Anything worth saving before you open 12 more tabs?",
                    ),
                    emotion="neutral", voice="oh_asking", particle="question",
                    choices=(
                        DialogueChoice("Researching—worth saving notes!", "br_research"),
                        DialogueChoice("Just browsing / taking a breather.", "br_chill"),
                        DialogueChoice("Oops—fell into a tab rabbit hole!", "br_escape"),
                    ),
                ),
                "br_research": DialogueNode(
                    texts=(
                        "Nice!! Want me to spin up a Note for '{note_title_app}' so you don't lose track of what you found?",
                        "Smart move—half of browser tabs exist just because we forgot to jot down one insight! Open Notes?",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Open Note: '{note_title_app}'", "br_note",
                                       action={"type": "open_new_note", "template": "study", "title_key": "note_title_app"}),
                        DialogueChoice("Add a quick Task from this instead.", "br_task",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Just reading for now, thanks Mona!", "br_ok"),
                    ),
                ),
                "br_note": DialogueNode(
                    texts=(
                        "Opening Notes for '{note_title_app}'! Summarize it in two lines and you can close 5 tabs guilt-free!",
                        "Fresh Note ready! Use `#tags` so you can find it again in a second.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "br_task": DialogueNode(
                    texts=(
                        "Opening Tasks! Capture the follow-up while it's right in front of you.",
                        "Task board ready! Pick the right Social Stat for it too!",
                    ),
                    emotion="smug", voice="hmm",
                ),
                "br_chill": DialogueNode(
                    texts=(
                        "Nothing wrong with a curious wander! Want a gentle timer so you don't lose track of time, or just cruising?",
                        "Enjoy the surf! Just remember to blink and stretch if you've been reading long pages.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                    choices=(
                        DialogueChoice("Set a 10m gentle reminder.", "br_timer",
                                       action={"type": "start_timer", "minutes": 10, "label": "Browser Check-In"}),
                        DialogueChoice("Just cruising—I'm good!", "br_ok"),
                    ),
                ),
                "br_escape": DialogueNode(
                    texts=(
                        "Haha, the classic 30-tab ambush! Let's pull you out cleanly—what gets you back on track fastest?",
                        "Rescue mission! Close the noisy tabs—want a short Focus sprint or a look at your Tasks?",
                    ),
                    emotion="smug", voice="naa", particle="sparkle",
                    choices=(
                        DialogueChoice("Start a clean 20m Focus sprint!", "br_focus",
                                       action={"type": "start_focus", "minutes": 20}),
                        DialogueChoice("Show me my Tasks!", "br_task",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "br_timer": DialogueNode(
                    texts=(
                        "10-minute gentle nudge set! Browse away—I'll give you a wave when time's up.",
                        "Timer running for 10 minutes! Happy browsing!",
                    ),
                    emotion="wink", voice="hmm",
                ),
                "br_focus": DialogueNode(
                    texts=(
                        "20-minute focus block rolling! Close those extra tabs and watch your speed jump!",
                        "Focus started! Fresh slate—let's make the next 20 minutes count!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "br_ok": DialogueNode(
                    texts=(
                        "Roger that! I'll be right here if you find something cool to save.",
                        "Sounds good! Tap me anytime if you want to spin up a note or timer.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
            },
        ),

        # 4) Live Study / Reading / AI Research Partner (PDFs, Wikipedia, Docs, ChatGPT, Claude, Obsidian)
        ConversationTree(
            tree_id="study_live",
            title="Live Study Partner",
            tags=("study",),
            priority=9,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Hitting the books in {app_phrase}{dwell_clause}! Is the material clicking, or is it dense today?",
                        "Spotted you studying {app_phrase}{switch_clause}! Absorbing new Knowledge—how's your focus holding up?",
                        "Deep study mode in {app_phrase}! Want to lock it into memory with active recall, or keep reading?",
                    ),
                    emotion="smug", voice="naa", particle="sparkle",
                    choices=(
                        DialogueChoice("Clicking nicely—good study flow!", "st_flow"),
                        DialogueChoice("Pretty dense... need to summarize it.", "st_summary"),
                        DialogueChoice("Eyes are getting tired from reading.", "st_tired"),
                    ),
                ),
                "st_flow": DialogueNode(
                    texts=(
                        "Awesome!! When material clicks like that, running a Focus block locks in both momentum and Knowledge!",
                        "Love that rhythm! Want a quiet 25m Study Focus timer, or a Study Summary note to cement it?",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Start a 25m Study Focus block!", "st_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("Open Study Note: '{note_title_app}'", "st_note",
                                       action={"type": "open_new_note", "template": "study", "title_key": "note_title_app"}),
                        DialogueChoice("Just keep reading undisturbed!", "st_quiet"),
                    ),
                ),
                "st_summary": DialogueNode(
                    texts=(
                        "Best trick for dense topics: close {app_name} for 60 seconds and write 3 bullet points from memory in your own words!",
                        "Dense reading sticks 3× better when you summarize as you go instead of re-reading! Want a pre-titled Study Note?",
                    ),
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Yes—open '{note_title_app}' in Notes!", "st_note",
                                       action={"type": "open_new_note", "template": "study", "title_key": "note_title_app"}),
                        DialogueChoice("Let's do a focused 15m breakdown sprint.", "st_focus_15",
                                       action={"type": "start_focus", "minutes": 15}),
                    ),
                ),
                "st_tired": DialogueNode(
                    texts=(
                        "Don't force tired eyes through heavy text—remember the 20-20-20 rule! A 5-minute break actually helps your brain consolidate what you just read.",
                        "Your brain saves memories during short pauses! Grab {fav_drink} and rest your eyes for 5 minutes?",
                    ),
                    emotion="worried", voice="yareyare", particle="heart",
                    choices=(
                        DialogueChoice("Start a 5m eye-rest break!", "st_break",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("Just one more page, then I'll pause.", "st_quiet"),
                    ),
                ),
                "st_focus": DialogueNode(
                    texts=(
                        "25-minute Study Focus rolling! Finish this block and watch your Knowledge climb!",
                        "Study clock started! I'll keep watch while you conquer {app_phrase}!",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                ),
                "st_focus_15": DialogueNode(
                    texts=(
                        "15-minute breakdown sprint started! One tough section at a time—you've got this!",
                        "15 minutes on the clock! Slice {topic_or_app} into bite-sized pieces!",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "st_note": DialogueNode(
                    texts=(
                        "Opening Study Note '{note_title_app}'! Write the big ideas in your own words without peeking first!",
                        "Study template ready for '{note_title_app}'! Add `#study` or topic `#tags` so it's easy to review later.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "st_break": DialogueNode(
                    texts=(
                        "5-minute rest running! Look out a window, sip {fav_drink}, and let your brain absorb what you read.",
                        "Eye-rest timer set! Step back for 5 minutes—you'll come back sharper!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "st_quiet": DialogueNode(
                    texts=(
                        "Quiet study mode—I won't interrupt! Wave if you need a timer or scratchpad.",
                        "Got it, cheering silently from the corner! Happy studying!",
                    ),
                    emotion="wink", voice="hmm",
                ),
            },
        ),

        # 5) Live Writing & Creative Studio Companion (Word, Notion, Google Docs, Figma, Blender, Photoshop)
        ConversationTree(
            tree_id="writing_creative_live",
            title="Creative & Writing Companion",
            tags=("writing", "creative"),
            priority=9,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Ooh, craft mode in {app_phrase}{dwell_clause}! Are ideas flowing smoothly, or hitting a blank-page wall?",
                        "Spotted you creating in {app_phrase}{switch_clause}! Drafting, designing, or putting on final polish?",
                        "Looks like real creative work in {app_phrase}! How's the inspiration meter looking?",
                    ),
                    emotion="neutral", voice="naa", particle="music",
                    choices=(
                        DialogueChoice("Ideas are flowing—feeling great!", "cr_flow"),
                        DialogueChoice("Hitting a creative / blank-page wall...", "cr_wall"),
                        DialogueChoice("Almost done, just polishing!", "cr_polish"),
                    ),
                ),
                "cr_flow": DialogueNode(
                    texts=(
                        "Ooh, don't let me break the magic! Want a quiet Focus timer running behind {app_name}, or pure uninterrupted flow?",
                        "Love that energy! Should I keep a quiet 25m Focus clock while you create in {app_name}?",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Yes, quiet 25m Focus block!", "cr_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("No clock—just cheer from the corner!", "cr_cheer"),
                    ),
                ),
                "cr_wall": DialogueNode(
                    texts=(
                        "Blank walls hate messy rough drafts! Write or sketch the ugliest v0 first—you can't polish an empty canvas.",
                        "Creative block in {app_name}? Sometimes stepping away for 5 minutes or dumping messy ideas in Notes breaks the seal!",
                    ),
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Do a messy brain-dump in Notes!", "cr_dump",
                                       action={"type": "open_new_note", "template": "idea", "title_key": "note_title_app"}),
                        DialogueChoice("Take a 5m walk to shake it loose.", "cr_walk",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "cr_polish": DialogueNode(
                    texts=(
                        "The home stretch in {app_phrase}! Watch out for endless tweaking—finished and shipped beats 'forever 99%'!",
                        "Final polish stage! Set a crisp 15-minute finish line so perfectionism doesn't drag it out?",
                    ),
                    emotion="wink", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Yes—15m final-polish sprint!", "cr_15",
                                       action={"type": "start_timer", "minutes": 15, "label": "Final Polish"}),
                        DialogueChoice("Almost there—thanks Mona!", "cr_cheer"),
                    ),
                ),
                "cr_focus": DialogueNode(
                    texts=(
                        "25-minute creative Focus started! I'll stay quiet and root for you from right here.",
                        "Focus clock running! Make something you're proud of in {app_name}!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "cr_cheer": DialogueNode(
                    texts=(
                        "You've got this! Don't forget to hit Ctrl+S every now and then!",
                        "Cheering from the desktop! Can't wait to see how {topic_or_app} turns out!",
                    ),
                    emotion="excited", voice="oh_excited", particle="heart",
                ),
                "cr_dump": DialogueNode(
                    texts=(
                        "Opening '{note_title_app}' in Notes! Zero filter, zero editing—just get every messy idea out!",
                        "Idea scratchpad ready! Throw down 5 rough bullets and pick the sparkliest one.",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "cr_walk": DialogueNode(
                    texts=(
                        "5-minute walk timer set! Your subconscious solves creative knots best while moving.",
                        "Break running! Grab {fav_drink}, stretch, and come back with fresh eyes!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "cr_15": DialogueNode(
                    texts=(
                        "15-minute polish countdown GO! Wrap up {topic_or_app} with pride!",
                        "15 minutes on the clock—ship it clean!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
            },
        ),

        # 6) Live Media Vibe Check (YouTube, Spotify, Twitch, Netflix when NOT procrastinating)
        ConversationTree(
            tree_id="media_vibe",
            title="Media & Soundtrack Vibe",
            tags=("media",),
            priority=8,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Ooh, tuned into {app_phrase}! Is that your work/study soundtrack, or full chill mode?",
                        "Good vibes over on {app_phrase}{dwell_clause}! Background audio while working, or kicking back?",
                        "Spotted {app_phrase}! Are we queueing up focus music or taking a cozy watch break?",
                    ),
                    emotion="wink", voice="naa", particle="music",
                    choices=(
                        DialogueChoice("Background music/audio while I work!", "med_bg"),
                        DialogueChoice("Full chill break—just enjoying it!", "med_chill"),
                        DialogueChoice("Learning from a video / tutorial!", "med_learn"),
                    ),
                ),
                "med_bg": DialogueNode(
                    texts=(
                        "A good soundtrack makes everything twice as smooth! Want to pair {app_name} with a 25m Focus block?",
                        "Solid desk vibes! Ready to kick off a Focus timer while {app_name} plays in the background?",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Start a 25m Focus block!", "med_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("Just vibing—no timer needed!", "med_done"),
                    ),
                ),
                "med_chill": DialogueNode(
                    texts=(
                        "Respect! Real rest without guilt is underrated. Want a soft reminder timer later, or completely off the clock?",
                        "Nice!! Sit back, relax your shoulders, and enjoy {app_phrase}!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                    choices=(
                        DialogueChoice("Set a 20m cozy break timer.", "med_cap",
                                       action={"type": "start_timer", "minutes": 20, "label": "End of Media Break"}),
                        DialogueChoice("Completely off the clock!", "med_done"),
                    ),
                ),
                "med_learn": DialogueNode(
                    texts=(
                        "Video tutorials hit way harder when you jot down key timestamps or takeaways! Want a Note titled '{note_title_app}'?",
                        "Smart! Capture the key ideas from {app_phrase} in Notes so you don't have to scrub the video again later!",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Open Note: '{note_title_app}'", "med_note",
                                       action={"type": "open_new_note", "template": "study", "title_key": "note_title_app"}),
                        DialogueChoice("Just watching for now!", "med_done"),
                    ),
                ),
                "med_focus": DialogueNode(
                    texts=(
                        "25-minute Focus started! Let {app_name} set the tempo while you get things done!",
                        "Focus clock running! Great soundtrack + solid focus = unstoppable!",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "med_cap": DialogueNode(
                    texts=(
                        "20-minute timer set! Kick back and enjoy {app_phrase}—I'll wave when it's up.",
                        "Timer locked in for 20 minutes! Enjoy the show!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "med_note": DialogueNode(
                    texts=(
                        "Opening '{note_title_app}' in Notes! Hit the clock button in the toolbar to drop quick timestamps!",
                        "Study Note ready for '{note_title_app}'! Jot down the best parts while watching.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "med_done": DialogueNode(
                    texts=(
                        "Enjoy {app_name}! I'll be grooving right here on the desktop!",
                        "Sounds great—have a relaxing time!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
            },
        ),

        # 7) Live Gaming Companion (when playing a game without heavy overdue pressure)
        ConversationTree(
            tree_id="gaming_live",
            title="Gaming Session Check-In",
            tags=("gaming",),
            priority=8,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Ooh, jumping into {app_phrase}{dwell_clause}! Victory unwind, or sneaking in a quick match?",
                        "Game time in {app_phrase}! You've got {screen_time} logged today—how's the run going?",
                        "Spotted {app_phrase}! Having a good run so far?",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Having a blast—well-earned unwind!", "gm_fun"),
                        DialogueChoice("Just a quick match—keep me honest!", "gm_cap"),
                        DialogueChoice("Getting wrecked... send luck, Mona!", "gm_luck"),
                    ),
                ),
                "gm_fun": DialogueNode(
                    texts=(
                        "Awesome!! Everyone needs proper fun to recharge. Just remember to stretch your wrists between rounds!",
                        "Nice!! Have fun in {app_name}—and don't forget to sip {fav_drink} between matches!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "gm_cap": DialogueNode(
                    texts=(
                        "Smart move—'one more match' is the oldest trap in the book! How long should I set your wrap-up timer?",
                        "I've got your back! Pick a break cap so you finish on a high note:",
                    ),
                    emotion="smug", voice="dousuru",
                    choices=(
                        DialogueChoice("20 minutes—quick round!", "gm_20",
                                       action={"type": "start_timer", "minutes": 20, "label": "Wrap Up Game"}),
                        DialogueChoice("45 minutes—proper session!", "gm_45",
                                       action={"type": "start_timer", "minutes": 45, "label": "Wrap Up Game"}),
                    ),
                ),
                "gm_20": DialogueNode(
                    texts=(
                        "20-minute game timer set! Make this round count—GLHF!",
                        "20 minutes locked in! Have an awesome match!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "gm_45": DialogueNode(
                    texts=(
                        "45-minute session set! Enjoy {app_name}—I'll nudge you when it's time to wrap up!",
                        "45 minutes on the clock! Good luck, have fun!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "gm_luck": DialogueNode(
                    texts=(
                        "Sending 100% max crit luck your way!! Deep breath, watch their patterns, and turn the next round around!",
                        "You've got this!! Shake off the last round, sip {fav_drink}, and clutch the comeback!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
            },
        ),

        # 8) Live Social & Meeting Companion (Discord, WhatsApp, Slack, Zoom, Google Meet)
        ConversationTree(
            tree_id="social_meeting_live",
            title="Chat & Call Companion",
            tags=("social", "meeting"),
            priority=8,
            app_aware=True,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Spotted you in {app_phrase}{dwell_clause}! Quick catch-up, live call, or dodging notification overload?",
                        "Hanging out in {app_phrase}{switch_clause}! Need a Meeting Note scratchpad, or just checking messages?",
                        "Looks active over in {app_phrase}! How's your social battery holding up?",
                    ),
                    emotion="neutral", voice="naa", particle="heart",
                    choices=(
                        DialogueChoice("On a call / meeting—open Meeting Notes!", "sm_meeting",
                                       action={"type": "open_new_note", "template": "meeting", "title_key": "note_title_meeting"}),
                        DialogueChoice("Quick chat break—keeping it brief.", "sm_brief"),
                        DialogueChoice("Social battery's getting low...", "sm_drained"),
                    ),
                ),
                "sm_meeting": DialogueNode(
                    texts=(
                        "Meeting Notes template ready! Highlight any action item afterward and click '→ TASK' to turn it straight into a Task!",
                        "Opening '{note_title_meeting}'! I'll stay super quiet while you're on the call.",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "sm_brief": DialogueNode(
                    texts=(
                        "Good balance! Want a quick 10m nudge so chat pings don't quietly swallow your next hour?",
                        "Catching up is great for Charm & Kindness—want a timer to hop back into work smoothly?",
                    ),
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Set a 10m chat-break timer.", "sm_timer",
                                       action={"type": "start_timer", "minutes": 10, "label": "Back from Chat"}),
                        DialogueChoice("I'm good—almost done replying!", "sm_ok"),
                    ),
                ),
                "sm_drained": DialogueNode(
                    texts=(
                        "Calls and group chats burn way more mental energy than people realize! Take 5 quiet minutes away from messages?",
                        "Totally valid to mute notifications for a bit! Want a peaceful 5m quiet break or a silent solo Focus block?",
                    ),
                    emotion="worried", voice="yareyare", particle="heart",
                    choices=(
                        DialogueChoice("5m quiet screen-free reset.", "sm_break",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("Mute pings & do a quiet 25m Focus!", "sm_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                    ),
                ),
                "sm_timer": DialogueNode(
                    texts=(
                        "10-minute chat timer set! Say hi to everyone—I'll wave when it's time to switch back.",
                        "Got it, 10 minutes on the clock!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "sm_break": DialogueNode(
                    texts=(
                        "5-minute quiet recharge started! Mute the pings, sip {fav_drink}, and exhale.",
                        "Quiet break rolling! Give your brain a breather from notifications.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "sm_focus": DialogueNode(
                    texts=(
                        "Solo 25-minute Focus started! Peaceful headspace—just you and your work.",
                        "25-minute quiet Focus locked in! Notifications can wait.",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "sm_ok": DialogueNode(
                    texts=(
                        "Nice! Have a good chat—I'm right here when you're ready for the next move.",
                        "Sounds good! Keep that balance strong!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
            },
        ),

        # ===================================================================
        # SCREEN TIME, WELLNESS & SITUATIONAL TREES (9-12)
        # ===================================================================

        # 9) Screen-Time Interactive Break Nudge (triggered automatically after nonstop sitting)
        ConversationTree(
            tree_id="screen_time_nudge",
            title="Screen-Time Wellness Check",
            tags=("screen_long",),
            priority=12,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Hey! You've been glued to the screen for {continuous_time} straight ({screen_time} today{app_clause}). Time for a breather?",
                        "Heads up—{continuous_time} nonstop at the desk! Total today is {screen_time}. Want to stretch and rest your eyes?",
                        "Whoa, {continuous_time} without a break! Even champions need pit stops—ready for a quick 5-minute reset?",
                    ),
                    emotion="worried", voice="hoi", particle="alert",
                    choices=(
                        DialogueChoice("Good catch—start a 5m break!", "break_5",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("I'll stretch & drink water right now.", "stretch_now",
                                       action={"type": "record_break"}),
                        DialogueChoice("Just 15 more minutes, I'm in the zone!", "snooze_15",
                                       action={"type": "snooze_break", "minutes": 15},
                                       memory_inc=("break_snoozes", 1)),
                    ),
                ),
                "break_5": DialogueNode(
                    texts=(
                        "Yes! Look 20 feet away for 20 seconds, sip {fav_drink}, and shake out your wrists. I'll watch the timer!",
                        "5-minute break started! Stand up, roll your shoulders, and give your eyes a real horizon to look at!",
                    ),
                    emotion="excited", voice="oh_excited", particle="heart",
                ),
                "stretch_now": DialogueNode(
                    texts=(
                        "Awesome! Shoulders back, deep breath, big sip of water—break logged and your future self is thanking you!",
                        "Nice!! Quick stretch & hydration logged! Keep taking care of yourself!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "snooze_15": DialogueNode(
                    texts=(
                        "Alright, 15 minutes! Promise you'll at least blink and take a sip of {fav_drink} while wrapping up?",
                        "Okay, snoozed for 15 minutes—I'll check back right after so you don't forget!",
                    ),
                    emotion="uneasy", voice="yareyare",
                    choices=(
                        DialogueChoice("Deal! Sipping right now.", "deal_ok"),
                        DialogueChoice("Set a visible 15m countdown too.", "deal_timer",
                                       action={"type": "start_timer", "minutes": 15, "label": "Break Time!"}),
                    ),
                ),
                "deal_ok": DialogueNode(
                    texts=(
                        "Good! I'll check back in 15 minutes—no sneaking past Mona's watch!",
                        "Deal! Wrap that thought strong and then we stretch!",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "deal_timer": DialogueNode(
                    texts=(
                        "15-minute countdown locked in! Finish cleanly, then stretch!",
                        "Timer running—15 minutes and then it's break o'clock!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
            },
        ),

        # 10) Screen-Time & Daily Activity Recap (references real daily breakdown!)
        ConversationTree(
            tree_id="screen_time_recap",
            title="Screen-Time Pulse Check",
            tags=("screen_active",),
            priority=6,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Quick pulse check: you've logged {screen_time} active today—mostly {top_cat_summary}! How's your pace feeling?",
                        "Look at today's screen-time log: {screen_time} total, led by {top_cat_summary}. Feeling balanced so far?",
                    ),
                    emotion="smug", voice="dousuru", particle="sparkle",
                    choices=(
                        DialogueChoice("Good momentum—keep it rolling!", "rec_keep"),
                        DialogueChoice("Need to switch gears to something else.", "rec_pivot"),
                        DialogueChoice("Let's see my full Daily Briefing!", "rec_brief",
                                       action={"type": "open_briefing"}),
                    ),
                ),
                "rec_keep": DialogueNode(
                    texts=(
                        "Love that steady rhythm! Want to channel the next stretch into a Focus session, or just cruise at your own pace?",
                        "Solid pace! Don't forget short breaks every hour so your focus stays sharp till evening.",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Kick off a 25m Focus sprint!", "rec_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("I'll keep cruising at my own pace!", "rec_ok"),
                    ),
                ),
                "rec_pivot": DialogueNode(
                    texts=(
                        "Switching gears refreshes your brain fast! Want to check Tasks for a different stat or take a 5m reset first?",
                        "Smart awareness! Where do you want to steer the next 30 minutes?",
                    ),
                    emotion="wink", voice="naa",
                    choices=(
                        DialogueChoice("Check Tasks & pick a fresh target.", "rec_tasks",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Take a 5m reset break first.", "rec_break",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("Check Social Stats & Daily Habits.", "rec_stats",
                                       action={"type": "open_stats"}),
                    ),
                ),
                "rec_brief": DialogueNode(
                    texts=(
                        "Opening your Daily Briefing—schedule, Confidant stats, and Screen Time all in one glance!",
                        "Here's the full overview for today!",
                    ),
                    emotion="wink", voice="hmm", particle="sparkle",
                ),
                "rec_focus": DialogueNode(
                    texts=(
                        "25-minute Focus started! Let's make this next block count!",
                        "Focus clock running! Steady progress all the way!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "rec_tasks": DialogueNode(
                    texts=(
                        "Opening Tasks! Pick whichever Social Stat you want to level up next!",
                        "Task board ready—pick one clean win!",
                    ),
                    emotion="smug", voice="hmm",
                ),
                "rec_break": DialogueNode(
                    texts=(
                        "5-minute reset timer started! Grab {fav_drink}, step away, and come back fresh!",
                        "Break running! Good call taking a clean pause before pivoting.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "rec_stats": DialogueNode(
                    texts=(
                        "Pulling up Social Stats! Your {lowest_stat} could use a little love today!",
                        "Opening Social Stats & Daily Habits—let's see what shines next!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "rec_ok": DialogueNode(
                    texts=(
                        "Sounds good! You know your rhythm best—I'll keep tracking quietly in the background.",
                        "Roger that! Keep pacing yourself well!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
            },
        ),

        # 11) Overdue Rescue Plan
        ConversationTree(
            tree_id="overdue_rescue",
            title="Overdue Rescue Plan",
            tags=("overdue",),
            priority=10,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Heads up—we've got {overdue_count} overdue item(s), starting with '{top_task_short}'. Want to tackle it together?",
                        "Psst! '{top_task_short}' is past its deadline ({overdue_count} overdue total). Let's knock it out or reschedule cleanly?",
                    ),
                    emotion="worried", voice="hoi", particle="alert",
                    choices=(
                        DialogueChoice("Tackle '{top_task_short}' now—25m sprint!", "rescue_sprint",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("It feels huge—help me slice step 1.", "rescue_split"),
                        DialogueChoice("Open Tasks to reschedule honestly.", "rescue_open",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "rescue_sprint": DialogueNode(
                    texts=(
                        "NOW we're talking! 25 minutes on the clock—even getting halfway breaks the dread!",
                        "Focus sprint rolling! Once '{top_task_short}' is checked off, you'll feel 10× lighter!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "rescue_split": DialogueNode(
                    texts=(
                        "Totally normal! Big tasks look intimidating until you shrink them to a 5-minute first step. Sketch it in Notes or do a tiny 10m starter?",
                        "Don't aim to finish the whole mountain—just take step 1! Which sounds easier right now?",
                    ),
                    emotion="neutral", voice="naa",
                    choices=(
                        DialogueChoice("Sketch 3 tiny steps in Notes!", "split_note",
                                       action={"type": "open_new_note", "template": "idea", "title_key": "note_title_task"}),
                        DialogueChoice("Start a tiny 10m warm-up sprint!", "split_10",
                                       action={"type": "start_focus", "minutes": 10}),
                    ),
                ),
                "split_note": DialogueNode(
                    texts=(
                        "Opening '{note_title_task}' in Notes! Jot down 3 tiny bullets, then highlight step 1 and hit '→ TASK'!",
                        "Breakdown Note ready! Tiny steps beat perfection every time.",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "split_10": DialogueNode(
                    texts=(
                        "10-minute stealth sprint started! Just touch the first tiny piece—no pressure to finish everything at once.",
                        "10 minutes on the clock! Break the ice on '{top_task_short}'!",
                    ),
                    emotion="wink", voice="hmm", particle="sparkle",
                ),
                "rescue_open": DialogueNode(
                    texts=(
                        "Smart move—an honest schedule beats stale red badges. Opening Tasks!",
                        "Opening Tasks! Update the deadline and take the weight off your shoulders.",
                    ),
                    emotion="neutral", voice="hmm",
                ),
            },
        ),

        # 12) Late-Night Curfew Negotiation
        ConversationTree(
            tree_id="late_night_pact",
            title="Late-Night Wind-Down",
            tags=("late_night",),
            priority=10,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Yawn... It's getting seriously late{app_clause}! What's still keeping you at the desk?",
                        "Whoa, late-night session{app_clause}! Wrapping up one last thing, or losing track of the clock?",
                    ),
                    emotion="sad", voice="yawn", particle="zzz",
                    choices=(
                        DialogueChoice("Finishing one last thing—give me 15m!", "fifteen_more"),
                        DialogueChoice("Brain-dumping thoughts so I can sleep.", "night_journal"),
                        DialogueChoice("You're right—time to wind down!", "wind_down"),
                    ),
                ),
                "fifteen_more": DialogueNode(
                    texts=(
                        "Alright, ONE 15-minute final lap! Want a hard-stop timer so midnight doesn't sneak past you?",
                        "Deal—15 minutes max! Should I set a 'Lights Out' countdown?",
                    ),
                    emotion="serious", voice="hoi",
                    choices=(
                        DialogueChoice("Yes—set a 15m 'Lights Out' timer!", "lights_out_timer",
                                       action={"type": "start_timer", "minutes": 15, "label": "Lights Out!"}),
                        DialogueChoice("I'll wrap up right after this step.", "wrap_promise"),
                    ),
                ),
                "lights_out_timer": DialogueNode(
                    texts=(
                        "15-minute Lights Out timer set! Finish strong, save your work, then straight to bed!",
                        "Final-lap countdown rolling! Save everything cleanly when the bell rings!",
                    ),
                    emotion="smug", voice="kettei", particle="sparkle",
                ),
                "wrap_promise": DialogueNode(
                    texts=(
                        "I'm holding you to that! Tomorrow's energy starts with tonight's sleep.",
                        "Don't make me nag you twice—wrap it up cleanly and get some rest!",
                    ),
                    emotion="worried", voice="yareyare", particle="zzz",
                ),
                "night_journal": DialogueNode(
                    texts=(
                        "Smart trick! Parking tomorrow's thoughts in Notes stops your brain from looping on the pillow.",
                        "Love that habit—emptying your head onto a page makes falling asleep way faster!",
                    ),
                    emotion="wink", voice="hmm",
                    choices=(
                        DialogueChoice("Open a Daily Journal note!", "open_journal",
                                       action={"type": "open_new_note", "template": "journal"}),
                        DialogueChoice("All clear in my head now—good night!", "wind_down"),
                    ),
                ),
                "open_journal": DialogueNode(
                    texts=(
                        "Opening a fresh Journal note! Jot down 3 quick lines, close the screen, and rest easy.",
                        "Journal ready! Put tomorrow on paper so tonight belongs to sleep.",
                    ),
                    emotion="wink", voice="kaketa", particle="music",
                ),
                "wind_down": DialogueNode(
                    texts=(
                        "Now THAT'S a wise call! Want me to switch into Sleep Mode while you head to bed?",
                        "Good night! Should I switch to Sleep Mode while your laptop stays on?",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                    choices=(
                        DialogueChoice("Good night, Morgana! (Sleep Mode)", "go_sleep",
                                       action={"type": "sleep_mode", "enabled": True}),
                        DialogueChoice("Good night! Stay awake for a minute.", "good_night"),
                    ),
                ),
                "go_sleep": DialogueNode(
                    texts=(
                        "Sweet dreams! Sleep Mode is on—I'll keep everything quiet while your laptop stays on!",
                        "Sleep Mode on! Rest up well—I'll stay quiet and catch you bright and sharp tomorrow!",
                    ),
                    emotion="wink", voice="sleep_on", particle="zzz",
                ),
                "good_night": DialogueNode(
                    texts=(
                        "Rest well! You earned a full night's recovery.",
                        "Good night! Sleep tight and recharge!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
            },
        ),

        # ===================================================================
        # GENERAL COMPANION & PERSONALITY TREES (13-16)
        # ===================================================================

        # 13) Daily Energy & Game Plan
        ConversationTree(
            tree_id="energy_strategy",
            title="Energy & Game Plan",
            tags=("any", "morning", "afternoon"),
            priority=3,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Hey! Quick desk check-in—how's your energy level {time_vibe}?",
                        "Checking in {time_vibe}! Are you feeling charged up, steady, or running low?",
                        "How are we feeling {time_vibe}? Ready to sprint, pace it steady, or recharge?",
                    ),
                    emotion="neutral", voice="dousuru", particle="question",
                    choices=(
                        DialogueChoice("Fired up! Ready to crush it!", "high_energy"),
                        DialogueChoice("Steady—just need a clear target.", "steady"),
                        DialogueChoice("Honestly running on fumes...", "low_energy"),
                    ),
                ),
                "high_energy": DialogueNode(
                    texts=(
                        "Ooh, I love that spark! With {pending_summary}, how do you want to channel it?",
                        "That's the energy! Pick your launchpad—Focus sprint, Tasks, or a fresh Note?",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Lock into a 25m Focus Sprint!", "start_sprint",
                                       action={"type": "start_focus", "minutes": 25},
                                       memory_set={"work_style": "sprint"}),
                        DialogueChoice("Open Tasks & pick my top priority!", "pick_top",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Brain-dump ideas in Notes first.", "open_brainstorm",
                                       action={"type": "open_new_note", "template": "idea"}),
                    ),
                ),
                "start_sprint": DialogueNode(
                    texts=(
                        "Focus timer rolling! I'll guard the clock while you sweep through it!",
                        "25 minutes on the clock—go make real progress!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "pick_top": DialogueNode(
                    texts=(
                        "Opening Tasks! Knock out the toughest one first and everything after feels effortless.",
                        "Task board open! Tag each task with the Social Stat you want to grow!",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                ),
                "open_brainstorm": DialogueNode(
                    texts=(
                        "Smart call! Catching ideas before they vanish keeps your head clear. Notes is open!",
                        "Opening a fresh Idea note—get it out of your head and onto the page!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "steady": DialogueNode(
                    texts=(
                        "Steady wins the long game! Want to boost your {lowest_stat} or check today's full Briefing?",
                        "Calm and consistent is underrated! What would help most right now?",
                    ),
                    emotion="smug", voice="hmm",
                    choices=(
                        DialogueChoice("Check Social Stats & Daily Habits.", "go_stats",
                                       action={"type": "open_stats"},
                                       memory_set={"work_style": "steady"}),
                        DialogueChoice("Show me today's Daily Briefing.", "go_brief",
                                       action={"type": "open_briefing"}),
                        DialogueChoice("Just hang out and keep me company.", "company_end"),
                    ),
                ),
                "go_stats": DialogueNode(
                    texts=(
                        "Pulling up Social Stats! One steady habit today pushes that gold star even brighter.",
                        "Opening Social Stats—let's see what's closest to a Rank Up!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "go_brief": DialogueNode(
                    texts=(
                        "Here's your full overview! Pick one meaningful win and we'll call today a victory.",
                        "Opening Daily Briefing—events, deadlines, stats, and screen time all in one view!",
                    ),
                    emotion="neutral", voice="naa",
                ),
                "company_end": DialogueNode(
                    texts=(
                        "Heh, you got it! No pressure—I'll be right here keeping watch on your desktop.",
                        "Always happy to keep you company! Just double-click anytime.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "low_energy": DialogueNode(
                    texts=(
                        "Whoa, don't push yourself into burnout! What would recharge you fastest right now?",
                        "Low battery happens! Rest smart now so you don't drag all day—what sounds best?",
                    ),
                    emotion="worried", voice="yareyare", particle="question",
                    choices=(
                        DialogueChoice("5-minute water & stretch break.", "recharge_break",
                                       action={"type": "start_break", "minutes": 5}),
                        DialogueChoice("10-minute screen-free rest.", "recharge_long",
                                       action={"type": "start_break", "minutes": 10}),
                        DialogueChoice("I'll do just one tiny easy win.", "tiny_win",
                                       action={"type": "open_tasks"}),
                    ),
                ),
                "recharge_break": DialogueNode(
                    texts=(
                        "5-minute break started! Step away, sip {fav_drink}, and loosen your shoulders!",
                        "Break rolling! Five real minutes off-screen does wonders.",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "recharge_long": DialogueNode(
                    texts=(
                        "10 minutes of pure recovery! Close your eyes or grab {fav_drink}—I'll call you back.",
                        "10-minute power rest started! Step away and recharge properly.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "tiny_win": DialogueNode(
                    texts=(
                        "One tiny check-off still counts! Pick the easiest item on Tasks to spark a little momentum.",
                        "Opening Tasks—go grab one easy win and be kind to yourself today!",
                    ),
                    emotion="smug", voice="naa", particle="sparkle",
                ),
            },
        ),

        # 14) Playful "I Am NOT a Cat!" Banter (remembers tease count!)
        ConversationTree(
            tree_id="not_a_cat",
            title="The Great Feline Debate",
            tags=("any", "afternoon", "evening"),
            priority=2,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Wait a second... you're looking at me with that 'aww, cute kitty' face again, aren't you?!",
                        "Hold on—don't think I didn't notice you hovering over my ears! Remember the #1 desk rule!",
                    ),
                    emotion="angry", voice="hoi", particle="anger",
                    choices=(
                        DialogueChoice("You literally have ears and a tail, Mona.", "tease_1",
                                       memory_inc=("cat_tease_count", 1)),
                        DialogueChoice("No way! You're a classy desk strategist!", "praise_1"),
                        DialogueChoice("Only if I get to offer you Fatty Tuna.", "tuna_1"),
                    ),
                ),
                "tease_1": DialogueNode(
                    texts=(
                        "I AM NOT A CAT! I'm Morgana—your suave, tactical desk partner! {tease_quip}",
                        "Excuse me?! I am a dignified companion, NOT a housecat! {tease_quip}",
                    ),
                    emotion="shout", voice="uwooh", particle="anger",
                    choices=(
                        DialogueChoice("Okay, okay—suave partner Morgana!", "truce"),
                        DialogueChoice("Sure thing, little kitty~", "tease_2",
                                       memory_inc=("cat_tease_count", 1)),
                    ),
                ),
                "truce": DialogueNode(
                    texts=(
                        "Hmph! Much better! ...Though I guess I don't mind hanging around on your desktop.",
                        "Now THAT'S proper respect! Alright, truce—back to being the best desk duo!",
                    ),
                    emotion="smug", voice="hmm", particle="heart",
                ),
                "tease_2": DialogueNode(
                    texts=(
                        "Gah!! Unbelievable! Keep that up and I'm moving your bedtime reminder to 8:00 PM! ...Hmph!",
                        "Unbelievable!! I'm adding that to my mental ledger! ...Alright, you got a smirk out of me.",
                    ),
                    emotion="angry", voice="yareyare", particle="anger",
                ),
                "praise_1": DialogueNode(
                    texts=(
                        "W-well, obviously! Someone around here has sharp judgment! What can this strategist help you tackle?",
                        "Heh—glad we're on the same page! Ready to check your stats or just hang out?",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Let's check my {lowest_stat} in Social Stats!", "help_stat",
                                       action={"type": "open_stats"}),
                        DialogueChoice("Just keep being awesome on my desk!", "stay_awesome"),
                    ),
                ),
                "help_stat": DialogueNode(
                    texts=(
                        "Leave it to me! Opening Social Stats—let's get that {lowest_stat} star growing!",
                        "Pulling up Social Stats! Pick a Habit or Task to boost your {lowest_stat}!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "stay_awesome": DialogueNode(
                    texts=(
                        "Heh, effortless for me! Anytime you need a partner, just double-click!",
                        "You've got it! S-tier desktop companionship, 24/7!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "tuna_1": DialogueNode(
                    texts=(
                        "F-Fatty Tuna?! ...Ahem! Bribery doesn't work on a professional companion—unless it's fresh toro sushi!",
                        "Whoa—did you just say Fatty Tuna?! ...I mean, stay focused! (Wait, do you actually have sushi?)",
                    ),
                    emotion="excited", voice="oh_excited", particle="heart",
                    choices=(
                        DialogueChoice("Deal—after I clear my next Task!", "tuna_deal",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Virtual Fatty Tuna, freshly served!", "tuna_serve"),
                    ),
                ),
                "tuna_deal": DialogueNode(
                    texts=(
                        "Ooh, now THAT'S motivation! Clear a task and we celebrate in style!",
                        "Opening Tasks! Knock one out and consider it a celebration!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                ),
                "tuna_serve": DialogueNode(
                    texts=(
                        "WHOA—melts right in my mouth!! You're the best partner a guy could ask for!",
                        "Best desk delivery ever!! Alright, my energy's at 200% now!",
                    ),
                    emotion="excited", voice="oh_excited", particle="heart",
                ),
            },
        ),

        # 15) Cozy Café & Drink Ritual Chat (saves favorite drink in memory!)
        ConversationTree(
            tree_id="cafe_break",
            title="Cozy Drink Ritual",
            tags=("any", "morning", "afternoon", "evening"),
            priority=3,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Feels like a cozy café kind of day! What's your go-to desk drink when you're working?",
                        "Quick hydration check—what's sitting next to your keyboard {time_vibe}?",
                    ),
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
                    texts=(
                        "Ah, a connoisseur! Bold roast and cozy aroma—just don't pour a cup too close to bedtime!",
                        "Classic desk fuel! Coffee + a clean Focus sprint is an unbeatable combo.",
                    ),
                    emotion="smug", voice="hmm", particle="sparkle",
                    choices=(
                        DialogueChoice("Pouring a cup & starting 25m Focus!", "cafe_focus",
                                       action={"type": "start_focus", "minutes": 25}),
                        DialogueChoice("Let me grab a quick 5m refill first.", "cafe_break",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "drink_tea": DialogueNode(
                    texts=(
                        "Ooh, classy and calm! Perfect for long study sessions or clean note-taking.",
                        "Love that vibe—steady focus without the caffeine jitters!",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                    choices=(
                        DialogueChoice("Open Notes for a quiet thinking session.", "cafe_notes",
                                       action={"type": "open_notes"}),
                        DialogueChoice("Brewing a cup—start a 5m break!", "cafe_break",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "drink_water": DialogueNode(
                    texts=(
                        "Pure hydration! Honestly the smartest real-life buff—have you taken a sip recently?",
                        "Respect! Staying hydrated keeps focus way sharper than people expect!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Taking a big sip right now!", "water_sip"),
                        DialogueChoice("Oops—refilling my bottle (5m break).", "cafe_break",
                                       action={"type": "start_break", "minutes": 5}),
                    ),
                ),
                "cafe_focus": DialogueNode(
                    texts=(
                        "Focus session started! Imagine cozy rain outside the window while you crush your work.",
                        "25 minutes on the clock—sip up and let's go!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "cafe_notes": DialogueNode(
                    texts=(
                        "Opening Notes! Take your time and let your thoughts flow cleanly onto the page.",
                        "Notes ready! Cozy drink + clean markdown = peak clarity.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "cafe_break": DialogueNode(
                    texts=(
                        "5-minute drink break running! Stretch your legs while you refill.",
                        "Break started! Step away for 5 minutes and come back refreshed!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "water_sip": DialogueNode(
                    texts=(
                        "Nice!! Hydrated brain = faster focus and fewer headaches!",
                        "Hydration check passed! Keep that bottle within reach!",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                ),
            },
        ),

        # 16) All-Clear Celebration & Reflection
        ConversationTree(
            tree_id="victory_lap",
            title="Victory Lap Reflection",
            tags=("all_done", "streak", "evening"),
            priority=6,
            start="root",
            nodes={
                "root": DialogueNode(
                    texts=(
                        "Look at you—{screen_time} active today and a {streak}-day streak! How do you feel about today so far?",
                        "Solid day in the books ({screen_time} active, {streak}d streak)! Happy with how today went?",
                    ),
                    emotion="excited", voice="oh_excited", particle="music",
                    choices=(
                        DialogueChoice("Proud! Got real progress done.", "v_proud"),
                        DialogueChoice("Could've been better, honestly.", "v_meh"),
                        DialogueChoice("Log a quick Daily Journal reflection!", "v_journal",
                                       action={"type": "open_new_note", "template": "journal"}),
                    ),
                ),
                "v_proud": DialogueNode(
                    texts=(
                        "YES! Give yourself real credit—momentum sticks when you actually celebrate your wins!",
                        "Awesome!! You earned every bit of that progress today!",
                    ),
                    emotion="excited", voice="oh_excited", particle="sparkle",
                    choices=(
                        DialogueChoice("Admire my Social Stats star!", "v_stats",
                                       action={"type": "open_stats"}),
                        DialogueChoice("Couldn't have done it without you, Mona!", "v_thanks"),
                    ),
                ),
                "v_meh": DialogueNode(
                    texts=(
                        "Hey, don't be too harsh on yourself! Showing up on low-energy days takes way more Guts than easy days.",
                        "Not every day has to be 100%—consistency over weeks beats one heroic day every time.",
                    ),
                    emotion="wink", voice="naa", particle="heart",
                    choices=(
                        DialogueChoice("Thanks... let's grab 1 tiny win.", "v_one",
                                       action={"type": "open_tasks"}),
                        DialogueChoice("Tomorrow's a fresh start!", "v_tomorrow"),
                    ),
                ),
                "v_journal": DialogueNode(
                    texts=(
                        "Opening a Daily Journal note! Write one win and one takeaway—takes 60 seconds and sharpens tomorrow.",
                        "Journal ready! Capturing one quick highlight is the best way to close a day.",
                    ),
                    emotion="wink", voice="hmm", particle="music",
                ),
                "v_stats": DialogueNode(
                    texts=(
                        "Pulling up your Social Stats—admire that gold star!",
                        "Opening Social Stats! Look how far you've come!",
                    ),
                    emotion="smug", voice="hmm", particle="music",
                ),
                "v_thanks": DialogueNode(
                    texts=(
                        "H-heh, well, of course I'm an S-tier partner! Same team tomorrow too!",
                        "Right back at you! Proud to be on your desk!",
                    ),
                    emotion="wink", voice="hmm", particle="heart",
                ),
                "v_one": DialogueNode(
                    texts=(
                        "One small check-off flips the whole mood. Opening Tasks!",
                        "Let's grab one quick win on Tasks and end the day smiling!",
                    ),
                    emotion="smug", voice="hmm",
                ),
                "v_tomorrow": DialogueNode(
                    texts=(
                        "That's the winning mindset! Rest well and we'll hit tomorrow running.",
                        "Fresh slate tomorrow—rest up and recharge tonight!",
                    ),
                    emotion="wink", voice="hmm", particle="sparkle",
                ),
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Dialogue Engine State Machine
# ---------------------------------------------------------------------------
class DialogueEngine:
    """Context-aware multi-turn conversation controller with live app awareness & memory."""

    def __init__(self, memory: DialogueMemoryStore) -> None:
        self.memory = memory
        self.trees: dict[str, ConversationTree] = {t.tree_id: t for t in _build_trees()}
        self._active_tree: ConversationTree | None = None
        self._current_node_id: str | None = None
        self._rendered_choices: list[DialogueChoice] = []
        self._vars: dict[str, Any] = {}
        self._flags: set[str] = set()

    @property
    def is_active(self) -> bool:
        return self._active_tree is not None and self._current_node_id is not None

    def cancel(self) -> None:
        self._active_tree = None
        self._current_node_id = None
        self._rendered_choices = []

    def _build_vars_and_flags(self, ctx: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
        pending = int(ctx.get("pending_tasks", 0))
        overdue = int(ctx.get("overdue_count", 0))
        top_task = str(ctx.get("top_task") or "").strip()
        top_task_short = _clip(top_task, 26) if top_task else "your next task"
        if overdue > 0:
            pending_summary = f"{overdue} overdue and {pending} pending tasks"
        elif pending == 0:
            pending_summary = "a totally clear task list"
        elif pending == 1:
            pending_summary = f"1 task left ('{top_task_short}')"
        else:
            pending_summary = f"{pending} pending tasks"

        hour = int(ctx.get("hour", 12))
        if 5 <= hour < 12:
            time_vibe = "this morning"
        elif 12 <= hour < 18:
            time_vibe = "this afternoon"
        elif 18 <= hour < 23:
            time_vibe = "tonight"
        else:
            time_vibe = "this late"

        app_name = str(ctx.get("active_app_name") or "").strip() or "your desk"
        app_topic = str(ctx.get("active_app_topic") or "").strip()
        app_phrase = str(ctx.get("active_app_phrase") or "").strip() or app_name
        app_dwell = str(ctx.get("active_app_dwell") or "").strip()
        prev_app = str(ctx.get("prev_app_name") or "").strip()
        switched_recent = bool(ctx.get("switched_app_recently", False)) and bool(prev_app) and prev_app != app_name

        dwell_clause = f" for {app_dwell}" if app_dwell else ""
        switch_clause = f" (just hopped over from {prev_app})" if switched_recent else (f" for {app_dwell}" if app_dwell else "")
        app_clause = f" in {app_phrase}" if ctx.get("active_app_name") else ""

        topic_or_code = _clip(app_topic, 24) if app_topic else app_name
        topic_or_app = _clip(app_topic, 24) if app_topic else app_name
        note_title_app = _clip(app_topic, 34) if app_topic else f"{app_name} Notes"
        note_title_debug = f"Debug: {_clip(app_topic, 26)}" if app_topic else f"Debug Notes ({app_name})"
        note_title_meeting = f"Meeting: {_clip(app_topic, 24)}" if app_topic else "Meeting Notes"
        note_title_task = f"Plan: {_clip(top_task, 26)}" if top_task else "Task Breakdown"

        top_cat_summary = str(ctx.get("top_category_summary") or "").strip() or "focused desk work"
        tease_count = self.memory.get_counter("cat_tease_count")
        if tease_count >= 4:
            tease_quip = f"That's strike #{tease_count + 1}—I'm counting!"
        elif tease_count >= 2:
            tease_quip = "You keep testing my patience on purpose!"
        else:
            tease_quip = "Say it back!"

        variables = {
            "app_name": app_name,
            "app_topic": app_topic or app_name,
            "app_phrase": app_phrase,
            "app_dwell": app_dwell or "a bit",
            "dwell_clause": dwell_clause,
            "switch_clause": switch_clause,
            "app_clause": app_clause,
            "prev_app": prev_app or "your last app",
            "topic_or_code": topic_or_code,
            "topic_or_app": topic_or_app,
            "note_title_app": note_title_app,
            "note_title_debug": note_title_debug,
            "note_title_meeting": note_title_meeting,
            "note_title_task": note_title_task,
            "top_task": top_task or "your next task",
            "top_task_short": top_task_short,
            "pending_summary": pending_summary,
            "overdue_count": overdue,
            "screen_time": ctx.get("screen_time_text") or "0m",
            "continuous_time": ctx.get("continuous_time_text") or "a while",
            "top_cat_summary": top_cat_summary,
            "lowest_stat": ctx.get("lowest_stat_label") or "Proficiency",
            "top_stat": ctx.get("top_stat_label") or "Knowledge",
            "streak": int(ctx.get("streak_days", 0)),
            "fav_drink": self.memory.get_pref("drink") or "a fresh drink",
            "time_vibe": time_vibe,
            "tease_quip": tease_quip,
        }
        flags: set[str] = set()
        if top_task:
            flags.add("has_top_task")
        if app_topic:
            flags.add("has_app_topic")
        if switched_recent:
            flags.add("switched_recent")
        if overdue > 0:
            flags.add("has_overdue")
        return variables, flags

    def _context_tags(self, ctx: dict[str, Any]) -> set[str]:
        tags = {"any"}
        hour = int(ctx.get("hour", 12))
        if 5 <= hour < 12:
            tags.add("morning")
        elif 12 <= hour < 18:
            tags.add("afternoon")
        elif 18 <= hour < 23:
            tags.add("evening")
        else:
            tags.add("late_night")
        if ctx.get("curfew_active"):
            tags.add("late_night")

        cat = str(ctx.get("active_app_cat") or "").strip().lower()
        prev_cat = str(ctx.get("prev_app_cat") or "").strip().lower()
        switched_recent = bool(ctx.get("switched_app_recently", False))
        if cat:
            tags.add(cat)

        overdue = int(ctx.get("overdue_count", 0))
        pending = int(ctx.get("pending_tasks", 0))
        if overdue > 0:
            tags.add("overdue")
        if pending == 0 and int(ctx.get("total_tasks", 0)) > 0:
            tags.add("all_done")
        if int(ctx.get("streak_days", 0)) >= 2:
            tags.add("streak")

        # Cross-context distraction / pivot detection:
        # User is in gaming/media/social while overdue/busy OR just switched away from work!
        if cat in _LEISURE_CATS and (
            overdue > 0
            or pending >= 2
            or (switched_recent and prev_cat in _WORK_CATS)
        ):
            tags.add("app_distracted")

        if int(ctx.get("continuous_s", 0)) >= 45 * 60:
            tags.add("screen_long")
        if int(ctx.get("screen_active_s", 0)) >= 35 * 60:
            tags.add("screen_active")
        return tags

    def start_contextual(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Select the most contextually alive conversation tree, strongly leveraging active app awareness."""
        matched = self._context_tags(ctx)
        recent = self.memory.recent_trees()
        cat = str(ctx.get("active_app_cat") or "").strip().lower()
        has_live_app = cat in ("coding", "study", "writing", "creative", "browsing", "media", "gaming", "social", "meeting")

        candidates: list[tuple[float, ConversationTree]] = []
        for tree in self.trees.values():
            overlap = matched.intersection(tree.tags)
            if not overlap:
                continue
            # Only trigger screen_time_nudge when continuous time is actually high
            if tree.tree_id == "screen_time_nudge" and "screen_long" not in matched:
                continue
            if tree.tree_id == "distraction_pivot" and "app_distracted" not in matched:
                continue
            if tree.tree_id == "screen_time_recap" and "screen_active" not in matched:
                continue

            specific = [t for t in overlap if t != "any"]
            score = float(tree.priority) + len(specific) * 3.0

            # Strong live Active App priority so TALK feels immediately plugged into what's on screen!
            if tree.app_aware and has_live_app and (cat in tree.tags or "app_distracted" in overlap):
                score += 11.0
                if ctx.get("active_app_topic"):
                    score += 2.5

            # Anti-repetition penalty (lets Morgana pivot smoothly if user clicks TALK repeatedly in same app)
            if tree.tree_id in recent:
                pos = len(recent) - 1 - recent.index(tree.tree_id)
                if pos == 0:
                    score -= 18.0       # never repeat exact same tree twice in a row
                elif pos == 1:
                    score -= 10.0
                else:
                    score -= max(2.0, 7.0 - pos * 1.5)

            score += random.uniform(0.0, 2.2)
            candidates.append((score, tree))

        if not candidates:
            chosen = self.trees["energy_strategy"]
        else:
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            top_score = candidates[0][0]
            top_pool = [t for sc, t in candidates if sc >= top_score - 2.5][:3]
            chosen = random.choice(top_pool)

        return self.start_tree(chosen.tree_id, ctx)

    def start_tree(self, tree_id: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
        tree = self.trees.get(tree_id)
        if tree is None:
            return None
        self._active_tree = tree
        self._current_node_id = tree.start
        self._vars, self._flags = self._build_vars_and_flags(ctx)
        self.memory.record_tree(tree.tree_id)
        return self._render_current()

    def _visible_choices(self, node: DialogueNode) -> list[DialogueChoice]:
        out: list[DialogueChoice] = []
        for ch in node.choices:
            if ch.require_flags and not all(f in self._flags for f in ch.require_flags):
                continue
            if ch.exclude_flags and any(f in self._flags for f in ch.exclude_flags):
                continue
            out.append(ch)
        return out[:3] if out else list(node.choices[:3])

    def _render_current(self) -> dict[str, Any] | None:
        if self._active_tree is None or self._current_node_id is None:
            return None
        node = self._active_tree.nodes.get(self._current_node_id)
        if node is None:
            self.cancel()
            return None
        node_key = f"{self._active_tree.tree_id}.{self._current_node_id}"
        raw_text = self.memory.pick_variant(node_key, node.texts)
        vis_choices = self._visible_choices(node)
        self._rendered_choices = vis_choices
        payload = {
            "tree_id": self._active_tree.tree_id,
            "title": self._active_tree.title,
            "node_id": self._current_node_id,
            "text": _fmt(raw_text, self._vars),
            "emotion": node.emotion,
            "voice": node.voice,
            "particle": node.particle,
            "choices": [_fmt(c.label, self._vars) for c in vis_choices],
            "has_choices": bool(vis_choices),
        }
        if not vis_choices:
            self.cancel()
        return payload

    def choose(self, index: int) -> dict[str, Any] | None:
        """Advance the active conversation by selecting choice `index` (0-based)."""
        if self._active_tree is None or self._current_node_id is None:
            return None
        if index < 0 or index >= len(self._rendered_choices):
            return None
        choice = self._rendered_choices[index]

        if choice.memory_set:
            for k, v in choice.memory_set.items():
                self.memory.set_pref(k, v)
                if k == "drink":
                    self._vars["fav_drink"] = str(v)
        if choice.memory_inc:
            k, delta = choice.memory_inc
            new_v = self.memory.inc_counter(k, delta)
            if k == "cat_tease_count":
                if new_v >= 4:
                    self._vars["tease_quip"] = f"That's strike #{new_v}—I'm counting!"
                elif new_v >= 2:
                    self._vars["tease_quip"] = "You keep testing my patience on purpose!"

        # Resolve dynamic action fields (like pre-titled Note titles from active window topic)
        action = dict(choice.action) if choice.action else None
        if action and "title_key" in action:
            tkey = str(action.pop("title_key"))
            resolved_title = str(self._vars.get(tkey) or "").strip()
            if resolved_title:
                action["title"] = resolved_title

        self._current_node_id = choice.next_node
        next_payload = self._render_current()
        return {
            "node": next_payload,
            "action": action,
        }
