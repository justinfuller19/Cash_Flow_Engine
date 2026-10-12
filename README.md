# CashFlow Engine

A lightweight, self-hosted household cash-flow planner designed to run on Raspberry Pi OS. The project separates its payday allocation rules from the web application and stores household setup in SQLite.

## Current milestone

- Pure Python payday allocation engine using integer cents.
- FastAPI app with a health check and interactive API docs at `/docs`.
- SQLite database initialized automatically at app startup.
- Browser-based Household Setup page for household members, protected cash buffers, and income sources.
- Persistent household-member and income-source APIs.
- Initial schema for recurring bills, credit cards, savings accounts and goals, transactions, payday plans, and checklist items.
- Unit tests for allocation behavior, setup APIs, and invalid input.

## Requirements

Python 3.11 or newer is recommended. Dependencies are in `requirements.txt`.

## Run locally

### Windows PowerShell

```powershell
py -3 -m venv .venv
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

Open <http://127.0.0.1:8000> for the dashboard and <http://127.0.0.1:8000/setup> to add household members and income sources. Explore the API at <http://127.0.0.1:8000/docs>.

## Database

By default, SQLite data is stored at `data/cashflow.db` in the project directory. The `data/` folder is created automatically. Override the location with `CASHFLOW_DB_PATH` if you want the database somewhere else, such as a persistent data directory on your Raspberry Pi.

The database file is excluded from Git by `.gitignore`. Back it up regularly and do not commit real household financial information.

## First setup endpoints

- `GET /api/people` — list household members.
- `POST /api/people` — create a household member, optionally specifying `default_buffer_cents`.
- `GET /api/income-sources` — list saved income sources.
- `POST /api/income-sources` — create an income source linked to a household member.
- `POST /api/plan/preview` — preview an allocation from explicitly supplied data.

Money values use integer cents: `$1,234.56` is `123456`. Income frequency options are `weekly`, `biweekly`, `semimonthly`, `monthly`, and `irregular`.

## Allocation order

1. Protect discretionary cash buffers.
2. Fund bills due during the pay-period window.
3. Fund credit-card minimums due during the window.
4. Contribute to savings goals by priority.
5. Apply remaining cash to extra debt payoff using avalanche or snowball order.

Unfunded amounts are explicitly shown. This is a planning tool, not a bank connection; it does not move money or verify that a payment happened.

## Current limitations and security

Household members and income sources can be added and viewed in the browser, but editing/deleting them is not implemented yet. Bills, cards, savings goals, transactions, saved payday plans, and checklist completion do not yet have management endpoints or UI. Income recurrence and payday calendar generation are not yet connected to the planner. Authentication, backups, and deployment hardening are not included. Keep the service bound to localhost or a trusted private network and do not expose it to the public internet.
