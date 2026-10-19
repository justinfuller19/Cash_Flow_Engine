# Buffer-Aware Cash-Flow Forecast and Pagination Requirements

This document records the implemented behavior and acceptance criteria for the buffer-aware cash-flow forecast and pagination. The API calculates a default 13-week horizon (configurable from 1 to 52 weeks), carries each week's ending balance forward, and limits current safe-to-spend to the lowest projected ending balance above the shared buffer.

## Cash-flow engine rules

The weekly cash-flow endpoint now returns the selected period plus `future_forecast` entries, `lowest_projected_cash_cents`, `future_buffer_shortfall_cents`, `future_buffer_reservation_cents`, and a lookahead-adjusted `free_to_spend_cents`. The weekly report displays the upcoming weeks and warnings. Recalculations use saved schedules and transactions at request time.

1. **Forecast the full planning horizon.** Evaluate dated expected income, bills, minimum debt payments, recorded transactions, and savings contributions for every week in the configured horizon, not only the currently displayed week.
2. **Carry balances forward.** Each period's projected ending cash becomes the next period's starting cash. Reconcile a scheduled item with a linked actual transaction once only; transfers between household accounts are cash-neutral.
3. **Protect one shared household buffer.** Treat the configured buffer as a minimum household cash balance, not a weekly expense and not a separate buffer for each person.
4. **Reserve for future shortfalls before declaring money spendable.** If any later period would fall below the buffer target, reserve enough of the current/earlier surplus to cover that projected gap where funds are available. Current-week surplus is not automatically safe-to-spend.
5. **Make infeasible plans explicit.** If total projected resources cannot cover obligations and maintain the buffer, show the amount and date/period of the shortfall. Never invent income, silently over-allocate, or hide a shortfall just because it occurs on another page.
6. **Use the established allocation priority.** Fund bills due, fund minimum debt payments, restore/protect the household buffer, fund planned savings by priority, then direct remaining eligible cash to extra debt payoff (avalanche or snowball strategy).
7. **Do not double-count reservations.** A buffer reservation is a protected balance carried through the forecast, not a repeated deduction in each week. A single dollar cannot be reserved independently by multiple future weeks.
8. **Recalculate after changes.** New or edited income, bills, transactions, savings contributions, or buffer targets must refresh affected future projections and safe-to-spend amounts.
9. **Keep calculations reliable.** Monetary calculations use integer cents and deterministic domain logic. The UI displays assumptions, expected versus recorded amounts, and forecast warnings. Forecasts never trigger bank transfers or payments.
10. **Test the edges.** Include cases where a later low-income week consumes current surplus, several shortfall weeks compete for the same reserve, the buffer is already below target, funds are insufficient for all obligations, transactions cover scheduled items, and no future shortfall exists.

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
- Pagination is presentation-only: forecast and buffer checks always cover the complete planning horizon, including periods not currently visible.

## Acceptance criteria
- A future shortfall can reduce the amount reported as safe to spend in an earlier week.
- The configured buffer is never treated as a recurring weekly bill.
- An impossible buffer target is shown as a shortfall rather than hidden or silently funded.
- Checklist and plan pages remain usable on mobile and with keyboard navigation.
- Existing database schema and saved financial history remain compatible; no financial data is added to Git.
- Relevant engine/API/UI regression tests run before merge, and results are reported accurately.
