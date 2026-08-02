from __future__ import annotations

from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

from surplus_ai.parser.exceptions import ParserError
from surplus_ai.parser.interpretation.county_config import CountyConfig, load_county_config
from surplus_ai.parser.interpretation.models import SurplusSource
from surplus_ai.parser.interpretation.pipeline import InterpretationPipeline
from surplus_ai.parser.pipeline import ParsingPipeline

logger = structlog.get_logger(__name__)


class CountyEvaluation(BaseModel):
    """How one county fared, with and without the help it has accumulated."""

    model_config = ConfigDict(frozen=True)

    county: str
    rows: int = 0
    columns: int = 0
    columns_resolved: int = 0
    surplus_source: SurplusSource = SurplusSource.ABSENT
    error: str | None = None

    @property
    def column_resolution_rate(self) -> float:
        return self.columns_resolved / self.columns if self.columns else 0.0

    @property
    def succeeded(self) -> bool:
        return self.error is None


class HoldoutComparison(BaseModel):
    """One county evaluated twice: with its configuration, and cold."""

    model_config = ConfigDict(frozen=True)

    county: str
    configured: CountyEvaluation
    cold_start: CountyEvaluation

    @property
    def rows_match(self) -> bool:
        """Extraction must not depend on configuration at all.

        Configuration says what columns *mean*. If holding it out changes how many rows
        come back, then county-specific knowledge has leaked into extraction, which is
        exactly the coupling the design forbids.
        """
        return self.configured.rows == self.cold_start.rows

    @property
    def column_resolution_delta(self) -> float:
        return self.configured.column_resolution_rate - self.cold_start.column_resolution_rate

    @property
    def surplus_degraded_safely(self) -> bool:
        """Losing configuration may cost an answer, but must never invent one.

        A cold-start county is allowed to report `ambiguous` or `absent` where a configured
        one reports a column. It is never allowed to name a surplus column that the
        configured run did not, because that would be a guess made in the absence of the
        very knowledge that settles it.
        """
        if self.cold_start.surplus_source in {SurplusSource.ABSENT, SurplusSource.AMBIGUOUS}:
            return True
        return self.cold_start.surplus_source == self.configured.surplus_source


class Evaluator:
    """Measures how well the generic path handles a county it knows nothing about.

    The corpus tests prove the pipeline reads the counties it has seen. That is not the
    same as proving it generalises: a rule tuned until one county passes will pass that
    county forever. Holding a county's configuration out and re-running it measures the
    cold-start behaviour directly, which is the number that matters as the corpus grows.
    """

    def __init__(
        self,
        parsing: ParsingPipeline | None = None,
        interpreting: InterpretationPipeline | None = None,
    ) -> None:
        self._parsing = parsing or ParsingPipeline()
        self._interpreting = interpreting or InterpretationPipeline()

    def evaluate(
        self, pdf_path: Path, county_config: CountyConfig | None = None
    ) -> CountyEvaluation:
        """Run one county through the pipeline and report what it achieved."""
        name = pdf_path.stem
        try:
            parsed = self._parsing.parse(pdf_path)
            document = self._interpreting.interpret(parsed, county_config)
        except ParserError as exc:
            return CountyEvaluation(county=name, error=str(exc))

        columns = sum(len(t.mappings) for t in document.tables)
        resolved = sum(1 for t in document.tables for m in t.mappings if m.is_resolved)
        surplus = document.tables[0].surplus.source if document.tables else SurplusSource.ABSENT
        return CountyEvaluation(
            county=name,
            rows=document.total_rows,
            columns=columns,
            columns_resolved=resolved,
            surplus_source=surplus,
        )

    def compare_holdout(self, pdf_path: Path, state: str, county: str) -> HoldoutComparison:
        """Evaluate a county with its configuration, then again without it."""
        config = load_county_config(state, county)
        configured = self.evaluate(pdf_path, config)
        cold = self.evaluate(pdf_path, None)
        logger.info(
            "holdout_evaluated",
            county=pdf_path.stem,
            configured_surplus=configured.surplus_source.value,
            cold_surplus=cold.surplus_source.value,
            rows_match=configured.rows == cold.rows,
        )
        return HoldoutComparison(county=pdf_path.stem, configured=configured, cold_start=cold)


class CorpusReport(BaseModel):
    """Corpus-wide totals, so a change that helps one county and hurts the rest is visible."""

    model_config = ConfigDict(frozen=True)

    evaluations: tuple[CountyEvaluation, ...] = Field(default_factory=tuple)

    @property
    def counties(self) -> int:
        return len(self.evaluations)

    @property
    def succeeded(self) -> int:
        return sum(1 for e in self.evaluations if e.succeeded)

    @property
    def total_rows(self) -> int:
        return sum(e.rows for e in self.evaluations)

    @property
    def mean_column_resolution(self) -> float:
        usable = [e for e in self.evaluations if e.succeeded and e.columns]
        if not usable:
            return 0.0
        return sum(e.column_resolution_rate for e in usable) / len(usable)

    @property
    def counties_naming_a_surplus(self) -> int:
        return sum(
            1
            for e in self.evaluations
            if e.surplus_source
            in {SurplusSource.EXPLICIT, SurplusSource.COUNTY_CONFIG, SurplusSource.DERIVED}
        )
