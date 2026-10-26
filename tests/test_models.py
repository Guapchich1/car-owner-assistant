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


def test_migrations_create_tables(db_url):
    """alembic upgrade head создаёт обе таблицы, downgrade — удаляет."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")
    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert {"cars", "service_records"} <= tables
    columns = {c["name"] for c in inspect(engine).get_columns("cars")}
    assert {"generation", "gearbox"} <= columns

    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
