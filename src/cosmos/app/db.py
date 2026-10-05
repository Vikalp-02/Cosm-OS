"""Database engine, sessions and schema migrations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        # Check a pooled connection is alive before use, so a database restart
        # costs one reconnect and not a failed request.
        return create_engine(url, pool_pre_ping=True)

    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite
        cursor.execute("PRAGMA journal_mode = WAL")  # readers do not block the writer
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def migrate(url: str) -> None:
    """Bring the schema at `url` up to the latest version. Safe to run repeatedly."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
