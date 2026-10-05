"""Tenants, users, sessions and leaks."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
    )
    op.create_table(
        "user_account",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False),
    )
    op.create_table(
        "membership",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user_account.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(32), nullable=False),
    )
    op.create_table(
        "user_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("user_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", _TIMESTAMP, nullable=False),
        sa.Column("expires_at", _TIMESTAMP, nullable=False),
    )
    op.create_index("ix_user_session_expires_at", "user_session", ["expires_at"])
    op.create_table(
        "leak",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cause", sa.String(32), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("detected_at", _TIMESTAMP, nullable=False),
        sa.Column("updated_at", _TIMESTAMP, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint("tenant_id", "reference"),
    )
    op.create_index("ix_leak_tenant_status_start", "leak", ["tenant_id", "status", "start_date"])


def downgrade() -> None:
    op.drop_table("leak")
    op.drop_table("user_session")
    op.drop_table("membership")
    op.drop_table("user_account")
    op.drop_table("tenant")
