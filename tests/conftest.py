from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401 — регистрирует таблицы в Base.metadata
from app.db import Base, get_session, make_engine
from app.main import app
from app.models import Car, User
from app.services.users import (
    SESSION_COOKIE,
    csrf_token_for,
    hash_password,
    start_session,
)

TEST_PASSWORD = "пароль-для-тестов"
# Считаем хеш один раз: Argon2 намеренно медленный, а пользователь нужен почти везде
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


@pytest.fixture
def db_url(tmp_path):
    """Адрес отдельной временной базы; car_assistant.db не трогаем."""
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def session(db_url):
    """Сессия временной базы с пустыми таблицами."""
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def temp_db(db_url):
    """Страницы приложения на время теста работают с временной базой."""
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    make_session = sessionmaker(bind=engine)

    def temp_session():
        with make_session() as session:
            yield session

    app.dependency_overrides[get_session] = temp_session
    yield
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def anon_client(temp_db):
    """Клиент без входа."""
    return TestClient(app)


@pytest.fixture
def make_user(session):
    """Создаёт пользователя в базе, минуя форму регистрации."""

    def create(username: str) -> User:
        user = User(username=username, password_hash=TEST_PASSWORD_HASH)
        session.add(user)
        session.commit()
        return user

    return create


@pytest.fixture
def make_client(temp_db, session):
    """Создаёт клиента, вошедшего под указанным пользователем: свои cookie и токен."""

    def create(user: User) -> TestClient:
        token = start_session(session, user)
        session.commit()
        client = TestClient(app)
        client.cookies.set(SESSION_COOKIE, token)
        client.headers["X-CSRF-Token"] = csrf_token_for(token)
        return client

    return create


@pytest.fixture
def user(make_user):
    """Основной пользователь тестов."""
    return make_user("driver")


@pytest.fixture
def client(make_client, user):
    """Клиент основного пользователя: вошёл и передаёт CSRF-токен."""
    return make_client(user)


def make_test_car(session, owner: User, **fields) -> Car:
    """Машина в базе у указанного владельца."""
    data = {
        "make": "Lada",
        "model": "Granta",
        "generation": "I Рестайлинг 2018-2026",
        "engine": "1.6 (90 л.с.)",
        "gearbox": "automatic",
        "year": 2020,
        "mileage": 84500,
        "created_at": datetime(2026, 10, 25, 22, 30),
        **fields,
    }
    car = Car(owner_id=owner.id, **data)
    session.add(car)
    session.commit()
    return car


@pytest.fixture
def car(session, user):
    """Машина основного пользователя: Lada Granta, добавлена 25.10.2026 22:30 UTC."""
    return make_test_car(session, user)
