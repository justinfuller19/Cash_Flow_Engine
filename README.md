# CashFlow Engine

> **Project roadmap:** See [docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md) for the planned milestones and [AGENTS.md](AGENTS.md) for instructions to coding agents. New work should follow that roadmap unless the project owner directs otherwise.

A lightweight, self-hosted household cash-flow manager designed to run on Raspberry Pi OS. The rolling three-month pay-period forecast is the source of truth for planning; household setup and recorded activity are stored in SQLite.

## Current milestone

- Rolling three-month pay-period forecast using integer cents, fixed-anchor paycheck grouping, and a soft end-of-period cash target.
- FastAPI app with a health check and interactive API docs at `/docs`.
- SQLite database initialized automatically at app startup.
- Browser-based Household Setup page for household members, income sources, and a confirmed starting-cash snapshot.
- Browser-based Bills & Debt page for recurring bills, credit-card balances, APRs, minimum payments, and due dates.
- Browser-based Savings Goals page with fixed per-pay-period contributions, optional deadlines, actual contribution recording, and a rolling forecast that recalculates deadline contributions across remaining pay periods.
- Action checklist for reviewing upcoming obligations and recording actual amounts and dates. The old planner URL redirects to the checklist; historical payday plans remain available read-only through the API.
- Live dashboard for current cash, reserved cash, maximum spendable, pay-period warnings, upcoming paydays, bills, debt minimums, and savings progress.
- Manual transaction ledger with typed income, expense, and transfer entries, user-managed categories, safe category renaming/deletion, and optional links to scheduled income, bills, and debt payments for reconciliation.
- Dedicated `/pay-periods` report for the rolling three-month forecast, including pay-period income, bills, savings, debt payments, projected ending cash, carry-over, warnings, and a printable PDF layout. The former `/weekly` page redirects here.
- No separate safe-surplus allocation workflow: the forecast calculates spendable cash directly, and actual expenses, debt payments, and savings contributions are recorded in their respective workflows.
- Record actual debt payments and savings contributions directly in the ledger and update the corresponding debt or savings balance. Historical surplus-allocation rows remain in SQLite for compatibility but no longer reserve cash or affect forecasts.
- Settings & Data page with a downloadable database backup and a typed, backup-first Clear All Data workflow that preserves the application schema and category definitions.
- Optional SMTP email delivery for upcoming bill/debt reminders and projected cash shortfall alerts.
- Shared household login accounts with scrypt password hashes, expiring opaque sessions, and a separate SQLite database file per household.
- Persistent household-member and income-source APIs.
- Historical payday-plan records remain in SQLite for compatibility and reference; the active forecast is not generated from the retired plan tables.
- Regression tests for pay-period calculations, cash reconciliation, transaction workflows, setup APIs, and invalid input.

## Requirements

Python 3.11, 3.12, and 3.13 are tested in CI. Use one of these versions for the most reliable dependency installation; Python 3.15 may require dependency updates. Dependencies are in `requirements.txt`.

## Run locally

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app.main:app --reload
```

### Raspberry Pi OS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> for the dashboard, <http://127.0.0.1:8000/setup> for household and income setup, <http://127.0.0.1:8000/bills> for bills and debts, <http://127.0.0.1:8000/savings> for savings goals, <http://127.0.0.1:8000/checklist> for actual actions, <http://127.0.0.1:8000/transactions> for account activity, and <http://127.0.0.1:8000/categories> to manage transaction categories, <http://127.0.0.1:8000/pay-periods> for the rolling forecast, and <http://127.0.0.1:8000/settings-data> for backups and the protected data reset. Explore the API at <http://127.0.0.1:8000/docs>.

## Household accounts and access

On first launch, open the app and create a household account at `/register`. Each household shares one username/email and password; partners do not need separate user accounts. Additional households can register on the same server, and each household's financial records are stored in a separate SQLite database file. Existing records remain in the original `CASHFLOW_DB_PATH` database and are assigned to the first household created.

Passwords are stored as salted scrypt hashes. Session tokens are random, expire after seven days, and are stored as hashes in a separate `<database>.auth.sqlite` account registry. Sign in and open `/account` to set this household's notification email or change its shared password. Password changes require the current password and sign out other sessions. Login attempts are rate-limited after 10 failed attempts per client address in a 15-minute window. Password recovery and household deletion are not currently available.

Set `CASHFLOW_COOKIE_SECURE=1` when serving the app over HTTPS so session cookies are marked Secure. By default, remote requests are blocked. On the Raspberry Pi deployment, nginx serves the app at `http://<pi-hostname>.local` on standard port 80 and forwards to the application bound to `127.0.0.1:8000`. The built-in updater configures nginx and Avahi. Nginx allows private-network addresses only; the household login remains required. Do not port-forward or expose the app to the public internet.

The scheduled backup script backs up every registered household database plus the shared `.auth.sqlite` account registry, with integrity checks and restrictive file permissions. The in-app manual download and Clear All Data safety backup cover only the currently signed-in household's financial database; use the scheduled server-wide backup for recovery of all accounts and records. Keep all backups together.

