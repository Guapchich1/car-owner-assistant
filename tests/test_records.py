from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import ServiceRecord, msk_today
from app.routers.pages import format_money
from app.services.cars import format_km
from app.services.records import (
    offers_mileage_update,
    spent_by_category,
    spent_month,
    validate_record_form,
)
from tests.conftest import make_test_car

# Правильно заполненная форма записи. Машина из conftest: Lada Granta 2020, 84 500 км
FORM = {
    "date": "2026-03-10",
    "mileage": "80000",
    "category": "oil",
    "title": "Замена масла и фильтра",
    "amount": "6500",
    "service": "Сервис у дома",
    "note": "Масло 5W-40",
}


def form(**fields):
    return {**FORM, **fields}


@pytest.fixture
def car_jan(session, car):
    """Машина, пробег которой указан на 1 января 2026 года."""
    car.mileage_date = date(2026, 1, 1)
    session.commit()
    return car


def add_record(session, car, **fields) -> ServiceRecord:
    data = {
        "date": date(2025, 6, 1),
        "mileage": 70000,
        "category": "oil",
        "title": "Масло",
        "amount": 5000,
        **fields,
    }
    record = ServiceRecord(car=car, **data)
    session.add(record)
    session.commit()
    return record


def records_in(session):
    session.expire_all()
    return session.scalars(select(ServiceRecord)).all()


def new_url(car):
    return f"/cars/{car.id}/records/new"


def mileage_error(values, car, record=None, today=date(2026, 11, 19)):
    _, errors = validate_record_form(values, car, record, today=today)
    return errors.get("mileage")


# --- Путь нового пользователя ---


def test_empty_journal_invites_to_add(client, car):
    """Новый пользователь видит пустой журнал с кнопкой «Добавить запись»."""
    html = client.get("/history").text

    assert "В журнале пока нет записей" in html
    assert f'href="{new_url(car)}"' in html
    assert "Добавить запись" in html
    assert '<table class="table">' not in html


def test_journal_without_car_invites_to_add_car(client):
    html = client.get("/history").text

    assert "Добавьте машину" in html
    assert "records/new" not in html


def test_add_record_shows_everywhere(client, session, car):
    """Запись видна в журнале, расходах и на главной."""
    today = msk_today()
    response = client.post(
        new_url(car),
        data=form(date=today.isoformat(), mileage="84000", amount="6500"),
        follow_redirects=False,
    )

    [record] = records_in(session)
    assert response.status_code == 303
    assert response.headers["location"] == f"/history?saved={record.id}"
    assert record.source == "manual"
    assert (record.title, record.amount, record.service, record.note) == (
        "Замена масла и фильтра",
        6500,
        "Сервис у дома",
        "Масло 5W-40",
    )

    history = client.get(response.headers["location"]).text
    assert "Запись сохранена" in history
    assert "Замена масла и фильтра" in history
    assert "Добавлено вручную" in history
    assert "Показано записей: 1 из 1" in history

    expenses = client.get("/expenses").text
    assert f"За всё время: {format_money(6500)}" in expenses
    assert "Масло и фильтр" in expenses

    index = client.get("/").text
    assert format_money(6500) in index  # расходы за всё время и за месяц
    assert "Замена масла и фильтра" in index  # лента истории
    assert "Записей пока нет" not in index


def test_empty_fields_are_none(client, session, car):
    client.post(new_url(car), data=form(service="  ", note=""))

    [record] = records_in(session)
    assert (record.service, record.note) == (None, None)


def test_add_page_opens(client, car):
    html = client.get(new_url(car)).text

    assert "<h1>Добавить запись</h1>" in html
    assert 'name="csrf_token"' in html
    for code in ("oil", "filters", "brakes", "tires", "diagnostic", "other"):
        assert f'<option value="{code}"' in html


# --- Изменение и удаление ---


def test_edit_form_shows_values_and_source(client, session, car):
    record = add_record(session, car, service="VAG", note="Примечание")

    html = client.get(f"/records/{record.id}/edit").text

    assert "<h1>Изменить запись</h1>" in html
    assert 'value="2025-06-01"' in html
    assert 'value="70000"' in html
    assert '<option value="oil" selected>' in html
    assert ">Примечание</textarea>" in html
    assert "Добавлено вручную" in html
    assert f'hx-post="/records/{record.id}/delete"' in html
    assert "hx-confirm=" in html


