"""Two-factor authentication: TOTP (RFC 6238) with authenticator apps, plus single-use recovery codes.

- Secrets are encrypted at rest (FERNET_KEY, or a key derived from SECRET_KEY when FERNET_KEY is unset).
- A code is accepted for the current 30-second step and one step either side (clock drift), and never twice:
  the last accepted step is stored, so an intercepted code cannot be replayed.
- Recovery codes are shown once and stored only as Argon2 hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings
from app.security import hash_password, verify_password

STEP = 30
DIGITS = 6
WINDOW = 1
RECOVERY_CODES = 10


class MfaUnavailable(RuntimeError):
    pass


def _fernet(settings: Settings) -> Fernet:
    if settings.FERNET_KEY:
        return Fernet(settings.FERNET_KEY.encode())
    if len(settings.SECRET_KEY) < 32:
        raise MfaUnavailable("Two-factor authentication needs SECRET_KEY (32+ characters) or FERNET_KEY to be configured.")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"trever-rfp-portal/totp-secrets").derive(
        settings.SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(settings: Settings, secret: str) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt(settings: Settings, token: str) -> str:
    return _fernet(settings).decrypt(token.encode()).decode()


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def totp(secret_b32: str, at: float | None = None, digits: int = DIGITS) -> str:
    return hotp(secret_b32, int((time.time() if at is None else at) // STEP), digits)


def verify_totp(secret_b32: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """Returns the matched time step (to store as the new last_step), or None."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((time.time() if at is None else at) // STEP)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(secret_b32, step), code):
            return step
    return None


def provisioning_uri(secret_b32: str, email: str, issuer: str) -> str:
    label = quote(f"{issuer}:{email}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_data_uri(uri: str) -> str:
    import segno
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#0b1320", light="#ffffff")


def new_recovery_codes() -> tuple[list[str], list[str]]:
    """Returns (codes to show once, hashes to store). Codes look like 'k3f9-x2mq-7hdp'."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
    codes = ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)) for _ in range(RECOVERY_CODES)]
    return codes, [hash_password(c) for c in codes]


def use_recovery_code(hashes: list[str] | None, code: str) -> list[str] | None:
    """Returns the remaining hashes when the code matches one (which is then spent), else None."""
    code = code.strip().lower().replace(" ", "")
    if len(code) == 12 and "-" not in code:
        code = f"{code[:4]}-{code[4:8]}-{code[8:]}"
    for i, h in enumerate(hashes or []):
        if verify_password(code, h):
            return hashes[:i] + hashes[i + 1:]
    return None
