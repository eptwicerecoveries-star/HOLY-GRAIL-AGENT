from __future__ import annotations

from datetime import date
from decimal import Decimal

import structlog

from surplus_ai.parser.interpretation.canonical import CanonicalField, kind_of
from surplus_ai.parser.interpretation.column_interpreter import ColumnInterpreter
from surplus_ai.parser.interpretation.confidence import ConfidenceModel
from surplus_ai.parser.interpretation.county_config import CountyConfig
from surplus_ai.parser.interpretation.models import (
    ColumnMapping,
    InterpretedDocument,
    InterpretedRow,
    InterpretedTable,
    SurplusResolution,
    SurplusSource,
)
from surplus_ai.parser.interpretation.surplus_resolver import SurplusResolver
from surplus_ai.parser.interpretation.typed_cell_cleanup import apply_parcel_id_cell_cleanup
from surplus_ai.parser.interpretation.type_inference import (
    coerce,
    has_usable_case_identity,
    parse_money,
)
from surplus_ai.parser.models import ParsedDocumentResult, RawRow, RawTable

logger = structlog.get_logger(__name__)

# Values sampled per column when a header has to be interpreted from its contents.
VALUE_SAMPLE_SIZE = 40


class InterpretationPipeline:
    """Turns verbatim extracted rows into the universal schema.

    Extraction and interpretation stay separate on purpose. The verbatim layer is the
    record of what a county actually published and never changes; this layer is an opinion
    about what those columns mean, and can be re-run and corrected against stored rows
    without going back to the PDF.
    """

    def __init__(
        self,
        interpreter: ColumnInterpreter | None = None,
        surplus_resolver: SurplusResolver | None = None,
        confidence_model: ConfidenceModel | None = None,
    ) -> None:
        self._interpreter = interpreter or ColumnInterpreter()
        self._surplus = surplus_resolver or SurplusResolver()
        self._confidence = confidence_model or ConfidenceModel()

    def interpret(
        self, parsed: ParsedDocumentResult, county_config: CountyConfig | None = None
    ) -> InterpretedDocument:
        """Interpret every table of an already-extracted document."""
        tables: list[InterpretedTable] = []
        warnings: list[str] = list(parsed.warnings)

        for raw_table in parsed.tables:
            table = self._interpret_table(parsed, raw_table, county_config)
            tables.append(table)
            if table.surplus.source is SurplusSource.AMBIGUOUS:
                warnings.append(table.surplus.reason)
            if table.unresolved_headers:
                warnings.append(
                    f"table {table.table_index}: unresolved columns "
                    f"{list(table.unresolved_headers)} were preserved but not mapped"
                )

        document = InterpretedDocument(
            source_path=parsed.profile.source_path,
            source_sha256=parsed.profile.source_sha256,
            tables=tuple(tables),
            county_config_used=county_config.county_name if county_config else None,
            warnings=tuple(warnings),
        )
        logger.info(
            "document_interpreted",
            path=str(parsed.profile.source_path),
            tables=len(tables),
            rows=document.total_rows,
            rows_with_surplus=document.rows_with_surplus,
            routing={k.value: v for k, v in document.routing_counts().items()},
        )
        return document

    def _interpret_table(
        self,
        parsed: ParsedDocumentResult,
        raw_table: RawTable,
        county_config: CountyConfig | None,
    ) -> InterpretedTable:
        headers = raw_table.original_headers
        surplus = self._surplus.resolve(headers, county_config)
        column_values = self._sample_column_values(raw_table)
        mappings = self._interpreter.interpret(headers, column_values, surplus, county_config)

        base_confidence = self._confidence.score(
            document_classification=parsed.profile.confidence,
            extraction_quality=parsed.extraction_confidence,
            header_confidence=raw_table.header_confidence,
            mappings=mappings,
        )

        rows = tuple(
            self._interpret_row(row, mappings, surplus, base_confidence) for row in raw_table.rows
        )
        return InterpretedTable(
            table_index=raw_table.table_index,
            original_headers=headers,
            mappings=mappings,
            surplus=surplus,
            rows=rows,
        )

    def _interpret_row(
        self,
        row: RawRow,
        mappings: tuple[ColumnMapping, ...],
        surplus: SurplusResolution,
        base_confidence: float,
    ) -> InterpretedRow:
        canonical: dict[CanonicalField, Decimal | date | str | None] = {}
        unmapped: dict[str, str] = {}
        failures: list[str] = []

        for mapping in mappings:
            raw_value = row.values.get(mapping.original_header, "")
            if mapping.canonical_field is None:
                if raw_value.strip():
                    unmapped[mapping.original_header] = raw_value
                continue
            kind = kind_of(mapping.canonical_field)
            typed = coerce(mapping.canonical_field, raw_value, kind)
            if typed is None and raw_value.strip():
                failures.append(
                    f"{mapping.original_header}={raw_value!r} is not a valid {kind.value}"
                )
            canonical[mapping.canonical_field] = typed

        cleanup = apply_parcel_id_cell_cleanup(canonical, mappings)
        canonical.update(cleanup.updates)

        # Any column the header set did not cover -- overflow cells, for instance -- is
        # still carried through rather than dropped.
        for header, value in row.values.items():
            if header not in {m.original_header for m in mappings} and value.strip():
                unmapped[header] = value

        surplus_amount = self._surplus_amount_for(row, surplus)
        confidence = base_confidence
        if failures:
            confidence *= 0.9

        return InterpretedRow(
            raw_values=dict(row.values),
            canonical_values=canonical,
            unmapped=unmapped,
            surplus_amount=surplus_amount,
            surplus_is_explicit=surplus.is_explicit and surplus_amount is not None,
            surplus_source=surplus.source,
            surplus_source_column=surplus.source_column,
            source_pdf_path=row.source_pdf_path,
            source_pdf_sha256=row.source_pdf_sha256,
            page_number=row.page_number,
            table_index=row.table_index,
            row_index_on_page=row.row_index_on_page,
            extraction_method=row.extraction_method,
            extraction_strategy=row.extraction_strategy,
            confidence=max(0.0, min(1.0, confidence)),
            routing=self._confidence.route(
                confidence,
                row.extraction_method,
                len(failures),
                surplus_unresolved=surplus.source is SurplusSource.AMBIGUOUS,
                identity_missing=not has_usable_case_identity(canonical),
                typed_cell_ambiguous=cleanup.ambiguous,
            ),
            coercion_failures=tuple(failures),
            review_reasons=cleanup.review_reasons,
        )

    def _surplus_amount_for(self, row: RawRow, surplus: SurplusResolution) -> Decimal | None:
        """Read the surplus figure from the designated column, and only from there.

        The designation is separate from the column's canonical identity. Marion's
        "Remaining Overbid" stays `remaining_amount` -- that is what the county calls it --
        and additionally supplies `surplus_amount`, because that is the money still held.
        Nothing is computed: if no column was designated, the amount is null.
        """
        if surplus.source_column is None:
            return None
        raw_value = row.values.get(surplus.source_column)
        if raw_value is None:
            stripped = surplus.source_column.strip()
            raw_value = next(
                (v for k, v in row.values.items() if k.strip() == stripped),
                None,
            )
        if raw_value is None:
            return None
        return parse_money(raw_value)

    def _sample_column_values(self, table: RawTable) -> dict[str, list[str]]:
        """Collect a sample of each column's values for value-based inference."""
        samples: dict[str, list[str]] = {header: [] for header in table.original_headers}
        for row in table.rows[:VALUE_SAMPLE_SIZE]:
            for header in table.original_headers:
                samples[header].append(row.values.get(header, ""))
        return samples
