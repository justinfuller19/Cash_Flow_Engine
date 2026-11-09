"""Smoke tests for browser-based editing controls."""
from fastapi.testclient import TestClient

from app.main import app


def test_setup_and_obligation_pages_expose_edit_controls():
    client = TestClient(app)
    setup = client.get("/setup")
    bills = client.get("/bills")
    assert setup.status_code == 200
    assert "Edit member" in setup.text
    assert "Edit income" in setup.text
    assert "Deactivate member" in setup.text
    assert bills.status_code == 200
    assert "Edit bill" in bills.text
    assert "Edit debt" in bills.text

def test_home_dashboard_uses_pay_period_metrics_instead_of_weekly_safe_to_spend():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "/api/cash-flow/pay-periods" in response.text
    assert "Current cash" in response.text
    assert "Reserved cash" in response.text
    assert "Maximum spendable" in response.text
    assert "Spendable while keeping target" in response.text
    assert "Safe to spend (13-week forecast)" not in response.text
    assert "per financial week" not in response.text
    assert "per pay period" in response.text
