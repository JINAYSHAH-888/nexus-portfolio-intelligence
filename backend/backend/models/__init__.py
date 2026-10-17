"""
SQLAlchemy ORM models.

The field sets here are not invented -- each is reverse-engineered from the
Pydantic response schemas that already existed in `backend/schemas/`, so the
ORM and the API contract cannot drift:

    schemas/auth.py::MeResponse                    -> User (role, is_active)
    schemas/profile.py::ProfileResponse            -> InvestorProfile
    schemas/schemes.py::SelectedSchemesResponse    -> SchemeSelection
    schemas/reports.py::ReportResponse             -> Report
    schemas/notifications.py::NotificationResponse -> Notification

Portability: `Uuid` and `JSON` are SQLAlchemy's dialect-neutral types --
native UUID/JSONB on Postgres, CHAR(32)/TEXT on SQLite -- so the test suite
runs these exact models against in-memory SQLite with no Docker.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.infrastructure.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=_utcnow,
        nullable=False,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # "investor" | "rm" (relationship manager) | "admin"
    role: Mapped[str] = mapped_column(String(32), default="investor", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profile: Mapped[InvestorProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    selection: Mapped[SchemeSelection | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    upstox_connection: Mapped[UpstoxConnection | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """
    Server-side refresh-token records.

    A stateless JWT cannot be revoked, but /auth/logout and /auth/logout-all
    must genuinely invalidate a session -- so refresh tokens are tracked by
    `jti` here and checked on every refresh. Postgres is the source of truth;
    the Redis denylist is only a latency shortcut in front of it.

    We store the jti, never the token string: a leaked DB dump then yields no
    usable tokens.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    @property
    def is_usable(self) -> bool:
        if self.revoked_at is not None:
            return False
        exp = self.expires_at
        # SQLite round-trips datetimes without tzinfo; normalise before comparing.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return exp > _utcnow()


class InvestorProfile(Base, TimestampMixin):
    """Phase-1 Deep Profile. Gates every Phase-2/3 endpoint."""

    __tablename__ = "investor_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )

    full_name: Mapped[str | None] = mapped_column(String(255))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    occupation: Mapped[str | None] = mapped_column(String(255))

    # INR, stored as integers. Money in a float column is how you end up
    # reporting a corpus of 249999.99999.
    monthly_income: Mapped[int | None] = mapped_column(Integer)
    total_corpus: Mapped[int | None] = mapped_column(Integer)
    investment_amount: Mapped[int | None] = mapped_column(Integer)

    goal: Mapped[str | None] = mapped_column(String(32))
    risk_appetite: Mapped[str | None] = mapped_column(String(32))

    target_retirement_age: Mapped[int | None] = mapped_column(Integer)
    monthly_expenses_in_retirement: Mapped[int | None] = mapped_column(Integer)

    # The ongoing monthly SIP, in INR. ARISE's accumulation phase needs this.
    # OPTIONAL, so the documented 10-field ProfileCreate contract (CLAUDE.md §6)
    # still holds: an existing client that never sends it keeps working, and
    # ARISE falls back to a clearly-labelled estimate. When it IS supplied,
    # ARISE uses the real number and stops guessing.
    monthly_sip: Mapped[int | None] = mapped_column(Integer)
    # Annual SIP step-up as a fraction (0.10 = 10%/yr). Investors commonly
    # escalate their SIP with salary growth; ARISE models it.
    sip_step_up_pct: Mapped[float | None] = mapped_column(Float)

    user: Mapped[User] = relationship(back_populates="profile")

    # The 10 fields the FilterEngine and ARISE actually require. ProfileCreate
    # demands all of them; ProfileUpdate may leave the record partial, so
    # completeness is computed, never trusted from the client.
    REQUIRED_FIELDS = (
        "full_name",
        "date_of_birth",
        "occupation",
        "monthly_income",
        "total_corpus",
        "investment_amount",
        "goal",
        "risk_appetite",
        "target_retirement_age",
        "monthly_expenses_in_retirement",
    )

    @property
    def is_complete(self) -> bool:
        return all(getattr(self, f) is not None for f in self.REQUIRED_FIELDS)

    @property
    def age(self) -> int | None:
        if self.date_of_birth is None:
            return None
        today = date.today()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    @property
    def years_to_retirement(self) -> int | None:
        if self.target_retirement_age is None or self.age is None:
            return None
        return max(self.target_retirement_age - self.age, 0)


