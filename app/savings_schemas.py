"""Validation schemas for savings goals."""
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, model_validator


class SavingsGoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_amount_cents: int = Field(ge=0)
    current_balance_cents: int = Field(default=0, ge=0)
    target_date: date | None = None
    priority: int = Field(default=100, ge=0, le=10000)
    weekly_target_cents: int = Field(default=0, ge=0)
    schedule_start_date: date | None = None
    active: bool = True


class SavingsGoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target_amount_cents: int | None = Field(default=None, ge=0)
    current_balance_cents: int | None = Field(default=None, ge=0)
    target_date: date | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    weekly_target_cents: int | None = Field(default=None, ge=0)
    schedule_start_date: date | None = None
    active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_invalid_nulls(cls, value: Any) -> Any:
        # target_date is intentionally nullable so a user can clear a goal date.
        if isinstance(value, dict) and any(
            item is None for key, item in value.items() if key != "target_date"
        ):
            raise ValueError("PATCH fields cannot be null (except target_date)")
        return value


class SavingsGoalOut(BaseModel):
    id: int
    name: str
    target_amount_cents: int
    current_balance_cents: int
    target_date: date | None
    priority: int
    weekly_target_cents: int = 0
    schedule_start_date: date | None = None
    active: bool
    created_at: str
