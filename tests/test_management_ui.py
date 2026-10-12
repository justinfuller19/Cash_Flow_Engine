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