## Database

By default, SQLite data is stored at `data/cashflow.db` in the project directory. The `data/` folder is created automatically. Override the location with `CASHFLOW_DB_PATH` if you want the database somewhere else, such as a persistent data directory on your Raspberry Pi.

The database file is excluded from Git by `.gitignore`. Back it up regularly and do not commit real household financial information.

## First setup endpoints

- `GET /api/people` — list household members.
- `POST /api/people` — create a household member.
- `PATCH /api/people/{id}` — edit a household member's name or `active` flag (deactivate instead of deleting).
- `GET /api/household/settings` — read the confirmed starting cash balance and financial week start.
- `PATCH /api/household/settings` — update the confirmed starting cash balance or financial week start.
- `GET /api/income-sources` — list saved income sources.
- `POST /api/income-sources` — create an income source linked to a household member.
- `GET/POST /api/bills` and `PATCH /api/bills/{id}` — manage recurring bills and deactivate/reactivate them.
- `GET/POST /api/debts` and `PATCH /api/debts/{id}` — manage debt balances, APRs, minimums, due dates, and active status.
- `GET/POST /api/savings-goals` and `PATCH /api/savings-goals/{id}` — manage savings targets, balances, priority, target dates, and active status.
- `PATCH /api/income-sources/{id}` — edit an income source or toggle its `active` flag.
- `GET /api/cash-flow/pay-periods` — calculate the rolling three-month forecast from saved schedules and recorded activity.
- `GET /pay-periods` — view and print the three-month forecast.
- `GET /api/plans` and `GET /api/plans/{id}` — read historical payday plans; new legacy single-payday plan generation and preview endpoints return HTTP 410.
- `GET /api/dashboard` — retrieve upcoming paydays, bills, debts, savings progress, and pending checklist counts.
- `GET /api/cash-flow/weekly` — retained as an internal compatibility calculation for checklist/reminder workflows; it is not the user-facing forecast.
- Optional systemd timers send daily due-date/shortfall reminders when SMTP transport settings are configured. Each household must set its own recipient under `/account`; the legacy `CASHFLOW_EMAIL_TO` setting is used only for household 1.
- `GET /api/checklist` and `PATCH /api/plan-items/{id}` — review and update planned actions with actual amounts and dates.
- `GET/POST /api/transactions` and `PATCH /api/transactions/{id}` — record and edit actual income/expense transactions (positive amounts are inflows; negative amounts are outflows).

Money values use integer cents: `$1,234.56` is `123456`. Income frequency options are `weekly`, `biweekly`, `semimonthly`, `monthly`, and `irregular`.

## Current forecast rules

The pay-period forecast groups paychecks within the configured fixed-anchor window (2 calendar days by default) and starts each period on the group's final payday. It reserves scheduled bills, planned savings contributions, and planned debt payments once. The end-of-period target is a soft goal, not a protected buffer or reservation. Max spendable is based on the lowest projected ending cash across the three-month horizon. See [docs/PAY_PERIOD_FORECAST_SPEC.md](docs/PAY_PERIOD_FORECAST_SPEC.md) for the complete rules.

Unfunded amounts and forecast assumptions are explicitly shown. This is a planning tool, not a bank connection; it does not initiate external payments or transfers.

## Raspberry Pi deployment

See [docs/DEPLOY_RASPBERRY_PI.md](docs/DEPLOY_RASPBERRY_PI.md) for the systemd service, daily backups, restore procedure, and upgrade notes.

## Current limitations and security

Household members and income sources can be added, edited, and deactivated/reactivated through both the API and the browser setup page. Transactions can be entered manually, but automatic imports and reconciliation against bank activity are not yet implemented. The active forecast plans savings per pay period. Deadline contributions are recalculated across remaining pay periods; ongoing goals use a fixed per-period contribution. The former mandatory buffer and safe-surplus allocation workflow are retired. Legacy weekly calculations remain only for internal checklist/reminder compatibility and do not define current forecast behavior. Irregular income is only forecast for its explicitly saved date. The Raspberry Pi systemd and backup/restore guide is in [docs/DEPLOY_RASPBERRY_PI.md](docs/DEPLOY_RASPBERRY_PI.md). Remote requests are blocked by default. To allow trusted home-network devices without a second login prompt, configure `CASHFLOW_ALLOW_LAN=1`; the household login still protects financial pages. Optional Basic authentication is available via `CASHFLOW_USERNAME` and `CASHFLOW_PASSWORD`. Keep the service on a trusted private network and do not expose it to the public internet.


### Deleting records

Household members, income sources, bills, debt accounts, savings goals, transactions, and saved payday plans can be permanently removed from their management pages. The browser asks for confirmation first. Removing a household member also removes their income sources; removing a saved payday plan also removes its checklist items. Historical plan line items and ledger transactions are not automatically removed when unrelated setup records are deleted. Back up the database before cleanup.
