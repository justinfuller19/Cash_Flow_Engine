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
**Status:** Complete — PR #38 merged. Covers balance-capped minimums, cycle-specific partial-payment reconciliation, surplus-payment debt links, and user-confirmed match suggestions.

- [x] Support explicit debt-obligation links on recorded expense transactions through the transaction ledger.
- [x] Link payments recorded through surplus allocations to the intended debt while keeping extra principal reductions out of minimum-payment reconciliation.
- [x] Suggest matches only when supported by amount/date/description/account/debt-status evidence; require explicit confirmation for all suggestions and support dismissal.
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
**Status:** Implementation complete in PR #39; GitHub Actions run #175 passed 137 tests on Python 3.11, 3.12, and 3.13.

- [x] Separate regular weekly target, recorded contributions, outstanding arrears, catch-up amount, and remaining goal amount.
- [x] Carry arrears that exist at the forecast start into future windows.
- [x] Add each future week's normal target without inventing new arrears for weeks that have not occurred.
- [x] Reduce arrears only when qualifying contributions are recorded.
- [x] Prevent the same shortfall from being repeatedly added as a new obligation or reserved multiple times.
- [x] Keep savings targets flexible; distinguish planned from completed contributions.
- [x] Preserve ongoing undated general savings goals with weekly targets and no finish date.
- [x] Test that existing arrears affect week 2 and later forecast weeks.
- [x] Assert expected multi-week safe-to-spend changes and accurate weekly API notes.

**Acceptance criteria**

- Existing arrears remain visible in future projections until caught up or adjusted.
- Future weeks include their normal target.
- No contribution or shortfall is double-counted.
- Unaffordable goals appear as shortfalls, not completed transfers or mandatory bills.

## Phase 4 — Consistent buffer and safe-to-spend semantics

**Priority:** High  
**Status:** Implementation complete in PR #40; GitHub Actions run #178 passed 141 tests on Python 3.11, 3.12, and 3.13. Awaiting review/merge.

- [x] Document one shared definition of the household buffer and safe-to-spend in this roadmap, `AGENTS.md`, and relevant calculation modules.
- [x] Define a single priority rule for starting cash, expected income, bills, debt minimums, buffer protection, savings allocations, and safe-to-spend.
- [x] Ensure pure-engine and weekly forecast behavior use consistent buffer semantics.
- [x] Calculate safe-to-spend across the relevant forecast horizon, not just the current week's ending balance.
- [x] Show buffer target, projected shortfall, replenishment amount, and explanation.
- [x] Prevent bills, minimums, buffer, and savings from reserving the same dollars more than once.
- [x] Test low-cash weeks, multiple-paycheck weeks, and future-week shortfalls.
- [x] Update UI wording and weekly API notes to match implemented rules.

**Shared calculation rule**

1. Start from the confirmed/selected opening cash balance and include expected or recorded income once.
2. Subtract bills due and remaining minimum debt payments, accounting for qualifying recorded payments once.
3. Protect the one household-wide buffer target. The funded amount is explicit; any buffer gap remains visible.
4. Fund savings from cash above the buffer according to the existing savings plan; unfunded goals remain shortfalls, not completed transfers.
5. Across a multi-week forecast, carry projected ending cash forward and cap today's safe-to-spend at the lowest projected weekly ending balance minus the buffer target, floored at zero.
6. Subtract planned surplus allocations once from the resulting safe-to-spend figure. Do not treat an allocation as a completed payment until the user records it.

**Acceptance criteria**

- [x] Calculation paths agree on the buffer floor and use integer cents.
- [x] The household buffer is respected across the forecast horizon.
- [x] Safe-to-spend is not overstated when a later week would fall below the buffer.
- [x] Shortfalls remain visible rather than silently discarded.

# Milestone B — More Realistic Long-Range Forecasting

## Phase 5 — Optional estimated debt interest

**Priority:** Medium  
**Status:** Implementation in progress in the Phase 5 PR; optional estimate is isolated to the multi-week forecast.

