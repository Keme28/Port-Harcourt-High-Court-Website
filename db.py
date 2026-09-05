"""Operational store. The Excel workbook is imported and exported, never treated as a scratch file."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import store
from store import (
    DATA_DIR,
    as_text,
    ensure_layout,
    norm_suit,
    read_cases,
    read_hearings,
    load_settings,
    save_settings,
)

DB_PATH = DATA_DIR / "portal.sqlite"

CASE_FIELDS = [
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

HEARING_FIELDS = [
    "SUIT NUMBER",
    "HEARING DATE",
    "PURPOSE",
    "OUTCOME",
    "NEXT ACTION",
    "NEXT DATE",
    "RULING",
    "DATE CONCLUDED",
]


def _connect() -> sqlite3.Connection:
    ensure_layout()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cases (
            suit TEXT PRIMARY KEY,
            parties TEXT,
            case_type TEXT,
            nature TEXT,
            particulars TEXT,
            claimant_counsel TEXT,
            phone TEXT,
            email TEXT,
            defendant_counsel TEXT,
            phone2 TEXT,
            email2 TEXT,
            date_filed TEXT,
            date_assigned TEXT,
            date_trial TEXT,
            status TEXT,
            details TEXT,
            date_concluded TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hearings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suit TEXT,
            hearing_date TEXT,
            purpose TEXT,
            outcome TEXT,
            next_action TEXT,
            next_date TEXT,
            ruling TEXT,
            date_concluded TEXT
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delete_challenges (
            id TEXT PRIMARY KEY,
            kind TEXT,
            target TEXT,
            otp_hash TEXT,
            expires TEXT,
            used INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created TEXT,
            action TEXT,
            kind TEXT,
            target TEXT,
            detail TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            google_id TEXT UNIQUE,
            display_name TEXT,
            password_salt TEXT,
            password_hash TEXT,
            role TEXT NOT NULL DEFAULT 'readonly',
            auth_provider TEXT NOT NULL DEFAULT 'local',
            active INTEGER NOT NULL DEFAULT 1,
            created TEXT,
            last_login TEXT,
            fail_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    _migrate_schema(conn)
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA table_info(users)")
    existing_cols = {row["name"] for row in cursor.fetchall()}
    if not existing_cols:
        return
    if "email" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "google_id" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
    if "auth_provider" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'local'")
    if "last_login" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
    conn.commit()


def _case_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "SUIT NUMBER": row["suit"],
        "PARTIES": row["parties"] or "",
        "CASE TYPE": row["case_type"] or "",
        "NATURE OF CLAIM": row["nature"] or "",
        "PARTICULARS": row["particulars"] or "",
        "CLAIMANT COUNSEL": row["claimant_counsel"] or "",
        "PHONE NUMBER": row["phone"] or "",
        "E-MAIL": row["email"] or "",
        "DEFENDANT COUNSEL": row["defendant_counsel"] or "",
        "PHONE NUMBER2": row["phone2"] or "",
        "E-MAIL2": row["email2"] or "",
        "DATE FILED": row["date_filed"] or "",
        "DATE ASSIGNED": row["date_assigned"] or "",
        "DATE TRIAL COMMENCED": row["date_trial"] or "",
        "STATUS": row["status"] or "",
        "DETAILS": row["details"] or "",
        "DATE CONCLUDED": row["date_concluded"] or "",
    }


def _hearing_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "row": row["id"],
        "SUIT NUMBER": row["suit"],
        "HEARING DATE": row["hearing_date"] or "",
        "PURPOSE": row["purpose"] or "",
        "OUTCOME": row["outcome"] or "",
        "NEXT ACTION": row["next_action"] or "",
        "NEXT DATE": row["next_date"] or "",
        "RULING": row["ruling"] or "",
        "DATE CONCLUDED": row["date_concluded"] or "",
    }


def _is_formula(value: Any) -> bool:
    return as_text(value).startswith("=")


def _clean(value: Any) -> str:
    text = as_text(value)
    return "" if text.startswith("=") else text


def import_from_excel(force: bool = False) -> dict[str, int]:
    conn = _connect()
    imported = conn.execute("SELECT value FROM meta WHERE key='imported'").fetchone()
    if imported and not force:
        conn.close()
        return {"cases": 0, "hearings": 0, "skipped": True}
    # The Excel template is empty of live rows. Opening it on every first
    # visit is slow enough to make Pinokio report "not connected".
    if not force:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('imported','1')")
        conn.commit()
        conn.close()
        return {"cases": 0, "hearings": 0, "skipped": True}
    cases = read_cases()
    hearings = read_hearings()
    if force:
        conn.execute("DELETE FROM cases")
        conn.execute("DELETE FROM hearings")
    for case in cases:
        upsert_case(case, conn=conn)
    for hearing in hearings:
        if _is_formula(hearing.get("SUIT NUMBER")):
            continue
        add_hearing(hearing, conn=conn)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('imported','1')")
    conn.commit()
    counts = {
        "cases": conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
        "hearings": conn.execute("SELECT COUNT(*) FROM hearings").fetchone()[0],
        "skipped": False,
    }
    conn.close()
    return counts


def list_cases() -> list[dict[str, Any]]:
    import_from_excel()
    conn = _connect()
    rows = conn.execute("SELECT * FROM cases ORDER BY date_filed DESC, suit").fetchall()
    conn.close()
    return [_case_from_row(r) for r in rows]


def list_hearings() -> list[dict[str, Any]]:
    import_from_excel()
    conn = _connect()
    rows = conn.execute("SELECT * FROM hearings ORDER BY hearing_date DESC, id DESC").fetchall()
    conn.close()
    return [_hearing_from_row(r) for r in rows]


def upsert_case(payload: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    own = conn is None
    conn = conn or _connect()
    suit = norm_suit(payload.get("SUIT NUMBER"))
    conn.execute(
        """
        INSERT INTO cases (
            suit, parties, case_type, nature, particulars, claimant_counsel, phone, email,
            defendant_counsel, phone2, email2, date_filed, date_assigned, date_trial,
            status, details, date_concluded
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(suit) DO UPDATE SET
            parties=excluded.parties,
            case_type=excluded.case_type,
            nature=excluded.nature,
            particulars=excluded.particulars,
            claimant_counsel=excluded.claimant_counsel,
            phone=excluded.phone,
            email=excluded.email,
            defendant_counsel=excluded.defendant_counsel,
            phone2=excluded.phone2,
            email2=excluded.email2,
            date_filed=excluded.date_filed,
            date_assigned=excluded.date_assigned,
            date_trial=excluded.date_trial,
            status=excluded.status,
            details=excluded.details,
            date_concluded=excluded.date_concluded
        """,
        (
            suit,
            _clean(payload.get("PARTIES")),
            _clean(payload.get("CASE TYPE")),
            _clean(payload.get("NATURE OF CLAIM")),
            _clean(payload.get("PARTICULARS")),
            _clean(payload.get("CLAIMANT COUNSEL")),
            _clean(payload.get("PHONE NUMBER")),
            _clean(payload.get("E-MAIL")),
            _clean(payload.get("DEFENDANT COUNSEL")),
            _clean(payload.get("PHONE NUMBER2")),
            _clean(payload.get("E-MAIL2")),
            _clean(payload.get("DATE FILED")),
            _clean(payload.get("DATE ASSIGNED")),
            _clean(payload.get("DATE TRIAL COMMENCED")),
            _clean(payload.get("STATUS")) or "ACTIVE",
            _clean(payload.get("DETAILS")),
            _clean(payload.get("DATE CONCLUDED")),
        ),
    )
    if own:
        conn.commit()
        conn.close()
    return {"ok": True, "suit": suit}


def update_case_fields(suit: str, payload: dict[str, Any]) -> dict[str, Any]:
    conn = _connect()
    current = conn.execute("SELECT * FROM cases WHERE suit=?", (norm_suit(suit),)).fetchone()
    if not current:
        conn.close()
        raise KeyError(f"Suit {suit} was not found in the case register.")
    merged = _case_from_row(current)
    for key in CASE_FIELDS:
        if key in payload:
            merged[key] = payload[key]
    result = upsert_case(merged, conn=conn)
    conn.commit()
    conn.close()
    return result


def add_hearing(payload: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    own = conn is None
    conn = conn or _connect()
    suit = norm_suit(payload.get("SUIT NUMBER"))
    cur = conn.execute(
        """
        INSERT INTO hearings (suit, hearing_date, purpose, outcome, next_action, next_date, ruling, date_concluded)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            suit,
            _clean(payload.get("HEARING DATE")),
            _clean(payload.get("PURPOSE")),
            _clean(payload.get("OUTCOME")),
            _clean(payload.get("NEXT ACTION")),
            _clean(payload.get("NEXT DATE")),
            _clean(payload.get("RULING")),
            _clean(payload.get("DATE CONCLUDED")),
        ),
    )
    row_id = cur.lastrowid
    if own:
        conn.commit()
        conn.close()
    return {"ok": True, "row": row_id, "suit": suit}


