from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import decode_access_token

SESSION_COOKIE = "ti_session"
CSRF_COOKIE = "ti_csrf"
CSRF_HEADER = "X-CSRF-Token"
ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    claims = decode_access_token(token) if token else None
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # double-submit CSRF: header must equal the token bound into the signed session
        if request.headers.get(CSRF_HEADER) != claims.get("csrf"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    request.state.user = user
    return user


def require_role(role: str) -> Callable[..., User]:
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {role} role")
        return user
    return dep
