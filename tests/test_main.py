import os

import pytest

from app.routers import pages
from app.routers.pages import due_text, format_km, format_money, oil_due, static_version

# Страница, заголовок и пункт меню, который на ней подсвечен
PAGES = [
    ("/", "Сервисная книжка", "Мой автомобиль"),
    ("/history", "История обслуживания", "История"),
    ("/maintenance", "План технического обслуживания", "План ТО"),
    ("/expenses", "Расходы на автомобиль", "Расходы"),
    ("/assistant", "Помощник автовладельца", "Помощник"),
]


@pytest.mark.parametrize("url, title, nav_label", PAGES)
def test_page_opens(url, title, nav_label, client):
    """Каждая страница из меню открывается и показывает свой заголовок."""
    response = client.get(url)

    assert response.status_code == 200
    assert f"<h1>{title}</h1>" in response.text


@pytest.mark.parametrize("url, title, nav_label", PAGES)
def test_active_nav_item(url, title, nav_label, client):
    """На каждой странице подсвечен свой пункт меню, и только он."""
    html = client.get(url).text

    assert html.count('class="active"') == 1
    active = html.split('class="active"', 1)[1].split("</a>", 1)[0]
    assert f'href="{url}" aria-current="page">' in active
    assert nav_label in active


def test_menu_links_lead_to_pages(client):
    """Ссылки меню ведут на страницы, а не на «#»."""
    html = client.get("/").text

    assert 'href="#"' not in html
    for url, _, _ in PAGES:
        assert f'href="{url}"' in html


def test_base_layout(client):
    """Страницы построены на base.html: шапка, карточка машины, подвал и свои стили."""
    html = client.get("/maintenance").text

    assert '<header class="topbar">' in html
    assert '<aside class="sidebar">' in html
    assert '<section class="car-card">' in html
    assert "/static/img/car.png" in html
    assert '<footer class="footer">' in html
    assert "/static/style.css" in html


def test_style_css_versioned(client):
    """Ссылка на стили содержит версию файла, чтобы браузер не брал старые стили из кэша."""
    html = client.get("/").text

    assert f"/static/style.css?v={static_version('style.css')}" in html


def test_static_version_changes(tmp_path, monkeypatch):
    """Версия меняется вместе со временем изменения файла."""
    css = tmp_path / "static" / "style.css"
    css.parent.mkdir()
    css.write_text("body {}", encoding="utf-8")
    monkeypatch.setattr(pages, "APP_DIR", tmp_path)

    os.utime(css, (1_000_000, 1_000_000))
    first = static_version("style.css")
    os.utime(css, (2_000_000, 2_000_000))

    assert first == 1_000_000
    assert static_version("style.css") == 2_000_000


def test_index_dashboard(client, car):
    """Главная с машиной: сводка, история, ближайшее ТО и карточка помощника."""
    html = client.get("/").text

    assert 'class="stats"' in html
    assert 'class="timeline"' in html
    assert "Ближайшее ТО" in html
    assert "Через 1 500 км" in html
    assert "60 600 ₽" in html
    assert 'action="/assistant"' in html


def stats_block(html: str) -> str:
    """HTML плиток сводки — до следующего блока страницы."""
    return html.split('class="stats"', 1)[1].split("</div>\n</div>", 1)[0]


def test_index_mileage_from_db(client, session, car):
    """На главной из базы — только плитка «Текущий пробег»; «До замены масла» и «Сейчас» — по примеру."""
    car.mileage = 85000
    session.commit()

    html = client.get("/").text
    stats = stats_block(html)

    assert format_km(85000) in stats
    assert format_km(84500) not in stats
    assert format_km(1500) in stats
    assert f"<h3>{format_km(84500)}<time>Сейчас</time></h3>" in html


def test_index_zero_mileage(client, session, car):
    """Пробег 0 км из базы показывается как есть, а не заменяется примером."""
    car.mileage = 0
    session.commit()

    assert format_km(0) in stats_block(client.get("/").text)


def test_expenses_mileage_from_demo(client, session, car):
    """На расходах сводка по примеру, даже если машина в базе есть."""
    car.mileage = 85000
    session.commit()

    stats = stats_block(client.get("/expenses").text)

    assert format_km(84500) in stats
    assert format_km(85000) not in stats


def test_history_table(client):
    """История показывает все работы из журнала."""
    html = client.get("/history").text

    assert "Показано записей: 6 из 6" in html
    assert "Замена передних тормозных колодок" in html


def test_history_filters(client):
    """Поиск и фильтры истории отбирают записи."""
    html = client.get("/history", params={"type": "oil"}).text
    assert "Показано записей: 2 из 6" in html
    assert "Компьютерная диагностика" not in html

    html = client.get("/history", params={"year": "2026", "q": "vag"}).text
    assert "Показано записей: 2 из 6" in html

    html = client.get("/history", params={"q": "нет такой работы"}).text
    assert "Ничего не найдено" in html


def test_maintenance_plan(client):
    """План ТО: замена масла считается от последней замены и интервала."""
    html = client.get("/maintenance").text

    assert html.count('class="feature-card"') == 6
    assert "Плановый пробег: 86 000 км" in html


def test_maintenance_ignores_db_mileage(client, session, car):
    """План ТО считается по пробегу из примера и не зависит от пробега машины в базе."""

    def plan() -> str:
        # Только основной блок: в карточке машины слева пробег из базы — так и должно быть
        html = client.get("/maintenance").text
        return html.split('<main class="workspace">', 1)[1].split("</main>", 1)[0]

    before = plan()
    car.mileage = 87000  # от этого пробега масло (86 000 км) было бы просрочено
    session.commit()

    after = plan()

    assert after == before
    assert f"Через {format_km(1500)}" in after
    assert "Просрочено" not in after


def test_expenses_groups(client):
    """Расходы разбиты по категориям работ."""
    html = client.get("/expenses").text

    assert "За всё время: 60 600 ₽" in html
    assert html.count('class="expense-bar"') == 4


def test_assistant_shows_question(client):
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
def test_static_files(path, client):
    """Стили и картинки отдаются приложением."""
    response = client.get(f"/static/{path}")

    assert response.status_code == 200
