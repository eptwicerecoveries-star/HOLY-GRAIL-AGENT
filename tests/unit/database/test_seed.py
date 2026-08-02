from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surplus_ai.database.models import User
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.seed import seed_dev_data


def test_seed_creates_expected_users(session: Session) -> None:
    created = seed_dev_data(session)

    emails = set(session.scalars(select(User.email)).all())
    assert created == 2
    assert emails == {"dev-admin@example.invalid", "dev-agent@example.invalid"}


def test_seeded_roles_are_correct(session: Session) -> None:
    seed_dev_data(session)

    admin = session.scalar(select(User).where(User.email == "dev-admin@example.invalid"))
    agent = session.scalar(select(User).where(User.email == "dev-agent@example.invalid"))

    assert admin is not None and admin.role is UserRole.ADMIN
    assert agent is not None and agent.role is UserRole.AGENT
    assert admin.is_active is True


def test_seed_is_idempotent(session: Session) -> None:
    first = seed_dev_data(session)
    second = seed_dev_data(session)
    third = seed_dev_data(session)

    assert (first, second, third) == (2, 0, 0)
    assert session.scalar(select(func.count()).select_from(User)) == 2


def test_seed_uses_non_routable_example_domain(session: Session) -> None:
    seed_dev_data(session)

    for email in session.scalars(select(User.email)).all():
        assert email.endswith(".invalid")
