"""Integration tests for household login and per-household data isolation."""
from fastapi.testclient import TestClient

from app import auth, database
from app.main import app


def test_registration_creates_shared_household_session_and_isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    database.initialize_database()
    client = TestClient(app)

    response = client.post("/register", data={
        "household_name": "Maple House",
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert auth.resolve_session(client.cookies.get(auth.SESSION_COOKIE)) == 1
    assert response.headers["set-cookie"].lower().find("httponly") >= 0


def test_login_required_and_logout_revokes_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    database.initialize_database()
    client = TestClient(app)
    client.post("/register", data={
        "household_name": "Maple House",
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    })
    client.cookies.clear()
    assert client.get("/api/people").status_code == 401
    response = client.post("/login", data={
        "username": "maple@example.test", "password": "correct-horse-battery"
    }, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/people").status_code == 200
    client.get("/logout", follow_redirects=False)
    assert client.get("/api/people").status_code == 401


def test_households_use_separate_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    database.initialize_database()
    first = TestClient(app)
    first.post("/register", data={
        "household_name": "First House", "username": "first@example.test",
        "password": "correct-horse-battery",
    })
    person = first.post("/api/people", json={"name": "Private member"})
    assert person.status_code == 201
    first.cookies.clear()

    second = TestClient(app)
    response = second.post("/register", data={
        "household_name": "Second House", "username": "second@example.test",
        "password": "correct-horse-battery",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert second.get("/api/people").json() == []
    assert second.post("/api/people", json={"name": "Second member"}).status_code == 201

    first.post("/login", data={
        "username": "first@example.test", "password": "correct-horse-battery"
    })
    assert [item["name"] for item in first.get("/api/people").json()] == ["Private member"]


def test_registration_rejects_short_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    response = TestClient(app).post("/register", data={
        "household_name": "Test", "username": "test@example.test", "password": "short"
    })
    assert response.status_code == 200
    assert "at least 12 characters" in response.text


def test_login_is_rate_limited_after_ten_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    auth.clear_login_failures("testclient")
    client = TestClient(app)
    client.post("/register", data={
        "household_name": "Maple House",
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    })
    client.cookies.clear()

    for _ in range(auth.LOGIN_ATTEMPT_LIMIT):
        response = client.post("/login", data={
            "username": "maple@example.test",
            "password": "incorrect-password",
        }, follow_redirects=False)
        assert response.status_code == 200
    limited = client.post("/login", data={
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    }, follow_redirects=False)
    assert limited.status_code == 429
    assert "Wait 15 minutes" in limited.text

    auth.clear_login_failures("testclient")
    accepted = client.post("/login", data={
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    }, follow_redirects=False)
    assert accepted.status_code == 303


def test_account_settings_save_private_recipient_and_rotate_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    client = TestClient(app)
    client.post("/register", data={
        "household_name": "Maple House",
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    })
    response = client.post("/account", data={
        "notification_email": "private@example.test",
        "current_password": "correct-horse-battery",
        "new_password": "a-new-long-household-password",
    })
    assert response.status_code == 200
    assert "Password updated" in response.text
    assert client.get("/api/people").status_code == 200
    account = auth.household_account(1)
    assert account["notification_email"] == "private@example.test"
    assert auth.authenticate("maple@example.test", "a-new-long-household-password") == 1
    assert auth.authenticate("maple@example.test", "correct-horse-battery") is None


def test_account_settings_require_current_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "cashflow.db"))
    client = TestClient(app)
    client.post("/register", data={
        "household_name": "Maple House",
        "username": "maple@example.test",
        "password": "correct-horse-battery",
    })
    response = client.post("/account", data={
        "notification_email": "attacker@example.test",
        "current_password": "wrong-password",
        "new_password": "",
    })
    assert response.status_code == 200
    assert "Current password is incorrect" in response.text
    assert auth.household_account(1)["notification_email"] is None
