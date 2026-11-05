"""Анкета об использовании автомобиля: вопросы, проверка ответов и нестыковки.

Здесь ответы только собираются и проверяются. На план ТО они пока не влияют.
"""

from datetime import date, datetime

from app.models import (
    CATEGORIES,
    CONCERNS,
    CONDITIONS,
    LONG_TRIP_DISTANCES,
    LONG_TRIPS,
    MAX_MONTHLY_KM,
    MILEAGE_PERIODS,
    PURPOSES,
    REMINDER_KM,
    ROAD_MIXES,
    SELF_CHECKS,
    SERVICE_HISTORIES,
    SERVICE_PLACES,
    SERVICE_TRIGGERS,
    TRIP_FREQUENCIES,
    TRIP_LENGTHS,
    Car,
    CarSurvey,
)
from app.services.cars import MSK, _whole_number

MAX_OTHER_LENGTH = 200

# Варианты, которые исключают все остальные ответы на вопрос
EXCLUSIVE = ("none", "unknown")

# Ответы про дальние поездки, при которых показываем уточняющий вопрос
LONG_TRIP_ANSWERS = ("rare", "several", "monthly")

# Работы последнего ТО — категории журнала. Вместо категории other — поле «Другое»
LAST_SERVICE_WORKS = tuple(c for c in CATEGORIES if c != "other")

REMINDER_KM_OPTIONS = {str(km): f"За {km} км" for km in REMINDER_KM}
REMINDER_MONTH_OPTIONS = {
    "1": "За 1 месяц",
    "2": "За 2 месяца",
    "3": "За 3 месяца",
}

# Вопросы с выбором одного варианта и с выбором нескольких
SINGLE_CHOICE = {
    "trip_frequency": TRIP_FREQUENCIES,
    "trip_length": TRIP_LENGTHS,
    "road_mix": ROAD_MIXES,
    "long_trips": LONG_TRIPS,
    "service_history": SERVICE_HISTORIES,
}
MULTI_CHOICE = {
    "purposes": PURPOSES,
    "conditions": CONDITIONS,
    "service_triggers": SERVICE_TRIGGERS,
    "service_places": SERVICE_PLACES,
    "self_checks": SELF_CHECKS,
    "concerns": CONCERNS,
}

LONG_TRIP_DISTANCE_TITLE = "Какой обычно длины дальняя поездка"

# Блоки анкеты в порядке показа. Форма и страница просмотра строятся по этому списку.
# Условный вопрос о длине дальней поездки идёт сразу после long_trips.
SURVEY_BLOCKS = (
    {
        "title": "Поездки",
        "questions": (
            {
                "kind": "multi",
                "name": "purposes",
                "title": "Для чего используете машину",
                "options": PURPOSES,
            },
            {
                "kind": "single",
                "name": "trip_frequency",
                "title": "Как часто ездите",
                "options": TRIP_FREQUENCIES,
            },
            {"kind": "mileage", "name": "mileage", "title": "Примерный пробег"},
            {
                "kind": "single",
                "name": "trip_length",
                "title": "Обычная длина поездки",
                "options": TRIP_LENGTHS,
            },
            {
                "kind": "single",
                "name": "road_mix",
                "title": "Где обычно ездите",
                "options": ROAD_MIXES,
            },
            {
                "kind": "long_trips",
                "name": "long_trips",
                "title": "Как часто ездите дальше 200 км в одну сторону",
                "options": LONG_TRIPS,
            },
        ),
    },
    {
        "title": "Условия эксплуатации",
        "questions": (
            {
                "kind": "multi",
                "name": "conditions",
                "title": "Какие условия бывают",
                "options": CONDITIONS,
            },
        ),
    },
    {
        "title": "Обслуживание",
        "questions": (
            {
                "kind": "multi",
                "name": "service_triggers",
                "title": "Как понимаете, что пора на ТО",
                "options": SERVICE_TRIGGERS,
            },
            {
                "kind": "multi",
                "name": "service_places",
                "title": "Где обслуживаете машину",
                "options": SERVICE_PLACES,
            },
            {"kind": "last_service", "name": "last_service", "title": "Последнее ТО"},
            {
                "kind": "single",
                "name": "service_history",
                "title": "Есть ли история обслуживания",
                "options": SERVICE_HISTORIES,
            },
            {
                "kind": "multi",
                "name": "self_checks",
                "title": "Что проверяете сами",
                "options": SELF_CHECKS,
            },
        ),
    },
    {
        "title": "Предпочтения",
        "questions": (
            {
                "kind": "reminder",
                "name": "reminder",
                "title": "За сколько напомнить о ТО",
            },
            {
                "kind": "multi",
                "name": "concerns",
                "title": "Что беспокоит в машине",
                "options": CONCERNS,
            },
        ),
    },
)

