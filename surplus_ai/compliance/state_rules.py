from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FeeCapBasis(str, Enum):
    """What a state's fee limit is measured against."""

    PERCENTAGE_OF_RECOVERY = "percentage_of_recovery"
    FLAT_AMOUNT = "flat_amount"
    NONE = "none"
    """The state sets no statutory cap. Different from unknown: this is a positive finding."""


class StateComplianceRules(BaseModel):
    """What one state's law permits, as recorded from its statutes.

    Every field defaults to unknown rather than permissive. A rule this system has not
    been told is a rule it must assume it would break: an unset waiting period does not
    mean "contact immediately", it means "we do not know when contact becomes lawful".

    `verified` is the gate. A file can be written, reviewed and committed while still
    carrying `verified: false`, and the engine will refuse to clear any case under it.
    Nothing here is inferred from another state, because surplus recovery is regulated
    state by state and the differences are exactly what matters.
    """

    model_config = ConfigDict(frozen=True)

    state_code: str = Field(min_length=2, max_length=2)
    state_name: str = ""

    verified: bool = False
    """Set true only when a person has checked these values against the statute."""

    verified_by: str = ""
    verified_on: date | None = None
    statute_citations: tuple[str, ...] = ()
    source_urls: tuple[str, ...] = ()

    # How long after the sale a former owner may lawfully be approached. Several states
    # bar contact for a period after the sale, and some bar it entirely until the funds
    # are reported unclaimed.
    waiting_period_days: int | None = None

    # The cap on what a recovery agent may charge. Exceeding it can void the contract and
    # in some states is a criminal matter, so a case is never cleared without knowing it.
    fee_cap_basis: FeeCapBasis = FeeCapBasis.PERCENTAGE_OF_RECOVERY
    max_contingency_fee_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    max_flat_fee_amount: float | None = Field(default=None, ge=0.0)

    # How long the claimant has before the money is lost, and to whom it goes.
    claim_deadline_days: int | None = None
    escheatment_period_days: int | None = None

    requires_notarized_contract: bool | None = None
    requires_written_contract: bool | None = None
    requires_locator_license: bool | None = None
    prohibits_assignment_of_claim: bool | None = None
    cooling_off_days: int | None = None

    required_disclosures: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_usable(self) -> bool:
        """Whether these rules may be relied on to clear a case.

        Verification alone is not enough: a file marked verified but missing the numbers
        every engagement turns on would still let a case through without them. Defined as
        the absence of missing fields so that the two can never disagree — a state
        reported usable while `missing_fields()` still named a gap would be the worst
        kind of bug here, clearing cases against a rule nobody recorded.
        """
        return not self.missing_fields()

    @property
    def has_fee_limit(self) -> bool:
        """Whether the fee position is known, including a state that caps nothing."""
        if self.fee_cap_basis is FeeCapBasis.NONE:
            return True
        if self.fee_cap_basis is FeeCapBasis.PERCENTAGE_OF_RECOVERY:
            return self.max_contingency_fee_pct is not None
        return self.max_flat_fee_amount is not None

    def missing_fields(self) -> tuple[str, ...]:
        """Which required facts are still unknown, for reporting to whoever fills them in."""
        missing: list[str] = []
        if not self.verified:
            missing.append("verified")
        if self.waiting_period_days is None:
            missing.append("waiting_period_days")
        if not self.has_fee_limit:
            missing.append(
                "max_contingency_fee_pct"
                if self.fee_cap_basis is FeeCapBasis.PERCENTAGE_OF_RECOVERY
                else "max_flat_fee_amount"
            )
        if not self.statute_citations:
            missing.append("statute_citations")
        return tuple(missing)
