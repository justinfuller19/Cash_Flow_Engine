"""Pydantic schemas for persistent household setup endpoints."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


IncomeFrequency = Literal["weekly", "biweekly", "semimonthly", "monthly", "irregular"]
Weekday = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PersonOut(BaseModel):
    id: int
    name: str
    active: bool
    created_at: str


class PersonUpdate(BaseModel):
    """Partial update for a household member; only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        """PATCH fields are optional, but null is not a valid replacement value."""
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("PATCH fields cannot be null")
        return value


class HouseholdSettingsOut(BaseModel):
    """The single shared household cash configuration row."""

    buffer_target_cents: int
    starting_cash_cents: int
    financial_week_start: Weekday = "Friday"
    updated_at: str


class HouseholdSettingsUpdate(BaseModel):
    """Partial update for household settings; only provided fields change."""

    buffer_target_cents: int | None = Field(default=None, ge=0)
    starting_cash_cents: int | None = Field(default=None, ge=0)
    financial_week_start: Weekday | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        """PATCH fields are optional, but null is not a valid replacement value."""
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("PATCH fields cannot be null")
        return value


class IncomeSourceCreate(BaseModel):
    person_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    net_amount_cents: int = Field(ge=0)
    frequency: IncomeFrequency
    next_payday: date
    active: bool = True


class IncomeSourceOut(BaseModel):
    id: int
    person_id: int
    name: str
    net_amount_cents: int
    frequency: IncomeFrequency
    next_payday: date
    active: bool
    created_at: str


class IncomeSourceUpdate(BaseModel):
    """Partial update for an income source; only provided fields change."""

    person_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    net_amount_cents: int | None = Field(default=None, ge=0)
    frequency: IncomeFrequency | None = None
    next_payday: date | None = None
    active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        """PATCH fields are optional, but null is not a valid replacement value."""
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("PATCH fields cannot be null")
        return value
