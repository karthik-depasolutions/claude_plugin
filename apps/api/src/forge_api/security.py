"""Password hashing and session tokens for admin-provisioned accounts (see
scripts/create_user.py — there is no signup endpoint). Password hashing uses
stdlib PBKDF2 rather than a new dependency; session tokens use PyJWT, which
the dependency tree already resolves (google-genai pulls it in transitively)
so declaring it directly in pyproject.toml adds nothing new to install."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from forge_api.config import get_settings

PBKDF2_ITERATIONS = 600_000
SESSION_COOKIE_NAME = "forge_session"
SESSION_TTL = timedelta(days=7)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    salt, _, digest = password_hash.partition("$")
    if not salt or not digest:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return hmac.compare_digest(candidate, digest)


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError(
            "FORGE_JWT_SECRET is not set - required to issue/verify login sessions. "
            "Generate one with `python -c \"import secrets; print(secrets.token_hex(32))\"`."
        )
    return secret


def issue_session_token(email: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": email, "iat": now, "exp": now + SESSION_TTL}, _jwt_secret(), algorithm="HS256")


def verify_session_token(token: str) -> str | None:
    """Returns the email the token was issued for, or None if missing/expired/tampered."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    email = payload.get("sub")
    return email if isinstance(email, str) else None


__all__ = [
    "SESSION_COOKIE_NAME",
    "SESSION_TTL",
    "hash_password",
    "issue_session_token",
    "verify_password",
    "verify_session_token",
]
