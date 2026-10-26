from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import models  # noqa: F401 — регистрирует таблицы в Base.metadata
from app.db import Base, get_session, make_engine
from app.main import app
from app.models import Car


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
def client(db_url):
    """Клиент приложения, страницы которого работают с временной базой."""
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    make_session = sessionmaker(bind=engine)

    def temp_session():
        with make_session() as session:
            yield session

    app.dependency_overrides[get_session] = temp_session
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def car(session):
    """Машина в базе: Lada Granta из справочника, добавлена 25.10.2026 22:30 UTC."""
    car = Car(
        make="Lada",
        model="Granta",
        generation="I Рестайлинг 2018-2026",
        engine="1.6 (90 л.с.)",
        gearbox="automatic",
        year=2020,
        mileage=84500,
        created_at=datetime(2026, 10, 25, 22, 30),
    )
    session.add(car)
    session.commit()
    return car
