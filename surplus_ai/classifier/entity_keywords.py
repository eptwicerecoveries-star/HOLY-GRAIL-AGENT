from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict

from surplus_ai.classifier.exceptions import KeywordConfigError
from surplus_ai.database.models.enums import OwnerType

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATION_CONFIG_DIR = PROJECT_ROOT / "config" / "classification"

_PUNCTUATION_RE = re.compile(r"[^\w\s&]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    """Reduce an owner name to a comparable form, keeping ampersands.

    "&" survives because it carries meaning here: "WHYTE, WALTER & CATHERINE" is two people
    on one line, which is a different thing from one person.
    """
    text = _PUNCTUATION_RE.sub(" ", raw)
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


class EntityKeywordSet(BaseModel):
    """The vocabulary that decides what kind of party a name refers to."""

    model_config = ConfigDict(frozen=True)

    company_markers: tuple[str, ...] = ()
    trust_markers: tuple[str, ...] = ()
    estate_markers: tuple[str, ...] = ()
    government_markers: tuple[str, ...] = ()
    person_suffixes: tuple[str, ...] = ()
    pursue: dict[str, bool] = {}

    def markers_for(self, owner_type: OwnerType) -> tuple[str, ...]:
        return {
            OwnerType.COMPANY: self.company_markers,
            OwnerType.TRUST: self.trust_markers,
            OwnerType.ESTATE: self.estate_markers,
            OwnerType.GOVERNMENT: self.government_markers,
        }.get(owner_type, ())

    def is_pursuable(self, owner_type: OwnerType) -> bool:
        """Whether this kind of owner is worth contacting.

        Configuration, not code, because it is a business decision rather than a fact about
        the name. Estates and trusts are pursued by default: an estate has heirs who are
        entitled to the money and often do not know it exists.
        """
        return self.pursue.get(owner_type.value, False)


def matches_whole_word(normalized_name: str, marker: str) -> bool:
    """Whether a marker appears as a whole word.

    Substring matching would be actively wrong here. "inc" appears inside "Vincent",
    "co" inside "Cooper", and "lp" inside "Alpert" -- each of which would turn a real
    person into a company and lose the lead.
    """
    if not marker:
        return False
    pattern = r"\b" + r"\s+".join(re.escape(part) for part in marker.split()) + r"\b"
    return re.search(pattern, normalized_name) is not None


@lru_cache(maxsize=1)
def load_entity_keywords() -> EntityKeywordSet:
    """Load config/classification/entity_keywords.yaml."""
    path = CLASSIFICATION_CONFIG_DIR / "entity_keywords.yaml"
    if not path.is_file():
        raise KeywordConfigError(f"Configuration file not found: {path}")
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise KeywordConfigError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise KeywordConfigError(f"{path} must contain a mapping at the top level")

    for key in ("company_markers", "trust_markers", "estate_markers", "government_markers"):
        if not isinstance(raw.get(key), list):
            raise KeywordConfigError(f"{path} is missing the {key!r} list")

    pursue = raw.get("pursue") or {}
    if not isinstance(pursue, dict):
        raise KeywordConfigError(f"{path}: 'pursue' must be a mapping")
    for name in pursue:
        try:
            OwnerType(name)
        except ValueError as exc:
            raise KeywordConfigError(f"{path}: 'pursue' names unknown owner type {name!r}") from exc

    keywords = EntityKeywordSet(
        company_markers=tuple(normalize_name(str(m)) for m in raw["company_markers"]),
        trust_markers=tuple(normalize_name(str(m)) for m in raw["trust_markers"]),
        estate_markers=tuple(normalize_name(str(m)) for m in raw["estate_markers"]),
        government_markers=tuple(normalize_name(str(m)) for m in raw["government_markers"]),
        person_suffixes=tuple(normalize_name(str(m)) for m in raw.get("person_suffixes", [])),
        pursue={str(k): bool(v) for k, v in pursue.items()},
    )
    logger.debug(
        "entity_keywords_loaded",
        company=len(keywords.company_markers),
        estate=len(keywords.estate_markers),
    )
    return keywords
