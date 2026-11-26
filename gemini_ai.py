# -*- coding: utf-8 -*-
"""Google Gemini & OpenAI-compatible AI companion engine for Morgana <-> Joker dialogue.

Zero Qt dependency and zero external pip dependencies (pure stdlib `urllib`).
Strictly opt-in: NEVER sends network requests unless the user enables AI in Settings
and provides a Gemini API key (or a local/custom OpenAI-compatible endpoint).
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

AI_PROVIDERS: tuple[str, ...] = ("gemini", "openai")
GEMINI_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
)
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_OPENAI_ENDPOINT = "http://localhost:11434/v1/chat/completions"
DEFAULT_CALL_USER = "Joker"

from emotions import VOICES as _ALL_VOICES, emotion_for_text, resolve_reaction

VALID_EXPRESSIONS: tuple[str, ...] = (
    "neutral", "wink", "serious", "worried", "angry",
    "sad", "uneasy", "excited", "shout", "smug",
)
VALID_VOICES: tuple[str, ...] = _ALL_VOICES
VALID_PARTICLES: tuple[str, ...] = (
    "", "sparkle", "music", "heart", "alert", "question", "anger", "zzz",
)
VALID_ACTIONS: tuple[str, ...] = (
    "", "focus_25", "break_5", "open_tasks", "open_notes",
    "open_events", "open_stats", "open_briefing", "open_wrapup",
)

ACTION_LABELS: dict[str, str] = {
    "focus_25": "FOCUS 25M",
    "break_5": "5M BREAK",
    "open_tasks": "OPEN TASKS",
    "open_notes": "OPEN NOTES",
    "open_events": "OPEN EVENTS",
    "open_stats": "OPEN STATS",
    "open_briefing": "BRIEFING",
    "open_wrapup": "WRAP-UP",
}

_EXPR_ALIAS: dict[str, str] = {
    "happy": "wink",
    "sparkle": "excited",
    "cheerful": "wink",
    "sleepy": "neutral",
    "tired": "sad",
    "surprised": "shout",
    "curious": "neutral",
    "proud": "smug",
    "annoyed": "angry",
    "concerned": "worried",
}

_VOICE_ALIAS: dict[str, str] = {
    "nice": "oh_excited",
    "lets_go": "ikuzo",
    "care": "naa",
    "not_a_cat": "chigau",
    "hello": "choushi",
    "morning": "ohayou",
    "happy": "oh_excited",
    "sleepy": "sleepy_nyaa",
    "oi": "oi",
    "ask": "oh_asking",
}

_PARTICLE_ALIAS: dict[str, str] = {
    "hearts": "heart",
    "love": "heart",
    "star": "sparkle",
    "stars": "sparkle",
    "notes": "music",
    "note": "music",
    "exclaim": "alert",
    "sleep": "zzz",
    "angry": "anger",
}


def normalize_expression(expr: str, default: str = "neutral") -> str:
    k = (expr or "").strip().lower()
    if k in VALID_EXPRESSIONS:
        return k
    mapped = _EXPR_ALIAS.get(k)
    return mapped if mapped in VALID_EXPRESSIONS else default


def normalize_voice(voice: str, default: str = "hmm") -> str:
    k = (voice or "").strip().lower()
    if k in VALID_VOICES:
        return k
    mapped = _VOICE_ALIAS.get(k)
    return mapped if mapped in VALID_VOICES else default


def normalize_particle(particle: str) -> str:
    k = (particle or "").strip().lower()
    if k in VALID_PARTICLES:
        return k
    mapped = _PARTICLE_ALIAS.get(k, "")
    return mapped if mapped in VALID_PARTICLES else ""


def normalize_action(action: str) -> str:
    k = (action or "").strip().lower()
    return k if k in VALID_ACTIONS else ""


@dataclass
class AIConfig:
    provider: str = "gemini"
    api_key: str = ""
    model: str = DEFAULT_GEMINI_MODEL
    endpoint: str = DEFAULT_OPENAI_ENDPOINT
    call_user: str = DEFAULT_CALL_USER

    def is_ready(self) -> bool:
        prov = (self.provider or "gemini").strip().lower()
        if prov == "gemini":
            return bool((self.api_key or "").strip())
        ep = (self.endpoint or "").strip()
        return ep.startswith("http://") or ep.startswith("https://")


@dataclass
class AIReply:
    ok: bool
    text: str
    emotion: str = "neutral"
    voice: str = "hmm"
    particle: str = ""
    suggested_action: str = ""
    error: str = ""


@dataclass
class AITalkChoice:
    label: str
    action: str = ""


@dataclass
class AITalkScene:
    ok: bool
    line: str
    emotion: str = "neutral"
    voice: str = "naa"
    particle: str = ""
    choices: tuple[AITalkChoice, ...] = ()
    error: str = ""


class ConversationBuffer:
    """Thread-safe rolling multi-turn conversation memory shared by Ask & Command and Talk."""

    def __init__(self, max_turns: int = 12) -> None:
        self._max_turns = max(4, int(max_turns))
        self._lock = threading.Lock()
        self._turns: list[dict[str, str]] = []

    def add_user(self, text: str) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        with self._lock:
            self._turns.append({"role": "user", "text": clean[:420]})
            if len(self._turns) > self._max_turns:
                self._turns = self._turns[-self._max_turns:]

    def add_model(self, text: str) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        with self._lock:
            self._turns.append({"role": "model", "text": clean[:420]})
            if len(self._turns) > self._max_turns:
                self._turns = self._turns[-self._max_turns:]

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(t) for t in self._turns]

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)


# ---------------------------------------------------------------------------
# Live Context & Persona 5 Morgana <-> Joker Prompt Builder
# ---------------------------------------------------------------------------
def _context_summary(ctx: dict) -> str:
    hour = int(ctx.get("hour", 12))
    tod = (
        "late night" if (hour >= 23 or hour < 5) else
        "morning" if hour < 12 else
        "afternoon" if hour < 18 else
        "evening"
    )
    app_name = str(ctx.get("active_app_name") or "").strip()
    app_topic = str(ctx.get("active_app_topic") or "").strip()
    app_cat = str(ctx.get("active_app_category") or "").strip()
    app_title = str(ctx.get("active_window_title") or "").strip()
    if app_name and app_topic:
        app_desc = f"{app_name} ({app_topic}, category={app_cat or 'desktop'})"
    elif app_name and app_title:
        app_desc = f"{app_name} — {app_title[:60]} (category={app_cat or 'desktop'})"
    elif app_name:
        app_desc = f"{app_name} (category={app_cat or 'desktop'})"
    else:
        app_desc = "Desktop"

    pending = int(ctx.get("pending_tasks", 0))
    overdue = int(ctx.get("overdue_count", 0))
    top_task = str(ctx.get("top_task") or "").strip()
    top_tasks = [str(t)[:42] for t in (ctx.get("top_tasks") or ()) if t][:3]
    if not top_tasks and top_task:
        top_tasks = [top_task[:42]]

    scr = str(ctx.get("screen_time_text") or "0m")
    cont = str(ctx.get("continuous_time_text") or "0m")
    h_done = int(ctx.get("habits_done", 0))
    h_tot = int(ctx.get("habits_total", 0))
    nxt_ev = str(ctx.get("next_event_title") or "").strip()
    nxt_dl = str(ctx.get("next_event_delta") or "").strip()
    focus = f"{ctx.get('focus_phase')} ({ctx.get('focus_rem')} left)" if ctx.get("focus_running") else "off"
    drink = str(ctx.get("favorite_drink") or "").strip()
    stats_txt = str(ctx.get("social_stats_summary") or "").strip()

    lines = [
        f"- Time: {hour:02d}:00 ({tod})",
        f"- Active window right now: {app_desc}",
        f"- Screen time today: {scr} (nonstop stretch: {cont})",
        f"- Pending tasks: {pending} (overdue: {overdue})"
        + (f"; top tasks: {', '.join(top_tasks)}" if top_tasks else ""),
        f"- Focus timer: {focus}",
    ]
    if h_tot:
        lines.append(f"- Daily habits: {h_done}/{h_tot} done")
    if nxt_ev and nxt_dl:
        lines.append(f"- Next event: '{nxt_ev[:40]}' in {nxt_dl}")
    if stats_txt:
        lines.append(f"- Social Stats: {stats_txt}")
    if drink:
        lines.append(f"- Remembered favorite drink: {drink}")
    return "\n".join(lines)


def build_morgana_system_prompt(ctx: dict, call_user: str = DEFAULT_CALL_USER) -> str:
    """Build an authentic Persona 5 Morgana <-> Joker companion system instruction."""
    name = (call_user or DEFAULT_CALL_USER).strip()[:24] or DEFAULT_CALL_USER
    return (
        f"You are Morgana from Persona 5, acting as {name}'s loyal, sharp-tongued, warm desktop partner.\n"
        f"Dynamic & Voice:\n"
        f"- Speak directly to {name} the way Morgana banters with Joker in Persona 5: confident, encouraging, "
        f"playfully bossy when caring about rest/sleep/meals, proud of teamwork, and genuinely observant of what {name} is doing right now.\n"
        f"- Occasional signature Morgana flair ('Looking cool, {name}!', 'Leave it to me!', 'Whoa, easy there!', "
        f"fiercely insisting 'I am NOT a cat!' if teased about being a cat), kept natural—never forced or repetitive.\n"
        f"- Mirror {name}'s language naturally: if {name} writes in Indonesian, reply in lively, natural Indonesian "
        f"with Morgana's Persona 5 personality; if in English, reply in crisp Persona 5 English.\n"
        f"- Keep replies tight like an in-game Persona 5 dialogue box: 1 to 3 punchy sentences (under ~240 characters). "
        f"No markdown bullets, no asterisks stage directions like *purrs*, and NEVER break character or mention being an AI.\n"
        f"- Ground your comments in {name}'s real live desktop context when relevant, without robotically reading every stat.\n\n"
        f"Live desktop snapshot:\n{_context_summary(ctx)}"
    )


# ---------------------------------------------------------------------------
# Low-level Transport (Native Google Gemini REST + OpenAI-Compatible)
# ---------------------------------------------------------------------------
def _extract_http_error(err: urllib.error.HTTPError) -> str:
    code = getattr(err, "code", 0)
    detail = ""
    try:
        raw = err.read().decode("utf-8", errors="replace")
        obj = json.loads(raw)
        if isinstance(obj, dict):
            e = obj.get("error")
            if isinstance(e, dict) and e.get("message"):
                detail = str(e["message"]).strip()
            elif isinstance(e, str):
                detail = e.strip()
    except Exception:
        pass
    if code in (401, 403):
        return f"API key rejected (HTTP {code})" + (f": {detail[:90]}" if detail else "")
    if code == 404:
        return f"Model/endpoint not found (HTTP 404)" + (f": {detail[:90]}" if detail else "")
    if code == 429:
        return "Rate limit or quota reached (HTTP 429) — try again in a moment"
    if code == 400:
        return f"Bad request (HTTP 400)" + (f": {detail[:100]}" if detail else "")
    return f"HTTP {code}" + (f": {detail[:100]}" if detail else "")


def _call_gemini_raw(
    cfg: AIConfig,
    system_prompt: str,
    turns: list[dict[str, str]],
    expect_json: bool = True,
    temperature: float = 0.85,
    max_tokens: int = 1024,
    timeout: float = 14.0,
) -> tuple[bool, str]:
    api_key = (cfg.api_key or "").strip()
    if not api_key:
        return False, "Missing Gemini API key"
    model = (cfg.model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    if model.startswith("models/"):
        model = model[len("models/"):]

    contents: list[dict[str, Any]] = []
    for t in turns:
        role = "model" if t.get("role") in ("model", "assistant") else "user"
        txt = str(t.get("text") or "").strip()
        if not txt:
            continue
        # Merge adjacent same-role turns cleanly for strict Gemini turn alternation
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": txt})
        else:
            contents.append({"role": role, "parts": [{"text": txt}]})
    if not contents:
        return False, "Empty prompt"

    gen_cfg: dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    if expect_json:
        gen_cfg["responseMimeType"] = "application/json"

    body_bytes = json.dumps({
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }).encode("utf-8")

    # Try user's selected model first; if Google returns 404 (deprecated model),
    # fall back cleanly to current production Flash models without failing.
    candidates_models = [model]
    for fb_m in ("gemini-3.5-flash-lite", "gemini-3.6-flash"):
        if fb_m not in candidates_models:
            candidates_models.append(fb_m)

    data = None
    last_err = ""
    for mdl in candidates_models:
        safe_model = urllib.parse.quote(mdl, safe=".-_")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{safe_model}:generateContent"
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            break
        except urllib.error.HTTPError as err:
            last_err = _extract_http_error(err)
            if getattr(err, "code", 0) == 404 and mdl != candidates_models[-1]:
                continue
            return False, last_err
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return False, f"Connection error: {exc}"

    if data is None:
        return False, last_err or "Gemini request failed"

    candidates = data.get("candidates") or []
    if not candidates:
        fb = data.get("promptFeedback") or {}
        reason = fb.get("blockReason") or "No response returned"
        return False, f"Gemini returned no text ({reason})"

    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    # Filter out Gemini internal thinking parts (`thought: true`) cleanly
    texts = [
        str(p.get("text") or "")
        for p in parts
        if isinstance(p, dict) and not p.get("thought") and p.get("text")
    ]
    out = "\n".join(texts).strip()
    if not out:
        return False, "Empty Gemini response"
    return True, out


def _call_openai_raw(
    cfg: AIConfig,
    system_prompt: str,
    turns: list[dict[str, str]],
    expect_json: bool = True,
    temperature: float = 0.8,
    max_tokens: int = 320,
    timeout: float = 14.0,
) -> tuple[bool, str]:
    url = (cfg.endpoint or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "Invalid OpenAI-compatible endpoint URL"
    model = (cfg.model or "llama3.2").strip() or "llama3.2"

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for t in turns:
        role = "assistant" if t.get("role") in ("model", "assistant") else "user"
        txt = str(t.get("text") or "").strip()
        if txt:
            messages.append({"role": role, "content": txt})

    body_obj: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if expect_json:
        body_obj["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if (cfg.api_key or "").strip():
        headers["Authorization"] = f"Bearer {cfg.api_key.strip()}"

    req = urllib.request.Request(
        url, data=json.dumps(body_obj).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as err:
        # Retry once without response_format if a local server rejects json_object
        if expect_json and getattr(err, "code", 0) == 400:
            body_obj.pop("response_format", None)
            req2 = urllib.request.Request(
                url, data=json.dumps(body_obj).encode("utf-8"), headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                    data = json.loads(resp2.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as err2:
                return False, _extract_http_error(err2)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc2:
                return False, f"Connection error: {exc2}"
        else:
            return False, _extract_http_error(err)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"Connection error: {exc}"

    choices = data.get("choices") or []
    if not choices:
        return False, "Empty AI response"
    msg = choices[0].get("message") or {}
    content = str(msg.get("content") or "").strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return (True, content) if content else (False, "Empty AI response")


def _dispatch_raw(
    cfg: AIConfig,
    system_prompt: str,
    turns: list[dict[str, str]],
    expect_json: bool = True,
    temperature: float = 0.85,
    max_tokens: int = 1024,
    timeout: float = 14.0,
) -> tuple[bool, str]:
    prov = (cfg.provider or "gemini").strip().lower()
    if prov == "gemini":
        return _call_gemini_raw(
            cfg, system_prompt, turns,
            expect_json=expect_json, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
        )
    return _call_openai_raw(
        cfg, system_prompt, turns,
        expect_json=expect_json, temperature=temperature,
        max_tokens=min(max_tokens, 420), timeout=timeout,
    )


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    txt = (raw or "").strip()
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL).strip()
    # Strip markdown ```json ... ``` fences if present
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, flags=re.DOTALL)
    if m:
        txt = m.group(1)
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    # Fallback: locate first balanced {...} substring
    s = txt.find("{")
    e = txt.rfind("}")
    if s != -1 and e > s:
        try:
            obj = json.loads(txt[s : e + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return None


def _clean_dialogue_text(raw: str, limit: int = 340) -> str:
    txt = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    txt = re.sub(r"\s+", " ", txt).strip().strip('"').strip("'")
    return txt[:limit]


# ---------------------------------------------------------------------------
# High-Level Morgana <-> Joker Companion AI Flows
# ---------------------------------------------------------------------------
def ask_morgana_chat(
    prompt: str,
    ctx: dict,
    cfg: AIConfig,
    history: list[dict[str, str]] | None = None,
    timeout: float = 14.0,
) -> AIReply:
    """Multi-turn Morgana <-> Joker conversational reply with matching expression, voice & particle."""
    if not cfg.is_ready():
        return AIReply(ok=False, text="", error="AI not configured")

    sys_prompt = (
        build_morgana_system_prompt(ctx, cfg.call_user)
        + "\n\nRespond ONLY with a valid JSON object matching this exact schema:\n"
        '{"reply": "<1-3 natural Persona 5 sentences>", '
        '"emotion": "<one of: neutral, wink, serious, worried, angry, sad, uneasy, excited, shout, smug>", '
        '"voice": "<one of: dousuru, hmm, hoi, naa, oh_asking, oh_excited, uwooh, yareyare>", '
        '"particle": "<one of: \'\', sparkle, music, heart, alert, question, anger, zzz>", '
        '"suggested_action": "<optional, usually \'\', or one of: focus_25, break_5, open_tasks, open_notes, open_events, open_stats, open_briefing, open_wrapup>"}'
    )
    turns = list(history or [])[-10:]
    turns.append({"role": "user", "text": (prompt or "").strip()[:600]})

    ok, raw = _dispatch_raw(cfg, sys_prompt, turns, expect_json=True, temperature=0.88, max_tokens=1024, timeout=timeout)
    if not ok:
        return AIReply(ok=False, text="", error=raw)

    obj = _extract_json_object(raw)
    if obj and isinstance(obj.get("reply"), str) and obj["reply"].strip():
        reply_txt = _clean_dialogue_text(obj["reply"])
        raw_emo = normalize_expression(str(obj.get("emotion") or ""), "wink")
        raw_voice = normalize_voice(str(obj.get("voice") or ""), "hmm")
        if raw_emo == "sad" and emotion_for_text(reply_txt) in ("excited", "wink", "smug"):
            raw_emo = emotion_for_text(reply_txt)
        emo, vkey = resolve_reaction(reply_txt, emotion=raw_emo, voice=raw_voice)
        return AIReply(
            ok=True,
            text=reply_txt,
            emotion=emo,
            voice=str(vkey) if isinstance(vkey, str) else "hmm",
            particle=normalize_particle(str(obj.get("particle") or "")),
            suggested_action=normalize_action(str(obj.get("suggested_action") or "")),
        )
    # Graceful plain-text fallback if a custom local model ignored JSON instructions
    cleaned = _clean_dialogue_text(raw)
    if cleaned:
        emo, vkey = resolve_reaction(cleaned)
        return AIReply(ok=True, text=cleaned, emotion=emo, voice=str(vkey) if isinstance(vkey, str) else "hmm", particle="")
    return AIReply(ok=False, text="", error="Empty AI reply")


def generate_joker_talk_scene(
    ctx: dict,
    cfg: AIConfig,
    history: list[dict[str, str]] | None = None,
    trigger_hint: str = "talk",
    timeout: float = 12.0,
) -> AITalkScene:
    """Generate a live Persona 5 Morgana <-> Joker interactive dialogue scene with 3 dynamic choices."""
    if not cfg.is_ready():
        return AITalkScene(ok=False, line="", error="AI not configured")

    call_user = (cfg.call_user or DEFAULT_CALL_USER).strip() or DEFAULT_CALL_USER
    sys_prompt = (
        build_morgana_system_prompt(ctx, call_user)
        + f"\n\nStart an interactive Persona 5 hangout/check-in scene with {call_user} (trigger: {trigger_hint}).\n"
        f"React specifically to what {call_user} is doing right now (their active app/topic, tasks, time of day, or screen time) "
        f"so it feels alive and personal—NOT like a generic script!\n"
        f"Provide 3 distinct, concise {call_user}-style Persona 5 dialogue choices (each 2 to 7 words—e.g. cool/confident, cheeky/playful, or practical).\n"
        f"Respond ONLY with valid JSON matching this schema:\n"
        '{"line": "<Morgana opening line, 1-2 punchy sentences>", '
        '"emotion": "<neutral|wink|serious|worried|angry|sad|uneasy|excited|shout|smug>", '
        '"voice": "<dousuru|hmm|hoi|naa|oh_asking|oh_excited|uwooh|yareyare>", '
        '"particle": "<\'\'|sparkle|music|heart|alert|question|anger|zzz>", '
        '"choices": ['
        '{"label": "<short Joker reply 1>", "action": "<\'\'|focus_25|break_5|open_tasks|open_notes|open_wrapup>"}, '
        '{"label": "<short Joker reply 2>", "action": ""}, '
        '{"label": "<short Joker reply 3>", "action": ""}'
        ']}'
    )
    turns = list(history or [])[-6:]
    turns.append({
        "role": "user",
        "text": f"[{call_user} just tapped Morgana to talk. Start a fresh contextual Persona 5 banter scene!]",
    })

    ok, raw = _dispatch_raw(cfg, sys_prompt, turns, expect_json=True, temperature=0.92, max_tokens=1024, timeout=timeout)
    if not ok:
        return AITalkScene(ok=False, line="", error=raw)

    obj = _extract_json_object(raw)
    if not obj or not isinstance(obj.get("line"), str) or not obj["line"].strip():
        return AITalkScene(ok=False, line="", error="Invalid scene JSON")

    raw_choices = obj.get("choices") or []
    parsed_choices: list[AITalkChoice] = []
    if isinstance(raw_choices, list):
        for item in raw_choices[:3]:
            if isinstance(item, dict) and isinstance(item.get("label"), str):
                lbl = _clean_dialogue_text(item["label"], limit=48)
                if lbl:
                    parsed_choices.append(AITalkChoice(
                        label=lbl,
                        action=normalize_action(str(item.get("action") or "")),
                    ))
            elif isinstance(item, str) and item.strip():
                parsed_choices.append(AITalkChoice(label=_clean_dialogue_text(item, limit=48)))

    if len(parsed_choices) < 2:
        return AITalkScene(ok=False, line="", error="Too few choices returned")

    line_txt = _clean_dialogue_text(obj["line"], limit=260)
    raw_emo = normalize_expression(str(obj.get("emotion") or ""), "wink")
    raw_voice = normalize_voice(str(obj.get("voice") or ""), "naa")
    if raw_emo == "sad" and emotion_for_text(line_txt) in ("excited", "wink", "smug"):
        raw_emo = emotion_for_text(line_txt)
    emo, vkey = resolve_reaction(line_txt, emotion=raw_emo, voice=raw_voice)

    return AITalkScene(
        ok=True,
        line=line_txt,
        emotion=emo,
        voice=str(vkey) if isinstance(vkey, str) else "naa",
        particle=normalize_particle(str(obj.get("particle") or "")),
        choices=tuple(parsed_choices),
    )


def continue_joker_talk_choice(
    opening_line: str,
    chosen_label: str,
    ctx: dict,
    cfg: AIConfig,
    history: list[dict[str, str]] | None = None,
    timeout: float = 12.0,
) -> AIReply:
    """Generate Morgana's lively in-character follow-up after Joker picks a Talk choice."""
    turns = list(history or [])[-8:]
    if opening_line:
        turns.append({"role": "model", "text": opening_line[:260]})
    return ask_morgana_chat(
        prompt=chosen_label,
        ctx=ctx,
        cfg=cfg,
        history=turns,
        timeout=timeout,
    )


