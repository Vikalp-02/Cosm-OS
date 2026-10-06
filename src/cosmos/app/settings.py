"""Runtime configuration, read from the environment and an optional `.env` file."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["anthropic", "gemini", "openai_compatible"]


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

    # Questions one user may ask per minute.
    ask_per_minute: int = 20

    # Which language model writes summaries and answers questions. "auto" uses
    # whichever provider has a key, Anthropic first, and switches the AI
    # features off when none has. Everything else works without them.
    llm_provider: Literal["auto", "none"] | Provider = "auto"
    # Leave empty for the provider's default model.
    llm_model: str = ""
    # Models to try, in order, when that one is overloaded. Unset means the
    # provider's defaults; an empty list means none. Written as JSON in the
    # environment: COSMOS_LLM_FALLBACK_MODELS=["gemini-3.5-flash"]
    llm_fallback_models: tuple[str, ...] | None = None
    llm_timeout_seconds: float = 60.0
    # Read from ANTHROPIC_API_KEY and GEMINI_API_KEY, in the environment or in `.env`.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    # For any other provider that speaks the OpenAI chat-completions protocol.
    llm_base_url: str = ""
    llm_api_key: SecretStr | None = None

    # Used only by the `seed-demo` command, for local development.
    demo_email: str = ""
    demo_password: str = ""

    @property
    def resolved_llm_provider(self) -> Provider | None:
        if self.llm_provider == "none":
            return None
        if self.llm_provider != "auto":
            return self.llm_provider
        if (
            self.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ):
            return "anthropic"
        if self.gemini_api_key:
            return "gemini"
        return None
