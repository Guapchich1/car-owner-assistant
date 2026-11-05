"""Машина пользователя: списки формы по справочнику и проверка введённых данных."""

import re
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GEARBOXES, Car
from app.services import catalog

MAX_MILEAGE = 2_000_000

# В Москве круглый год UTC+3: летнего времени нет с 2014 года.
# ZoneInfo("Europe/Moscow") на Windows без пакета tzdata не работает.
MSK = timezone(timedelta(hours=3))

# Поля формы добавления машины
FORM_FIELDS = ("brand", "model", "generation", "engine", "year", "mileage", "gearbox")


def get_car(session: Session) -> Car | None:
    """Машина пользователя; пока она одна — первая в базе."""
    return session.scalars(select(Car).order_by(Car.id).limit(1)).first()


def to_msk(value: datetime) -> datetime:
    """Время из базы (UTC без часового пояса) → московское."""
    return value.replace(tzinfo=UTC).astimezone(MSK)


def _match(names: list[str], value: str) -> str:
    """Название из списка без учёта регистра; не нашли — пустая строка."""
    value = value.strip().casefold()
    return next((n for n in names if n.casefold() == value), "")


def form_options(
    brand: str = "", model: str = "", generation: str = "", engine: str = ""
) -> dict:
    """Списки формы по выбору пользователя: марка → модель → поколение → двигатель.

    Каждый список зависит от значения выше. Если значение не подходит
    (например, сменили марку, а модель от прежней), оно и все ниже сбрасываются.
    Выбранные значения возвращаются в написании справочника.
    """
    brands = catalog.list_brands()
    brand = _match(brands, brand)
    models = catalog.list_models(brand) if brand else []
    model = _match(models, model)
    generations = catalog.list_generations(brand, model) if model else []
    selected = _match([g["name"] for g in generations], generation)
    found = next((g for g in generations if g["name"] == selected), None)
    engines = catalog.list_engines(brand, model, selected) if found else []
    return {
        "brands": brands,
        "models": models,
        "generations": generations,
        "engines": engines,
        "brand": brand,
        "model": model,
        "generation": found,  # поколение целиком: название и годы выпуска
        "engine": _match(engines, engine),
    }


def _whole_number(value: str) -> int | None:
    """Целое неотрицательное число из строки; иначе None."""
    value = value.strip()
    return int(value) if re.fullmatch(r"\d{1,9}", value) else None


def validate_car_form(values: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Проверяет форму добавления машины.

    Возвращает данные для Car и ошибки по полям. Если ошибок нет,
    словарь ошибок пустой.
    """
    options = form_options(
        values.get("brand", ""),
        values.get("model", ""),
        values.get("generation", ""),
        values.get("engine", ""),
    )
    generation = options["generation"]
    errors = {}
    if not options["brand"]:
        errors["brand"] = "Выберите марку из списка"
    elif not options["model"]:
        errors["model"] = "Выберите модель из списка"
    elif generation is None:
        errors["generation"] = "Выберите поколение из списка"
    elif not options["engine"]:
        errors["engine"] = "Выберите двигатель из списка"

    year = _whole_number(values.get("year", ""))
    if year is None:
        errors["year"] = "Укажите год выпуска числом"
    elif generation and not generation["year_from"] <= year <= generation["year_to"]:
        errors["year"] = (
            f"Это поколение выпускали с {generation['year_from']} "
            f"по {generation['year_to']} год"
        )

    mileage = _whole_number(values.get("mileage", ""))
    if mileage is None or mileage > MAX_MILEAGE:
        errors["mileage"] = "Пробег — целое число от 0 до 2 000 000 км"

    gearbox = values.get("gearbox", "")
    if gearbox not in GEARBOXES:
        errors["gearbox"] = "Выберите коробку передач из списка"

    if errors:
        return {}, errors
    car = {
        "make": options["brand"],
        "model": options["model"],
        "generation": generation["name"],
        "engine": options["engine"],
        "year": year,
        "mileage": mileage,
        "gearbox": gearbox,
    }
    return car, {}
