from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from rapidfuzz import fuzz, process

from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.canonical import CanonicalField

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSING_CONFIG_DIR = PROJECT_ROOT / "config" / "parsing"

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")
_POSITIONAL_SUFFIX_RE = re.compile(r"__\d+$")

# Applied before punctuation is stripped, so "#" and "no." survive as a word.
_TOKEN_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"#"), " number "),
    # "no"/"nos" expands only when it carries a period or ends the label. Requiring one of
    # those keeps "Parcel No" and "Acct. No." working while leaving a header like
    # "No Sale" alone, where the word is a negation rather than an abbreviation. A plain
    # \b on the right would not do: "Acct. No." ends on the period, and there is no word
    # boundary between a period and the end of the string.
    (re.compile(r"\bnos?(?:\.|$)", re.IGNORECASE), " number "),
    (re.compile(r"\bamt\b", re.IGNORECASE), " amount "),
    (re.compile(r"\bacct\b", re.IGNORECASE), " account "),
)


class AliasConfigError(ParserError):
    """The alias or surplus vocabulary could not be loaded."""


def normalize_header(header: str) -> str:
    """Reduce a published column label to a comparable key.

    "ACCT #", "Acct. No." and "account number" all normalize to "account number", which is
    what lets one county's spelling help every county that follows. The original label is
    never modified -- only this derived key is.
    """
    text = _POSITIONAL_SUFFIX_RE.sub("", header.strip())
    for pattern, replacement in _TOKEN_EXPANSIONS:
        text = pattern.sub(replacement, text)
    text = _PUNCTUATION_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip().casefold()


class AliasRegistry:
    """Maps published column labels onto canonical fields."""

    def __init__(self, aliases: dict[CanonicalField, tuple[str, ...]]) -> None:
        self._aliases = aliases
        self._exact: dict[str, CanonicalField] = {}
        for field, labels in aliases.items():
            for label in labels:
                key = normalize_header(label)
                if key and key not in self._exact:
                    self._exact[key] = field
        self._choices = tuple(self._exact)

    @property
    def exact_keys(self) -> tuple[str, ...]:
        return self._choices

    def match_exact(self, header: str) -> CanonicalField | None:
        """Resolve a header by exact match on its normalized form."""
        return self._exact.get(normalize_header(header))

    def match_fuzzy(self, header: str, threshold: float) -> tuple[CanonicalField, float] | None:
        """Resolve a header by similarity, returning the field and its score.

        Used only for non-surplus fields. Confusing `assessed value` with
        `appraised value` costs an enrichment detail; confusing `sale amount` with
        `surplus amount` would invent money owed, so surplus never reaches this path.
        """
        key = normalize_header(header)
        if not key or not self._choices:
            return None
        best = process.extractOne(key, self._choices, scorer=fuzz.token_sort_ratio)
        if best is None:
            return None
        matched_key, score, _ = best
        if score < threshold:
            return None
        return self._exact[matched_key], float(score)


class SurplusVocabulary:
    """The explicit-only vocabulary that decides what counts as surplus."""

    def __init__(
        self,
        explicit_terms: tuple[str, ...],
        ambiguity_tokens: tuple[str, ...],
        denied_terms: tuple[str, ...],
    ) -> None:
        self._explicit = frozenset(normalize_header(t) for t in explicit_terms)
        self._tokens = tuple(normalize_header(t) for t in ambiguity_tokens)
        self._denied = frozenset(normalize_header(t) for t in denied_terms)

    def is_denied(self, header: str) -> bool:
        """True when a header can never be surplus, whatever else it resembles."""
        return normalize_header(header) in self._denied

    def is_explicit(self, header: str) -> bool:
        """True only on an exact match of the whole normalized header."""
        key = normalize_header(header)
        return key in self._explicit and key not in self._denied

    def ambiguity_token_in(self, header: str) -> str | None:
        """The shared token a header contains, if any, used to detect rival columns."""
        key = normalize_header(header)
        for token in self._tokens:
            if re.search(rf"\b{re.escape(token)}\b", key):
                return token
        return None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AliasConfigError(f"Configuration file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise AliasConfigError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AliasConfigError(f"{path} must contain a mapping at the top level")
    return loaded


@lru_cache(maxsize=1)
def load_alias_registry() -> AliasRegistry:
    """Load config/parsing/field_aliases.yaml into a registry."""
    raw = _load_yaml(PARSING_CONFIG_DIR / "field_aliases.yaml")
    aliases: dict[CanonicalField, tuple[str, ...]] = {}
    for field_name, labels in raw.items():
        try:
            field = CanonicalField(field_name)
        except ValueError as exc:
            raise AliasConfigError(
                f"field_aliases.yaml names an unknown canonical field: {field_name!r}"
            ) from exc
        if not isinstance(labels, list):
            raise AliasConfigError(f"Aliases for {field_name!r} must be a list")
        aliases[field] = tuple(str(label) for label in labels)

    if CanonicalField.SURPLUS_AMOUNT in aliases:
        raise AliasConfigError(
            "surplus_amount must not appear in field_aliases.yaml; its vocabulary lives in "
            "surplus_terms.yaml and is matched exactly, never fuzzily"
        )

    logger.debug("alias_registry_loaded", fields=len(aliases))
    return AliasRegistry(aliases)


@lru_cache(maxsize=1)
def load_surplus_vocabulary() -> SurplusVocabulary:
    """Load config/parsing/surplus_terms.yaml."""
    raw = _load_yaml(PARSING_CONFIG_DIR / "surplus_terms.yaml")
    for key in ("explicit_terms", "ambiguity_tokens", "denied_terms"):
        if not isinstance(raw.get(key), list):
            raise AliasConfigError(f"surplus_terms.yaml is missing the {key!r} list")

    vocabulary = SurplusVocabulary(
        explicit_terms=tuple(str(t) for t in raw["explicit_terms"]),
        ambiguity_tokens=tuple(str(t) for t in raw["ambiguity_tokens"]),
        denied_terms=tuple(str(t) for t in raw["denied_terms"]),
    )
    logger.debug("surplus_vocabulary_loaded", terms=len(raw["explicit_terms"]))
    return vocabulary


@lru_cache(maxsize=1)
def load_confidence_config() -> dict[str, Any]:
    """Load config/parsing/confidence.yaml."""
    raw = _load_yaml(PARSING_CONFIG_DIR / "confidence.yaml")
    for key in ("weights", "method_confidence", "routing"):
        if not isinstance(raw.get(key), dict):
            raise AliasConfigError(f"confidence.yaml is missing the {key!r} mapping")
    return raw
