"""Accounts and sessions: Argon2 hashes, signed httpOnly session cookie, login rate limit,
email verification and password reset (links printed to the console in DEMO MODE)."""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from ..config import Settings
from ..state import STATE
from .db import PlatformDB, new_id, now_iso

log = logging.getLogger("fundbridge.auth")
COOKIE = "fb_session"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("timing-equaliser")


class AuthError(HTTPException):
    pass


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _utc(dt: str) -> datetime:
    return datetime.fromisoformat(dt)


class AuthService:
    def __init__(self, db: PlatformDB, settings: Settings):
        self.db, self.settings = db, settings
        secret = settings.session_secret
        if not secret:
            if not settings.demo_mode:
                raise RuntimeError("SESSION_SECRET must be set when DEMO_MODE=false")
            secret = secrets.token_urlsafe(32)
            log.warning("SESSION_SECRET not set: using a random per-process secret (sessions end on restart).")
        self.signer = TimestampSigner(secret, salt="fundbridge-session")
        self.outbox: list[dict] = []  # DEMO MODE "emails" (also printed to console)

    # -- users -----------------------------------------------------------------
    def create_user(self, email: str, password: str, role: str, *, is_demo: bool = False,
                    verified: bool = False, consent: bool = True, internal: bool = False) -> dict:
        """internal=True only from scripts/admin.py and the demo seed: the only way to create an admin."""
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise AuthError(422, "Enter a valid email address.")
        if len(password or "") < 8:
            raise AuthError(422, "Password must be at least 8 characters.")
        if role not in ("ngo", "company", "donor") and not (internal and role == "admin"):
            raise AuthError(422, "Invalid role.")
        if not consent:
            raise AuthError(422, "You must accept the privacy and consent notice.")
        if self.db.one("SELECT id FROM users WHERE email = ?", (email,)):
            raise AuthError(409, "An account with this email already exists.")
        uid = new_id("usr")
        self.db.run("INSERT INTO users (id, email, password_hash, role, email_verified, is_demo, consent_at, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (uid, email, _hasher.hash(password), role, int(verified), int(is_demo), now_iso(), now_iso()))
        return self.db.one("SELECT * FROM users WHERE id = ?", (uid,))

    # -- login / sessions ----------------------------------------------------------
    def _failures(self, key: str) -> int:
        since = (datetime.now(timezone.utc) - timedelta(minutes=self.settings.login_window_minutes)).isoformat()
        return self.db.one("SELECT COUNT(*) AS n FROM login_failures WHERE key = ? AND at > ?", (key, since))["n"]

    def login(self, email: str, password: str, ip: str) -> str:
        email = (email or "").strip().lower()
        key = f"{email}|{ip}"
        if self._failures(key) >= self.settings.login_max_failures or self._failures(f"*|{ip}") >= self.settings.login_max_failures * 4:
            raise AuthError(429, f"Too many failed logins. Try again in {self.settings.login_window_minutes} minutes.")
        user = self.db.one("SELECT * FROM users WHERE email = ?", (email,))
        ok = False
        try:
            ok = _hasher.verify(user["password_hash"] if user else _DUMMY_HASH, password or "") and user is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
        if not ok:
            self.db.run("INSERT INTO login_failures VALUES (?,?)", (key, now_iso()))
            self.db.run("INSERT INTO login_failures VALUES (?,?)", (f"*|{ip}", now_iso()))
            raise AuthError(401, "Wrong email or password.")
        self.db.run("DELETE FROM login_failures WHERE key = ?", (key,))
        if _hasher.check_needs_rehash(user["password_hash"]):
            self.db.run("UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), user["id"]))
        return self.start_session(user["id"])

    def start_session(self, user_id: str) -> str:
        sid = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=self.settings.session_hours)
        self.db.run("INSERT INTO sessions VALUES (?,?,?,?)", (sid, user_id, now_iso(), expires.isoformat()))
        return self.signer.sign(sid).decode()

    def end_session(self, cookie: str | None) -> None:
        sid = self._unsign(cookie)
        if sid:
            self.db.run("DELETE FROM sessions WHERE id = ?", (sid,))

    def _unsign(self, cookie: str | None) -> str | None:
        if not cookie:
            return None
        try:
            return self.signer.unsign(cookie, max_age=self.settings.session_hours * 3600).decode()
        except (BadSignature, SignatureExpired):
            return None

    def user_for_cookie(self, cookie: str | None) -> dict | None:
        sid = self._unsign(cookie)
        if not sid:
            return None
        row = self.db.one(
            "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (sid,))
        if not row or _utc(row["expires_at"]) < datetime.now(timezone.utc):
            return None
        ngo = self.db.one("SELECT ngo_id FROM ngo_profiles WHERE user_id = ?", (row["id"],))
        comp = self.db.one("SELECT company_id FROM company_profiles WHERE user_id = ?", (row["id"],))
        donor = self.db.one("SELECT donor_id FROM donor_profiles WHERE user_id = ?", (row["id"],))
        return {
            "id": row["id"], "email": row["email"], "role": row["role"], "email_verified": bool(row["email_verified"]) or not self.settings.require_email_verification,
            "is_demo": bool(row["is_demo"]),
            "ngo_id": ngo["ngo_id"] if ngo else None,
            "company_id": comp["company_id"] if comp else None,
            "donor_id": donor["donor_id"] if donor else None,
        }

    # -- tokens (verify / reset) ------------------------------------------------------
    def issue_token(self, user_id: str, kind: str, hours: int = 24) -> str:
        raw = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        self.db.run("INSERT INTO tokens VALUES (?,?,?,?,0)", (_hash_token(raw), user_id, kind, expires))
        return raw

    def _deliver(self, to: str, subject: str, link: str) -> None:
        """No mail server in DEMO MODE: print the link to the server console."""
        msg = {"to": to, "subject": subject, "link": link, "at": now_iso()}
        self.outbox.append(msg)
        print(f"\n===== [DEMO MODE] email to {to}: {subject}\n      {link}\n", flush=True)

    def send_verification(self, user: dict) -> None:
        raw = self.issue_token(user["id"], "verify")
        self._deliver(user["email"], "Verify your FundBridge email", f"{self.settings.app_base_url}/?verify={raw}")

    def consume_token(self, raw: str, kind: str) -> str:
        row = self.db.one("SELECT * FROM tokens WHERE token_hash = ? AND kind = ?", (_hash_token(raw or ""), kind))
        if not row or row["used"] or _utc(row["expires_at"]) < datetime.now(timezone.utc):
            raise AuthError(400, "This link is invalid or has expired.")
        self.db.run("UPDATE tokens SET used = 1 WHERE token_hash = ?", (row["token_hash"],))
        return row["user_id"]

    def verify_email(self, raw: str) -> None:
        uid = self.consume_token(raw, "verify")
        self.db.run("UPDATE users SET email_verified = 1 WHERE id = ?", (uid,))

    def request_reset(self, email: str) -> None:
        user = self.db.one("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),))
        if user:  # same response either way: no account enumeration
            raw = self.issue_token(user["id"], "reset", hours=1)
            self._deliver(user["email"], "Reset your FundBridge password", f"{self.settings.app_base_url}/?reset={raw}")

    def reset_password(self, raw: str, new_password: str) -> None:
        if len(new_password or "") < 8:
            raise AuthError(422, "Password must be at least 8 characters.")
        uid = self.consume_token(raw, "reset")
        self.db.run("UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(new_password), uid))
        self.db.run("DELETE FROM sessions WHERE user_id = ?", (uid,))  # log out everywhere


# -- FastAPI dependencies -----------------------------------------------------------
def current_user(request: Request) -> dict:
    user = STATE["platform"].auth.user_for_cookie(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Please log in.")
    return user


def require_role(*roles: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, "Your account type cannot use this feature.")
        return user
    return dep


def require_verified(user: dict) -> None:
    if not user["email_verified"]:
        raise HTTPException(403, "Verify your email address first (DEMO MODE: the link is printed in the server console).")

