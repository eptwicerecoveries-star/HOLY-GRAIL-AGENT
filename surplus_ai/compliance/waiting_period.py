from __future__ import annotations

from datetime import date, timedelta


def compute_earliest_contact_date(sale_date: date, waiting_period_days: int) -> date:
    """The first day a former owner may lawfully be approached.

    A zero-day period means the day of the sale, not the day after: some states impose no
    wait at all, and adding a day would hold back a case the law already permits.
    """
    if waiting_period_days < 0:
        raise ValueError("waiting_period_days cannot be negative")
    return sale_date + timedelta(days=waiting_period_days)


def waiting_period_has_elapsed(sale_date: date, waiting_period_days: int, as_of: date) -> bool:
    """Whether the wait is over. The earliest date itself counts as elapsed."""
    return as_of >= compute_earliest_contact_date(sale_date, waiting_period_days)


def days_until_contact_allowed(sale_date: date, waiting_period_days: int, as_of: date) -> int:
    """How many days remain before contact is permitted; zero once it is."""
    earliest = compute_earliest_contact_date(sale_date, waiting_period_days)
    return max((earliest - as_of).days, 0)