- [x] Add an optional, clearly labeled future-interest estimate for debts retaining a projected balance after scheduled and recorded payments.
- [x] Document and test a daily-prorated APR approximation (opening projected balance × APR × days / 365), rounded to the nearest cent.
- [x] Keep estimated interest separate from actual transactions, confirmed balances, and cash-flow/safe-to-spend totals.
- [x] Carry projected balances forward within each request so interest is not duplicated across the weeks of one forecast.
- [x] Explain that actual interest depends on lender terms, daily balances, payment timing, statement cycles, and fees.
- [x] Keep the pure single-period engine unchanged.
- [x] Test opt-in behavior, zero balances, zero APR, higher APR, cent rounding, and that estimates do not change cash-flow requirements or saved balances.

## Phase 6 — More flexible recurrence options

**Priority:** Medium-Low  
**Status:** Complete — PR #42 merged.

- [x] Preserve semimonthly (1st and 15th) and existing weekly/biweekly/monthly behavior.
- [x] Add a 15th-and-last-calendar-day option.
- [x] Add a 1st-and-3rd-Friday option.
- [x] Add a last-business-day-of-month option using weekends and commonly observed US bank holidays, including Good Friday.
- [x] Use calendar-based occurrence generation consistently in the weekly forecast and saved payday planner.
- [x] Define weekend, holiday, month-end, and short-month behavior; monthly anchors return to their original day after short months.
- [x] Add recurrence and legacy income-table migration regression tests for leap years, month ends, and schedule compatibility.

## Phase 7 — Income changes and end dates

**Priority:** Medium-Low  
**Status:** Complete — PR #43 merged.

- [x] Add optional end dates to recurring income sources and allow clearing the end date.
- [ ] Evaluate optional end dates for recurring bills when appropriate; not included in this income-focused implementation.
- [x] Add temporary amount overrides for specified dates without permanently changing the recurring amount.
- [x] Allow users to review and remove overrides; overlapping override ranges are rejected.
- [x] Preserve irregular income as explicit one-time dated occurrences through the existing recurrence rule.
- [x] Ensure schedule edits and overrides affect projections only and do not rewrite historical transactions.
- [x] Test that no occurrences appear after end dates and normal amounts resume outside temporary override ranges.

# Milestone C — Better Error Prevention and Usability

## Phase 8 — Potentially unmatched expenses

**Priority:** Medium-Low  
**Status:** Implemented; GitHub Actions run #202 passed 164 tests on Python 3.11, 3.12, and 3.13; awaiting review in PR #44.

