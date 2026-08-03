"""a case may have no case number

Most counties publish a parcel or account number rather than a case number, and some
publish neither. Requiring one forced a synthesised identifier into a field a reader would
take for the county's own wording. Case identity lives in dedupe_hash instead.

Revision ID: a96d6f5432c0
Revises: 1c15277b8000
Create Date: 2026-08-03 00:34:00.297028

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a96d6f5432c0"
down_revision: str | None = "1c15277b8000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "surplus_cases", "case_number", existing_type=sa.VARCHAR(length=200), nullable=True
    )


def downgrade() -> None:
    # Rows written since the upgrade may legitimately hold NULL, and there is no honest
    # value to put in their place. Filling them in would fabricate a county's identifier,
    # so the downgrade refuses rather than guessing.
    bind = op.get_bind()
    nulls = bind.execute(
        sa.text("SELECT count(*) FROM surplus_cases WHERE case_number IS NULL")
    ).scalar_one()
    if nulls:
        raise RuntimeError(
            f"{nulls} surplus_cases row(s) have no case_number. Downgrading would require "
            "inventing one. Delete or amend those rows deliberately before downgrading."
        )
    op.alter_column(
        "surplus_cases", "case_number", existing_type=sa.VARCHAR(length=200), nullable=False
    )
