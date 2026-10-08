"""Password hashing (Argon2id), JWT sessions, secret encryption (Fernet) and rate limiting."""
from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

from app.config import get_settings
from app.db import utcnow

_ph = PasswordHasher()
_DEV_SECRET = secrets.token_urlsafe(48)  # per-process fallback in development only


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, VerificationError):
        return False


def validate_password(pw: str) -> str | None:
    if len(pw) < 12:
        return "Password must be at least 12 characters."
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        return "Password must mix upper/lower case letters and digits."
    return None


def _jwt_key() -> str:
    key = get_settings().SECRET_KEY
    return key if key else _DEV_SECRET


def create_access_token(user_id: int, role: str) -> tuple[str, str]:
    """Returns (jwt, csrf_token). The CSRF token is bound into the JWT and echoed in a readable cookie."""
    csrf = secrets.token_urlsafe(24)
    exp = utcnow() + timedelta(minutes=get_settings().ACCESS_TOKEN_MINUTES)
    token = jwt.encode({"sub": str(user_id), "role": role, "csrf": csrf, "exp": exp}, _jwt_key(), algorithm="HS256")
    return token, csrf


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _jwt_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def fernet() -> Fernet:
    key = get_settings().FERNET_KEY
    if not key:
        raise RuntimeError("FERNET_KEY is not configured; cannot store encrypted secrets")
    return Fernet(key.encode())


def encrypt_secret(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    return fernet().decrypt(token.encode()).decode()


class RateLimiter:
    """In-process sliding-window limiter. Behind multiple API replicas, also rate-limit at the proxy."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


limiter = RateLimiter()
