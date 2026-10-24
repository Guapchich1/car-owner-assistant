"""Таблицы базы данных: машины и записи журнала работ."""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Категории работ — как поле type в app/data/demo.json
CATEGORIES = ("oil", "filters", "brakes", "tires", "diagnostic", "other")


class Car(Base):
    """Машина пользователя."""

    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    make: Mapped[str] = mapped_column(String(50))  # марка
    model: Mapped[str] = mapped_column(String(50))
    year: Mapped[int] = mapped_column(Integer)
    engine: Mapped[str | None] = mapped_column(String(50))
    # Необязательный: машин без VIN может быть несколько, NULL не конфликтует
    vin: Mapped[str | None] = mapped_column(String(17), unique=True)
    mileage: Mapped[int] = mapped_column(Integer)  # текущий пробег, км
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    records: Mapped[list["ServiceRecord"]] = relationship(
        back_populates="car",
        cascade="all, delete-orphan",
        order_by="ServiceRecord.date",
    )


class ServiceRecord(Base):
    """Запись журнала: выполненная работа и её стоимость."""

    __tablename__ = "service_records"
    __table_args__ = (
        CheckConstraint(
            "category IN ({})".format(", ".join(f"'{c}'" for c in CATEGORIES)),
            name="ck_service_records_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    mileage: Mapped[int] = mapped_column(Integer)  # пробег на момент работы, км
    category: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))  # название работы
    amount: Mapped[int] = mapped_column(Integer)  # стоимость, целые рубли
    service: Mapped[str | None] = mapped_column(String(200))  # где выполнено
    note: Mapped[str | None] = mapped_column(Text)  # примечание

    car: Mapped[Car] = relationship(back_populates="records")
