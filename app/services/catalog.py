"""Справочник моделей: марки, модели, поколения, двигатели и регламент ТО.

Справочник собирает скрипт scripts/build_catalog.py. Берём полный файл
data/catalog_full.json, если он есть, иначе выборку app/data/catalog.json.
Поиск по названиям — без учёта регистра.
"""

import json
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FULL_PATH = ROOT / "data" / "catalog_full.json"
SAMPLE_PATH = ROOT / "app" / "data" / "catalog.json"


def catalog_path() -> Path:
    """Путь к справочнику: полный файл, если есть, иначе выборка."""
    return FULL_PATH if FULL_PATH.exists() else SAMPLE_PATH


@cache
def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_catalog() -> dict:
    """Справочник целиком (файл читается один раз)."""
    return _read(catalog_path())


def _same(a: str, b: str) -> bool:
    """Названия совпадают без учёта регистра."""
    return a.casefold() == b.casefold()


def _get(items: dict, name: str, empty):
    """Значение словаря по названию без учёта регистра; не нашли — empty."""
    return next((v for k, v in items.items() if _same(k, name)), empty)


def _models(brand: str) -> dict:
    return _get(load_catalog()["brands"], brand, {})


def _generations(brand: str, model: str) -> list[dict]:
    return _get(_models(brand), model, [])


def _generation(brand: str, model: str, generation: str) -> dict | None:
    generations = _generations(brand, model)
    return next((g for g in generations if _same(g["name"], generation)), None)


def list_brands() -> list[str]:
    """Марки справочника."""
    return list(load_catalog()["brands"])


def list_models(brand: str) -> list[str]:
    """Модели марки; для неизвестной марки — пустой список."""
    return list(_models(brand))


def list_generations(brand: str, model: str) -> list[dict]:
    """Поколения модели: название, годы выпуска и ссылка на источник."""
    return [
        {
            "name": g["name"],
            "year_from": g["year_from"],
            "year_to": g["year_to"],
            "source": g["source"],
        }
        for g in _generations(brand, model)
    ]


def list_engines(brand: str, model: str, generation: str) -> list[str]:
    """Двигатели поколения без повторов, в порядке справочника."""
    found = _generation(brand, model, generation)
    if found is None:
        return []
    engines = []
    for group in found["engine_groups"]:
        for engine in group["engines"]:
            if engine not in engines:
                engines.append(engine)
    return engines


def default_regulation() -> list[dict]:
    """Общий регламент: для каждой работы — самый частый интервал по таблице."""
    return [dict(work) for work in load_catalog()["default_regulation"]]


def get_regulation(brand: str, model: str, generation: str, engine: str) -> dict:
    """Регламент ТО для машины.

    Работы собираем из всех групп, где есть двигатель. Если работа есть
    в нескольких группах, берём группу с меньшим числом двигателей — она точнее.
    Если машина или двигатель не найдены — общий регламент (is_default=True).
    """
    found = _generation(brand, model, generation)
    groups = []
    if found is not None:
        groups = [
            group
            for group in found["engine_groups"]
            if any(_same(e, engine) for e in group["engines"])
        ]
    if not groups:
        return {"source": None, "is_default": True, "works": default_regulation()}

    works = {}
    # sorted устойчивая: при равном числе двигателей — порядок справочника
    for group in sorted(groups, key=lambda g: len(g["engines"])):
        for work in group["works"]:
            works.setdefault(work["code"], dict(work))
    return {
        "source": found["source"],
        "is_default": False,
        "works": list(works.values()),
    }
