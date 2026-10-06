"""Application state: who can sign in, to which tenant, and the leaks they see."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp that is always UTC and always timezone-aware.

    SQLite drops the timezone on the way in and Postgres keeps it, so without
    this the same query would return different kinds of value on each.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenant"

    # The same identifier the warehouse uses, so the two never need mapping.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class User(Base):
    __tablename__ = "user_account"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Stored lower-cased, so sign-in does not depend on how it was typed.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Membership(Base):
    __tablename__ = "membership"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), default="member")


class UserSession(Base):
    __tablename__ = "user_session"

    # A hash of the token in the cookie. The token itself is never stored, so a
    # copy of this table cannot be used to sign in as anyone.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)


class Leak(Base):
    """A finding from the engine, kept under a reference that stays put between runs."""

    __tablename__ = "leak"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference"),
        Index("ix_leak_tenant_status_start", "tenant_id", "status", "start_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    reference: Mapped[str] = mapped_column(String(16))
    # "open", or "withdrawn" once a later run of the engine no longer reports it.
    status: Mapped[str] = mapped_column(String(16), default="open")
    cause: Mapped[str] = mapped_column(String(32))
    platform: Mapped[str] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    detected_at: Mapped[datetime] = mapped_column(UtcDateTime)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    # The finding as the engine reported it, plus the names of its products.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    # An AI-written summary with the key of the facts it was written from, or
    # null when there is none that passed its checks.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class LlmCall(Base):
    """One request to a language model: what it was for, how it went and what it used."""

    __tablename__ = "llm_call"
    __table_args__ = (Index("ix_llm_call_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenant.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
