"""phase 6f-a contacts lead/type/value uniqueness

Concurrency-safe Contact dedupe invariant. Does not rewrite Contact values or delete
rows. Fail-closed if duplicate groups already exist.

Revision ID: f3a8d12e90b1
Revises: e7b2c91f4a60
Create Date: 2026-08-16 20:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a8d12e90b1"
down_revision: str | None = "e7b2c91f4a60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_contacts_lead_contact_type_value"


def upgrade() -> None:
    bind = op.get_bind()
    # Count duplicate *groups* only — never select/print contact values.
    duplicate_groups = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM contacts
                GROUP BY lead_id, contact_type, value
                HAVING COUNT(*) > 1
            ) AS duplicate_groups
            """
        )
    ).scalar_one()
    if int(duplicate_groups or 0) > 0:
        raise RuntimeError(
            "Cannot create "
            f"{_CONSTRAINT}: {int(duplicate_groups)} duplicate "
            "lead/contact_type/value group(s) exist. "
            "Resolve duplicates manually before upgrading; "
            "this migration does not delete or merge Contact rows."
        )
    op.create_unique_constraint(
        _CONSTRAINT,
        "contacts",
        ["lead_id", "contact_type", "value"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "contacts", type_="unique")
