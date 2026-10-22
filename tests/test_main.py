from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_page():
    """Главная страница открывается и показывает заголовок."""
    response = client.get("/")

    assert response.status_code == 200
    assert "<h1>Сервисная книжка</h1>" in response.text
    assert "Здесь появятся ваши машины" in response.text


def test_index_uses_base_layout():
    """Главная построена на base.html: шапка с меню, подвал и свои стили."""
    response = client.get("/")
    html = response.text

    assert '<header class="topbar">' in html
    assert '<footer class="footer">' in html
    assert "/static/style.css" in html
    assert "pico" not in html.lower()
    for label in ["Мой автомобиль", "История", "План ТО", "Расходы", "Помощник"]:
        assert label in html


def test_active_nav_item():
    """На главной подсвечен пункт «Мой автомобиль», и только он."""
    html = client.get("/").text

    assert html.count('class="active"') == 1
    assert 'aria-current="page">' in html
    active = html.split('class="active"', 1)[1].split("</a>", 1)[0]
    assert "Мой автомобиль" in active


def test_static_style():
    """Файл стилей отдаётся приложением."""
    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ".topbar" in response.text
