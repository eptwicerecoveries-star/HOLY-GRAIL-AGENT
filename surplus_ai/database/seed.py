from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User

logger = structlog.get_logger(__name__)

_DEV_USERS: tuple[dict[str, str], ...] = (
    {"name": "Dev Admin", "email": "dev-admin@example.invalid", "role": UserRole.ADMIN.value},
    {"name": "Dev Agent", "email": "dev-agent@example.invalid", "role": UserRole.AGENT.value},
)


def seed_dev_data(session: Session) -> int:
    """Insert idempotent development-only reference rows. Returns the number created.

    Safe to run repeatedly: rows already present (matched on the natural key) are skipped.
    Seeds no county or case data, since those are county-specific and must come from config.
    """
    created = 0
    for spec in _DEV_USERS:
        existing = session.scalar(select(User).where(User.email == spec["email"]))
        if existing is not None:
            logger.debug("seed_user_exists", email=spec["email"])
            continue
        session.add(
            User(
                name=spec["name"],
                email=spec["email"],
                role=UserRole(spec["role"]),
                is_active=True,
            )
        )
        created += 1
        logger.info("seed_user_created", email=spec["email"], role=spec["role"])

    session.flush()
    logger.info("seed_dev_data_complete", created=created, skipped=len(_DEV_USERS) - created)
    return created
