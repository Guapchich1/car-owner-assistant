import pytest
from sqlalchemy.orm import Session

from app import models  # noqa: F401 — регистрирует таблицы в Base.metadata
from app.db import Base, make_engine


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
