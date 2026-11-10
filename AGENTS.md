# Instructions for coding agents

Before planning or implementing a feature in this repository, read **[docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md)**. Treat it as the canonical long-term product outline and use it to understand the intended milestones, architecture boundaries, allocation order, and MVP. For financial-accuracy and forecast-reliability work, also read **[docs/FINANCIAL_ACCURACY_UPGRADE.md](docs/FINANCIAL_ACCURACY_UPGRADE.md)**; keep its checkboxes, phase status, and progress log updated in the same PR as each implementation.

## Working rules

1. Check the current branch, open pull requests, and existing implementation before assuming a roadmap item is complete. A feature in an open PR is not necessarily present on `main`.
2. Work on the next incomplete milestone or the specific milestone the user requests. Do not jump ahead to optional enhancements unless asked.
3. Treat the roadmaps as living plans, not proof of current implementation. Update milestone checkboxes/status only when supported by the actual code and tests. Mark a task complete only when the relevant implementation and tests are present in the PR branch; distinguish merged work from open PR work.
4. Keep pay-period financial calculations deterministic and testable. Keep HTTP/UI and persistence concerns outside the core forecast calculations.
5. Represent money as integer cents. Do not use floating-point arithmetic for financial calculations.
6. Follow the current owner-approved financial specification. The former mandatory household buffer is retired; do not restore it. The new end-of-pay-period target is a soft goal, is not reserved, and remains spendable. Reserve bills, savings, and planned debt payments exactly once; use the backward carry-over calculation in docs/PAY_PERIOD_FORECAST_SPEC.md to account for later shortfalls. If older code/docs conflict with that specification, the current specification takes precedence.
7. Make shortfalls and assumptions visible. Do not silently over-allocate cash or imply forecasts are guaranteed.
8. Never initiate external bank transfers or payments. The owner-approved specification does request internal automatic ledger posting for scheduled paychecks and bills; those entries must remain editable/undoable and must not be represented as actual bank activity.
9. Add or update tests for each behavior change, run the relevant tests when possible, and report exactly what was and was not run. Never claim tests passed without running them.
10. Keep real household financial data, database files, credentials, and secrets out of version control.
11. Prefer small, reviewable pull requests and avoid unrelated refactors. Update README/documentation when setup, API behavior, or user workflows change.
12. The owner-approved pay-period specification supersedes older weekly/buffer assumptions in the historical roadmap. Keep the roadmap and upgrade log aligned with docs/PAY_PERIOD_FORECAST_SPEC.md.
13. Do not merge pull requests or deploy to the Raspberry Pi without explicit approval.

## Before starting a task

- Read `docs/PROJECT_ROADMAP.md` and the relevant README section.
- For cash-flow accuracy work, also read `docs/FINANCIAL_ACCURACY_UPGRADE.md`.
- Inspect the current implementation, open PRs, and tests.
- Identify the milestone and acceptance criteria affected.
- State important assumptions, then implement the smallest complete, tested increment.
- Update the upgrade checklist and progress log in the same PR as the corresponding code changes.

- Optional estimated debt interest belongs only to the multi-week forecast unless separately approved. Keep estimates visibly labeled and separate from actual balances, transactions, and cash/safe-to-spend calculations; never persist projections as actual activity.
