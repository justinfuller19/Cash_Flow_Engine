# CashFlow Engine

> **Project roadmap:** See [docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md) for the planned milestones and [AGENTS.md](AGENTS.md) for instructions to coding agents. New work should follow that roadmap unless the project owner directs otherwise.

A lightweight, self-hosted household cash-flow planner designed to run on Raspberry Pi OS. The project separates its payday allocation rules from the web application and stores household setup in SQLite.

## Current milestone

- Pure Python payday allocation engine using integer cents.
- FastAPI app with a health check and interactive API docs at `/docs`.
- SQLite database initialized automatically at app startup.
- Browser-based Household Setup page for household members, income sources, and a confirmed starting-cash snapshot.
- Browser-based Bills & Debt page for recurring bills, credit-card balances, APRs, minimum payments, and due dates.
- Browser-based Savings Goals page with per-financial-week targets, optional deadlines, separate recorded contributions and catch-up amounts, carried arrears, safe-funding amounts, and deadline warnings. Multi-week forecasts keep existing arrears visible but reserve catch-up cash only once; future unfunded targets do not become new arrears before those weeks occur. Undated goals support ongoing general savings with no target amount or finish date.
- Automatically generated weekly checklist based on saved pay schedules, bills, debt minimums, savings goals, recorded transactions, and the safe cash-flow forecast. The old planner URL redirects to this checklist; historical manual plans remain available through the API.
- Live dashboard for upcoming paydays, bills, debt minimums, savings progress, and recent plans.
- Payday checklist for marking actions complete, skipped, or needing attention and recording actual amounts/dates.
- Manual transaction ledger with typed income, expense, and transfer entries, user-managed categories, safe category renaming/deletion, and optional links to scheduled income, bills, and debt payments for reconciliation.
- Weekly cash-flow calculation API plus a dedicated `/weekly` report page and dashboard summary for expected income, due bills and debt minimums, recorded transactions, spending by category, planned savings, daily cash shortfalls, and safe available surplus after looking ahead across future weeks. The weekly report offers an opt-in estimated-interest projection that never changes saved balances or cash-flow totals. The financial week is configurable (Friday by default).
- Safe-surplus allocation workflow: reserve a user-chosen amount for debt snowball (recommending the smallest active balance), a savings goal, or intentionally unallocated cash; reject allocations that exceed forecast-safe surplus, and release cancelled allocations.
- Record the actual amount paid for a planned extra debt payment or savings contribution. The app logs it as a transaction, updates the debt balance or savings-goal balance, and reconciles the planned allocation so the same money is not counted twice. Extra debt payments remain separate from minimum-payment matching. Legacy buffer allocations are ignored by current forecasts and are retained only as historical records.
- Settings & Data page with a downloadable database backup and a typed, backup-first Clear All Data workflow that preserves the application schema and category definitions.
- Optional SMTP email delivery for weekly cash-flow reports, upcoming bill/debt reminders, and projected cash shortfall alerts.
- Shared household login accounts with scrypt password hashes, expiring opaque sessions, and a separate SQLite database file per household.
- Persistent household-member and income-source APIs.
- Initial schema for recurring bills, credit cards, savings accounts and goals, transactions, payday plans, and checklist items.
- Unit tests for allocation behavior, setup APIs, and invalid input.

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

Open <http://127.0.0.1:8000> for the dashboard, <http://127.0.0.1:8000/setup> for household and income setup, <http://127.0.0.1:8000/bills> for bills and debts, <http://127.0.0.1:8000/savings> for savings goals, <http://127.0.0.1:8000/checklist> for actual actions, <http://127.0.0.1:8000/transactions> for account activity, and <http://127.0.0.1:8000/categories> to manage transaction categories, and <http://127.0.0.1:8000/weekly> for the weekly cash-flow report, and <http://127.0.0.1:8000/settings-data> for backups and the protected data reset. Explore the API at <http://127.0.0.1:8000/docs>.

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
- `POST /api/plan/preview` — preview an allocation from explicitly supplied data.
- `POST /api/plans/generate` — forecast saved household data, generate a payday plan, and persist the plan and its line items.
- `GET /api/plans` and `GET /api/plans/{id}` — review saved plan history and details.
- `GET /api/dashboard` — retrieve upcoming paydays, bills, debts, savings progress, and pending checklist counts.
- `GET /api/cash-flow/weekly?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` — calculate a weekly cash-flow forecast; defaults to seven days and accepts up to 31 days.
- Optional systemd timers send daily due-date/shortfall reminders and a Sunday evening weekly summary when SMTP transport settings are configured. Each household must set its own recipient under `/account`; the legacy `CASHFLOW_EMAIL_TO` setting is used only for household 1.
- `GET /api/checklist` and `PATCH /api/plan-items/{id}` — review and update planned actions with actual amounts and dates.
- `GET/POST /api/transactions` and `PATCH /api/transactions/{id}` — record and edit actual income/expense transactions (positive amounts are inflows; negative amounts are outflows).

Money values use integer cents: `$1,234.56` is `123456`. Income frequency options are `weekly`, `biweekly`, `semimonthly`, `monthly`, and `irregular`.

## Allocation order

1. Fund bills due during the pay-period window, ordered by due date.
2. Fund credit-card minimums due during the window, ordered by due date.
3. Contribute to savings goals by priority when the dated cash forecast can fund them.
4. Apply remaining safe cash to extra debt payoff using avalanche or snowball order.

Unfunded amounts are explicitly shown. This is a planning tool, not a bank connection; it does not move money or verify that a payment happened.

## Raspberry Pi deployment

See [docs/DEPLOY_RASPBERRY_PI.md](docs/DEPLOY_RASPBERRY_PI.md) for the systemd service, daily backups, restore procedure, and upgrade notes.

## Current limitations and security

Household members and income sources can be added, edited, and deactivated/reactivated through both the API and the browser setup page. Transactions can be entered manually, but automatic imports and reconciliation against bank activity are not yet implemented. The cash-flow forecast plans savings per financial week rather than per payday. Goals with deadlines receive calculated weekly targets; undated goals use the chosen weekly amount for ongoing savings, with missed weekly amounts carried forward based on recorded contributions. The weekly report estimates safe available surplus from daily cash checkpoints across future weeks. Same-day outflows are tested before inflows because exact posting times are unknown. The household can allocate safe surplus to extra debt payments, savings goals, or leave it unallocated. The former buffer target remains in the database only for upgrade compatibility and no longer affects calculations. Irregular income is only forecast for its explicitly saved date. The Raspberry Pi systemd and backup/restore guide is in [docs/DEPLOY_RASPBERRY_PI.md](docs/DEPLOY_RASPBERRY_PI.md). Remote requests are blocked by default. To allow trusted home-network devices without a second login prompt, configure `CASHFLOW_ALLOW_LAN=1`; the household login still protects financial pages. Optional Basic authentication is available via `CASHFLOW_USERNAME` and `CASHFLOW_PASSWORD`. Keep the service on a trusted private network and do not expose it to the public internet.


### Deleting records

Household members, income sources, bills, debt accounts, savings goals, transactions, and saved payday plans can be permanently removed from their management pages. The browser asks for confirmation first. Removing a household member also removes their income sources; removing a saved payday plan also removes its checklist items. Historical plan line items and ledger transactions are not automatically removed when unrelated setup records are deleted. Back up the database before cleanup.
