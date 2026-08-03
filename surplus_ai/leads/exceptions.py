from __future__ import annotations

from surplus_ai.utils.exceptions import AppError


class LeadError(AppError):
    """Base class for failures while building cases, owners and leads."""


class CountyRegistrationError(LeadError):
    """A county could not be identified or registered.

    Raised rather than defaulted. Every case belongs to a county, and attaching cases to a
    county guessed from a filename would mix two counties' records together.
    """
