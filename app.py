from __future__ import annotations

import hmac
import json
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2.utils import htmlsafe_json_dumps
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import auth
import db
import logic
import store

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


templates.env.policies["json.dumps_function"] = lambda obj, **kwargs: htmlsafe_json_dumps(
    obj, dumps=json.dumps, default=_json_default, **kwargs
)


def tab_url(tab: str, **params) -> str:
    query = {"tab": tab}
    for key, value in params.items():
        if value not in (None, "", 0):
            query[key] = value
    return "/?" + urlencode(query)


def flash_redirect(url: str, message: str, kind: str = "ok") -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    response.set_cookie("hct6_flash", message, max_age=20, httponly=False)
    response.set_cookie("hct6_flash_kind", kind, max_age=20, httponly=False)
    return response


BOOT_ID = secrets.token_hex(16)


def current_user(request: Request) -> dict | None:
    if "session" not in request.scope:
        return None
    try:
        data = request.session.get("user")
        boot_id = request.session.get("boot_id")
    except (AssertionError, KeyError, AttributeError):
        return None
    if not data or boot_id != BOOT_ID:
        try:
            request.session.clear()
        except Exception:
            pass
        return None
    user = db.get_user_by_id(int(data.get("id") or 0))
    if not user or not user.get("active"):
        try:
            request.session.clear()
        except Exception:
            pass
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email", ""),
        "display_name": user["display_name"],
        "role": user["role"],
        "auth_provider": user.get("auth_provider", "local"),
        "is_admin": user["role"] == "admin",
        "is_readonly": user["role"] == "readonly",
    }


def context(request: Request, **extra):
    settings = store.load_settings()
    user = extra.get("current_user") if "current_user" in extra else current_user(request)
    is_read_only_sys = db.is_read_only_mode()
    user_read_only = auth.is_read_only(user, is_read_only_sys)
    payload = {
        "request": request,
        "settings": settings,
        "today": date.today(),
        "flash": request.cookies.get("hct6_flash"),
        "flash_kind": request.cookies.get("hct6_flash_kind", "ok"),
        "nav": extra.get("tab") or request.query_params.get("tab") or "home",
        "current_user": user,
        "read_only_mode": is_read_only_sys,
        "user_read_only": user_read_only,
        "can_write": auth.can_write(user, is_read_only_sys),
        "is_admin": auth.is_admin(user),
    }
    payload.update(extra)
    return payload


def catalogs():
    try:
        return store.read_catalogs()
    except Exception:
        return {
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


def render(request: Request, template: str, tab: str, extra: dict | None = None):
    return templates.TemplateResponse(request, template, context(request, tab=tab, **(extra or {})))


PUBLIC_PATHS = {
    "/login",
    "/setup",
    "/logout",
    "/favicon.ico",
    "/api/health",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


class RequireLoginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path in PUBLIC_PATHS:
            return await call_next(request)
        if db.user_count() == 0:
            if path != "/setup":
                return RedirectResponse("/setup", status_code=303)
            return await call_next(request)
        if path == "/setup":
            return RedirectResponse("/login", status_code=303)
        user = current_user(request)
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"ok": False, "error": "Sign in required."}, status_code=401)
            nxt = request.url.path
            if request.url.query:
                nxt += "?" + request.url.query
            return RedirectResponse("/login?next=" + urlencode({"": nxt})[1:], status_code=303)

        # Read-Only Enforcement on mutating HTTP methods
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # Settings user administration and read-only toggle are allowed for admin
            is_admin_action = path.startswith("/settings/users") or path.startswith("/settings/read-only")
            if is_admin_action:
                if not auth.is_admin(user):
                    if path.startswith("/api/"):
                        return JSONResponse({"ok": False, "error": "Administrator privilege required."}, status_code=403)
                    return flash_redirect(tab_url("settings"), "Administrator privilege required.", "alert")
            else:
                # Case filings, case updates, hearings, deletions, court settings
                if not auth.can_write(user, db.is_read_only_mode()):
                    if path.startswith("/api/"):
                        return JSONResponse(
                            {"ok": False, "error": "Action prohibited: The portal is in Read-Only mode or your account has Read-Only permissions."},
                            status_code=403,
                        )
                    return flash_redirect(
                        tab_url("register"),
                        "Action prohibited: The desk is currently in Read-Only mode or your user account has Read-Only access.",
                        "alert",
                    )

        return await call_next(request)



class ClearFlashMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.cookies.get("hct6_flash") and not request.url.path.startswith("/static"):
            response.delete_cookie("hct6_flash")
            response.delete_cookie("hct6_flash_kind")
        return response


middleware = [
    Middleware(
        SessionMiddleware,
        secret_key=auth.session_secret(),
        max_age=None,
        same_site="lax",
        https_only=False,
    ),
    Middleware(SecurityHeadersMiddleware),
    Middleware(RequireLoginMiddleware),
    Middleware(ClearFlashMiddleware),
]

app = FastAPI(title="High Court Portal", version="1.4.0", middleware=middleware)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")



@app.get("/setup")
async def setup_page(request: Request):
    if db.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "error": "",
            "success": "",
        },
    )


@app.post("/setup")
async def setup_submit(
    request: Request,
    username: str = Form(""),
    email: str = Form(""),
    display_name: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    if db.user_count() > 0:
        return RedirectResponse("/login", status_code=303)

    error = auth.validate_username(username)
    if error:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": error, "success": ""},
        )
    password_error = auth.validate_password(password)
    if password_error:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": password_error, "success": ""},
        )
    if password != confirm_password:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": "Passwords do not match.", "success": ""},
        )

    try:
        user = db.create_user(
            username=username,
            password=password,
            display_name=display_name or username,
            email=email.strip().lower(),
            role="admin",
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"request": request, "error": str(exc), "success": ""},
        )

    request.session["user"] = {"id": user["id"]}
    request.session["boot_id"] = BOOT_ID
    db.record_login_result(user["username"], True)
    return RedirectResponse("/", status_code=303)


@app.get("/login")
async def login_page(request: Request, next: str = ""):
    try:
        request.session.clear()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "next": next or "/",
            "error": "",
        },
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    user = db.get_user(username)
    if not user or not user.get("active"):
        db.record_login_result(username, False)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "next": next or "/",
                "error": "Invalid username/email or password.",
            },
        )

    ok = auth.check_password(password, user["password_salt"], user["password_hash"])
    if not ok:
        db.record_login_result(username, False)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "next": next or "/",
                "error": "Invalid username/email or password.",
            },
        )

    db.record_login_result(username, True)
    request.session["user"] = {"id": user["id"]}
    request.session["boot_id"] = BOOT_ID
    return RedirectResponse(next or "/", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)




def view_home():
    return logic.dashboard()


def view_file():
    cases = logic.all_cases()
    return {
        "catalogs": catalogs(),
        "directory": logic.counsel_directory(cases),
        "suggested_suit": logic.suggest_suit(cases),
        "case": None,
    }


def view_register(q: str, status: str, case_type: str, needs: str):
    cases = logic.all_cases()
    hearings = logic.all_hearings(cases)
    latest = logic.latest_hearing_by_suit(hearings)
    query = (q or "").strip().lower()
    rows = []
    for case in cases:
        last = latest.get(store.norm_suit(case["SUIT NUMBER"]))
        item = dict(case)
        item["last_hearing"] = last
        if query:
            blob = " ".join(
                [
                    case.get("SUIT NUMBER") or "",
                    case.get("PARTIES") or "",
                    case.get("CLAIMANT COUNSEL") or "",
                    case.get("DEFENDANT COUNSEL") or "",
                    case.get("NATURE OF CLAIM") or "",
                ]
            ).lower()
            if query not in blob:
                continue
        if status and (case.get("STATUS") or "").upper() != status.upper():
            continue
        if case_type and (case.get("CASE TYPE") or "").upper() != case_type.upper():
            continue
        if needs == "needs-date":
            if not case.get("is_active"):
                continue
            if last and last.get("next_on"):
                continue
        rows.append(item)
    return {
        "cases": rows,
        "catalogs": catalogs(),
        "q": q,
        "status": status,
        "case_type": case_type,
        "filter": needs,
        "total": len(cases),
    }


