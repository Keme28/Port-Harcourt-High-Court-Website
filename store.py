"""Excel-preserving store for the High Court 6 File Portal.

The workbook is the backend of record. This module only reads lookup sheets
and writes user-entered cells in HCT6_CR / HCT6_HL. Formulas, LISTS, Insights,
Back Office, Auto Cause List, and named ranges are left untouched.
"""

from __future__ import annotations

import json
import re
import shutil
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import range_boundaries

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
SETTINGS_PATH = DATA_DIR / "settings.json"
DEFAULT_WORKBOOK = DATA_DIR / "HIGH_COURT_6_FILE_PORTAL.xlsx"
TEMPLATE_WORKBOOK = DATA_DIR / "template.xlsx"
EXPORT_DIR = DATA_DIR / "exports"
SEED_CANDIDATES = [
    Path(r"C:\Users\USER\Downloads\Telegram Desktop\Copy of 29.07.2026 17.54.09 - HIGH COURT 6 FILE PORTAL.xlsx"),
]

CASE_SHEET = "CASE REGISTER"
HEARING_SHEET = "HEARING LOG"
LISTS_SHEET = "LISTS"
CASE_TABLE = "HCT6_CR"
HEARING_TABLE = "HCT6_HL"

CASE_INPUT_FIELDS = [
    "SUIT NUMBER",
    "PARTIES",
    "CASE TYPE",
    "NATURE OF CLAIM",
    "PARTICULARS",
    "CLAIMANT COUNSEL",
    "PHONE NUMBER",
    "E-MAIL",
    "DEFENDANT COUNSEL",
    "PHONE NUMBER2",
    "E-MAIL2",
    "DATE FILED",
    "DATE ASSIGNED",
    "DATE TRIAL COMMENCED",
    "STATUS",
    "DETAILS",
    "DATE CONCLUDED",
]

HEARING_INPUT_FIELDS = [
    "SUIT NUMBER",
    "HEARING DATE",
    "PURPOSE",
    "OUTCOME",
    "NEXT ACTION",
    "NEXT DATE",
    "RULING",
    "DATE CONCLUDED",
]

DEFAULT_SETTINGS = {
    "workbook_path": str(DEFAULT_WORKBOOK),
    "court_name": "RIVERS STATE HIGH COURT",
    "court_title": "HIGH COURT",
    "judge_name": "HON. JUST. E. N. THOMPSON",
    "division": "PORT HARCOURT",
    "court_level": "HIGH COURT",
}


def ensure_layout() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_WORKBOOK.exists():
        for seed in SEED_CANDIDATES:
            if seed.exists():
                shutil.copy2(seed, TEMPLATE_WORKBOOK)
                break
        if not TEMPLATE_WORKBOOK.exists() and DEFAULT_WORKBOOK.exists():
            shutil.copy2(DEFAULT_WORKBOOK, TEMPLATE_WORKBOOK)
    if not DEFAULT_WORKBOOK.exists() and TEMPLATE_WORKBOOK.exists():
        shutil.copy2(TEMPLATE_WORKBOOK, DEFAULT_WORKBOOK)
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS.copy())


def load_settings() -> dict[str, Any]:
    ensure_layout()
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            merged = DEFAULT_SETTINGS.copy()
            merged.update({k: v for k, v in data.items() if v not in (None, "")})
            return merged
        except json.JSONDecodeError:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def workbook_path() -> Path:
    ensure_layout()
    path = Path(load_settings().get("workbook_path") or DEFAULT_WORKBOOK)
    if not path.exists():
        return DEFAULT_WORKBOOK
    return path


def as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and value > 20000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def norm_suit(value: Any) -> str:
    return re.sub(r"\s+", " ", as_text(value)).upper()


def _table(ws, name: str):
    tables = getattr(ws, "tables", {}) or {}
    if name in tables:
        return tables[name]
    for key, table in tables.items():
        if key.upper() == name.upper() or getattr(table, "displayName", "") == name:
            return table
    raise KeyError(f"Table {name} not found on {ws.title}")


