"""Shared fixtures for API tests: ordinary feature tests run as an authenticated household."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def authenticated_test_clients(request, tmp_path, monkeypatch):
    # Keep every test's financial and auth databases isolated from real user data.
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    filename = str(request.node.path).replace("\\", "/")
    if filename.endswith("/test_security.py") or filename.endswith("/test_auth.py"):
        return

    original_init = TestClient.__init__

    def init_with_household_session(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        from app.auth import household_count
        credentials = {
            "household_name": "Test Household",
            "username": "test-household@example.test",
            "password": "test-household-password-123",
        }
        if household_count() == 0:
            response = self.post("/register", data=credentials, follow_redirects=False)
        else:
            response = self.post("/login", data={
                "username": credentials["username"],
                "password": credentials["password"],
            }, follow_redirects=False)
        assert response.status_code == 303

    monkeypatch.setattr(TestClient, "__init__", init_with_household_session)