def view_case(suit: str):
    cases = logic.all_cases()
    found = logic.cases_by_suit(cases).get(store.norm_suit(suit))
    if not found:
        return None
    hearings = [h for h in logic.all_hearings(cases) if store.norm_suit(h.get("SUIT NUMBER")) == store.norm_suit(suit)]
    return {
        "case": found,
        "hearings": hearings,
        "catalogs": catalogs(),
        "directory": logic.counsel_directory(cases),
    }


def view_hearings(suit: str, date_on: str):
    cases = logic.all_cases()
    hearings = logic.all_hearings(cases)
    if suit:
        hearings = [h for h in hearings if store.norm_suit(h.get("SUIT NUMBER")) == store.norm_suit(suit)]
    target = logic.parse_iso(date_on)
    if target:
        hearings = [h for h in hearings if h.get("hearing_on") == target]
    return {
        "cases": cases,
        "hearings": hearings,
        "catalogs": catalogs(),
        "selected_suit": store.norm_suit(suit),
        "date_on": date_on,
        "draft": {
            "SUIT NUMBER": store.norm_suit(suit),
            "HEARING DATE": date_on or date.today().isoformat(),
        },
    }


def view_cause(date_on: str):
    target = logic.parse_iso(date_on) or date.today()
    cases = logic.all_cases()
    hearings = logic.all_hearings(cases)
    return {
        "rows": logic.cause_list(target, cases, hearings),
        "date_on": target.isoformat(),
        "target": target,
        "settings": store.load_settings(),
    }


def view_counsel(q: str):
    directory = logic.counsel_directory()
    query = (q or "").strip().lower()
    if query:
        directory = [d for d in directory if query in d["name"].lower() or query in d["phone"] or query in d["email"].lower()]
    return {"directory": directory, "q": q}


def view_counsel_cases(name: str):
    return logic.cases_for_counsel(name)


def view_insights(year: int, quarter: str):
    today = date.today()
    year = year or today.year
    quarter = quarter or f"Q{(today.month - 1) // 3 + 1}"
    return {
        "stats": logic.insights(year, quarter),
        "catalogs": catalogs(),
        "cases": logic.all_cases(),
        "year": year,
        "quarter": quarter,
    }


def view_quarter(year: int, quarter: str):
    today = date.today()
    year = year or today.year
    quarter = quarter or f"Q{(today.month - 1) // 3 + 1}"
    stats = logic.insights(year, quarter)
    cases = logic.all_cases()
    hearings = [
        h
        for h in logic.all_hearings(cases)
        if logic.in_range(h.get("HEARING DATE"), stats["start"], stats["end"])
        and store.as_text(h.get("OUTCOME")).upper() in {"JUDGMENT DELIVERED", "RULING DELIVERED"}
    ]
    return {"stats": stats, "settings": store.load_settings(), "hearings": hearings, "catalogs": catalogs()}


def view_settings():
    return {
        "catalogs": catalogs(),
        "workbook": store.workbook_path(),
        "pin_set": auth.pin_is_set(),
        "audit": db.list_audit(15),
        "users": db.list_users(),
        "read_only_mode": db.is_read_only_mode(),
    }



def view_delete(kind: str, suit: str, hearing_id: str, challenge: str = ""):
    kind = (kind or "case").strip().lower()
    record = None
    label = ""
    if kind == "hearing":
        try:
            record = db.get_hearing(int(hearing_id))
        except (TypeError, ValueError):
            record = None
        if record:
            label = f"Hearing #{record.get('id')} · {record.get('SUIT NUMBER')} · {record.get('HEARING DATE')}"
    else:
        kind = "case"
        cases = logic.cases_by_suit()
        record = cases.get(store.norm_suit(suit))
        if record:
            label = f"{record.get('SUIT NUMBER')} · {record.get('PARTIES')}"
    return {
        "kind": kind,
        "suit": store.norm_suit(suit) or (record or {}).get("SUIT NUMBER", ""),
        "hearing_id": hearing_id,
        "record": record,
        "label": label,
        "pin_set": auth.pin_is_set(),
        "challenge": challenge,
        "otp": None,
        "locked": auth.locked_until(),
    }


