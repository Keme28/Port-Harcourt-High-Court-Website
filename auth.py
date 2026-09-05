"""Registrar PIN + one-time delete codes. Nothing is deleted with one click."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from store import DATA_DIR, ensure_layout

AUTH_PATH = DATA_DIR / "auth.json"
DEFAULT_PIN = "000000"
OTP_MINUTES = 5
MAX_FAILS = 5
LOCK_MINUTES = 10


def _now() -> datetime:
    return datetime.now()


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()


def ensure_default_pin() -> None:
    data = {}
    if AUTH_PATH.exists():
        try:
            data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    if data.get("pin_hash") and data.get("pin_salt"):
        return
    salt = secrets.token_hex(16)
    _save(
        {
            "pin_salt": salt,
            "pin_hash": _hash_pin(DEFAULT_PIN, salt),
            "fail_count": 0,
            "locked_until": "",
            "default_pin": DEFAULT_PIN,
        }
    )


def _load() -> dict[str, Any]:
    ensure_layout()
    ensure_default_pin()
    if not AUTH_PATH.exists():
        return {}
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(data: dict[str, Any]) -> None:
    ensure_layout()
    AUTH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def pin_is_set() -> bool:
    data = _load()
    return bool(data.get("pin_hash") and data.get("pin_salt"))


def set_pin(new_pin: str, current_pin: str = "") -> str | None:
    pin = (new_pin or "").strip()
    if len(pin) < 6 or not pin.isdigit():
        return "The registrar PIN must be at least 6 digits."
    data = _load()
    if data.get("pin_hash"):
        if not current_pin:
            return "Enter the current registrar PIN to change it."
        if not verify_pin(current_pin):
            return "The current registrar PIN is wrong."
    salt = secrets.token_hex(16)
    data["pin_salt"] = salt
    data["pin_hash"] = _hash_pin(pin, salt)
    data["fail_count"] = 0
    data["locked_until"] = ""
    data["default_pin"] = DEFAULT_PIN if pin == DEFAULT_PIN else ""
    _save(data)
    return None


def locked_until() -> datetime | None:
    raw = _load().get("locked_until") or ""
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if until > _now():
        return until
    return None


def verify_pin(pin: str) -> bool:
    data = _load()
    until = locked_until()
    if until:
        return False
    salt = data.get("pin_salt") or ""
    expected = data.get("pin_hash") or ""
    if not salt or not expected:
        return False
    digest = _hash_pin(pin or "", salt)
    ok = hmac.compare_digest(digest, expected)
    if ok:
        data["fail_count"] = 0
        data["locked_until"] = ""
        _save(data)
        return True
    fails = int(data.get("fail_count") or 0) + 1
    data["fail_count"] = fails
    if fails >= MAX_FAILS:
        data["locked_until"] = (_now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
        data["fail_count"] = 0
    _save(data)
    return False


def make_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def session_secret() -> str:
    data = _load()
    if not data.get("session_secret"):
        data["session_secret"] = secrets.token_hex(32)
        _save(data)
    return data["session_secret"]


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    return salt, _hash_pin(password, salt)


def check_password(password: str, salt: str, expected: str) -> bool:
    if not salt or not expected:
        return False
    return hmac.compare_digest(_hash_pin(password or "", salt), expected)


def validate_password(password: str) -> str | None:
    if len(password or "") < 8:
        return "Password must be at least 8 characters."
    return None


def validate_username(username: str) -> str | None:
    name = (username or "").strip().lower()
    if len(name) < 3:
        return "Username must be at least 3 characters."
    if not all(ch.isalnum() or ch in "._-" for ch in name):
        return "Username may only contain letters, numbers, dots, hyphens and underscores."
    return None


def can_write(user: dict[str, Any] | None, is_global_read_only: bool = False) -> bool:
    if not user:
        return False
    if is_global_read_only:
        return False
    role = (user.get("role") or "").lower()
    return role in {"admin", "registrar"}


def is_admin(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return (user.get("role") or "").lower() == "admin"


def is_read_only(user: dict[str, Any] | None, is_global_read_only: bool = False) -> bool:
    return not can_write(user, is_global_read_only)

