"""Command line entry point: `python -m cosmos.app <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cosmos.app.assistant import make_llm
from cosmos.app.calls import database_recorder
from cosmos.app.db import make_engine, make_session_factory, migrate
from cosmos.app.models import Membership, Tenant, User
from cosmos.app.pipeline import refresh_leaks
from cosmos.app.security import hash_password
from cosmos.app.settings import Settings
from cosmos.app.summaries import summarise_leaks

DEMO_TENANT = ("northwind", "Northwind Foods")
MIN_PASSWORD_LENGTH = 12


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cosmos.app")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="create or upgrade the database schema")
    commands.add_parser(
        "seed-demo",
        help="create the demo tenant and a user from COSMOS_DEMO_EMAIL and COSMOS_DEMO_PASSWORD",
    )
    refresh = commands.add_parser("refresh", help="run the engine and store its findings")
    refresh.add_argument("--data", type=Path, default=Path("data"), help="data directory")
    refresh.add_argument("--tenant", default=DEMO_TENANT[0])
    serve = commands.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command == "serve":
        import uvicorn

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        uvicorn.run("cosmos.app.main:create_app", factory=True, host=args.host, port=args.port)
        return 0

    migrate(settings.database_url)
    if args.command == "migrate":
        print("Database schema is up to date.")
        return 0

    engine = make_engine(settings.database_url)
    try:
        sessions = make_session_factory(engine)
        with sessions() as db:
            if args.command == "seed-demo":
                return _seed_demo(db, settings)
            result = refresh_leaks(args.data, args.tenant, db, datetime.now(UTC))
            print(
                f"{args.tenant}: {result.created} new leaks, {result.updated} updated,"
                f" {result.withdrawn} withdrawn."
            )
            llm = make_llm(settings)
            if llm is None:
                print("No language model is configured, so no summaries were written.")
            else:
                record = database_recorder(sessions, args.tenant)
                written = summarise_leaks(db, args.tenant, llm, record)
                print(f"{written} summaries written with {llm.model}.")
            return 0
    finally:
        engine.dispose()


def _seed_demo(db: Session, settings: Settings) -> int:
    email = settings.demo_email.strip().lower()
    if not email or len(settings.demo_password) < MIN_PASSWORD_LENGTH:
        print(
            "error: set COSMOS_DEMO_EMAIL and a COSMOS_DEMO_PASSWORD of at least"
            f" {MIN_PASSWORD_LENGTH} characters, in the environment or in .env",
            file=sys.stderr,
        )
        return 1

    tenant_id, tenant_name = DEMO_TENANT
    if db.get(Tenant, tenant_id) is None:
        db.add(Tenant(id=tenant_id, name=tenant_name))
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            id=str(uuid.uuid4()), email=email, name="Demo User", created_at=datetime.now(UTC)
        )
        db.add(user)
    user.password_hash = hash_password(settings.demo_password)
    user.is_active = True
    db.flush()
    if db.get(Membership, (user.id, tenant_id)) is None:
        db.add(Membership(user_id=user.id, tenant_id=tenant_id, role="admin"))
    db.commit()
    print(f"Demo user {email} can sign in to {tenant_name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