def _headers(ws, table) -> dict[str, int]:
    min_col, min_row, max_col, _max_row = range_boundaries(table.ref)
    mapping = {}
    for col in range(min_col, max_col + 1):
        header = as_text(ws.cell(min_row, col).value)
        if header:
            mapping[header] = col
    return mapping


def _copy_row_style(ws, source_row: int, dest_row: int, min_col: int, max_col: int) -> None:
    for col in range(min_col, max_col + 1):
        src = ws.cell(source_row, col)
        dest = ws.cell(dest_row, col)
        if src.has_style:
            dest.font = copy(src.font)
            dest.border = copy(src.border)
            dest.fill = copy(src.fill)
            dest.number_format = src.number_format
            dest.protection = copy(src.protection)
            dest.alignment = copy(src.alignment)


def _copy_formulas(ws, source_row: int, dest_row: int, min_col: int, max_col: int, input_cols: set[int]) -> None:
    for col in range(min_col, max_col + 1):
        if col in input_cols:
            continue
        src = ws.cell(source_row, col)
        if isinstance(src.value, str) and src.value.startswith("="):
            ws.cell(dest_row, col).value = src.value


def _expand_table(table, max_row: int) -> None:
    min_col, min_row, max_col, current = range_boundaries(table.ref)
    if max_row > current:
        from openpyxl.utils import get_column_letter

        table.ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}"


def _first_empty_row(ws, suit_col: int, start: int, end: int) -> int:
    for row in range(start, end + 1):
        if not as_text(ws.cell(row, suit_col).value):
            return row
    return end + 1


