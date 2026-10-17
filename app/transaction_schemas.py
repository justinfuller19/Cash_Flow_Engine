"""Validation schemas for the manual transaction ledger."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TransactionCreate(BaseModel):
    transaction_date: date
    category: str = Field(min_length=1, max_length=40)
    transaction_type: Literal['income', 'expense', 'transfer'] | None = None
    description: str = Field(min_length=1, max_length=200)
    amount_cents: int
    account_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class TransactionUpdate(BaseModel):
    transaction_date: date | None = None
    category: str | None = Field(default=None, min_length=1, max_length=40)
    transaction_type: Literal['income', 'expense', 'transfer'] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=200)
    amount_cents: int | None = None
    account_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value: Any) -> Any:
        nullable = {"account_name", "notes"}
        if isinstance(value, dict) and any(
            item is None for key, item in value.items() if key not in nullable
        ):
            raise ValueError("Only account_name and notes may be cleared with null")
        return value


class TransactionOut(BaseModel):
    id: int
    transaction_date: date
    category: str
    transaction_type: Literal['income', 'expense', 'transfer']
    description: str
    amount_cents: int
    account_name: str | None
    notes: str | None
    created_at: str
