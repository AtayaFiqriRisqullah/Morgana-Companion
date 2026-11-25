# -*- coding: utf-8 -*-
"""Opt-in WhatsApp Bot (Termux / Baileys FastAPI) task deadline sync client.

Zero Qt dependency and zero external pip dependencies (pure stdlib `urllib`).
Strictly opt-in: NEVER sends network requests unless the user enables WhatsApp
Sync in Settings and configures a valid HTTP(S) Bot API URL.

Sync protocol (timeout- & miss-resistant):
  1. Pull pending completions made via WhatsApp (`GET /morgana/outbox`).
  2. Caller applies completions to `TaskStore` (with timestamp conflict check)
     and saves `tasks.json` atomically.
  3. Push active deadline tasks + ACK the applied outbox IDs in one atomic
     server call (`POST /morgana/sync`). If step 3 times out, the server keeps
     un-acked outbox items so nothing is ever lost on retry.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class WhatsAppSyncConfig:
    enabled: bool = False
    url: str = ""
    token: str = ""

    def normalized_url(self) -> str:
        raw = (self.url or "").strip().rstrip("/")
        if not raw:
            return ""
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return raw

    def is_ready(self) -> bool:
        return bool(self.enabled and self.normalized_url())


@dataclass(frozen=True)
class OutboxItem:
    task_id: str
    title: str = ""
    done_at: str = ""


@dataclass
class OutboxResult:
    ok: bool
    items: list[OutboxItem] = field(default_factory=list)
    error: str = ""
    status_code: int = 0


@dataclass
class SyncPushResult:
    ok: bool
    synced_count: int = 0
    remaining_outbox: int = 0
    server_time: str = ""
    error: str = ""
    status_code: int = 0


def parse_remote_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (aware, naive, or `Z` suffix) into a timezone-aware datetime."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt


def to_local_aware_iso(value: str | None) -> str:
    """Convert a local naive or aware ISO timestamp into an explicit offset ISO string."""
    dt = parse_remote_dt(value)
    if dt is None:
        return ""
    return dt.astimezone().isoformat(timespec="seconds")


def serialize_tasks_for_sync(tasks: list[dict]) -> list[dict]:
    """Filter and normalize active tasks that have a valid deadline for the WhatsApp bot."""
    payload: list[dict] = []
    for t in tasks:
        if not isinstance(t, dict) or t.get("done"):
            continue
        tid = str(t.get("id") or "").strip()
        title = str(t.get("title") or "").strip()
        dl_iso = to_local_aware_iso(t.get("deadline"))
        if not tid or not title or not dl_iso:
            continue
        upd_iso = to_local_aware_iso(t.get("updated_at") or t.get("created"))
        payload.append({
            "id": tid,
            "title": title[:160],
            "deadline": dl_iso,
            "priority": "high" if t.get("priority") == "high" else "normal",
            "stat": str(t.get("stat") or "proficiency"),
            "updated_at": upd_iso,
        })
    return payload


def _extract_error_detail(body: bytes, fallback: str) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            detail = data.get("detail") or data.get("error") or data.get("galat")
            if detail:
                return str(detail).strip()[:180]
    except (ValueError, TypeError):
        pass
    return fallback


def _http_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str = "",
    payload: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> tuple[bool, dict[str, Any], int, str]:
    """Perform a single JSON HTTP request without ever leaking `token` in errors."""
    url = base_url.rstrip("/") + path
    headers = {
        "Accept": "application/json",
        "User-Agent": "Morgana-Companion/1.5.0",
    }
    tok = (token or "").strip()
    if tok:
        headers["X-Morgana-Token"] = tok
    data_bytes: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read(2 * 1024 * 1024)
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(parsed, dict):
                return False, {}, status, "Unexpected non-object JSON response from Bot WA."
            return True, parsed, status, ""
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        try:
            body = exc.read(4096)
        except Exception:
            body = b""
        if code == 401:
            msg = _extract_error_detail(body, "Unauthorized (check Sync Token).")
        elif code == 503:
            msg = _extract_error_detail(body, "MORGANA_SYNC_TOKEN is not configured in bot-wa .env.")
        else:
            msg = _extract_error_detail(body, f"HTTP {code} from Bot WA.")
        return False, {}, code, msg
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            return False, {}, 0, "Connection timed out (check Tailscale / Wi-Fi)."
        return False, {}, 0, f"Cannot reach Bot WA ({type(reason).__name__})."
    except TimeoutError:
        return False, {}, 0, "Connection timed out (check Tailscale / Wi-Fi)."
    except (ValueError, OSError) as exc:
        return False, {}, 0, f"Sync request failed ({type(exc).__name__})."


def test_whatsapp_connection(cfg: WhatsAppSyncConfig, timeout: float = 6.0) -> tuple[bool, str]:
    """Verify reachability and token authentication against `GET /morgana/status`."""
    base = cfg.normalized_url()
    if not base:
        return False, "Enter a valid http:// or https:// Bot API URL."
    ok, data, _code, err = _http_json(base, "/morgana/status", method="GET", token=cfg.token, timeout=timeout)
    if not ok:
        return False, err
    if not data.get("ok"):
        return False, str(data.get("detail") or "Bot WA returned an error.")
    synced = int(data.get("synced_tasks", 0))
    pending = int(data.get("pending_outbox", 0))
    extra = f" · {pending} pending done" if pending else ""
    return True, f"Connected · {synced} synced deadline{'s' if synced != 1 else ''}{extra}"


def pull_whatsapp_outbox(cfg: WhatsAppSyncConfig, timeout: float = 8.0) -> OutboxResult:
    """Step 1 of sync: pull pending task completions marked done via WhatsApp."""
    base = cfg.normalized_url()
    if not base:
        return OutboxResult(ok=False, error="Bot API URL is not configured.")
    ok, data, code, err = _http_json(base, "/morgana/outbox", method="GET", token=cfg.token, timeout=timeout)
    if not ok:
        return OutboxResult(ok=False, error=err, status_code=code)
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[OutboxItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("task_id") or "").strip()
        if not tid:
            continue
        items.append(OutboxItem(
            task_id=tid,
            title=str(entry.get("title") or "").strip(),
            done_at=str(entry.get("done_at") or "").strip(),
        ))
    return OutboxResult(ok=True, items=items, status_code=code)


def push_whatsapp_sync(
    cfg: WhatsAppSyncConfig,
    tasks_payload: list[dict],
    ack_ids: list[str] | None = None,
    timeout: float = 10.0,
) -> SyncPushResult:
    """Step 3 of sync: ACK applied outbox completions and push authoritative active deadlines."""
    base = cfg.normalized_url()
    if not base:
        return SyncPushResult(ok=False, error="Bot API URL is not configured.")
    clean_ack = [str(i).strip() for i in (ack_ids or ()) if str(i).strip()]
    body = {
        "ack_ids": clean_ack,
        "tasks": list(tasks_payload),
    }
    ok, data, code, err = _http_json(
        base, "/morgana/sync", method="POST", token=cfg.token, payload=body, timeout=timeout
    )
    if not ok:
        return SyncPushResult(ok=False, error=err, status_code=code)
    return SyncPushResult(
        ok=bool(data.get("ok", True)),
        synced_count=int(data.get("synced_count", len(tasks_payload))),
        remaining_outbox=int(data.get("remaining_outbox", 0)),
        server_time=str(data.get("server_time") or ""),
        status_code=code,
    )
