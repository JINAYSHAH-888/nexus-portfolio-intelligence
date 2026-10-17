"""transaction ledger + profile SIP fields

Revision ID: 0002_transactions_and_sip
Revises: 0001_initial
Create Date: 2026-07-13

Adds the two things that turned an honest refusal into a real answer:

  * `transactions` -- the cost-basis ledger. Without it a mark-to-market P&L is
    not derivable at all, which is why /market/pnl used to return
    `pnl_available: false`.
  * `investor_profiles.monthly_sip` / `.sip_step_up_pct` -- so ARISE projects on
    the investor's real SIP instead of a 20%-of-income estimate.

Both are ADDITIVE and NULLABLE: the documented 10-field ProfileCreate contract
(CLAUDE.md §6) is unchanged, and existing rows need no backfill.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_transactions_and_sip"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("investor_profiles", sa.Column("monthly_sip", sa.Integer(), nullable=True))
    op.add_column("investor_profiles", sa.Column("sip_step_up_pct", sa.Float(), nullable=True))

    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scheme_id", sa.String(length=128), nullable=False),
        sa.Column("txn_type", sa.String(length=16), nullable=False),
        # Numeric, NOT float. Units run to 4dp and NAVs to 4dp; float error
        # accumulated across 240 SIP instalments stops the holding reconciling
        # with the AMC's own statement.
        sa.Column("units", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("nav", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        # The ACQUISITION date -- drives the holding period and therefore the
        # LTCG/STCG split. Deliberately distinct from created_at.
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Idempotency: a retried POST from a flaky mobile connection must not
        # book the same purchase twice. A duplicated buy silently doubles the
        # cost basis and corrupts every tax number downstream.
        sa.UniqueConstraint("user_id", "external_ref", name="uq_txn_user_external_ref"),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    # The hot query is FIFO lot matching: this user's ledger for this scheme,
    # oldest first. One index scan, already ordered.
    op.create_index(
        "idx_txn_user_scheme_date", "transactions", ["user_id", "scheme_id", "txn_date"]
    )


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_column("investor_profiles", "sip_step_up_pct")
    op.drop_column("investor_profiles", "monthly_sip")
