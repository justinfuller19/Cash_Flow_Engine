# CashFlow Engine — Project Roadmap

This document is the long-term implementation outline for CashFlow Engine. It describes the intended sequence and product behavior, not a claim that every listed feature already exists. Update the status of milestones as work is completed.

## Product goal

Build a reliable, self-hosted household cash-flow planner for Raspberry Pi OS. Given household income, bills, debt obligations, savings priorities, and a selected payday, it should produce a practical action plan that protects required cash buffers, funds obligations, supports savings goals, and directs remaining money toward debt payoff.

The app is a planning tool. It must not claim that money was transferred or a bill was paid unless a user explicitly records that action. Do not add automatic money movement as part of the early milestones.

## Current foundation

- [x] Pure Python allocation engine using integer cents.
- [x] FastAPI application and health endpoint.
- [x] SQLite schema and startup initialization.
- [x] Initial household-member and income-source API/UI foundation.
- [x] Initial tests for engine and database/API behavior.

The current state of the repository and merged code is authoritative. Check open pull requests before assuming unmerged work is part of `main`.

## Milestones

### Milestone 1 — Core allocation engine
**Goal:** Keep the financial rules deterministic, testable, and independent of HTTP/UI code.

- Represent all monetary values as integer cents; never use floating-point arithmetic for money.
- Protect each household member's configured discretionary cash buffer.
- Allocate funds to bills due within the selected planning window.
- Fund required debt minimum payments.
- Allocate to savings goals according to configured priority.
- Direct remaining funds toward extra debt repayment using avalanche or snowball ordering.
- Clearly report fully funded items, partially funded items, and shortfalls.
- Add unit tests for zero/negative/insufficient funds, ties, rounding, and allocation order.

### Milestone 2 — Database foundation
**Goal:** Persist the household's financial setup in SQLite.

- Store household members, income sources, bills, debts/credit cards, savings accounts/goals, transactions, payday plans, and plan items.
- Keep database access separate from route/UI logic.
- Use safe parameterized SQL and explicit transactions where multiple writes must succeed or fail together.
- Keep the database in a persistent configurable location; never commit real financial data.
- Add schema migration/versioning before schema changes become difficult to manage.

### Milestone 3 — Household and income setup
**Goal:** Let users enter and maintain household members and income sources.

- Create and list household members.
- Configure each person's default cash buffer.
- Create and list income sources linked to a household member.
- Support income amount, frequency, next payday, and active/inactive status.
- Add edit and deactivate/reactivate workflows; avoid hard deletion when records are referenced by historical plans.
- Validate required fields, dates, frequency choices, and nonnegative amounts.

### Milestone 4 — Bills and debt management (in progress)
**Goal:** Maintain obligations that the planning engine can use.

The initial management increment adds SQLite-backed bill and debt endpoints plus a browser page for creating and deactivating records. Persisted records are not yet wired into payday-plan generation; that integration remains part of the saved payday planning milestone.

- Create, view, edit, and deactivate recurring bills.
- Support bill name, amount, due date or recurrence, category, autopay indicator, and optional household owner.
- Support debts/credit cards with current balance, APR, minimum payment, due date, and active status.
- Make variable bills and irregular obligations possible without confusing estimates with confirmed amounts.
- Validate APR, balances, due dates, and payment amounts.
- Add tests for inactive records, due-date boundaries, and missing or malformed values.

### Milestone 5 — Savings goals and buffers (in progress)
**Goal:** Model cash reserves and savings targets without double-counting money.

Savings-goal creation, editing, target-date management, priority ordering, and deactivation are now available. The allocation engine is not yet connected to these persisted goals, and contribution history is still outstanding.

- Configure a target amount, current saved amount, priority, and optional target date for each goal.
- Track contributions and balances clearly.
- Distinguish cash already reserved for a goal from money available to allocate again.
- Keep emergency/discretionary buffers separate from goal contributions.
- Make it clear when a goal is underfunded or its target date is unrealistic.

### Milestone 6 — Payday calendar and income forecasting
**Goal:** Determine which income is expected and which obligations fall into each planning window.

- Support weekly, biweekly, semimonthly, monthly, and irregular income.
- Generate upcoming payday dates with correct calendar behavior, including month ends and leap years.
- Forecast expected income and upcoming bills over a configurable date range.
- Distinguish expected income from income confirmed as received.
- Test recurrence boundaries, timezone/date handling, and overlapping pay periods.

### Milestone 7 — Saved payday planning
**Goal:** Generate a complete plan from persisted household data rather than only manually supplied preview data.