@app.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    tab: str = "home",
    q: str = "",
    status: str = "",
    case_type: str = "",
    filter: str = "",
    suit: str = "",
    date_on: str = "",
    year: int = 0,
    quarter: str = "",
    kind: str = "",
    id: str = "",
    challenge: str = "",
    name: str = "",
):
    tab = (tab or "home").strip().lower()
    try:
        if tab == "delete":
            return render(request, "delete.html", "register", view_delete(kind, suit, id, challenge))
        if tab == "file":
            return render(request, "file_case.html", "file", view_file())
        if tab == "register":
            return render(request, "register.html", "register", view_register(q, status, case_type, filter))
        if tab == "case":
            data = view_case(suit)
            if not data:
                return flash_redirect(tab_url("register"), f"Suit {suit} was not found.", "alert")
            return render(request, "case_detail.html", "register", data)
        if tab == "hearings":
            return render(request, "hearing.html", "hearings", view_hearings(suit, date_on))
        if tab in {"cause-list", "causelist"}:
            return render(request, "cause_list.html", "cause-list", view_cause(date_on))
        if tab == "counsel":
            if name:
                return render(request, "counsel_cases.html", "counsel", view_counsel_cases(name))
            return render(request, "counsel.html", "counsel", view_counsel(q))
        if tab == "insights":
            return render(request, "insights.html", "insights", view_insights(year, quarter))
        if tab == "quarter":
            return render(request, "quarter.html", "quarter", view_quarter(year, quarter))
        if tab == "settings":
            return render(request, "settings.html", "settings", view_settings())
        return render(request, "dashboard.html", "home", view_home())
    except Exception as exc:
        return render(request, "error.html", tab or "home", {"error": str(exc)})


@app.get("/file")
async def file_redirect():
    return RedirectResponse(tab_url("file"), status_code=303)


@app.get("/register")
async def register_redirect(q: str = "", status: str = "", case_type: str = "", filter: str = ""):
    return RedirectResponse(tab_url("register", q=q, status=status, case_type=case_type, filter=filter), status_code=303)


@app.get("/register/{suit:path}")
async def case_redirect(suit: str):
    return RedirectResponse(tab_url("case", suit=suit), status_code=303)


@app.get("/hearings")
async def hearings_redirect(suit: str = "", date_on: str = ""):
    return RedirectResponse(tab_url("hearings", suit=suit, date_on=date_on), status_code=303)


@app.get("/cause-list")
async def cause_redirect(date_on: str = ""):
    return RedirectResponse(tab_url("cause-list", date_on=date_on), status_code=303)


@app.get("/counsel")
async def counsel_redirect(q: str = ""):
    return RedirectResponse(tab_url("counsel", q=q), status_code=303)


@app.get("/insights")
async def insights_redirect(year: int = 0, quarter: str = ""):
    return RedirectResponse(tab_url("insights", year=year, quarter=quarter), status_code=303)


@app.get("/quarter")
async def quarter_redirect(year: int = 0, quarter: str = ""):
    return RedirectResponse(tab_url("quarter", year=year, quarter=quarter), status_code=303)


@app.get("/settings")
async def settings_redirect():
    return RedirectResponse(tab_url("settings"), status_code=303)


