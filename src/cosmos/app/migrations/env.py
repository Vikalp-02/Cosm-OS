"""Alembic environment. Run through `cosmos.app.db.migrate`, which supplies the URL."""

from __future__ import annotations

from alembic import context

from cosmos.app.db import make_engine
from cosmos.app.models import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    url = context.config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("no database URL configured for migrations")
    engine = make_engine(url)
    with engine.connect() as connection:
        # Batch mode lets SQLite alter tables, which it cannot do in place.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