def get_hearing(hearing_id: int) -> dict[str, Any] | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM hearings WHERE id=?", (hearing_id,)).fetchone()
    conn.close()
    return _hearing_from_row(row) if row else None


def delete_case(suit: str) -> dict[str, Any]:
    conn = _connect()
    needle = norm_suit(suit)
    current = conn.execute("SELECT * FROM cases WHERE suit=?", (needle,)).fetchone()
    if not current:
        conn.close()
        raise KeyError(f"Suit {needle} was not found.")
    snapshot = _case_from_row(current)
    conn.execute("DELETE FROM hearings WHERE suit=?", (needle,))
    conn.execute("DELETE FROM cases WHERE suit=?", (needle,))
    conn.commit()
    conn.close()
    return snapshot


def delete_hearing(hearing_id: int) -> dict[str, Any]:
    conn = _connect()
    row = conn.execute("SELECT * FROM hearings WHERE id=?", (hearing_id,)).fetchone()
    if not row:
        conn.close()
        raise KeyError(f"Hearing {hearing_id} was not found.")
    snapshot = _hearing_from_row(row)
    conn.execute("DELETE FROM hearings WHERE id=?", (hearing_id,))
    conn.commit()
    conn.close()
    return snapshot


def add_challenge(kind: str, target: str, otp_hash: str, expires: str) -> str:
    conn = _connect()
    challenge_id = secrets.token_hex(12)
    conn.execute(
        "INSERT INTO delete_challenges(id, kind, target, otp_hash, expires, used) VALUES (?,?,?,?,?,0)",
        (challenge_id, kind, target, otp_hash, expires),
    )
    conn.commit()
    conn.close()
    return challenge_id