@app.post("/file")
async def file_submit(request: Request):
    form = await request.form()
    payload = logic.form_payload(form)
    cases = logic.all_cases()
    errors = logic.validate_case(payload, cases)
    if errors:
        data = view_file()
        data["case"] = payload
        data["errors"] = errors
        data["suggested_suit"] = payload.get("SUIT NUMBER") or data["suggested_suit"]
        return render(request, "file_case.html", "file", data)
    try:
        db.upsert_case(payload)
    except Exception as exc:
        return flash_redirect(tab_url("file"), f"Could not file the matter: {exc}", "alert")
    excel_error = store.push_case(payload)
    mention_on = store.as_text(form.get("list_for"))
    if mention_on:
        listing = {
            "SUIT NUMBER": payload.get("SUIT NUMBER"),
            "HEARING DATE": mention_on,
            "PURPOSE": store.as_text(form.get("first_purpose")) or "FOR MENTION",
            "NEXT ACTION": store.as_text(form.get("first_purpose")) or "MENTION",
            "NEXT DATE": mention_on,
        }
        try:
            db.add_hearing(listing)
            listing_error = store.push_hearing(listing)
            if listing_error and not excel_error:
                excel_error = listing_error
        except Exception as exc:
            return flash_redirect(
                tab_url("case", suit=store.norm_suit(payload.get("SUIT NUMBER"))),
                f"Matter filed, but the first listing could not be written: {exc}",
                "warn",
            )
    note = f"{store.norm_suit(payload.get('SUIT NUMBER'))} has been filed and written to the Excel case register."
    if excel_error:
        note = f"{store.norm_suit(payload.get('SUIT NUMBER'))} was filed on the desk, but Excel could not be updated: {excel_error}"
    return flash_redirect(tab_url("case", suit=store.norm_suit(payload.get("SUIT NUMBER"))), note, "warn" if excel_error else "ok")


@app.post("/register/{suit:path}")
async def case_update(request: Request, suit: str):
    form = await request.form()
    payload = logic.form_payload(form)
    cases = logic.all_cases()
    errors = logic.validate_case(payload, cases, updating=suit)
    if errors:
        data = view_case(suit) or {"case": payload, "hearings": [], "catalogs": catalogs(), "directory": []}
        data["case"] = {**(data.get("case") or {}), **payload}
        data["errors"] = errors
        return render(request, "case_detail.html", "register", data)
    try:
        db.update_case_fields(suit, payload)
    except Exception as exc:
        return flash_redirect(tab_url("case", suit=suit), str(exc), "alert")
    excel_error = store.push_case(payload)
    note = "Case register updated in the desk and in Excel."
    if excel_error:
        note = f"Desk updated, but Excel could not be written: {excel_error}"
    return flash_redirect(tab_url("case", suit=store.norm_suit(payload.get("SUIT NUMBER") or suit)), note, "warn" if excel_error else "ok")


@app.post("/hearings")
async def hearings_submit(request: Request):
    form = await request.form()
    payload = logic.form_payload(form)
    cases = logic.all_cases()
    errors = logic.validate_hearing(payload, cases)
    if errors:
        data = view_hearings(payload.get("SUIT NUMBER") or "", payload.get("HEARING DATE") or "")
        data["draft"] = payload
        data["errors"] = errors
        return render(request, "hearing.html", "hearings", data)
    try:
        db.add_hearing(payload)
        conclude_payload = None
        if form.get("auto_conclude") != "no" and (
            form.get("auto_conclude") == "yes" or logic.should_conclude(payload)
        ):
            conclude_payload = {
                "STATUS": "CONCLUDED",
                "DATE CONCLUDED": payload.get("DATE CONCLUDED") or payload.get("HEARING DATE"),
                "DETAILS": payload.get("OUTCOME") or payload.get("NEXT ACTION") or "CONCLUDED",
            }
            db.update_case_fields(payload["SUIT NUMBER"], conclude_payload)
    except Exception as exc:
        return flash_redirect(tab_url("hearings"), str(exc), "alert")
    excel_error = store.push_hearing(payload)
    if conclude_payload:
        case = logic.cases_by_suit().get(store.norm_suit(payload["SUIT NUMBER"]))
        if case:
            store.push_case({**case, **conclude_payload})
    msg = f"Hearing recorded for {store.norm_suit(payload.get('SUIT NUMBER'))} and written to the Excel hearing log."
    if logic.should_conclude(payload):
        msg += " Matter marked concluded on the register."
    if excel_error:
        msg = f"Hearing saved on the desk, but Excel could not be updated: {excel_error}"
    return flash_redirect(tab_url("hearings"), msg, "warn" if excel_error else "ok")


