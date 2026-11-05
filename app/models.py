"""Таблицы базы данных: пользователи и сессии, машины, журнал работ и анкета."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Категории работ — как поле type в app/data/demo.json
CATEGORIES = ("oil", "filters", "brakes", "tires", "diagnostic", "other")

# Коробки передач: код в базе → название в интерфейсе
GEARBOXES = {
    "manual": "МКПП",
    "automatic": "АКПП",
    "cvt": "Вариатор",
    "robot": "Робот",
}

# Варианты ответов анкеты: код в базе → название в интерфейсе.
# Для 4.1: None — вопрос пропущен, unknown — «не знаю», none — «ничего из этого».

# Один вариант ответа: в базе — строка с CHECK
TRIP_FREQUENCIES = {
    "daily": "Почти каждый день",
    "few_week": "Несколько раз в неделю",
    "weekly": "Раз в неделю",
    "rare": "Реже",
    "unknown": "Не знаю",
}
TRIP_LENGTHS = {
    "under_5": "До 5 км",
    "5_20": "5–20 км",
    "20_50": "20–50 км",
    "over_50": "Больше 50 км",
    "unknown": "Не знаю",
}
ROAD_MIXES = {
    "city_jams": "Город, часто пробки",
    "city_free": "Город, пробки редко",
    "mixed": "Город и трасса поровну",
    "highway": "В основном трасса",
    "unknown": "Не знаю",
}
LONG_TRIPS = {
    "never": "Никогда",
    "rare": "1–2 раза в год",
    "several": "Несколько раз в год",
    "monthly": "Раз в месяц и чаще",
    "unknown": "Не знаю",
}
LONG_TRIP_DISTANCES = {
    "200_500": "200–500 км",
    "500_1000": "500–1000 км",
    "over_1000": "Больше 1000 км",
    "unknown": "Не знаю",
}
SERVICE_HISTORIES = {
    "full": "Есть полная",
    "partial": "Есть частичная",
    "none": "Нет",
    "unknown": "Не знаю",
}
# Период, за который пользователь указал пробег
MILEAGE_PERIODS = {
    "week": "в неделю",
    "month": "в месяц",
    "year": "в год",
}
REMINDER_KM = (500, 1000, 2000, 3000)
REMINDER_MONTHS = (1, 2, 3)
MAX_MONTHLY_KM = 30_000

# Несколько вариантов ответа: в базе — JSON-список кодов, проверяет сервер
PURPOSES = {
    "work": "Работа",
    "study": "Учёба",
    "family": "Семейные дела",
    "travel": "Путешествия",
    "dacha": "Дача",
    "taxi_delivery": "Такси или доставка",
}
CONDITIONS = {
    "bad_roads": "Плохие и грунтовые дороги",
    "dust": "Пыль",
    "frost": "Морозы",
    "heat": "Жара",
    "heavy_loads": "Тяжёлые грузы",
    "trailer": "Прицеп",
    "long_idle": "Простои больше месяца",
    "none": "Ничего из этого",
    "unknown": "Не знаю",
}
SERVICE_TRIGGERS = {
    "mileage": "По пробегу",
    "time": "По времени",
    "schedule": "По регламенту",
    "dashboard": "По индикатору на панели",
    "mechanic": "По совету мастера",
    "symptoms": "Когда что-то не так",
    "unknown": "Не знаю",
}
SERVICE_PLACES = {
    "dealer": "У дилера",
    "independent": "В независимом сервисе",
    "private": "У частного мастера",
    "self": "Сам",
    "unknown": "Не знаю",
}
SELF_CHECKS = {
    "oil": "Масло",
    "coolant": "Охлаждающая жидкость",
    "tires": "Давление в шинах",
    "lights": "Свет",
    "brake_fluid": "Тормозная жидкость",
    "none": "Ничего",
    "unknown": "Не знаю",
}
CONCERNS = {
    "noises": "Посторонние шумы",
    "vibration": "Вибрация",
    "brakes": "Тормоза",
    "warning_lights": "Индикаторы на панели",
    "leaks": "Подтёки",
    "starting": "Запуск двигателя",
    "consumption": "Расход топлива или масла",
    "steering": "Руль и подвеска",
    "none": "Ничего",
    "unknown": "Не знаю",
}


def _one_of(column: str, values) -> str:
    """Условие CHECK: значение колонки — из списка."""
    listed = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({listed})"


class User(Base):
    """Пользователь: логин хранится так, как он введён, пароль — только хешем."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# Логин уникален без учёта регистра: Arsen и arsen — один и тот же логин
Index("uq_users_username_lower", func.lower(User.username), unique=True)


