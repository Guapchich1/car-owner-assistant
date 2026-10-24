"""Сборка справочника моделей из таблицы регламентов ТО.

Читает data/regulations_full.csv и создаёт:
- app/data/catalog.json — выборка популярных моделей (в репозитории);
- data/catalog_full.json — все модели (не в git).

Запуск из папки проекта: python scripts/build_catalog.py
"""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "regulations_full.csv"
SAMPLE_PATH = ROOT / "app" / "data" / "catalog.json"
FULL_PATH = ROOT / "data" / "catalog_full.json"

# Выборка — поколения, выпуск которых закончился в этом году или позже
MIN_YEAR_TO = 2010

# Модели для выборки: марка → модели
SELECTION = {
    "Lada": [
        "Granta",
        "Vesta",
        "Largus",
        "XRAY",
        "Priora",
        "Kalina",
        "2114",
        "2121 (4x4)",
    ],
    "Haval": ["Jolion", "F7", "Dargo", "M6"],
    "Chery": ["Tiggo 4", "Tiggo 4 Pro", "Tiggo 7 Pro", "Tiggo 8 Pro"],
    "Geely": ["Coolray", "Monjaro", "Atlas", "Atlas Pro"],
    "Changan": ["CS35 Plus", "CS55 Plus", "UNI-K"],
    "Tank": ["300"],
    "Kia": [
        "Rio",
        "Ceed",
        "Cerato",
        "Optima",
        "K5",
        "Sportage",
        "Sorento",
        "Seltos",
        "Picanto",
    ],
    "Hyundai": ["Solaris", "Elantra", "Sonata", "Creta", "Tucson", "Santa Fe"],
    "Toyota": ["Camry", "RAV4"],
    "Volkswagen": ["Polo", "Jetta", "Passat", "Tiguan"],
    "Skoda": ["Rapid", "Octavia", "Kodiaq"],
    "Renault": ["Logan", "Sandero", "Duster", "Kaptur", "Arkana"],
    "Nissan": ["Almera", "Terrano", "Qashqai", "X-Trail"],
    "Mitsubishi": ["Lancer", "ASX", "Outlander"],
    "Mazda": ["3", "6", "CX-5"],
    "Chevrolet": ["Niva", "Lacetti", "Aveo", "Cruze"],
    "Daewoo": ["Nexia", "Matiz"],
    "Ford": ["Focus"],
    "Opel": ["Astra"],
    "УАЗ": ["Патриот"],
    "Lifan": ["X60"],
    "Exeed": ["TXL"],
}

# Коды работ: название в таблице → код
WORK_CODES = {
    "Замена масла и масляного фильтра": "engine_oil",
    "Замена масла в АКПП": "atf",
    "Замена масла в МКПП и редукторе": "mtf",
    "Замена воздушного фильтра": "air_filter",
    "Замена салонного фильтра": "cabin_filter",
    "Замена топливного фильтра": "fuel_filter",
    "Замена фильтра кондиционера": "ac_filter",
    "Замена свечей зажигания": "spark_plugs",
    "Замена свечей накала": "glow_plugs",
    "Замена ремня ГРМ": "timing_belt",
    "Замена комплекта ремня ГРМ": "timing_belt_kit",
    "Замена цепи ГРМ": "timing_chain",
    "Замена приводного ремня": "drive_belt",
    "Замена охлаждающей жидкости (антифриза)": "coolant",
    "Замена жидкости ГУР": "power_steering_fluid",
    "Замена тормозной жидкости": "brake_fluid",
    "Замена тормозных колодок": "brake_pads",
    "Замена тормозных дисков": "brake_discs",
    "Очистка топливной системы": "fuel_system_cleaning",
}

# Годы выпуска в конце названия поколения: «I Рестайлинг 2018-2026»
YEARS_RE = re.compile(r"(\d{4})-(\d{4})$")


