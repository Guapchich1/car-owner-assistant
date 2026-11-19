"""Страницы приложения по прототипу docs/design/prototype/.

Карточка машины — из базы. Журнал, расходы и план ТО пока показывают
пример из app/data/demo.json.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import GEARBOXES, User
from app.services.cars import get_car, to_msk
from app.services.survey import car_mismatch_warnings
from app.services.users import (
    SESSION_COOKIE,
    LoginRequired,
    csrf_token_for,
    tokens_match,
    user_by_token,
)

APP_DIR = Path(__file__).resolve().parent.parent

router = APIRouter()
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Сессия базы для обработчика: открывается на время запроса
DbSession = Annotated[Session, Depends(get_session)]

CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def optional_user(request: Request, session: Session) -> User | None:
    """Пользователь действующей сессии из cookie; нет сессии — None."""
    token = request.cookies.get(SESSION_COOKIE)
    return user_by_token(session, token) if token else None


def get_current_user(request: Request, session: DbSession) -> User:
    """Вошедший пользователь; без входа — переход на /login. Токен для форм — в state."""
    user = optional_user(request, session)
    if user is None:
        raise LoginRequired
    request.state.csrf_token = csrf_token_for(request.cookies[SESSION_COOKIE])
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def verify_csrf(request: Request, user: CurrentUser) -> None:
    """Запрос, меняющий данные, должен нести токен этой сессии; иначе — 403.

    HTMX передаёт токен заголовком, обычная форма — скрытым полем.
    """
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get(CSRF_HEADER)
    if not sent:
        sent = (await request.form()).get(CSRF_FIELD)
    if not tokens_match(request.state.csrf_token, sent):
        raise HTTPException(status_code=403)


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


def format_msk_datetime(value: datetime) -> str:
    """Время из базы (UTC) по Москве: 26.10.2026 14:05."""
    return to_msk(value).strftime("%d.%m.%Y %H:%M")


def static_version(path: str) -> int:
    """Версия файла из app/static/ — время его изменения. После правки файла
    адрес меняется, и браузер загружает файл заново, а не берёт из кэша."""
    return int((APP_DIR / "static" / path).stat().st_mtime)


templates.env.filters["km"] = format_km
templates.env.filters["money"] = format_money
templates.env.filters["ru_date"] = format_date
templates.env.filters["msk_datetime"] = format_msk_datetime
templates.env.globals["gearboxes"] = GEARBOXES
templates.env.globals["static_version"] = static_version


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


def page_context(active_nav: str, session: Session, user: User) -> dict:
    """Общие данные для всех страниц: пользователь, его машина, пример журнала и сводки."""
    demo = load_demo()
    vehicle = demo["vehicle"]
    records = sorted_records(demo["records"])
    month = date.today().isoformat()[:7]
    due = oil_due(vehicle, records)
    return {
        "active_nav": active_nav,
        "user": user,
        "car": get_car(session, user),
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
def index(
    request: Request, session: DbSession, user: CurrentUser, saved: int = 0
) -> HTMLResponse:
    """Главная: сводка по машине; без машины — приглашение её добавить."""
    context = page_context("car", session, user)
    context["saved"] = bool(saved)
    records = context["records"]
    recent = list(reversed(records[:8]))
    top = max((r["amount"] for r in recent), default=1) or 1
    context["timeline"] = list(reversed(records[:4]))
    context["chart"] = [max(10, round(r["amount"] / top * 100)) for r in recent]
    context["jobs"] = maintenance_jobs(context["vehicle"], records)[:2]
    # Плитка «Текущий пробег» — из машины в базе; остальное пока по примеру:
    # журнал — пример, и сроки от реального пробега были бы неверными
    car = context["car"]
    context["current_mileage"] = car.mileage if car else context["vehicle"]["mileage"]
    # Сразу после правки машины: её новые данные могли разойтись с анкетой
    context["car_warnings"] = (
        car_mismatch_warnings(car.survey, car) if saved and car and car.survey else []
    )
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    q: str = "",
    type: str = "all",
    year: str = "all",
) -> HTMLResponse:
    """История обслуживания с поиском и фильтрами."""
    context = page_context("history", session, user)
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
def maintenance(
    request: Request, session: DbSession, user: CurrentUser
) -> HTMLResponse:
    """План ТО."""
    context = page_context("maintenance", session, user)
    context["jobs"] = maintenance_jobs(context["vehicle"], context["records"])
    return templates.TemplateResponse(request, "maintenance.html", context)


@router.get("/expenses", response_class=HTMLResponse)
def expenses(request: Request, session: DbSession, user: CurrentUser) -> HTMLResponse:
    """Расходы по категориям работ."""
    context = page_context("expenses", session, user)
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
def assistant(
    request: Request, session: DbSession, user: CurrentUser, q: str = ""
) -> HTMLResponse:
    """ИИ-помощник. Модель пока не подключена."""
    context = page_context("assistant", session, user)
    context["question"] = q.strip()
    return templates.TemplateResponse(request, "assistant.html", context)