def get_challenge(challenge_id: str) -> dict[str, Any] | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM delete_challenges WHERE id=?", (challenge_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def mark_challenge_used(challenge_id: str) -> None:
    conn = _connect()
    conn.execute("UPDATE delete_challenges SET used=1 WHERE id=?", (challenge_id,))
    conn.commit()
    conn.close()


def add_audit(action: str, kind: str, target: str, detail: str = "") -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO audit_log(created, action, kind, target, detail) VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), action, kind, target, detail),
    )
    conn.commit()
    conn.close()


def list_audit(limit: int = 20) -> list[dict[str, Any]]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _user_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    keys = row.keys()
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"] or "" if "email" in keys else "",
        "google_id": row["google_id"] or "" if "google_id" in keys else "",
        "display_name": row["display_name"] or row["username"],
        "role": row["role"] or "readonly",
        "auth_provider": row["auth_provider"] if "auth_provider" in keys else "local",
        "active": bool(row["active"]),
        "created": row["created"] if "created" in keys else "",
        "last_login": row["last_login"] if "last_login" in keys else "",
        "password_salt": row["password_salt"] if "password_salt" in keys else "",
        "password_hash": row["password_hash"] if "password_hash" in keys else "",
        "fail_count": row["fail_count"] if "fail_count" in keys else 0,
    }


