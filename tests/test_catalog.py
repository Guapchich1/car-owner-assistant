import json

import pytest

from app.models import CATEGORIES
from app.services import catalog
from scripts.build_catalog import MIN_YEAR_TO, SELECTION, WORK_CODES


def work(code, km, months=None, category="other"):
    """Работа справочника с нужными интервалами."""
    return {
        "code": code,
        "title": code,
        "category": category,
        "interval_km": km,
        "interval_months": months,
    }


# Маленький полный справочник: общая группа и группа дизелей пересекаются
FULL = {
    "default_regulation": [work("engine_oil", 15000, 12, "oil")],
    "brands": {
        "Kia": {
            "Ceed": [
                {
                    "name": "III 2018-2021",
                    "year_from": 2018,
                    "year_to": 2021,
                    "source": "https://example.ru/ceed",
                    "engine_groups": [
                        {
                            "engines": ["1.6 (128 л.с.)", "1.6d (136 л.с.)"],
                            "works": [
                                work("engine_oil", 15000, 12, "oil"),
                                work("fuel_filter", 60000, 48, "filters"),
                            ],
                        },
                        {
                            "engines": ["1.6d (136 л.с.)"],
                            "works": [work("fuel_filter", 30000, 24, "filters")],
                        },
                    ],
                }
            ]
        }
    },
}


@pytest.fixture(autouse=True)
def no_full_file(tmp_path, monkeypatch):
    """По умолчанию полного файла нет — тесты работают на выборке из git."""
    monkeypatch.setattr(catalog, "FULL_PATH", tmp_path / "нет.json")


