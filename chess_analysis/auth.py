"""Accounts without passwords: a magic link by email creates a session cookie.

Tokens are random, stored only as SHA-256 hashes, single-use and short-lived. Sessions are
random too, stored hashed, and expire after `settings.session_days`.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import smtplib
import time
import uuid
from email.message import EmailMessage
from typing import Any

from fastapi import Request

from .config import Caps, settings
from .db import Database

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSION_COOKIE = "session"


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AuthError("That doesn't look like an email address.")
    return email


def send_email(to: str, subject: str, body: str) -> bool:
    """Send through SMTP when configured. Returns False (and logs the body) otherwise."""
    if not settings.smtp_host:
        log.warning("SMTP not configured; email to %s not sent:\n%s", to, body)
        return False
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)
    return True


def request_login(db: Database, email: str) -> dict[str, Any]:
    """Create a magic link and mail it. Returns what the caller may tell the user."""
    email = normalize_email(email)
    if db.count_login_tokens_since(email, time.time() - 3600) >= settings.login_links_per_hour:
        raise AuthError("Too many sign-in links requested for this address. Try again in an hour.", 429)
    token = secrets.token_urlsafe(32)
    db.create_login_token(hash_token(token), email, time.time() + settings.login_token_minutes * 60)
    link = f"{settings.base_url}/#/login/{token}"
    body = (
        "Hello,\n\n"
        f"Click this link to sign in to Chess Improvement Report:\n\n{link}\n\n"
        f"The link works once and expires in {settings.login_token_minutes} minutes. "
        "If you did not request it, you can ignore this email.\n"
    )
    sent = send_email(email, "Your sign-in link", body)
    out: dict[str, Any] = {"sent": sent, "email": email}
    if settings.auth_dev_links or (not sent and not settings.smtp_host):
        # No mail server: hand the link back so local development still works.
        out["dev_link"] = link
    return out


def verify_login(db: Database, token: str) -> tuple[dict[str, Any], str]:
    """Consume a magic link token; returns (account, session token)."""
    if not token or len(token) > 200:
        raise AuthError("Invalid sign-in link.")
    email = db.consume_login_token(hash_token(token))
    if email is None:
        raise AuthError("This sign-in link is invalid, already used or expired. Request a new one.", 400)
    account = db.get_or_create_account(uuid.uuid4().hex, email)
    session_token = secrets.token_urlsafe(32)
    db.create_session(hash_token(session_token), account["id"], time.time() + settings.session_days * 86400)
    return account, session_token


def account_from_request(request: Request, db: Database) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return db.account_for_session(hash_token(token))


def logout(request: Request, db: Database) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(hash_token(token))


def is_admin(account: dict[str, Any] | None) -> bool:
    return bool(account and account.get("email", "").lower() in settings.admin_emails)


def tier_of(account: dict[str, Any] | None) -> str:
    if is_admin(account):
        return "admin"
    return "user" if account else "anonymous"


def caps_for(account: dict[str, Any] | None) -> Caps:
    return {"admin": settings.admin_caps, "user": settings.user_caps, "anonymous": settings.anon_caps}[tier_of(account)]


def public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {"id": account["id"], "email": account["email"], "created_at": account["created_at"],
            "admin": is_admin(account), "tier": tier_of(account)}
