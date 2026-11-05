"""Машина: добавление вручную из справочника, редактирование и удаление."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models import Car
from app.routers.pages import DbSession, page_context, templates
from app.services.cars import (
    FORM_FIELDS,
    catalog_mismatches,
    form_options,
    get_car,
    validate_car_form,
    values_from_car,
)

router = APIRouter(prefix="/cars")


def get_target_car(car_id: int, session: DbSession) -> Car:
    """Машина из адреса страницы; нет такой — 404."""
    car = session.get(Car, car_id)
    if car is None:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    return car


TargetCar = Annotated[Car, Depends(get_target_car)]


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
    context.update(form_action="/cars/new", submit_label="Сохранить машину")
    return templates.TemplateResponse(
        request, "car_new.html", context, status_code=status_code
    )


def render_edit_form(
    request: Request,
    session: Session,
    car: Car,
    values: dict[str, str],
    errors: dict[str, str],
    status_code: int = 200,
) -> HTMLResponse:
    context = page_context("car", session)
    context.update(fields_context(values, errors))
    context.update(
        target_car=car,
        mismatches=catalog_mismatches(car),
        form_action=f"/cars/{car.id}/edit",
        submit_label="Сохранить изменения",
    )
    return templates.TemplateResponse(
        request, "car_edit.html", context, status_code=status_code
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
    data, errors = validate_car_form(values)
    if errors:
        return render_form(request, session, values, errors, status_code=422)
    car = Car(**data)
    session.add(car)
    session.commit()
    # Сразу предлагаем анкету: её можно пропустить и вернуться позже из карточки
    return RedirectResponse(f"/cars/{car.id}/survey/edit", status_code=303)


@router.get("/new/fields", response_class=HTMLResponse)
def car_fields(request: Request) -> HTMLResponse:
    """Блок зависимых списков для HTMX: обновляется при выборе в любом списке."""
    context = fields_context(form_values(request.query_params), {})
    return templates.TemplateResponse(request, "_car_fields.html", context)


@router.get("/{car_id}/edit", response_class=HTMLResponse)
def edit_car(request: Request, session: DbSession, car: TargetCar) -> HTMLResponse:
    """Форма редактирования с сохранёнными характеристиками машины."""
    return render_edit_form(request, session, car, values_from_car(car), {})


@router.post("/{car_id}/edit", response_class=HTMLResponse)
def update_car(
    request: Request,
    session: DbSession,
    car: TargetCar,
    brand: FormField = "",
    model: FormField = "",
    generation: FormField = "",
    engine: FormField = "",
    year: FormField = "",
    mileage: FormField = "",
    gearbox: FormField = "",
) -> Response:
    """Сохраняет изменения. При ошибке форма возвращается с введёнными данными."""
    values = {
        "brand": brand,
        "model": model,
        "generation": generation,
        "engine": engine,
        "year": year,
        "mileage": mileage,
        "gearbox": gearbox,
    }
    data, errors = validate_car_form(values)
    if errors:
        return render_edit_form(request, session, car, values, errors, status_code=422)
    for name, value in data.items():
        setattr(car, name, value)
    session.commit()
    return RedirectResponse("/?saved=1", status_code=303)


@router.post("/{car_id}/delete")
def delete_car(session: DbSession, car: TargetCar) -> Response:
    """Удаляет машину с журналом и анкетой. Подтверждение спрашивает кнопка (hx-confirm)."""
    session.delete(car)
    session.commit()
    # HTMX выполнит переход сам: обычный редирект он отрисовал бы внутри страницы
    return Response(status_code=204, headers={"HX-Redirect": "/"})