- Let the user choose a payday/planning window and applicable income sources.
- Combine forecast income, starting cash, required buffers, bills, debt minimums, savings goals, and payoff strategy.
- Preserve the agreed allocation order: buffers → bills → minimum debt payments → prioritized savings → extra debt payoff.
- Support avalanche (highest APR first) and snowball (smallest balance first).
- Save each generated plan and its line items for review and history.
- Explain every allocation and any shortfall in plain language.
- Recalculate safely when the underlying household setup changes, without silently rewriting historical plans.

### Milestone 8 — Payday action checklist
**Goal:** Turn a plan into an actionable, trackable checklist.

- Show the planned action, amount, intended date, and priority for each item.
- Allow users to mark an item complete, skipped, or needing attention.
- Record actual payment/contribution amount and date separately from planned values.
- Show differences between planned and actual amounts.
- Support replanning after an income amount or payment differs from expectations.
- Never imply that checking an item off triggers a bank transaction.

### Milestone 9 — Dashboard and financial history
**Goal:** Make the household's current position and next actions easy to understand.

- Show available cash and protected buffers.
- Show the next paydays, upcoming bills, and minimum debt payments.
- Summarize debt balances, savings progress, and current-plan shortfalls.
- Show the current payday checklist and its completion status.
- Provide historical plans and completed actions for comparison.
- Keep the interface usable on a phone as well as a desktop browser.

### Milestone 10 — Reliability, privacy, and data protection
**Goal:** Make the app trustworthy for real household financial data.

- Expand tests to cover engine, API, persistence, recurrence, and full planning workflows.
- Test database persistence across application restarts and transaction rollback on failure.
- Provide documented backup and restore procedures and verify restores.
- Add an audit-friendly record of plan changes and actual actions.
- Add authentication before supporting access beyond a trusted single-user environment.
- Validate inputs and avoid logging sensitive financial details unnecessarily.
- Keep secrets and the SQLite database out of Git.
- Document limitations and avoid presenting projections as guarantees.

### Milestone 11 — Raspberry Pi deployment
**Goal:** Run the application reliably as a private household service.

- Document installation in a Python virtual environment.
- Run the app through a systemd service with a dedicated, least-privilege user.
- Store the SQLite database in a persistent writable directory outside disposable build artifacts.
- Configure logs, restart behavior, and a health check.
- Document LAN access and firewall considerations.
- Keep the service off the public internet unless a future, properly secured remote-access design is implemented.
- Document backup scheduling, restore steps, upgrades, and rollback.

## Future / optional enhancements

These are not prerequisites for the first useful release:

- Import transactions from CSV or supported bank exports.
- Optional bank connectivity after security, privacy, and reliability requirements are understood.
- Notifications for upcoming paydays, bills, or unfinished checklist items.
- Multiple authenticated users and household roles.
- Charts and richer reporting.
- A native mobile app or installable progressive web app.

Automatic payments or money transfers are explicitly out of scope for the early project. Prefer read-only imports and user-confirmed actions first.

## Recommended delivery order

1. Verify and merge the existing engine/database/household setup work in dependency order.
2. Finish household and income editing/deactivation.
3. Build bill and debt management.
4. Build savings goals and buffer management.
5. Add payday forecasting.
6. Connect persisted data to the allocation engine and save plans.
7. Add the checklist and dashboard/history.
8. Harden testing, privacy, backups, and Raspberry Pi deployment.

## First useful release (MVP)

The first end-to-end release should let a user:

1. Enter household members and income sources.
2. Enter bills, debts, cash buffers, and savings goals.
3. Select an upcoming payday.
4. Generate an explainable plan that shows what to fund and any shortfalls.
5. Track the plan manually as actions are completed.
6. Revisit the plan and its history later.

## Architecture direction

Keep responsibilities separated:

- **Web layer:** FastAPI routes and browser UI.
- **Persistence layer:** SQLite schema, queries, and migrations.
- **Domain/planning layer:** income forecasting and payday orchestration.
- **Allocation engine:** pure, deterministic allocation rules with no web or database dependencies.
- **Tests:** unit tests for the engine plus integration tests for API/database workflows.

As the application grows, consider organizing code into route modules, schemas, and service modules. Avoid a large refactor just to match a proposed folder layout; extract modules when it makes the next feature simpler and safer.

## Definition of done for each milestone

- Acceptance criteria are explicit and tied to user-visible behavior.
- Input validation and relevant edge cases are covered by tests.
- Tests are actually run; report results accurately and do not claim success without evidence.
- Documentation is updated where behavior or setup changes.
- Existing functionality remains covered by regression tests.
- Financial calculations use integer cents and explain shortfalls rather than silently discarding them.
- Changes are scoped to the milestone and delivered through a reviewable pull request.
