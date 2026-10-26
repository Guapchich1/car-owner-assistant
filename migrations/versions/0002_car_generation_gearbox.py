"""Поколение и коробка передач машины

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет в cars поля generation и gearbox."""
    # SQLite не умеет добавлять ограничения через ALTER — batch пересоздаёт таблицу
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("generation", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(sa.Column("gearbox", sa.String(length=20), nullable=True))
        batch_op.create_check_constraint(
            "ck_cars_gearbox",
            "gearbox IN ('manual', 'automatic', 'cvt', 'robot')",
        )


def downgrade() -> None:
    """Удаляет поля generation и gearbox."""
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.drop_constraint("ck_cars_gearbox", type_="check")
        batch_op.drop_column("gearbox")
        batch_op.drop_column("generation")
