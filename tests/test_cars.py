from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import Car
from app.routers.pages import format_msk_datetime
from app.services import catalog
from app.services.cars import form_options, to_msk, validate_car_form

# Правильно заполненная форма: машина из выборки справочника
FORM = {
    "brand": "Lada",
    "model": "Vesta",
    "generation": "I 2015-2022",
    "engine": "1.8 (122 л.с.)",
    "year": "2017",
    "mileage": "120000",
    "gearbox": "robot",
}


@pytest.fixture(autouse=True)
def sample_catalog(tmp_path, monkeypatch):
    """Справочник — выборка из git, а не полный файл с компьютера разработчика."""
    monkeypatch.setattr(catalog, "FULL_PATH", tmp_path / "нет.json")


def form(**fields):
    """Форма с типовыми данными; нужные поля можно переопределить."""
    return {**FORM, **fields}


def cars_in(session):
    return session.scalars(select(Car)).all()


# --- Главная и карточка машины ---


def test_index_without_car_invites_to_add(client):
    """Без машины главная предлагает её добавить и не показывает сводку-пример."""
    html = client.get("/").text

    assert "Добавьте машину" in html
    assert 'href="/cars/new"' in html
    assert 'class="stats"' not in html
    assert "Машина не добавлена" in html


def test_sidebar_without_car_on_every_page(client):
    """Без машины карточка в боковой панели ведёт на форму на всех страницах."""
    for url in ("/history", "/maintenance", "/expenses", "/assistant"):
        html = client.get(url).text
        assert "Машина не добавлена" in html
        assert 'href="/cars/new"' in html


def test_index_with_car_shows_card_from_db(client, car):
    """С машиной карточка показывает данные из базы, остальное помечено как пример."""
    html = client.get("/").text

    for text in (
        "Lada Granta",
        "I Рестайлинг 2018-2026",
        "1.6 (90 л.с.)",
        "АКПП",
        "2020",
        "84 500 км",
        "26.10.2026 01:30 (МСК)",
        "/static/img/car.png",
        "Пример — заработает после задач 3.1 и 4.1",
    ):
        assert text in html
    assert "Добавьте машину" not in html
    assert "Škoda" not in html


@pytest.mark.parametrize("url", ["/history", "/maintenance", "/expenses"])
def test_demo_pages_marked_as_example(client, car, url):
    """Журнал, план ТО и расходы — пример с пометкой, карточка — из базы."""
    html = client.get(url).text

    assert "Пример — заработает после задач 3.1 и 4.1" in html
    assert "Lada Granta" in html


@pytest.mark.parametrize(
    "url", ["/", "/history", "/maintenance", "/expenses", "/assistant"]
)
def test_sidebar_watch_block_and_footer(client, car, url):
    """Блок «На контроле» помечен как пример, подвал говорит правду о базе."""
    html = client.get(url).text

    watch = html.split("На контроле", 1)[1].split("</section>", 1)[0]
    assert "Пример — заработает после задач 3.1 и 4.1" in watch
    footer = html.split('<footer class="footer">', 1)[1]
    assert "Машина хранится в базе данных" in footer
    assert "база данных, VIN-декодер и ИИ-помощник пока не подключены" not in footer


def test_moscow_time():
    """Время из базы в UTC выводится по Москве (UTC+3)."""
    assert to_msk(datetime(2026, 10, 25, 22, 30)).hour == 1
    assert format_msk_datetime(datetime(2026, 10, 25, 22, 30)) == "26.10.2026 01:30"


# --- Форма добавления ---


def test_form_opens_without_car(client):
    """Форма открывается и подключает HTMX для зависимых списков."""
    response = client.get("/cars/new")

    assert response.status_code == 200
    assert "<h1>Добавить машину</h1>" in response.text
    assert 'hx-get="/cars/new/fields"' in response.text
    assert "/static/js/htmx.min.js" in response.text
    for title in ("МКПП", "АКПП", "Вариатор", "Робот"):
        assert f">{title}</option>" in response.text


def test_htmx_file_served(client):
    assert client.get("/static/js/htmx.min.js").status_code == 200