@pytest.fixture
def full_file(tmp_path, monkeypatch):
    """Полный справочник есть — подставляем маленький FULL."""
    path = tmp_path / "catalog_full.json"
    path.write_text(json.dumps(FULL, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(catalog, "FULL_PATH", path)
    return path


def test_sample_used_without_full_file():
    """Если полного файла нет — берём выборку app/data/catalog.json."""
    assert catalog.catalog_path() == catalog.SAMPLE_PATH
    assert "Lada" in catalog.list_brands()


def test_full_file_used_when_exists(full_file):
    """Если полный файл есть — берём его."""
    assert catalog.catalog_path() == full_file
    assert catalog.list_brands() == ["Kia"]


def test_brands_models_generations_engines():
    """Функции возвращают марки, модели, поколения и двигатели."""
    assert "Kia" in catalog.list_brands()
    assert "Rio" in catalog.list_models("Kia")

    generations = catalog.list_generations("Kia", "Rio")
    rio4 = next(g for g in generations if g["name"] == "IV 2017-2020")
    assert rio4["year_from"] == 2017
    assert rio4["year_to"] == 2020
    assert rio4["source"].startswith("https://")

    engines = catalog.list_engines("Kia", "Rio", "IV 2017-2020")
    assert engines == ["1.4 (100 л.с.)", "1.6 (123 л.с.)"]


def test_regulation_for_car():
    """Регламент машины из справочника — с источником и работами."""
    regulation = catalog.get_regulation("Kia", "Rio", "IV 2017-2020", "1.6 (123 л.с.)")
    assert regulation["is_default"] is False
    assert regulation["source"].startswith("https://")
    codes = {w["code"] for w in regulation["works"]}
    assert "engine_oil" in codes


def test_engines_without_repeats(full_file):
    """Двигатель из нескольких групп в списке один раз."""
    assert catalog.list_engines("Kia", "Ceed", "III 2018-2021") == [
        "1.6 (128 л.с.)",
        "1.6d (136 л.с.)",
    ]


def test_regulation_merges_groups_specific_wins(full_file):
    """Работы из всех групп двигателя; при повторе — группа с меньшим числом моторов."""
    diesel = catalog.get_regulation("Kia", "Ceed", "III 2018-2021", "1.6d (136 л.с.)")
    works = {w["code"]: w for w in diesel["works"]}
    assert set(works) == {"engine_oil", "fuel_filter"}
    assert works["fuel_filter"]["interval_km"] == 30000

    petrol = catalog.get_regulation("Kia", "Ceed", "III 2018-2021", "1.6 (128 л.с.)")
    works = {w["code"]: w for w in petrol["works"]}
    assert works["fuel_filter"]["interval_km"] == 60000


@pytest.mark.parametrize(
    "brand, model, generation, engine",
    [
        ("Нет такой", "Rio", "IV 2017-2020", "1.6 (123 л.с.)"),
        ("Kia", "Нет такой", "IV 2017-2020", "1.6 (123 л.с.)"),
        ("Kia", "Rio", "Нет такого", "1.6 (123 л.с.)"),
        ("Kia", "Rio", "IV 2017-2020", "Нет такого"),
    ],
)
def test_unknown_car_gets_default_regulation(brand, model, generation, engine):
    """Для неизвестной машины или двигателя — общий регламент."""
    regulation = catalog.get_regulation(brand, model, generation, engine)
    assert regulation["is_default"] is True
    assert regulation["source"] is None
    assert regulation["works"] == catalog.default_regulation()
    assert regulation["works"]


def test_unknown_names_give_empty_lists():
    """Для неизвестных марки, модели, поколения — пустые списки."""
    assert catalog.list_models("Нет такой") == []
    assert catalog.list_generations("Kia", "Нет такой") == []
    assert catalog.list_engines("Kia", "Rio", "Нет такого") == []


def test_search_ignores_case():
    """Поиск без учёта регистра."""
    assert catalog.list_models("kia") == catalog.list_models("Kia")
    assert catalog.list_generations("KIA", "rio") == catalog.list_generations(
        "Kia", "Rio"
    )
    assert catalog.list_engines("kia", "RIO", "iv 2017-2020") == catalog.list_engines(
        "Kia", "Rio", "IV 2017-2020"
    )
    assert catalog.list_models("уаз") == ["Патриот"]
    regulation = catalog.get_regulation("kia", "rio", "iv 2017-2020", "1.6 (123 Л.С.)")
    assert regulation["is_default"] is False


def test_result_does_not_change_catalog():
    """Изменение результата не портит справочник в памяти."""
    regulation = catalog.get_regulation("Kia", "Rio", "IV 2017-2020", "1.6 (123 л.с.)")
    regulation["works"][0]["interval_km"] = 1
    catalog.default_regulation()[0]["interval_km"] = 1
    again = catalog.get_regulation("Kia", "Rio", "IV 2017-2020", "1.6 (123 л.с.)")
    assert again["works"][0]["interval_km"] != 1
    assert catalog.default_regulation()[0]["interval_km"] != 1


# Проверка выборки app/data/catalog.json


@pytest.fixture(scope="module")
def sample():
    with open(catalog.SAMPLE_PATH, encoding="utf-8") as f:
        return json.load(f)


def all_works(sample):
    """Все работы выборки вместе с общим регламентом."""
    yield from sample["default_regulation"]
    for models in sample["brands"].values():
        for generations in models.values():
            for generation in generations:
                for group in generation["engine_groups"]:
                    yield from group["works"]


def test_sample_only_selected_models(sample):
    """В выборке ровно модели из списка."""
    models = {(b, m) for b, ms in sample["brands"].items() for m in ms}
    assert models == {(b, m) for b, ms in SELECTION.items() for m in ms}
    assert len(models) == 74


def test_sample_generations(sample):
    """Только поколения 2010+, у каждого есть источник, годы и двигатели."""
    generations = [
        g for ms in sample["brands"].values() for gs in ms.values() for g in gs
    ]
    assert len(generations) == 286
    for g in generations:
        assert g["year_to"] >= MIN_YEAR_TO
        assert g["year_from"] <= g["year_to"]
        assert g["source"].startswith("https://")
        assert g["engine_groups"]
        for group in g["engine_groups"]:
            assert group["engines"]
            assert group["works"]


def test_sample_works(sample):
    """Коды из таблицы кодов, категории из CATEGORIES, интервалы положительные."""
    codes = set(WORK_CODES.values())
    for w in all_works(sample):
        assert w["code"] in codes
        assert WORK_CODES[w["title"]] == w["code"]
        assert w["category"] in CATEGORIES
        assert w["interval_km"] is not None or w["interval_months"] is not None
        for interval in (w["interval_km"], w["interval_months"]):
            assert interval is None or interval > 0


def test_sample_default_regulation_covers_all_works(sample):
    """Общий регламент — по одной записи на каждую работу."""
    codes = [w["code"] for w in sample["default_regulation"]]
    assert codes == list(WORK_CODES.values())
