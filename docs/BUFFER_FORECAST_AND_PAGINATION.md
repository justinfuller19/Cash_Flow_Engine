# Daily Cash-Flow Forecast and Pagination Requirements

This document records the daily cash-flow forecast and pagination behavior. The API calculates a default 13-week horizon (configurable from 1 to 52 weeks), carries each week's ending balance forward, and limits current safe-to-spend to the lowest projected daily cash balance across the horizon.

## Cash-flow engine rules

1. **Forecast dated cash movements.** Evaluate scheduled income, bills, debt minimums, recorded income/expenses, and planned savings on their dates. Transfers between household accounts remain cash-neutral.
2. **Carry balances forward.** Each period's projected ending balance becomes the next period's starting cash. Reconcile linked actual transactions with scheduled income/bills/debt only once.
3. **No mandatory household buffer.** Do not reserve an arbitrary minimum cash floor or create buffer allocation items. The former database setting is retained only for upgrade compatibility and must not affect safe-to-spend, savings funding, or allocations.
4. **Use daily checkpoints.** A weekly net total can hide an overdraft earlier in the week. Apply each day's outflows before that day's inflows because exact bank posting times are not available; show the daily lowest and ending balances.
5. **Reserve for future obligations.** If a later daily balance is lower than the current period's safe daily balance, reserve the difference before declaring current discretionary cash available.
6. **Keep savings distinct.** Savings goals remain planned commitments with their existing weekly targets, deadline calculations, arrears, and actual-contribution reconciliation. They are not a cash buffer and must not be removed with the old buffer setting.
7. **Make infeasible plans explicit.** If any daily balance falls below zero, show the date and projected cash shortfall. Never invent income, silently over-allocate, or hide a shortfall because it occurs in a later week.
8. **Recalculate after changes.** New or edited income, bills, transactions, savings contributions, or cash snapshots refresh affected daily and future projections.
9. **Keep calculations reliable.** Monetary calculations use integer cents and deterministic domain logic. Forecasts never trigger bank transfers or payments.
10. **Test the edges.** Include early-week bills followed by later income, same-day paycheck/payment ordering, future shortfalls, several shortfall weeks, insufficient cash, linked transaction reconciliation, savings funding, and no future shortfall.

## Pagination requirements

### Payday Checklist
- Display at most 10 pending/attention-needed checklist items per page.
- Provide Previous/Next controls and a page indicator, with navigation disabled at the boundaries.
- Saving an item's status or actual amount/date persists independently of the current page.
- After a saved action is removed from the pending checklist, keep the page index valid and show the remaining items without an empty out-of-range page.

### View Plan
- Display at most 10 plan line items per page when a plan contains more than 10 items.
- Paginate saved plan history at 10 plans per page.
- Provide accessible Previous/Next controls and page indicators; omit controls when there is only one page.
- Navigating pages must not alter the saved plan, item values, allocations, or forecast.
- Pagination is presentation-only: daily cash and forecast checks always cover the complete planning horizon, including periods not currently visible.

## Acceptance criteria
- Safe-to-spend is limited by the lowest projected daily balance across the full forecast horizon, not an arbitrary buffer target.
- An early-week cash shortfall is detected even when later income makes the weekly net balance positive.
- Same-day outflows are tested before same-day inflows and the assumption is disclosed.
- The former buffer target does not appear in household setup or allocation choices and does not affect calculations.
- Savings goals and recorded contributions continue to work independently.
- Existing database schema and saved financial history remain compatible; no financial data is added to Git.
- Relevant engine/API/UI regression tests run before merge, and results are reported accurately.