def test_form_hidden_when_car_exists(client, car):
    """Машина пока одна: если она есть, форма уводит на главную."""
    response = client.get("/cars/new", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    response = client.post("/cars/new", data=FORM, follow_redirects=False)
    assert response.status_code == 303


def test_save_car(client, session):
    """Сохранённая машина появляется в базе и на главной."""
    response = client.post("/cars/new", data=FORM, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    [car] = cars_in(session)
    assert (car.make, car.model, car.generation, car.engine) == (
        "Lada",
        "Vesta",
        "I 2015-2022",
        "1.8 (122 л.с.)",
    )
    assert (car.year, car.mileage, car.gearbox) == (2017, 120000, "robot")
    assert car.created_at is not None

    html = client.get("/").text
    assert "Lada Vesta" in html
    assert "Робот" in html
    assert "120 000 км" in html


def test_save_uses_catalog_spelling(client, session):
    """Названия сохраняются так, как они записаны в справочнике."""
    client.post("/cars/new", data=form(brand="lada", model="VESTA"))

    [car] = cars_in(session)
    assert (car.make, car.model) == ("Lada", "Vesta")


@pytest.mark.parametrize(
    "year, ok",
    [("2015", True), ("2022", True), ("2014", False), ("2023", False), ("", False)],
)
def test_year_within_generation(year, ok):
    """Год — только из лет выпуска поколения (I 2015-2022)."""
    _, errors = validate_car_form(form(year=year))
    assert ("year" not in errors) is ok


@pytest.mark.parametrize(
    "mileage, ok",
    [
        ("0", True),
        ("2000000", True),
        ("84500", True),
        ("-1", False),
        ("2000001", False),
        ("12.5", False),
        ("abc", False),
        ("", False),
    ],
)
def test_mileage_range(mileage, ok):
    """Пробег — целое число от 0 до 2 000 000."""
    _, errors = validate_car_form(form(mileage=mileage))
    assert ("mileage" not in errors) is ok


@pytest.mark.parametrize(
    "field, value",
    [
        ("brand", "Нет такой"),
        ("model", "Granta"),  # модель есть, но поколения I 2015-2022 у неё нет
        ("generation", "II 2030-2035"),
        ("engine", "1.6 (87 л.с.)"),  # двигатель другого поколения
        ("gearbox", "DSG"),
        ("gearbox", "АКПП"),  # в форме — код, а не название
    ],
)
def test_wrong_choice_rejected(field, value):
    """Значения не из справочника и не из списка коробок не принимаются."""
    car, errors = validate_car_form(form(**{field: value}))

    assert car == {}
    assert errors


def test_error_keeps_entered_data(client, session):
    """При ошибке форма показывает, что не так, и не теряет введённое."""
    response = client.post("/cars/new", data=form(year="2030", mileage="-5"))

    assert response.status_code == 422
    html = response.text
    assert "Это поколение выпускали с 2015 по 2022 год" in html
    assert "Пробег — целое число от 0 до 2 000 000 км" in html
    assert '<option value="Lada" selected>' in html
    assert '<option value="Vesta" selected>' in html
    assert '<option value="I 2015-2022" selected>' in html
    assert '<option value="1.8 (122 л.с.)" selected>' in html
    assert '<option value="robot" selected>' in html
    assert 'value="2030"' in html
    assert 'value="-5"' in html
    assert cars_in(session) == []


def test_empty_form_shows_our_errors(client, session):
    """Пустая форма — наша страница с сообщениями, а не ошибка FastAPI в JSON."""
    response = client.post("/cars/new", data={})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Выберите марку из списка" in response.text
    assert "Укажите год выпуска числом" in response.text
    assert "Выберите коробку передач из списка" in response.text
    assert cars_in(session) == []


# --- Зависимые списки ---


def test_fields_brand_shows_only_its_models(client):
    """После выбора марки — только её модели."""
    html = client.get("/cars/new/fields", params={"brand": "Lada"}).text

    assert '<option value="Vesta">' in html
    assert '<option value="Rio">' not in html  # модель Kia
    assert "Сначала выберите модель" in html


def test_fields_model_shows_its_generations(client):
    html = client.get(
        "/cars/new/fields", params={"brand": "Lada", "model": "Vesta"}
    ).text

    assert '<option value="I 2015-2022">' in html
    assert '<option value="I Рестайлинг (NG) 2022-2026">' in html
    assert "I Рестайлинг 2018-2026" not in html  # поколение Granta
    assert "Сначала выберите поколение" in html


def test_fields_generation_shows_engines_and_years(client):
    """После выбора поколения — его двигатели и границы года."""
    params = {"brand": "Lada", "model": "Vesta", "generation": "I 2015-2022"}
    html = client.get("/cars/new/fields", params=params).text

    assert '<option value="1.8 (145 л.с.)">' in html
    assert "1.8 (147 л.с.)" not in html  # двигатель другого поколения
    assert 'min="2015" max="2022"' in html
    assert "Годы выпуска поколения: 2015–2022" in html


def test_fields_reset_when_brand_changes(client):
    """Сменили марку — модель от прежней марки и всё ниже сбрасываются."""
    params = {"brand": "Kia", "model": "Vesta", "generation": "I 2015-2022"}
    html = client.get("/cars/new/fields", params=params).text

    assert '<option value="Rio">' in html
    assert "selected" not in html.split('id="model"', 1)[1]
    assert "Сначала выберите модель" in html


def test_form_options_reset_chain():
    """form_options сбрасывает значения, которые не подходят к выбору выше."""
    options = form_options("Kia", "Vesta", "I 2015-2022", "1.8 (122 л.с.)")

    assert options["brand"] == "Kia"
    assert options["model"] == ""
    assert options["generation"] is None
    assert options["engine"] == ""
    assert options["engines"] == []
