from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.models import Car, ServiceRecord

ROOT = Path(__file__).resolve().parent.parent


def make_car(**fields):
    """Машина с типовыми данными; нужные поля можно переопределить."""
    data = {
        "make": "Škoda",
        "model": "Octavia",
        "year": 2020,
        "engine": "1.4 TSI",
        "mileage": 84500,
    }
    data.update(fields)
    return Car(**data)


def make_record(car, **fields):
    """Запись журнала для машины с типовыми данными."""
    data = {
        "car": car,
        "date": date(2026, 4, 18),
        "mileage": 71000,
        "category": "oil",
        "title": "Замена масла и масляного фильтра",
        "amount": 8400,
        "service": "VAG Service",
        "note": "Масло 5W-30",
    }
    data.update(fields)
    return ServiceRecord(**data)


def test_save_and_read_car_and_record(session):
    """Машину и запись журнала можно сохранить и прочитать."""
    car = make_car(vin="XW8ZZZ1ZZLG000001")
    session.add(make_record(car))
    session.commit()
    session.expunge_all()

    saved_car = session.scalars(select(Car)).one()
    assert saved_car.make == "Škoda"
    assert saved_car.model == "Octavia"
    assert saved_car.year == 2020
    assert saved_car.engine == "1.4 TSI"
    assert saved_car.vin == "XW8ZZZ1ZZLG000001"
    assert saved_car.mileage == 84500
    assert saved_car.created_at is not None

    saved_record = session.scalars(select(ServiceRecord)).one()
    assert saved_record.car_id == saved_car.id
    assert saved_record.date == date(2026, 4, 18)
    assert saved_record.mileage == 71000
    assert saved_record.category == "oil"
    assert saved_record.title == "Замена масла и масляного фильтра"
    assert saved_record.amount == 8400
    assert saved_record.service == "VAG Service"
    assert saved_record.note == "Масло 5W-30"


def test_car_shows_its_records(session):
    """У машины видны её записи, по дате, и не видны записи другой машины."""
    car = make_car()
    other = make_car(model="Rapid")
    make_record(car, date=date(2026, 9, 12), category="brakes", title="Колодки")
    make_record(car, date=date(2021, 3, 12), title="Масло")
    make_record(other, title="Чужая запись")
    session.add_all([car, other])
    session.commit()
    session.expunge_all()

    saved = session.scalars(select(Car).where(Car.model == "Octavia")).one()
    assert [r.title for r in saved.records] == ["Масло", "Колодки"]


