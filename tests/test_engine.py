from datetime import date
import pytest

from app.engine import (
    Bill, BufferReservation, CreditCard, Income, PaydayRequest, SavingsGoal,
    generate_payday_plan,
)

START = date(2026, 11, 13)
END = date(2026, 11, 26)


def sample_request(**overrides):
    values = dict(
        plan_date=START,
        period_start=START,
        period_end=END,
        starting_cash_cents=0,
        incomes=[Income("Paycheck", 280000, START)],
        buffers=[BufferReservation("Personal cash buffer", 50000)],
        bills=[Bill("Electric", 14000, date(2026, 11, 18)), Bill("Water", 6500, date(2026, 11, 22))],
        credit_cards=[
            CreditCard("Chase Sapphire", 166000, 8000, date(2026, 11, 20), 24.9),
            CreditCard("Store Card", 40000, 3000, date(2026, 11, 21), 18.0),
        ],
        savings_goals=[
            SavingsGoal("Emergency HYSA", 100000, 60000, 2, 1),
            SavingsGoal("Vacation", 50000, 0, 4, 2),
        ],
        debt_strategy="avalanche",
    )
    values.update(overrides)
    return PaydayRequest(**values)


def test_waterfall_order_and_balances():
    plan = generate_payday_plan(sample_request())
    categories = [item.category for item in plan.items]
    assert categories[0] == "bill"
    assert categories.index("bill") < categories.index("debt_minimum")
    assert categories.index("debt_minimum") < categories.index("buffer")
    assert categories.index("buffer") < categories.index("savings")
    assert categories[-1] == "debt_extra"
    assert plan.target_card == "Chase Sapphire"
    assert plan.total_available_cents == plan.allocated_cents + plan.unallocated_cents
    assert plan.unallocated_cents == 0
    assert plan.unfunded_cents == 0


def test_only_income_received_inside_window_counts():
    plan = generate_payday_plan(sample_request(
        incomes=[
            Income("Before window", 50000, date(2026, 11, 1)),
            Income("Payday", 100000, START),
            Income("After window", 75000, date(2026, 12, 1)),
        ],
        buffers=[], bills=[], credit_cards=[], savings_goals=[],
    ))
    assert plan.income_cents == 100000
    assert plan.unallocated_cents == 10000000


def test_shortfalls_are_explicit_and_never_make_cash_negative():
    plan = generate_payday_plan(sample_request(
        incomes=[Income("Small paycheck", 10000, START)],
        buffers=[BufferReservation("Buffer", 7000)],
        bills=[Bill("Rent", 8000, date(2026, 11, 18))],
        credit_cards=[], savings_goals=[],
    ))
    assert plan.items[0].category == "bill"
    assert plan.items[0].funded_cents == 8000
    assert plan.items[0].unfunded_cents == 0
    assert plan.items[1].category == "buffer"
    assert plan.items[1].funded_cents == 2000
    assert plan.items[1].unfunded_cents == 5000
    assert plan.unallocated_cents == 0
    assert plan.unfunded_cents == 5000
    assert plan.is_fully_funded is False


def test_snowball_targets_lowest_balance_card():
    plan = generate_payday_plan(sample_request(debt_strategy="snowball", buffers=[], bills=[], savings_goals=[]))
    assert plan.target_card == "Store Card"


def test_savings_contribution_rounds_up_to_cents():
    plan = generate_payday_plan(sample_request(
        incomes=[Income("Paycheck", 10000, START)],
        buffers=[], bills=[], credit_cards=[],
        savings_goals=[SavingsGoal("Goal", 100, 0, 3)],
    ))
    assert plan.items[0].requested_cents == 34


def test_invalid_pay_period_is_rejected():
    with pytest.raises(ValueError, match="period_end"):
        generate_payday_plan(sample_request(period_start=END, period_end=START))



def test_buffer_is_a_protected_floor_and_shortfall_is_explicit():
    plan = generate_payday_plan(sample_request(
        starting_cash_cents=0,
        incomes=[Income("Paycheck", 10000, START)],
        buffers=[BufferReservation("Household buffer", 7000)],
        bills=[Bill("Rent", 8000, date(2026, 11, 18))],
        credit_cards=[], savings_goals=[],
    ))
    buffer_item = next(item for item in plan.items if item.category == "buffer")
    assert buffer_item.requested_cents == 7000
    assert buffer_item.funded_cents == 2000
    assert buffer_item.unfunded_cents == 5000
    assert plan.unallocated_cents == 0
    assert plan.unfunded_cents == 5000


def test_buffer_reservation_does_not_consume_cash_twice():
    plan = generate_payday_plan(sample_request(
        starting_cash_cents=5000,
        incomes=[Income("Paycheck", 10000, START)],
        buffers=[BufferReservation("Household buffer", 5000)],
        bills=[Bill("Rent", 4000, date(2026, 11, 18))],
        credit_cards=[], savings_goals=[],
    ))
    buffer_item = next(item for item in plan.items if item.category == "buffer")
    assert buffer_item.funded_cents == 5000
    assert plan.total_available_cents == plan.allocated_cents + plan.unallocated_cents
    assert plan.unallocated_cents == 6000