class UserSession(Base):
    """Сессия входа. В базе только хеш токена: сам токен есть лишь в cookie."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)  # UTC без часового пояса
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class Car(Base):
    """Машина пользователя."""

    __tablename__ = "cars"
    __table_args__ = (
        CheckConstraint(
            "gearbox IN ({})".format(", ".join(f"'{g}'" for g in GEARBOXES)),
            name="ck_cars_gearbox",
        ),
        # У пользователя не больше одной машины; NULL между собой не конфликтуют
        UniqueConstraint("owner_id", name="uq_cars_owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL — машина добавлена до появления пользователей: её никто не видит
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_cars_owner_id_users")
    )
    make: Mapped[str] = mapped_column(String(50))  # марка
    model: Mapped[str] = mapped_column(String(50))
    # Поколение и коробка необязательны в базе: у старых записей их нет
    generation: Mapped[str | None] = mapped_column(String(100))
    gearbox: Mapped[str | None] = mapped_column(String(20))  # код из GEARBOXES
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
    survey: Mapped["CarSurvey | None"] = relationship(
        back_populates="car", cascade="all, delete-orphan"
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


class CarSurvey(Base):
    """Анкета об использовании: одна строка на машину.

    Строки нет — анкету не заполняли или пропустили. Сведения о последнем ТО —
    со слов владельца: в журнал работ они не попадают (перенос — в задаче 3.1).
    """

    __tablename__ = "car_surveys"
    __table_args__ = (
        CheckConstraint(
            _one_of("trip_frequency", TRIP_FREQUENCIES),
            name="ck_car_surveys_trip_frequency",
        ),
        CheckConstraint(
            f"monthly_km BETWEEN 0 AND {MAX_MONTHLY_KM}",
            name="ck_car_surveys_monthly_km",
        ),
        CheckConstraint("mileage_amount >= 0", name="ck_car_surveys_mileage_amount"),
        CheckConstraint(
            _one_of("mileage_period", MILEAGE_PERIODS),
            name="ck_car_surveys_mileage_period",
        ),
        CheckConstraint(
            _one_of("trip_length", TRIP_LENGTHS), name="ck_car_surveys_trip_length"
        ),
        CheckConstraint(
            _one_of("road_mix", ROAD_MIXES), name="ck_car_surveys_road_mix"
        ),
        CheckConstraint(
            _one_of("long_trips", LONG_TRIPS), name="ck_car_surveys_long_trips"
        ),
        CheckConstraint(
            _one_of("long_trip_distance", LONG_TRIP_DISTANCES),
            name="ck_car_surveys_long_trip_distance",
        ),
        CheckConstraint(
            "last_service_mileage >= 0", name="ck_car_surveys_last_service_mileage"
        ),
        CheckConstraint(
            _one_of("service_history", SERVICE_HISTORIES),
            name="ck_car_surveys_service_history",
        ),
        CheckConstraint(
            _one_of("reminder_km", REMINDER_KM), name="ck_car_surveys_reminder_km"
        ),
        CheckConstraint(
            _one_of("reminder_months", REMINDER_MONTHS),
            name="ck_car_surveys_reminder_months",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), unique=True
    )

    # Поездки
    purposes: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    purposes_other: Mapped[str | None] = mapped_column(String(200))
    trip_frequency: Mapped[str | None] = mapped_column(String(20))
    # Оценка пробега за месяц, отдельно от cars.mileage. Для расчётов — только она;
    # mileage_amount и mileage_period нужны, чтобы показать ввод таким, каким он был
    monthly_km: Mapped[int | None] = mapped_column(Integer)
    mileage_amount: Mapped[int | None] = mapped_column(Integer)
    mileage_period: Mapped[str | None] = mapped_column(String(10))
    trip_length: Mapped[str | None] = mapped_column(String(20))
    road_mix: Mapped[str | None] = mapped_column(String(20))
    long_trips: Mapped[str | None] = mapped_column(String(20))
    long_trip_distance: Mapped[str | None] = mapped_column(String(20))

    # Условия эксплуатации
    conditions: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    conditions_other: Mapped[str | None] = mapped_column(String(200))

    # Обслуживание
    service_triggers: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    service_triggers_other: Mapped[str | None] = mapped_column(String(200))
    service_places: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    service_places_other: Mapped[str | None] = mapped_column(String(200))
    last_service_date: Mapped[date | None] = mapped_column(Date)
    last_service_mileage: Mapped[int | None] = mapped_column(Integer)
    # Коды из CATEGORIES, кроме other: вместо него — текст last_service_works_other
    last_service_works: Mapped[list[str] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    last_service_works_other: Mapped[str | None] = mapped_column(String(200))
    # Дата и пробег последнего ТО указаны примерно
    last_service_approximate: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    service_history: Mapped[str | None] = mapped_column(String(20))
    self_checks: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    self_checks_other: Mapped[str | None] = mapped_column(String(200))

    # Предпочтения
    reminder_km: Mapped[int | None] = mapped_column(Integer)
    reminder_months: Mapped[int | None] = mapped_column(Integer)
    concerns: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    concerns_other: Mapped[str | None] = mapped_column(String(200))

    # Показывает, насколько устарела оценка monthly_km
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    car: Mapped[Car] = relationship(back_populates="survey")
