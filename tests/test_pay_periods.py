"""Paycheck grouping and configurable pay-period boundaries."""
from datetime import date

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.pay_periods import group_paychecks


def test_two_day_paychecks_group_and_period_starts_on_last_payday():
    groups = group_paychecks([date(2026, 11, 11), date(2026, 11, 13), date(2026, 11, 20)])
    assert len(groups) == 2
    assert groups[0]["paycheck_dates"] == (date(2026, 11, 11), date(2026, 11, 13))
    assert groups[0]["period_start"] == date(2026, 11, 13)
    assert groups[0]["period_end"] == date(2026, 11, 19)
    assert groups[1]["period_start"] == date(2026, 11, 20)
    assert groups[1]["period_end"] is None


def test_friday_and_following_wednesday_are_separate_groups():
    groups = group_paychecks([date(2026, 11, 13), date(2026, 11, 18)])
    assert len(groups) == 2
    assert groups[0]["period_start"] == date(2026, 11, 13)
    assert groups[0]["period_end"] == date(2026, 11, 17)
    assert groups[1]["period_start"] == date(2026, 11, 18)


def test_grouping_window_is_measured_from_first_paycheck_not_previous():
    groups = group_paychecks([
        date(2026, 11, 9), date(2026, 11, 11), date(2026, 11, 13)
    ], window_days=2)
    assert [group["paycheck_dates"] for group in groups] == [
        (date(2026, 11, 9), date(2026, 11, 11)),
        (date(2026, 11, 13),)
    ]


def test_grouping_window_accepts_exact_boundary_and_deduplicates_dates():
    groups = group_paychecks([
        date(2026, 11, 13), date(2026, 11, 15), date(2026, 11, 15)
    ], window_days=2)
    assert len(groups) == 1
    assert groups[0]["paycheck_dates"] == (date(2026, 11, 13), date(2026, 11, 15))


def test_grouping_window_rejects_out_of_range_values():
    for value in (-1, 15, True, 2.5):
        try:
            group_paychecks([date(2026, 11, 13)], window_days=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid window to be rejected: {value!r}")


def test_household_grouping_setting_defaults_to_two_days_and_is_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "pay-period.db"))
    database.initialize_database()
    client = TestClient(app)
    settings = client.get("/api/household/settings").json()
    assert settings["paycheck_group_window_days"] == 2
    updated = client.patch("/api/household/settings", json={"paycheck_group_window_days": 4})
    assert updated.status_code == 200
    assert updated.json()["paycheck_group_window_days"] == 4


def test_household_grouping_setting_rejects_invalid_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "invalid-pay-period.db"))
    database.initialize_database()
    client = TestClient(app)
    for value in (-1, 15, 2.5, None):
        response = client.patch("/api/household/settings", json={"paycheck_group_window_days": value})
        assert response.status_code == 422


def test_pay_period_settings_page_is_available(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "pay-period-page.db"))
    database.initialize_database()
    response = TestClient(app).get("/pay-period-settings")
    assert response.status_code == 200
    assert "Pay Period Settings" in response.text
    assert "first paycheck in its group" in response.text

