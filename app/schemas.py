"""HTTP request/response schemas for the preview API."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class IncomeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=0)
    received_date: date


class BufferIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=0)


class BillIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=0)
    due_date: date


class CreditCardIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    balance_cents: int = Field(ge=0)
    minimum_payment_cents: int = Field(ge=0)
    due_date: date
    apr_percent: float = Field(ge=0, le=1000)
    active: bool = True


class SavingsGoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_amount_cents: int = Field(ge=0)
    current_balance_cents: int = Field(ge=0)
    remaining_periods: int = Field(ge=1, le=1200)
    priority: int = 100


class PaydayPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_date: date
    period_start: date
    period_end: date
    starting_cash_cents: int = Field(ge=0)
    incomes: list[IncomeIn] = []
    buffers: list[BufferIn] = []
    bills: list[BillIn] = []
    credit_cards: list[CreditCardIn] = []
    savings_goals: list[SavingsGoalIn] = []
    debt_strategy: Literal["avalanche", "snowball"] = "avalanche"

    @model_validator(mode="after")
    def validate_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class PlanItemOut(BaseModel):
    category: str
    name: str
    requested_cents: int
    funded_cents: int
    unfunded_cents: int
    due_date: date | None = None
    note: str | None = None


class PaydayPlanOut(BaseModel):
    plan_date: date
    period_start: date
    period_end: date
    starting_cash_cents: int
    income_cents: int
    total_available_cents: int
    items: list[PlanItemOut]
    allocated_cents: int
    unallocated_cents: int
    unfunded_cents: int
    target_card: str | None
    debt_strategy: str
    is_fully_funded: bool
