import json

import pytest

from scripts import build_catalog

HEADER = "марка;модель;поколение;двигатели;работа;категория;интервал_км;интервал_мес;источник"
SRC = "https://example.ru/reglament"

# Таблица в формате data/regulations_full.csv (без настоящих данных)
ROWS = [
    f'Kia;Rio;IV 2017-2020;"1.4 (100 л.с.); 1.6 (123 л.с.)";Замена масла и масляного фильтра;oil;15000;;{SRC}',
    f'Kia;Rio;IV 2017-2020;"1.4 (100 л.с.); 1.6 (123 л.с.)";Замена воздушного фильтра;filters;30000;24;{SRC}',
    f"Kia;Rio;IV 2017-2020;1.6 (123 л.с.);Замена свечей зажигания;other;45000;36;{SRC}",
    f"Kia;Rio;II 2005-2009;1.4 (97 л.с.);Замена масла и масляного фильтра;oil;10000;12;{SRC}",
    f"Kia;Soul;I 2008-2011;1.6 (124 л.с.);Замена масла и масляного фильтра;oil;15000;12;{SRC}",
    f"Kia;Soul;I 2008-2011;1.6 (124 л.с.);Замена воздушного фильтра;filters;30000;24;{SRC}",
    f"Kia;Soul;I 2008-2011;1.6 (124 л.с.);Замена воздушного фильтра;filters;20000;36;{SRC}",
]


@pytest.fixture
def csv_path(tmp_path):
    """Маленькая таблица регламентов, как в data/: «;» и UTF-8 с BOM."""
    path = tmp_path / "regulations.csv"
    path.write_text("\n".join([HEADER, *ROWS]) + "\n", encoding="utf-8-sig")
    return path


def test_build_structure(csv_path):
    """Марка → модель → поколение → группы двигателей → работы."""
    brands, _ = build_catalog.build(build_catalog.read_rows(csv_path))

    assert list(brands) == ["Kia"]
    assert list(brands["Kia"]) == ["Rio", "Soul"]
    rio4, rio2 = brands["Kia"]["Rio"]
    assert rio4["name"] == "IV 2017-2020"
    assert (rio4["year_from"], rio4["year_to"]) == (2017, 2020)
    assert rio4["source"] == SRC
    assert rio2["name"] == "II 2005-2009"

    general, plugs = rio4["engine_groups"]
    assert general["engines"] == ["1.4 (100 л.с.)", "1.6 (123 л.с.)"]
    assert general["works"] == [
        {
            "code": "engine_oil",
            "title": "Замена масла и масляного фильтра",
            "category": "oil",
            "interval_km": 15000,
            "interval_months": None,
        },
        {
            "code": "air_filter",
            "title": "Замена воздушного фильтра",
            "category": "filters",
            "interval_km": 30000,
            "interval_months": 24,
        },
    ]
    assert plugs["engines"] == ["1.6 (123 л.с.)"]
    assert [w["code"] for w in plugs["works"]] == ["spark_plugs"]


def test_repeated_work_takes_smaller_intervals(csv_path):
    """Повтор работы в группе — меньшие интервалы и предупреждение."""
    brands, warnings = build_catalog.build(build_catalog.read_rows(csv_path))

    works = brands["Kia"]["Soul"][0]["engine_groups"][0]["works"]
    air = [w for w in works if w["code"] == "air_filter"]
    assert len(air) == 1
    assert air[0]["interval_km"] == 20000
    assert air[0]["interval_months"] == 24
    assert len(warnings) == 1
    assert "Kia Soul I 2008-2011" in warnings[0]


def test_unknown_work_is_error():
    """Работа без кода — ошибка, а не молчаливый пропуск."""
    row = dict(zip(HEADER.split(";"), ["Kia"] * 9))
    with pytest.raises(ValueError, match="Нет кода"):
        build_catalog.make_work(row)


def test_generation_without_years_is_error():
    """Поколение без годов в конце названия — ошибка."""
    with pytest.raises(ValueError, match="Нет годов"):
        build_catalog.parse_years("I Рестайлинг")
    assert build_catalog.parse_years("2020-2026") == (2020, 2026)


def test_default_regulation_most_common(csv_path):
    """Общий регламент — самая частая пара (км, месяцы); при равенстве — меньшая."""
    default = build_catalog.default_regulation(build_catalog.read_rows(csv_path))
    by_code = {w["code"]: w for w in default}

    assert [w["code"] for w in default] == ["engine_oil", "air_filter", "spark_plugs"]
    # масло: (15000, нет), (10000, 12), (15000, 12) — по разу, берём меньшую
    assert by_code["engine_oil"]["interval_km"] == 10000
    assert by_code["engine_oil"]["interval_months"] == 12
    # воздушный фильтр: (30000, 24) ×2, (20000, 36) ×1
    assert by_code["air_filter"]["interval_km"] == 30000
    assert by_code["air_filter"]["interval_months"] == 24


def test_most_common_pair():
    """Пара берётся целиком, а не км и месяцы по отдельности."""
    pairs = [(60000, 48), (60000, 48), (30000, 12), (90000, 12)]
    assert build_catalog.most_common(pairs) == (60000, 48)
    assert build_catalog.most_common([(20000, 24), (10000, 36)]) == (10000, 36)
    assert build_catalog.most_common([(10000, None), (10000, 12)]) == (10000, 12)


def test_select_models_and_generations(csv_path):
    """Выборка: только модели из списка и поколения с концом выпуска 2010+."""
    brands, _ = build_catalog.build(build_catalog.read_rows(csv_path))
    sample = build_catalog.select(brands)

    assert list(sample["Kia"]) == ["Rio"]  # Soul нет в списке
    assert [g["name"] for g in sample["Kia"]["Rio"]] == ["IV 2017-2020"]


def test_main_writes_both_files_same_on_rerun(csv_path, tmp_path, capsys):
    """Скрипт создаёт оба файла; повторный запуск даёт тот же результат."""
    sample_path = tmp_path / "app" / "catalog.json"
    full_path = tmp_path / "data" / "catalog_full.json"

    build_catalog.main(csv_path, sample_path, full_path)
    first = sample_path.read_bytes()
    build_catalog.main(csv_path, sample_path, full_path)

    assert sample_path.read_bytes() == first
    assert b"\r\n" not in first
    sample = json.loads(first.decode("utf-8"))
    full = json.loads(full_path.read_text(encoding="utf-8"))
    assert list(sample["brands"]["Kia"]) == ["Rio"]
    assert list(full["brands"]["Kia"]) == ["Rio", "Soul"]
    assert sample["default_regulation"] == full["default_regulation"]

    out = capsys.readouterr().out
    assert "Предупреждение: Повтор работы" in out
    assert "моделей 1, поколений 1" in out
    assert "моделей 2, поколений 3" in out
