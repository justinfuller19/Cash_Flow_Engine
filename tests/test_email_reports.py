"""Tests for optional SMTP reports, due reminders, and shortfall alert deduplication."""
from datetime import date, timedelta

import pytest

from app import database
from scripts import send_email_reports as email_reports


def setup_database(tmp_path, monkeypatch):
    monkeypatch.setenv("CASHFLOW_DB_PATH", str(tmp_path / "email.db"))
    database.initialize_database()


def test_email_configuration_is_optional_but_partial_configuration_is_rejected():
    assert email_reports.load_email_config({}) is None
    with pytest.raises(ValueError, match="partially configured"):
        email_reports.load_email_config({"CASHFLOW_SMTP_HOST": "smtp.example.test"})
    config = email_reports.load_email_config({
        "CASHFLOW_SMTP_HOST": "smtp.example.test",
        "CASHFLOW_SMTP_PORT": "465",
        "CASHFLOW_SMTP_FROM": "budget@example.test",
        "CASHFLOW_EMAIL_TO": "one@example.test, two@example.test",
        "CASHFLOW_SMTP_USERNAME": "user",
        "CASHFLOW_SMTP_PASSWORD": "secret",
    })
    assert config.host == "smtp.example.test"
    assert config.port == 465
    assert config.recipients == ("one@example.test", "two@example.test")


def test_weekly_email_sends_once_for_the_next_monday_to_sunday_window(tmp_path, monkeypatch, capsys):
    setup_database(tmp_path, monkeypatch)
    today = date(2026, 10, 18)  # Sunday
    key, payload_hash, subject, body = email_reports.build_weekly_email(today)
    assert key == "weekly:2026-10-19"
    assert "2026-10-19 through 2026-10-25" in body
    assert "Extra debt payoff capacity" in body
    sent = []
    monkeypatch.setattr(email_reports, "send_email", lambda config, subject, body: sent.append((subject, body)))
    config = email_reports.EmailConfig(
        "smtp.example.test", 587, "budget@example.test", ("family@example.test",)
    )
    assert email_reports.run_once("weekly", today, config) is True
    assert len(sent) == 1
    assert email_reports._notification_row(key)["payload_hash"] == payload_hash
    assert email_reports.run_once("weekly", today, config) is False
    assert len(sent) == 1


def test_daily_digest_sends_due_reminder_and_shortfall_once_per_state(tmp_path, monkeypatch):
    setup_database(tmp_path, monkeypatch)
    today = date(2026, 10, 17)
    due_date = today + timedelta(days=2)
    with database.connect() as db:
        db.execute(
            "UPDATE household_settings SET starting_cash_cents = 1000, buffer_target_cents = 5000 WHERE id = 1"
        )
        db.execute(
            """INSERT INTO bills
               (name, amount_cents, next_due_date, flexible_window_days, active,
                category, frequency, autopay)
               VALUES ('Rent', 20000, ?, 0, 1, 'housing', 'monthly', 0)""",
            (due_date.isoformat(),),
        )
    body, notifications = email_reports.build_daily_digest(today, reminder_days=3)
    assert "Rent" in body
    assert "CASH-FLOW WARNING" in body
    assert any(key.startswith("due:bill:") for key, _ in notifications)
    assert any(key == "shortfall:household" for key, _ in notifications)
    email_reports._record_notifications(notifications, today)

    repeated_body, repeated_notifications = email_reports.build_daily_digest(today, reminder_days=3)
    assert repeated_body == ""
    assert repeated_notifications == []


def test_resolved_shortfall_clears_alert_deduplication_state(tmp_path, monkeypatch):
    setup_database(tmp_path, monkeypatch)
    today = date(2026, 10, 17)
    with database.connect() as db:
        db.execute(
            "INSERT INTO email_notification_log (notification_key, payload_hash, last_sent_on) "
            "VALUES ('shortfall:household', 'old-hash', ?)",
            (today.isoformat(),),
        )
    body, notifications = email_reports.build_daily_digest(today, reminder_days=3)
    assert "CASH-FLOW WARNING" not in body
    assert ("shortfall:household", "old-hash") not in notifications
    with database.connect() as db:
        assert db.execute(
            "SELECT 1 FROM email_notification_log WHERE notification_key = 'shortfall:household'"
        ).fetchone() is None


def test_send_email_uses_tls_and_sends_to_all_recipients(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            calls.append(("starttls", context is not None))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message, to_addrs):
            calls.append(("send", message["Subject"], tuple(to_addrs), message.get_content()))

    monkeypatch.setattr(email_reports.smtplib, "SMTP", FakeSMTP)
    config = email_reports.EmailConfig(
        "smtp.example.test", 587, "budget@example.test",
        ("one@example.test", "two@example.test"), "user", "password",
    )
    email_reports.send_email(config, "Test subject", "Test body")
    assert ("starttls", True) in calls
    send_call = next(call for call in calls if call[0] == "send")
    assert send_call[1] == "Test subject"
    assert send_call[2] == ("one@example.test", "two@example.test")
    assert "Test body" in send_call[3]


def test_household_recipients_do_not_leak_reports_between_accounts():
    config = email_reports.EmailConfig(
        "smtp.example.test", 587, "budget@example.test", ("legacy-family@example.test",)
    )
    first = {"id": 1, "notification_email": None}
    second_without_email = {"id": 2, "notification_email": None}
    second_with_email = {"id": 2, "notification_email": "second-household@example.test"}

    assert email_reports._recipients_for_household(first, config) == ("legacy-family@example.test",)
    assert email_reports._recipients_for_household(second_without_email, config) == ()
    assert email_reports._recipients_for_household(second_with_email, config) == (
        "second-household@example.test",
    )


def test_email_config_allows_recipients_to_be_set_per_household():
    config = email_reports.load_email_config({
        "CASHFLOW_SMTP_HOST": "smtp.example.test",
        "CASHFLOW_SMTP_FROM": "budget@example.test",
    })
    assert config is not None
    assert config.recipients == ()
