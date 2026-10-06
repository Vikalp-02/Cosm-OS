"""AI-written leak summaries, and a log of every model call."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leak") as leak:
        leak.add_column(sa.Column("summary", sa.JSON, nullable=True))
    op.create_table(
        "llm_call",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_call_tenant_created", "llm_call", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("llm_call")
    with op.batch_alter_table("leak") as leak:
        leak.drop_column("summary")