def user_count() -> int:
    conn = _connect()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return int(count)


def get_user(identifier: str) -> dict[str, Any] | None:
    cleaned = (identifier or "").strip().lower()
    if not cleaned:
        return None
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM users WHERE lower(username)=? OR lower(email)=?",
        (cleaned, cleaned),
    ).fetchone()
    conn.close()
    return _user_from_row(row)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return _user_from_row(row)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    cleaned = (email or "").strip().lower()
    if not cleaned:
        return None
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE lower(email)=?", (cleaned,)).fetchone()
    conn.close()
    return _user_from_row(row)


def get_user_by_google_id(google_id: str) -> dict[str, Any] | None:
    cleaned = (google_id or "").strip()
    if not cleaned:
        return None
    conn = _connect()
    row = conn.execute("SELECT * FROM users WHERE google_id=?", (cleaned,)).fetchone()
    conn.close()
    return _user_from_row(row)


def list_users() -> list[dict[str, Any]]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM users ORDER BY display_name, username").fetchall()
    conn.close()
    users = []
    for row in rows:
        item = _user_from_row(row)
        if item:
            item.pop("password_salt", None)
            item.pop("password_hash", None)
            users.append(item)
    return users


def create_user(
    username: str,
    password: str,
    display_name: str,
    email: str = "",
    role: str = "readonly",
) -> dict[str, Any]:
    import auth

    username = (username or "").strip().lower()
    email = (email or "").strip().lower()
    role = "admin" if user_count() == 0 else (role if role in {"admin", "registrar", "readonly"} else "readonly")
    salt, digest = auth.hash_password(password) if password else ("", "")
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO users(username, email, display_name, password_salt, password_hash, role, auth_provider, active, created, fail_count)
            VALUES (?,?,?,?,?,?,'local',1,?,0)
            """,
            (username, email or None, (display_name or username).strip(), salt, digest, role, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise ValueError("That username or email is already registered.") from exc
    conn.close()
    return {"id": user_id, "username": username, "email": email, "display_name": display_name or username, "role": role}


def upsert_google_user(
    google_id: str,
    email: str,
    display_name: str,
    default_role: str = "readonly",
) -> dict[str, Any]:
    google_id = (google_id or "").strip()
    email = (email or "").strip().lower()
    display_name = (display_name or "").strip()
    if not google_id or not email:
        raise ValueError("Google profile must have an ID and email.")

    conn = _connect()
    # 1. Try finding user by google_id
    row = conn.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
    now_iso = datetime.now().isoformat(timespec="seconds")
    if row:
        user_id = row["id"]
        conn.execute(
            "UPDATE users SET last_login=?, display_name=coalesce(nullif(?, ''), display_name), email=? WHERE id=?",
            (now_iso, display_name, email, user_id),
        )
        conn.commit()
        conn.close()
        return get_user_by_id(user_id)  # type: ignore

    # 2. Try finding user by email to link Google account
    row = conn.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
    if row:
        user_id = row["id"]
        conn.execute(
            "UPDATE users SET google_id=?, last_login=?, display_name=coalesce(nullif(?, ''), display_name) WHERE id=?",
            (google_id, now_iso, display_name, user_id),
        )
        conn.commit()
        conn.close()
        return get_user_by_id(user_id)  # type: ignore

    # 3. Create new user for this Google account
    username_candidate = email.split("@")[0].lower()
    # Ensure username uniqueness
    candidate = username_candidate
    counter = 1
    while conn.execute("SELECT id FROM users WHERE lower(username)=?", (candidate,)).fetchone():
        candidate = f"{username_candidate}_{counter}"
        counter += 1

    role = "admin" if user_count() == 0 else (default_role if default_role in {"admin", "registrar", "readonly"} else "readonly")
    cur = conn.execute(
        """
        INSERT INTO users(username, email, google_id, display_name, password_salt, password_hash, role, auth_provider, active, created, last_login, fail_count)
        VALUES (?,?,?,?,?,?,?,?,1,?,?,0)
        """,
        (candidate, email, google_id, display_name or candidate, "", "", role, "google", now_iso, now_iso),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return get_user_by_id(user_id)  # type: ignore


def update_user_role(user_id: int, role: str) -> None:
    if role not in {"admin", "registrar", "readonly"}:
        raise ValueError(f"Invalid role: {role}")
    conn = _connect()
    # Protect against demoting the last active admin
    current = conn.execute("SELECT role, active FROM users WHERE id=?", (user_id,)).fetchone()
    if current and current["role"] == "admin" and role != "admin":
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
        if admin_count <= 1:
            conn.close()
            raise ValueError("Cannot demote the last remaining active administrator.")
    conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    conn.commit()
    conn.close()


def set_user_password(user_id: int, password: str) -> None:
    import auth

    salt, digest = auth.hash_password(password)
    conn = _connect()
    conn.execute(
        "UPDATE users SET password_salt=?, password_hash=?, fail_count=0 WHERE id=?",
        (salt, digest, user_id),
    )
    conn.commit()
    conn.close()


def update_user_profile(user_id: int, display_name: str, email: str = "") -> None:
    conn = _connect()
    conn.execute(
        "UPDATE users SET display_name=?, email=? WHERE id=?",
        ((display_name or "").strip(), (email or "").strip().lower(), user_id),
    )
    conn.commit()
    conn.close()



def set_user_active(user_id: int, active: bool) -> None:
    conn = _connect()
    if not active:
        current = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if current and current["role"] == "admin":
            admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if admin_count <= 1:
                conn.close()
                raise ValueError("Cannot deactivate the only active administrator.")
    conn.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
    conn.commit()
    conn.close()


def delete_user(user_id: int) -> None:
    conn = _connect()
    current = conn.execute("SELECT role, active FROM users WHERE id=?", (user_id,)).fetchone()
    if current and current["role"] == "admin" and current["active"]:
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
        if admin_count <= 1:
            conn.close()
            raise ValueError("Cannot delete the only active administrator.")
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


def record_login_result(identifier: str, ok: bool) -> None:
    cleaned = (identifier or "").strip().lower()
    if not cleaned:
        return
    conn = _connect()
    now_iso = datetime.now().isoformat(timespec="seconds")
    if ok:
        conn.execute(
            "UPDATE users SET fail_count=0, last_login=? WHERE lower(username)=? OR lower(email)=?",
            (now_iso, cleaned, cleaned),
        )
    else:
        conn.execute(
            "UPDATE users SET fail_count=fail_count+1 WHERE lower(username)=? OR lower(email)=?",
            (cleaned, cleaned),
        )
    conn.commit()
    conn.close()


def is_read_only_mode() -> bool:
    conn = _connect()
    row = conn.execute("SELECT value FROM meta WHERE key='read_only_mode'").fetchone()
    conn.close()
    if row and row["value"] == "1":
        return True
    settings = store.load_settings()
    return bool(settings.get("read_only_mode"))


def set_read_only_mode(enabled: bool) -> None:
    conn = _connect()
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('read_only_mode', ?)", ("1" if enabled else "0",))
    conn.commit()
    conn.close()
    settings = store.load_settings()
    settings["read_only_mode"] = enabled
    store.save_settings(settings)
