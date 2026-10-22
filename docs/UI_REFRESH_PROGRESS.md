# CashFlow Engine UI Refresh — Progress Log

## Goal
Deliver one cohesive pull request that makes daily household money management easier without changing the financial engine, API contracts, stored data, or allocation rules.

## Design decisions
- Lead with the existing safe-to-spend figure and a clear next-action path.
- Use four primary destinations: Home, Checklist, Activity, and More.
- Put secondary reports and management pages under More.
- On mobile, use a persistent bottom navigation with accessible touch targets.
- Collapse secondary dashboard metrics under “At a glance.”
- Explain the existing forecast and household buffer in plain language; the buffer is a forecast-protection rule, not an extra deduction to apply again.
- Retain existing routes, detailed reports, management controls, account settings, and API documentation.
- Respect reduced-motion preferences, visible keyboard focus, and light/dark system preferences.

## Implementation checklist
- [x] Shared responsive design tokens and common component styling.
- [x] Consistent primary navigation and mobile navigation behavior.
- [x] Home dashboard hierarchy and progressive disclosure for secondary metrics.
- [x] Quick paths to checklist, transactions, and weekly forecast.
- [x] In-place explanation of the existing safe-to-spend calculation and buffer role.
- [x] Keep future forecast buffer gaps visible on Home when the forecast indicates a positive gap, with a direct link to the weekly forecast.
- [x] Pin dashboard metric label hooks in regression tests so label changes are noticed during review.
- [x] Preserve existing financial API values and calculation paths.
- [x] Add focused UI injection and dashboard-enhancement tests.
- [x] Run the full pytest suite successfully on Python 3.11, 3.12, and 3.13.
- [x] Review PR feedback and address both non-blocking suggestions: regression coverage for label-based metric hooks and a visible future-buffer-gap warning.

## Safety and validation
No financial formulas, allocation priorities, database schema, transaction semantics, or deployment configuration are intentionally changed in this UI refresh. The UI displays existing API values. CI passed the full test suite on Python 3.11, 3.12, and 3.13 before these follow-up review fixes. The follow-up adds a visible Home warning when the forecast buffer gap is positive; the warning links to the weekly forecast and disappears when the gap is zero, negative, or unavailable. Label-hook regression tests pin the metric names used by the presentation script. Re-run CI on this follow-up before merge. These remain presentation-only changes; the PR has not been deployed to the Raspberry Pi.
