from typing import Any

import asyncpg

from app.config import settings


async def create_db_pool() -> asyncpg.Pool:
    if settings.database_url:
        return await asyncpg.create_pool(
            dsn=settings.database_url.get_secret_value(),
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            command_timeout=30,
        )

    return await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=30,
    )


def build_assignments(values: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """Build SET clauses. Keys must be trusted names, such as Pydantic field names."""
    assignments: list[str] = []
    params: list[Any] = []
    for column, value in values.items():
        params.append(value)
        assignments.append(f"{column} = ${len(params)}")
    return assignments, params