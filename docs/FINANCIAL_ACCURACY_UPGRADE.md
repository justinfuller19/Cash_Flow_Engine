# Cash Flow Engine — Financial Accuracy & Forecast Reliability Upgrade

**Status:** Planned  
**Purpose:** Track implementation progress for this upgrade. Update the checkboxes and status as code, tests, and documentation are completed. A checkbox may be marked complete only when the change is present in the PR branch and supported by tests. An open PR is not part of `main` until merged.

## Goal

Make household cash balances, debt obligations, savings plans, and multi-week forecasts consistent, explainable, and reliable.

Cash Flow Engine remains a planning and tracking tool. It does not execute transfers, guarantee lender payment processing, or know the household's actual bank balance unless the user supplies that information or a supported data source is added.

## Core rules

- [ ] Distinguish confirmed balances and recorded transactions from projections and estimates.
- [ ] Never count a transaction, payment, or contribution more than once.
- [ ] Use integer cents for all financial calculations.
- [ ] Keep the dashboard, weekly report, API, and forecast engine consistent.
- [ ] Explain reservations, shortfalls, assumptions, and safe-to-spend amounts.
- [ ] Never silently change confirmed balances or automatically link uncertain transactions.
- [ ] Preserve one household-wide buffer across calculation paths.
- [ ] Do not add automatic transfers or bill payments as part of this upgrade.

# Milestone A — Trustworthy Weekly Cash Flow

## Phase 1 — Starting cash snapshots

**Priority:** Critical  
**Status:** Complete — PR #35 and PR #36 merged.

- [x] Define starting cash as a confirmed household balance effective at the start of a specified financial week.
- [x] Track the effective financial-week start and last-confirmed timestamp; preserve history where needed for historical reports.
- [x] Reject future-dated cash confirmations; a snapshot can confirm only the current or a past financial week.
- [x] Add a dashboard control: **“Set cash on hand as of [week start]”.** The same dated control is available in Household & income setup.
- [x] Show the previous confirmed amount, effective date, and last-confirmed date in Household & income setup.
- [x] Apply transactions after the snapshot exactly once; do not reapply transactions already reflected in a confirmed balance. Regression coverage verifies that a transaction before the snapshot is excluded from the selected week and a transaction after the snapshot is counted once.
- [x] Ensure new snapshots do not silently rewrite historical transactions or prior reports; snapshots are versioned by effective week and prior snapshots are retained.
- [x] Optionally suggest a balance from the previous snapshot and known transactions; label it estimated and require explicit confirmation.
- [x] Add first-run guidance for establishing the initial cash balance.
- [x] Warn when the balance has not been confirmed in more than 10 days.
- [x] Centralize the stale-balance threshold in `CASH_SNAPSHOT_STALE_DAYS` so the settings API and weekly forecast use the same rule.
- [x] Add safe schema migration and migration tests that preserve existing starting-cash settings and financial records.
- [x] Test exact before-and-after cash values, transaction boundaries, and multi-week safe-to-spend for the implemented snapshot path.
- [x] Update UI and `/api/cash-flow/weekly` notes to explain the balance and transaction period used.

**Acceptance criteria**

- A user can set a dated cash snapshot and see when it was last confirmed.
- Each post-snapshot transaction affects the forecast exactly once.
- Future forecast weeks carry forward the prior projected ending cash; future-dated balances are not treated as confirmed snapshots.
- Suggested balances never overwrite confirmed balances without explicit action.
- Stale balances produce a visible reminder.
- Tests assert exact values and historical behavior.

## Phase 2 — Debt payment matching and remaining minimums

**Priority:** Critical  
**Status:** In progress — PR #37 implements balance-capped minimums and cycle-specific partial-payment reconciliation; matching suggestions and surplus-allocation payment linking remain.

- [x] Support explicit debt-obligation links on recorded expense transactions through the transaction ledger.
- [ ] Link payments recorded through surplus allocations to the intended debt/cycle without misclassifying an extra payment as a separate minimum payment.
- [ ] Suggest matches only when supported by available data; require confirmation when ambiguous.
- [x] Match qualifying linked payments to the correct debt and payment cycle, within the documented seven-day window.
- [x] Cap the required minimum at the recorded outstanding balance: `min(stated_minimum, outstanding_balance)`.
- [x] Subtract all qualifying payments credited to that cycle and floor the remaining minimum at zero.
- [x] Keep amounts above the minimum visible as extra debt reduction.
- [x] Count each linked payment as a recorded expense once, reduce only its linked cycle, and prevent it from covering another debt or cycle.
- [x] Align weekly-path minimum caps with the pure engine's existing balance-capped minimum logic.
- [x] Document that app-side matching is a planning assumption and may differ from lender payment-application rules.
- [x] Test balances below minimums, multiple partial payments, overpayments, wrong debt, and wrong cycle.
- [x] Update weekly report details and API notes to explain remaining minimums, credited payments, and excess payments.

**Acceptance criteria**

