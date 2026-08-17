"""p3-a nullable users.password_hash

Additive authentication-data foundation only. Does not add sessions, cookies,
or HTTP login. Existing User rows remain valid with password_hash NULL.

Revision ID: fc21ee2dd624
Revises: f3a8d12e90b1
Create Date: 2026-08-16 23:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fc21ee2dd624"
down_revision: str | None = "f3a8d12e90b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
