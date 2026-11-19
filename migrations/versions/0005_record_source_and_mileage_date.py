"""Источник записи журнала и дата пробега машины

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет service_records.source и cars.mileage_date.

    Прежние записи журнала — «добавлено вручную». Пробег прежних машин
    считаем указанным в день добавления машины (по Москве).
    """
    # SQLite не умеет добавлять ограничения через ALTER — batch пересоздаёт таблицу.
    # Внешние ключи при миграции выключены, поэтому записи и анкеты не удаляются
    with op.batch_alter_table("service_records", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=20),
                server_default="manual",
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_service_records_source", "source IN ('manual', 'apipoint')"
        )

    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mileage_date", sa.Date(), nullable=True))
    # created_at — UTC; дата по Москве на 3 часа позже
    op.execute("UPDATE cars SET mileage_date = date(created_at, '+3 hours')")
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.alter_column("mileage_date", existing_type=sa.Date(), nullable=False)


def downgrade() -> None:
    """Удаляет cars.mileage_date и service_records.source; данные остаются."""
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.drop_column("mileage_date")
    with op.batch_alter_table("service_records", schema=None) as batch_op:
        batch_op.drop_constraint("ck_service_records_source", type_="check")
        batch_op.drop_column("source")
