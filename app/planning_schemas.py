"""Schemas for reading historical saved payday plans."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SavedPlanItemOut(BaseModel):
    id: int
    category: str
    name: str
    requested_cents: int
    funded_cents: int
    unfunded_cents: int
    due_date: date | None
    note: str | None
    status: Literal["pending", "completed", "skipped", "attention"]
    actual_amount_cents: int | None
    actual_date: date | None
    is_completed: bool


class SavedPlanOut(BaseModel):
    id: int
    plan_date: date
    period_start: date
    period_end: date
    starting_cash_cents: int
    income_cents: int
    total_available_cents: int
    allocated_cents: int
    unallocated_cents: int
    unfunded_cents: int
    target_card: str | None
    debt_strategy: str
    status: str
    is_auto_generated: bool = False
    created_at: str
    is_fully_funded: bool
    items: list[SavedPlanItemOut] = Field(default_factory=list)