def test_update_record(client, session, car):
    record = add_record(session, car)

    response = client.post(
        f"/records/{record.id}/edit",
        data=form(title="Колодки", category="brakes"),
        follow_redirects=False,
    )

    assert response.headers["location"] == f"/history?saved={record.id}"
    [saved] = records_in(session)
    assert (saved.title, saved.category, saved.mileage) == ("Колодки", "brakes", 80000)
    assert saved.source == "manual"


def test_update_keeps_source(client, session, car):
    record = add_record(session, car, source="apipoint")

    client.post(f"/records/{record.id}/edit", data=FORM)

    [saved] = records_in(session)
    assert saved.source == "apipoint"
    assert "Из истории apipoint" in client.get("/history").text


def test_update_error_keeps_entered_data(client, session, car):
    record = add_record(session, car)

    response = client.post(
        f"/records/{record.id}/edit", data=form(title="", amount="-5")
    )

    assert response.status_code == 422
    assert "Укажите название работы" in response.text
    assert 'value="-5"' in response.text
    [saved] = records_in(session)
    assert (saved.title, saved.amount) == ("Масло", 5000)


def test_delete_record(client, session, car):
    record = add_record(session, car)
    other = add_record(session, car, title="Другая")

    response = client.post(f"/records/{record.id}/delete", follow_redirects=False)

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/history"
    assert [r.id for r in records_in(session)] == [other.id]


@pytest.mark.parametrize(
    "method, path",
    [("get", "edit"), ("post", "edit"), ("post", "delete"), ("post", "update-mileage")],
)
def test_unknown_record_not_found(client, car, method, path):
    assert client.request(method, f"/records/999/{path}").status_code == 404


# --- Проверка полей ---


@pytest.mark.parametrize(
    "value, message",
    [
        ("", "Укажите дату работы"),
        ("10.03.2026", "Укажите дату работы"),
        ("2026-02-30", "Укажите дату работы"),
        ("2026-11-20", "Дата работы не может быть в будущем"),
        (
            "2018-12-31",
            "Машина 2020 года выпуска: дата работы — не раньше 01.01.2019",
        ),
    ],
)
def test_date_errors(car, value, message):
    _, errors = validate_record_form(form(date=value), car, today=date(2026, 11, 19))
    assert errors["date"] == message


@pytest.mark.parametrize("value", ["2019-01-01", "2026-11-19"])
def test_date_bounds_ok(car, value):
    """Ровно 1 января года перед выпуском и сегодня — можно."""
    _, errors = validate_record_form(
        form(date=value, mileage="0"), car, today=date(2026, 11, 19)
    )
    assert "date" not in errors


@pytest.mark.parametrize("value", ["", "-1", "1.5", "abc", "2000001"])
def test_mileage_range(car, value):
    assert (
        mileage_error(form(mileage=value), car)
        == "Пробег — целое число от 0 до 2 000 000 км"
    )


@pytest.mark.parametrize("value", ["0", "2000000"])
def test_mileage_range_ok(session, car, value):
    car.mileage = 2_000_000
    assert mileage_error(form(mileage=value), car) is None


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("category", "", "Выберите категорию из списка"),
        ("category", "tuning", "Выберите категорию из списка"),
        ("title", "   ", "Укажите название работы"),
        ("title", "x" * 201, "Название — не длиннее 200 символов"),
        ("service", "x" * 201, "Место — не длиннее 200 символов"),
        ("note", "x" * 2001, "Примечание — не длиннее 2000 символов"),
        ("amount", "", "Стоимость — целое число от 0 до 100 000 000 ₽"),
        ("amount", "-1", "Стоимость — целое число от 0 до 100 000 000 ₽"),
        ("amount", "12.5", "Стоимость — целое число от 0 до 100 000 000 ₽"),
        ("amount", "100000001", "Стоимость — целое число от 0 до 100 000 000 ₽"),
    ],
)
def test_field_errors(car, field, value, message):
    data, errors = validate_record_form(
        form(**{field: value}), car, today=date(2026, 11, 19)
    )
    assert data == {}
    assert errors == {field: message}


def test_amount_zero_ok(car):
    _, errors = validate_record_form(form(amount="0"), car, today=date(2026, 11, 19))
    assert errors == {}


def test_error_keeps_entered_data_and_saves_nothing(client, session, car):
    response = client.post(new_url(car), data=form(mileage="abc", title="Свечи"))

    assert response.status_code == 422
    assert "Проверьте поля формы" in response.text
    assert "Пробег — целое число от 0 до 2 000 000 км" in response.text
    assert 'value="Свечи"' in response.text
    assert records_in(session) == []


