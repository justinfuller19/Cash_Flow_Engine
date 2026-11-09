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
    assert result["reserved_cash_cents"] == 345000


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
        {"name": "Rent", "due_date": "2026-11-10", "unfunded_cents": 10000},
    ]
    assert result["total_bill_shortfall_cents"] == 10000


def test_transaction_that_preserves_bill_reservations_needs_no_confirmation():
    result = transaction_reservation_impact(2000, 30000, [
        {"name": "Rent", "due_date": "2026-11-10", "amount_cents": 12000, "paid_cents": 0},
        {"name": "Electric", "due_date": "2026-11-12", "amount_cents": 8000, "paid_cents": 0},
    ])
    assert not result["requires_confirmation"]
    assert result["underfunded_bills"] == []


def test_transaction_warning_excludes_preexisting_bill_shortfalls():
    bills = [
        {"name": "Rent", "due_date": "2026-11-10", "amount_cents": 12000, "paid_cents": 0},
        {"name": "Electric", "due_date": "2026-11-12", "amount_cents": 8000, "paid_cents": 0},
    ]
    no_new_impact = transaction_reservation_impact(0, 10000, bills)
    assert no_new_impact["requires_confirmation"] is False
    assert no_new_impact["total_bill_shortfall_cents"] == 0

    new_impact = transaction_reservation_impact(5000, 10000, bills)
    assert new_impact["requires_confirmation"] is True
    assert new_impact["underfunded_bills"] == [
        {"name": "Rent", "due_date": "2026-11-10", "unfunded_cents": 5000},
    ]

def test_deadline_savings_is_prorated_over_remaining_pay_periods():
    from app.pay_period_forecast import plan_savings_contributions

    result = plan_savings_contributions(
        [{"id": 7, "name": "Vacation", "target_amount_cents": 100000,
          "current_balance_cents": 0, "target_date": "2026-11-15"}],
        ["2026-11-01", "2026-11-08", "2026-11-15"],
    )[0]
    assert [p["planned_contribution_cents"] for p in result["periods"]] == [
        33334, 33333, 33333
    ]
    assert result["remaining_target_cents"] == 0


def test_missed_savings_contribution_carries_forward_to_fewer_periods():
    from app.pay_period_forecast import plan_savings_contributions

    result = plan_savings_contributions(
        [{"id": 8, "target_amount_cents": 100000, "current_balance_cents": 0,
          "target_date": "2026-11-15"}],
        ["2026-11-08", "2026-11-15"],
    )[0]
    assert [p["planned_contribution_cents"] for p in result["periods"]] == [
        50000, 50000
    ]


def test_ongoing_savings_uses_fixed_per_period_amount_and_subtracts_actual():
    from app.pay_period_forecast import plan_savings_contributions

    result = plan_savings_contributions(
        [{"id": 9, "name": "General savings", "target_amount_cents": 0,
          "current_balance_cents": 0, "ongoing_per_period_cents": 5000}],
        ["2026-11-01", "2026-11-08"],
        actual_contributions={(9, "2026-11-01"): 2000},
    )[0]
    assert [p["planned_contribution_cents"] for p in result["periods"]] == [
        3000, 5000
    ]

def test_debt_interest_is_added_before_payment_and_balance_stops_at_zero():
    from app.pay_period_forecast import project_debt_schedule

    result = project_debt_schedule(
        [{
            "id": 1, "name": "Card", "balance_cents": 100000,
            "minimum_payment_cents": 10000, "planned_payment_cents": 15000,
            "apr_percent": 12.0, "interest_date": "2026-11-15",
            "next_due_date": "2026-11-20",
        }],
        [
            {"start_date": "2026-11-01", "end_date": "2026-11-30"},
            {"start_date": "2026-12-01", "end_date": "2026-12-31"},
        ],
        as_of_date="2026-11-10",
    )
    assert [p["estimated_interest_cents"] for p in result["periods"]] == [1000, 860]
    assert [p["debt_payments_cents"] for p in result["periods"]] == [15000, 15000]
    assert result["debts"][0]["projected_balance_cents"] == 71860
    assert [event["type"] for event in result["events"]] == [
        "interest", "payment", "interest", "payment"
    ]


