"""Validation schemas for the manual transaction ledger."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TransactionCreate(BaseModel):
    transaction_date: date
    category: str = Field(min_length=1, max_length=40)
    transaction_type: Literal['income', 'expense', 'transfer'] | None = None
    linked_obligation_type: Literal['income', 'bill', 'debt'] | None = None
    linked_obligation_id: int | None = Field(default=None, gt=0)
    description: str = Field(min_length=1, max_length=200)
    amount_cents: int
    account_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_amount_sign(self):
        if self.transaction_type == "expense" and self.amount_cents >= 0:
            raise ValueError("Expense amounts must be negative; record refunds as income.")
        if self.transaction_type == "income" and self.amount_cents <= 0:
            raise ValueError("Income amounts must be positive.")
        if self.amount_cents == 0:
            raise ValueError("Transaction amount must not be zero.")
        return self


class TransactionUpdate(BaseModel):
    transaction_date: date | None = None
    category: str | None = Field(default=None, min_length=1, max_length=40)
    transaction_type: Literal['income', 'expense', 'transfer'] | None = None
    linked_obligation_type: Literal['income', 'bill', 'debt'] | None = None
    linked_obligation_id: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, min_length=1, max_length=200)
    amount_cents: int | None = None
    account_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value: Any) -> Any:
        nullable = {"account_name", "notes", "linked_obligation_type", "linked_obligation_id"}
        if isinstance(value, dict) and any(
            item is None for key, item in value.items() if key not in nullable
        ):
            raise ValueError("Only account_name and notes may be cleared with null")
        return value

    @model_validator(mode="after")
    def validate_amount_sign(self):
        if self.amount_cents is not None and self.transaction_type == "expense" and self.amount_cents >= 0:
            raise ValueError("Expense amounts must be negative; record refunds as income.")
        if self.amount_cents is not None and self.transaction_type == "income" and self.amount_cents <= 0:
            raise ValueError("Income amounts must be positive.")
        if self.amount_cents == 0:
            raise ValueError("Transaction amount must not be zero.")
        return self


class TransactionOut(BaseModel):
    id: int
    transaction_date: date
    category: str
    transaction_type: Literal['income', 'expense', 'transfer']
    linked_obligation_type: Literal['income', 'bill', 'debt'] | None
    linked_obligation_id: int | None
    description: str
    amount_cents: int
    account_name: str | None
    notes: str | None
    created_at: str
