from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

from surplus_ai.compliance.exceptions import ComplianceConfigError, StateRulesNotFoundError
from surplus_ai.compliance.state_rules import FeeCapBasis, StateComplianceRules

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATES_CONFIG_DIR = PROJECT_ROOT / "config" / "compliance" / "states"


class ComplianceRulesLoader:
    """Reads state rules from YAML.

    A missing state is not an error here. Batches span counties in many states, and one
    unwritten file must not stop the rest; the engine records the absence as a blocking
    reason instead, which reports honestly and holds the case back.
    """

    def __init__(self, states_dir: Path | None = None) -> None:
        self._dir = states_dir or STATES_CONFIG_DIR

    def path_for(self, state_code: str) -> Path:
        return self._dir / f"{state_code.strip().lower()}.yaml"

    def exists(self, state_code: str) -> bool:
        return self.path_for(state_code).is_file()

    def load(self, state_code: str) -> StateComplianceRules:
        """Load one state's rules, raising if the file is absent."""
        path = self.path_for(state_code)
        if not path.is_file():
            raise StateRulesNotFoundError(
                f"No compliance rules for {state_code.upper()}. Create {path} from "
                "config/compliance/states/_template.yaml and have the statutory values "
                "checked before relying on them."
            )
        return _parse(path)

    def load_or_none(self, state_code: str) -> StateComplianceRules | None:
        """Load one state's rules, or None when no file exists."""
        try:
            return self.load(state_code)
        except StateRulesNotFoundError:
            logger.info("state_rules_missing", state=state_code.upper())
            return None

    def load_all(self) -> dict[str, StateComplianceRules]:
        """Every state with a rules file, keyed by upper-case code."""
        if not self._dir.is_dir():
            return {}
        rules: dict[str, StateComplianceRules] = {}
        for path in sorted(self._dir.glob("*.yaml")):
            if path.stem.startswith("_"):
                continue
            parsed = _parse(path)
            rules[parsed.state_code] = parsed
        return rules

    def verified_states(self) -> tuple[str, ...]:
        """States whose rules may actually be relied on."""
        return tuple(code for code, r in sorted(self.load_all().items()) if r.is_usable)


@lru_cache(maxsize=64)
def _parse(path: Path) -> StateComplianceRules:
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ComplianceConfigError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ComplianceConfigError(f"{path} must contain a mapping at the top level")

    state_code = str(raw.get("state_code", "")).strip().upper()
    if len(state_code) != 2:
        raise ComplianceConfigError(f"{path}: state_code must be a two-letter code")
    if state_code.lower() != path.stem.lower():
        raise ComplianceConfigError(
            f"{path}: state_code {state_code!r} does not match the filename"
        )

    basis_raw = str(raw.get("fee_cap_basis", FeeCapBasis.PERCENTAGE_OF_RECOVERY.value))
    try:
        basis = FeeCapBasis(basis_raw)
    except ValueError as exc:
        raise ComplianceConfigError(f"{path}: unknown fee_cap_basis {basis_raw!r}") from exc

    verified_on = raw.get("verified_on")
    if verified_on is not None and not isinstance(verified_on, date):
        raise ComplianceConfigError(f"{path}: verified_on must be a date")

    try:
        rules = StateComplianceRules(
            state_code=state_code,
            state_name=str(raw.get("state_name", "")),
            verified=bool(raw.get("verified", False)),
            verified_by=str(raw.get("verified_by", "")),
            verified_on=verified_on,
            statute_citations=_strings(raw.get("statute_citations")),
            source_urls=_strings(raw.get("source_urls")),
            waiting_period_days=_optional_int(path, raw, "waiting_period_days"),
            fee_cap_basis=basis,
            max_contingency_fee_pct=_optional_float(path, raw, "max_contingency_fee_pct"),
            max_flat_fee_amount=_optional_float(path, raw, "max_flat_fee_amount"),
            claim_deadline_days=_optional_int(path, raw, "claim_deadline_days"),
            escheatment_period_days=_optional_int(path, raw, "escheatment_period_days"),
            requires_notarized_contract=_optional_bool(raw, "requires_notarized_contract"),
            requires_written_contract=_optional_bool(raw, "requires_written_contract"),
            requires_locator_license=_optional_bool(raw, "requires_locator_license"),
            prohibits_assignment_of_claim=_optional_bool(raw, "prohibits_assignment_of_claim"),
            cooling_off_days=_optional_int(path, raw, "cooling_off_days"),
            required_disclosures=_strings(raw.get("required_disclosures")),
            notes=str(raw.get("notes", "")),
        )
    except ValueError as exc:
        raise ComplianceConfigError(f"{path}: {exc}") from exc

    if rules.verified and rules.missing_fields():
        raise ComplianceConfigError(
            f"{path} is marked verified but still missing "
            f"{list(rules.missing_fields())}. Verification means every value below was "
            "checked against the statute, so a verified file cannot have gaps."
        )

    logger.debug(
        "state_rules_loaded", state=state_code, verified=rules.verified, usable=rules.is_usable
    )
    return rules


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        return (str(value),)
    return tuple(str(item) for item in value)


def _optional_int(path: Path, raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ComplianceConfigError(f"{path}: {key} must be a whole number") from exc


def _optional_float(path: Path, raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ComplianceConfigError(f"{path}: {key} must be a number") from exc


def _optional_bool(raw: dict[str, Any], key: str) -> bool | None:
    value = raw.get(key)
    return None if value is None else bool(value)
