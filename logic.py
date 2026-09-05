"""Filing automation that mirrors High Court 6 Excel rules without changing them."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import db
from store import as_date, as_text, load_settings, norm_suit, read_catalogs

TERMINAL_OUTCOMES = {
    "JUDGMENT DELIVERED",
    "STRUCK OUT",
    "DISCONTINUED",
    "WITHDRAWN",
    "SETTLED OUT OF COURT",
    "SETTLED",
}
CONCLUDED_NEXT = {"JUDGMENT", "CONSENT JUDGMENT"}
PHONE_RE = re.compile(r"^0\d{10}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def enrich_case(case: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    item = dict(case)
    filed = as_date(item.get("DATE FILED"))
    concluded = as_date(item.get("DATE CONCLUDED"))
    item["filed_on"] = filed
    item["concluded_on"] = concluded
    item["age_days"] = (today - filed).days if filed else None
    item["age_years"] = round(item["age_days"] / 365) if item["age_days"] is not None else None
    if filed:
        end = concluded or today
        years = end.year - filed.year
        months = end.month - filed.month
        days = end.day - filed.day
        if days < 0:
            months -= 1
        if months < 0:
            years -= 1
            months += 12
        item["age_display"] = f"{max(years, 0)} year(s), {max(months, 0)} month(s)"
    else:
        item["age_display"] = ""
    item["status"] = (item.get("STATUS") or "ACTIVE").upper()
    item["is_active"] = item["status"] == "ACTIVE"
    return item


def hydrate_hearing(hearing: dict[str, Any], cases_by_suit: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = dict(hearing)
    case = cases_by_suit.get(norm_suit(item.get("SUIT NUMBER")), {})
    for field in ("PARTIES", "CLAIMANT COUNSEL", "DEFENDANT COUNSEL", "CASE TYPE", "NATURE OF CLAIM"):
        value = as_text(item.get(field))
        if (not value) or value.startswith("="):
            item[field] = case.get(field) or ""
    item["NATURE OF CLAIM/CRIME"] = item.get("NATURE OF CLAIM") or case.get("NATURE OF CLAIM") or ""
    item["hearing_on"] = as_date(item.get("HEARING DATE"))
    item["next_on"] = as_date(item.get("NEXT DATE"))
    item["ruling_on"] = as_date(item.get("RULING")) if str(item.get("RULING", "")).count("-") == 2 else None
    return item


def all_cases() -> list[dict[str, Any]]:
    return [enrich_case(case) for case in db.list_cases()]


def all_hearings(cases: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    cases = cases if cases is not None else all_cases()
    by_suit = {norm_suit(c["SUIT NUMBER"]): c for c in cases}
    hearings = [hydrate_hearing(h, by_suit) for h in db.list_hearings()]
    hearings.sort(key=lambda h: (h.get("hearing_on") or date.min, h.get("row", 0)), reverse=True)
    return hearings


def cases_by_suit(cases: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    cases = cases if cases is not None else all_cases()
    return {norm_suit(c["SUIT NUMBER"]): c for c in cases}


def latest_hearing_by_suit(hearings: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    hearings = hearings if hearings is not None else all_hearings()
    latest = {}
    for hearing in hearings:
        suit = norm_suit(hearing.get("SUIT NUMBER"))
        if suit not in latest:
            latest[suit] = hearing
    return latest


def validate_phone(value: str, label: str) -> str | None:
    text = as_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("0"):
        return None
    if len(digits) == 13 and digits.startswith("234"):
        return None
    return f"{label} must be an 11-digit Nigerian number, e.g. 08012345678."


def validate_email(value: str, label: str) -> str | None:
    text = as_text(value)
    if not text:
        return None
    if EMAIL_RE.match(text):
        return None
    return f"{label} is not a valid email address."


def validate_case(payload: dict[str, Any], existing: list[dict[str, Any]], updating: str | None = None) -> list[str]:
    errors = []
    suit = norm_suit(payload.get("SUIT NUMBER"))
    if not suit:
        errors.append("Suit number is required.")
    if not as_text(payload.get("PARTIES")):
        errors.append("Parties are required.")
    if not as_text(payload.get("CASE TYPE")):
        errors.append("Case type is required.")
    if not as_text(payload.get("STATUS")):
        errors.append("Status is required.")
    if not as_date(payload.get("DATE FILED")):
        errors.append("Date filed is required.")
    if updating:
        others = [c for c in existing if norm_suit(c.get("SUIT NUMBER")) != norm_suit(updating)]
    else:
        others = existing
    if suit and any(norm_suit(c.get("SUIT NUMBER")) == suit for c in others):
        errors.append(f"Suit {suit} is already on the case register.")
    for key, label in (("PHONE NUMBER", "Claimant phone"), ("PHONE NUMBER2", "Defendant phone")):
        msg = validate_phone(payload.get(key, ""), label)
        if msg:
            errors.append(msg)
    for key, label in (("E-MAIL", "Claimant email"), ("E-MAIL2", "Defendant email")):
        msg = validate_email(payload.get(key, ""), label)
        if msg:
            errors.append(msg)
    return errors


def validate_hearing(payload: dict[str, Any], existing_cases: list[dict[str, Any]]) -> list[str]:
    errors = []
    suit = norm_suit(payload.get("SUIT NUMBER"))
    if not suit:
        errors.append("Select a suit number from the register.")
    elif not any(norm_suit(c.get("SUIT NUMBER")) == suit for c in existing_cases):
        errors.append(f"Suit {suit} is not on the case register. File the matter first.")
    if not as_date(payload.get("HEARING DATE")):
        errors.append("Hearing date is required.")
    if not as_text(payload.get("PURPOSE")):
        errors.append("Purpose of sitting is required.")
    return errors


def should_conclude(payload: dict[str, Any]) -> bool:
    outcome = as_text(payload.get("OUTCOME")).upper()
    next_action = as_text(payload.get("NEXT ACTION")).upper()
    return outcome in TERMINAL_OUTCOMES or next_action in CONCLUDED_NEXT


def counsel_directory(cases: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    cases = cases if cases is not None else all_cases()
    seen: dict[str, dict[str, Any]] = {}
    for case in cases:
        for role, name_key, phone_key, email_key in (
            ("Claimant", "CLAIMANT COUNSEL", "PHONE NUMBER", "E-MAIL"),
            ("Defendant", "DEFENDANT COUNSEL", "PHONE NUMBER2", "E-MAIL2"),
        ):
            name = as_text(case.get(name_key)).upper()
            if not name:
                continue
            entry = seen.setdefault(
                name,
                {"name": name, "phones": set(), "emails": set(), "roles": set(), "suits": []},
            )
            phone = as_text(case.get(phone_key))
            email = as_text(case.get(email_key))
            if phone:
                entry["phones"].add(phone)
            if email:
                entry["emails"].add(email)
            entry["roles"].add(role)
            entry["suits"].append(case.get("SUIT NUMBER"))
    directory = []
    for entry in seen.values():
        directory.append(
            {
                "name": entry["name"],
                "phone": ", ".join(sorted(entry["phones"])),
                "email": ", ".join(sorted(entry["emails"])),
                "roles": ", ".join(sorted(entry["roles"])),
                "matters": len(set(entry["suits"])),
            }
        )
    directory.sort(key=lambda item: item["name"])
    return directory


def counsel_key(name: Any) -> str:
    return as_text(name).upper()


def cases_for_counsel(name: str, cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    needle = counsel_key(name)
    cases = cases if cases is not None else all_cases()
    hearings = all_hearings(cases)
    latest = latest_hearing_by_suit(hearings)
    linked = []
    phones = set()
    emails = set()
    roles = set()
    for case in cases:
        claimant = counsel_key(case.get("CLAIMANT COUNSEL"))
        defendant = counsel_key(case.get("DEFENDANT COUNSEL"))
        appears = []
        if claimant == needle:
            appears.append("Claimant counsel")
            roles.add("Claimant")
            if case.get("PHONE NUMBER"):
                phones.add(case["PHONE NUMBER"])
            if case.get("E-MAIL"):
                emails.add(case["E-MAIL"])
        if defendant == needle:
            appears.append("Defendant counsel")
            roles.add("Defendant")
            if case.get("PHONE NUMBER2"):
                phones.add(case["PHONE NUMBER2"])
            if case.get("E-MAIL2"):
                emails.add(case["E-MAIL2"])
        if not appears:
            continue
        item = dict(case)
        item["appears_as"] = ", ".join(appears)
        item["last_hearing"] = latest.get(norm_suit(case["SUIT NUMBER"]))
        linked.append(item)
    return {
        "name": needle,
        "phone": ", ".join(sorted(phones)),
        "email": ", ".join(sorted(emails)),
        "roles": ", ".join(sorted(roles)),
        "cases": linked,
        "matters": len(linked),
        "active": len([c for c in linked if c.get("is_active")]),
    }


def cause_list(target: date, cases: list[dict[str, Any]] | None = None, hearings: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    cases = cases if cases is not None else all_cases()
    hearings = hearings if hearings is not None else all_hearings(cases)
    latest = {}
    for hearing in hearings:
        nxt = hearing.get("next_on")
        if nxt != target:
            continue
        suit = norm_suit(hearing.get("SUIT NUMBER"))
        current = latest.get(suit)
        if current is None or (hearing.get("hearing_on") or date.min) > (current.get("hearing_on") or date.min):
            latest[suit] = hearing
    rows = []
    for index, hearing in enumerate(sorted(latest.values(), key=lambda h: h.get("SUIT NUMBER") or ""), start=1):
        rows.append(
            {
                "sn": index,
                "suit": hearing.get("SUIT NUMBER"),
                "parties": hearing.get("PARTIES"),
                "status": hearing.get("NEXT ACTION") or hearing.get("PURPOSE") or "",
                "claimant": hearing.get("CLAIMANT COUNSEL"),
                "defendant": hearing.get("DEFENDANT COUNSEL"),
                "purpose": hearing.get("NEXT ACTION") or hearing.get("PURPOSE") or "",
                "adj": "",
            }
        )
    return rows


def quarter_bounds(year: int, quarter: str) -> tuple[date, date]:
    q = int(str(quarter).upper().replace("Q", "") or 1)
    start_month = 1 + (q - 1) * 3
    start = date(year, start_month, 1)
    if q == 4:
        end = date(year, 12, 31)
    else:
        end = date(year, start_month + 3, 1)
        end = date.fromordinal(end.toordinal() - 1)
    return start, end


def in_range(value: Any, start: date, end: date) -> bool:
    parsed = as_date(value)
    return bool(parsed and start <= parsed <= end)


def insights(year: int | None = None, quarter: str | None = None) -> dict[str, Any]:
    today = date.today()
    year = year or today.year
    quarter = (quarter or f"Q{(today.month - 1) // 3 + 1}").upper()
    start, end = quarter_bounds(year, quarter)
    cases = all_cases()
    hearings = all_hearings(cases)
    active = [c for c in cases if c.get("is_active")]
    concluded = [c for c in cases if c.get("status") == "CONCLUDED"]
    by_type = defaultdict(int)
    assigned_by_type = defaultdict(int)
    for case in cases:
        by_type[case.get("CASE TYPE") or "UNSET"] += 1
        if in_range(case.get("DATE ASSIGNED") or case.get("DATE FILED"), start, end):
            assigned_by_type[case.get("CASE TYPE") or "UNSET"] += 1
    age_buckets = {"0-3": 0, "4-6": 0, "7+": 0}
    for case in active:
        years = case.get("age_years") or 0
        if years <= 3:
            age_buckets["0-3"] += 1
        elif years <= 6:
            age_buckets["4-6"] += 1
        else:
            age_buckets["7+"] += 1
    purpose_counts = defaultdict(int)
    next_counts = defaultdict(int)
    for hearing in hearings:
        if hearing.get("PURPOSE"):
            purpose_counts[hearing["PURPOSE"]] += 1
        if hearing.get("NEXT ACTION"):
            next_counts[hearing["NEXT ACTION"]] += 1
    q_hearings = [h for h in hearings if in_range(h.get("HEARING DATE"), start, end)]

    # Collect judgments from hearing log and concluded register
    judgments_map: dict[str, dict[str, Any]] = {}
    for h in hearings:
        outcome = as_text(h.get("OUTCOME")).upper()
        purpose = as_text(h.get("PURPOSE")).upper()
        next_action = as_text(h.get("NEXT ACTION")).upper()
        is_judgment = (
            "JUDGMENT" in outcome
            or ("JUDGMENT" in purpose and outcome in {"DELIVERED", "HEARD", "JUDGMENT DELIVERED", "JUDGMENT", "CONSENT JUDGMENT"})
            or (next_action in CONCLUDED_NEXT and (h.get("DATE CONCLUDED") or outcome in TERMINAL_OUTCOMES))
        )
        if is_judgment:
            j_date = as_date(h.get("DATE CONCLUDED")) or as_date(h.get("HEARING DATE"))
            if j_date and in_range(j_date, start, end):
                suit = norm_suit(h.get("SUIT NUMBER"))
                if suit not in judgments_map:
                    judgments_map[suit] = {
                        "suit": h.get("SUIT NUMBER"),
                        "date": j_date.isoformat(),
                        "date_obj": j_date,
                        "parties": h.get("PARTIES") or "",
                        "case_type": h.get("CASE TYPE") or "",
                        "nature": h.get("NATURE OF CLAIM") or h.get("NATURE OF CLAIM/CRIME") or "",
                        "outcome": h.get("OUTCOME") or "JUDGMENT DELIVERED",
                        "claimant_counsel": h.get("CLAIMANT COUNSEL") or "",
                        "defendant_counsel": h.get("DEFENDANT COUNSEL") or "",
                        "source": "hearing",
                    }

    for c in cases:
        status = (c.get("STATUS") or "").upper()
        c_date = as_date(c.get("DATE CONCLUDED"))
        if status == "CONCLUDED" and c_date and in_range(c_date, start, end):
            suit = norm_suit(c.get("SUIT NUMBER"))
            details = as_text(c.get("DETAILS"))
            remark = as_text(c.get("REMARK"))
            outcome_text = details or remark or "JUDGMENT DELIVERED"
            if suit not in judgments_map and ("JUDGMENT" in outcome_text.upper() or not details or details.upper() == "CONCLUDED"):
                judgments_map[suit] = {
                    "suit": c.get("SUIT NUMBER"),
                    "date": c_date.isoformat(),
                    "date_obj": c_date,
                    "parties": c.get("PARTIES") or "",
                    "case_type": c.get("CASE TYPE") or "",
                    "nature": c.get("NATURE OF CLAIM") or "",
                    "outcome": details if "JUDGMENT" in details.upper() else "JUDGMENT DELIVERED",
                    "claimant_counsel": c.get("CLAIMANT COUNSEL") or "",
                    "defendant_counsel": c.get("DEFENDANT COUNSEL") or "",
                    "source": "register",
                }

    judgments_list = sorted(judgments_map.values(), key=lambda j: j["date_obj"], reverse=True)

    # Collect rulings from hearing log
    rulings_map: dict[str, dict[str, Any]] = {}
    for h in hearings:
        outcome = as_text(h.get("OUTCOME")).upper()
        ruling = as_text(h.get("RULING")).upper()
        if ("RULING" in outcome and "NOT READY" not in outcome) or ruling == "DELIVERED":
            r_date = as_date(h.get("HEARING DATE")) or as_date(h.get("DATE CONCLUDED"))
            if r_date and in_range(r_date, start, end):
                suit = norm_suit(h.get("SUIT NUMBER"))
                key = f"{suit}_{r_date.isoformat()}"
                if key not in rulings_map:
                    rulings_map[key] = {
                        "suit": h.get("SUIT NUMBER"),
                        "date": r_date.isoformat(),
                        "date_obj": r_date,
                        "parties": h.get("PARTIES") or "",
                        "case_type": h.get("CASE TYPE") or "",
                        "purpose": h.get("PURPOSE") or "",
                        "outcome": h.get("OUTCOME") or "RULING DELIVERED",
                    }
    rulings_list = sorted(rulings_map.values(), key=lambda r: r["date_obj"], reverse=True)

    return {
        "year": year,
        "quarter": quarter,
        "start": start,
        "end": end,
        "total": len(cases),
        "active": len(active),
        "concluded": len(concluded),
        "dormant": len([c for c in cases if c.get("status") == "DORMANT"]),
        "by_type": dict(sorted(by_type.items())),
        "assigned_by_type": dict(sorted(assigned_by_type.items())),
        "age_buckets": age_buckets,
        "purpose_counts": dict(sorted(purpose_counts.items())),
        "next_counts": dict(sorted(next_counts.items())),
        "assigned_this_quarter": sum(assigned_by_type.values()),
        "concluded_this_quarter": len([c for c in concluded if in_range(c.get("DATE CONCLUDED"), start, end)]),
        "hearings_this_quarter": len(q_hearings),
        "judgments_this_quarter": len(judgments_list),
        "judgments": judgments_list,
        "rulings_this_quarter": len(rulings_list),
        "rulings": rulings_list,
        "sittings": len({h.get("hearing_on") for h in q_hearings if h.get("hearing_on")}),
        "court_not_sitting": len([h for h in q_hearings if as_text(h.get("OUTCOME")).upper() == "COURT NOT SITTING"]),
        "struck_out": len([h for h in q_hearings if as_text(h.get("OUTCOME")).upper() == "STRUCK OUT"]),
        "withdrawn": len([h for h in q_hearings if as_text(h.get("OUTCOME")).upper() == "WITHDRAWN"]),
        "settled": len([h for h in q_hearings if "SETTLE" in as_text(h.get("OUTCOME")).upper()]),
        "pending_over_one": len([c for c in active if (c.get("age_days") or 0) > 365]),
        "pending_over_five": len([c for c in active if (c.get("age_years") or 0) >= 5]),
    }


def alerts(cases: list[dict[str, Any]] | None = None, hearings: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    today = date.today()
    cases = cases if cases is not None else all_cases()
    hearings = hearings if hearings is not None else all_hearings(cases)
    latest = latest_hearing_by_suit(hearings)
    items = []
    today_list = cause_list(today, cases, hearings)
    if today_list:
        items.append(
            {
                "level": "today",
                "title": f"{len(today_list)} matter(s) on today's cause list",
                "detail": "Open the cause list and record each sitting after court.",
                "href": "/?tab=cause-list",
            }
        )
    missing_next = []
    for case in cases:
        if not case.get("is_active"):
            continue
        last = latest.get(norm_suit(case["SUIT NUMBER"]))
        if last is None:
            missing_next.append(case["SUIT NUMBER"])
        elif not last.get("next_on") and as_text(last.get("OUTCOME")).upper() not in TERMINAL_OUTCOMES:
            missing_next.append(case["SUIT NUMBER"])
    if missing_next:
        items.append(
            {
                "level": "warn",
                "title": f"{len(missing_next)} active matter(s) have no next date",
                "detail": ", ".join(missing_next[:6]) + ("…" if len(missing_next) > 6 else ""),
                "href": "/?tab=register&filter=needs-date",
            }
        )
    stale = [c for c in cases if c.get("is_active") and (c.get("age_years") or 0) >= 5]
    if stale:
        items.append(
            {
                "level": "alert",
                "title": f"{len(stale)} active matter(s) are 5+ years old",
                "detail": "These will appear on the NJC pending-over-five return.",
                "href": "/?tab=insights",
            }
        )
    incomplete = [
        c
        for c in cases
        if c.get("is_active") and (not c.get("CLAIMANT COUNSEL") or not c.get("DATE ASSIGNED"))
    ]
    if incomplete:
        items.append(
            {
                "level": "info",
                "title": f"{len(incomplete)} filing(s) still need counsel or assignment",
                "detail": "Finish the register row so the matter can be listed.",
                "href": "/?tab=register",
            }
        )
    return items


def suggest_suit(cases: list[dict[str, Any]] | None = None, year: int | None = None) -> str:
    year = year or date.today().year
    cases = cases if cases is not None else all_cases()
    highest = 0
    pattern = re.compile(rf"(?:PHC|HCT?6?|SUIT)[^\d]*(\d+)[^\d]*{year}", re.I)
    for case in cases:
        match = pattern.search(case.get("SUIT NUMBER") or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"PHC/{highest + 1}/{year}"


def form_payload(form) -> dict[str, Any]:
    payload = {}
    for key in form.keys():
        payload[key] = as_text(form.get(key))
    return payload


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return as_date(value)


def dashboard() -> dict[str, Any]:
    settings = load_settings()
    cases = all_cases()
    hearings = all_hearings(cases)
    today = date.today()
    stats = insights()
    return {
        "settings": settings,
        "cases": cases,
        "hearings": hearings,
        "today_list": cause_list(today, cases, hearings),
        "recent_cases": sorted(cases, key=lambda c: c.get("filed_on") or date.min, reverse=True)[:8],
        "recent_hearings": hearings[:8],
        "alerts": alerts(cases, hearings),
        "stats": stats,
        "today": today,
    }