def _backup(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{path.stem}-{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    old = sorted(BACKUP_DIR.glob(f"{path.stem}-*{path.suffix}"))
    for stale in old[:-20]:
        try:
            stale.unlink()
        except OSError:
            pass
    return dest


def _open():
    path = workbook_path()
    if not path.exists():
        raise FileNotFoundError(f"Backend workbook not found: {path}")
    return path, load_workbook(path)


def _save(wb, path: Path) -> None:
    _backup(path)
    tmp = path.with_suffix(".tmp.xlsx")
    try:
        wb.save(tmp)
        tmp.replace(path)
    except PermissionError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise PermissionError(
            "The Excel backend is open or locked. Close it in Excel and try again."
        ) from exc
    finally:
        wb.close()


def _named_values(wb, defined_name: str) -> list[str]:
    names = wb.defined_names
    defn = names.get(defined_name) or names.get(defined_name.upper()) or names.get(defined_name.lower())
    if defn is None:
        return []
    values = []
    try:
        destinations = list(defn.destinations)
    except Exception:
        return []
    for sheet_name, coord in destinations:
        if not sheet_name or sheet_name not in wb.sheetnames:
            continue
        if "#REF" in str(coord).upper():
            continue
        ws = wb[sheet_name]
        try:
            cells = ws[coord]
        except Exception:
            continue
        if hasattr(cells, "value"):
            cells = ((cells,),)
        elif isinstance(cells, tuple) and cells and not isinstance(cells[0], tuple):
            cells = (cells,)
        for row in cells:
            for cell in row:
                text = as_text(cell.value)
                if text:
                    values.append(text)
    return values


def _column_dump(ws, col: int, start: int, end: int) -> list[str]:
    out = []
    for row in range(start, end + 1):
        text = as_text(ws.cell(row, col).value)
        if text:
            out.append(text)
    return out


_CATALOG_CACHE: dict[str, Any] = {"mtime": None, "data": None}
LOOKUPS_JSON = DATA_DIR / "lookups.json"


def _fallback_catalogs(path: Path) -> dict[str, Any]:
    return {
        "workbook": str(path),
        "case_types": ["APPEAL", "CRIMINAL_MATTER", "FHR", "MOTION", "ORIGINATING_SUMMONS", "PETITION", "WRIT"],
        "status": ["ACTIVE", "CONCLUDED", "DORMANT"],
        "purpose": [],
        "actions": [],
        "outcomes": [],
        "rulings": [],
        "judges": [],
        "courts": [],
        "divisions": [],
        "quarters": ["Q1", "Q2", "Q3", "Q4"],
        "natures": {},
        "particulars": {},
        "details": {},
        "lists": {},
    }


def _load_lookups_json() -> dict[str, Any] | None:
    if not LOOKUPS_JSON.exists():
        return None
    try:
        return json.loads(LOOKUPS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_catalogs() -> dict[str, Any]:
    path = workbook_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None
    cached = _CATALOG_CACHE.get("data")
    if cached is not None:
        return cached
    disk = _load_lookups_json()
    if disk and (disk.get("lists") or disk.get("natures")):
        _CATALOG_CACHE["data"] = disk
        _CATALOG_CACHE["mtime"] = mtime
        return disk
    try:
        data = _read_catalogs_from_workbook(path)
        data["source_mtime"] = mtime
        LOOKUPS_JSON.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception:
        if cached is not None:
            return cached
        if disk:
            return disk
        return _fallback_catalogs(path)


def _read_catalogs_from_workbook(path: Path) -> dict[str, Any]:
    path, wb = _open()
    try:
        lists = wb[LISTS_SHEET] if LISTS_SHEET in wb.sheetnames else None

        def named_or_col(name: str, col: int, start: int, end: int) -> list[str]:
            values = _named_values(wb, name)
            if values:
                return values
            if lists is None:
                return []
            return _column_dump(lists, col, start, end)

        case_types = named_or_col("CASETYPE", 3, 8, 14)
        status = named_or_col("STATUS", 41, 15, 17)
        purpose = named_or_col("PURPOSE", 7, 29, 44)
        actions = named_or_col("ACTIONS", 41, 22, 39)
        outcomes = named_or_col("OUTCOME", 27, 18, 38) or named_or_col("REASON", 27, 18, 38)
        rulings = named_or_col("RULING", 23, 27, 28)
        judges = named_or_col("JUDGES2", 3, 47, 93)
        courts = named_or_col("COURT", 1, 8, 15)
        divisions = named_or_col("DIVISIONS", 5, 59, 71)
        quarters = named_or_col("QUARTER", 5, 47, 50)
        lists_by_name: dict[str, list[str]] = {}
        skip_names = {"Print_Area", "Print_Titles"}
        for defined in wb.defined_names.values():
            name = as_text(getattr(defined, "name", ""))
            if not name or name.startswith("_xlnm") or name.startswith("Slicer") or name in skip_names:
                continue
            values = _named_values(wb, name)
            if values:
                lists_by_name[name] = values
                lists_by_name[name.upper()] = values
        extras = {
            "LABOUR": _column_dump(lists, 11, 20, 22) if lists else [],
            "TENANCY": _column_dump(lists, 24, 8, 9) if lists else [],
            "GARNISHEE": _column_dump(lists, 31, 7, 7) if lists else [],
            "NATURE": named_or_col("NATURE", 3, 27, 35),
        }
        for key, values in extras.items():
            if values:
                lists_by_name[key] = values
                lists_by_name[key.upper()] = values

        natures: dict[str, list[str]] = {}
        for case_type in case_types:
            natures[case_type] = lists_by_name.get(case_type) or _named_values(wb, case_type)

        # Child lists used by Particulars (INDIRECT of nature) and Details (INDIRECT of particulars).
        child_keys = [
            "LAND", "CONTRACT", "TORT", "MATRIMONIAL", "LABOUR", "TENANCY", "BAIL",
            "FELONY", "MISDEMEANOR", "FHR", "MOTION", "APPEAL", "PETITION",
            "ORIGINATING_SUMMONS", "UNDEFENDED_LIST", "OTHERS", "WRIT",
            "INTERPRETATION", "POSSESSION", "LEAVE", "MAGISTRATE_COURT",
            "CONCLUDED", "GARNISHEE",
        ]
        particulars = {}
        details = {}
        for key in child_keys:
            values = lists_by_name.get(key) or []
            if values:
                particulars[key] = values
                details[key] = values
        result = {
            "workbook": str(path),
            "case_types": case_types,
            "status": status or ["ACTIVE", "CONCLUDED", "DORMANT"],
            "purpose": purpose,
            "actions": actions,
            "outcomes": outcomes,
            "rulings": rulings,
            "judges": judges,
            "courts": courts,
            "divisions": divisions,
            "quarters": quarters or ["Q1", "Q2", "Q3", "Q4"],
            "natures": natures,
            "particulars": particulars,
            "details": details,
            "lists": lists_by_name,
        }
        try:
            _CATALOG_CACHE["mtime"] = path.stat().st_mtime
        except OSError:
            _CATALOG_CACHE["mtime"] = None
        _CATALOG_CACHE["data"] = result
        return result
    finally:
        wb.close()


def _row_to_case(ws, headers: dict[str, int], row: int) -> dict[str, Any] | None:
    suit = norm_suit(ws.cell(row, headers.get("SUIT NUMBER", 4)).value)
    if not suit:
        return None
    record = {"row": row, "sheet": CASE_SHEET}
    for name, col in headers.items():
        value = ws.cell(row, col).value
        if "DATE" in name or name in {"RULING"}:
            parsed = as_date(value)
            record[name] = parsed.isoformat() if parsed else as_text(value)
        else:
            record[name] = as_text(value)
    record["SUIT NUMBER"] = suit
    return record


def _row_to_hearing(ws, headers: dict[str, int], row: int) -> dict[str, Any] | None:
    suit = norm_suit(ws.cell(row, headers.get("SUIT NUMBER", 3)).value)
    if not suit:
        return None
    record = {"row": row, "sheet": HEARING_SHEET}
    for name, col in headers.items():
        value = ws.cell(row, col).value
        if "DATE" in name or name == "RULING":
            parsed = as_date(value)
            record[name] = parsed.isoformat() if parsed else as_text(value)
        else:
            record[name] = as_text(value)
    record["SUIT NUMBER"] = suit
    return record


def read_cases() -> list[dict[str, Any]]:
    _path, wb = _open()
    try:
        ws = wb[CASE_SHEET]
        table = _table(ws, CASE_TABLE)
        headers = _headers(ws, table)
        _min_col, min_row, _max_col, max_row = range_boundaries(table.ref)
        cases = []
        for row in range(min_row + 1, max_row + 1):
            item = _row_to_case(ws, headers, row)
            if item:
                cases.append(item)
        return cases
    finally:
        wb.close()


def read_hearings() -> list[dict[str, Any]]:
    _path, wb = _open()
    try:
        ws = wb[HEARING_SHEET]
        table = _table(ws, HEARING_TABLE)
        headers = _headers(ws, table)
        _min_col, min_row, _max_col, max_row = range_boundaries(table.ref)
        hearings = []
        for row in range(min_row + 1, max_row + 1):
            item = _row_to_hearing(ws, headers, row)
            if item:
                hearings.append(item)
        return hearings
    finally:
        wb.close()


def _apply_inputs(ws, headers: dict[str, int], row: int, payload: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        if field not in payload:
            continue
        col = headers.get(field)
        if not col:
            continue
        value = payload[field]
        if value in ("", None):
            ws.cell(row, col).value = None
            continue
        if "DATE" in field or field == "RULING":
            parsed = as_date(value)
            ws.cell(row, col).value = parsed if parsed else as_text(value)
        elif field == "SUIT NUMBER":
            ws.cell(row, col).value = norm_suit(value)
        else:
            ws.cell(row, col).value = as_text(value)


def upsert_case(payload: dict[str, Any]) -> dict[str, Any]:
    path, wb = _open()
    try:
        ws = wb[CASE_SHEET]
        table = _table(ws, CASE_TABLE)
        headers = _headers(ws, table)
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        suit_col = headers["SUIT NUMBER"]
        needle = norm_suit(payload.get("SUIT NUMBER"))
        target = None
        for row in range(min_row + 1, max_row + 1):
            if norm_suit(ws.cell(row, suit_col).value) == needle:
                target = row
                break
        if target is None:
            target = _first_empty_row(ws, suit_col, min_row + 1, max_row)
            source = min_row + 1
            if target > max_row:
                _copy_row_style(ws, source, target, min_col, max_col)
                _copy_formulas(ws, source, target, min_col, max_col, {headers[f] for f in CASE_INPUT_FIELDS if f in headers})
                _expand_table(table, target)
        _apply_inputs(ws, headers, target, payload, CASE_INPUT_FIELDS)
        _save(wb, path)
        return {"ok": True, "row": target, "suit": needle}
    except Exception:
        wb.close()
        raise


def upsert_hearing(payload: dict[str, Any]) -> dict[str, Any]:
    path, wb = _open()
    try:
        ws = wb[HEARING_SHEET]
        table = _table(ws, HEARING_TABLE)
        headers = _headers(ws, table)
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        suit_col = headers["SUIT NUMBER"]
        date_col = headers.get("HEARING DATE")
        needle = norm_suit(payload.get("SUIT NUMBER"))
        hearing_day = as_date(payload.get("HEARING DATE"))
        target = None
        for row in range(min_row + 1, max_row + 1):
            if norm_suit(ws.cell(row, suit_col).value) != needle:
                continue
            if date_col and as_date(ws.cell(row, date_col).value) != hearing_day:
                continue
            target = row
            break
        if target is None:
            target = _first_empty_row(ws, suit_col, min_row + 1, max_row)
            source = min_row + 1
            if target > max_row:
                _copy_row_style(ws, source, target, min_col, max_col)
                _copy_formulas(
                    ws,
                    source,
                    target,
                    min_col,
                    max_col,
                    {headers[f] for f in HEARING_INPUT_FIELDS if f in headers},
                )
                _expand_table(table, target)
        _apply_inputs(ws, headers, target, payload, HEARING_INPUT_FIELDS)
        _save(wb, path)
        return {"ok": True, "row": target, "suit": needle}
    except Exception:
        wb.close()
        raise


def delete_case_row(suit: str) -> None:
    path, wb = _open()
    try:
        needle = norm_suit(suit)
        ws = wb[CASE_SHEET]
        table = _table(ws, CASE_TABLE)
        headers = _headers(ws, table)
        _min_col, min_row, _max_col, max_row = range_boundaries(table.ref)
        for row in range(min_row + 1, max_row + 1):
            if norm_suit(ws.cell(row, headers["SUIT NUMBER"]).value) == needle:
                for field in CASE_INPUT_FIELDS:
                    col = headers.get(field)
                    if col:
                        ws.cell(row, col).value = None
        ws = wb[HEARING_SHEET]
        table = _table(ws, HEARING_TABLE)
        headers = _headers(ws, table)
        _min_col, min_row, _max_col, max_row = range_boundaries(table.ref)
        for row in range(min_row + 1, max_row + 1):
            if norm_suit(ws.cell(row, headers["SUIT NUMBER"]).value) == needle:
                for field in HEARING_INPUT_FIELDS:
                    col = headers.get(field)
                    if col:
                        ws.cell(row, col).value = None
        _save(wb, path)
    except Exception:
        wb.close()
        raise


def delete_hearing_row(suit: str, hearing_date: str) -> None:
    path, wb = _open()
    try:
        needle = norm_suit(suit)
        day = as_date(hearing_date)
        ws = wb[HEARING_SHEET]
        table = _table(ws, HEARING_TABLE)
        headers = _headers(ws, table)
        _min_col, min_row, _max_col, max_row = range_boundaries(table.ref)
        date_col = headers.get("HEARING DATE")
        for row in range(min_row + 1, max_row + 1):
            if norm_suit(ws.cell(row, headers["SUIT NUMBER"]).value) != needle:
                continue
            if date_col and as_date(ws.cell(row, date_col).value) != day:
                continue
            for field in HEARING_INPUT_FIELDS:
                col = headers.get(field)
                if col:
                    ws.cell(row, col).value = None
        _save(wb, path)
    except Exception:
        wb.close()
        raise


def push_case(payload: dict[str, Any]) -> str | None:
    try:
        upsert_case(payload)
        return None
    except Exception as exc:
        return str(exc)


def push_hearing(payload: dict[str, Any]) -> str | None:
    try:
        upsert_hearing(payload)
        return None
    except Exception as exc:
        return str(exc)


def add_case(payload: dict[str, Any]) -> dict[str, Any]:
    path, wb = _open()
    try:
        ws = wb[CASE_SHEET]
        table = _table(ws, CASE_TABLE)
        headers = _headers(ws, table)
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        suit_col = headers["SUIT NUMBER"]
        target = _first_empty_row(ws, suit_col, min_row + 1, max_row)
        source = min_row + 1
        if target > max_row:
            _copy_row_style(ws, source, target, min_col, max_col)
            _copy_formulas(ws, source, target, min_col, max_col, {headers[f] for f in CASE_INPUT_FIELDS if f in headers})
            _expand_table(table, target)
        _apply_inputs(ws, headers, target, payload, CASE_INPUT_FIELDS)
        _save(wb, path)
        return {"ok": True, "row": target, "suit": norm_suit(payload.get("SUIT NUMBER"))}
    except Exception:
        wb.close()
        raise


def update_case(suit: str, payload: dict[str, Any]) -> dict[str, Any]:
    path, wb = _open()
    try:
        ws = wb[CASE_SHEET]
        table = _table(ws, CASE_TABLE)
        headers = _headers(ws, table)
        _min_col, min_row, _max_col, max_row = range_boundaries(table.ref)
        target = None
        needle = norm_suit(suit)
        for row in range(min_row + 1, max_row + 1):
            if norm_suit(ws.cell(row, headers["SUIT NUMBER"]).value) == needle:
                target = row
                break
        if target is None:
            wb.close()
            raise KeyError(f"Suit {needle} was not found in the case register.")
        _apply_inputs(ws, headers, target, payload, CASE_INPUT_FIELDS)
        _save(wb, path)
        return {"ok": True, "row": target, "suit": needle}
    except Exception:
        try:
            wb.close()
        except Exception:
            pass
        raise


def add_hearing(payload: dict[str, Any]) -> dict[str, Any]:
    path, wb = _open()
    try:
        ws = wb[HEARING_SHEET]
        table = _table(ws, HEARING_TABLE)
        headers = _headers(ws, table)
        min_col, min_row, max_col, max_row = range_boundaries(table.ref)
        suit_col = headers["SUIT NUMBER"]
        target = _first_empty_row(ws, suit_col, min_row + 1, max_row)
        source = min_row + 1
        if target > max_row:
            _copy_row_style(ws, source, target, min_col, max_col)
            _copy_formulas(
                ws,
                source,
                target,
                min_col,
                max_col,
                {headers[f] for f in HEARING_INPUT_FIELDS if f in headers},
            )
            _expand_table(table, target)
        _apply_inputs(ws, headers, target, payload, HEARING_INPUT_FIELDS)
        _save(wb, path)
        return {"ok": True, "row": target, "suit": norm_suit(payload.get("SUIT NUMBER"))}
    except Exception:
        wb.close()
        raise


def update_hearing(row_number: int, payload: dict[str, Any]) -> dict[str, Any]:
    path, wb = _open()
    try:
        ws = wb[HEARING_SHEET]
        table = _table(ws, HEARING_TABLE)
        headers = _headers(ws, table)
        _apply_inputs(ws, headers, row_number, payload, HEARING_INPUT_FIELDS)
        _save(wb, path)
        return {"ok": True, "row": row_number, "suit": norm_suit(payload.get("SUIT NUMBER"))}
    except Exception:
        wb.close()
        raise


def set_workbook(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")
    settings = load_settings()
    settings["workbook_path"] = str(path)
    return save_settings(settings)


def export_rows(cases: list[dict[str, Any]], hearings: list[dict[str, Any]]) -> Path:
    ensure_layout()
    if not TEMPLATE_WORKBOOK.exists():
        raise FileNotFoundError("Excel template is missing. Place the High Court 6 portal workbook in app/data.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = EXPORT_DIR / f"HCT6-FILE-PORTAL-{stamp}.xlsx"
    shutil.copy2(TEMPLATE_WORKBOOK, dest)
    settings = load_settings()
    previous = settings.get("workbook_path")
    settings["workbook_path"] = str(dest)
    save_settings(settings)
    try:
        for case in cases:
            add_case(case)
        for hearing in hearings:
            add_hearing(hearing)
    finally:
        settings["workbook_path"] = previous or str(DEFAULT_WORKBOOK)
        save_settings(settings)
    return dest