@app.post("/settings")
async def settings_save(
    court_name: str = Form(""),
    court_title: str = Form(""),
    judge_name: str = Form(""),
    division: str = Form(""),
    court_level: str = Form(""),
    workbook_path: str = Form(""),
):
    settings = store.load_settings()
    settings.update(
        {
            "court_name": court_name or settings["court_name"],
            "court_title": court_title or settings["court_title"],
            "judge_name": judge_name or settings["judge_name"],
            "division": division or settings["division"],
            "court_level": court_level or settings["court_level"],
        }
    )
    if workbook_path:
        try:
            store.set_workbook(workbook_path)
            settings["workbook_path"] = workbook_path
        except Exception as exc:
            return flash_redirect(tab_url("settings"), str(exc), "alert")
    store.save_settings(settings)
    return flash_redirect(tab_url("settings"), "Settings saved. The Excel backend was not restructured.")


@app.post("/quarter/particulars")
async def quarter_particulars(
    judge_name: str = Form(""),
    court_level: str = Form(""),
    division: str = Form(""),
    court_name: str = Form(""),
    year: int = Form(0),
    quarter: str = Form(""),
):
    settings = store.load_settings()
    if judge_name:
        settings["judge_name"] = judge_name
    if court_level:
        settings["court_level"] = court_level
    if division:
        settings["division"] = division
    if court_name:
        settings["court_name"] = court_name
    store.save_settings(settings)
    return flash_redirect(
        tab_url("quarter", year=year, quarter=quarter),
        "Judge particulars updated. They now appear on this return and on printed copies.",
    )


@app.post("/settings/pin")
async def settings_pin(
    new_pin: str = Form(""),
    confirm_pin: str = Form(""),
    current_pin: str = Form(""),
):
    if new_pin != confirm_pin:
        return flash_redirect(tab_url("settings"), "The new PIN and confirmation do not match.", "alert")
    error = auth.set_pin(new_pin, current_pin)
    if error:
        return flash_redirect(tab_url("settings"), error, "alert")
    return flash_redirect(tab_url("settings"), "Registrar PIN saved. Deletion now requires this PIN plus a one-time code.")


@app.post("/settings/read-only")
async def settings_read_only(
    read_only_mode: str = Form("0"),
):
    enabled = read_only_mode in {"1", "true", "yes", "on"}
    db.set_read_only_mode(enabled)
    db.add_audit("settings", "security", "portal", f"Read-only mode set to {enabled}")
    status_str = "ENABLED (All data modifications blocked)" if enabled else "DISABLED (Normal desk operations)"
    return flash_redirect(tab_url("settings"), f"Portal Read-Only mode is now {status_str}.")


@app.post("/settings/users/create")
async def settings_user_create(
    username: str = Form(""),
    email: str = Form(""),
    display_name: str = Form(""),
    password: str = Form(""),
    role: str = Form("readonly"),
):
    name_err = auth.validate_username(username)
    if name_err:
        return flash_redirect(tab_url("settings"), name_err, "alert")
    pass_err = auth.validate_password(password)
    if pass_err:
        return flash_redirect(tab_url("settings"), pass_err, "alert")
    try:
        user = db.create_user(
            username=username,
            password=password,
            display_name=display_name or username,
            email=email,
            role=role,
        )
        db.add_audit("user_create", role, user["username"], f"Officer {user['username']} ({role}) created")
        return flash_redirect(tab_url("settings"), f"Officer account '{user['username']}' created successfully.")
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")