# Текстовые поля формы: всё, что не список галочек
_TEXT_FIELDS = (
    *SINGLE_CHOICE,
    "long_trip_distance",
    "mileage_amount",
    "mileage_period",
    *(f"{name}_other" for name in MULTI_CHOICE),
    "last_service_date",
    "last_service_mileage",
    "last_service_works_other",
    "last_service_approximate",
    "reminder_km",
    "reminder_months",
)
_LIST_FIELDS = (*MULTI_CHOICE, "last_service_works")


def has_long_trips(long_trips: str | None) -> bool:
    """Нужен ли уточняющий вопрос о дальних поездках."""
    return long_trips in LONG_TRIP_ANSWERS


def blank_values() -> dict:
    """Значения новой анкеты. Галочка «примерные» включена: обычно отвечают по памяти."""
    values = {name: "" for name in _TEXT_FIELDS}
    values.update({name: [] for name in _LIST_FIELDS})
    values["mileage_period"] = "month"
    values["last_service_approximate"] = "on"
    return values


def values_from_form(form) -> dict:
    """Значения из отправленной формы; неотмеченной галочки в форме просто нет."""
    values = {name: form.get(name, "") for name in _TEXT_FIELDS}
    values.update({name: form.getlist(name) for name in _LIST_FIELDS})
    return values


def values_from_survey(survey: CarSurvey) -> dict:
    """Значения формы из сохранённой анкеты."""
    values = blank_values()
    for name in (*_TEXT_FIELDS, *_LIST_FIELDS):
        saved = getattr(survey, name)
        if saved is not None and name != "last_service_approximate":
            values[name] = saved if name in _LIST_FIELDS else str(saved)
    # Если о последнем ТО ещё ничего не указано, галочка остаётся включённой
    if survey.last_service_date or survey.last_service_mileage is not None:
        values["last_service_approximate"] = (
            "on" if survey.last_service_approximate else ""
        )
    return values


def monthly_km(amount: int, period: str) -> int:
    """Пробег за неделю, месяц или год → км в месяц, округлённые до целых."""
    if period == "week":
        return round(amount * 52 / 12)
    if period == "year":
        return round(amount / 12)
    return amount


def _validate_mileage(values: dict, data: dict, errors: dict) -> None:
    """Пробег: число и период; все три поля заполняются или очищаются вместе."""
    data.update(monthly_km=None, mileage_amount=None, mileage_period=None)
    raw = values["mileage_amount"].strip()
    if not raw:
        return
    message = "Пробег в пересчёте на месяц — от 0 до 30 000 км"
    amount = _whole_number(raw)
    period = values["mileage_period"]
    if amount is None:
        errors["mileage"] = "Укажите пробег целым числом"
    elif period not in MILEAGE_PERIODS:
        errors["mileage"] = "Выберите период из списка"
    elif monthly_km(amount, period) > MAX_MONTHLY_KM:
        errors["mileage"] = message
    else:
        data.update(
            monthly_km=monthly_km(amount, period),
            mileage_amount=amount,
            mileage_period=period,
        )


def _validate_multi(
    name: str, chosen: list[str], other: str, options, data: dict, errors: dict
) -> None:
    """Список галочек и поле «Другое» для одного вопроса."""
    data[name] = [code for code in options if code in chosen] or None
    other = other.strip()
    data[f"{name}_other"] = other or None
    if len(other) > MAX_OTHER_LENGTH:
        errors[f"{name}_other"] = f"Не больше {MAX_OTHER_LENGTH} символов"
    if any(code not in options for code in chosen):
        errors[name] = "Выберите варианты из списка"
        return
    for code in EXCLUSIVE:
        if code in chosen and (len(set(chosen)) > 1 or other):
            title = options[code]
            errors[name] = f"«{title}» нельзя выбрать вместе с другими ответами"
            return


