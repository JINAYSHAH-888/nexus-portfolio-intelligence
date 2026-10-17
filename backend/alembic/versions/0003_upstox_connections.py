"""upstox_connections

Revision ID: 0003_upstox_connections
Revises: 0002_transactions_and_sip
Create Date: 2026-07-13

Links a platform user to their Upstox broker identity. The access_token itself
is deliberately NOT a column here -- it lives in Redis with a TTL to the next
03:30 IST expiry (Upstox's real lifecycle), so a stale token can never sit in
Postgres looking valid. This table only records who they are on Upstox and
what their account can do (exchanges/products/POA), which is useful even
between logins.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_upstox_connections"
down_revision: Union[str, None] = "0002_transactions_and_sip"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upstox_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("upstox_user_id", sa.String(length=64), nullable=False),
        sa.Column("upstox_user_name", sa.String(length=255), nullable=True),
        sa.Column("upstox_email", sa.String(length=320), nullable=True),
        sa.Column("exchanges", sa.JSON(), nullable=False),
        sa.Column("products", sa.JSON(), nullable=False),
        sa.Column("has_extended_token", sa.Boolean(), nullable=False),
        sa.Column("poa", sa.Boolean(), nullable=False),
        sa.Column(
            "last_connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_upstox_connections_user_id", "upstox_connections", ["user_id"], unique=True
    )
    op.create_index(
        "ix_upstox_connections_upstox_user_id",
        "upstox_connections",
        ["upstox_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("upstox_connections")
