# -*- coding: utf-8 -*-
"""Opt-in Google Sheets cloud relay (write-only output) for WhatsApp Bot reminders.

Zero Qt dependency and zero extra pip dependencies beyond `requirements.txt`
(`google-api-python-client`, `google-auth-httplib2`).

Design:
  * Strictly opt-in: NEVER sends network requests unless enabled in Settings
    with a valid Spreadsheet ID and Service Account JSON key file.
  * Write-only output during sync: Morgana pushes its authoritative active
    deadline tasks to a dedicated tab (default: `Morgana`) whenever tasks
    change, without reading cells back from Sheets.
  * The Termux WhatsApp Bot periodically pulls that tab using its existing
    Google Sheets connection and sends staged WhatsApp reminders even when
    this PC is turned off.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_SHEETS_TAB = "Morgana"
SHEET_HEADERS: tuple[str, ...] = ("id", "title", "deadline", "priority", "stat", "updated_at")
MAX_SYNC_TASKS = 500
PIP_HINT = "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"

_SHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{10,128})")
_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_BAD_TAB_CHARS_RE = re.compile(r"[\[\]:*?/\\]")


def extract_spreadsheet_id(raw: str | None) -> str:
    """Accept either a raw Google Spreadsheet ID or a full Docs URL; return clean ID or ''."""
    text = (raw or "").strip()
    if not text:
        return ""
    m = _SHEET_URL_RE.search(text)
    if m:
        return m.group(1)
    if _SHEET_ID_RE.fullmatch(text):
        return text
    return ""


def normalize_tab_name(raw: str | None) -> str:
    """Sanitize worksheet tab name (defaults to 'Morgana')."""
    cleaned = _BAD_TAB_CHARS_RE.sub("", (raw or "").strip()).strip("'")
    return cleaned[:60] or DEFAULT_SHEETS_TAB


def _a1_range(tab_name: str, cells: str) -> str:
    escaped = tab_name.replace("'", "''")
    return f"'{escaped}'!{cells}"


@dataclass(frozen=True)
class SheetsSyncConfig:
    enabled: bool = False
    spreadsheet_id: str = ""
    tab_name: str = DEFAULT_SHEETS_TAB
    key_path: str = ""

    def normalized_sheet_id(self) -> str:
        return extract_spreadsheet_id(self.spreadsheet_id)

    def normalized_tab(self) -> str:
        return normalize_tab_name(self.tab_name)

    def resolve_key_file(self, base_dir: Path) -> Path | None:
        raw = (self.key_path or "").strip()
        if raw:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = Path(base_dir) / p
            return p if p.is_file() else None
        for name in ("sheets_key.json", "kunci-google.json"):
            cand = Path(base_dir) / name
            if cand.is_file():
                return cand
        return None

    def is_ready(self, base_dir: Path) -> bool:
        return bool(self.enabled and self.normalized_sheet_id() and self.resolve_key_file(base_dir))


@dataclass
class SheetsPushResult:
    ok: bool
    synced_count: int = 0
    error: str = ""


def _parse_local_aware(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.astimezone()


def serialize_tasks_to_rows(tasks: list[dict]) -> list[list[str]]:
    """Build `[header, *rows]` for active tasks with valid deadlines, sorted soonest-first."""
    entries: list[tuple[datetime, list[str]]] = []
    for t in tasks or ():
        if not isinstance(t, dict) or t.get("done"):
            continue
        tid = str(t.get("id") or "").strip()
        title = str(t.get("title") or "").strip()
        dl_dt = _parse_local_aware(t.get("deadline"))
        if not tid or not title or dl_dt is None:
            continue
        upd_dt = _parse_local_aware(t.get("updated_at") or t.get("created"))
        row = [
            tid,
            title[:160],
            dl_dt.isoformat(timespec="seconds"),
            "high" if t.get("priority") == "high" else "normal",
            str(t.get("stat") or "proficiency"),
            upd_dt.isoformat(timespec="seconds") if upd_dt is not None else "",
        ]
        entries.append((dl_dt, row))
    entries.sort(key=lambda pair: pair[0])
    rows = [list(SHEET_HEADERS)]
    rows.extend(row for _, row in entries[:MAX_SYNC_TASKS])
    return rows


def _import_google_sheets():
    import httplib2
    from google.oauth2 import service_account
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    return httplib2, service_account, AuthorizedHttp, build, HttpError


def _load_key_info(key_file: Path) -> tuple[dict | None, str]:
    try:
        data = json.loads(key_file.read_text(encoding="utf-8"))
    except OSError:
        return None, f"Cannot read key file: {key_file.name}"
    except ValueError:
        return None, f"Invalid JSON in key file: {key_file.name}"
    if not isinstance(data, dict) or data.get("type") != "service_account":
        return None, "Selected JSON is not a Google Service Account key (expected type: service_account)."
    if not data.get("client_email") or not data.get("private_key"):
        return None, "Service Account JSON is missing client_email or private_key."
    return data, ""


def _build_service(key_file: Path, timeout: float = 12.0):
    try:
        httplib2, service_account, AuthorizedHttp, build, HttpError = _import_google_sheets()
    except ImportError:
        return None, "", None, f"Google API packages missing ({PIP_HINT})."
    info, err = _load_key_info(key_file)
    if err or info is None:
        return None, "", HttpError, err
    client_email = str(info.get("client_email") or "")
    try:
        creds = service_account.Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
        authed = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
        service = build("sheets", "v4", http=authed, cache_discovery=False)
        return service, client_email, HttpError, ""
    except Exception as exc:
        return None, client_email, HttpError, f"Failed to initialize Google Sheets client ({type(exc).__name__})."


def _close_service(service) -> None:
    try:
        service.close()
    except Exception:
        pass


def _friendly_http_error(exc, client_email: str) -> str:
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    if status == 403:
        if client_email:
            return f"Permission denied (403). Share the sheet as Editor with {client_email}."
        return "Permission denied (403). Share the sheet with your Service Account email as Editor."
    if status == 404:
        return "Spreadsheet not found (404). Check your Sheet ID / URL."
    if status == 429:
        return "Google Sheets rate limit reached (429). Retrying automatically."
    if status:
        return f"Google Sheets API error (HTTP {status})."
    return f"Google Sheets request failed ({type(exc).__name__})."


def _create_tab(service, sheet_id: str, tab_name: str, HttpError) -> None:
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
    except HttpError as exc:
        # Ignore race if tab was just created
        if "already exists" not in str(exc).lower():
            raise


def test_sheets_connection(cfg: SheetsSyncConfig, base_dir: Path, timeout: float = 10.0) -> tuple[bool, str]:
    """Verify Service Account access, check Spreadsheet, and auto-create the target tab if needed."""
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return False, "Enter a valid Google Spreadsheet ID or URL."
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return False, "Select your Google Service Account JSON (e.g. kunci-google.json)."
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return False, err

    tab = cfg.normalized_tab()
    try:
        meta = service.spreadsheets().get(
            spreadsheetId=sheet_id,
            fields="properties.title,sheets.properties.title",
        ).execute()
        doc_title = str((meta.get("properties") or {}).get("title") or "Spreadsheet")
        existing_tabs = {
            str((s.get("properties") or {}).get("title") or "")
            for s in (meta.get("sheets") or ())
        }
        created_note = ""
        if tab not in existing_tabs:
            _create_tab(service, sheet_id, tab, HttpError)
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=_a1_range(tab, "A1"),
                valueInputOption="RAW",
                body={"values": [list(SHEET_HEADERS)]},
            ).execute()
            created_note = " (tab created)"
        return True, f"Connected · '{doc_title}' -> tab '{tab}' ready{created_note}"
    except HttpError as exc:
        return False, _friendly_http_error(exc, client_email)
    except TimeoutError:
        return False, "Connection to Google Sheets timed out."
    except Exception as exc:
        return False, f"Sheets connection failed ({type(exc).__name__})."
    finally:
        _close_service(service)


def push_tasks_to_sheets(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    rows: list[list[str]],
    timeout: float = 12.0,
) -> SheetsPushResult:
    """Write-only output: overwrite the dedicated Morgana tab with active task deadline rows."""
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return SheetsPushResult(ok=False, error="Spreadsheet ID is not configured.")
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return SheetsPushResult(ok=False, error="Service Account JSON key file not found.")
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return SheetsPushResult(ok=False, error=err)

    tab = cfg.normalized_tab()
    clear_rng = _a1_range(tab, f"A1:F{MAX_SYNC_TASKS + 20}")
    write_rng = _a1_range(tab, "A1")
    values = rows if rows else [list(SHEET_HEADERS)]

    def _write_once() -> None:
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=clear_rng,
            body={},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=write_rng,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    try:
        try:
            _write_once()
        except HttpError as exc:
            status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
            if status == 400 and "unable to parse range" in str(exc).lower():
                _create_tab(service, sheet_id, tab, HttpError)
                _write_once()
            else:
                raise
        return SheetsPushResult(ok=True, synced_count=max(0, len(values) - 1))
    except HttpError as exc:
        return SheetsPushResult(ok=False, error=_friendly_http_error(exc, client_email))
    except TimeoutError:
        return SheetsPushResult(ok=False, error="Google Sheets sync timed out.")
    except Exception as exc:
        return SheetsPushResult(ok=False, error=f"Google Sheets sync failed ({type(exc).__name__}).")
    finally:
        _close_service(service)
