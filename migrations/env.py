"""Запуск миграций Alembic."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401 — регистрирует таблицы в Base.metadata
from app.db import DATABASE_URL, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Адрес базы можно передать через config (так делают тесты),
# иначе берём DATABASE_URL из app/db.py
url = config.get_main_option("sqlalchemy.url") or DATABASE_URL


def run_migrations_offline() -> None:
    """Миграции без подключения к базе: выводят SQL."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Миграции с подключением к базе."""
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # render_as_batch: SQLite не умеет большинство ALTER TABLE,
        # Alembic в этом режиме пересоздаёт таблицу целиком
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
