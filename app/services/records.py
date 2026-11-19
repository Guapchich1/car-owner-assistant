"""Журнал работ: проверка формы записи, пробег машины и сводка расходов."""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CATEGORIES, Car, ServiceRecord, User, msk_today
from app.services.cars import MAX_MILEAGE, _whole_number, format_km

MAX_AMOUNT = 100_000_000
MAX_TITLE = 200
MAX_SERVICE = 200
MAX_NOTE = 2000

# Поля формы записи: добавление и изменение
FORM_FIELDS = ("date", "mileage", "category", "title", "amount", "service", "note")


def ru_date(value: date) -> str:
    """2026-10-05 → 05.10.2026."""
    return value.strftime("%d.%m.%Y")


def car_records(car: Car | None) -> list[ServiceRecord]:
    """Записи машины от новых к старым; нет машины — пустой список."""
    if car is None:
        return []
    return sorted(car.records, key=lambda r: (r.date, r.mileage, r.id), reverse=True)


def user_record(session: Session, user: User, record_id: int) -> ServiceRecord | None:
    """Запись журнала, если она принадлежит машине пользователя; иначе None."""
    query = (
        select(ServiceRecord)
        .join(Car)
        .where(ServiceRecord.id == record_id, Car.owner_id == user.id)
    )
    return session.scalars(query).first()


def top_record(car: Car) -> ServiceRecord | None:
    """Запись с наибольшим пробегом: пробег машины не может быть меньше."""
    return max(car.records, key=lambda r: (r.mileage, r.date), default=None)


def values_from_record(record: ServiceRecord) -> dict[str, str]:
    """Значения формы из сохранённой записи."""
    return {
        "date": record.date.isoformat(),
        "mileage": str(record.mileage),
        "category": record.category,
        "title": record.title,
        "amount": str(record.amount),
        "service": record.service or "",
        "note": record.note or "",
    }


def _parse_date(value: str) -> date | None:
    """Дата из поля формы (ГГГГ-ММ-ДД); иначе None."""
    value = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _validate_date(value: str, car: Car, today: date, errors: dict) -> date | None:
    """Дата работы: не в будущем и не раньше 1 января года перед выпуском."""
    work_date = _parse_date(value)
    earliest = date(car.year - 1, 1, 1)
    if work_date is None:
        errors["date"] = "Укажите дату работы"
    elif work_date > today:
        errors["date"] = "Дата работы не может быть в будущем"
    elif work_date < earliest:
        errors["date"] = (
            f"Машина {car.year} года выпуска: дата работы — не раньше {ru_date(earliest)}"
        )
    else:
        return work_date
    return None


def _mileage_conflict(
    work_date: date, mileage: int, car: Car, record: ServiceRecord | None
) -> str | None:
    """Противоречие пробега записи с машиной и другими записями; нет — None.

    Изменяемую запись не учитываем. Записи с той же датой между собой
    не сравниваем: порядок работ за один день неизвестен.
    """
    if work_date < car.mileage_date and mileage > car.mileage:
        return (
            f"На {ru_date(car.mileage_date)} у машины было {format_km(car.mileage)}, "
            "работа раньше — пробег не может быть больше"
        )
    others = [r for r in car.records if record is None or r.id != record.id]
    earlier = [r for r in others if r.date < work_date and r.mileage > mileage]
    if earlier:
        other = max(earlier, key=lambda r: (r.mileage, r.date))
        return (
            "Пробег меньше, чем в более ранней работе. "
            f"В записи от {ru_date(other.date)} пробег {format_km(other.mileage)}"
        )
    later = [r for r in others if r.date > work_date and r.mileage < mileage]
    if later:
        other = min(later, key=lambda r: (r.mileage, r.date))
        return (
            "Пробег больше, чем в более поздней работе. "
            f"В записи от {ru_date(other.date)} пробег {format_km(other.mileage)}"
        )
    return None


def _optional_text(
    values: dict, name: str, limit: int, message: str, errors: dict
) -> str | None:
    """Необязательный текст: пустой — None, длиннее limit — ошибка."""
    text = values.get(name, "").strip()
    if len(text) > limit:
        errors[name] = message
    return text or None


def validate_record_form(
    values: dict[str, str],
    car: Car,
    record: ServiceRecord | None = None,
    today: date | None = None,
) -> tuple[dict, dict[str, str]]:
    """Проверяет форму записи журнала.

    record — изменяемая запись (при добавлении None). Возвращает данные
    для ServiceRecord и ошибки по полям; если ошибок нет, словарь пустой.
    """
    today = today or msk_today()
    errors: dict[str, str] = {}

    work_date = _validate_date(values.get("date", ""), car, today, errors)

    mileage = _whole_number(values.get("mileage", ""))
    if mileage is None or mileage > MAX_MILEAGE:
        errors["mileage"] = "Пробег — целое число от 0 до 2 000 000 км"
    elif work_date is not None:
        conflict = _mileage_conflict(work_date, mileage, car, record)
        if conflict:
            errors["mileage"] = conflict

    category = values.get("category", "")
    if category not in CATEGORIES:
        errors["category"] = "Выберите категорию из списка"

    title = values.get("title", "").strip()
    if not title:
        errors["title"] = "Укажите название работы"
    elif len(title) > MAX_TITLE:
        errors["title"] = f"Название — не длиннее {MAX_TITLE} символов"

    amount = _whole_number(values.get("amount", ""))
    if amount is None or amount > MAX_AMOUNT:
        errors["amount"] = "Стоимость — целое число от 0 до 100 000 000 ₽"

    service = _optional_text(
        values,
        "service",
        MAX_SERVICE,
        f"Место — не длиннее {MAX_SERVICE} символов",
        errors,
    )
    note = _optional_text(
        values, "note", MAX_NOTE, f"Примечание — не длиннее {MAX_NOTE} символов", errors
    )

    if errors:
        return {}, errors
    data = {
        "date": work_date,
        "mileage": mileage,
        "category": category,
        "title": title,
        "amount": amount,
        "service": service,
        "note": note,
    }
    return data, {}


def offers_mileage_update(record: ServiceRecord, car: Car) -> bool:
    """Запись не раньше даты пробега машины и с бо́льшим пробегом:
    предлагаем обновить пробег машины."""
    return record.date >= car.mileage_date and record.mileage > car.mileage


def spent_total(records: list[ServiceRecord]) -> int:
    return sum(r.amount for r in records)


def spent_month(records: list[ServiceRecord], today: date | None = None) -> int:
    """Расходы за текущий месяц по Москве."""
    today = today or msk_today()
    return sum(
        r.amount
        for r in records
        if (r.date.year, r.date.month) == (today.year, today.month)
    )


def spent_by_category(records: list[ServiceRecord]) -> dict[str, int]:
    """Суммы по категориям, без пустых."""
    totals = {key: 0 for key in CATEGORIES}
    for r in records:
        totals[r.category] += r.amount
    return {key: amount for key, amount in totals.items() if amount}
