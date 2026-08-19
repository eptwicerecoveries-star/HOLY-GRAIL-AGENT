"""p4b2a login_throttle_buckets table

Additive login throttle bucket rows. Digest-only keys for IP attempt and
credential failure scopes. HTTP integration is P4-B2-B.

Revision ID: e1f4a8c92b03
Revises: d4c8a1b9e703
Create Date: 2026-08-17 23:05:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f4a8c92b03"
down_revision: str | None = "d4c8a1b9e703"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_throttle_buckets",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("event_count >= 0", name="ck_login_throttle_buckets_event_count"),
        sa.CheckConstraint(
            "scope IN ('ip', 'credential')",
            name="ck_login_throttle_buckets_scope",
        ),
        sa.PrimaryKeyConstraint("scope", "key_digest"),
    )


def downgrade() -> None:
    op.drop_table("login_throttle_buckets")
