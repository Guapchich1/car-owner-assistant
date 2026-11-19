"""Машина: добавление из справочника или по VIN, редактирование и удаление."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Car, User, msk_today
from app.routers.pages import CurrentUser, DbSession, page_context, templates
from app.services.cars import (
    FORM_FIELDS,
    catalog_mismatches,
    form_options,
    get_car,
    validate_car_form,
    values_from_car,
)
from app.services.records import top_record
from app.services.vin import (
    DAILY_LIMIT,
    FORMAT_ERROR,
    VinError,
    decode_vin,
    match_catalog,
    normalize_vin,
    requests_left,
)

router = APIRouter(prefix="/cars")


def get_target_car(car_id: int, session: DbSession, user: CurrentUser) -> Car:
    """Машина из адреса страницы, если она принадлежит вошедшему; иначе — 404.

    Чужая и несуществующая машина неотличимы, а без владельца не видна никому.
    Все адреса с номером машины получают её только отсюда.
    """
    query = select(Car).where(Car.id == car_id, Car.owner_id == user.id)
    car = session.scalars(query).first()
    if car is None:
        raise HTTPException(status_code=404)
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


def vin_context(session: Session, user: User) -> dict:
    """Остаток запросов по VIN на сегодня — подпись у кнопки «Заполнить по VIN»."""
    return {"vin_left": requests_left(session, user), "vin_limit": DAILY_LIMIT}


# Поле формы — строка, по умолчанию пустая: проверку и понятные сообщения
# об ошибках делает validate_car_form, а не FastAPI
FormField = Annotated[str, Form()]


# Способы добавления машины: адрес /cars/new?by=…
ADD_MODES = ("vin", "manual")
VIN_REQUIRED = "Введите VIN. Если его нет под рукой — добавьте машину вручную."


def render_form(
    request: Request,
    session: Session,
    user: User,
    values: dict[str, str],
    errors: dict[str, str],
    mode: str,
    status_code: int = 200,
    details_hidden: bool = False,
) -> HTMLResponse:
    """Форма добавления: mode — vin (с полем VIN) или manual (только справочник).

    details_hidden — по VIN поля машины скрыты, пока VIN не расшифрован.
    """
    context = page_context("car", session, user)
    context.update(fields_context(values, errors))
    context.update(vin_context(session, user))
    context.update(
        form_action="/cars/new",
        submit_label="Сохранить машину",
        add_mode=mode,
        vin_mode=mode == "vin",
        details_hidden=details_hidden,
    )
    return templates.TemplateResponse(
        request, "car_new.html", context, status_code=status_code
    )


def render_edit_form(
    request: Request,
    session: Session,
    user: User,
    car: Car,
    values: dict[str, str],
    errors: dict[str, str],
    status_code: int = 200,
) -> HTMLResponse:
    context = page_context("car", session, user)
    context.update(fields_context(values, errors))
    context.update(vin_context(session, user))
    context.update(
        vin_mode=True,
        target_car=car,
        mismatches=catalog_mismatches(car),
        form_action=f"/cars/{car.id}/edit",
        submit_label="Сохранить изменения",
    )
    return templates.TemplateResponse(
        request, "car_edit.html", context, status_code=status_code
    )


@router.get("/new", response_class=HTMLResponse)
def new_car(
    request: Request, session: DbSession, user: CurrentUser, by: str = ""
) -> Response:
    """Добавление машины: без by — выбор способа (по VIN или вручную), с by — форма.

    Машина пока одна: если она есть — на главную.
    """
    if get_car(session, user):
        return RedirectResponse("/", status_code=303)
    if by not in ADD_MODES:
        context = page_context("car", session, user)
        context.update(vin_context(session, user))
        return templates.TemplateResponse(request, "car_new_choice.html", context)
    return render_form(
        request, session, user, form_values({}), {}, by, details_hidden=by == "vin"
    )


@router.post("/new", response_class=HTMLResponse)
def create_car(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    by: FormField = "",
    vin: FormField = "",
    brand: FormField = "",
    model: FormField = "",
    generation: FormField = "",
    engine: FormField = "",
    year: FormField = "",
    mileage: FormField = "",
    gearbox: FormField = "",
) -> Response:
    """Сохраняет машину. При ошибке форма возвращается с введёнными данными."""
    if get_car(session, user):
        return RedirectResponse("/", status_code=303)
    values = {
        "vin": vin,
        "brand": brand,
        "model": model,
        "generation": generation,
        "engine": engine,
        "year": year,
        "mileage": mileage,
        "gearbox": gearbox,
    }
    data, errors = validate_car_form(values)
    if by == "vin" and not vin.strip():
        errors["vin"] = VIN_REQUIRED
    if errors:
        # Способ добавления сохраняется; если его нет — по тому, введён ли VIN
        mode = by if by in ADD_MODES else ("vin" if vin.strip() else "manual")
        return render_form(
            request, session, user, values, errors, mode, status_code=422
        )
    car = Car(**data, owner_id=user.id, mileage_date=msk_today())
    session.add(car)
    try:
        session.commit()
    except IntegrityError:
        # Форму отправили дважды одновременно: машина у пользователя уже есть
        session.rollback()
        return RedirectResponse("/", status_code=303)
    # Сразу предлагаем анкету: её можно пропустить и вернуться позже из карточки
    return RedirectResponse(f"/cars/{car.id}/survey/edit", status_code=303)


@router.get("/new/fields", response_class=HTMLResponse)
def car_fields(request: Request) -> HTMLResponse:
    """Блок зависимых списков для HTMX: обновляется при выборе в любом списке."""
    context = fields_context(form_values(request.query_params), {})
    return templates.TemplateResponse(request, "_car_fields.html", context)


@router.post("/new/vin", response_class=HTMLResponse)
def fill_by_vin(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    by: FormField = "",
    vin: FormField = "",
    brand: FormField = "",
    model: FormField = "",
    generation: FormField = "",
    engine: FormField = "",
    year: FormField = "",
    mileage: FormField = "",
    gearbox: FormField = "",
) -> HTMLResponse:
    """Кнопка «Заполнить по VIN» (HTMX) — для добавления и редактирования.

    Удалось — блок #car-details целиком: открытый, с подставленными значениями
    (пробег — как был введён), и сообщение под VIN (hx-swap-oob). Не удалось —
    только сообщение, поля формы не меняются. Сохраняет форма.
    """
    add_mode = by if by in ADD_MODES else ""
    values = {
        "mileage": mileage,
        "vin": vin,
        "brand": brand,
        "model": model,
        "generation": generation,
        "engine": engine,
        "year": year,
        "gearbox": gearbox,
    }
    errors, notes, candidates, filled = {}, [], [], False
    normalized = normalize_vin(vin)
    if normalized is None:
        errors["vin"] = FORMAT_ERROR
    else:
        values["vin"] = normalized
        try:
            matched = match_catalog(decode_vin(session, user, normalized))
        except VinError as exc:
            errors["vin"] = exc.message
        else:
            filled = True
            gearbox = matched["values"].pop("gearbox")
            values.update(matched["values"])
            if gearbox:
                values["gearbox"] = gearbox
            notes, candidates = matched["notes"], matched["candidates"]
    # В errors может быть только vin: списки марки и модели ошибок не показывают
    context = fields_context(values, errors)
    context.update(vin_context(session, user))
    context.update(
        add_mode=add_mode,
        submit_label="Сохранить машину" if add_mode else "Сохранить изменения",
        vin_notes=notes,
        vin_filled=filled,
        vin_candidates=candidates,
    )
    response = templates.TemplateResponse(request, "_vin_fill.html", context)
    if not filled:
        # Поля не трогаем: HTMX применит только сообщение (hx-swap-oob)
        response.headers["HX-Reswap"] = "none"
    return response


@router.get("/{car_id}/edit", response_class=HTMLResponse)
def edit_car(
    request: Request, session: DbSession, user: CurrentUser, car: TargetCar
) -> HTMLResponse:
    """Форма редактирования с сохранёнными характеристиками машины."""
    return render_edit_form(request, session, user, car, values_from_car(car), {})


@router.post("/{car_id}/edit", response_class=HTMLResponse)
def update_car(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    car: TargetCar,
    vin: FormField = "",
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
        "vin": vin,
        "brand": brand,
        "model": model,
        "generation": generation,
        "engine": engine,
        "year": year,
        "mileage": mileage,
        "gearbox": gearbox,
    }
    data, errors = validate_car_form(values, top_record(car))
    if errors:
        return render_edit_form(
            request, session, user, car, values, errors, status_code=422
        )
    if data["mileage"] != car.mileage:
        car.mileage_date = msk_today()
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
