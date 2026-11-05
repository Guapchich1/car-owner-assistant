from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import CATEGORIES, Car, CarSurvey, ServiceRecord
from app.routers.pages import WORK_TYPES
from app.services.survey import (
    LAST_SERVICE_WORKS,
    SURVEY_BLOCKS,
    blank_values,
    car_mismatch_warnings,
    monthly_km,
    survey_warnings,
    validate_survey_form,
    values_from_survey,
)


@pytest.fixture
def url(car):
    return f"/cars/{car.id}/survey"


def surveys_in(session):
    session.expire_all()
    return session.scalars(select(CarSurvey)).all()


def validate(car, today=date(2026, 11, 5), **fields):
    """Проверка анкеты, в которой заполнены только указанные поля."""
    return validate_survey_form({**blank_values(), **fields}, car, today)


# --- Форма и её содержание ---


def test_form_has_blocks_and_intro(client, url):
    """Перед вопросами — назначение, объём и возможность изменить ответы позже."""
    html = client.get(f"{url}/edit").text

    for text_part in (
        "Поездки",
        "Условия эксплуатации",
        "Обслуживание",
        "Предпочтения",
        "Не больше 15 вопросов, у большинства есть варианты ответа",
        "можно пропустить",
        "изменить позже",
        "Что беспокоит в машине",
    ):
        assert text_part in html


def test_form_has_no_removed_items(client, url):
    """Вопроса о нужных функциях и обещания «3–5 минут» нет."""
    html = client.get(f"{url}/edit").text

    assert "функци" not in html.lower()
    assert "3–5 минут" not in html


def test_at_most_15_questions(client, url):
    """С показанным условным вопросом вопросов не больше 15."""
    data = {"long_trips": "rare"}
    client.post(f"{url}/edit", data=data)

    assert client.get(f"{url}/edit").text.count("data-question") == 15


def test_most_questions_have_answer_options():
    """У большинства вопросов есть варианты ответа."""
    questions = [q for block in SURVEY_BLOCKS for q in block["questions"]]
    with_options = [q for q in questions if "options" in q or q["kind"] == "reminder"]

    assert len(with_options) > len(questions) / 2


def test_form_does_not_ask_car_data(client, url):
    """Характеристики и пробег машины из карточки анкета не спрашивает."""
    html = client.get(f"{url}/edit").text

    for field in ('name="year"', 'name="engine"', 'name="gearbox"', 'name="mileage"'):
        assert field not in html


def test_work_labels_exist_for_last_service():
    """Работы последнего ТО — категории журнала, кроме «Прочих»; у всех есть названия."""
    assert set(LAST_SERVICE_WORKS) == set(CATEGORIES) - {"other"}
    assert set(LAST_SERVICE_WORKS) <= set(WORK_TYPES)


# --- Сохранение и восстановление ---


