# CashFlow Engine — milestone 1

A lightweight, self-hosted household cash-flow planner. This first milestone proves the calculation rules before adding permanent data storage and management screens.

## What is included

- Pure Python allocation engine using integer cents for money.
- FastAPI app with a health check and a plan-preview endpoint.
- Interactive API documentation at `/docs`.
- Unit tests for waterfall order, paycheck date filtering, explicit shortfalls, avalanche/snowball selection, and cent rounding.

## Requirements

Python 3.11 or newer is recommended. The app is lightweight, but actual Raspberry Pi RAM use should be measured on the intended device.

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

Then open <http://127.0.0.1:8000>. Try the calculator at <http://127.0.0.1:8000/docs> using `POST /api/plan/preview`.

## Money format

All money inputs and outputs are integer cents: `$1,234.56` is `123456`. APR is a percentage (for example, `24.9` means 24.9%). Dates are ISO format (`YYYY-MM-DD`).

## Preview request example

```json
{
  "plan_date": "2026-11-13",
  "period_start": "2026-11-13",
  "period_end": "2026-11-26",
  "starting_cash_cents": 0,
  "incomes": [
    {"name": "Household paycheck", "amount_cents": 280000, "received_date": "2026-11-13"}
  ],
  "buffers": [
    {"name": "Personal cash buffer", "amount_cents": 50000}
  ],
  "bills": [
    {"name": "Electric", "amount_cents": 14000, "due_date": "2026-11-18"}
  ],
  "credit_cards": [
    {"name": "Chase Sapphire", "balance_cents": 166000, "minimum_payment_cents": 8000, "due_date": "2026-11-20", "apr_percent": 24.9}
  ],
  "savings_goals": [
    {"name": "Emergency HYSA", "target_amount_cents": 100000, "current_balance_cents": 60000, "remaining_periods": 2, "priority": 1}
  ],
  "debt_strategy": "avalanche"
}
```

## Current limitations

- Financial data is not persisted yet; preview requests are stateless.
- This milestone expects pay-period income dates, bill due dates, and savings goal periods to be supplied explicitly. Recurrence and calendar generation come next.
- This is a planning tool, not a bank connection; an allocation does not move money or verify that a payment happened.
- Authentication, backups, and deployment hardening are not included. Do not expose this preview service to the public internet.
