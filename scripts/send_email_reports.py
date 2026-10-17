#!/usr/bin/env python3
"""Send optional CashFlow Engine weekly reports, due reminders, and shortfall alerts."""
from __future__ import annotations

import argparse
import hashlib
import os
import smtplib
import sqlite3
import ssl
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from email.message import EmailMessage

from app import auth, database
from app.cash_flow import weekly_cash_flow


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    sender: str
    recipients: tuple[str, ...]
    username: str = ""
    password: str = ""


def load_email_config(environ=None) -> EmailConfig | None:
    environ = environ if environ is not None else os.environ
    keys = (
        "CASHFLOW_SMTP_HOST", "CASHFLOW_SMTP_FROM", "CASHFLOW_EMAIL_TO",
    )
    if not any(environ.get(key, "").strip() for key in keys):
        return None
    host = environ.get("CASHFLOW_SMTP_HOST", "").strip()
    sender = environ.get("CASHFLOW_SMTP_FROM", "").strip()
    recipients = tuple(
        item.strip() for item in environ.get("CASHFLOW_EMAIL_TO", "").split(",")
        if item.strip()
    )
    username = environ.get("CASHFLOW_SMTP_USERNAME", "").strip()
    password = environ.get("CASHFLOW_SMTP_PASSWORD", "")
    if not host or not sender or not recipients:
        raise ValueError(
            "Email is partially configured. Set CASHFLOW_SMTP_HOST, "
            "CASHFLOW_SMTP_FROM, and CASHFLOW_EMAIL_TO together."
        )
    if bool(username) != bool(password):
        raise ValueError("Set both CASHFLOW_SMTP_USERNAME and CASHFLOW_SMTP_PASSWORD, or neither.")
    try:
        port = int(environ.get("CASHFLOW_SMTP_PORT", "587"))
    except ValueError as exc:
        raise ValueError("CASHFLOW_SMTP_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("CASHFLOW_SMTP_PORT must be between 1 and 65535.")
    return EmailConfig(host, port, sender, recipients, username, password)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def _weekly_period(today: date) -> tuple[date, date]:
    # The Sunday-evening report looks ahead to the next Monday-Sunday week.
    start = today + timedelta(days=7 - today.weekday())
    return start, start + timedelta(days=6)


def build_weekly_email(today: date) -> tuple[str, str, str, str]:
    start, end = _weekly_period(today)
    report = weekly_cash_flow(start_date=start, end_date=end)
    key = f"weekly:{start.isoformat()}"
    subject = f"CashFlow Engine weekly report: {start.isoformat()}–{end.isoformat()}"
    lines = [
        f"Household cash-flow forecast: {start.isoformat()} through {end.isoformat()}",
        "",
        f"Starting household cash: {_money(report['starting_cash_cents'])}",
        f"Expected income: {_money(report['expected_income_cents'])}",
        f"Recorded income in period: {_money(report['recorded_income_cents'])}",
        f"Bills still due: {_money(report['bills_due_cents'])}",
        f"Minimum debt payments still due: {_money(report['minimum_debt_payments_cents'])}",
        f"Recorded expenses: {_money(report['transaction_expenses_cents'])}",
        f"Projected cash before buffer: {_money(report['projected_cash_before_buffer_cents'])}",
        f"Household buffer target: {_money(report['buffer_target_cents'])}",
        f"Buffer replenishment needed: {_money(report['buffer_replenishment_cents'])}",
        f"Planned savings: {_money(report['planned_savings_cents'])}",
        f"Savings funded in forecast: {_money(report['funded_savings_cents'])}",
        f"Free to spend above buffer and planned savings: {_money(report['free_to_spend_cents'])}",
        f"Extra debt payoff capacity: {_money(report['extra_debt_payoff_capacity_cents'])}",
        "",
    ]
    if report["projected_shortfall_cents"]:
        lines.append(f"WARNING: projected shortfall of {_money(report['projected_shortfall_cents'])}.")
    elif report["buffer_shortfall_cents"]:
        lines.append(f"WARNING: projected cash is below the buffer target by {_money(report['buffer_shortfall_cents'])}.")
    lines.extend(["", "Bills due:"])
    due_bills = [item for item in report["bills"] if item["status"] == "due"]
    lines.extend(
        [f"- {item['due_date']}: {item['name']} — {_money(item['amount_cents'])}" for item in due_bills]
        or ["- No uncovered bills due."]
    )
    lines.append("")
    lines.append("Minimum debt payments due:")
    due_debts = [item for item in report["minimum_debt_payments"] if item["status"] == "due"]
    lines.extend(
        [f"- {item['due_date']}: {item['name']} — {_money(item['amount_cents'])}" for item in due_debts]
        or ["- No uncovered minimum debt payments due."]
    )
    lines.extend(["", "This is a forecast, not a bank balance or payment instruction. Transfers are excluded from household income and spending."])
    body = "\n".join(lines)
    return key, _hash(body), subject, body


def _notification_row(key: str):
    with database.connect() as db:
        row = db.execute(
            "SELECT notification_key, payload_hash, last_sent_on FROM email_notification_log WHERE notification_key = ?",
            (key,),
        ).fetchone()
    return dict(row) if row else None


def _should_send_changed_item(key: str, payload_hash: str, today: date, repeat_after_days: int = 0) -> bool:
    row = _notification_row(key)
    if row is None:
        return True
    last_sent = date.fromisoformat(row["last_sent_on"])
    if last_sent >= today:
        return False
    if row["payload_hash"] != payload_hash:
        return True
    return repeat_after_days > 0 and (today - last_sent).days >= repeat_after_days


def _record_notifications(items: list[tuple[str, str]], today: date) -> None:
    if not items:
        return
    with database.connect() as db:
        db.executemany(
            """INSERT INTO email_notification_log (notification_key, payload_hash, last_sent_on)
               VALUES (?, ?, ?)
               ON CONFLICT(notification_key) DO UPDATE SET
                   payload_hash = excluded.payload_hash,
                   last_sent_on = excluded.last_sent_on""",
            [(key, payload_hash, today.isoformat()) for key, payload_hash in items],
        )


def build_daily_digest(today: date, reminder_days: int = 3) -> tuple[str, list[tuple[str, str]]]:
    if not 1 <= reminder_days <= 6:
        raise ValueError("CASHFLOW_REMINDER_DAYS must be between 1 and 6.")
    end = today + timedelta(days=6)
    report = weekly_cash_flow(start_date=today, end_date=end)
    due_items = []
    log_items: list[tuple[str, str]] = []
    cutoff = today + timedelta(days=reminder_days)
    for kind, items in (("bill", report["bills"]), ("debt", report["minimum_debt_payments"])):
        for item in items:
            due = date.fromisoformat(item["due_date"])
            if item["status"] != "due" or not today <= due <= cutoff:
                continue
            key = f"due:{kind}:{item['id']}:{item['due_date']}"
            payload = f"{kind}|{item['name']}|{item['amount_cents']}|{item['due_date']}"
            digest = _hash(payload)
            if _should_send_changed_item(key, digest, today):
                due_items.append((kind, item))
                log_items.append((key, digest))

    alert_key = "shortfall:household"
    has_shortfall = report["projected_shortfall_cents"] > 0 or report["buffer_shortfall_cents"] > 0
    alert = None
    if has_shortfall:
        alert_payload = (
            f"{report['projected_shortfall_cents']}|{report['buffer_shortfall_cents']}|"
            f"{report['projected_cash_before_buffer_cents']}"
        )
        alert_hash = _hash(alert_payload)
        if _should_send_changed_item(alert_key, alert_hash, today, repeat_after_days=7):
            alert = alert_hash
            log_items.append((alert_key, alert_hash))
    else:
        with database.connect() as db:
            db.execute("DELETE FROM email_notification_log WHERE notification_key = ?", (alert_key,))

    if not due_items and alert is None:
        return "", []

    lines = [f"CashFlow Engine reminders for {today.isoformat()}", ""]
    if due_items:
        lines.append("Upcoming bills and debt payments:")
        for kind, item in due_items:
            label = "Bill" if kind == "bill" else "Minimum debt payment"
            lines.append(f"- {label}: {item['name']} — {_money(item['amount_cents'])}, due {item['due_date']}")
        lines.append("")
    if alert is not None:
        lines.extend([
            "CASH-FLOW WARNING",
            f"Projected shortfall: {_money(report['projected_shortfall_cents'])}",
            f"Amount below household buffer target: {_money(report['buffer_shortfall_cents'])}",
            f"Projected cash before buffer: {_money(report['projected_cash_before_buffer_cents'])}",
            "",
        ])
    lines.append("Review the weekly report in CashFlow Engine before making financial decisions.")
    return "\n".join(lines), log_items


def send_email(config: EmailConfig, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(body)
    if config.port == 465:
        with smtplib.SMTP_SSL(
            config.host, config.port, context=ssl.create_default_context(), timeout=30
        ) as server:
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message, to_addrs=list(config.recipients))
    else:
        with smtplib.SMTP(config.host, config.port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            if config.username:
                server.login(config.username, config.password)
            server.send_message(message, to_addrs=list(config.recipients))


def run_once(mode: str, today: date, config: EmailConfig | None, dry_run: bool = False) -> bool:
    if mode == "weekly":
        key, payload_hash, subject, body = build_weekly_email(today)
        if _notification_row(key):
            print(f"Weekly report already sent for {key.split(':', 1)[1]}.")
            return False
        notifications = [(key, payload_hash)]
    else:
        body, notifications = build_daily_digest(
            today, int(os.environ.get("CASHFLOW_REMINDER_DAYS", "3"))
        )
        if not notifications:
            print("No new reminders or shortfall alerts.")
            return False
        subject = f"CashFlow Engine reminders and alerts: {today.isoformat()}"

    if dry_run:
        print(f"Subject: {subject}\n\n{body}")
        return True
    if config is None:
        print("Email is not configured; no message sent.")
        return False
    send_email(config, subject, body)
    _record_notifications(notifications, today)
    print(f"Sent: {subject}")
    return True


def _registered_households() -> list[dict]:
    auth.initialize_auth_database()
    with auth.auth_connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT id, notification_email FROM households ORDER BY id"
        ).fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily", "weekly"), default="daily")
    parser.add_argument("--date", type=date.fromisoformat, help="Override the local date for testing")
    parser.add_argument("--dry-run", action="store_true", help="Print the message without sending or logging it")
    args = parser.parse_args()
    try:
        today = args.date or date.today()
        config = load_email_config()
        if config is None and not args.dry_run:
            print("Email is not configured; skipping notifications.")
            return 0

        households = _registered_households()
        if not households:
            # Compatibility for a pre-account database or an installation not yet registered.
            database.initialize_database()
            run_once(args.mode, today, config, dry_run=args.dry_run)
            return 0

        sent_or_previewed = 0
        for household in households:
            household_id = int(household["id"])
            # The environment recipient list is a legacy default for household 1 only.
            # Every additional household must explicitly set its own notification address.
            recipients = (
                (household["notification_email"],)
                if household["notification_email"]
                else (config.recipients if config and household_id == 1 else ())
            )
            if not args.dry_run and (config is None or not recipients):
                print(f"Skipping household {household_id}: no household-specific email recipient configured.")
                continue
            household_config = replace(config, recipients=tuple(recipients)) if config else None
            token = auth.set_current_household(household_id)
            try:
                database.initialize_database()
                if run_once(args.mode, today, household_config, dry_run=args.dry_run):
                    sent_or_previewed += 1
            finally:
                auth.reset_current_household(token)
        if sent_or_previewed == 0:
            print("No household notifications were sent or previewed.")
        return 0
    except (OSError, sqlite3.Error, ValueError, smtplib.SMTPException) as exc:
        print(f"Email notification run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