@app.post("/settings/users/password")
async def settings_user_password(
    user_id: int = Form(0),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    if new_password != confirm_password:
        return flash_redirect(tab_url("settings"), "New password and confirmation do not match.", "alert")
    err = auth.validate_password(new_password)
    if err:
        return flash_redirect(tab_url("settings"), err, "alert")
    try:
        user = db.get_user_by_id(user_id)
        if not user:
            return flash_redirect(tab_url("settings"), "User account not found.", "alert")
        db.set_user_password(user_id, new_password)
        db.add_audit("user_password", "reset", user["username"], f"Password updated for {user['username']}")
        return flash_redirect(tab_url("settings"), f"Login password for '{user['display_name']}' (@{user['username']}) updated successfully.")
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")


@app.post("/settings/users/role")
async def settings_user_role(
    user_id: int = Form(0),
    role: str = Form("readonly"),
):
    try:
        db.update_user_role(user_id, role)
        db.add_audit("user_role", role, str(user_id), f"User #{user_id} role changed to {role}")
        return flash_redirect(tab_url("settings"), f"Officer role updated to '{role}'.")
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")


@app.post("/settings/users/edit")
async def settings_user_edit(
    user_id: int = Form(0),
    display_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("readonly"),
):
    try:
        user = db.get_user_by_id(user_id)
        if not user:
            return flash_redirect(tab_url("settings"), "Officer account not found.", "alert")
        db.update_user_profile(user_id, display_name or user["username"], email)
        if role in {"admin", "registrar", "readonly"}:
            db.update_user_role(user_id, role)
        db.add_audit("user_edit", "profile", user["username"], f"Profile updated for {user['username']}")
        return flash_redirect(tab_url("settings"), f"Officer particulars for '{user['username']}' updated.")
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")


@app.post("/settings/users/active")
async def settings_user_active(
    user_id: int = Form(0),
    active: int = Form(1),
):
    try:
        is_active = bool(active)
        db.set_user_active(user_id, is_active)
        status_text = "activated" if is_active else "deactivated"
        db.add_audit("user_status", status_text, str(user_id), f"User #{user_id} {status_text}")
        return flash_redirect(tab_url("settings"), f"Officer account {status_text}.")
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")


@app.post("/settings/users/delete")
async def settings_user_delete(
    user_id: int = Form(0),
):
    try:
        user = db.get_user_by_id(user_id)
        name = user["username"] if user else str(user_id)
        db.delete_user(user_id)
        db.add_audit("user_delete", "user", name, f"User {name} deleted")
        return flash_redirect(tab_url("settings"), f"Officer account '{name}' deleted.")
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")



@app.post("/delete/challenge")
async def delete_challenge(
    request: Request,
    kind: str = Form("case"),
    suit: str = Form(""),
    hearing_id: str = Form(""),
):
    if not auth.pin_is_set():
        return flash_redirect(tab_url("settings"), "Set a registrar PIN in Settings before any record can be deleted.", "alert")
    if auth.locked_until():
        return flash_redirect(tab_url("delete", kind=kind, suit=suit, id=hearing_id), "Too many wrong PIN attempts. Try again later.", "alert")
    data = view_delete(kind, suit, hearing_id)
    if not data["record"]:
        return flash_redirect(tab_url("register"), "That record was not found.", "alert")
    code = auth.make_otp()
    expires = (datetime.now() + timedelta(minutes=auth.OTP_MINUTES)).isoformat(timespec="seconds")
    target = hearing_id if kind == "hearing" else store.norm_suit(suit)
    challenge_id = db.add_challenge(kind, target, auth.hash_otp(code), expires)
    db.add_audit("challenge", kind, str(target), "One-time delete code issued")
    extra = view_delete(kind, suit, hearing_id, challenge_id)
    extra["otp"] = code
    extra["challenge"] = challenge_id
    return render(request, "delete.html", "register", extra)


@app.post("/delete/confirm")
async def delete_confirm(
    kind: str = Form("case"),
    suit: str = Form(""),
    hearing_id: str = Form(""),
    challenge: str = Form(""),
    pin: str = Form(""),
    otp: str = Form(""),
    confirm_text: str = Form(""),
):
    if auth.locked_until():
        return flash_redirect(tab_url("delete", kind=kind, suit=suit, id=hearing_id), "Deletion is locked after too many wrong PIN attempts.", "alert")
    row = db.get_challenge(challenge)
    if not row or row.get("used"):
        return flash_redirect(tab_url("delete", kind=kind, suit=suit, id=hearing_id), "The delete request expired. Start again.", "alert")
    try:
        expires = datetime.fromisoformat(row["expires"])
    except (TypeError, ValueError):
        expires = datetime.min
    if expires < datetime.now():
        return flash_redirect(tab_url("delete", kind=kind, suit=suit, id=hearing_id), "The one-time code has expired. Start again.", "alert")
    if not hmac.compare_digest(auth.hash_otp(otp.strip()), row["otp_hash"]):
        return flash_redirect(tab_url("delete", kind=kind, suit=suit, id=hearing_id, challenge=challenge), "The one-time code is wrong.", "alert")
    if not auth.verify_pin(pin):
        until = auth.locked_until()
        msg = "The registrar PIN is wrong."
        if until:
            msg += f" Locked until {until.strftime('%H:%M')}."
        return flash_redirect(tab_url("delete", kind=kind, suit=suit, id=hearing_id, challenge=challenge), msg, "alert")
    expected = store.norm_suit(suit) if kind != "hearing" else store.norm_suit(suit or "")
    if store.norm_suit(confirm_text) != expected:
        return flash_redirect(
            tab_url("delete", kind=kind, suit=suit, id=hearing_id, challenge=challenge),
            "Type the suit number exactly to confirm this is not a mistake.",
            "alert",
        )
    try:
        if kind == "hearing":
            snapshot = db.delete_hearing(int(hearing_id))
            try:
                store.delete_hearing_row(snapshot.get("SUIT NUMBER"), snapshot.get("HEARING DATE"))
            except Exception:
                pass
            target = f"{snapshot.get('SUIT NUMBER')} #{hearing_id}"
            dest = tab_url("hearings")
            msg = f"Hearing on {snapshot.get('HEARING DATE')} for {snapshot.get('SUIT NUMBER')} has been deleted from the desk and Excel."
        else:
            snapshot = db.delete_case(suit)
            try:
                store.delete_case_row(suit)
            except Exception:
                pass
            target = snapshot.get("SUIT NUMBER")
            dest = tab_url("register")
            msg = f"{target} and its hearing log have been deleted from the desk and Excel."
    except Exception as exc:
        return flash_redirect(tab_url("register"), str(exc), "alert")
    db.mark_challenge_used(challenge)
    db.add_audit("delete", kind, str(target), str(snapshot))
    return flash_redirect(dest, msg)


@app.post("/export")
async def export_workbook():
    try:
        path = store.export_rows(db.list_cases(), db.list_hearings())
    except Exception as exc:
        return flash_redirect(tab_url("settings"), str(exc), "alert")
    return flash_redirect(tab_url("settings"), f"Exported without changing the original backend: {path}")


@app.get("/api/health")
async def api_health():
    path = store.workbook_path()
    return {
        "ok": True,
        "backend": str(path),
        "backend_exists": path.exists(),
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/lookups")
async def api_lookups():
    return catalogs()


@app.get("/api/cases")
async def api_cases():
    return {"cases": logic.all_cases()}


@app.post("/api/cases")
async def api_file_case(request: Request):
    payload = await request.json()
    errors = logic.validate_case(payload, logic.all_cases())
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    result = db.upsert_case(payload)
    excel_error = store.push_case(payload)
    if excel_error:
        result["excel_error"] = excel_error
    return {"ok": True, **result}


@app.get("/api/hearings")
async def api_hearings():
    return {"hearings": logic.all_hearings()}


@app.post("/api/hearings")
async def api_add_hearing(request: Request):
    payload = await request.json()
    errors = logic.validate_hearing(payload, logic.all_cases())
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    result = db.add_hearing(payload)
    if logic.should_conclude(payload):
        db.update_case_fields(
            payload["SUIT NUMBER"],
            {
                "STATUS": "CONCLUDED",
                "DATE CONCLUDED": payload.get("DATE CONCLUDED") or payload.get("HEARING DATE"),
            },
        )
        result["concluded"] = True
    return {"ok": True, **result}


@app.get("/api/cause-list")
async def api_cause_list(date_on: str = ""):
    target = logic.parse_iso(date_on) or date.today()
    return {"date": target.isoformat(), "items": logic.cause_list(target)}


@app.get("/api/counsel")
async def api_counsel():
    return {"counsel": logic.counsel_directory()}


@app.get("/api/insights")
async def api_insights(year: int = 0, quarter: str = ""):
    today = date.today()
    return logic.insights(year or today.year, quarter or f"Q{(today.month - 1) // 3 + 1}")
