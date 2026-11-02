from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from solbrief.config import get_settings
from solbrief.db.models import Base

target_metadata = Base.metadata

# procrastinate owns its own tables; keep autogenerate from trying to drop them.
PROCRASTINATE_PREFIX = "procrastinate_"


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name and name.startswith(PROCRASTINATE_PREFIX):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().sqlalchemy_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
