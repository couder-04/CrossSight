"""User seeding helpers."""

from __future__ import annotations

from uuid import uuid4

from anpr_common.config import Settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import Role, hash_password
from api.db import User


async def ensure_seed_users(session: AsyncSession, settings: Settings) -> None:
    seeds = (
        (settings.seed_admin_user, settings.seed_admin_pass, Role.admin),
        (settings.seed_operator_user, settings.seed_operator_pass, Role.operator),
        (settings.seed_analyst_user, settings.seed_analyst_pass, Role.analyst),
    )
    for username, password, role in seeds:
        result = await session.execute(select(User).where(User.username == username))
        existing = result.scalar_one_or_none()
        if existing is None:
            session.add(
                User(
                    id=uuid4(),
                    username=username,
                    password_hash=hash_password(password),
                    role=role.value,
                )
            )
        else:
            existing.password_hash = hash_password(password)
            existing.role = role.value
    await session.commit()
