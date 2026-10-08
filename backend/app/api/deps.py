from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.security import decode_access_token

SESSION_COOKIE = "ti_session"
CSRF_COOKIE = "ti_csrf"
CSRF_HEADER = "X-CSRF-Token"
ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}


MFA_SETUP_HEADER = "X-MFA-Setup-Required"


def mfa_setup_required(user: User, session_mfa: bool) -> bool:
    return user.role in get_settings().mfa_required_roles and not (user.totp_enabled and session_mfa)


def session_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Any valid session, including one that still has to set up 2FA. Only the account endpoints use this."""
    token = request.cookies.get(SESSION_COOKIE)
    claims = decode_access_token(token) if token else None
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, int(claims["sub"]))
    if user is None or not user.is_active or claims.get("sv", 0) != (user.session_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # double-submit CSRF: header must equal the token bound into the signed session
        if request.headers.get(CSRF_HEADER) != claims.get("csrf"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    request.state.user = user
    request.state.session_mfa = bool(claims.get("mfa"))
    return user


def current_user(request: Request, user: User = Depends(session_user)) -> User:
    if mfa_setup_required(user, request.state.session_mfa):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Set up two-factor authentication to continue.",
                            headers={MFA_SETUP_HEADER: "1"})
    return user


def require_role(role: str) -> Callable[..., User]:
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {role} role")
        return user
    return dep
