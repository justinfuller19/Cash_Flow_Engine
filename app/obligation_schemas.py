"""Request and response schemas for recurring bills and debts."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


BillFrequency = Literal["weekly", "biweekly", "monthly", "quarterly", "yearly", "irregular"]


class PatchModel(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("PATCH fields cannot be null")
        return value


class BillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=0)
    next_due_date: date
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    flexible_window_days: int = Field(default=0, ge=0, le=31)
    category: str = Field(default="other", min_length=1, max_length=40)
    frequency: BillFrequency = "monthly"
    autopay: bool = False
    active: bool = True


class BillUpdate(PatchModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    amount_cents: int | None = Field(default=None, ge=0)
    next_due_date: date | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    flexible_window_days: int | None = Field(default=None, ge=0, le=31)
    category: str | None = Field(default=None, min_length=1, max_length=40)
    frequency: BillFrequency | None = None
    autopay: bool | None = None
    active: bool | None = None


class BillOut(BaseModel):
    id: int
    name: str
    amount_cents: int
    due_day_of_month: int | None
    next_due_date: date | None
    flexible_window_days: int
    category: str
    frequency: BillFrequency
    autopay: bool
    active: bool
    created_at: str


class DebtCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    balance_cents: int = Field(ge=0)
    minimum_payment_cents: int = Field(ge=0)
    apr_percent: float = Field(ge=0, le=1000)
    next_due_date: date
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    active: bool = True


class DebtUpdate(PatchModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    balance_cents: int | None = Field(default=None, ge=0)
    minimum_payment_cents: int | None = Field(default=None, ge=0)
    apr_percent: float | None = Field(default=None, ge=0, le=1000)
    next_due_date: date | None = None
    due_day_of_month: int | None = Field(default=None, ge=1, le=31)
    active: bool | None = None


class DebtOut(BaseModel):
    id: int
    name: str
    balance_cents: int
    minimum_payment_cents: int
    apr_percent: float
    due_day_of_month: int | None
    next_due_date: date | None
    active: bool
    created_at: str
