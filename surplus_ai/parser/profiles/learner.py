from __future__ import annotations

import structlog

from surplus_ai.parser.interpretation.models import InterpretedDocument, SurplusSource
from surplus_ai.parser.interpretation.type_inference import looks_like_person_name
from surplus_ai.parser.models import ParsedDocumentResult
from surplus_ai.parser.profiles.models import (
    CountyProfile,
    OwnerTypeObservation,
    TableStructure,
)

logger = structlog.get_logger(__name__)

OWNER_SAMPLE_SIZE = 200


class ProfileLearner:
    """Builds a county profile from a document that has just been read.

    Learning is observation, never inference about business meaning. The profile records
    what the county did -- which strategy read it, what its columns were called, whether it
    named a surplus -- so the next document from that county begins informed instead of
    cold.
    """

    def learn(
        self,
        county_slug: str,
        parsed: ParsedDocumentResult,
        interpreted: InterpretedDocument | None = None,
        county_name: str = "",
        state: str = "",
    ) -> CountyProfile:
        """Derive a profile from one parsed, optionally interpreted, document."""
        structures = tuple(
            TableStructure(
                table_index=table.table_index,
                column_count=len(table.original_headers),
                original_columns=table.original_headers,
                spans_pages=max(len(table.page_numbers), 1),
                repeated_header_pages=max(len(table.page_numbers) - 1, 0),
            )
            for table in parsed.tables
        )
        columns = tuple(
            dict.fromkeys(header for table in parsed.tables for header in table.original_headers)
        )

        surplus_source = SurplusSource.ABSENT
        surplus_column: str | None = None
        unresolved: tuple[str, ...] = ()
        owner_types = OwnerTypeObservation()

        if interpreted is not None and interpreted.tables:
            primary = interpreted.tables[0]
            surplus_source = primary.surplus.source
            surplus_column = primary.surplus.source_column
            unresolved = tuple(
                dict.fromkeys(h for t in interpreted.tables for h in t.unresolved_headers)
            )
            owner_types = self._observe_owner_types(interpreted)

        profile = CountyProfile(
            county_slug=county_slug,
            county_name=county_name,
            state=state,
            pdf_type=parsed.profile.pdf_type,
            ocr_required=parsed.profile.ocr_required,
            required_parsing_strategy=parsed.winning_strategy,
            table_structures=structures,
            original_column_names=columns,
            surplus_explicitly_listed=surplus_source
            in {SurplusSource.EXPLICIT, SurplusSource.COUNTY_CONFIG},
            surplus_source=surplus_source,
            surplus_source_column=surplus_column,
            owner_types=owner_types,
            typical_row_count=parsed.total_rows,
            mean_extraction_confidence=parsed.extraction_confidence,
            unresolved_columns=unresolved,
        )
        logger.info(
            "profile_learned",
            county=county_slug,
            version=profile.version_hash()[:12],
            strategy=profile.required_parsing_strategy,
            columns=len(columns),
            surplus=surplus_source.value,
        )
        return profile

    def _observe_owner_types(self, interpreted: InterpretedDocument) -> OwnerTypeObservation:
        """Count how many owner names look like people rather than entities.

        A distribution, not a classification. Phase 3 owns the decision; this only records
        what the names looked like so that phase starts with evidence.
        """
        from surplus_ai.parser.interpretation.canonical import CanonicalField

        names: list[str] = []
        for table in interpreted.tables:
            for row in table.rows:
                value = row.value(CanonicalField.OWNER_NAME)
                if isinstance(value, str) and value.strip():
                    names.append(value)
                if len(names) >= OWNER_SAMPLE_SIZE:
                    break
            if len(names) >= OWNER_SAMPLE_SIZE:
                break

        if not names:
            return OwnerTypeObservation()
        person_like = sum(1 for name in names if looks_like_person_name(name))
        return OwnerTypeObservation(
            sampled=len(names),
            person_like=person_like,
            entity_like=len(names) - person_like,
        )
