"""Config-driven property-record provider registry.

County-specific behaviour is expressed only through configuration (global defaults and,
when present, an optional ``research`` block in a county YAML). There are no
``if county == "…"`` branches.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from surplus_ai.research.exceptions import ProviderNotFoundError, ResearchConfigError
from surplus_ai.research.http import HttpGetter
from surplus_ai.research.policy import (
    ReliabilityPolicy,
    ReliabilityPolicyPatch,
    merge_reliability,
)
from surplus_ai.research.providers.arcgis import (
    ArcGISFeatureServerProvider,
    ArcGISProviderOptions,
)
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider
from surplus_ai.research.providers.credentials_missing import CredentialsMissingProvider
from surplus_ai.research.providers.manual import ManualLookupProvider
from surplus_ai.research.providers.null import NullProvider
from surplus_ai.research.providers.rest_json import RestJsonProvider, RestJsonProviderOptions
from surplus_ai.research.providers.socrata import SocrataOpenDataProvider, SocrataProviderOptions

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROVIDERS_PATH = PROJECT_ROOT / "config" / "research" / "providers.yaml"
COUNTIES_CONFIG_DIR = PROJECT_ROOT / "config" / "counties"

ProviderTypeName = Literal[
    "manual", "null", "credentials_required", "socrata", "arcgis", "rest_json"
]


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ProviderTypeName
    description: str = ""
    requires_credential: str | None = None
    reliability: ReliabilityPolicyPatch | None = None
    options: SocrataProviderOptions | ArcGISProviderOptions | RestJsonProviderOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def _typed_options(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        provider_type = data.get("type")
        raw_options = data.get("options")
        if raw_options is None or isinstance(
            raw_options,
            SocrataProviderOptions | ArcGISProviderOptions | RestJsonProviderOptions,
        ):
            return data
        if provider_type == "socrata":
            out = dict(data)
            out["options"] = SocrataProviderOptions.model_validate(raw_options)
            return out
        if provider_type == "arcgis":
            out = dict(data)
            out["options"] = ArcGISProviderOptions.model_validate(raw_options)
            return out
        if provider_type == "rest_json":
            out = dict(data)
            out["options"] = RestJsonProviderOptions.model_validate(raw_options)
            return out
        return data

    @model_validator(mode="after")
    def _options_match_type(self) -> ProviderSpec:
        if self.type == "socrata":
            if not isinstance(self.options, SocrataProviderOptions):
                raise ValueError("socrata providers require options")
        elif self.type == "arcgis":
            if not isinstance(self.options, ArcGISProviderOptions):
                raise ValueError("arcgis providers require options")
        elif self.type == "rest_json":
            if not isinstance(self.options, RestJsonProviderOptions):
                raise ValueError("rest_json providers require options")
        elif self.options is not None:
            raise ValueError(
                "options is only valid for type socrata, arcgis, or rest_json"
            )
        return self


class ProvidersFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    default_property_provider: str = "manual_lookup"
    reliability: ReliabilityPolicy = Field(default_factory=ReliabilityPolicy)
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"Unsupported providers.yaml schema_version: {value}")
        return value


class CountyResearchConfig(BaseModel):
    """Optional research block from config/counties/<state>/<slug>.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    property_provider: str | None = None


