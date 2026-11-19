"""Журнал работ: добавление, изменение и удаление записи, обновление пробега машины."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models import ServiceRecord, User
from app.routers.cars import FormField, TargetCar
from app.routers.pages import CurrentUser, DbSession, page_context, templates
from app.services.records import (
    MAX_NOTE,
    MAX_SERVICE,
    MAX_TITLE,
    offers_mileage_update,
    user_record,
    validate_record_form,
    values_from_record,
)

router = APIRouter()


def get_target_record(
    record_id: int, session: DbSession, user: CurrentUser
) -> ServiceRecord:
    """Запись из адреса страницы, если она в журнале машины вошедшего; иначе — 404.

    Чужая и несуществующая запись неотличимы. Все адреса с номером записи
    получают её только отсюда.
    """
    record = user_record(session, user, record_id)
    if record is None:
        raise HTTPException(status_code=404)
    return record


TargetRecord = Annotated[ServiceRecord, Depends(get_target_record)]


def render_record_form(
    request: Request,
    session: Session,
    user: User,
    template: str,
    values: dict[str, str],
    errors: dict[str, str],
    form_action: str,
    record: ServiceRecord | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    context = page_context("history", session, user)
    context.update(
        values=values,
        errors=errors,
        form_action=form_action,
        record=record,
        max_title=MAX_TITLE,
        max_service=MAX_SERVICE,
        max_note=MAX_NOTE,
    )
    return templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


def record_values(
    date: str,
    mileage: str,
    category: str,
    title: str,
    amount: str,
    service: str,
    note: str,
) -> dict[str, str]:
    return {
        "date": date,
        "mileage": mileage,
        "category": category,
        "title": title,
        "amount": amount,
        "service": service,
        "note": note,
    }


@router.get("/cars/{car_id}/records/new", response_class=HTMLResponse)
def new_record(
    request: Request, session: DbSession, user: CurrentUser, car: TargetCar
) -> HTMLResponse:
    """Форма новой записи журнала."""
    values = record_values("", "", "", "", "", "", "")
    return render_record_form(
        request,
        session,
        user,
        "record_new.html",
        values,
        {},
        f"/cars/{car.id}/records/new",
    )


@router.post("/cars/{car_id}/records/new", response_class=HTMLResponse)
def create_record(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    car: TargetCar,
    date: FormField = "",
    mileage: FormField = "",
    category: FormField = "",
    title: FormField = "",
    amount: FormField = "",
    service: FormField = "",
    note: FormField = "",
) -> Response:
    """Сохраняет запись. При ошибке форма возвращается с введёнными данными."""
    values = record_values(date, mileage, category, title, amount, service, note)
    data, errors = validate_record_form(values, car)
    if errors:
        return render_record_form(
            request,
            session,
            user,
            "record_new.html",
            values,
            errors,
            f"/cars/{car.id}/records/new",
            status_code=422,
        )
    record = ServiceRecord(**data, car=car, source="manual")
    session.add(record)
    session.commit()
    return RedirectResponse(f"/history?saved={record.id}", status_code=303)


@router.get("/records/{record_id}/edit", response_class=HTMLResponse)
def edit_record(
    request: Request, session: DbSession, user: CurrentUser, record: TargetRecord
) -> HTMLResponse:
    """Форма изменения записи с сохранёнными значениями."""
    return render_record_form(
        request,
        session,
        user,
        "record_edit.html",
        values_from_record(record),
        {},
        f"/records/{record.id}/edit",
        record=record,
    )


@router.post("/records/{record_id}/edit", response_class=HTMLResponse)
def update_record(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    record: TargetRecord,
    date: FormField = "",
    mileage: FormField = "",
    category: FormField = "",
    title: FormField = "",
    amount: FormField = "",
    service: FormField = "",
    note: FormField = "",
) -> Response:
    """Сохраняет изменения. Источник записи не меняется."""
    values = record_values(date, mileage, category, title, amount, service, note)
    data, errors = validate_record_form(values, record.car, record)
    if errors:
        return render_record_form(
            request,
            session,
            user,
            "record_edit.html",
            values,
            errors,
            f"/records/{record.id}/edit",
            record=record,
            status_code=422,
        )
    for name, value in data.items():
        setattr(record, name, value)
    session.commit()
    return RedirectResponse(f"/history?saved={record.id}", status_code=303)


@router.post("/records/{record_id}/delete")
def delete_record(session: DbSession, record: TargetRecord) -> Response:
    """Удаляет запись. Подтверждение спрашивает кнопка (hx-confirm)."""
    session.delete(record)
    session.commit()
    # HTMX выполнит переход сам: обычный редирект он отрисовал бы внутри страницы
    return Response(status_code=204, headers={"HX-Redirect": "/history"})


@router.post("/records/{record_id}/update-mileage")
def update_car_mileage(session: DbSession, record: TargetRecord) -> Response:
    """Пробег машины — из записи, если запись по-прежнему новее и пробег больше.

    Иначе ничего не меняем: форму могли отправить повторно или после правок.
    """
    car = record.car
    if offers_mileage_update(record, car):
        car.mileage = record.mileage
        car.mileage_date = record.date
        session.commit()
        return RedirectResponse("/history?mileage_updated=1", status_code=303)
    return RedirectResponse("/history", status_code=303)