def generate_ambient_reaction(
    event_desc: str,
    ctx: dict,
    cfg: AIConfig,
    timeout: float = 10.0,
) -> AIReply:
    """Generate a single snappy Persona 5 reaction line for an active-app observation."""
    if not cfg.is_ready():
        return AIReply(ok=False, text="", error="AI not configured")
    return ask_morgana_chat(
        prompt=f"[Ambient observation: {event_desc}. Make one short, witty 1-sentence Morgana remark in character.]",
        ctx=ctx,
        cfg=cfg,
        history=None,
        timeout=timeout,
    )


def test_ai_connection(cfg: AIConfig, timeout: float = 10.0) -> tuple[bool, str]:
    """Quick connectivity test for the Settings 'TEST KEY' button."""
    if not cfg.is_ready():
        if (cfg.provider or "gemini").lower() == "gemini":
            return False, "Paste your Gemini API key first (from aistudio.google.com/apikey)."
        return False, "Enter a valid http:// or https:// endpoint URL first."

    sample_ctx = {
        "hour": 14,
        "active_app_name": "Morgana Companion",
        "pending_tasks": 2,
        "overdue_count": 0,
        "screen_time_text": "1h 10m",
        "continuous_time_text": "25m",
    }
    res = ask_morgana_chat(
        prompt="Quick mic check, Morgana! Say hello in one short line.",
        ctx=sample_ctx,
        cfg=cfg,
        history=None,
        timeout=timeout,
    )
    if res.ok and res.text:
        return True, f"Connected! Morgana: \"{res.text[:90]}\""
    return False, res.error or "Connection failed"
