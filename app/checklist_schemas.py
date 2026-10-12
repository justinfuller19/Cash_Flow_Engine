"""Schemas for updating payday checklist actions."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ChecklistStatus = Literal["pending", "completed", "skipped", "attention"]


class ChecklistItemUpdate(BaseModel):
    status: ChecklistStatus | None = None
    actual_amount_cents: int | None = Field(default=None, ge=0)
    actual_date: date | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_status(cls, value: Any) -> Any:
        if isinstance(value, dict) and "status" in value and value["status"] is None:
            raise ValueError("status cannot be null")
        return value


class ChecklistItemOut(BaseModel):
    id: int
    plan_id: int
    plan_period_start: date
    plan_period_end: date
    category: str
    name: str
    requested_cents: int
    funded_cents: int
    unfunded_cents: int
    due_date: date | None
    note: str | None
    status: ChecklistStatus
    actual_amount_cents: int | None
    actual_date: date | None