# --- Пробег записи и машины ---


def test_mileage_above_car_before_mileage_date(car_jan):
    """Работа раньше даты пробега машины не может иметь пробег больше."""
    assert mileage_error(form(date="2025-12-31", mileage="84501"), car_jan) == (
        f"На 01.01.2026 у машины было {format_km(84500)}, "
        "работа раньше — пробег не может быть больше"
    )


def test_mileage_equal_car_before_mileage_date_ok(car_jan):
    assert mileage_error(form(date="2025-12-31", mileage="84500"), car_jan) is None


def test_mileage_above_car_on_mileage_date_ok(car_jan):
    """В день, на который указан пробег, — не ошибка, а предложение обновить."""
    assert mileage_error(form(date="2026-01-01", mileage="90000"), car_jan) is None


def test_mileage_below_car_after_mileage_date(car_jan):
    """Работа позже даты пробега машины не может иметь пробег меньше."""
    assert mileage_error(form(date="2026-01-02", mileage="84499"), car_jan) == (
        f"На 01.01.2026 у машины было {format_km(84500)}, "
        "работа позже — пробег не может быть меньше"
    )


def test_mileage_below_car_on_mileage_date_ok(car_jan):
    """В тот же день меньший пробег разрешён."""
    assert mileage_error(form(date="2026-01-01", mileage="80000"), car_jan) is None


def test_mileage_equal_car_after_mileage_date_ok(car_jan):
    assert mileage_error(form(date="2026-01-02", mileage="84500"), car_jan) is None


def test_mileage_below_earlier_record(session, car_jan):
    add_record(session, car_jan, date=date(2025, 6, 1), mileage=70000)

    assert mileage_error(form(date="2025-07-01", mileage="69999"), car_jan) == (
        "Пробег меньше, чем в более ранней работе. "
        f"В записи от 01.06.2025 пробег {format_km(70000)}"
    )


def test_mileage_above_later_record(session, car_jan):
    add_record(session, car_jan, date=date(2025, 6, 1), mileage=70000)

    assert mileage_error(form(date="2025-05-01", mileage="70001"), car_jan) == (
        "Пробег больше, чем в более поздней работе. "
        f"В записи от 01.06.2025 пробег {format_km(70000)}"
    )


def test_mileage_between_records_ok(session, car_jan):
    add_record(session, car_jan, date=date(2025, 1, 1), mileage=60000)
    add_record(session, car_jan, date=date(2025, 12, 1), mileage=80000)

    assert mileage_error(form(date="2025-06-01", mileage="60000"), car_jan) is None
    assert mileage_error(form(date="2025-06-01", mileage="80000"), car_jan) is None


def test_same_day_records_not_compared(session, car_jan):
    add_record(session, car_jan, date=date(2025, 6, 1), mileage=70000)

    assert mileage_error(form(date="2025-06-01", mileage="65000"), car_jan) is None


def test_edit_ignores_record_itself(session, car_jan):
    """При изменении сама запись в сравнении не участвует."""
    record = add_record(session, car_jan, date=date(2025, 6, 1), mileage=70000)

    assert (
        mileage_error(form(date="2025-05-01", mileage="75000"), car_jan, record) is None
    )


def test_edit_checks_other_records(client, session, car_jan):
    """При изменении остальные записи проверяются так же, ничего не меняется."""
    add_record(session, car_jan, date=date(2025, 1, 1), mileage=60000)
    record = add_record(session, car_jan, date=date(2025, 6, 1), mileage=70000)

    response = client.post(
        f"/records/{record.id}/edit", data=form(date="2025-06-01", mileage="50000")
    )

    assert response.status_code == 422
    assert "В записи от 01.01.2025 пробег" in response.text
    session.expire_all()
    assert session.get(ServiceRecord, record.id).mileage == 70000


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"date": "2025-12-31", "mileage": "84501"}, "работа раньше"),
        ({"date": "2025-07-01", "mileage": "69999"}, "в более ранней работе"),
        ({"date": "2025-05-01", "mileage": "70001"}, "в более поздней работе"),
    ],
)
def test_mileage_errors_shown_in_form(client, session, car_jan, fields, message):
    """Каждая ошибка пробега — у поля, запись не сохраняется."""
    add_record(session, car_jan, date=date(2025, 6, 1), mileage=70000)

    response = client.post(new_url(car_jan), data=form(**fields))

    assert response.status_code == 422
    error = response.text.split('id="mileage-error"', 1)[1].split("</div>", 1)[0]
    assert message in error
    assert len(records_in(session)) == 1


