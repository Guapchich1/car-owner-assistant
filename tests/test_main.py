from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_page():
    """Главная страница открывается и показывает заголовок."""
    response = client.get("/")

    assert response.status_code == 200
    assert "<h1>Помощник автовладельца</h1>" in response.text
