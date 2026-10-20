"""Shared household-buffer arithmetic.

The buffer is a protected household cash floor, not a second bill. Required
bills and debt minimums are funded first; discretionary allocations may use
only projected cash above the buffer. All amounts are integer cents.
"""


def buffer_shortfall_cents(projected_cash_cents: int, buffer_target_cents: int) -> int:
    """Return how far projected cash falls below the shared household buffer."""
    return max(0, buffer_target_cents - projected_cash_cents)


def safe_surplus_cents(projected_cash_cents: int, buffer_target_cents: int) -> int:
    """Return cash above the shared buffer that is eligible for discretionary use."""
    return max(0, projected_cash_cents - buffer_target_cents)


def buffer_reservation_cents(available_cents: int, buffer_target_cents: int) -> tuple[int, int, int]:
    """Return (funded buffer, unfunded buffer, remaining safe surplus).

    This helper is for the pure payday allocation waterfall, where the buffer
    is explicitly shown as an allocation item after required obligations.
    """
    funded = min(max(0, available_cents), max(0, buffer_target_cents))
    shortfall = buffer_shortfall_cents(available_cents, buffer_target_cents)
    remaining = safe_surplus_cents(available_cents, buffer_target_cents)
    return funded, shortfall, remaining