- A $100 minimum with a qualifying $40 payment leaves $60 due.
- A $60 balance with a $100 stated minimum produces no more than $60 due before cycle payments.
- A qualifying $125 payment against a $100 remaining minimum satisfies it and shows $25 as extra.
- Payments are never double-counted or applied to unrelated obligations.

## Phase 3 — Savings arrears in future weeks

**Priority:** High  
**Status:** Not started

- [ ] Separate regular weekly target, recorded contributions, outstanding arrears, catch-up amount, and remaining goal amount.
- [ ] Carry arrears that exist at the forecast start into future windows.
- [ ] Add each future week's normal target without inventing new arrears for weeks that have not occurred.
- [ ] Reduce arrears only when qualifying contributions are recorded.
- [ ] Prevent the same shortfall from being repeatedly added as a new obligation.
- [ ] Keep savings targets flexible; distinguish planned from completed contributions.
- [ ] Preserve ongoing undated general savings goals with weekly targets and no finish date.
- [ ] Test that existing arrears affect week 2 and later forecast weeks.
- [ ] Assert expected multi-week safe-to-spend changes and accurate weekly API notes.

**Acceptance criteria**

- Existing arrears remain visible in future projections until caught up or adjusted.
- Future weeks include their normal target.
- No contribution or shortfall is double-counted.
- Unaffordable goals appear as shortfalls, not completed transfers or mandatory bills.

## Phase 4 — Consistent buffer and safe-to-spend semantics

**Priority:** High  
**Status:** Not started

- [ ] Document one shared definition of the household buffer and safe-to-spend in this roadmap, `AGENTS.md`, and relevant calculation modules.
- [ ] Define a single priority rule for starting cash, expected income, bills, debt minimums, buffer protection, savings allocations, and safe-to-spend.
- [ ] Ensure pure-engine and weekly forecast behavior use consistent buffer semantics.
- [ ] Calculate safe-to-spend across the relevant forecast horizon, not just the current week's ending balance.
- [ ] Show buffer target, projected shortfall, replenishment amount, and explanation.
- [ ] Prevent bills, minimums, buffer, and savings from reserving the same dollars more than once.
- [ ] Test low-cash weeks, multiple-paycheck weeks, and future-week shortfalls.
- [ ] Update UI wording and weekly API notes to match implemented rules.

**Acceptance criteria**

- Calculation paths agree for equivalent inputs.
- The household buffer is respected across the forecast horizon.
- Safe-to-spend is not overstated when a later week would fall below the buffer.
- Shortfalls remain visible rather than silently discarded.

# Milestone B — More Realistic Long-Range Forecasting

## Phase 5 — Optional estimated debt interest

**Priority:** Medium  
**Status:** Not started

- [ ] Add an optional, clearly labeled future-interest estimate for debts retaining a projected balance after planned payments.
- [ ] Document and test the approximation (for example, monthly interest based on balance and APR), including cent rounding.
- [ ] Keep estimated interest separate from actual transactions and confirmed balances.
- [ ] Avoid duplicating interest across overlapping forecast windows.
- [ ] Explain that actual interest depends on lender terms, daily balances, payment timing, and fees.
- [ ] Keep the pure single-period engine unchanged initially unless separately approved.
- [ ] Test zero balances, zero APR, higher APR, and effects on future cash requirements.

## Phase 6 — More flexible recurrence options

**Priority:** Medium-Low  
**Status:** Not started

- [ ] Preserve current semimonthly behavior (1st and 15th) as the default.
- [ ] Add a 15th-and-last-calendar-day option.
- [ ] Add a 1st-and-3rd-Friday option.
- [ ] Add a last-business-day-of-month option.
- [ ] Use consistent rules in occurrence generation and monthly due-date helpers.
- [ ] Define weekend, holiday, month-end, and short-month behavior.
- [ ] Test February, leap years, short months, year boundaries, and existing-schedule compatibility.

## Phase 7 — Income changes and end dates

**Priority:** Medium-Low  
**Status:** Not started

- [ ] Add optional end dates to recurring income sources.
- [ ] Evaluate optional end dates for recurring bills when appropriate.
- [ ] Add temporary amount overrides for specified upcoming occurrences without permanently changing the recurring amount.
- [ ] Allow users to review and remove overrides.
- [ ] Preserve irregular income as explicit one-time dated occurrences.
- [ ] Ensure schedule edits do not rewrite historical transactions.
- [ ] Test that no occurrences appear after end dates and normal amounts resume after temporary overrides.

# Milestone C — Better Error Prevention and Usability

## Phase 8 — Potentially unmatched expenses

**Priority:** Medium-Low  
**Status:** Not started

- [ ] Identify possible links between recent expenses and uncovered bills/debt obligations using amount, date, payee/description when available, and obligation status.
- [ ] Show suggestions such as **“Could this payment cover [Bill Name]?”**
- [ ] Add a possible-unmatched-payments section to the weekly report.
- [ ] Explain why each match was suggested and let the user review the transaction and obligation.
- [ ] Never auto-link an uncertain match.
- [ ] Avoid repeatedly showing dismissed suggestions unless relevant data changes.
- [ ] Test suggestion quality, confirmed matching, dismissal, and non-matching expenses.

