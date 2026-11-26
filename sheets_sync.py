# -*- coding: utf-8 -*-
"""Opt-in Google Sheets integration for Morgana Companion (bot-wa compatible).

Zero Qt dependency and zero extra pip dependencies beyond `requirements.txt`
(`google-api-python-client`, `google-auth-httplib2`).

Design:
  * Strictly opt-in: NEVER sends network requests unless enabled in Settings
    with a valid Spreadsheet ID and Service Account JSON key file.
  * Task Deadline Relay (write-only): pushes active deadline tasks to a
    dedicated tab (default: `Morgana`) for WhatsApp Bot reminders.
  * Finance Monthly Budget Sync (read/write): connects directly to the same
    `bot-wa` Monthly Budget spreadsheet (`Januari`..`Desember` tabs, summary
    ranges `G9:H12` & `K9:L12`, 4 expense tables, Pemasukan `B10:D31`, and
    Pemasukan Bank `T10:V31`) using identical formula-safe write & undo rules.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_SHEETS_TAB = "Morgana"
COMPANION_SYNC_TAB = "Morgana_Sync"
COMPANION_SYNC_STORES: tuple[str, ...] = ("tasks", "notes", "social_stats", "local_events", "habits")
COMPANION_SYNC_HEADERS: tuple[str, ...] = ("store", "updated_at", "count", "payload_0")
SYNC_CHUNK_SIZE = 45000
SYNC_TOMBSTONE_MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000
SHEET_HEADERS: tuple[str, ...] = ("id", "title", "deadline", "priority", "stat", "updated_at")
MAX_SYNC_TASKS = 500
PIP_HINT = "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"

# Exact bot-wa Monthly Budget spreadsheet schema
FINANCE_MONTH_TABS: tuple[str, ...] = (
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
)
FINANCE_SHEET_CAT_COLS: dict[str, tuple[str, str]] = {
    "Jajan": ("B", "D"),
    "Alat": ("G", "I"),
    "Mingguan&Bulanan": ("K", "M"),
    "Hiburan": ("P", "R"),
}
FINANCE_EXPENSE_TOTAL_ROW = 35
FINANCE_EXPENSE_ROW_START = 36
FINANCE_EXPENSE_ROW_END = 1000
FINANCE_INCOME_ROW_START = 10
FINANCE_INCOME_ROW_END = 31
FINANCE_BANK_ROW_START = 10
FINANCE_BANK_ROW_END = 31
FINANCE_SUMMARY_RANGE = "G9:H12"
FINANCE_BANK_BAL_RANGE = "K9:L12"
QRIS_SOURCE_BY_BANK: dict[str, str] = {"BNI": "QRIS BNI", "Mandiri": "QRIS Mandiri"}
FINANCE_BANK_WORDS: tuple[str, ...] = ("BNI", "Mandiri")

_FINANCE_WRITE_LOCK = threading.RLock()

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


def _format_rupiah_plain(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}Rp {abs(int(amount)):,}".replace(",", ".")


_ENGLISH_MONTH_TABS: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _match_month_tab(existing_tabs: list[str] | set[str], month: int) -> str | None:
    """Match a worksheet tab for `month` (1..12), tolerant of case, whitespace, Indonesian & English names."""
    idx = (int(month) - 1) % 12
    id_name = FINANCE_MONTH_TABS[idx].lower()
    en_name = _ENGLISH_MONTH_TABS[idx].lower()
    prefixes = {id_name[:3], en_name[:3]}
    tabs_list = [str(t) for t in existing_tabs if str(t).strip()]
    # 1. Exact Indonesian / English match (case & surrounding whitespace insensitive)
    for t in tabs_list:
        low = t.strip().lower()
        if low == id_name or low == en_name:
            return t
    # 2. Prefix match (e.g. 'Nov', 'Okt', 'November 2026')
    for t in tabs_list:
        low = t.strip().lower()
        first = low.split()[0] if low else ""
        if first in prefixes or low.startswith(id_name) or low.startswith(en_name):
            return t
    return None


def _month_tabs_in(existing_tabs: list[str] | set[str]) -> list[str]:
    """Return worksheet tabs that match any calendar month, preserving spreadsheet order."""
    out: list[str] = []
    for t in existing_tabs:
        for m in range(1, 13):
            if _match_month_tab([t], m) is not None:
                out.append(str(t))
                break
    return out


def test_sheets_connection(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    timeout: float = 10.0,
    check_tasks: bool = True,
    check_finance: bool = False,
) -> tuple[bool, str]:
    """Verify Service Account access, check Spreadsheet, and verify Tasks / Finance tabs."""
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
        existing_list = [
            str((s.get("properties") or {}).get("title") or "")
            for s in (meta.get("sheets") or ())
        ]
        existing_tabs = set(existing_list)
        notes: list[str] = []
        if check_tasks or not check_finance:
            created_note = ""
            if tab not in existing_tabs:
                _create_tab(service, sheet_id, tab, HttpError)
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(tab, "A1"),
                    valueInputOption="RAW",
                    body={"values": [list(SHEET_HEADERS)]},
                ).execute()
                created_note = " (created)"
            sync_created = ""
            if COMPANION_SYNC_TAB not in existing_tabs:
                _create_tab(service, sheet_id, COMPANION_SYNC_TAB, HttpError)
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(COMPANION_SYNC_TAB, "A1"),
                    valueInputOption="RAW",
                    body={"values": [list(COMPANION_SYNC_HEADERS)]},
                ).execute()
                sync_created = " (created)"
            notes.append(f"Sync '{COMPANION_SYNC_TAB}' ready{sync_created} · Deadlines '{tab}' ready{created_note}")
        if check_finance:
            cur_month = date.today().month
            matched = _match_month_tab(existing_list, cur_month)
            if matched is not None:
                bal_note = ""
                try:
                    snap = service.spreadsheets().values().batchGet(
                        spreadsheetId=sheet_id,
                        ranges=[_a1_range(matched, FINANCE_SUMMARY_RANGE), _a1_range(matched, FINANCE_BANK_BAL_RANGE)],
                        valueRenderOption="UNFORMATTED_VALUE",
                    ).execute()
                    vrs = snap.get("valueRanges") or []
                    b0 = (vrs[0].get("values") or []) if len(vrs) > 0 else []
                    b1 = (vrs[1].get("values") or []) if len(vrs) > 1 else []
                    h_map = {str(r[0]).strip().lower(): float(r[1]) for r in b0 if len(r) >= 2 and _is_number(r[1])}
                    k_map = {str(r[0]).strip().lower(): float(r[1]) for r in b1 if len(r) >= 2 and _is_number(r[1])}
                    tot = int(round(h_map.get("sisa uang", 0.0) + k_map.get("bni", 0.0) + k_map.get("mandiri", 0.0)))
                    bal_note = f" ({_format_rupiah_plain(tot)})"
                except Exception:
                    pass
                notes.append(f"Finance tab '{matched}' ready{bal_note}")
            else:
                avail = _month_tabs_in(existing_list)
                avail_hint = f" (found: {', '.join(avail[-4:])})" if avail else ""
                notes.append(f"Finance tab '{month_tab_name(cur_month)}' not found yet{avail_hint}")
        return True, f"Connected · '{doc_title}' · " + " · ".join(notes)
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


# ---------------------------------------------------------------------------
# Finance Monthly Budget Sync (100% compatible with AtayaFiqriRisqullah/bot-wa)
# ---------------------------------------------------------------------------
@dataclass
class FinanceSheetMonthData:
    ok: bool
    tab: str = ""
    year: int = 0
    month: int = 0
    balances: dict[str, int] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    transactions: list[dict] = field(default_factory=list)
    synced_at: str = ""
    available_tabs: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class FinanceSheetWriteResult:
    ok: bool
    tab: str = ""
    cells: list[dict] = field(default_factory=list)
    message: str = ""
    error: str = ""


def month_tab_name(month: int) -> str:
    """Return Indonesian Monthly Budget worksheet tab name ('Januari'..'Desember')."""
    return FINANCE_MONTH_TABS[(int(month) - 1) % 12]


def safe_sheet_text(text: str) -> str:
    """Prefix leading formula characters (=, +, -, @) with apostrophe to prevent formula injection."""
    s = str(text or "").strip()
    if s and s[0] in "=+-@":
        return "'" + s
    return s


def forbidden_bank_word(text: str, allowed_bank: str | None = None) -> str | None:
    """Return forbidden bank name ('BNI'|'Mandiri') if `text` would corrupt Monthly Budget SUMIF formulas.

    Matches `bot-wa`'s exact rule:
      * Cash income (`allowed_bank=None`) and Transfer notes must NOT mention 'BNI' or 'Mandiri'.
      * Bank income (`allowed_bank='BNI'` or `'Mandiri'`) must NOT mention the OTHER bank.
    """
    low = str(text or "").lower()
    if not low:
        return None
    allow_low = str(allowed_bank or "").strip().lower()
    for bank in FINANCE_BANK_WORDS:
        if bank.lower() == allow_low:
            continue
        if bank.lower() in low:
            return bank
    return None


def _is_number(val) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def parse_sheet_date(cell) -> str:
    """Convert Google Sheets UNFORMATTED_VALUE date (Excel serial number or string) -> 'YYYY-MM-DD' or ''."""
    if isinstance(cell, bool) or cell is None:
        return ""
    if isinstance(cell, (int, float)):
        if cell <= 0 or cell > 200000:
            return ""
        try:
            return (date(1899, 12, 30) + timedelta(days=int(cell))).isoformat()
        except (OverflowError, ValueError):
            return ""
    raw = str(cell).strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _parse_tx_date(day: str | date | None) -> date:
    if isinstance(day, date):
        return day
    if isinstance(day, str) and day.strip():
        try:
            return date.fromisoformat(day.strip()[:10])
        except ValueError:
            pass
    return date.today()


def _is_missing_tab_error(exc) -> bool:
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    txt = str(exc).lower()
    return status == 400 and ("unable to parse range" in txt or "not found" in txt)


def _friendly_finance_http_error(exc, client_email: str, tab: str, avail: list[str] | None = None) -> str:
    if _is_missing_tab_error(exc):
        if avail:
            return f"Tab '{tab}' not found. Available tabs: {', '.join(avail)}."
        return f"Tab '{tab}' not found in spreadsheet. Create the '{tab}' sheet tab first."
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    if status == 400:
        detail = str(exc)
        if "exceeds grid limits" in detail.lower():
            return f"Tab '{tab}' grid range error (HTTP 400) — check tab rows/columns."
        return f"Google Sheets rejected range on '{tab}' (HTTP 400)."
    return _friendly_http_error(exc, client_email)


def _find_empty_rows(col_values: list[list], row_start: int, row_end: int, count: int = 1) -> list[int]:
    """Return the first `count` empty 1-based row numbers in [row_start, row_end]."""
    found: list[int] = []
    for row_num in range(row_start, row_end + 1):
        idx = row_num - row_start
        cell = col_values[idx][0] if (idx < len(col_values) and col_values[idx]) else ""
        if str(cell).strip() == "":
            found.append(row_num)
            if len(found) == count:
                return found
    return []


def _format_bank_income_label(bank: str, title: str) -> str:
    clean = str(title or "").strip()
    if not clean:
        return bank
    if clean.lower().startswith(bank.lower()):
        return clean
    return f"{bank} - {clean}"


def _extract_bank_from_label(label: str) -> str:
    low = str(label or "").lower()
    if "mandiri" in low:
        return "Mandiri"
    return "BNI"


def _strip_transfer_note(label: str) -> str:
    """Extract optional user note after ' - ' from bot-wa transfer labels."""
    txt = str(label or "").strip()
    if txt.endswith(")") and " - " in txt:
        inner = txt.rsplit(" - ", 1)[1].rstrip(")").strip()
        return inner
    if " - " in txt:
        return txt.split(" - ", 1)[1].strip()
    return ""


def _resolve_finance_tab_or_list(service, sheet_id: str, month: int) -> tuple[str | None, list[str]]:
    """Inspect spreadsheet tabs and match `month` (handles trailing spaces, casing, EN/ID names)."""
    meta = service.spreadsheets().get(
        spreadsheetId=sheet_id,
        fields="sheets.properties.title",
    ).execute()
    titles = [
        str((s.get("properties") or {}).get("title") or "")
        for s in (meta.get("sheets") or ())
    ]
    return _match_month_tab(titles, month), _month_tabs_in(titles)


def _finance_batch_ranges(tab: str) -> list[str]:
    """Build open-ended ranges safe for any sheet row count (never triggers HTTP 400 grid limits)."""
    ranges = [
        _a1_range(tab, FINANCE_SUMMARY_RANGE),    # 0: G9:H12 (Hasil Akhir)
        _a1_range(tab, FINANCE_BANK_BAL_RANGE),   # 1: K9:L12 (Sisa Saldo Bank — Okt/Nov+)
        _a1_range(tab, "P9:Q12"),                 # 2: P9:Q12 (Aktivitas Bank — fallback for Sep)
    ]
    for cat in FINANCE_SHEET_CAT_COLS:
        col_a, col_c = FINANCE_SHEET_CAT_COLS[cat]
        ranges.append(_a1_range(tab, f"{col_a}{FINANCE_EXPENSE_TOTAL_ROW}:{col_c}"))  # 3..6: open-ended!
    ranges.append(_a1_range(tab, "B9:D34"))       # 7: Pemasukan B9:D31 + legacy Agustus B16:D34 check
    ranges.append(_a1_range(tab, "T8:V31"))       # 8: Pemasukan Bank T10:V31 (with header check T8)
    ranges.append(_a1_range(tab, "S8:U31"))       # 9: Legacy Pemasukan Bank S9:U31 (September)
    return ranges


def fetch_finance_month_from_sheets(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    year: int,
    month: int,
    timeout: float = 12.0,
) -> FinanceSheetMonthData:
    """Read live Monthly Budget balances, category totals, and transactions for `(year, month)`."""
    tab = month_tab_name(month)
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return FinanceSheetMonthData(ok=False, tab=tab, year=year, month=month,
                                     error="Spreadsheet ID is not configured in Settings.")
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return FinanceSheetMonthData(ok=False, tab=tab, year=year, month=month,
                                     error="Service Account JSON key file not found (e.g. kunci-google.json).")
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return FinanceSheetMonthData(ok=False, tab=tab, year=year, month=month, error=err)

    cat_names = list(FINANCE_SHEET_CAT_COLS.keys())
    avail_tabs: list[str] = []

    try:
        ranges = _finance_batch_ranges(tab)
        try:
            resp = service.spreadsheets().values().batchGet(
                spreadsheetId=sheet_id,
                ranges=ranges,
                valueRenderOption="UNFORMATTED_VALUE",
            ).execute()
        except HttpError as exc:
            if _is_missing_tab_error(exc):
                matched, avail_tabs = _resolve_finance_tab_or_list(service, sheet_id, month)
                if matched is not None and matched != tab:
                    tab = matched
                    ranges = _finance_batch_ranges(tab)
                    resp = service.spreadsheets().values().batchGet(
                        spreadsheetId=sheet_id,
                        ranges=ranges,
                        valueRenderOption="UNFORMATTED_VALUE",
                    ).execute()
                else:
                    return FinanceSheetMonthData(
                        ok=False, tab=tab, year=int(year), month=int(month),
                        available_tabs=avail_tabs,
                        error=_friendly_finance_http_error(exc, client_email, tab, avail=avail_tabs),
                    )
            else:
                raise

        vrs = resp.get("valueRanges") or []
        blocks = [(vrs[i].get("values") or []) if i < len(vrs) else [] for i in range(len(ranges))]

        # 1. Summary & Bank balances (G9:H12 + K9:L12, with P9:Q12 fallback for legacy September tab)
        hasil: dict[str, float] = {}
        for row in blocks[0]:
            if len(row) >= 2 and _is_number(row[1]):
                hasil[str(row[0]).strip().lower()] = float(row[1])
        bank_hasil: dict[str, float] = {}
        for row in blocks[1]:
            if len(row) >= 2 and _is_number(row[1]):
                bank_hasil[str(row[0]).strip().lower()] = float(row[1])
        bank_act_hasil: dict[str, float] = {}
        for row in blocks[2]:
            if len(row) >= 2 and _is_number(row[1]):
                bank_act_hasil[str(row[0]).strip().lower()] = float(row[1])

        b9_34 = blocks[7]
        t8_31 = blocks[8]
        s8_31 = blocks[9]

        # Detect legacy Agustus single-column expense table (B16='Pengeluaran', B17='Source')
        b16_val = str(b9_34[7][0]).strip().lower() if (len(b9_34) > 7 and b9_34[7]) else ""
        b17_val = str(b9_34[8][0]).strip().lower() if (len(b9_34) > 8 and b9_34[8]) else ""
        legacy_single_table = (b16_val == "pengeluaran" and b17_val == "source")

        # Detect legacy September bank table at S8:U31 when T8 is NOT 'Source' and S8 == 'Source'
        t8_hdr = str(t8_31[0][0]).strip().lower() if (t8_31 and t8_31[0]) else ""
        s8_hdr = str(s8_31[0][0]).strip().lower() if (s8_31 and s8_31[0]) else ""
        legacy_sep_bank = (t8_hdr != "source" and s8_hdr == "source")

        tunai_bal = int(round(hasil.get("sisa uang", 0.0)))
        bni_bal = int(round(bank_hasil.get("bni", 0.0)))
        mandiri_bal = int(round(bank_hasil.get("mandiri", 0.0)))
        if not bank_hasil and "sisa uang" in bank_act_hasil:
            # Legacy September sheet tracks combined bank remainder in P12 ('Sisa Uang')
            bni_bal = int(round(bank_act_hasil.get("sisa uang", 0.0)))
        balances = {
            "Tunai": tunai_bal,
            "BNI": bni_bal,
            "Mandiri": mandiri_bal,
            "Total": tunai_bal + bni_bal + mandiri_bal,
        }

        # 2. Expense tables
        by_category: dict[str, int] = {c: 0 for c in cat_names}
        transactions: list[dict] = []
        default_date = f"{int(year):04d}-{int(month):02d}-01"
        today_str = date.today().isoformat()
        today_expense = 0

        if legacy_single_table:
            # Legacy Agustus layout: expenses start at B18:D and continue past row 35
            combined_exp: list[tuple[int, list]] = []
            for offset, r in enumerate(b9_34[9:]):  # rows 18..34
                combined_exp.append((18 + offset, r))
            for offset, r in enumerate(blocks[3]):  # rows 35+
                row_num = FINANCE_EXPENSE_TOTAL_ROW + offset
                if row_num > FINANCE_EXPENSE_ROW_END:
                    break
                combined_exp.append((row_num, r))
            for row_num, r in combined_exp:
                nama, tgl, nilai = (list(r) + ["", "", ""])[:3]
                nama_str = str(nama).strip()
                if not _is_number(nilai):
                    continue
                val_int = int(round(float(nilai)))
                by_category["Jajan"] += val_int
                d_str = parse_sheet_date(tgl) or default_date
                if d_str == today_str:
                    today_expense += abs(val_int)
                rng = f"B{row_num}:D{row_num}"
                transactions.append({
                    "id": f"sh:{tab}:{rng}",
                    "type": "expense",
                    "amount": abs(val_int),
                    "title": nama_str or "Expense",
                    "category": "Jajan",
                    "wallet": "",
                    "to_wallet": "",
                    "date": d_str,
                    "source": "sheets",
                    "sheet_row": row_num,
                    "sheet_cells": [{"tab": tab, "range": rng, "name": nama_str, "amount": val_int}],
                })
        else:
            # Standard bot-wa 4-category expense tables (row 35 = TOTAL, rows 36..1000 = entries)
            for c_idx, cat in enumerate(cat_names):
                col_a, col_c = FINANCE_SHEET_CAT_COLS[cat]
                rows = blocks[3 + c_idx]
                total_row = rows[0] if rows else []
                cat_sum = 0
                for offset, r in enumerate(rows[1:]):
                    row_num = FINANCE_EXPENSE_ROW_START + offset
                    if row_num > FINANCE_EXPENSE_ROW_END:
                        break
                    nama, tgl, nilai = (list(r) + ["", "", ""])[:3]
                    nama_str = str(nama).strip()
                    if not nama_str and not _is_number(nilai):
                        continue
                    if not _is_number(nilai):
                        continue
                    val_int = int(round(float(nilai)))
                    cat_sum += val_int
                    d_str = parse_sheet_date(tgl) or default_date
                    if d_str == today_str:
                        today_expense += abs(val_int)
                    rng = f"{col_a}{row_num}:{col_c}{row_num}"
                    transactions.append({
                        "id": f"sh:{tab}:{rng}",
                        "type": "expense",
                        "amount": abs(val_int),
                        "title": nama_str or cat,
                        "category": cat,
                        "wallet": "",
                        "to_wallet": "",
                        "date": d_str,
                        "source": "sheets",
                        "sheet_row": row_num,
                        "sheet_cells": [{"tab": tab, "range": rng, "name": nama_str, "amount": val_int}],
                    })
                if len(total_row) >= 3 and _is_number(total_row[2]):
                    by_category[cat] = int(round(float(total_row[2])))
                else:
                    by_category[cat] = cat_sum

        # 3. Parse Pemasukan (B10:D31, or B9:D15 on legacy Agustus) & Pemasukan Bank (T10:V31 or legacy S9:U31)
        cash_raw: list[dict] = []
        if legacy_single_table:
            inc_slice = list(enumerate(b9_34[:7], start=9))  # rows 9..15
        else:
            # Skip row 9 ('Sisa Bulanan' carry-over) so users don't accidentally edit/delete it; read rows 10..31
            inc_slice = list(enumerate(b9_34[1:23], start=FINANCE_INCOME_ROW_START))

        for row_num, r in inc_slice:
            nama, tgl, nilai = (list(r) + ["", "", ""])[:3]
            nama_str = str(nama).strip()
            if not nama_str and not _is_number(nilai):
                continue
            if not _is_number(nilai):
                continue
            val_int = int(round(float(nilai)))
            rng = f"B{row_num}:D{row_num}"
            cash_raw.append({
                "row": row_num,
                "name": nama_str,
                "low": nama_str.lower(),
                "date": parse_sheet_date(tgl) or default_date,
                "amount": val_int,
                "cell": {"tab": tab, "range": rng, "name": nama_str, "amount": val_int},
            })

        bank_raw: list[dict] = []
        if legacy_sep_bank:
            bank_col_a, bank_col_c = "S", "U"
            bank_slice = list(enumerate(s8_31[1:], start=9))  # rows 9..31
        else:
            bank_col_a, bank_col_c = "T", "V"
            bank_slice = list(enumerate(t8_31[2:], start=FINANCE_BANK_ROW_START))  # rows 10..31

        for row_num, r in bank_slice:
            nama, tgl, nilai = (list(r) + ["", "", ""])[:3]
            nama_str = str(nama).strip()
            if not nama_str and not _is_number(nilai):
                continue
            if not _is_number(nilai):
                continue
            val_int = int(round(float(nilai)))
            rng = f"{bank_col_a}{row_num}:{bank_col_c}{row_num}"
            bank_raw.append({
                "row": row_num,
                "name": nama_str,
                "low": nama_str.lower(),
                "date": parse_sheet_date(tgl) or default_date,
                "amount": val_int,
                "cell": {"tab": tab, "range": rng, "name": nama_str, "amount": val_int},
            })

        used_bank_rows: set[int] = set()
        fallback_income = 0

        # Process Pemasukan (B10:D31)
        for item in cash_raw:
            low = item["low"]
            amt = item["amount"]
            if low in ("qris bni", "qris mandiri", "qris"):
                bank = "Mandiri" if "mandiri" in low else "BNI"
                transactions.append({
                    "id": f"sh:{tab}:{item['cell']['range']}",
                    "type": "transfer",
                    "amount": abs(amt),
                    "title": f"{item['name']} (Monthly Pool)" if low != "qris" else "QRIS",
                    "category": "",
                    "wallet": bank,
                    "to_wallet": "QRIS",
                    "date": item["date"],
                    "source": "sheets",
                    "sheet_kind": "qris_pool",
                    "sheet_row": item["row"],
                    "sheet_cells": [item["cell"]],
                })
            elif low.startswith("tarik uang"):
                bank = "Mandiri" if "mandiri" in low else "BNI"
                note = _strip_transfer_note(item["name"])
                transactions.append({
                    "id": f"sh:{tab}:{item['cell']['range']}",
                    "type": "transfer",
                    "amount": abs(amt),
                    "title": item["name"],
                    "edit_note": note,
                    "category": "",
                    "wallet": bank,
                    "to_wallet": "Tunai",
                    "date": item["date"],
                    "source": "sheets",
                    "sheet_row": item["row"],
                    "sheet_cells": [item["cell"]],
                })
            elif low.startswith("setor tunai") and amt < 0:
                partner = None
                for b in bank_raw:
                    if (b["row"] not in used_bank_rows and "(setor tunai" in b["low"]
                            and b["amount"] == -amt and b["date"] == item["date"]):
                        partner = b
                        break
                if partner is not None:
                    used_bank_rows.add(partner["row"])
                    to_bank = _extract_bank_from_label(partner["name"])
                    note = _strip_transfer_note(item["name"])
                    cells = [item["cell"], partner["cell"]]
                    transactions.append({
                        "id": f"sh:{tab}:{item['cell']['range']}+{partner['cell']['range']}",
                        "type": "transfer",
                        "amount": abs(amt),
                        "title": note or f"Setor Tunai → {to_bank}",
                        "edit_note": note,
                        "category": "",
                        "wallet": "Tunai",
                        "to_wallet": to_bank,
                        "date": item["date"],
                        "source": "sheets",
                        "sheet_row": item["row"],
                        "sheet_cells": cells,
                    })
                else:
                    note = _strip_transfer_note(item["name"])
                    transactions.append({
                        "id": f"sh:{tab}:{item['cell']['range']}",
                        "type": "transfer",
                        "amount": abs(amt),
                        "title": item["name"],
                        "edit_note": note,
                        "category": "",
                        "wallet": "Tunai",
                        "to_wallet": "BNI",
                        "date": item["date"],
                        "source": "sheets",
                        "sheet_row": item["row"],
                        "sheet_cells": [item["cell"]],
                    })
            else:
                if amt > 0:
                    fallback_income += amt
                transactions.append({
                    "id": f"sh:{tab}:{item['cell']['range']}",
                    "type": "income",
                    "amount": abs(amt),
                    "title": item["name"] or "Tunai",
                    "category": "",
                    "wallet": "Tunai",
                    "to_wallet": "",
                    "date": item["date"],
                    "source": "sheets",
                    "sheet_row": item["row"],
                    "sheet_cells": [item["cell"]],
                })

        # Process Pemasukan Bank (T10:V31 or legacy S9:U31) — pair bank-to-bank transfers first
        for b in bank_raw:
            if b["row"] in used_bank_rows:
                continue
            if "(tf keluar" in b["low"] and b["amount"] < 0:
                partner = None
                for p in bank_raw:
                    if (p["row"] not in used_bank_rows and p["row"] != b["row"]
                            and "(tf masuk" in p["low"]
                            and p["amount"] == -b["amount"]
                            and p["date"] == b["date"]):
                        partner = p
                        break
                if partner is not None:
                    used_bank_rows.add(b["row"])
                    used_bank_rows.add(partner["row"])
                    from_bank = _extract_bank_from_label(b["name"])
                    to_bank = _extract_bank_from_label(partner["name"])
                    note = _strip_transfer_note(b["name"])
                    transactions.append({
                        "id": f"sh:{tab}:{b['cell']['range']}+{partner['cell']['range']}",
                        "type": "transfer",
                        "amount": abs(b["amount"]),
                        "title": note or f"{from_bank} → {to_bank}",
                        "edit_note": note,
                        "category": "",
                        "wallet": from_bank,
                        "to_wallet": to_bank,
                        "date": b["date"],
                        "source": "sheets",
                        "sheet_row": b["row"],
                        "sheet_cells": [b["cell"], partner["cell"]],
                    })
                    continue
            used_bank_rows.add(b["row"])
            bank = _extract_bank_from_label(b["name"])
            if b["amount"] > 0:
                fallback_income += b["amount"]
            transactions.append({
                "id": f"sh:{tab}:{b['cell']['range']}",
                "type": "income",
                "amount": abs(b["amount"]),
                "title": b["name"] or bank,
                "category": "",
                "wallet": bank,
                "to_wallet": "",
                "date": b["date"],
                "source": "sheets",
                "sheet_row": b["row"],
                "sheet_cells": [b["cell"]],
            })

        transactions.sort(key=lambda t: (t.get("date", ""), int(t.get("sheet_row", 0))), reverse=True)

        month_income = int(round(hasil["pemasukan"])) if "pemasukan" in hasil else fallback_income
        month_expense = int(round(hasil["pengeluaran"])) if "pengeluaran" in hasil else sum(by_category.values())
        summary = {
            "income": month_income,
            "expense": month_expense,
            "net": month_income - month_expense,
            "today_expense": today_expense,
            "count": len(transactions),
            "by_category": by_category,
        }
        return FinanceSheetMonthData(
            ok=True,
            tab=tab,
            year=int(year),
            month=int(month),
            balances=balances,
            summary=summary,
            transactions=transactions,
            synced_at=datetime.now().strftime("%H:%M"),
            available_tabs=avail_tabs,
        )
    except HttpError as exc:
        return FinanceSheetMonthData(
            ok=False, tab=tab, year=int(year), month=int(month),
            available_tabs=avail_tabs,
            error=_friendly_finance_http_error(exc, client_email, tab, avail=avail_tabs),
        )
    except TimeoutError:
        return FinanceSheetMonthData(
            ok=False, tab=tab, year=int(year), month=int(month),
            error="Google Sheets request timed out.",
        )
    except Exception as exc:
        return FinanceSheetMonthData(
            ok=False, tab=tab, year=int(year), month=int(month),
            error=f"Failed to read Google Sheets ({type(exc).__name__}).",
        )
    finally:
        _close_service(service)


def _write_finance_with_service(
    service,
    sheet_id: str,
    tx_type: str,
    amount: int,
    title: str,
    category: str,
    wallet: str,
    to_wallet: str,
    dt: date,
    tab_override: str | None = None,
) -> FinanceSheetWriteResult:
    """Core bot-wa Monthly Budget writer operating on an open Google Sheets service."""
    tab = tab_override or month_tab_name(dt.month)
    date_str = dt.isoformat()
    amt = int(amount)
    if amt <= 0:
        return FinanceSheetWriteResult(ok=False, tab=tab, error="Amount must be greater than zero.")

    clean_title = str(title or "").strip()[:100]
    tx_t = str(tx_type or "expense").strip().lower()
    wal = str(wallet or "Tunai").strip()
    to_wal = str(to_wallet or "").strip()
    cat = category if category in FINANCE_SHEET_CAT_COLS else "Jajan"

    # Bank keyword guard (protects Monthly_Budget SUMIF("*BNI*") / SUMIF("*Mandiri*") formulas)
    if tx_t == "income":
        allowed = wal if wal in FINANCE_BANK_WORDS else None
        bad = forbidden_bank_word(clean_title, allowed_bank=allowed)
        if bad:
            if allowed:
                return FinanceSheetWriteResult(
                    ok=False, tab=tab,
                    error=f"Note cannot contain '{bad}' for a {allowed} deposit (would distort bank formulas).",
                )
            return FinanceSheetWriteResult(
                ok=False, tab=tab,
                error=f"Cash income cannot contain '{bad}' (would be counted as bank income in Sheets).",
            )
    elif tx_t == "transfer":
        bad = forbidden_bank_word(clean_title, allowed_bank=None)
        if bad:
            return FinanceSheetWriteResult(
                ok=False, tab=tab,
                error=f"Transfer note cannot contain '{bad}' (would distort bank formulas in Sheets).",
            )

    cells: list[dict] = []

    if tx_t == "expense":
        col_a, col_c = FINANCE_SHEET_CAT_COLS[cat]
        name = clean_title or cat
        exp_col_rng = _a1_range(tab, f"{col_a}{FINANCE_EXPENSE_ROW_START}:{col_a}")

        if wal in QRIS_SOURCE_BY_BANK:
            # Exact bot-wa /qris flow: pre-check BOTH Pemasukan (B10:D31) and Expense table before writing!
            sumber_qris = QRIS_SOURCE_BY_BANK[wal]
            inc_rng = _a1_range(tab, f"B{FINANCE_INCOME_ROW_START}:D{FINANCE_INCOME_ROW_END}")
            resp = service.spreadsheets().values().batchGet(
                spreadsheetId=sheet_id,
                ranges=[inc_rng, exp_col_rng],
                valueRenderOption="UNFORMATTED_VALUE",
            ).execute()
            vrs = resp.get("valueRanges") or []
            inc_rows = (vrs[0].get("values") or []) if len(vrs) > 0 else []
            exp_col = (vrs[1].get("values") or []) if len(vrs) > 1 else []

            erows = _find_empty_rows(exp_col, FINANCE_EXPENSE_ROW_START, FINANCE_EXPENSE_ROW_END, count=1)
            if not erows:
                return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Table '{cat}' in '{tab}' is full.")
            erow = erows[0]

            # Look for existing QRIS BNI / QRIS Mandiri row
            existing_qris = None
            inc_col_b: list[list] = []
            for idx, r in enumerate(inc_rows):
                col0 = r[0] if len(r) > 0 else ""
                inc_col_b.append([col0])
                if (existing_qris is None and len(r) >= 3
                        and str(r[0]).strip().lower() == sumber_qris.lower()
                        and _is_number(r[2])):
                    old_val = int(round(float(r[2])))
                    existing_qris = (FINANCE_INCOME_ROW_START + idx, str(r[0]).strip(), old_val)

            if existing_qris is None:
                qrows = _find_empty_rows(inc_col_b, FINANCE_INCOME_ROW_START, FINANCE_INCOME_ROW_END, count=1)
                if not qrows:
                    return FinanceSheetWriteResult(
                        ok=False, tab=tab,
                        error=f"Pemasukan table (B10:D31) in '{tab}' is full — cannot create '{sumber_qris}' row.",
                    )
                qrow = qrows[0]
                qrange = f"B{qrow}:D{qrow}"
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(tab, qrange),
                    valueInputOption="USER_ENTERED",
                    body={"values": [[safe_sheet_text(sumber_qris), date_str, amt]]},
                ).execute()
                cells.append({"tab": tab, "range": qrange, "name": sumber_qris, "amount": amt})
            else:
                qrow, qname, old_val = existing_qris
                new_val = old_val + amt
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(tab, f"D{qrow}"),
                    valueInputOption="USER_ENTERED",
                    body={"values": [[new_val]]},
                ).execute()
                cells.append({
                    "tab": tab,
                    "range": f"B{qrow}:D{qrow}",
                    "name": qname,
                    "amount": new_val,
                    "prev_amount": old_val,
                    "kind": "increment",
                })

            erange = f"{col_a}{erow}:{col_c}{erow}"
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=_a1_range(tab, erange),
                valueInputOption="USER_ENTERED",
                body={"values": [[safe_sheet_text(name), date_str, amt]]},
            ).execute()
            cells.append({"tab": tab, "range": erange, "name": name, "amount": amt})
            return FinanceSheetWriteResult(
                ok=True, tab=tab, cells=cells,
                message=f"Saved QRIS ({wal}) '{name}' to {tab} · {cat} row {erow}",
            )

        # Standard Tunai expense
        resp = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=exp_col_rng,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        erows = _find_empty_rows(resp.get("values") or [], FINANCE_EXPENSE_ROW_START, FINANCE_EXPENSE_ROW_END, count=1)
        if not erows:
            return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Table '{cat}' in '{tab}' is full.")
        erow = erows[0]
        erange = f"{col_a}{erow}:{col_c}{erow}"
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=_a1_range(tab, erange),
            valueInputOption="USER_ENTERED",
            body={"values": [[safe_sheet_text(name), date_str, amt]]},
        ).execute()
        cells.append({"tab": tab, "range": erange, "name": name, "amount": amt})
        return FinanceSheetWriteResult(
            ok=True, tab=tab, cells=cells,
            message=f"Saved '{name}' to {tab} · {cat} row {erow}",
        )

    if tx_t == "income":
        if wal in FINANCE_BANK_WORDS:
            label = _format_bank_income_label(wal, clean_title)
            col_rng = _a1_range(tab, f"T{FINANCE_BANK_ROW_START}:T{FINANCE_BANK_ROW_END}")
            resp = service.spreadsheets().values().get(
                spreadsheetId=sheet_id, range=col_rng, valueRenderOption="UNFORMATTED_VALUE",
            ).execute()
            rows = _find_empty_rows(resp.get("values") or [], FINANCE_BANK_ROW_START, FINANCE_BANK_ROW_END, count=1)
            if not rows:
                return FinanceSheetWriteResult(
                    ok=False, tab=tab,
                    error=f"Pemasukan Bank table (T10:V31) in '{tab}' is full.",
                )
            rnum = rows[0]
            rng = f"T{rnum}:V{rnum}"
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=_a1_range(tab, rng),
                valueInputOption="USER_ENTERED",
                body={"values": [[safe_sheet_text(label), date_str, amt]]},
            ).execute()
            cells.append({"tab": tab, "range": rng, "name": label, "amount": amt})
            return FinanceSheetWriteResult(
                ok=True, tab=tab, cells=cells,
                message=f"Saved bank income '{label}' to {tab} row {rnum}",
            )

        label = clean_title or "Pemasukan"
        col_rng = _a1_range(tab, f"B{FINANCE_INCOME_ROW_START}:B{FINANCE_INCOME_ROW_END}")
        resp = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=col_rng, valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        rows = _find_empty_rows(resp.get("values") or [], FINANCE_INCOME_ROW_START, FINANCE_INCOME_ROW_END, count=1)
        if not rows:
            return FinanceSheetWriteResult(
                ok=False, tab=tab,
                error=f"Pemasukan table (B10:D31) in '{tab}' is full.",
            )
        rnum = rows[0]
        rng = f"B{rnum}:D{rnum}"
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=_a1_range(tab, rng),
            valueInputOption="USER_ENTERED",
            body={"values": [[safe_sheet_text(label), date_str, amt]]},
        ).execute()
        cells.append({"tab": tab, "range": rng, "name": label, "amount": amt})
        return FinanceSheetWriteResult(
            ok=True, tab=tab, cells=cells,
            message=f"Saved cash income '{label}' to {tab} row {rnum}",
        )

    if tx_t == "transfer":
        if wal == to_wal or wal not in ("Tunai", "BNI", "Mandiri") or to_wal not in ("Tunai", "BNI", "Mandiri"):
            return FinanceSheetWriteResult(ok=False, tab=tab, error="Choose two different wallets for a transfer.")
        extra = f" - {clean_title}" if clean_title else ""

        if wal in FINANCE_BANK_WORDS and to_wal == "Tunai":
            label = f"Tarik Uang {wal}{extra}"
            col_rng = _a1_range(tab, f"B{FINANCE_INCOME_ROW_START}:B{FINANCE_INCOME_ROW_END}")
            resp = service.spreadsheets().values().get(
                spreadsheetId=sheet_id, range=col_rng, valueRenderOption="UNFORMATTED_VALUE",
            ).execute()
            rows = _find_empty_rows(resp.get("values") or [], FINANCE_INCOME_ROW_START, FINANCE_INCOME_ROW_END, count=1)
            if not rows:
                return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Pemasukan table (B10:D31) in '{tab}' is full.")
            rnum = rows[0]
            rng = f"B{rnum}:D{rnum}"
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=_a1_range(tab, rng),
                valueInputOption="USER_ENTERED",
                body={"values": [[safe_sheet_text(label), date_str, amt]]},
            ).execute()
            cells.append({"tab": tab, "range": rng, "name": label, "amount": amt})
            return FinanceSheetWriteResult(ok=True, tab=tab, cells=cells, message=f"Saved transfer {wal} → Tunai to {tab}")

        if wal == "Tunai" and to_wal in FINANCE_BANK_WORDS:
            cash_rng = _a1_range(tab, f"B{FINANCE_INCOME_ROW_START}:B{FINANCE_INCOME_ROW_END}")
            bank_rng = _a1_range(tab, f"T{FINANCE_BANK_ROW_START}:T{FINANCE_BANK_ROW_END}")
            resp = service.spreadsheets().values().batchGet(
                spreadsheetId=sheet_id, ranges=[cash_rng, bank_rng], valueRenderOption="UNFORMATTED_VALUE",
            ).execute()
            vrs = resp.get("valueRanges") or []
            cash_rows = _find_empty_rows(
                (vrs[0].get("values") or []) if len(vrs) > 0 else [],
                FINANCE_INCOME_ROW_START, FINANCE_INCOME_ROW_END, count=1,
            )
            bank_rows = _find_empty_rows(
                (vrs[1].get("values") or []) if len(vrs) > 1 else [],
                FINANCE_BANK_ROW_START, FINANCE_BANK_ROW_END, count=1,
            )
            if not cash_rows:
                return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Pemasukan table (B10:D31) in '{tab}' is full.")
            if not bank_rows:
                return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Pemasukan Bank table (T10:V31) in '{tab}' is full.")
            lbl_cash = f"Setor Tunai{extra}"
            lbl_bank = f"{to_wal} (setor tunai{extra})"
            rng_c = f"B{cash_rows[0]}:D{cash_rows[0]}"
            rng_b = f"T{bank_rows[0]}:V{bank_rows[0]}"
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body={
                    "valueInputOption": "USER_ENTERED",
                    "data": [
                        {"range": _a1_range(tab, rng_c), "values": [[safe_sheet_text(lbl_cash), date_str, -amt]]},
                        {"range": _a1_range(tab, rng_b), "values": [[safe_sheet_text(lbl_bank), date_str, amt]]},
                    ],
                },
            ).execute()
            cells.append({"tab": tab, "range": rng_c, "name": lbl_cash, "amount": -amt})
            cells.append({"tab": tab, "range": rng_b, "name": lbl_bank, "amount": amt})
            return FinanceSheetWriteResult(ok=True, tab=tab, cells=cells, message=f"Saved transfer Tunai → {to_wal} to {tab}")

        # Bank <-> Bank transfer (2 rows in T10:V31)
        bank_rng = _a1_range(tab, f"T{FINANCE_BANK_ROW_START}:T{FINANCE_BANK_ROW_END}")
        resp = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=bank_rng, valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        rows = _find_empty_rows(resp.get("values") or [], FINANCE_BANK_ROW_START, FINANCE_BANK_ROW_END, count=2)
        if len(rows) < 2:
            return FinanceSheetWriteResult(
                ok=False, tab=tab,
                error=f"Pemasukan Bank table (T10:V31) in '{tab}' needs 2 free rows for bank transfer.",
            )
        lbl_out = f"{wal} (tf keluar{extra})"
        lbl_in = f"{to_wal} (tf masuk{extra})"
        rng_out = f"T{rows[0]}:V{rows[0]}"
        rng_in = f"T{rows[1]}:V{rows[1]}"
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": _a1_range(tab, rng_out), "values": [[safe_sheet_text(lbl_out), date_str, -amt]]},
                    {"range": _a1_range(tab, rng_in), "values": [[safe_sheet_text(lbl_in), date_str, amt]]},
                ],
            },
        ).execute()
        cells.append({"tab": tab, "range": rng_out, "name": lbl_out, "amount": -amt})
        cells.append({"tab": tab, "range": rng_in, "name": lbl_in, "amount": amt})
        return FinanceSheetWriteResult(ok=True, tab=tab, cells=cells, message=f"Saved transfer {wal} → {to_wal} to {tab}")

    return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Unsupported transaction type: {tx_t}")


def write_finance_tx_to_sheets(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    tx_type: str,
    amount: int,
    title: str,
    category: str = "Jajan",
    wallet: str = "Tunai",
    to_wallet: str = "",
    day: str | date | None = None,
    timeout: float = 12.0,
) -> FinanceSheetWriteResult:
    """Write a finance transaction to Google Sheets using exact `bot-wa` Monthly Budget rules."""
    dt = _parse_tx_date(day)
    tab = month_tab_name(dt.month)
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return FinanceSheetWriteResult(ok=False, tab=tab, error="Spreadsheet ID is not configured.")
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return FinanceSheetWriteResult(ok=False, tab=tab, error="Service Account JSON key file not found.")
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return FinanceSheetWriteResult(ok=False, tab=tab, error=err)

    with _FINANCE_WRITE_LOCK:
        avail_tabs: list[str] = []
        try:
            try:
                return _write_finance_with_service(
                    service, sheet_id, tx_type, amount, title, category, wallet, to_wallet, dt,
                )
            except HttpError as exc:
                if _is_missing_tab_error(exc):
                    matched, avail_tabs = _resolve_finance_tab_or_list(service, sheet_id, dt.month)
                    if matched is not None and matched != tab:
                        tab = matched
                        return _write_finance_with_service(
                            service, sheet_id, tx_type, amount, title, category, wallet, to_wallet, dt,
                            tab_override=matched,
                        )
                raise
        except HttpError as exc:
            return FinanceSheetWriteResult(
                ok=False, tab=tab, error=_friendly_finance_http_error(exc, client_email, tab, avail=avail_tabs),
            )
        except TimeoutError:
            return FinanceSheetWriteResult(ok=False, tab=tab, error="Google Sheets write timed out.")
        except Exception as exc:
            return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Google Sheets write failed ({type(exc).__name__}).")
        finally:
            _close_service(service)


def _verify_cells_match(service, sheet_id: str, cells: list[dict]) -> tuple[bool, list[dict]]:
    """Verify all target sheet cells are either empty or still match expected (name, amount).

    Returns `(safe, non_empty_cells)` matching `bot-wa`'s `batalkan_terakhir` protection.
    """
    if not cells:
        return False, []
    ranges = [_a1_range(str(c["tab"]), str(c["range"])) for c in cells]
    resp = service.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id,
        ranges=ranges,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    vrs = resp.get("valueRanges") or []
    active: list[dict] = []
    for i, c in enumerate(cells):
        vals = (vrs[i].get("values") or []) if i < len(vrs) else []
        if not any(str(x).strip() for row in vals for x in row):
            continue  # Already cleared manually
        row = list(vals[0]) if vals else []
        if len(row) < 3 or not _is_number(row[2]):
            return False, []
        cur_name = str(row[0]).strip().lstrip("'").lower()
        exp_name = str(c.get("name") or "").strip().lstrip("'").lower()
        if cur_name != exp_name or float(row[2]) != float(c["amount"]):
            return False, []
        active.append(c)
    return True, active


def _clear_cells_with_service(service, sheet_id: str, cells: list[dict]) -> FinanceSheetWriteResult:
    tab = str(cells[0].get("tab") or "") if cells else ""
    safe, active = _verify_cells_match(service, sheet_id, cells)
    if not safe:
        return FinanceSheetWriteResult(
            ok=False, tab=tab,
            error="Cell was modified in Google Sheets — skipped to protect your manual edits.",
        )
    to_clear: list[str] = []
    to_restore: list[dict] = []
    for c in active:
        rng_full = _a1_range(str(c["tab"]), str(c["range"]))
        if c.get("prev_amount") is not None:
            amt_cell = str(c["range"]).split(":")[-1]
            to_restore.append({"range": _a1_range(str(c["tab"]), amt_cell), "values": [[c["prev_amount"]]]})
        else:
            to_clear.append(rng_full)
    if to_clear:
        service.spreadsheets().values().batchClear(
            spreadsheetId=sheet_id,
            body={"ranges": to_clear},
        ).execute()
    if to_restore:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": to_restore},
        ).execute()
    return FinanceSheetWriteResult(ok=True, tab=tab, cells=cells, message="Reverted cells in Google Sheets.")


def clear_finance_cells_in_sheets(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    cells: list[dict],
    timeout: float = 12.0,
) -> FinanceSheetWriteResult:
    """Safely clear or revert recorded finance cells in Google Sheets (bot-wa /undo safety)."""
    tab = str(cells[0].get("tab") or "") if cells else ""
    if not cells:
        return FinanceSheetWriteResult(ok=False, tab=tab, error="No Google Sheets cell trace to revert.")
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return FinanceSheetWriteResult(ok=False, tab=tab, error="Spreadsheet ID is not configured.")
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return FinanceSheetWriteResult(ok=False, tab=tab, error="Service Account JSON key file not found.")
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return FinanceSheetWriteResult(ok=False, tab=tab, error=err)

    with _FINANCE_WRITE_LOCK:
        try:
            return _clear_cells_with_service(service, sheet_id, cells)
        except HttpError as exc:
            return FinanceSheetWriteResult(ok=False, tab=tab, error=_friendly_finance_http_error(exc, client_email, tab))
        except TimeoutError:
            return FinanceSheetWriteResult(ok=False, tab=tab, error="Google Sheets request timed out.")
        except Exception as exc:
            return FinanceSheetWriteResult(ok=False, tab=tab, error=f"Google Sheets delete failed ({type(exc).__name__}).")
        finally:
            _close_service(service)


def update_finance_tx_in_sheets(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    old_tx: dict,
    tx_type: str,
    amount: int,
    title: str,
    category: str = "Jajan",
    wallet: str = "Tunai",
    to_wallet: str = "",
    day: str | date | None = None,
    timeout: float = 12.0,
) -> FinanceSheetWriteResult:
    """Safely edit a Sheet-backed transaction (in-place when same tab/table, else write-then-clear)."""
    if str(old_tx.get("sheet_kind") or "") == "qris_pool":
        return FinanceSheetWriteResult(
            ok=False, error="QRIS monthly pool rows are updated automatically by QRIS expenses.",
        )
    old_cells = list(old_tx.get("sheet_cells") or [])
    if not old_cells:
        return FinanceSheetWriteResult(ok=False, error="Selected transaction is not linked to Google Sheets cells.")

    dt = _parse_tx_date(day)
    new_tab = month_tab_name(dt.month)
    old_tab = str(old_cells[0].get("tab") or "")
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return FinanceSheetWriteResult(ok=False, tab=new_tab, error="Spreadsheet ID is not configured.")
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return FinanceSheetWriteResult(ok=False, tab=new_tab, error="Service Account JSON key file not found.")
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return FinanceSheetWriteResult(ok=False, tab=new_tab, error=err)

    with _FINANCE_WRITE_LOCK:
        try:
            safe, active = _verify_cells_match(service, sheet_id, old_cells)
            if not safe or not active:
                return FinanceSheetWriteResult(
                    ok=False, tab=old_tab,
                    error="Row changed in Google Sheets — refresh before editing.",
                )

            amt = int(amount)
            if amt <= 0:
                return FinanceSheetWriteResult(ok=False, tab=new_tab, error="Amount must be greater than zero.")
            clean_title = str(title or "").strip()[:100]
            date_str = dt.isoformat()

            # Fast in-place update for single-row Expense in same tab & category (without new QRIS charge)
            if (len(old_cells) == 1 and old_tx.get("type") == "expense" and tx_type == "expense"
                    and old_tab == new_tab and old_tx.get("category") == category
                    and wallet not in QRIS_SOURCE_BY_BANK):
                rng = str(old_cells[0]["range"])
                name = clean_title or category
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(new_tab, rng),
                    valueInputOption="USER_ENTERED",
                    body={"values": [[safe_sheet_text(name), date_str, amt]]},
                ).execute()
                return FinanceSheetWriteResult(
                    ok=True, tab=new_tab,
                    cells=[{"tab": new_tab, "range": rng, "name": name, "amount": amt}],
                    message=f"Updated '{name}' in {new_tab} ({rng})",
                )

            # Fast in-place update for single-row Income in same tab & same table (Tunai vs Bank)
            old_is_bank = old_tx.get("wallet") in FINANCE_BANK_WORDS
            new_is_bank = wallet in FINANCE_BANK_WORDS
            if (len(old_cells) == 1 and old_tx.get("type") == "income" and tx_type == "income"
                    and old_tab == new_tab and old_is_bank == new_is_bank):
                allowed = wallet if new_is_bank else None
                bad = forbidden_bank_word(clean_title, allowed_bank=allowed)
                if bad:
                    return FinanceSheetWriteResult(
                        ok=False, tab=new_tab,
                        error=f"Label cannot contain '{bad}' for this wallet (protects Sheet bank formulas).",
                    )
                label = _format_bank_income_label(wallet, clean_title) if new_is_bank else (clean_title or "Pemasukan")
                rng = str(old_cells[0]["range"])
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(new_tab, rng),
                    valueInputOption="USER_ENTERED",
                    body={"values": [[safe_sheet_text(label), date_str, amt]]},
                ).execute()
                return FinanceSheetWriteResult(
                    ok=True, tab=new_tab,
                    cells=[{"tab": new_tab, "range": rng, "name": label, "amount": amt}],
                    message=f"Updated '{label}' in {new_tab} ({rng})",
                )

            # Cross-table / cross-tab / type-change edit: write new FIRST, then clear old so data is never lost
            written = _write_finance_with_service(
                service, sheet_id, tx_type, amt, clean_title, category, wallet, to_wallet, dt,
            )
            if not written.ok:
                return written
            _clear_cells_with_service(service, sheet_id, old_cells)
            return FinanceSheetWriteResult(
                ok=True, tab=written.tab, cells=written.cells,
                message=f"Moved & updated transaction in {written.tab}",
            )
        except HttpError as exc:
            return FinanceSheetWriteResult(
                ok=False, tab=new_tab, error=_friendly_finance_http_error(exc, client_email, new_tab),
            )
        except TimeoutError:
            return FinanceSheetWriteResult(ok=False, tab=new_tab, error="Google Sheets update timed out.")
        except Exception as exc:
            return FinanceSheetWriteResult(ok=False, tab=new_tab, error=f"Google Sheets update failed ({type(exc).__name__}).")
        finally:
            _close_service(service)


# ---------------------------------------------------------------------------
# 2-Way Companion JSON Sync (`Morgana_Sync` tab — Tasks, Notes, Stats, Events, Habits)
# ---------------------------------------------------------------------------
_COMPANION_SYNC_LOCK = threading.RLock()


@dataclass
class SheetsCompanionSyncResult:
    ok: bool
    merged: dict[str, tuple[list[dict], dict[str, str]]] = field(default_factory=dict)
    local_changed: set[str] = field(default_factory=set)
    remote_changed: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    deadlines_pushed: int = 0
    synced_at: str = ""
    error: str = ""


def parse_sync_ts_ms(value: str | None) -> int:
    """Parse naive local or timezone-aware ISO-8601 string into UTC epoch milliseconds (0 if invalid)."""
    raw = str(value or "").strip()
    if not raw:
        return 0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return int(round(dt.timestamp() * 1000))
    except (TypeError, ValueError, OSError):
        return 0


def item_timestamp_ms(item: dict) -> int:
    """Return latest modification timestamp (epoch ms) across common Morgana JSON timestamp fields."""
    if not isinstance(item, dict):
        return 0
    best = 0
    for key in ("updated_at", "updated", "done_at", "created"):
        ts = parse_sync_ts_ms(item.get(key))
        if ts > best:
            best = ts
    return best


def normalize_sync_item(store_key: str, item: dict) -> dict:
    """Normalize store item for seamless Desktop <-> Android JSON parity."""
    copy = dict(item)
    if store_key == "habits":
        raw_dates = copy.get("done_dates")
        if not isinstance(raw_dates, list):
            raw_dates = copy.get("dates") if isinstance(copy.get("dates"), list) else []
        cleaned = sorted({str(d).strip() for d in raw_dates if str(d).strip()})
        copy["done_dates"] = cleaned
        copy["dates"] = list(cleaned)
        if not isinstance(copy.get("log_ids"), dict):
            copy["log_ids"] = {}
    elif store_key == "notes":
        if not isinstance(copy.get("tags"), list):
            copy["tags"] = []
    return copy


def _canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def merge_store_payload(
    store_key: str,
    local_items: list[dict],
    local_tombstones: dict[str, str],
    remote_items: list[dict],
    remote_tombstones: dict[str, str],
    *,
    now_ms: int | None = None,
) -> tuple[list[dict], dict[str, str], bool, bool]:
    """Deterministic Last-Write-Wins (LWW) + Tombstone 2-way merge for a single Morgana JSON store.

    Returns `(merged_items, merged_tombstones, local_changed, remote_changed)`.
    """
    ref_ms = int(now_ms if now_ms is not None else round(datetime.now().astimezone().timestamp() * 1000))
    cutoff_ms = ref_ms - SYNC_TOMBSTONE_MAX_AGE_MS

    norm_local = [normalize_sync_item(store_key, i) for i in (local_items or ()) if isinstance(i, dict) and i.get("id")]
    norm_remote = [normalize_sync_item(store_key, i) for i in (remote_items or ()) if isinstance(i, dict) and i.get("id")]

    loc_tomb = {str(k).strip(): str(v).strip() for k, v in (local_tombstones or {}).items() if str(k).strip() and str(v).strip()}
    rem_tomb = {str(k).strip(): str(v).strip() for k, v in (remote_tombstones or {}).items() if str(k).strip() and str(v).strip()}

    # 1. Merge tombstones (keep newest deletion per ID, prune >90d expired entries)
    merged_tomb: dict[str, str] = {}
    for tid in set(loc_tomb.keys()) | set(rem_tomb.keys()):
        lt = loc_tomb.get(tid, "")
        rt = rem_tomb.get(tid, "")
        lms = parse_sync_ts_ms(lt)
        rms = parse_sync_ts_ms(rt)
        best_str, best_ms = (rt, rms) if rms >= lms and rt else (lt, lms)
        if best_str and (best_ms == 0 or best_ms >= cutoff_ms):
            merged_tomb[tid] = best_str

    # 2. Index items preserving order (local order first, then newly arrived remote items)
    loc_by_id: dict[str, dict] = {str(i["id"]): i for i in norm_local}
    rem_by_id: dict[str, dict] = {str(i["id"]): i for i in norm_remote}
    ordered_ids: list[str] = []
    seen_ids: set[str] = set()
    for src in (norm_local, norm_remote):
        for it in src:
            iid = str(it["id"])
            if iid not in seen_ids:
                seen_ids.add(iid)
                ordered_ids.append(iid)

    # 3. Resolve each item by LWW timestamp vs tombstone timestamp
    merged_items: list[dict] = []
    for iid in ordered_ids:
        loc_it = loc_by_id.get(iid)
        rem_it = rem_by_id.get(iid)
        if loc_it is not None and rem_it is not None:
            l_ms = item_timestamp_ms(loc_it)
            r_ms = item_timestamp_ms(rem_it)
            if r_ms > l_ms:
                winner = rem_it
                win_ms = r_ms
            elif l_ms > r_ms:
                winner = loc_it
                win_ms = l_ms
            else:
                # Deterministic tie-breaker so Laptop & Android always converge identically
                winner = rem_it if _canonical_json(rem_it) >= _canonical_json(loc_it) else loc_it
                win_ms = l_ms
        elif loc_it is not None:
            winner = loc_it
            win_ms = item_timestamp_ms(loc_it)
        else:
            winner = rem_it  # type: ignore[assignment]
            win_ms = item_timestamp_ms(winner)

        tomb_str = merged_tomb.get(iid)
        if tomb_str:
            tomb_ms = parse_sync_ts_ms(tomb_str)
            if tomb_ms >= win_ms:
                # Deletion is newer than or equal to item -> keep deleted
                continue
            # Item was restored/updated after deletion -> clear stale tombstone
            merged_tomb.pop(iid, None)
        merged_items.append(normalize_sync_item(store_key, winner))

    local_changed = (
        _canonical_json(merged_items) != _canonical_json(norm_local)
        or _canonical_json(merged_tomb) != _canonical_json(loc_tomb)
    )
    remote_changed = (
        _canonical_json(merged_items) != _canonical_json(norm_remote)
        or _canonical_json(merged_tomb) != _canonical_json(rem_tomb)
    )
    return merged_items, merged_tomb, local_changed, remote_changed


def encode_store_row(
    store_key: str,
    items: list[dict],
    tombstones: dict[str, str],
    updated_iso: str,
) -> list[str]:
    """Encode a store snapshot into a Google Sheets row split into <=45,000-char cell chunks."""
    blob = json.dumps(
        {"v": 2, "items": items, "tombstones": tombstones},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    chunks = [blob[i:i + SYNC_CHUNK_SIZE] for i in range(0, len(blob), SYNC_CHUNK_SIZE)] or ["{}"]
    return [store_key, updated_iso, str(len(items)), *chunks]


def decode_store_rows(rows: list[list]) -> dict[str, tuple[list[dict], dict[str, str]]]:
    """Decode `Morgana_Sync` sheet rows -> `{store_key: (items, tombstones)}`."""
    parsed: dict[str, tuple[list[dict], dict[str, str]]] = {}
    for row in rows or ():
        if not row or len(row) < 4:
            continue
        key = str(row[0] or "").strip().lower()
        if key not in COMPANION_SYNC_STORES:
            continue
        blob = "".join(str(cell) for cell in row[3:] if cell is not None and str(cell) != "")
        if not blob.strip():
            continue
        try:
            obj = json.loads(blob)
        except ValueError:
            continue
        if isinstance(obj, dict):
            raw_items = obj.get("items") or []
            raw_tomb = obj.get("tombstones") or {}
            items = [i for i in raw_items if isinstance(i, dict) and i.get("id")]
            tomb = {
                str(k).strip(): str(v).strip()
                for k, v in raw_tomb.items()
                if str(k).strip() and str(v).strip()
            } if isinstance(raw_tomb, dict) else {}
            parsed[key] = (items, tomb)
        elif isinstance(obj, list):
            parsed[key] = ([i for i in obj if isinstance(i, dict) and i.get("id")], {})
    return parsed


def _write_deadlines_with_service(service, sheet_id: str, tab: str, tasks: list[dict], HttpError) -> int:
    rows = serialize_tasks_to_rows(tasks)
    clear_rng = _a1_range(tab, f"A1:F{MAX_SYNC_TASKS + 20}")
    write_rng = _a1_range(tab, "A1")

    def _do_write() -> None:
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=clear_rng,
            body={},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=write_rng,
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

    try:
        _do_write()
    except HttpError as exc:
        if _is_missing_tab_error(exc):
            _create_tab(service, sheet_id, tab, HttpError)
            _do_write()
        else:
            raise
    return max(0, len(rows) - 1)


def sync_companion_data_with_sheets(
    cfg: SheetsSyncConfig,
    base_dir: Path,
    local_stores: dict[str, tuple[list[dict], dict[str, str]]],
    *,
    push_deadlines: bool = True,
    force_deadlines: bool = False,
    timeout: float = 15.0,
) -> SheetsCompanionSyncResult:
    """2-way Google Sheets sync for Tasks, Notes, Social Stats, Local Events, and Habits.

    Uses a single dedicated `Morgana_Sync` tab (1 read when idle, 1 write only when changed)
    while keeping the `Morgana` WhatsApp deadline relay tab up to date automatically.
    """
    sheet_id = cfg.normalized_sheet_id()
    if not sheet_id:
        return SheetsCompanionSyncResult(ok=False, error="Spreadsheet ID is not configured.")
    key_file = cfg.resolve_key_file(base_dir)
    if key_file is None:
        return SheetsCompanionSyncResult(ok=False, error="Service Account JSON key file not found.")
    service, client_email, HttpError, err = _build_service(key_file, timeout=timeout)
    if err or service is None:
        return SheetsCompanionSyncResult(ok=False, error=err)

    sync_rng = _a1_range(COMPANION_SYNC_TAB, "A1:Z20")
    now_dt = datetime.now().astimezone()
    now_iso_str = now_dt.isoformat(timespec="seconds")
    now_ms = int(round(now_dt.timestamp() * 1000))

    with _COMPANION_SYNC_LOCK:
        try:
            remote_missing = False
            try:
                resp = service.spreadsheets().values().get(
                    spreadsheetId=sheet_id,
                    range=sync_rng,
                    valueRenderOption="UNFORMATTED_VALUE",
                ).execute()
                remote_rows = resp.get("values") or []
            except HttpError as exc:
                if _is_missing_tab_error(exc):
                    _create_tab(service, sheet_id, COMPANION_SYNC_TAB, HttpError)
                    remote_rows = []
                    remote_missing = True
                else:
                    raise

            remote_map = decode_store_rows(remote_rows)
            merged_map: dict[str, tuple[list[dict], dict[str, str]]] = {}
            local_changed: set[str] = set()
            remote_changed: set[str] = set()
            counts: dict[str, int] = {}

            for store_key in COMPANION_SYNC_STORES:
                loc_items, loc_tomb = local_stores.get(store_key, ([], {}))
                has_remote = store_key in remote_map
                rem_items, rem_tomb = remote_map.get(store_key, ([], {}))
                m_items, m_tomb, l_chg, r_chg = merge_store_payload(
                    store_key, loc_items, loc_tomb, rem_items, rem_tomb, now_ms=now_ms,
                )
                merged_map[store_key] = (m_items, m_tomb)
                counts[store_key] = len(m_items)
                if l_chg:
                    local_changed.add(store_key)
                if r_chg or not has_remote:
                    remote_changed.add(store_key)

            if remote_missing or remote_changed:
                max_chunks = 1
                encoded_rows: list[list[str]] = []
                for store_key in COMPANION_SYNC_STORES:
                    m_items, m_tomb = merged_map[store_key]
                    row = encode_store_row(store_key, m_items, m_tomb, now_iso_str)
                    max_chunks = max(max_chunks, len(row) - 3)
                    encoded_rows.append(row)
                header = ["store", "updated_at", "count", *[f"payload_{i}" for i in range(max_chunks)]]
                service.spreadsheets().values().clear(
                    spreadsheetId=sheet_id,
                    range=sync_rng,
                    body={},
                ).execute()
                service.spreadsheets().values().update(
                    spreadsheetId=sheet_id,
                    range=_a1_range(COMPANION_SYNC_TAB, "A1"),
                    valueInputOption="RAW",
                    body={"values": [header, *encoded_rows]},
                ).execute()

            deadlines_pushed = 0
            if push_deadlines and ("tasks" in remote_changed or "tasks" in local_changed or remote_missing or force_deadlines):
                merged_tasks = merged_map["tasks"][0]
                deadlines_pushed = _write_deadlines_with_service(
                    service, sheet_id, cfg.normalized_tab(), merged_tasks, HttpError,
                )

            return SheetsCompanionSyncResult(
                ok=True,
                merged=merged_map,
                local_changed=local_changed,
                remote_changed=remote_changed,
                counts=counts,
                deadlines_pushed=deadlines_pushed,
                synced_at=now_iso_str,
            )
        except HttpError as exc:
            return SheetsCompanionSyncResult(ok=False, error=_friendly_http_error(exc, client_email))
        except TimeoutError:
            return SheetsCompanionSyncResult(ok=False, error="Google Sheets sync timed out.")
        except Exception as exc:
            return SheetsCompanionSyncResult(ok=False, error=f"Google Sheets sync failed ({type(exc).__name__}).")
        finally:
            _close_service(service)
