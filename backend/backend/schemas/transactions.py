"""Pydantic schemas for the transaction ledger and P&L."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TransactionCreate(BaseModel):
    scheme_id: str = Field(min_length=1, max_length=128)
    txn_type: Literal["buy", "sip", "sell"]

    # Decimal, not float: units carry 3-4 dp and NAVs 4. Float error accumulated
    # across 240 SIP instalments stops the holding reconciling with the AMC's
    # own statement.
    units: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    nav: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    txn_date: date

    # Optional: the AMC contract note is authoritative for the rupee figure
    # (units x nav can differ by paise from their rounding). Derived if omitted,
    # and cross-checked against units x nav if supplied.
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)

    # Idempotency key -- a retried POST from a flaky mobile connection must not
    # book the same purchase twice.
    external_ref: str | None = Field(default=None, max_length=128)

    @field_validator("txn_date")
    @classmethod
    def not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("A transaction cannot be dated in the future.")
        return v


class TransactionResponse(BaseModel):
    id: uuid.UUID
    scheme_id: str
    txn_type: str
    units: Decimal
    nav: Decimal
    amount: Decimal
    txn_date: date
    external_ref: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    transactions: list[TransactionResponse]
    count: int
