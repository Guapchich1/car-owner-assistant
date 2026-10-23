"""Таблицы машин и записей журнала

Revision ID: 0001
Revises:
Create Date: 2026-10-24 01:43:07.931497

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт таблицы cars и service_records."""
    op.create_table(
        "cars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("make", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=50), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("engine", sa.String(length=50), nullable=True),
        sa.Column("vin", sa.String(length=17), nullable=True),
        sa.Column("mileage", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vin"),
    )
    op.create_table(
        "service_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("mileage", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("service", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "category IN ('oil', 'filters', 'brakes', 'tires', 'diagnostic', 'other')",
            name="ck_service_records_category",
        ),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("service_records", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_service_records_car_id"), ["car_id"], unique=False
        )


def downgrade() -> None:
    """Удаляет обе таблицы."""
    with op.batch_alter_table("service_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_service_records_car_id"))

    op.drop_table("service_records")
    op.drop_table("cars")