def _validate_last_service(
    values: dict, car: Car, today: date, data: dict, errors: dict
) -> None:
    """Последнее ТО: дата, пробег, работы и отметка о неточности."""
    data["last_service_date"] = None
    raw_date = values["last_service_date"].strip()
    if raw_date:
        try:
            service_date = date.fromisoformat(raw_date)
        except ValueError:
            errors["last_service_date"] = "Укажите дату в формате ДД.ММ.ГГГГ"
        else:
            if service_date > today:
                errors["last_service_date"] = (
                    "Дата последнего ТО не может быть в будущем"
                )
            elif service_date.year < car.year:
                errors["last_service_date"] = (
                    f"Дата последнего ТО не может быть раньше {car.year} года выпуска"
                )
            else:
                data["last_service_date"] = service_date

    data["last_service_mileage"] = None
    raw_mileage = values["last_service_mileage"].strip()
    if raw_mileage:
        mileage = _whole_number(raw_mileage)
        if mileage is None:
            errors["last_service_mileage"] = "Укажите пробег целым числом"
        elif mileage > car.mileage:
            errors["last_service_mileage"] = (
                f"Пробег последнего ТО не может быть больше текущего: {car.mileage} км"
            )
        else:
            data["last_service_mileage"] = mileage

    _validate_multi(
        "last_service_works",
        values["last_service_works"],
        values["last_service_works_other"],
        dict.fromkeys(LAST_SERVICE_WORKS),
        data,
        errors,
    )
    has_data = bool(raw_date or raw_mileage)
    data["last_service_approximate"] = has_data and bool(
        values["last_service_approximate"]
    )


def validate_survey_form(
    values: dict, car: Car, today: date | None = None
) -> tuple[dict, dict[str, str]]:
    """Проверяет анкету. Все вопросы необязательные.

    Возвращает данные для CarSurvey и ошибки по полям. Пропущенный вопрос — None.
    Если ошибок нет, словарь ошибок пустой.
    """
    today = today or datetime.now(MSK).date()
    data, errors = {}, {}

    for name, options in SINGLE_CHOICE.items():
        value = values[name]
        data[name] = value or None
        if value and value not in options:
            errors[name] = "Выберите вариант из списка"

    # Уточняющий вопрос имеет смысл только при дальних поездках:
    # если их больше нет, прежний ответ очищаем
    data["long_trip_distance"] = None
    if has_long_trips(values["long_trips"]):
        distance = values["long_trip_distance"]
        data["long_trip_distance"] = distance or None
        if distance and distance not in LONG_TRIP_DISTANCES:
            errors["long_trip_distance"] = "Выберите вариант из списка"

    _validate_mileage(values, data, errors)

    for name, options in MULTI_CHOICE.items():
        _validate_multi(
            name, values[name], values[f"{name}_other"], options, data, errors
        )

    _validate_last_service(values, car, today, data, errors)

    for name, options in (
        ("reminder_km", REMINDER_KM_OPTIONS),
        ("reminder_months", REMINDER_MONTH_OPTIONS),
    ):
        value = values[name]
        data[name] = int(value) if value in options else None
        if value and value not in options:
            errors[name] = "Выберите вариант из списка"

    if errors:
        return {}, errors
    return data, {}


def car_mismatch_warnings(survey: CarSurvey, car: Car) -> list[dict[str, str]]:
    """Расхождения последнего ТО с машиной: её данные могли исправить после анкеты.

    Для каждого — текст, поле формы анкеты, к которому ведёт ссылка,
    и подпись под этим полем.
    """
    warnings = []
    if (
        survey.last_service_mileage is not None
        and survey.last_service_mileage > car.mileage
    ):
        warnings.append(
            {
                "text": (
                    f"В анкете пробег последнего ТО ({survey.last_service_mileage} км) "
                    f"больше текущего пробега машины ({car.mileage} км). "
                    "Проверьте, всё ли верно."
                ),
                "field": "last_service_mileage",
                "hint": (
                    f"Больше текущего пробега машины ({car.mileage:,} км)".replace(
                        ",", " "
                    )
                ),
            }
        )
    if survey.last_service_date and survey.last_service_date.year < car.year:
        warnings.append(
            {
                "text": (
                    f"В анкете дата последнего ТО ({survey.last_service_date.year} год) "
                    f"раньше года выпуска машины ({car.year}). "
                    "Проверьте, всё ли верно."
                ),
                "field": "last_service_date",
                "hint": f"Раньше года выпуска машины ({car.year})",
            }
        )
    return warnings


def survey_warnings(survey: CarSurvey) -> list[str]:
    """Нестыковки в ответах. Только подсказка: сохранению и расчётам они не мешают."""
    km = survey.monthly_km
    if km is None:
        return []
    warnings = []
    if "taxi_delivery" in (survey.purposes or []) and km < 500:
        warnings.append(
            "Указаны такси или доставка, но пробег меньше 500 км в месяц. "
            "Проверьте, всё ли верно."
        )
    if survey.trip_frequency == "daily" and km == 0:
        warnings.append(
            "Вы ездите почти каждый день, но указан нулевой пробег. "
            "Проверьте, всё ли верно."
        )
    if survey.long_trips == "monthly" and km < 400:
        warnings.append(
            "Дальние поездки раз в месяц и чаще — это от 400 км в месяц, "
            "а указанный пробег меньше. Проверьте, всё ли верно."
        )
    return warnings
