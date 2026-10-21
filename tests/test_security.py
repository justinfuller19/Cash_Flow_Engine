"""Tests for loopback, optional LAN access, and optional Basic authentication."""
import base64

from fastapi.testclient import TestClient

from app.main import app


def test_local_access_works_without_credentials(monkeypatch):
    monkeypatch.delenv("CASHFLOW_USERNAME", raising=False)
    monkeypatch.delenv("CASHFLOW_PASSWORD", raising=False)
    response = TestClient(app).get("/health")
    assert response.status_code == 200


def test_forwarded_remote_request_is_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("CASHFLOW_USERNAME", raising=False)
    monkeypatch.delenv("CASHFLOW_PASSWORD", raising=False)
    response = TestClient(app).get("/health", headers={"X-Forwarded-For": "192.168.1.20"})
    assert response.status_code == 503
    assert "Remote access is disabled" in response.json()["detail"]


def test_partial_credentials_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("CASHFLOW_USERNAME", "household")
    monkeypatch.delenv("CASHFLOW_PASSWORD", raising=False)
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert "Set both" in response.json()["detail"]


def test_basic_auth_is_required_when_configured(monkeypatch):
    monkeypatch.setenv("CASHFLOW_USERNAME", "household")
    monkeypatch.setenv("CASHFLOW_PASSWORD", "long-random-test-password")
    client = TestClient(app)
    missing = client.get("/health")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Basic")
    wrong = client.get("/health", auth=("household", "wrong-password"))
    assert wrong.status_code == 401
    correct = client.get("/health", auth=("household", "long-random-test-password"))
    assert correct.status_code == 200


def test_lan_mode_allows_private_network_clients_without_basic_auth(monkeypatch):
    monkeypatch.setenv("CASHFLOW_ALLOW_LAN", "1")
    monkeypatch.setenv("CASHFLOW_USERNAME", "household")
    monkeypatch.setenv("CASHFLOW_PASSWORD", "long-random-test-password")
    client = TestClient(app, client=("192.168.1.20", 12345))
    response = client.get("/health")
    assert response.status_code == 200


def test_lan_mode_does_not_allow_public_clients(monkeypatch):
    monkeypatch.setenv("CASHFLOW_ALLOW_LAN", "1")
    monkeypatch.setenv("CASHFLOW_USERNAME", "household")
    monkeypatch.setenv("CASHFLOW_PASSWORD", "long-random-test-password")
    client = TestClient(app, client=("8.8.8.8", 12345))
    response = client.get("/health")
    assert response.status_code == 503


def test_lan_mode_does_not_trust_forwarded_private_addresses(monkeypatch):
    monkeypatch.setenv("CASHFLOW_ALLOW_LAN", "1")
    monkeypatch.delenv("CASHFLOW_USERNAME", raising=False)
    monkeypatch.delenv("CASHFLOW_PASSWORD", raising=False)
    response = TestClient(app).get(
        "/health", headers={"X-Forwarded-For": "192.168.1.20"}
    )
    assert response.status_code == 503
