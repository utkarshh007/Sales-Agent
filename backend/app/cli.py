"""Operational CLI:  python -m app.cli <command>

  init-db                 create tables (use `alembic upgrade head` in production) and seed catalog/portals
  seed                    re-sync catalog.yaml into the database
  create-user EMAIL ROLE  create a user (password read from TENDER_USER_PASSWORD or prompted)
  discover PORTAL_CODE    run one discovery pass for a portal and process the resulting queue
  run-once                schedule due work and drain the queue, then exit
  worker                  run the scheduler + job worker forever
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from sqlalchemy import select

from app.catalog import get_catalog
from app.config import get_settings
from app.db import Base, SessionLocal, engine


def _seed() -> None:
    from app.pipeline import seed_reference_data
    with SessionLocal() as s:
        seed_reference_data(s, get_catalog())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("seed")
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    cu.add_argument("role", choices=["admin", "analyst", "viewer"])
    d = sub.add_parser("discover")
    d.add_argument("portal")
    d.add_argument("--max-pages", type=int, default=None)
    sub.add_parser("run-once")
    sub.add_parser("embed", help="store embeddings for tenders analysed before Phase 6 (similar-tender search)")
    sub.add_parser("worker")
    args = ap.parse_args(argv)

    import app.models  # noqa: F401  (register tables)

    if args.cmd == "init-db":
        Base.metadata.create_all(engine)
        _seed()
        print("database initialised")
    elif args.cmd == "seed":
        _seed()
        print("catalog synced")
    elif args.cmd == "create-user":
        from app.models import User
        from app.security import hash_password, validate_password
        pw = os.environ.get("TENDER_USER_PASSWORD") or getpass.getpass("Password: ")
        problem = validate_password(pw)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        with SessionLocal() as s:
            if s.scalar(select(User).where(User.email == args.email.lower())):
                print("user exists", file=sys.stderr)
                return 1
            s.add(User(email=args.email.lower(), password_hash=hash_password(pw), role=args.role))
            s.commit()
        print(f"created {args.role} {args.email}")
    elif args.cmd == "discover":
        from app.models import Portal
        from app.pipeline import discover_portal
        from app.worker import Worker
        settings = get_settings()
        with SessionLocal() as s:
            portal = s.scalar(select(Portal).where(Portal.code == args.portal))
            if portal is None:
                print(f"unknown portal {args.portal}", file=sys.stderr)
                return 1
            from app.connectors.registry import build_connector
            config = {**portal.config, **({"max_pages": args.max_pages} if args.max_pages else {})}
            connector = build_connector(portal.connector, portal.code, config, settings)
            print(discover_portal(s, portal, settings, connector))
        print(f"processed {Worker(settings).drain()} queued jobs")
    elif args.cmd == "embed":
        from app.history import embed_tender
        from app.models import Tender, TenderEmbedding
        from app.semantic import get_semantic_matcher
        matcher = get_semantic_matcher(get_catalog(), get_settings())
        if matcher is None:
            print("semantic matching is disabled or the model is unavailable", file=sys.stderr)
            return 1
        done = 0
        with SessionLocal() as s:
            have = set(s.scalars(select(TenderEmbedding.tender_id)))
            for t in s.scalars(select(Tender)):
                if t.id not in have:
                    embed_tender(s, t, matcher)
                    done += 1
                    if done % 500 == 0:
                        s.commit()
            s.commit()
        print(f"embedded {done} tenders")
    elif args.cmd == "run-once":
        from app.worker import Worker
        w = Worker()
        w.schedule()
        print(f"processed {w.drain()} jobs")
    elif args.cmd == "worker":
        from app.worker import Worker
        Worker().run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