# Testing and Quality Requirements

## Financial invariants

- [ ] All monetary calculations use integer cents.
- [ ] Do not present negative cash as spendable; expose projected deficits clearly.
- [ ] Never count one transaction against the same obligation more than once.
- [ ] Never allocate the same available dollar to multiple purposes.
- [ ] Keep planned amounts distinct from recorded actual activity.

## Multi-week forecast coverage

- [ ] Weekly income scenario.
- [ ] Biweekly income scenario.
- [ ] Weekly and biweekly income in the same household.
- [ ] Already-paid bill and partially paid debt minimum. (Partial debt checklist remainder is now covered by PR #37 tests; already-paid bill case remains.)
- [x] Debt balance below stated minimum.
- [x] Partial debt minimum remains on checklist; zero-balance active debt creates no $0 checklist item.
- [ ] Savings goal with existing arrears.
- [ ] Household below buffer target.
- [ ] Low-income week and future-week shortfall.
- [ ] At least one regression test per core phase asserting the direction and exact amount of safe-to-spend changes.

## API, UI, and migration safety

- [ ] Dashboard, weekly report, and `/api/cash-flow/weekly` agree on amounts and explanations.
- [ ] Each schema change has a versioned migration and tests from a representative previous schema.
- [ ] Repeated startup/migration does not corrupt data.
- [ ] Production database backup is verified before deploying schema-changing upgrades.
- [ ] API notes describe assumptions, estimates, shortfalls, and why values changed.

# Delivery Plan and Progress Log

## Milestone A — Trustworthy weekly cash flow

- [x] Phase 1: Starting cash snapshots.
- [ ] Phase 2: Debt payment matching and remaining minimums.
- [ ] Phase 3: Savings arrears in future weeks.
- [ ] Phase 4: Consistent buffer and safe-to-spend semantics.

**Exit criteria:** Core calculations agree across the engine, weekly forecast, API, and UI; exact-value regression tests pass; assumptions and shortfalls are explained.

## Milestone B — More realistic long-range forecasting

- [ ] Phase 5: Optional estimated debt interest.
- [ ] Phase 6: More flexible recurrence options.
- [ ] Phase 7: Income changes and end dates.

## Milestone C — Better error prevention and usability

- [ ] Phase 8: Potentially unmatched expenses.

## Progress log

Record meaningful implementation updates here. Each entry should include date, phase, PR/commit reference, tests run, and remaining work.

- **2026-10-20 — Roadmap created:** Defined three milestones and eight phases. No application behavior changed by this documentation alone. Implementation remained pending at that point.
- **2026-10-20 — Phase 1 complete (PRs #35 and #36, merged):** Added schema v17 dated cash snapshots, legacy starting-cash migration, effective-week lookup in weekly forecasts, confirmation metadata and a 10-day stale warning, dated confirmation controls, an advisory opening-balance estimate, and explicit confirmation in the weekly report. The estimate uses only recorded income and expenses before the current financial week, excludes transfers, and never saves automatically. Regression tests cover snapshot history/migration, transaction boundaries, multi-week safe-to-spend, stale warnings, transfer exclusion, and the confirmation workflow. GitHub Actions passed on Python 3.11, 3.12, and 3.13 for the merged implementation.
- **2026-10-20 — Phase 2 first increment (PR #37, in progress):** Weekly debt forecasts now cap a scheduled minimum at the recorded balance, credit all linked expense transactions within seven days of the due date, preserve partial minimums, and display over-minimum amounts as extra debt reduction. Payments remain ordinary recorded expenses and are not double-counted as both a full minimum and the payment itself. Added regression cases for balances below minimums, multiple partial payments, overpayments, wrong debts, and wrong cycles. Reviewer found that partial minimums disappeared from the automatic checklist and zero-balance active debts could create $0 checklist items; fixed the checklist criteria and added tests for both. Surplus-allocation payment linking and suggested-match review remain outstanding. CI validation is pending.

## Implementation / release rules

- Inspect the current repository, open PRs, existing schema, and tests before coding.
- Work on the next incomplete phase unless the project owner changes priorities.
- Deliver focused, reviewable PRs; update this checklist in the same PR as the corresponding implementation.
- Mark a task complete only after the code and relevant tests support it. Note partial work explicitly.
- Run relevant tests and the full suite where practical; report results accurately.
- Preserve existing financial records and historical behavior during migrations.
- Do not merge or deploy without explicit approval.
- Before production deployment, back up and verify the household databases and account registry; follow the repository's deployment guide.
- After deployment, verify startup and representative existing household records and reports.

## Guiding rule

**Do not tell the household that money is safe to spend unless the forecast accounts for known obligations and the protected household cash buffer. Every important number should be explainable and testable.**
