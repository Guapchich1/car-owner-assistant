"""Расшифровки VIN, журнал проверок VIN, VIN машины без уникальности

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# В 0001 ограничение unique на cars.vin создано без имени. Правило имён
# даёт ему имя при пересоздании таблицы, чтобы его можно было снять
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    """Создаёт vin_decodes и vin_requests; cars.vin — обычный индекс вместо unique.

    Одну машину могут добавить разные пользователи, поэтому VIN не уникален.
    """
    op.create_table(
        "vin_decodes",
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("vin"),
    )
    op.create_table(
        "vin_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("answered", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vin_requests_user_id", "vin_requests", ["user_id"])
    # SQLite не умеет снимать ограничения через ALTER — batch пересоздаёт таблицу.
    # Внешние ключи при миграции выключены, поэтому записи и анкеты не удаляются
    with op.batch_alter_table(
        "cars", schema=None, naming_convention=NAMING
    ) as batch_op:
        batch_op.drop_constraint("uq_cars_vin", type_="unique")
        batch_op.create_index("ix_cars_vin", ["vin"])


def downgrade() -> None:
    """Возвращает unique на cars.vin, удаляет vin_requests и vin_decodes.

    Если в базе уже есть машины с одинаковым VIN, откат не пройдёт.
    """
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.drop_index("ix_cars_vin")
        batch_op.create_unique_constraint("uq_cars_vin", ["vin"])
    op.drop_index("ix_vin_requests_user_id", table_name="vin_requests")
    op.drop_table("vin_requests")
    op.drop_table("vin_decodes")
