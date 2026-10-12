"""Pydantic schemas for persistent household setup endpoints."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


IncomeFrequency = Literal["weekly", "biweekly", "semimonthly", "monthly", "irregular"]


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    default_buffer_cents: int = Field(default=0, ge=0)


class PersonOut(BaseModel):
    id: int
    name: str
    default_buffer_cents: int
    active: bool
    created_at: str


class PersonUpdate(BaseModel):
    """Partial update for a household member; only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_buffer_cents: int | None = Field(default=None, ge=0)
    active: bool | None = None


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
