"""Pure, deterministic payday allocation engine.

Money is represented as integer cents. This module deliberately has no web or
 database dependencies so the financial rules are easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Sequence

from .buffer import buffer_reservation_cents

DebtStrategy = Literal["avalanche", "snowball"]


@dataclass(frozen=True)
class Income:
    name: str
    amount_cents: int
    received_date: date


@dataclass(frozen=True)
class BufferReservation:
    name: str
    amount_cents: int


@dataclass(frozen=True)
class Bill:
    name: str
    amount_cents: int
    due_date: date


@dataclass(frozen=True)
class CreditCard:
    name: str
    balance_cents: int
    minimum_payment_cents: int
    due_date: date
    apr_percent: float
    active: bool = True


@dataclass(frozen=True)
class SavingsGoal:
    name: str
    target_amount_cents: int
    current_balance_cents: int
    remaining_periods: int
    priority: int = 100
    contribution_per_period_cents: int | None = None


@dataclass(frozen=True)
class PaydayRequest:
    plan_date: date
    period_start: date
    period_end: date
    starting_cash_cents: int
    incomes: Sequence[Income] = field(default_factory=tuple)
    buffers: Sequence[BufferReservation] = field(default_factory=tuple)
    bills: Sequence[Bill] = field(default_factory=tuple)
    credit_cards: Sequence[CreditCard] = field(default_factory=tuple)
    savings_goals: Sequence[SavingsGoal] = field(default_factory=tuple)
    debt_strategy: DebtStrategy = "avalanche"


@dataclass(frozen=True)
class PlanItem:
    category: str
    name: str
    requested_cents: int
    funded_cents: int
    unfunded_cents: int
    due_date: date | None = None
    note: str | None = None


@dataclass(frozen=True)
class PaydayPlan:
    plan_date: date
    period_start: date
    period_end: date
    starting_cash_cents: int
    income_cents: int
    total_available_cents: int
    items: tuple[PlanItem, ...]
    allocated_cents: int
    unallocated_cents: int
    unfunded_cents: int
    target_card: str | None
    debt_strategy: DebtStrategy

    @property
    def is_fully_funded(self) -> bool:
        return self.unfunded_cents == 0


def _validate_nonnegative(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer number of cents")


def _validate_request(request: PaydayRequest) -> None:
    if request.period_end < request.period_start:
        raise ValueError("period_end must be on or after period_start")
    _validate_nonnegative(request.starting_cash_cents, "starting_cash_cents")
    if request.debt_strategy not in ("avalanche", "snowball"):
        raise ValueError("debt_strategy must be 'avalanche' or 'snowball'")
    for group_name, group in (
        ("income", request.incomes),
        ("buffer", request.buffers),
        ("bill", request.bills),
        ("credit card balance", [c.balance_cents for c in request.credit_cards]),
        ("credit card minimum", [c.minimum_payment_cents for c in request.credit_cards]),
        ("savings target", [g.target_amount_cents for g in request.savings_goals]),
        ("savings balance", [g.current_balance_cents for g in request.savings_goals]),
        ("savings contribution", [g.contribution_per_period_cents for g in request.savings_goals if g.contribution_per_period_cents is not None]),
    ):
        for entry in group:
            amount = entry.amount_cents if hasattr(entry, "amount_cents") else entry
            _validate_nonnegative(amount, group_name)
    for goal in request.savings_goals:
        if goal.remaining_periods < 1:
            raise ValueError(f"Savings goal '{goal.name}' must have at least one remaining period")
    for card in request.credit_cards:
        if card.apr_percent < 0:
            raise ValueError(f"Credit card '{card.name}' APR cannot be negative")


def _cents_for_goal(goal: SavingsGoal) -> int:
    """Required contribution per period, rounded up to the next cent."""
    remaining = max(0, goal.target_amount_cents - goal.current_balance_cents)
    if goal.contribution_per_period_cents is not None:
        return min(remaining, goal.contribution_per_period_cents)
    return (remaining + goal.remaining_periods - 1) // goal.remaining_periods


def generate_payday_plan(request: PaydayRequest) -> PaydayPlan:
    """Generate an ordered allocation plan for a single pay period.

    Waterfall: bills due in the window, card minimums due in the window,
    household buffer reservation, savings goals in priority order, then extra
    payment to the avalanche/snowball target. Required obligations take
    priority over rebuilding the buffer. Each item distinguishes its requested
    amount from the amount actually funded, so shortfalls are explicit.
    """
    _validate_request(request)

    incomes_in_window = [
        income for income in request.incomes
        if request.period_start <= income.received_date <= request.period_end
    ]
    income_total = sum(i.amount_cents for i in incomes_in_window)
    available = request.starting_cash_cents + income_total
    total_available = available
    items: list[PlanItem] = []

    def allocate(category: str, name: str, requested: int, due_date: date | None = None,
                 note: str | None = None) -> int:
        nonlocal available
        # Do not allow a malformed internal request to spend a negative amount.
        requested = max(0, requested)
        funded = min(available, requested)
        available -= funded
        items.append(PlanItem(
            category=category,
            name=name,
            requested_cents=requested,
            funded_cents=funded,
            unfunded_cents=requested - funded,
            due_date=due_date,
            note=note,
        ))
        return funded

    # 1. Bills due in the pay-period window, in due-date order.
    bills_due = sorted(
        (b for b in request.bills if request.period_start <= b.due_date <= request.period_end),
        key=lambda b: (b.due_date, b.name.casefold()),
    )
    for bill in bills_due:
        allocate("bill", bill.name, bill.amount_cents, due_date=bill.due_date)

    # 2. Minimums due this period; never plan to pay more than the recorded balance.
    cards = [c for c in request.credit_cards if c.active]
    minimum_funded_by_card: dict[str, int] = {}
    minimum_due = sorted(
        (c for c in cards if request.period_start <= c.due_date <= request.period_end),
        key=lambda c: (c.due_date, c.name.casefold()),
    )
    for card in minimum_due:
        requested = min(card.minimum_payment_cents, card.balance_cents)
        funded = allocate("debt_minimum", f"{card.name} minimum payment", requested,
                          due_date=card.due_date)
        minimum_funded_by_card[card.name] = funded

    # 3. Protect the shared household buffer only after required obligations.
    # Keep the requested target visible when cash is insufficient, but only
    # leave cash above the target available for later discretionary categories.
    for buffer in request.buffers:
        funded, shortfall, remaining = buffer_reservation_cents(
            available, buffer.amount_cents
        )
        items.append(PlanItem(
            category="buffer",
            name=buffer.name,
            requested_cents=buffer.amount_cents,
            funded_cents=funded,
            unfunded_cents=shortfall,
            note="Household buffer protected after bills and minimum debt payments",
        ))
        available = remaining

    # 4. Goal contributions: priority ascending, then name for deterministic results.
    goals = sorted(request.savings_goals, key=lambda g: (g.priority, g.name.casefold()))
    for goal in goals:
        requested = _cents_for_goal(goal)
        if requested:
            allocate("savings", goal.name, requested,
                     note=f"Required per-period contribution; {goal.remaining_periods} periods remaining")

    # 5. Route all remaining cash to the chosen eligible debt target.
    # Use one active card per unique name to keep payment accounting unambiguous.
    if request.debt_strategy == "avalanche":
        ordered_cards = sorted(cards, key=lambda c: (-c.apr_percent, c.balance_cents, c.name.casefold()))
    else:
        ordered_cards = sorted(cards, key=lambda c: (c.balance_cents, -c.apr_percent, c.name.casefold()))

    target: CreditCard | None = None
    for card in ordered_cards:
        planned_minimum = minimum_funded_by_card.get(card.name, 0)
        if card.balance_cents - planned_minimum > 0:
            target = card
            break

    if target and available > 0:
        # If the priority card is paid off with this allocation, continue to the
        # next card in the selected strategy rather than leaving cash idle.
        # This also prevents paying any card beyond its recorded balance.
        for card in ordered_cards:
            planned_minimum = minimum_funded_by_card.get(card.name, 0)
            remaining_balance = max(0, card.balance_cents - planned_minimum)
            if remaining_balance <= 0 or available <= 0:
                continue
            extra = min(available, remaining_balance)
            allocate(
                "debt_extra",
                f"Extra payoff to {card.name}",
                extra,
                note=f"{request.debt_strategy.title()} strategy; {card.apr_percent:.2f}% APR",
            )

    allocated = sum(item.funded_cents for item in items)
    unfunded = sum(item.unfunded_cents for item in items)
    # Accounting invariant: sources are either allocated or remain unallocated.
    assert total_available == allocated + available, "Cash allocation arithmetic does not balance"

    return PaydayPlan(
        plan_date=request.plan_date,
        period_start=request.period_start,
        period_end=request.period_end,
        starting_cash_cents=request.starting_cash_cents,
        income_cents=income_total,
        total_available_cents=total_available,
        items=tuple(items),
        allocated_cents=allocated,
        unallocated_cents=available,
        unfunded_cents=unfunded,
        target_card=target.name if target else None,
        debt_strategy=request.debt_strategy,
    )