def test_debt_payment_is_capped_at_remaining_balance_and_future_payments_stop():
    from app.pay_period_forecast import project_debt_schedule

    result = project_debt_schedule(
        [{
            "id": 2, "name": "Small balance", "balance_cents": 5000,
            "minimum_payment_cents": 10000, "planned_payment_cents": 12000,
            "apr_percent": 0, "interest_date": None,
            "next_due_date": "2026-11-20",
        }],
        [
            {"start_date": "2026-11-01", "end_date": "2026-11-30"},
            {"start_date": "2026-12-01", "end_date": "2026-12-31"},
        ],
        as_of_date="2026-11-10",
    )
    assert result["periods"][0]["debt_payments_cents"] == 5000
    assert result["periods"][1]["debt_payments_cents"] == 0
    assert result["debts"][0]["projected_balance_cents"] == 0


def test_debt_interest_recurrence_handles_year_boundary():
    from app.pay_period_forecast import project_debt_schedule

    result = project_debt_schedule(
        [{
            "id": 3, "name": "Loan", "balance_cents": 100000,
            "minimum_payment_cents": 0, "planned_payment_cents": 0,
            "apr_percent": 12.0, "interest_date": "2026-12-15",
            "next_due_date": "2027-01-20",
        }],
        [{"start_date": "2027-01-01", "end_date": "2027-01-31"}],
        as_of_date="2026-12-20",
    )
    assert result["periods"][0]["estimated_interest_cents"] == 1000
    assert result["periods"][0]["debt_payments_cents"] == 0

def test_proposed_transaction_warns_only_for_new_bill_underfunding_after_future_income():
    from app.pay_period_forecast import transaction_bill_reservation_impact_from_forecast

    forecast = {
        "generated_on": "2026-11-01",
        "current_cash_cents": 100000,
        "periods": [{
            "start_date": "2026-11-01", "end_date": "2026-11-30",
            "income_items": [{"date": "2026-11-07", "amount_cents": 100000}],
            "transactions": [], "debt_events": [], "savings": [],
            "bills": [
                {"bill_id": 1, "name": "Rent", "due_date": "2026-11-05", "amount_cents": 80000},
                {"bill_id": 2, "name": "Electric", "due_date": "2026-11-10", "amount_cents": 50000},
            ],
        }],
    }
    impact = transaction_bill_reservation_impact_from_forecast(
        forecast, 30000, "2026-11-03"
    )
    assert impact["requires_confirmation"] is True
    assert impact["underfunded_bills"] == [
        {"name": "Rent", "due_date": "2026-11-05", "unfunded_cents": 10000}
    ]
    assert impact["total_bill_shortfall_cents"] == 10000


def test_proposed_transaction_does_not_warn_for_preexisting_shortfall_increment():
    from app.pay_period_forecast import transaction_bill_reservation_impact_from_forecast

    forecast = {
        "generated_on": "2026-11-01", "current_cash_cents": 50000,
        "periods": [{
            "start_date": "2026-11-01", "end_date": "2026-11-30",
            "income_items": [], "transactions": [], "debt_events": [], "savings": [],
            "bills": [{"bill_id": 3, "name": "Rent", "due_date": "2026-11-05",
                       "amount_cents": 80000}],
        }],
    }
    impact = transaction_bill_reservation_impact_from_forecast(
        forecast, 10000, "2026-11-03"
    )
    assert impact["underfunded_bills"] == [
        {"name": "Rent", "due_date": "2026-11-05", "unfunded_cents": 10000}
    ]

def test_bill_funding_report_uses_future_paychecks_and_identifies_exact_shortfall():
    from app.pay_period_forecast import bill_funding_shortfalls_from_forecast

    forecast = {
        "current_cash_cents": 70000,
        "periods": [{
            "end_date": "2026-11-30",
            "income_items": [{"date": "2026-11-07", "amount_cents": 100000}],
            "transactions": [], "debt_events": [], "savings": [],
            "bills": [{"bill_id": 10, "name": "Rent", "due_date": "2026-11-05",
                       "amount_cents": 80000}],
        }],
    }
    assert bill_funding_shortfalls_from_forecast(forecast) == [{
        "bill_id": 10, "name": "Rent", "due_date": "2026-11-05",
        "shortfall_cents": 10000,
    }]
