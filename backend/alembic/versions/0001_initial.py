"""initial schema: users, refresh_tokens, investor_profiles, scheme_selections, reports, notifications

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-13

Mirrors backend/models exactly (verified by compiling Base.metadata against the
postgresql dialect). `sa.Uuid` renders as native UUID on Postgres and CHAR(32)
on SQLite, which is what lets the same models back both prod and the test suite.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        # We store the jti, never the token string: a leaked DB dump then yields
        # no usable tokens.
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "investor_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("occupation", sa.String(length=255), nullable=True),
        # INR as integers -- money in a float column is how you end up reporting
        # a corpus of 249999.99999.
        sa.Column("monthly_income", sa.Integer(), nullable=True),
        sa.Column("total_corpus", sa.Integer(), nullable=True),
        sa.Column("investment_amount", sa.Integer(), nullable=True),
        sa.Column("goal", sa.String(length=32), nullable=True),
        sa.Column("risk_appetite", sa.String(length=32), nullable=True),
        sa.Column("target_retirement_age", sa.Integer(), nullable=True),
        sa.Column("monthly_expenses_in_retirement", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investor_profiles_user_id", "investor_profiles", ["user_id"], unique=True
    )

    op.create_table(
        "scheme_selections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("selected_scheme_ids", sa.JSON(), nullable=False),
        # Weights are FRACTIONS summing to 1.0, not percentages -- the contract
        # PortfolioStatisticsBuilder.build() enforces downstream.
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scheme_selections_user_id", "scheme_selections", ["user_id"], unique=True
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheme_ids", sa.JSON(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_user_id", "reports", ["user_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("investor_user_id", sa.Uuid(), nullable=False),
        # Nullable: a notification can be raised before an RM is assigned, so
        # SET NULL (not CASCADE) if the RM's account is later removed --
        # deleting an RM must not delete the investor's notification history.
        sa.Column("rm_user_id", sa.Uuid(), nullable=True),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("delivery_channel", sa.String(length=32), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("related_object_type", sa.String(length=64), nullable=True),
        sa.Column("related_object_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["investor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rm_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_investor_user_id", "notifications", ["investor_user_id"])
    op.create_index("ix_notifications_rm_user_id", "notifications", ["rm_user_id"])
    # GET /notifications/mine is exactly this query: filter by investor, newest
    # first. The composite index keeps it a single index scan.
    op.create_index(
        "idx_notifications_investor_created",
        "notifications",
        ["investor_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("reports")
    op.drop_table("scheme_selections")
    op.drop_table("investor_profiles")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
