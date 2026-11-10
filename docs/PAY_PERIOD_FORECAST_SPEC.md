# Pay-Period Forecast Specification

**Owner-approved:** 2026-11-08  
**Status:** Authoritative product direction for the next forecast implementation. This supersedes the legacy weekly mandatory-buffer model.

## Paycheck grouping and period boundaries

- The household setting `paycheck_group_window_days` controls the maximum number of calendar days between the first paycheck in a group and any later paycheck in that group. Default: 2; allowed range: 0–14.
- Always compare a candidate paycheck with the group's first paycheck, never the previous paycheck. This prevents transitive chains from stretching a group.
- The pay period begins on the group's final paycheck date. Income from every paycheck in the group belongs to that period.
- The period ends the day before the next group's final paycheck date.
- A bill due between two paychecks in the same group belongs to the preceding period. A bill due exactly on the period-start payday belongs to the new period.
- Example: Friday and the following Wednesday are separate groups with a 2-day setting. Friday starts a period ending Tuesday; Wednesday starts the next period.

## Forecast horizon and cash

- Forecast every pay period whose start date falls within the next three months.
- Current-period starting cash is today's confirmed cash snapshot. It already includes paychecks received and bills paid; do not count those again.
- In the current period, include only unpaid bills due from today through period end and paychecks not yet received.
- Later periods begin with the prior period's projected ending cash plus that period's expected income.
- Store and calculate money as integer cents.

## Reservations and soft target

- Reserved cash consists of bills, planned savings contributions, and planned debt/card payments. Each dollar is reserved at most once.
- The user-configured end-of-period target is a soft goal, not a reservation or protected floor. It is spendable.
- Outflow is bills due plus savings contributions plus debt payments.
- Savings deadline contributions are recalculated each period as remaining goal amount divided by remaining pay periods. A missed contribution increases later contributions; do not silently reduce savings.
- Ongoing savings goals contribute their fixed amount every period.
- Debt balances are projected chronologically: add interest on configured interest dates, then subtract payments on their planned dates. Stop future payments once the debt is paid off. Do not persist estimates as actual transactions.

## Backward carry-over

For target T and periods ordered from current to future:
- Set R(last) = T.
- For each earlier period i, set R(i) = max(T, outflow(i+1) - income(i+1) + R(i+1)).
- R(i) is the cash required at the end of period i to fund the next period while reaching the target later.
- Carry-over above the target is R(i) - T. Compare the forward projected period ending balance with R(i) to report a target shortfall.
- Max spendable is max(0, minimum projected period ending balance across the horizon).
- Spendable while keeping the target is max(0, max spendable - T). Do not use a mandatory buffer.

## Transactions and internal posting

- Every recorded expense reduces cash, including credit-card purchases; the user separately updates the card balance.
- If a transaction would consume reserved bill cash, warn and require confirmation. On confirmation, identify the exact bills and dates that become underfunded. When a confirmed cash baseline exists, also warn if a transaction exceeds max spendable or pushes a later period below zero, even when the final shortfall would otherwise fall on a savings reservation rather than a bill due-date event. Attribute that excess to the last bill in the first affected period so the user sees a concrete bill/date and shortfall; do not apply this max-spendable trigger before the household has confirmed a cash baseline.
- Scheduled bills may be auto-posted to the internal ledger on the due date; scheduled paychecks may be auto-received on payday. These entries must be editable and undoable. This is not a bank payment or bank deposit.
- Early bill payments release the reservation immediately but remain assigned to the original due-date period. Never subtract both the early transaction and the scheduled bill.
- Actual records, expected activity, and projections must remain distinguishable.

## Reporting

Show the three-month pay-period view with dates, income, bills, savings, debt payments, projected ending cash, carry-in/out, and status. Display current cash, reserved cash, max spendable, and spendable while keeping the target. Surface below-target, negative/underfunded, and impossible-to-maintain-target warnings. Provide a printable report that does not split a pay period across pages.


