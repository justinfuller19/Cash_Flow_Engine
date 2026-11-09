from app.pay_period_forecast import calculate_pay_periods, transaction_reservation_impact


def _example():
    return [
        {"start_date": "P1", "starting_cash_cents": 120000, "income_cents": 0,
         "bills_cents": 30000, "savings_cents": 5000},
        {"start_date": "P2", "income_cents": 100000, "bills_cents": 40000, "savings_cents": 5000},
        {"start_date": "P3", "income_cents": 100000, "bills_cents": 225000, "savings_cents": 5000},
        {"start_date": "P4", "income_cents": 100000, "bills_cents": 30000, "savings_cents": 5000},
    ]


def test_backward_carry_over_matches_owner_example():
    result = calculate_pay_periods(_example(), 20000)
    periods = result["periods"]
    assert [p["projected_ending_cash_cents"] for p in periods] == [85000, 140000, 10000, 75000]
    assert [p["required_ending_cash_cents"] for p in periods] == [95000, 150000, 20000, 20000]
    assert [p["target_shortfall_cents"] for p in periods] == [10000, 10000, 10000, 0]
    assert [p["carry_out_above_target_cents"] for p in periods] == [75000, 130000, 0, 0]
    assert result["max_spendable_cents"] == 10000
    assert result["spendable_while_keeping_target_cents"] == 0


def test_soft_target_is_not_deducted_from_cash_or_reserved_cash():
    result = calculate_pay_periods(_example(), 20000)
    assert result["periods"][0]["projected_ending_cash_cents"] == 85000
    assert result["periods"][0]["outflow_cents"] == 35000
    assert result["reserved_cash_cents"] == 350000


def test_later_periods_start_with_prior_ending_cash():
    periods = calculate_pay_periods(_example(), 20000)["periods"]
    assert [p["starting_cash_cents"] for p in periods] == [120000, 85000, 140000, 10000]


def test_empty_horizon_returns_zero_metrics():
    result = calculate_pay_periods([], 20000)
    assert result["periods"] == []
    assert result["max_spendable_cents"] == 0


def test_invalid_money_values_are_rejected():
    for invalid in (-1, True, 1.5, "100"):
        try:
            calculate_pay_periods([{"starting_cash_cents": 100, "income_cents": invalid}], 0)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Accepted invalid amount: {invalid!r}")


def test_transaction_warning_names_underfunded_bills():
    result = transaction_reservation_impact(15000, 10000, [
        {"name": "Rent", "due_date": "2026-11-10", "amount_cents": 12000, "paid_cents": 0},
        {"name": "Electric", "due_date": "2026-11-12", "amount_cents": 8000, "paid_cents": 0},
    ])
    assert result["requires_confirmation"]
    assert result["underfunded_bills"] == [
        {"name": "Rent", "due_date": "2026-11-10", "unfunded_cents": 12000},
        {"name": "Electric", "due_date": "2026-11-12", "unfunded_cents": 8000},
    ]
    assert result["total_bill_shortfall_cents"] == 20000


def test_transaction_that_preserves_bill_reservations_needs_no_confirmation():
    result = transaction_reservation_impact(2000, 30000, [
        {"name": "Rent", "due_date": "2026-11-10", "amount_cents": 12000, "paid_cents": 0},
        {"name": "Electric", "due_date": "2026-11-12", "amount_cents": 8000, "paid_cents": 0},
    ])
    assert not result["requires_confirmation"]
    assert result["underfunded_bills"] == []