- [x] Identify possible links between recent unlinked expenses and uncovered bill/debt obligations using amount, date, payee/description, account, category, and obligation status.
- [x] Show review suggestions such as **“Could this payment cover [Bill Name]?”** for likely bill matches and retain the existing debt-payment review queue.
- [x] Add a possible-unmatched-payments section to the weekly report with explicit confirm/dismiss controls.
- [x] Explain the evidence behind each suggestion and show the transaction date, amount, and obligation due date.
- [x] Never auto-link an uncertain match; only an explicit confirmation updates the transaction's obligation link.
- [x] Keep dismissed suggestions hidden until relevant transaction data changes; transaction or obligation edits stale existing pending/dismissed suggestions for reconsideration.
- [x] Test bill-match evidence, explicit confirmation, dismissal, unrelated expenses, already-covered bills, and weekly-report visibility.

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
- [x] Phase 2: Debt payment matching and remaining minimums.
- [x] Phase 3: Savings arrears in future weeks (PR #39 open; GitHub Actions run #175 passed on Python 3.11, 3.12, and 3.13).
- [x] Phase 4: Consistent buffer and safe-to-spend semantics (implementation in PR branch; awaiting CI/review).

**Exit criteria:** Core calculations agree across the engine, weekly forecast, API, and UI; exact-value regression tests pass; assumptions and shortfalls are explained.

## Milestone B — More realistic long-range forecasting

- [x] Phase 5: Optional estimated debt interest (implementation in Phase 5 PR; awaiting CI/review).
- [ ] Phase 6: More flexible recurrence options.
- [ ] Phase 7: Income changes and end dates.

## Milestone C — Better error prevention and usability

- [ ] Phase 8: Potentially unmatched expenses.

## Progress log

Record meaningful implementation updates here. Each entry should include date, phase, PR/commit reference, tests run, and remaining work.

- **2026-10-20 — Roadmap created:** Defined three milestones and eight phases. No application behavior changed by this documentation alone. Implementation remained pending at that point.
- **2026-10-20 — Phase 1 complete (PRs #35 and #36, merged):** Added schema v17 dated cash snapshots, legacy starting-cash migration, effective-week lookup in weekly forecasts, confirmation metadata and a 10-day stale warning, dated confirmation controls, an advisory opening-balance estimate, and explicit confirmation in the weekly report. The estimate uses only recorded income and expenses before the current financial week, excludes transfers, and never saves automatically. Regression tests cover snapshot history/migration, transaction boundaries, multi-week safe-to-spend, stale warnings, transfer exclusion, and the confirmation workflow. GitHub Actions passed on Python 3.11, 3.12, and 3.13 for the merged implementation.
- **2026-10-20 — Phase 2 first increment (PR #37, merged):** Weekly debt forecasts cap a scheduled minimum at the recorded balance, credit linked expense transactions within seven days of the due date, preserve partial minimums, and display over-minimum amounts as extra debt reduction. Regression coverage includes balances below minimums, multiple partial payments, overpayments, wrong debts/cycles, partial checklist minimums, and zero-balance debts.
- **2026-10-20 — Phase 2 complete (PR #38, merged):** Surplus-allocation extra payments carry explicit debt links but remain excluded from minimum-payment matching. The schema-v18 review queue suggests matches for unlinked debt-like expenses and requires user confirmation; dismissal and reconsideration are supported. Follow-up review fixes also preserve legitimate manually categorized debt payments and remove a duplicate migration invocation.
- **2026-10-20 — Phase 3 implementation (PR #39 open):** Weekly savings plans now expose regular target, recorded contributions, outstanding arrears, catch-up, and remaining goal amount separately. Forecasts carry existing arrears forward but reserve the catch-up amount only once across the horizon; regular targets continue each future week, and future unfunded targets do not create new arrears before those weeks occur. The savings page and weekly report explain these amounts. Added regression tests for exact multi-week safe-to-spend, recorded contributions reducing arrears, and underfunded future targets. GitHub Actions run #175 passed the full suite: 137 tests passed on Python 3.11, 3.12, and 3.13.

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

- **2026-10-20 — Phase 4 implementation (PR #40, open):** Added shared integer-cent buffer arithmetic for the pure payday allocation engine and weekly forecast. The weekly API explicitly reports horizon-wide buffer replenishment and safe-to-spend before planned allocations; the dashboard shows safe-to-spend and forecast buffer gap. Regression tests cover underfunded buffers, allocation accounting, future-week gaps, and dashboard metrics. GitHub Actions run #178 passed 141 tests on Python 3.11, 3.12, and 3.13. PR #40 awaits review/merge; no deployment performed.


- **2026-10-20 — Phase 4 merged (PR #40):** Household-wide buffer and horizon safe-to-spend semantics are merged. Earlier Phase 4 review notes are corrected in this update.
- **2026-10-21 — Phase 8 implementation (PR pending):** Added schema-v21 bill-payment suggestion storage, evidence-based candidate generation for recent unlinked expenses, explicit confirm/dismiss endpoints, stale-on-transaction/obligation-edit behavior, and a possible-unmatched-payments review section in the weekly report. Existing debt-payment suggestions are surfaced alongside bill suggestions without changing their existing confirmation workflow. Added regression tests; GitHub Actions run #202 passed 164 tests on Python 3.11, 3.12, and 3.13. No deployment performed.
- **2026-10-20 — Phase 5 implementation (PR pending):** Added an opt-in multi-week debt-interest estimate based on projected opening balances and APR, carried projected balances week by week, and exposed estimates in the weekly report. Estimates do not affect cash totals, saved debt balances, or recorded transactions. Regression tests cover opt-in behavior, zero APR/balance, higher APR, rounding, and unchanged cash values. CI results pending.
