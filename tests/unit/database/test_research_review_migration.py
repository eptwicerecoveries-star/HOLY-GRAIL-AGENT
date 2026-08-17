from __future__ import annotations

from surplus_ai.database.migrations.versions.e7b2c91f4a60_phase6c_research_review_items import (
    down_revision,
    revision,
)
from surplus_ai.database.migrator import head_revision


def test_phase6c_migration_revises_prior_head() -> None:
    assert down_revision == "a96d6f5432c0"
    assert revision == "e7b2c91f4a60"
    # Head advanced by Phase 6F-A; 6C remains the direct parent of that revision.
    assert head_revision() == "f3a8d12e90b1"
