# Instructions for coding agents

Before planning or implementing a feature in this repository, read **[docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md)**. Treat it as the canonical long-term product outline and use it to understand the intended milestones, architecture boundaries, allocation order, and MVP. For financial-accuracy and forecast-reliability work, also read **[docs/FINANCIAL_ACCURACY_UPGRADE.md](docs/FINANCIAL_ACCURACY_UPGRADE.md)**; keep its checkboxes, phase status, and progress log updated in the same PR as each implementation.

## Working rules

1. Check the current branch, open pull requests, and existing implementation before assuming a roadmap item is complete. A feature in an open PR is not necessarily present on `main`.
2. Work on the next incomplete milestone or the specific milestone the user requests. Do not jump ahead to optional enhancements unless asked.
3. Treat the roadmaps as living plans, not proof of current implementation. Update milestone checkboxes/status only when supported by the actual code and tests. Mark a task complete only when the relevant implementation and tests are present in the PR branch; distinguish merged work from open PR work.
4. Keep the allocation engine pure and deterministic. Keep HTTP/UI, persistence, and forecasting concerns outside the core allocation rules.
5. Represent money as integer cents. Do not use floating-point arithmetic for financial calculations.
6. Preserve the documented allocation order and household-wide buffer semantics. The buffer is one protected minimum cash floor: fund required bills and debt minimums first, protect the buffer next, then fund planned savings and discretionary debt payoff only from cash above the buffer. In multi-week forecasts, safe-to-spend is limited by the lowest projected weekly ending cash minus the one buffer target. Never reserve the same dollars twice. If the current implementation or docs disagree, inspect the code and tests and resolve the discrepancy explicitly rather than propagating contradictory rules.
7. Make shortfalls and assumptions visible. Do not silently over-allocate cash or imply forecasts are guaranteed.
8. Do not implement real transfers or automatic bill payments unless the user explicitly requests and approves a separately scoped design.
9. Add or update tests for each behavior change, run the relevant tests when possible, and report exactly what was and was not run. Never claim tests passed without running them.
10. Keep real household financial data, database files, credentials, and secrets out of version control.
11. Prefer small, reviewable pull requests and avoid unrelated refactors. Update README/documentation when setup, API behavior, or user workflows change.
12. If the requested work conflicts with the roadmap or reveals that the roadmap is stale, explain the conflict and propose an update rather than silently choosing a direction.
13. Do not merge pull requests or deploy to the Raspberry Pi without explicit approval.

## Before starting a task

- Read `docs/PROJECT_ROADMAP.md` and the relevant README section.
- For cash-flow accuracy work, also read `docs/FINANCIAL_ACCURACY_UPGRADE.md`.
- Inspect the current implementation, open PRs, and tests.
- Identify the milestone and acceptance criteria affected.
- State important assumptions, then implement the smallest complete, tested increment.
- Update the upgrade checklist and progress log in the same PR as the corresponding code changes.
