from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.routes import router
from app.catalog import get_catalog
from app.config import get_settings
from app.db import SessionLocal
from app.security import hash_password, limiter, validate_password

log = logging.getLogger(__name__)


def _bootstrap_admin() -> None:
    """Create the first admin from BOOTSTRAP_ADMIN_* env vars if no users exist yet."""
    from app.models import User

    s = get_settings()
    if not (s.BOOTSTRAP_ADMIN_EMAIL and s.BOOTSTRAP_ADMIN_PASSWORD):
        return
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is not None:
            return
        problem = validate_password(s.BOOTSTRAP_ADMIN_PASSWORD)
        if problem:
            log.error("BOOTSTRAP_ADMIN_PASSWORD rejected: %s", problem)
            return
        db.add(User(email=s.BOOTSTRAP_ADMIN_EMAIL.lower(), password_hash=hash_password(s.BOOTSTRAP_ADMIN_PASSWORD), role="admin"))
        db.commit()
        log.info("bootstrap admin %s created", s.BOOTSTRAP_ADMIN_EMAIL)


def create_app() -> FastAPI:
    settings = get_settings()
    get_catalog()  # fail fast on an invalid catalog
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _bootstrap_admin()
        yield

    app = FastAPI(title="Tender Intelligence API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if settings.is_production else "/api/docs", redoc_url=None, openapi_url=None
                  if settings.is_production else "/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
                       allow_credentials=True, allow_methods=["GET", "POST", "PATCH"],
                       allow_headers=["Content-Type", "X-CSRF-Token"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        ip = request.client.host if request.client else "?"
        if request.url.path.startswith("/api/") and not limiter.allow(f"api:{ip}", settings.API_RATE_LIMIT_PER_MINUTE):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    app.include_router(router)
    return app


app = create_app()
