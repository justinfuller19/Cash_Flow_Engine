"""Schemas for generating and reviewing saved payday plans."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_date: date
    period_start: date
    period_end: date
    starting_cash_cents: int = Field(ge=0)
    debt_strategy: Literal["avalanche", "snowball"] = "avalanche"

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class SavedPlanItemOut(BaseModel):
    id: int
    category: str
    name: str
    requested_cents: int
    funded_cents: int
    unfunded_cents: int
    due_date: date | None
    note: str | None
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
    created_at: str
    is_fully_funded: bool
    items: list[SavedPlanItemOut] = []
