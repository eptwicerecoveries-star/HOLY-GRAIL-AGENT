"""phase 6c research review items

Additive human-review work list over existing research_results. Does not rewrite
research evidence. Does not backfill historical rows.

Revision ID: e7b2c91f4a60
Revises: a96d6f5432c0
Create Date: 2026-08-14 00:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b2c91f4a60"
down_revision: str | None = "a96d6f5432c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# review_status already exists. Reusing it inside create_table would try to CREATE TYPE
# again, so it is referenced with create_type=False. The two new types are created
# explicitly below, then also referenced with create_type=False so create_table does not
# emit CREATE TYPE a second time.
_EXISTING_REVIEW_STATUS = postgresql.ENUM(
    "pending",
    "resolved",
    "rejected",
    name="review_status",
    create_type=False,
)

_RESEARCH_REVIEW_REASON = postgresql.ENUM(
    "ambiguous_identity",
    "complex_owner_context",
    "manual_research_required",
    "provider_unavailable",
    "provider_failure",
    name="research_review_reason",
    create_type=False,
)
_RESEARCH_REVIEW_RESOLUTION = postgresql.ENUM(
    "evidence_usable",
    "evidence_insufficient",
    "needs_additional_research",
    "conflict_unresolved",
    "not_relevant",
    name="research_review_resolution",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _RESEARCH_REVIEW_REASON.create(bind, checkfirst=True)
    _RESEARCH_REVIEW_RESOLUTION.create(bind, checkfirst=True)

    op.create_table(
        "research_review_items",
        sa.Column("surplus_case_id", sa.Uuid(), nullable=False),
        sa.Column("research_result_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("reason", _RESEARCH_REVIEW_REASON, nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("status", _EXISTING_REVIEW_STATUS, nullable=False),
        sa.Column("resolution", _RESEARCH_REVIEW_RESOLUTION, nullable=True),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["surplus_case_id"], ["surplus_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_result_id"], ["research_results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_review_items_surplus_case_id"),
        "research_review_items",
        ["surplus_case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_review_items_research_result_id"),
        "research_review_items",
        ["research_result_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_review_items_provider"),
        "research_review_items",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_review_items_reason"),
        "research_review_items",
        ["reason"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_review_items_status"),
        "research_review_items",
        ["status"],
        unique=False,
    )
    op.create_index(
        "uq_research_review_open_issue",
        "research_review_items",
        ["surplus_case_id", "provider", "reason"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_research_review_open_issue", table_name="research_review_items")
    op.drop_index(op.f("ix_research_review_items_status"), table_name="research_review_items")
    op.drop_index(op.f("ix_research_review_items_reason"), table_name="research_review_items")
    op.drop_index(op.f("ix_research_review_items_provider"), table_name="research_review_items")
    op.drop_index(
        op.f("ix_research_review_items_research_result_id"), table_name="research_review_items"
    )
    op.drop_index(
        op.f("ix_research_review_items_surplus_case_id"), table_name="research_review_items"
    )
    op.drop_table("research_review_items")
    bind = op.get_bind()
    _RESEARCH_REVIEW_RESOLUTION.drop(bind, checkfirst=True)
    _RESEARCH_REVIEW_REASON.drop(bind, checkfirst=True)
