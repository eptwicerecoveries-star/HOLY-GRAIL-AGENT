from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from surplus_ai.research.models import PropertyLookupQuery
from surplus_ai.research.providers.manual import ManualLookupProvider
from surplus_ai.research.providers.null import NullProvider
from surplus_ai.research.registry import ProviderRegistry


def _q(state: str = "MD", slug: str = "harford") -> PropertyLookupQuery:
    return PropertyLookupQuery(state=state, county_slug=slug)


def test_default_registry_loads_shipped_config() -> None:
    registry = ProviderRegistry()
    assert registry.default_provider_name == "manual_lookup"
    provider = registry.resolve("manual_lookup")
    assert isinstance(provider, ManualLookupProvider)


def test_unknown_provider_resolves_to_null_never_success() -> None:
    registry = ProviderRegistry()
    provider = registry.resolve("does_not_exist")
    assert isinstance(provider, NullProvider)
    outcome = provider.lookup(_q())
    assert outcome.found is False


def test_missing_credentials_via_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_RESEARCH_EXAMPLE_API_KEY", raising=False)
    registry = ProviderRegistry()
    provider = registry.resolve("example_open_data")
    outcome = provider.lookup(_q("IN", "marion"))
    assert outcome.found is False
    assert outcome.error_code == "credentials_missing"


def test_resolve_for_county_uses_default_without_county_hardcoding(
    tmp_path: Path,
) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "default_property_provider": "manual_lookup",
                "providers": {
                    "manual_lookup": {"type": "manual", "description": "test"},
                    "null": {"type": "null", "description": "test"},
                },
            }
        ),
        encoding="utf-8",
    )
    counties = tmp_path / "counties"
    (counties / "md").mkdir(parents=True)
    (counties / "md" / "harford.yaml").write_text(
        "county_name: Harford\nstate: MD\n",
        encoding="utf-8",
    )
    registry = ProviderRegistry(config_path=config, counties_dir=counties)
    provider = registry.resolve_for_county("MD", "harford")
    assert provider.name == "manual_lookup"


def test_county_research_block_selects_named_provider(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "default_property_provider": "manual_lookup",
                "providers": {
                    "manual_lookup": {"type": "manual"},
                    "null": {"type": "null"},
                },
            }
        ),
        encoding="utf-8",
    )
    counties = tmp_path / "counties" / "md"
    counties.mkdir(parents=True)
    (counties / "harford.yaml").write_text(
        "county_name: Harford\nstate: MD\nresearch:\n  property_provider: null\n",
        encoding="utf-8",
    )
    registry = ProviderRegistry(config_path=config, counties_dir=tmp_path / "counties")
    provider = registry.resolve_for_county("MD", "harford")
    assert isinstance(provider, NullProvider)