# --- Предложение обновить пробег машины ---


def test_offers_mileage_update():
    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    car = Obj(mileage=84500, mileage_date=date(2026, 1, 1))

    def rec(day, mileage):
        return Obj(date=day, mileage=mileage)

    assert offers_mileage_update(rec(date(2026, 1, 2), 84501), car)
    assert offers_mileage_update(rec(date(2026, 1, 1), 84501), car)
    assert not offers_mileage_update(rec(date(2026, 1, 2), 84500), car)
    assert not offers_mileage_update(rec(date(2025, 12, 31), 90000), car)


def test_banner_after_saving_newer_record(client, session, car_jan):
    response = client.post(
        new_url(car_jan), data=form(date="2026-02-01", mileage="90000")
    )

    assert response.status_code == 200  # после перехода на журнал
    html = response.text
    assert (
        f"В записи {format_km(90000)}, у машины {format_km(84500)}. "
        "Обновить пробег машины?" in html
    )
    [record] = records_in(session)
    assert f'action="/records/{record.id}/update-mileage"' in html
    assert ">Нет</button>" in html
    # Без нажатия пробег машины не меняется
    session.expire_all()
    assert (car_jan.mileage, car_jan.mileage_date) == (84500, date(2026, 1, 1))


def test_banner_when_car_added_today(client, session, car):
    """Машина добавлена сегодня, запись сегодня с бо́льшим пробегом — плашка есть."""
    today = msk_today()
    assert car.mileage_date == today

    response = client.post(
        new_url(car), data=form(date=today.isoformat(), mileage="85000")
    )

    assert response.status_code == 200
    assert len(records_in(session)) == 1
    assert "Обновить пробег машины?" in response.text


@pytest.mark.parametrize(
    "fields",
    [
        {"date": "2026-02-01", "mileage": "84500"},  # пробег не больше
        {"date": "2025-12-01", "mileage": "80000"},  # раньше даты пробега
    ],
)
def test_no_banner_otherwise(client, car_jan, fields):
    html = client.post(new_url(car_jan), data=form(**fields)).text

    assert "Запись сохранена" in html
    assert "Обновить пробег машины?" not in html


def test_banner_only_right_after_saving(client, session, car_jan):
    add_record(session, car_jan, date=date(2026, 2, 1), mileage=90000)

    assert "Обновить пробег машины?" not in client.get("/history").text


def test_banner_after_editing(client, session, car_jan):
    record = add_record(session, car_jan)

    html = client.post(
        f"/records/{record.id}/edit", data=form(date="2026-02-01", mileage="90000")
    ).text

    assert "Обновить пробег машины?" in html


