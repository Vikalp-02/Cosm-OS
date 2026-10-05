"""Runtime configuration, read from the environment and an optional `.env` file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COSMOS_", env_file=".env", extra="ignore")

    # Any SQLAlchemy URL. SQLite needs no setup; Postgres is the production target.
    database_url: str = "sqlite:///data/app.db"

    session_hours: int = 12
    # Leave on anywhere real. Turn off only for plain-http local development.
    cookie_secure: bool = True
    # Origins a browser may send state-changing requests from.
    allowed_origins: tuple[str, ...] = ("http://localhost:3000",)

    # Failed sign-ins allowed per email and address before a pause.
    login_attempts: int = 5
    login_window_minutes: int = 15

    # Used only by the `seed-demo` command, for local development.
    demo_email: str = ""
    demo_password: str = ""
