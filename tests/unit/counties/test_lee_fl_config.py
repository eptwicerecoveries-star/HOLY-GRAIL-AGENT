"""Offline tests for the Lee County, Florida county configuration."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from surplus_ai.database.models.enums import CountySourceType, PublishingFrequency
from surplus_ai.parser.interpretation.county_config import load_county_config
from surplus_ai.research.providers.manual import ManualLookupProvider
from surplus_ai.research.registry import COUNTIES_CONFIG_DIR, ProviderRegistry

_LEE_PYTHON_BRANCH = re.compile(
    r"""(?:if|elif)\s+county\s*==\s*['\"]lee['\"]|['\"]lee['\"]\s*==\s*county""",
    re.IGNORECASE,
)


def test_lee_county_config_loads() -> None:
    config = load_county_config("fl", "lee")

    assert config is not None
    assert config.county_name == "Lee County"
    assert config.state == "FL"
    assert config.fips_code == "12071"
    assert config.surplus_column == "Balance"
    assert config.surplus_column_declared is True
    assert config.source_type is CountySourceType.MANUAL_UPLOAD
    assert config.publishing_frequency is PublishingFrequency.WEEKLY
    assert config.source_url == (
        "https://www.leeclerk.org/departments/courts/property-sales/"
        "tax-deed-sales/tax-deed-reports"
    )
    assert "pending claims" in config.notes.lower()
    assert "not a determination of legal entitlement" in config.notes.lower()


def test_lee_research_provider_is_manual_lookup() -> None:
    path = COUNTIES_CONFIG_DIR / "fl" / "lee.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = raw.get("research")
    assert isinstance(block, dict)
    assert block.get("property_provider") == "manual_lookup"

    provider = ProviderRegistry().resolve_for_county("FL", "lee")
    assert isinstance(provider, ManualLookupProvider)


def test_lee_is_not_a_live_provider() -> None:
    path = COUNTIES_CONFIG_DIR / "fl" / "lee.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert "verified_for_automated_access" not in raw
    registry = ProviderRegistry()
    assert "lee" not in {name.lower() for name in registry.registered_names()}


def test_no_county_specific_python_branch_for_lee() -> None:
    root = Path(__file__).resolve().parents[3] / "surplus_ai"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _LEE_PYTHON_BRANCH.search(text):
            offenders.append(str(path.relative_to(root)))
        lowered = text.lower()
        if "if county ==" in lowered and "lee" in lowered:
            if re.search(r'if\s+county\s*==\s*["\']lee["\']', text, re.IGNORECASE):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