def test_update_mileage_from_record(client, session, car_jan):
    record = add_record(session, car_jan, date=date(2026, 2, 1), mileage=90000)

    response = client.post(
        f"/records/{record.id}/update-mileage", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/history?mileage_updated=1"
    session.expire_all()
    assert (car_jan.mileage, car_jan.mileage_date) == (90000, date(2026, 2, 1))
    assert "Пробег машины обновлён" in client.get(response.headers["location"]).text


@pytest.mark.parametrize(
    "day, mileage",
    [
        (date(2026, 2, 1), 84500),  # пробег не больше
        (date(2025, 12, 1), 80000),  # раньше даты пробега
    ],
)
def test_update_mileage_ignored_when_not_offered(
    client, session, car_jan, day, mileage
):
    """Повторная или неуместная отправка ничего не меняет."""
    record = add_record(session, car_jan, date=day, mileage=mileage)

    response = client.post(
        f"/records/{record.id}/update-mileage", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    session.expire_all()
    assert (car_jan.mileage, car_jan.mileage_date) == (84500, date(2026, 1, 1))


def test_update_mileage_needs_csrf(client, session, car_jan):
    record = add_record(session, car_jan, date=date(2026, 2, 1), mileage=90000)
    del client.headers["X-CSRF-Token"]

    response = client.post(f"/records/{record.id}/update-mileage")

    assert response.status_code == 403
    session.expire_all()
    assert car_jan.mileage == 84500


def test_record_form_needs_csrf(client, session, car):
    del client.headers["X-CSRF-Token"]

    assert client.post(new_url(car), data=FORM).status_code == 403
    assert records_in(session) == []


# --- Журнал, расходы и главная по базе ---


def test_history_sorted_and_filtered(client, session, car):
    add_record(
        session,
        car,
        date=date(2024, 5, 14),
        mileage=60000,
        category="oil",
        title="Масло 2024",
        service="VAG Service",
    )
    add_record(
        session,
        car,
        date=date(2025, 8, 18),
        mileage=70000,
        category="brakes",
        title="Колодки",
        service=None,
    )
    add_record(
        session,
        car,
        date=date(2025, 9, 1),
        mileage=71000,
        category="oil",
        title="Масло 2025",
        service="Точка",
    )

    html = client.get("/history").text
    assert "Показано записей: 3 из 3" in html
    assert html.index("Масло 2025") < html.index("Колодки") < html.index("Масло 2024")
    assert "<option>2025</option>" in html and "<option>2024</option>" in html

    html = client.get("/history", params={"type": "oil"}).text
    assert "Показано записей: 2 из 3" in html
    assert "Колодки" not in html

    html = client.get("/history", params={"year": "2025", "q": "точка"}).text
    assert "Показано записей: 1 из 3" in html
    assert "Масло 2025" in html

    html = client.get("/history", params={"q": "нет такой работы"}).text
    assert "Ничего не найдено" in html


def test_history_has_edit_links(client, session, car):
    record = add_record(session, car)

    assert f'href="/records/{record.id}/edit"' in client.get("/history").text


def test_expenses_groups(client, session, car):
    add_record(session, car, category="oil", amount=5000)
    add_record(session, car, category="oil", amount=3000)
    add_record(session, car, category="brakes", amount=11500)
    add_record(session, car, category="tires", amount=0)

    html = client.get("/expenses").text

    assert f"За всё время: {format_money(19500)}" in html
    assert html.count('class="expense-bar"') == 2
    assert html.index("Тормоза") < html.index("Масло и фильтр")


def test_expenses_empty(client, car):
    html = client.get("/expenses").text

    assert f"За всё время: {format_money(0)}" in html
    assert "Добавьте первую работу с расходом" in html


def test_spent_month_by_moscow_date():
    class Rec:
        def __init__(self, day, amount):
            self.date, self.amount = day, amount

    records = [
        Rec(date(2026, 11, 1), 100),
        Rec(date(2026, 11, 30), 20),
        Rec(date(2026, 10, 31), 3),
        Rec(date(2025, 11, 15), 4000),
    ]

    assert spent_month(records, today=date(2026, 11, 19)) == 120
    assert spent_month(records, today=date(2026, 12, 1)) == 0


def test_spent_by_category_skips_empty():
    class Rec:
        def __init__(self, category, amount):
            self.category, self.amount = category, amount

    records = [Rec("oil", 10), Rec("oil", 5), Rec("tires", 0), Rec("other", 1)]

    assert spent_by_category(records) == {"oil": 15, "other": 1}


def test_index_tiles_from_db(client, session, car):
    today = msk_today()
    add_record(session, car, date=today, mileage=84000, amount=4000, title="Свежая")
    add_record(
        session,
        car,
        date=today - timedelta(days=400),
        mileage=60000,
        amount=9000,
        title="Старая",
    )

    html = client.get("/").text
    stats = html.split('class="stats"', 1)[1].split("</div>\n</div>", 1)[0]

    assert format_money(4000) in stats  # за месяц — только свежая
    assert "<strong>2</strong>" in stats  # работ в истории
    assert format_money(13000) in html  # за всё время
    assert "Свежая" in html and "Старая" in html


def test_pages_without_car_open(client):
    for url in ("/", "/history", "/expenses"):
        assert client.get(url).status_code == 200


def test_neighbour_sees_only_own_records(client, make_client, make_user, session, car):
    """Второй пользователь не видит записей первого ни в журнале, ни в расходах."""
    add_record(session, car, title="Секретная работа", amount=7777)
    neighbour = make_user("neighbour")
    make_test_car(session, neighbour, make="Kia", model="Rio", year=2018)
    neighbour_client = make_client(neighbour)

    for url in ("/", "/history", "/expenses"):
        html = neighbour_client.get(url).text
        assert "Секретная работа" not in html
        assert format_money(7777) not in html
    assert "В журнале пока нет записей" in neighbour_client.get("/history").text
