from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.canonical import CanonicalField

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COUNTIES_CONFIG_DIR = PROJECT_ROOT / "config" / "counties"


class CountyConfigError(ParserError):
    """A county configuration file is missing or invalid."""


class DerivedSurplus(BaseModel):
    model_config = ConfigDict(frozen=True)

    formula: str
    source_note: str


class CountyConfig(BaseModel):
    """Per-county overrides. Highest precedence in column interpretation.

    A county needs one of these only when the generic rules get something wrong or report
    ambiguity. Adding one is a configuration change, never a code change.
    """

    model_config = ConfigDict(frozen=True)

    county_name: str
    state: str
    column_overrides: dict[str, CanonicalField] = Field(default_factory=dict)
    surplus_column: str | None = None
    surplus_column_declared: bool = False
    derived_surplus: DerivedSurplus | None = None
    notes: str = ""

    def override_for(self, header: str) -> CanonicalField | None:
        """Look up an override, tolerating whitespace differences in the header."""
        if header in self.column_overrides:
            return self.column_overrides[header]
        stripped = header.strip()
        for key, field in self.column_overrides.items():
            if key.strip() == stripped:
                return field
        return None


def county_config_path(state: str, slug: str) -> Path:
    return COUNTIES_CONFIG_DIR / state.lower() / f"{slug.lower()}.yaml"


def load_county_config(state: str, slug: str) -> CountyConfig | None:
    """Load a county's overrides, or None when the county has no file.

    Having no file is normal and means "use the generic rules", which is different from a
    file that explicitly asserts the county publishes no surplus.
    """
    path = county_config_path(state, slug)
    if not path.is_file():
        return None
    return _load_county_config_file(path)


@lru_cache(maxsize=128)
def _load_county_config_file(path: Path) -> CountyConfig:
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise CountyConfigError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CountyConfigError(f"{path} must contain a mapping at the top level")

    overrides: dict[str, CanonicalField] = {}
    for header, field_name in (raw.get("column_overrides") or {}).items():
        try:
            overrides[str(header)] = CanonicalField(field_name)
        except ValueError as exc:
            raise CountyConfigError(
                f"{path} maps {header!r} to unknown canonical field {field_name!r}"
            ) from exc

    derived_raw = raw.get("derived_surplus")
    derived = None
    if derived_raw:
        if not isinstance(derived_raw, dict) or "formula" not in derived_raw:
            raise CountyConfigError(f"{path}: derived_surplus needs a 'formula'")
        derived = DerivedSurplus(
            formula=str(derived_raw["formula"]),
            source_note=str(derived_raw.get("source_note", "")),
        )

    config = CountyConfig(
        county_name=str(raw.get("county_name", "")),
        state=str(raw.get("state", "")),
        column_overrides=overrides,
        surplus_column=(
            str(raw["surplus_column"]) if raw.get("surplus_column") is not None else None
        ),
        surplus_column_declared="surplus_column" in raw,
        derived_surplus=derived,
        notes=str(raw.get("notes", "")),
    )
    logger.debug(
        "county_config_loaded",
        path=str(path),
        overrides=len(overrides),
        surplus_column=config.surplus_column,
    )
    return config