def test_answers_saved_and_restored(client, session, url):
    """Ответы сохраняются у машины и восстанавливаются в форме."""
    response = client.post(
        f"{url}/edit",
        data={
            "purposes": ["work", "dacha"],
            "purposes_other": "Рыбалка",
            "trip_frequency": "daily",
            "conditions": ["dust", "frost"],
            "concerns": ["noises"],
            "concerns_other": "Стук справа",
            "service_history": "partial",
            "reminder_km": "1000",
            "reminder_months": "2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"{url}?saved=1"
    [survey] = surveys_in(session)
    assert survey.purposes == ["work", "dacha"]
    assert survey.purposes_other == "Рыбалка"
    assert survey.trip_frequency == "daily"
    assert survey.conditions == ["dust", "frost"]
    assert survey.concerns == ["noises"]
    assert survey.concerns_other == "Стук справа"
    assert (survey.reminder_km, survey.reminder_months) == (1000, 2)

    html = client.get(f"{url}/edit").text
    assert 'name="purposes" value="work" checked' in html
    assert 'value="Рыбалка"' in html
    assert '<option value="daily" selected>' in html
    assert '<option value="1000" selected>' in html
    assert '<option value="2" selected>' in html


def test_second_save_updates_same_row(client, session, url):
    """Повторная отправка меняет ту же строку; снятый ответ очищается."""
    client.post(f"{url}/edit", data={"trip_frequency": "daily", "conditions": ["dust"]})
    client.post(f"{url}/edit", data={"trip_frequency": "rare"})

    [survey] = surveys_in(session)
    assert survey.trip_frequency == "rare"
    assert survey.conditions is None


def test_empty_form_is_saved_with_sql_nulls(client, session, url):
    """Все вопросы можно пропустить: в базе настоящий NULL, а не JSON null."""
    assert client.post(f"{url}/edit", data={}).status_code == 200

    [survey] = surveys_in(session)
    assert survey.purposes is None
    assert survey.monthly_km is None
    value = session.execute(text("SELECT purposes FROM car_surveys")).scalar()
    assert value is None


def test_survey_belongs_to_its_car(client, session, car):
    """Анкета одной машины не показывается у другой."""
    other = Car(make="Kia", model="Rio", year=2018, mileage=50000)
    session.add(other)
    session.commit()

    client.post(f"/cars/{car.id}/survey/edit", data={"purposes_other": "Рыбалка"})

    assert "Рыбалка" not in client.get(f"/cars/{other.id}/survey/edit").text
    response = client.get(f"/cars/{other.id}/survey", follow_redirects=False)
    assert response.status_code == 303
    [survey] = surveys_in(session)
    assert survey.car_id == car.id


def test_only_one_survey_per_car(session, car):
    """Вторую анкету той же машине сохранить нельзя."""
    session.add_all([CarSurvey(car_id=car.id), CarSurvey(car_id=car.id)])
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_car_deletes_survey(session, car):
    """При удалении машины удаляется и её анкета."""
    session.add(CarSurvey(car=car))
    session.commit()

    session.delete(car)
    session.commit()

    assert session.scalars(select(CarSurvey)).all() == []


@pytest.mark.parametrize(
    "field, value",
    [
        ("trip_frequency", "often"),
        ("trip_length", "far"),
        ("road_mix", "forest"),
        ("long_trips", "always"),
        ("long_trip_distance", "moon"),
        ("mileage_period", "day"),
        ("service_history", "lost"),
        ("reminder_km", 700),
        ("reminder_months", 12),
    ],
)
def test_database_rejects_unknown_values(session, car, field, value):
    """Значение не из списка база не пропускает."""
    session.add(CarSurvey(car=car, **{field: value}))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize(
    "fields",
    [{"monthly_km": 30001}, {"monthly_km": -1}, {"last_service_mileage": -1}],
)
def test_database_rejects_wrong_numbers(session, car, fields):
    session.add(CarSurvey(car=car, **fields))
    with pytest.raises(IntegrityError):
        session.commit()


# --- Пробег ---


@pytest.mark.parametrize(
    "amount, period, expected",
    [
        (1000, "month", 1000),
        (1000, "week", 4333),
        (1000, "year", 83),
        (12000, "year", 1000),
        (0, "week", 0),
    ],
)
def test_monthly_km(amount, period, expected):
    assert monthly_km(amount, period) == expected


@pytest.mark.parametrize(
    "amount, period, ok",
    [
        ("0", "month", True),
        ("30000", "month", True),
        ("30001", "month", False),
        ("360000", "year", True),
        ("400000", "year", False),
        ("6923", "week", True),
        ("7000", "week", False),
        ("много", "month", False),
        ("-5", "month", False),
        ("1.5", "month", False),
        ("100", "day", False),
    ],
)
def test_mileage_range(car, amount, period, ok):
    """Лимит 0–30 000 км/мес проверяется после пересчёта."""
    _, errors = validate(car, mileage_amount=amount, mileage_period=period)

    assert ("mileage" not in errors) is ok


def test_entered_mileage_restored_as_entered(client, session, car, url):
    """Форма показывает 1000 км в год, для расчётов — 83 км в месяц."""
    client.post(
        f"{url}/edit", data={"mileage_amount": "1000", "mileage_period": "year"}
    )

    [survey] = surveys_in(session)
    assert survey.monthly_km == 83
    assert (survey.mileage_amount, survey.mileage_period) == (1000, "year")
    html = client.get(f"{url}/edit").text
    assert 'value="1000"' in html
    assert '<option value="year" selected>' in html


def test_empty_mileage_clears_all_fields(client, session, url):
    """Пробег стёрли — все три поля очищаются вместе."""
    filled = {"mileage_amount": "1000", "mileage_period": "week"}
    assert client.post(f"{url}/edit", data=filled).status_code == 200
    assert surveys_in(session)[0].monthly_km == 4333

    client.post(f"{url}/edit", data={"mileage_amount": "", "mileage_period": "week"})

    [survey] = surveys_in(session)
    assert survey.monthly_km is None
    assert survey.mileage_amount is None
    assert survey.mileage_period is None


def test_survey_does_not_change_car_mileage(client, session, car, url):
    """Оценка пробега хранится отдельно от пробега машины."""
    client.post(
        f"{url}/edit", data={"mileage_amount": "1000", "mileage_period": "month"}
    )

    assert surveys_in(session)[0].monthly_km == 1000
    assert session.get(Car, car.id).mileage == 84500


def test_mileage_error_shown_with_entered_data(client, session, url):
    """Ошибка показана текстом, введённое не теряется, в базу ничего не попало."""
    response = client.post(
        f"{url}/edit",
        data={
            "mileage_amount": "30001",
            "mileage_period": "month",
            "purposes": ["work"],
        },
    )

    assert response.status_code == 422
    assert "Пробег в пересчёте на месяц — от 0 до 30 000 км" in response.text
    assert 'value="30001"' in response.text
    assert 'name="purposes" value="work" checked' in response.text
    assert surveys_in(session) == []


# --- Условный вопрос о дальних поездках ---


@pytest.mark.parametrize(
    "answer, shown",
    [
        ("never", False),
        ("unknown", False),
        ("", False),
        ("rare", True),
        ("several", True),
        ("monthly", True),
    ],
)
def test_long_trip_block_depends_on_answer(client, url, answer, shown):
    """Блок для HTMX появляется только при дальних поездках."""
    html = client.get(f"{url}/long-trip", params={"long_trips": answer}).text

    assert ('name="long_trip_distance"' in html) is shown


def test_long_trip_block_keeps_selected_answer(client, url):
    params = {"long_trips": "rare", "long_trip_distance": "over_1000"}
    html = client.get(f"{url}/long-trip", params=params).text

    assert '<option value="over_1000" selected>' in html


def test_form_connects_long_trip_block(client, url):
    html = client.get(f"{url}/edit").text

    assert f'hx-get="{url}/long-trip"' in html
    assert 'name="long_trip_distance"' not in html


def test_long_trip_answer_restored(client, session, url):
    """При повторном открытии условный вопрос виден вместе с ответом."""
    client.post(
        f"{url}/edit", data={"long_trips": "rare", "long_trip_distance": "over_1000"}
    )

    html = client.get(f"{url}/edit").text
    assert '<option value="over_1000" selected>' in html


def test_long_trip_answer_cleared_without_trips(client, session, url):
    """Дальних поездок больше нет — прежний ответ не остаётся в базе."""
    client.post(
        f"{url}/edit", data={"long_trips": "rare", "long_trip_distance": "over_1000"}
    )
    client.post(
        f"{url}/edit", data={"long_trips": "never", "long_trip_distance": "over_1000"}
    )

    [survey] = surveys_in(session)
    assert survey.long_trips == "never"
    assert survey.long_trip_distance is None


# --- Последнее ТО ---


def test_last_service_saved_with_mark_and_no_journal_record(client, session, car, url):
    """Сведения о ТО — только в анкете, с отметкой; запись в журнале не создаётся."""
    client.post(
        f"{url}/edit",
        data={
            "last_service_date": "2026-04-18",
            "last_service_mileage": "80000",
            "last_service_works": ["oil", "filters"],
            "last_service_works_other": "Ремень",
            "last_service_approximate": "on",
        },
    )

    [survey] = surveys_in(session)
    assert survey.last_service_date == date(2026, 4, 18)
    assert survey.last_service_mileage == 80000
    assert survey.last_service_works == ["oil", "filters"]
    assert survey.last_service_works_other == "Ремень"
    assert survey.last_service_approximate is True
    assert session.scalars(select(ServiceRecord)).all() == []


def test_exact_last_service(client, session, url):
    """Галочку сняли — данные точные, и при открытии она остаётся снятой."""
    client.post(f"{url}/edit", data={"last_service_mileage": "80000"})

    [survey] = surveys_in(session)
    assert survey.last_service_approximate is False
    assert values_from_survey(survey)["last_service_approximate"] == ""


def test_new_form_marks_last_service_approximate(client, url):
    """В новой анкете галочка включена: обычно отвечают по памяти."""
    html = client.get(f"{url}/edit").text

    assert 'value="on" checked>Дата и пробег примерные' in html


def test_no_mark_without_date_and_mileage(client, session, url):
    """Нет ни даты, ни пробега — отмечать нечего, но в форме галочка включена."""
    client.post(f"{url}/edit", data={"last_service_approximate": "on"})

    [survey] = surveys_in(session)
    assert survey.last_service_approximate is False
    assert values_from_survey(survey)["last_service_approximate"] == "on"


@pytest.mark.parametrize(
    "value, ok",
    [
        ("2026-11-05", True),
        ("2026-11-06", False),
        ("2020-01-01", True),
        ("2019-12-31", False),
        ("вчера", False),
    ],
)
def test_last_service_date_range(car, value, ok):
    """Дата не в будущем и не раньше года выпуска; машина 2020 года."""
    _, errors = validate(car, last_service_date=value)

    assert ("last_service_date" not in errors) is ok


@pytest.mark.parametrize(
    "value, ok",
    [("0", True), ("84500", True), ("84501", False), ("много", False)],
)
def test_last_service_mileage_range(car, value, ok):
    """Пробег не больше текущего: у машины 84 500 км."""
    _, errors = validate(car, last_service_mileage=value)

    assert ("last_service_mileage" not in errors) is ok


# --- Проверка вариантов ответа ---


@pytest.mark.parametrize(
    "field",
    ["trip_frequency", "road_mix", "long_trips", "reminder_km", "reminder_months"],
)
def test_unknown_single_choice_rejected(car, field):
    _, errors = validate(car, **{field: "чужое"})

    assert errors[field] == "Выберите вариант из списка"


def test_unknown_multi_choice_rejected(car):
    _, errors = validate(car, purposes=["work", "чужое"])

    assert errors["purposes"] == "Выберите варианты из списка"


def test_unknown_work_rejected(car):
    """Категория other заменена полем «Другое»."""
    _, errors = validate(car, last_service_works=["other"])

    assert errors["last_service_works"] == "Выберите варианты из списка"


@pytest.mark.parametrize(
    "fields",
    [
        {"conditions": ["none", "dust"]},
        {"conditions": ["unknown", "dust"]},
        {"conditions": ["none", "unknown"]},
        {"conditions": ["none"], "conditions_other": "Соль на дорогах"},
    ],
)
def test_exclusive_answers_cannot_combine(car, fields):
    """«Ничего из этого» и «Не знаю» не сочетаются с другими ответами."""
    _, errors = validate(car, **fields)

    assert "нельзя выбрать вместе с другими ответами" in errors["conditions"]


@pytest.mark.parametrize("code", ["none", "unknown"])
def test_exclusive_answer_alone_is_kept(car, code):
    """Для 4.1 «ничего» и «не знаю» отличаются от пропущенного вопроса."""
    data, errors = validate(car, conditions=[code])

    assert not errors
    assert data["conditions"] == [code]


def test_other_text_length(car):
    data, errors = validate(car, concerns_other="а" * 200)
    assert not errors
    assert data["concerns_other"] == "а" * 200

    _, errors = validate(car, concerns_other="а" * 201)
    assert errors["concerns_other"] == "Не больше 200 символов"


def test_user_text_is_escaped(client, url):
    """Текст пользователя показывается как текст, а не как разметка."""
    client.post(f"{url}/edit", data={"concerns_other": "<script>x</script>"})

    for page in (url, f"{url}/edit"):
        html = client.get(page).text
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x&lt;/script&gt;" in html


# --- Переходы ---


def test_missing_car_returns_404(client):
    for path in ("", "/edit", "/long-trip"):
        assert client.get(f"/cars/999/survey{path}").status_code == 404
    assert client.post("/cars/999/survey/edit", data={}).status_code == 404
    assert client.post("/cars/999/survey/clear").status_code == 404


def test_skip_leads_home_and_saves_nothing(client, session, url):
    """«Пропустить» — просто ссылка на главную: строка не создаётся."""
    html = client.get(f"{url}/edit").text

    assert '<a class="secondary" href="/">Пропустить</a>' in html
    assert surveys_in(session) == []


def test_skip_hidden_after_save(client, url):
    client.post(f"{url}/edit", data={})

    html = client.get(f"{url}/edit").text
    assert "Пропустить" not in html
    assert f'<a class="secondary" href="{url}">Отмена</a>' in html


def test_card_link_depends_on_survey(client, url):
    """Из карточки машины можно вернуться к анкете в любой момент."""
    html = client.get("/").text
    assert f'href="{url}"' in html
    assert "Заполнить анкету" in html

    client.post(f"{url}/edit", data={})

    html = client.get("/").text
    assert "Анкета об использовании" in html
    assert "Заполнить анкету" not in html


# --- Просмотр ---


def test_view_redirects_to_form_without_survey(client, url):
    response = client.get(url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"{url}/edit"


def test_view_shows_titles_not_codes(client, url):
    """На просмотре — названия ответов; пропущенные вопросы помечены."""
    client.post(
        f"{url}/edit",
        data={
            "purposes": ["taxi_delivery"],
            "purposes_other": "Рыбалка",
            "mileage_amount": "1000",
            "mileage_period": "year",
            "long_trips": "rare",
            "long_trip_distance": "over_1000",
            "last_service_date": "2026-04-18",
            "last_service_mileage": "80000",
            "last_service_works": ["oil"],
            "last_service_approximate": "on",
            "reminder_km": "1000",
            "reminder_months": "2",
        },
    )

    html = client.get(url).text
    for answer in (
        "Такси или доставка, Другое: Рыбалка",
        "1 000 км в год (примерно 83 км в месяц)",
        "1–2 раза в год",
        "Больше 1000 км",
        "18.04.2026",
        "80 000 км",
        "Масло и фильтр",
        "дата и пробег примерные",
        "За 1000 км, За 2 месяца",
        "Не указано",
    ):
        assert answer in html
    assert "taxi_delivery" not in html
    assert "<input" not in html.split('class="workspace"', 1)[1]


def test_view_hides_distance_without_long_trips(client, url):
    client.post(f"{url}/edit", data={"long_trips": "never"})

    assert "Какой обычно длины дальняя поездка" not in client.get(url).text


def test_view_says_saved_only_after_save(client, url):
    client.post(f"{url}/edit", data={})

    assert "Ответы сохранены" in client.get(f"{url}?saved=1").text
    assert "Ответы сохранены" not in client.get(url).text


# --- Нестыковки ---


@pytest.mark.parametrize(
    "fields, part",
    [
        ({"purposes": ["taxi_delivery"], "monthly_km": 499}, "такси или доставка"),
        ({"trip_frequency": "daily", "monthly_km": 0}, "нулевой пробег"),
        ({"long_trips": "monthly", "monthly_km": 399}, "от 400 км"),
    ],
)
def test_warning_fires_on_mismatch(fields, part):
    [warning] = survey_warnings(CarSurvey(**fields))

    assert part in warning


@pytest.mark.parametrize(
    "fields",
    [
        {"purposes": ["taxi_delivery"], "monthly_km": 500},
        {"purposes": ["taxi_delivery"]},
        {"purposes": ["work"], "monthly_km": 100},
        {"trip_frequency": "daily", "monthly_km": 1},
        {"trip_frequency": "rare", "monthly_km": 0},
        {"long_trips": "monthly", "monthly_km": 400},
        {"long_trips": "rare", "monthly_km": 100},
        {},
    ],
)
def test_no_warning_without_mismatch(fields):
    assert survey_warnings(CarSurvey(**fields)) == []


@pytest.mark.parametrize(
    "car_fields, part, field",
    [
        (
            {"mileage": "100"},
            "больше текущего пробега машины (100 км)",
            "last_service_mileage",
        ),
        (
            {"year": "2022"},
            "раньше года выпуска машины (2022)",
            "last_service_date",
        ),
    ],
)
def test_warning_after_car_edit(client, session, car, url, car_fields, part, field):
    """Исправили машину так, что анкета не сходится: предупреждение сразу после
    сохранения, а не на странице анкеты и не на обычной главной."""
    session.add(
        CarSurvey(
            car=car, last_service_mileage=80000, last_service_date=date(2021, 3, 1)
        )
    )
    session.commit()

    car_form = {
        "brand": "Lada",
        "model": "Granta",
        "generation": "I Рестайлинг 2018-2026",
        "engine": "1.6 (90 л.с.)",
        "year": "2020",
        "mileage": "84500",
        "gearbox": "automatic",
        **car_fields,
    }
    response = client.post(f"/cars/{car.id}/edit", data=car_form)

    assert "Изменения сохранены" in response.text
    assert part in response.text
    # Ссылка ведёт к полю с расхождением, и такое поле в форме анкеты есть
    assert f'<a href="{url}/edit#{field}">Исправить анкету</a>' in response.text
    assert f'id="{field}"' in client.get(f"{url}/edit").text
    alert = response.text.split('<div class="bottom-alerts">', 1)[1]
    assert part in alert
    assert 'aria-label="Закрыть"' in alert
    assert part not in client.get(url).text
    assert part not in client.get("/").text


def field_tag(html, field):
    """Тег поля формы анкеты."""
    return html.split(f'<input id="{field}"', 1)[1].split(">", 1)[0]


def test_mismatched_field_stays_red_until_fixed(client, session, car, url):
    """Поле красное при каждом открытии формы, пока расхождение не исправят."""
    session.add(CarSurvey(car=car, last_service_mileage=90000))
    session.commit()

    # Обычное открытие формы, без перехода по ссылке из предупреждения
    html = client.get(f"{url}/edit").text
    assert "field-mismatch" in field_tag(html, "last_service_mileage")
    assert "field-mismatch" not in field_tag(html, "last_service_date")
    assert "Больше текущего пробега машины (84 500 км)" in html
    assert "Раньше года выпуска машины" not in html
    html = client.get(f"{url}/edit").text
    assert "field-mismatch" in field_tag(html, "last_service_mileage")

    response = client.post(f"{url}/edit", data={"last_service_mileage": "80000"})

    assert response.status_code == 200
    html = client.get(f"{url}/edit").text
    assert "field-mismatch" not in html
    assert "Больше текущего пробега машины" not in html


def test_date_mismatch_hint_under_field(client, session, car, url):
    """Дата раньше года выпуска: поле красное, под ним подпись с годом."""
    session.add(CarSurvey(car=car, last_service_date=date(2019, 5, 1)))
    session.commit()

    html = client.get(f"{url}/edit").text

    assert "field-mismatch" in field_tag(html, "last_service_date")
    assert "Раньше года выпуска машины (2020)" in html


def test_no_car_mismatch_warnings_when_consistent(car):
    """Пробег и год сходятся с машиной: предупреждений нет."""
    survey = CarSurvey(last_service_mileage=84500, last_service_date=date(2020, 1, 1))

    assert car_mismatch_warnings(survey, car) == []


def test_warning_does_not_block_saving(client, session, url):
    """С нестыковкой ответы сохраняются как есть, предупреждение — на просмотре."""
    response = client.post(
        f"{url}/edit",
        data={
            "purposes": ["taxi_delivery"],
            "mileage_amount": "100",
            "mileage_period": "month",
        },
    )

    assert response.status_code == 200
    assert "Проверьте, всё ли верно" in response.text
    [survey] = surveys_in(session)
    assert survey.monthly_km == 100


# --- Очистка ---


def test_clear_button_asks_confirmation(client, url):
    """Кнопка работает только через HTMX, а значит, с подтверждением."""
    client.post(f"{url}/edit", data={})

    html = client.get(url).text
    assert f'hx-post="{url}/clear"' in html
    assert 'hx-confirm="Удалить все ответы анкеты? Отменить это будет нельзя."' in html


def test_clear_deletes_only_survey(client, session, car, url):
    """Анкета удаляется, машина и записи журнала остаются."""
    session.add(
        ServiceRecord(
            car_id=car.id,
            date=date(2026, 4, 18),
            mileage=71000,
            category="oil",
            title="Замена масла",
            amount=8400,
        )
    )
    session.commit()
    client.post(f"{url}/edit", data={"purposes": ["work"]})

    response = client.post(f"{url}/clear", follow_redirects=False)

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"{url}/edit"
    assert surveys_in(session) == []
    assert session.get(Car, car.id) is not None
    assert len(session.scalars(select(ServiceRecord)).all()) == 1
    assert "Пропустить" in client.get(f"{url}/edit").text


def test_clear_twice_is_not_an_error(client, url):
    assert client.post(f"{url}/clear").status_code == 204
    assert client.post(f"{url}/clear").status_code == 204
