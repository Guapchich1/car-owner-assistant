"""Анкета об использовании автомобиля

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создаёт таблицу car_surveys: одна анкета на машину."""
    op.create_table(
        "car_surveys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("purposes", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("purposes_other", sa.String(length=200), nullable=True),
        sa.Column("trip_frequency", sa.String(length=20), nullable=True),
        sa.Column("monthly_km", sa.Integer(), nullable=True),
        sa.Column("mileage_amount", sa.Integer(), nullable=True),
        sa.Column("mileage_period", sa.String(length=10), nullable=True),
        sa.Column("trip_length", sa.String(length=20), nullable=True),
        sa.Column("road_mix", sa.String(length=20), nullable=True),
        sa.Column("long_trips", sa.String(length=20), nullable=True),
        sa.Column("long_trip_distance", sa.String(length=20), nullable=True),
        sa.Column("conditions", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("conditions_other", sa.String(length=200), nullable=True),
        sa.Column("service_triggers", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("service_triggers_other", sa.String(length=200), nullable=True),
        sa.Column("service_places", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("service_places_other", sa.String(length=200), nullable=True),
        sa.Column("last_service_date", sa.Date(), nullable=True),
        sa.Column("last_service_mileage", sa.Integer(), nullable=True),
        sa.Column("last_service_works", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("last_service_works_other", sa.String(length=200), nullable=True),
        sa.Column(
            "last_service_approximate",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("service_history", sa.String(length=20), nullable=True),
        sa.Column("self_checks", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("self_checks_other", sa.String(length=200), nullable=True),
        sa.Column("reminder_km", sa.Integer(), nullable=True),
        sa.Column("reminder_months", sa.Integer(), nullable=True),
        sa.Column("concerns", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("concerns_other", sa.String(length=200), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trip_frequency IN ('daily', 'few_week', 'weekly', 'rare', 'unknown')",
            name="ck_car_surveys_trip_frequency",
        ),
        sa.CheckConstraint(
            "monthly_km BETWEEN 0 AND 30000", name="ck_car_surveys_monthly_km"
        ),
        sa.CheckConstraint("mileage_amount >= 0", name="ck_car_surveys_mileage_amount"),
        sa.CheckConstraint(
            "mileage_period IN ('week', 'month', 'year')",
            name="ck_car_surveys_mileage_period",
        ),
        sa.CheckConstraint(
            "trip_length IN ('under_5', '5_20', '20_50', 'over_50', 'unknown')",
            name="ck_car_surveys_trip_length",
        ),
        sa.CheckConstraint(
            "road_mix IN ('city_jams', 'city_free', 'mixed', 'highway', 'unknown')",
            name="ck_car_surveys_road_mix",
        ),
        sa.CheckConstraint(
            "long_trips IN ('never', 'rare', 'several', 'monthly', 'unknown')",
            name="ck_car_surveys_long_trips",
        ),
        sa.CheckConstraint(
            "long_trip_distance IN ('200_500', '500_1000', 'over_1000', 'unknown')",
            name="ck_car_surveys_long_trip_distance",
        ),
        sa.CheckConstraint(
            "last_service_mileage >= 0", name="ck_car_surveys_last_service_mileage"
        ),
        sa.CheckConstraint(
            "service_history IN ('full', 'partial', 'none', 'unknown')",
            name="ck_car_surveys_service_history",
        ),
        sa.CheckConstraint(
            "reminder_km IN (500, 1000, 2000, 3000)", name="ck_car_surveys_reminder_km"
        ),
        sa.CheckConstraint(
            "reminder_months IN (1, 2, 3)", name="ck_car_surveys_reminder_months"
        ),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("car_id"),
    )


def downgrade() -> None:
    """Удаляет таблицу car_surveys."""
    op.drop_table("car_surveys")