class ProviderRegistry:
    """Resolves property providers by configured name, never by hardcoded county."""

    def __init__(
        self,
        config_path: Path | None = None,
        *,
        counties_dir: Path | None = None,
        http_client: HttpGetter | None = None,
    ) -> None:
        self._config_path = config_path or DEFAULT_PROVIDERS_PATH
        self._counties_dir = counties_dir or COUNTIES_CONFIG_DIR
        self._http_client = http_client
        self._file = self._load_file(self._config_path)
        self._custom: dict[str, AbstractPropertyRecordProvider] = {}

    @property
    def default_provider_name(self) -> str:
        return self._file.default_property_provider

    def registered_names(self) -> list[str]:
        names = set(self._file.providers) | set(self._custom)
        return sorted(names)

    def register(self, provider: AbstractPropertyRecordProvider) -> None:
        """Register a provider instance (tests / future adapters)."""
        self._custom[provider.name] = provider

    @property
    def global_reliability(self) -> ReliabilityPolicy:
        return self._file.reliability

    def reliability_for(self, provider_name: str) -> ReliabilityPolicy:
        spec = self._file.providers.get(provider_name)
        override = spec.reliability if spec is not None else None
        return merge_reliability(self._file.reliability, override)

    def provider_type(self, provider_name: str) -> str | None:
        """Configured provider type, or None when the name is unconfigured/custom."""
        spec = self._file.providers.get(provider_name)
        if spec is None:
            return None
        return spec.type

    def resolve_for_county(self, state: str, county_slug: str) -> AbstractPropertyRecordProvider:
        county_cfg = load_county_research_config(
            state, county_slug, counties_dir=self._counties_dir
        )
        if county_cfg is not None and county_cfg.property_provider is not None:
            name = county_cfg.property_provider
        else:
            name = self._file.default_property_provider
        return self.resolve(name)

    def resolve(self, name: str) -> AbstractPropertyRecordProvider:
        if name in self._custom:
            return self._custom[name]

        spec = self._file.providers.get(name)
        if spec is None:
            logger.warning("research_provider_unknown", provider=name)
            # Missing configuration must never become success — NullProvider.
            return NullProvider(name=name)

        if spec.requires_credential:
            env_var = spec.requires_credential
            if not os.environ.get(env_var):
                return CredentialsMissingProvider(name=name, env_var=env_var)

        return self._build(name, spec)

    def describe(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for name, spec in sorted(self._file.providers.items()):
            cred = spec.requires_credential or ""
            cred_state = ""
            if cred:
                cred_state = "present" if os.environ.get(cred) else "MISSING"
            rows.append(
                {
                    "name": name,
                    "type": spec.type,
                    "description": spec.description,
                    "credential_env": cred,
                    "credential_status": cred_state,
                    "is_default": "yes" if name == self.default_provider_name else "",
                }
            )
        for name in sorted(self._custom):
            rows.append(
                {
                    "name": name,
                    "type": "custom",
                    "description": "runtime-registered",
                    "credential_env": "",
                    "credential_status": "",
                    "is_default": "",
                }
            )
        return rows

    def _build(self, name: str, spec: ProviderSpec) -> AbstractPropertyRecordProvider:
        if spec.type == "manual":
            return ManualLookupProvider(name=name)
        if spec.type == "null":
            return NullProvider(name=name)
        if spec.type == "credentials_required":
            env_var = spec.requires_credential or "SURPLUS_AI_RESEARCH_API_KEY"
            return CredentialsMissingProvider(name=name, env_var=env_var)
        if spec.type == "socrata":
            if not isinstance(spec.options, SocrataProviderOptions):
                raise ResearchConfigError(f"Socrata provider {name!r} is missing options")
            return SocrataOpenDataProvider(
                name=name,
                options=spec.options,
                http=self._http_client,
            )
        if spec.type == "arcgis":
            if not isinstance(spec.options, ArcGISProviderOptions):
                raise ResearchConfigError(f"ArcGIS provider {name!r} is missing options")
            return ArcGISFeatureServerProvider(
                name=name,
                options=spec.options,
                http=self._http_client,
            )
        if spec.type == "rest_json":
            if not isinstance(spec.options, RestJsonProviderOptions):
                raise ResearchConfigError(f"REST JSON provider {name!r} is missing options")
            return RestJsonProvider(
                name=name,
                options=spec.options,
                http=self._http_client,
            )
        raise ResearchConfigError(f"Unsupported provider type {spec.type!r} for {name!r}")

    @staticmethod
    def _load_file(path: Path) -> ProvidersFile:
        if not path.is_file():
            raise ResearchConfigError(
                f"Research providers config not found at {path}. "
                "Create config/research/providers.yaml."
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ResearchConfigError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ResearchConfigError(f"{path} must contain a mapping at the top level")
        raw = _normalize_providers_file_raw(raw)
        try:
            return ProvidersFile.model_validate(raw)
        except Exception as exc:
            raise ResearchConfigError(f"Invalid providers config in {path}: {exc}") from exc


def _normalize_yaml_null_string(value: Any) -> Any:
    """Coerce YAML null (Python None) to the provider name/type string \"null\"."""
    return "null" if value is None else value


def _normalize_providers_file_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Fix unquoted YAML ``null`` keys/types that PyYAML loads as None."""
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return raw
    normalized: dict[str, Any] = {}
    for key, spec in providers.items():
        name = _normalize_yaml_null_string(key)
        if not isinstance(name, str):
            normalized[key] = spec
            continue
        if isinstance(spec, dict):
            spec = dict(spec)
            if "type" in spec:
                spec["type"] = _normalize_yaml_null_string(spec["type"])
            normalized[name] = spec
        else:
            normalized[name] = spec
    out = dict(raw)
    out["providers"] = normalized
    return out


def load_county_research_config(
    state: str,
    county_slug: str,
    *,
    counties_dir: Path | None = None,
) -> CountyResearchConfig | None:
    """Load optional ``research`` block from a county YAML, if present.

    Does not alter parser CountyConfig. Unknown counties simply have no override.
    """
    root = counties_dir or COUNTIES_CONFIG_DIR
    path = root / state.strip().lower() / f"{county_slug.strip().lower()}.yaml"
    if not path.is_file():
        return None
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ResearchConfigError(f"Invalid county YAML {path}: {exc}") from exc
    if not isinstance(raw, dict):
        return None
    block = raw.get("research")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ResearchConfigError(f"{path} research block must be a mapping")
    # Explicit ``property_provider: null`` means the provider named "null", not "unset".
    if "property_provider" in block and block["property_provider"] is None:
        block = dict(block)
        block["property_provider"] = "null"
    try:
        return CountyResearchConfig.model_validate(block)
    except Exception as exc:
        raise ResearchConfigError(f"Invalid research block in {path}: {exc}") from exc


def require_known_provider(registry: ProviderRegistry, name: str) -> AbstractPropertyRecordProvider:
    """Resolve a provider by name, raising if it is neither configured nor custom."""
    if name not in registry.registered_names() and name not in registry._file.providers:
        raise ProviderNotFoundError(f"Unknown research provider {name!r}")
    return registry.resolve(name)