def test_same_vin_twice_is_rejected(session):
    """Два раза один VIN сохранить нельзя."""
    session.add(make_car(vin="XW8ZZZ1ZZLG000001"))
    session.commit()

    session.add(make_car(vin="XW8ZZZ1ZZLG000001"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_cars_without_vin_allowed(session):
    """VIN необязательный: несколько машин без VIN сохраняются."""
    session.add_all([make_car(), make_car()])
    session.commit()

    assert len(session.scalars(select(Car)).all()) == 2


def test_car_generation_and_gearbox(session):
    """Поколение и коробку можно сохранить; у машины без них — пусто."""
    session.add_all(
        [make_car(generation="III 2013-2020", gearbox="robot"), make_car(model="Rapid")]
    )
    session.commit()
    session.expunge_all()

    octavia, rapid = session.scalars(select(Car).order_by(Car.id)).all()
    assert (octavia.generation, octavia.gearbox) == ("III 2013-2020", "robot")
    assert (rapid.generation, rapid.gearbox) == (None, None)


def test_unknown_gearbox_is_rejected(session):
    """Коробку не из списка сохранить нельзя."""
    session.add(make_car(gearbox="DSG"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_unknown_category_is_rejected(session):
    """Категорию не из списка сохранить нельзя."""
    session.add(make_record(make_car(), category="tuning"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_record_without_existing_car_is_rejected(session):
    """Запись с несуществующей машиной не сохраняется: внешние ключи проверяются."""
    session.add(
        ServiceRecord(
            car_id=999,
            date=date(2026, 4, 18),
            mileage=71000,
            category="oil",
            title="Замена масла",
            amount=8400,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_car_deletes_its_records(session):
    """При удалении машины удаляются и её записи."""
    car = make_car()
    make_record(car)
    session.add(car)
    session.commit()

    session.delete(car)
    session.commit()

    assert session.scalars(select(ServiceRecord)).all() == []


def test_migration_0002_adds_car_fields(db_url):
    """Миграция 0002 применяется после 0001 и добавляет поколение и коробку."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)
    engine = create_engine(db_url)

    def car_columns():
        return {c["name"] for c in inspect(engine).get_columns("cars")}

    command.upgrade(config, "0001")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cars (make, model, year, mileage) "
                "VALUES ('Lada', 'Granta', 2020, 84500)"
            )
        )
    assert "gearbox" not in car_columns()

    command.upgrade(config, "0002")
    assert {"generation", "gearbox"} <= car_columns()
    with engine.connect() as connection:
        row = connection.execute(text("SELECT make, gearbox FROM cars")).one()
    assert tuple(row) == ("Lada", None)  # старая машина сохранилась
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE cars SET gearbox = 'DSG'"))

    command.downgrade(config, "0001")
    assert not {"generation", "gearbox"} & car_columns()
    engine.dispose()


def test_migration_0003_adds_car_surveys(db_url):
    """Миграция 0003 применяется поверх базы с машиной и откатывается."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)
    engine = create_engine(db_url)

    command.upgrade(config, "0002")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cars (make, model, year, mileage) "
                "VALUES ('Lada', 'Granta', 2020, 84500)"
            )
        )
    assert "car_surveys" not in inspect(engine).get_table_names()

    command.upgrade(config, "0003")
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO car_surveys (car_id, monthly_km) VALUES (1, 1000)")
        )
        row = connection.execute(
            text(
                "SELECT c.make, s.last_service_approximate, s.updated_at "
                "FROM car_surveys s JOIN cars c ON c.id = s.car_id"
            )
        ).one()
    assert row[:2] == ("Lada", 0)  # машина сохранилась, значение по умолчанию
    assert row[2] is not None
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE car_surveys SET monthly_km = 30001"))

    command.downgrade(config, "0002")
    assert "car_surveys" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM cars")).scalar() == 1
    engine.dispose()


def test_migration_0004_adds_users_sessions_and_owner(db_url):
    """На базе с данными: всё сохраняется, старая машина остаётся без владельца."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)
    engine = create_engine(db_url)

    def count(table):
        with engine.connect() as connection:
            return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()

    def add_user(connection, username):
        connection.execute(
            text("INSERT INTO users (username, password_hash) VALUES (:u, 'x')"),
            {"u": username},
        )

    command.upgrade(config, "0003")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cars (make, model, year, mileage, vin, gearbox) "
                "VALUES ('Lada', 'Granta', 2020, 84500, 'XTA21900000000001', 'robot')"
            )
        )
        connection.execute(
            text("INSERT INTO car_surveys (car_id, monthly_km) VALUES (1, 1000)")
        )
        connection.execute(
            text(
                "INSERT INTO service_records "
                "(car_id, date, mileage, category, title, amount) "
                "VALUES (1, '2026-01-01', 1000, 'oil', 'Масло', 1000)"
            )
        )

    command.upgrade(config, "0004")

    # Таблица cars пересоздана: данные и связанные записи на месте
    assert (count("cars"), count("car_surveys"), count("service_records")) == (1, 1, 1)
    with engine.connect() as connection:
        owner = connection.execute(text("SELECT owner_id FROM cars")).scalar()
    assert owner is None
    # Прежние ограничения машины не потерялись
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE cars SET gearbox = 'DSG'"))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cars (make, model, year, mileage, vin) "
                "VALUES ('Kia', 'Rio', 2018, 1, 'XTA21900000000001')"
            )
        )
    # Логин уникален без учёта регистра
    with engine.begin() as connection:
        add_user(connection, "Arsen")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        add_user(connection, "arsen")
    # У пользователя одна машина; без владельца машин может быть сколько угодно
    with engine.begin() as connection:
        connection.execute(text("UPDATE cars SET owner_id = 1"))
        connection.execute(
            text(
                "INSERT INTO cars (make, model, year, mileage) "
                "VALUES ('Kia', 'Rio', 2018, 1), ('Kia', 'Ceed', 2018, 1)"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("UPDATE cars SET owner_id = 1 WHERE model = 'Rio'"))

    command.downgrade(config, "0003")

    tables = set(inspect(engine).get_table_names())
    assert not {"users", "user_sessions"} & tables
    assert "owner_id" not in {c["name"] for c in inspect(engine).get_columns("cars")}
    assert (count("cars"), count("car_surveys"), count("service_records")) == (3, 1, 1)
    engine.dispose()


def test_migrated_schema_matches_models(db_url):
    """После миграций модели и база совпадают: новой миграции не требуется."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")
    command.check(config)


def test_migrations_create_tables(db_url):
    """alembic upgrade head создаёт все таблицы, downgrade — удаляет."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")
    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "cars",
        "service_records",
        "car_surveys",
        "users",
        "user_sessions",
    } <= tables
    columns = {c["name"] for c in inspect(engine).get_columns("cars")}
    assert {"generation", "gearbox", "owner_id"} <= columns

    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
