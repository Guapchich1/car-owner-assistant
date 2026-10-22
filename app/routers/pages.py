"""Страницы приложения по прототипу docs/design/prototype/.

Пока базы данных нет, страницы показывают пример из app/data/demo.json.
"""

import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent.parent

router = APIRouter()
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Категории работ в журнале
WORK_TYPES = {
    "oil": "Масло и фильтр",
    "filters": "Фильтры",
    "brakes": "Тормоза",
    "tires": "Шины и диски",
    "diagnostic": "Диагностика",
    "other": "Прочие работы",
}

NBSP = " "


def format_number(value: float) -> str:
    """Число с разделителем разрядов: 84 500."""
    if value == int(value):
        return f"{int(value):,}".replace(",", NBSP)
    return f"{value:,.2f}".replace(",", NBSP).replace(".", ",")


def format_km(value: float) -> str:
    return f"{format_number(value)}{NBSP}км"


def format_money(value: float) -> str:
    return f"{format_number(value)}{NBSP}₽"


def format_date(value: str) -> str:
    """2026-10-05 → 05.10.2026."""
    return date.fromisoformat(value).strftime("%d.%m.%Y")


templates.env.filters["km"] = format_km
templates.env.filters["money"] = format_money
templates.env.filters["ru_date"] = format_date


def load_demo() -> dict:
    """Пример машины и журнала работ."""
    with open(APP_DIR / "data" / "demo.json", encoding="utf-8") as file:
        return json.load(file)


def sorted_records(records: list[dict]) -> list[dict]:
    """Записи журнала от новых к старым."""
    return sorted(records, key=lambda r: (r["date"], r["mileage"]), reverse=True)


def oil_due(vehicle: dict, records: list[dict]) -> int:
    """Пробег следующей замены масла: от последней замены плюс интервал."""
    oil = [r["mileage"] for r in records if r["type"] == "oil"]
    return (max(oil) if oil else 0) + vehicle["oil_interval"]


def due_text(target: int, mileage: int) -> str:
    left = target - mileage
    if left > 0:
        return f"Через {format_km(left)}"
    if left == 0:
        return "Пора выполнить ТО"
    return f"Просрочено на {format_km(-left)}"


def maintenance_jobs(vehicle: dict, records: list[dict]) -> list[dict]:
    """Работы плана ТО. Кроме масла — демонстрационные сроки."""
    mileage = vehicle["mileage"]
    jobs = [
        (
            "Масло и масляный фильтр",
            oil_due(vehicle, records),
            "wrench",
            f"Интервал {format_km(vehicle['oil_interval'])}. Можно настроить по регламенту.",
        ),
        (
            "Воздушный и салонный фильтры",
            90000,
            "parts",
            "Пример планового пробега. Состояние зависит от условий эксплуатации.",
        ),
        (
            "Проверка тормозов",
            90000,
            "wheel",
            "Проверка износа колодок, дисков и состояния тормозной жидкости.",
        ),
        (
            "Шины и давление",
            None,
            "wheel",
            "Сезонная проверка состояния, давления и равномерности износа.",
        ),
        (
            "Аккумулятор",
            None,
            "bolt",
            "Диагностика перед холодным сезоном или при затруднённом запуске.",
        ),
        (
            "Диагностика электроники",
            None,
            "engine",
            "При ошибках на панели или изменениях в поведении автомобиля.",
        ),
    ]
    return [
        {
            "title": title,
            "target": target,
            "icon": icon,
            "text": text,
            "due": due_text(target, mileage) if target else "Проверьте по сезону",
            "soon": bool(target) and target - mileage < 2000,
        }
        for title, target, icon, text in jobs
    ]


def page_context(active_nav: str) -> dict:
    """Общие данные для всех страниц: машина, журнал, сводка."""
    demo = load_demo()
    vehicle = demo["vehicle"]
    records = sorted_records(demo["records"])
    month = date.today().isoformat()[:7]
    due = oil_due(vehicle, records)
    return {
        "active_nav": active_nav,
        "vehicle": vehicle,
        "records": records,
        "work_types": WORK_TYPES,
        "oil_due": due,
        "oil_due_text": due_text(due, vehicle["mileage"]),
        "oil_soon": due - vehicle["mileage"] < 2000,
        "spent_total": sum(r["amount"] for r in records),
        "spent_month": sum(r["amount"] for r in records if r["date"].startswith(month)),
    }


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Главная: сводка по машине."""
    context = page_context("car")
    records = context["records"]
    recent = list(reversed(records[:8]))
    top = max((r["amount"] for r in recent), default=1) or 1
    context["timeline"] = list(reversed(records[:4]))
    context["chart"] = [max(10, round(r["amount"] / top * 100)) for r in recent]
    context["jobs"] = maintenance_jobs(context["vehicle"], records)[:2]
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request, q: str = "", type: str = "all", year: str = "all"
) -> HTMLResponse:
    """История обслуживания с поиском и фильтрами."""
    context = page_context("history")
    records = context["records"]
    query = q.strip().lower()
    found = [
        r
        for r in records
        if (type == "all" or r["type"] == type)
        and (year == "all" or r["date"].startswith(year))
        and (query in r["title"].lower() or query in r["service"].lower())
    ]
    context.update(
        found=found,
        years=sorted({r["date"][:4] for r in records}, reverse=True),
        q=q,
        selected_type=type,
        selected_year=year,
    )
    return templates.TemplateResponse(request, "history.html", context)


@router.get("/maintenance", response_class=HTMLResponse)
def maintenance(request: Request) -> HTMLResponse:
    """План ТО."""
    context = page_context("maintenance")
    context["jobs"] = maintenance_jobs(context["vehicle"], context["records"])
    return templates.TemplateResponse(request, "maintenance.html", context)


@router.get("/expenses", response_class=HTMLResponse)
def expenses(request: Request) -> HTMLResponse:
    """Расходы по категориям работ."""
    context = page_context("expenses")
    groups = []
    for key, title in WORK_TYPES.items():
        amount = sum(r["amount"] for r in context["records"] if r["type"] == key)
        if amount:
            groups.append({"title": title, "amount": amount})
    groups.sort(key=lambda g: g["amount"], reverse=True)
    top = max((g["amount"] for g in groups), default=1)
    for group in groups:
        group["width"] = round(group["amount"] / top * 100)
    context["groups"] = groups
    return templates.TemplateResponse(request, "expenses.html", context)


@router.get("/assistant", response_class=HTMLResponse)
def assistant(request: Request, q: str = "") -> HTMLResponse:
    """ИИ-помощник. Модель пока не подключена."""
    context = page_context("assistant")
    context["question"] = q.strip()
    return templates.TemplateResponse(request, "assistant.html", context)