class SchemeSelection(Base, TimestampMixin):
    """
    The investor's selected schemes and their allocation weights.

    `weights` are FRACTIONS SUMMING TO 1.0, not percentages. This matches
    AllocateWeightsRequest's validator and, critically,
    PortfolioStatisticsBuilder.build(), which raises if
    abs(sum(weights) - 1.0) > 0.01. Storing 60/40 here instead of 0.6/0.4
    would blow up ARISE at the far end of the pipeline.
    """

    __tablename__ = "scheme_selections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    selected_scheme_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    weights: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)

    user: Mapped[User] = relationship(back_populates="selection")


class Transaction(Base):
    """
    A single buy/sell against one scheme. THE COST-BASIS RECORD.

    Without this table a mark-to-market P&L is not derivable at all -- you can
    know what an investor *intended* to allocate, but not what they actually
    paid, when, or how many units they hold. That is why /market/pnl previously
    had to answer `pnl_available: false` rather than invent a number.

    Design notes that matter for money:

    * `units` and `nav` are Numeric, NOT float. Units run to 3-4 decimals and
      NAVs to 4; accumulating float error across a multi-year SIP produces
      holdings that don't reconcile with the AMC's own statement. Numeric is
      exact.
    * `amount` is stored as well as derived, because the AMC's contract note is
      the source of truth: amount = units * nav in principle, but rounding at
      the AMC's end means the recorded rupee figure is authoritative.
    * Sells are recorded as their own rows with positive `units`; direction is
      carried by `txn_type`. A signed-units scheme reads compactly but makes
      every FIFO/tax query in the codebase remember the sign convention.
    * `txn_date` is the ACQUISITION date, which is what drives the holding
      period and therefore the LTCG/STCG split. It is deliberately distinct
      from created_at (when the row was entered).
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scheme_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # "buy" | "sip" | "sell"  ("sip" is a buy; kept distinct for reporting)
    txn_type: Mapped[str] = mapped_column(String(16), nullable=False)

    units: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    nav: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    txn_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Idempotency key. A retried POST (mobile client on a flaky connection) must
    # not book the same purchase twice -- a duplicated buy silently corrupts the
    # cost basis and every downstream tax number.
    external_ref: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="transactions")

    __table_args__ = (
        # The hot query is "this user's ledger for this scheme, oldest first"
        # (FIFO lot matching). One index scan, already ordered.
        Index("idx_txn_user_scheme_date", "user_id", "scheme_id", "txn_date"),
        UniqueConstraint("user_id", "external_ref", name="uq_txn_user_external_ref"),
    )

    BUY_TYPES = ("buy", "sip")

    @property
    def is_buy(self) -> bool:
        return self.txn_type in self.BUY_TYPES


class UpstoxConnection(Base, TimestampMixin):
    """
    Links a platform user to their Upstox broker identity.

    Deliberately does NOT store the access_token -- that lives in Redis
    (integrations/upstox/token_store.py) with a TTL to the next 03:30 IST
    expiry, matching Upstox's own lifecycle exactly. Persisting a token here
    would let a stale, already-dead token sit in Postgres looking valid.

    What IS persisted is the stable identity (upstox_user_id) and profile
    facts from the broker (exchanges/products enabled, POA status) that are
    useful even between logins -- e.g. to show "connect Upstox again" with the
    right account context instead of a blank slate.
    """

    __tablename__ = "upstox_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    # Upstox's own identifier (their UCC), NOT our user_id. This is the join
    # key token_store.py uses to look up the live Redis-held token.
    upstox_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    upstox_user_name: Mapped[str | None] = mapped_column(String(255))
    upstox_email: Mapped[str | None] = mapped_column(String(320))
    exchanges: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    products: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    has_extended_token: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    poa: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="upstox_connection")


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # pending | generating | completed | failed
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    scheme_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    investor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Nullable: a notification can be raised before an RM is assigned.
    rm_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_channel: Mapped[str] = mapped_column(String(32), default="in_app", nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    related_object_type: Mapped[str | None] = mapped_column(String(64))
    related_object_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # GET /notifications/mine is exactly this query: filter by investor,
        # newest first. A composite index keeps it one index scan.
        Index("idx_notifications_investor_created", "investor_user_id", "created_at"),
    )


__all__ = [
    "Base",
    "User",
    "RefreshToken",
    "InvestorProfile",
    "SchemeSelection",
    "Report",
    "Notification",
    "Transaction",
    "UpstoxConnection",
]
