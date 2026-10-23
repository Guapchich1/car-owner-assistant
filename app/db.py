"""Подключение к базе данных и сессии SQLAlchemy."""

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./car_assistant.db")


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""


def make_engine(url: str) -> Engine:
    """Создаёт подключение к базе; в SQLite включает проверку внешних ключей."""
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        # По умолчанию SQLite не проверяет внешние ключи — включаем
        # на каждом новом соединении
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Iterator[Session]:
    """Сессия для обработчика FastAPI: закрывается после запроса."""
    with SessionLocal() as session:
        yield session
