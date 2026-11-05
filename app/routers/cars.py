"""Добавление машины вручную: выбор из справочника по шагам."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models import Car
from app.routers.pages import DbSession, page_context, templates
from app.services.cars import FORM_FIELDS, form_options, get_car, validate_car_form

router = APIRouter(prefix="/cars")


def form_values(source) -> dict[str, str]:
    """Значения полей формы; отсутствующие — пустые строки."""
    return {name: source.get(name, "") for name in FORM_FIELDS}


def fields_context(values: dict[str, str], errors: dict[str, str]) -> dict:
    """Данные для блока зависимых списков и года."""
    return {
        "values": values,
        "errors": errors,
        "options": form_options(
            values["brand"], values["model"], values["generation"], values["engine"]
        ),
    }


# Поле формы — строка, по умолчанию пустая: проверку и понятные сообщения
# об ошибках делает validate_car_form, а не FastAPI
FormField = Annotated[str, Form()]


def render_form(
    request: Request,
    session: Session,
    values: dict[str, str],
    errors: dict[str, str],
    status_code: int = 200,
) -> HTMLResponse:
    context = page_context("car", session)
    context.update(fields_context(values, errors))
    return templates.TemplateResponse(
        request, "car_new.html", context, status_code=status_code
    )


@router.get("/new", response_class=HTMLResponse)
def new_car(request: Request, session: DbSession) -> Response:
    """Форма добавления машины. Машина пока одна: если она есть — на главную."""
    if get_car(session):
        return RedirectResponse("/", status_code=303)
    return render_form(request, session, form_values({}), {})


@router.post("/new", response_class=HTMLResponse)
def create_car(
    request: Request,
    session: DbSession,
    brand: FormField = "",
    model: FormField = "",
    generation: FormField = "",
    engine: FormField = "",
    year: FormField = "",
    mileage: FormField = "",
    gearbox: FormField = "",
) -> Response:
    """Сохраняет машину. При ошибке форма возвращается с введёнными данными."""
    if get_car(session):
        return RedirectResponse("/", status_code=303)
    values = {
        "brand": brand,
        "model": model,
        "generation": generation,
        "engine": engine,
        "year": year,
        "mileage": mileage,
        "gearbox": gearbox,
    }
    car, errors = validate_car_form(values)
    if errors:
        return render_form(request, session, values, errors, status_code=422)
    session.add(Car(**car))
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/new/fields", response_class=HTMLResponse)
def car_fields(request: Request) -> HTMLResponse:
    """Блок зависимых списков для HTMX: обновляется при выборе в любом списке."""
    context = fields_context(form_values(request.query_params), {})
    return templates.TemplateResponse(request, "_car_fields.html", context)