def read_rows(path: Path) -> list[dict]:
    """Читает таблицу регламентов (разделитель «;», UTF-8 с BOM)."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def parse_interval(value: str) -> int | None:
    """Интервал из ячейки таблицы; пустая ячейка — None."""
    value = value.strip()
    return int(value) if value else None


def parse_years(generation: str) -> tuple[int, int]:
    """Годы выпуска из названия поколения."""
    match = YEARS_RE.search(generation)
    if not match:
        raise ValueError(f"Нет годов выпуска в названии поколения: {generation!r}")
    return int(match.group(1)), int(match.group(2))


def make_work(row: dict) -> dict:
    """Работа из строки таблицы."""
    title = row["работа"]
    if title not in WORK_CODES:
        raise ValueError(f"Нет кода для работы: {title!r}")
    return {
        "code": WORK_CODES[title],
        "title": title,
        "category": row["категория"],
        "interval_km": parse_interval(row["интервал_км"]),
        "interval_months": parse_interval(row["интервал_мес"]),
    }


def smaller(a: int | None, b: int | None) -> int | None:
    """Меньший из двух интервалов; пустой интервал не учитываем."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def build(rows: list[dict]) -> tuple[dict, list[str]]:
    """Справочник по всем строкам таблицы и список предупреждений.

    Структура: марка → модель → поколения → группы двигателей → работы.
    Порядок — как в таблице.
    """
    brands: dict = {}
    generations: dict = {}  # (марка, модель, поколение) → поколение
    groups: dict = {}  # (марка, модель, поколение, двигатели) → работы по коду
    warnings = []
    for row in rows:
        brand, model, name = row["марка"], row["модель"], row["поколение"]
        generation = generations.get((brand, model, name))
        if generation is None:
            year_from, year_to = parse_years(name)
            generation = {
                "name": name,
                "year_from": year_from,
                "year_to": year_to,
                "source": row["источник"],
                "engine_groups": [],
            }
            generations[(brand, model, name)] = generation
            brands.setdefault(brand, {}).setdefault(model, []).append(generation)

        key = (brand, model, name, row["двигатели"])
        if key not in groups:
            groups[key] = {}
            generation["engine_groups"].append(
                {
                    "engines": row["двигатели"].split("; "),
                    "works": groups[key],
                }
            )
        work = make_work(row)
        works = groups[key]
        same = works.get(work["code"])
        if same is None:
            works[work["code"]] = work
            continue
        # Одна работа дважды в группе — берём меньшие интервалы
        warnings.append(
            f"Повтор работы «{work['title']}»: {brand} {model} {name}, "
            f"двигатели {row['двигатели']}"
        )
        same["interval_km"] = smaller(same["interval_km"], work["interval_km"])
        same["interval_months"] = smaller(
            same["interval_months"], work["interval_months"]
        )

    for generation in generations.values():
        for group in generation["engine_groups"]:
            group["works"] = list(group["works"].values())
    return brands, warnings


Pair = tuple[int | None, int | None]


def most_common(pairs: list[Pair]) -> Pair:
    """Самая частая пара (км, месяцы); при равенстве — меньшая.

    Пустой интервал при сравнении считаем больше любого числа.
    """
    counts = Counter(pairs)

    def order(pair: Pair):
        km, months = pair
        return (-counts[pair], km is None, km or 0, months is None, months or 0)

    return min(counts, key=order)


def default_regulation(rows: list[dict]) -> list[dict]:
    """Общий регламент: для каждой работы — самая частая пара интервалов."""
    pairs = defaultdict(list)
    categories = {}
    for row in rows:
        title = row["работа"]
        categories.setdefault(title, row["категория"])
        pairs[title].append(
            (parse_interval(row["интервал_км"]), parse_interval(row["интервал_мес"]))
        )
    result = []
    for title, code in WORK_CODES.items():
        if title not in categories:
            continue
        km, months = most_common(pairs[title])
        result.append(
            {
                "code": code,
                "title": title,
                "category": categories[title],
                "interval_km": km,
                "interval_months": months,
            }
        )
    return result


def select(brands: dict) -> dict:
    """Выборка: модели из SELECTION, поколения с концом выпуска от MIN_YEAR_TO."""
    result = {}
    for brand, models in brands.items():
        for model, generations in models.items():
            if model not in SELECTION.get(brand, []):
                continue
            recent = [g for g in generations if g["year_to"] >= MIN_YEAR_TO]
            if recent:
                result.setdefault(brand, {})[model] = recent
    return result


def count(brands: dict) -> tuple[int, int]:
    """Число моделей и поколений в справочнике."""
    models = [g for m in brands.values() for g in m.values()]
    return len(models), sum(len(g) for g in models)


def write_json(path: Path, data: dict) -> None:
    """Пишет JSON так, чтобы повторный запуск давал тот же файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main(
    csv_path: Path = CSV_PATH,
    sample_path: Path = SAMPLE_PATH,
    full_path: Path = FULL_PATH,
) -> None:
    """Собирает оба файла справочника и печатает сводку."""
    rows = read_rows(csv_path)
    brands, warnings = build(rows)
    default = default_regulation(rows)
    sample = select(brands)
    write_json(sample_path, {"default_regulation": default, "brands": sample})
    write_json(full_path, {"default_regulation": default, "brands": brands})

    for warning in warnings:
        print(f"Предупреждение: {warning}")
    no_months = sum(1 for row in rows if not row["интервал_мес"].strip())
    no_km = sum(1 for row in rows if not row["интервал_км"].strip())
    print(f"Строк в таблице: {len(rows)}")
    print(f"Без интервала в км: {no_km}, без интервала в месяцах: {no_months}")
    print(f"Повторов работ: {len(warnings)}")
    print("Выборка {}: моделей {}, поколений {}".format(sample_path, *count(sample)))
    print("Полный {}: моделей {}, поколений {}".format(full_path, *count(brands)))


if __name__ == "__main__":
    main()
