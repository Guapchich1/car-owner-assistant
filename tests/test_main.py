import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.pages import due_text, format_km, format_money, oil_due

client = TestClient(app)

# Страница, заголовок и пункт меню, который на ней подсвечен
PAGES = [
    ("/", "Сервисная книжка", "Мой автомобиль"),
    ("/history", "История обслуживания", "История"),
    ("/maintenance", "План технического обслуживания", "План ТО"),
    ("/expenses", "Расходы на автомобиль", "Расходы"),
    ("/assistant", "Помощник автовладельца", "Помощник"),
]


@pytest.mark.parametrize("url, title, nav_label", PAGES)
def test_page_opens(url, title, nav_label):
    """Каждая страница из меню открывается и показывает свой заголовок."""
    response = client.get(url)

    assert response.status_code == 200
    assert f"<h1>{title}</h1>" in response.text


@pytest.mark.parametrize("url, title, nav_label", PAGES)
def test_active_nav_item(url, title, nav_label):
    """На каждой странице подсвечен свой пункт меню, и только он."""
    html = client.get(url).text

    assert html.count('class="active"') == 1
    active = html.split('class="active"', 1)[1].split("</a>", 1)[0]
    assert f'href="{url}" aria-current="page">' in active
    assert nav_label in active


def test_menu_links_lead_to_pages():
    """Ссылки меню ведут на страницы, а не на «#»."""
    html = client.get("/").text

    assert 'href="#"' not in html
    for url, _, _ in PAGES:
        assert f'href="{url}"' in html


def test_base_layout():
    """Страницы построены на base.html: шапка, карточка машины, подвал и свои стили."""
    html = client.get("/maintenance").text

    assert '<header class="topbar">' in html
    assert '<aside class="sidebar">' in html
    assert "Škoda Octavia" in html
    assert "/static/img/car.png" in html
    assert '<footer class="footer">' in html
    assert "/static/style.css" in html


def test_index_dashboard():
    """Главная: сводка, история, ближайшее ТО и карточка помощника."""
    html = client.get("/").text

    assert 'class="stats"' in html
    assert 'class="timeline"' in html
    assert "Ближайшее ТО" in html
    assert "Через 1 500 км" in html
    assert "60 600 ₽" in html
    assert 'action="/assistant"' in html


def test_history_table():
    """История показывает все работы из журнала."""
    html = client.get("/history").text

    assert "Показано записей: 6 из 6" in html
    assert "Замена передних тормозных колодок" in html


def test_history_filters():
    """Поиск и фильтры истории отбирают записи."""
    html = client.get("/history", params={"type": "oil"}).text
    assert "Показано записей: 2 из 6" in html
    assert "Компьютерная диагностика" not in html

    html = client.get("/history", params={"year": "2026", "q": "vag"}).text
    assert "Показано записей: 2 из 6" in html

    html = client.get("/history", params={"q": "нет такой работы"}).text
    assert "Ничего не найдено" in html


def test_maintenance_plan():
    """План ТО: замена масла считается от последней замены и интервала."""
    html = client.get("/maintenance").text

    assert html.count('class="feature-card"') == 6
    assert "Плановый пробег: 86 000 км" in html


def test_expenses_groups():
    """Расходы разбиты по категориям работ."""
    html = client.get("/expenses").text

    assert "За всё время: 60 600 ₽" in html
    assert html.count('class="expense-bar"') == 4


def test_assistant_shows_question():
    """Помощник показывает вопрос и честно говорит, что модель не подключена."""
    html = client.get("/assistant", params={"q": "<b>Что проверить?</b>"}).text

    assert "Ваш вопрос: &lt;b&gt;Что проверить?&lt;/b&gt;" in html
    assert "ещё не подключён" in html


def test_oil_due_and_text():
    """Расчёт следующей замены масла и подпись срока."""
    vehicle = {"mileage": 84500, "oil_interval": 15000}
    records = [
        {"type": "oil", "mileage": 10000},
        {"type": "oil", "mileage": 71000},
        {"type": "brakes", "mileage": 82000},
    ]

    assert oil_due(vehicle, records) == 86000
    assert oil_due(vehicle, []) == 15000
    assert due_text(86000, 84500) == "Через 1 500 км"
    assert due_text(86000, 86000) == "Пора выполнить ТО"
    assert due_text(86000, 87000) == "Просрочено на 1 000 км"


def test_formatting():
    """Числа выводятся с пробелом между разрядами."""
    assert format_km(84500) == "84 500 км"
    assert format_money(1234.5) == "1 234,50 ₽"


@pytest.mark.parametrize("path", ["style.css", "img/car.png", "img/scenery.png"])
def test_static_files(path):
    """Стили и картинки отдаются приложением."""
    response = client.get(f"/static/{path}")

    assert response.status_code == 200
