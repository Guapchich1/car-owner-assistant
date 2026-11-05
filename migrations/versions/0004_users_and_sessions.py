"""Пользователи, сессии и владелец машины

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт users и user_sessions, добавляет в cars поле owner_id.

    Машины, добавленные раньше, остаются без владельца и не показываются.
    """
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=30), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    # SQLite не умеет добавлять ограничения через ALTER — batch пересоздаёт таблицу.
    # Внешние ключи при миграции выключены, поэтому анкеты и журнал не удаляются
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_cars_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint("uq_cars_owner_id", ["owner_id"])


def downgrade() -> None:
    """Удаляет owner_id, users и user_sessions; машины остаются."""
    with op.batch_alter_table("cars", schema=None) as batch_op:
        batch_op.drop_constraint("uq_cars_owner_id", type_="unique")
        batch_op.drop_constraint("fk_cars_owner_id_users", type_="foreignkey")
        batch_op.drop_column("owner_id")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("uq_users_username_lower", table_name="users")
    op.drop_table("users")