## Implementation status

The integrated forecast API is available at `/api/cash-flow/pay-periods`, with the printable report at `/pay-periods`. It reads saved household schedules and ledger activity, applies current-snapshot reconciliation, assigns grouped paychecks to the period beginning on the group's last payday, and projects unpaid bills, savings contributions, and debt interest/payments. The checklist below records the completed posting, savings reconciliation, and transaction-safeguard work. Remaining verification is documented in the PR description; it does not imply these listed features are unfinished.


- [x] Due paychecks and bills can be idempotently posted to the internal transaction ledger when the live forecast is opened; entries are linked and editable/deletable, and no external payment is initiated.
- [x] Manual expense creation now compares the proposed transaction against dated bill funding and asks for explicit confirmation with exact bill names, due dates, and new shortfall amounts.
- [x] The printable report includes bill-level underfunding warnings in addition to negative-cash and target/carry-over warnings.
- [x] Ongoing savings goals now expose a fixed per-pay-period contribution setting; dated goals remain dynamically recalculated by remaining pay periods.


## Savings contribution recording

Actual contributions can be recorded from the savings-goal page. Each contribution creates a negative internal-ledger transaction, increases the saved balance, and records the date of the latest contribution. The current pay period is then treated as satisfied for that goal, so the planned contribution is not reserved twice. When checking an actual contribution, remove that goal's planned contribution from the current and subsequent projected cash metrics before comparing the actual expense; this prevents the same savings amount from being counted twice. Editing or deleting the linked transaction reverses or recalculates the saved balance and last-contribution date. This remains an internal record only; no bank transfer is initiated.


- [x] Actual savings contributions are recorded as linked internal-ledger expenses, update saved goal balances, satisfy the current pay period, and reverse correctly when the transaction is edited or deleted.
- [x] Future-dated linked debt payments remain unapplied to the saved debt balance until their transaction date; the live forecast projects them chronologically and applies them once when due.
- [x] Multi-month recurring bill schedules support every two, three, four, and six months, plus quarterly and yearly schedules.
- [x] Negative opening cash is preserved so overdrafts are surfaced instead of silently clamped to zero.


- [x] Recurring bills support per-occurrence amount overrides, one-occurrence cancellation, and restore-to-recurring-amount controls without changing the underlying schedule.


## Grouped-paycheck bill funding

When several paychecks are grouped, the group contributes all of its income to the period beginning on the group's final payday. For bill-level funding checks, those grouped paycheck amounts become available on that period start, not on the earlier paycheck dates. A bill due on the period start is funded after that day's paycheck income; a bill due between grouped paydays remains assigned to the preceding period and cannot use the grouped income.


Actual savings contributions are subject to the same bill-funding safeguard as other expenses. The current period's planned contribution for that goal is replaced by the actual dated contribution when evaluating the warning, so it is not counted twice. If recording it would newly underfund scheduled bills, the user sees the exact bill dates and shortfalls and must explicitly confirm.

## Retired legacy workflows

The rolling pay-period forecast is the only user-facing cash-flow forecast.

- The old `/weekly` report URL redirects to `/pay-periods`.
- The former safe-surplus allocation API returns HTTP 410. Existing allocation rows are retained for historical and migration compatibility, but planned rows no longer reserve cash or reduce spendable amounts.
- The old single-payday `/api/plan/preview` and `/api/plans/generate` endpoints return HTTP 410. Existing saved payday plans remain available as historical records; no new plans are generated by the retired allocation engine.
- The weekly cash-flow calculation API remains only as internal compatibility support for checklist and reminder workflows. It is not a second forecast product and must not be linked as a report.
- The Sunday weekly forecast email and its systemd timer are retired. Daily due-date and shortfall reminders remain and direct the household to the pay-period forecast.

The former mandatory buffer, safe-surplus allocation workflow, and single-payday allocation waterfall must not be reintroduced. Legacy database columns and historical rows may remain where removing them would risk existing household data.
