"""Анкета об использовании автомобиля: просмотр, заполнение и очистка."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.models import LONG_TRIP_DISTANCES, MILEAGE_PERIODS, Car, CarSurvey
from app.routers.pages import (
    NBSP,
    WORK_TYPES,
    DbSession,
    format_date,
    format_km,
    format_number,
    page_context,
    templates,
)
from app.services.survey import (
    LAST_SERVICE_WORKS,
    LONG_TRIP_DISTANCE_TITLE,
    REMINDER_KM_OPTIONS,
    REMINDER_MONTH_OPTIONS,
    SURVEY_BLOCKS,
    blank_values,
    has_long_trips,
    survey_warnings,
    validate_survey_form,
    values_from_form,
    values_from_survey,
)

router = APIRouter(prefix="/cars/{car_id}/survey")

NOT_ANSWERED = "Не указано"


def get_target_car(car_id: int, session: DbSession) -> Car:
    """Машина из адреса страницы; нет такой — 404."""
    car = session.get(Car, car_id)
    if car is None:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    return car


async def read_form(request: Request) -> FormData:
    """Форма целиком: у анкеты много полей, а галочки приходят списками."""
    return await request.form()


TargetCar = Annotated[Car, Depends(get_target_car)]


def form_context(session: Session, car: Car, values: dict, errors: dict) -> dict:
    context = page_context("car", session)
    context.update(
        target_car=car,
        values=values,
        errors=errors,
        blocks=SURVEY_BLOCKS,
        distance_title=LONG_TRIP_DISTANCE_TITLE,
        distance_options=LONG_TRIP_DISTANCES,
        show_distance=has_long_trips(values["long_trips"]),
        mileage_periods=MILEAGE_PERIODS,
        last_service_works=LAST_SERVICE_WORKS,
        reminder_km_options=REMINDER_KM_OPTIONS,
        reminder_month_options=REMINDER_MONTH_OPTIONS,
    )
    return context


def _labels(codes: list[str] | None, options, other: str | None = None) -> str:
    """Названия выбранных вариантов и текст «Другое» одной строкой."""
    parts = [options[code] for code in codes or []]
    if other:
        parts.append(f"Другое: {other}")
    return ", ".join(parts) or NOT_ANSWERED


def _mileage_answer(survey: CarSurvey) -> str:
    if survey.monthly_km is None:
        return NOT_ANSWERED
    entered = (
        f"{format_number(survey.mileage_amount)}{NBSP}км "
        f"{MILEAGE_PERIODS[survey.mileage_period]}"
    )
    if survey.mileage_period == "month":
        return entered
    return f"{entered} (примерно {format_km(survey.monthly_km)} в месяц)"


def _last_service_answer(survey: CarSurvey) -> str:
    parts = []
    if survey.last_service_date:
        parts.append(format_date(survey.last_service_date.isoformat()))
    if survey.last_service_mileage is not None:
        parts.append(format_km(survey.last_service_mileage))
    if survey.last_service_works or survey.last_service_works_other:
        parts.append(
            _labels(
                survey.last_service_works,
                WORK_TYPES,
                survey.last_service_works_other,
            )
        )
    if survey.last_service_approximate:
        parts.append("дата и пробег примерные")
    return " · ".join(parts) or NOT_ANSWERED


def _reminder_answer(survey: CarSurvey) -> str:
    parts = []
    if survey.reminder_km is not None:
        parts.append(REMINDER_KM_OPTIONS[str(survey.reminder_km)])
    if survey.reminder_months is not None:
        parts.append(REMINDER_MONTH_OPTIONS[str(survey.reminder_months)])
    return ", ".join(parts) or NOT_ANSWERED


def survey_answers(survey: CarSurvey) -> list[dict]:
    """Ответы для страницы просмотра: блоки со строками «вопрос — ответ»."""
    blocks = []
    for block in SURVEY_BLOCKS:
        rows = []
        for question in block["questions"]:
            kind, name = question["kind"], question["name"]
            if kind == "multi":
                other = getattr(survey, f"{name}_other")
                answer = _labels(getattr(survey, name), question["options"], other)
            elif kind == "mileage":
                answer = _mileage_answer(survey)
            elif kind == "last_service":
                answer = _last_service_answer(survey)
            elif kind == "reminder":
                answer = _reminder_answer(survey)
            else:
                code = getattr(survey, name)
                answer = question["options"][code] if code else NOT_ANSWERED
            rows.append({"title": question["title"], "answer": answer})
            if kind == "long_trips" and has_long_trips(survey.long_trips):
                code = survey.long_trip_distance
                rows.append(
                    {
                        "title": LONG_TRIP_DISTANCE_TITLE,
                        "answer": LONG_TRIP_DISTANCES[code] if code else NOT_ANSWERED,
                    }
                )
        blocks.append({"title": block["title"], "rows": rows})
    return blocks


@router.get("", response_class=HTMLResponse)
def view_survey(
    request: Request, session: DbSession, car: TargetCar, saved: int = 0
) -> Response:
    """Ответы только для чтения; если анкеты нет — сразу форма."""
    if car.survey is None:
        return RedirectResponse(f"/cars/{car.id}/survey/edit", status_code=303)
    context = page_context("car", session)
    context.update(
        target_car=car,
        answers=survey_answers(car.survey),
        warnings=survey_warnings(car.survey),
        saved=bool(saved),
    )
    return templates.TemplateResponse(request, "survey_view.html", context)


@router.get("/edit", response_class=HTMLResponse)
def edit_survey(request: Request, session: DbSession, car: TargetCar) -> HTMLResponse:
    """Форма анкеты с сохранёнными ответами."""
    values = values_from_survey(car.survey) if car.survey else blank_values()
    context = form_context(session, car, values, {})
    return templates.TemplateResponse(request, "survey.html", context)


@router.post("/edit", response_class=HTMLResponse)
def save_survey(
    request: Request,
    session: DbSession,
    car: TargetCar,
    form: Annotated[FormData, Depends(read_form)],
) -> Response:
    """Сохраняет ответы. При ошибке форма возвращается с введёнными данными."""
    values = values_from_form(form)
    data, errors = validate_survey_form(values, car)
    if errors:
        context = form_context(session, car, values, errors)
        return templates.TemplateResponse(
            request, "survey.html", context, status_code=422
        )
    survey = car.survey or CarSurvey(car=car)
    for name, value in data.items():
        setattr(survey, name, value)
    session.add(survey)
    session.commit()
    return RedirectResponse(f"/cars/{car.id}/survey?saved=1", status_code=303)


@router.get("/long-trip", response_class=HTMLResponse)
def long_trip_fields(request: Request, car: TargetCar) -> HTMLResponse:
    """Блок условного вопроса для HTMX: пустой, если дальних поездок нет."""
    params = request.query_params
    context = {
        "show_distance": has_long_trips(params.get("long_trips")),
        "distance_title": LONG_TRIP_DISTANCE_TITLE,
        "distance_options": LONG_TRIP_DISTANCES,
        "values": {"long_trip_distance": params.get("long_trip_distance", "")},
        "errors": {},
    }
    return templates.TemplateResponse(request, "_survey_long_trip.html", context)


@router.post("/clear")
def clear_survey(session: DbSession, car: TargetCar) -> Response:
    """Удаляет анкету машины. Подтверждение спрашивает кнопка (hx-confirm)."""
    if car.survey is not None:
        session.delete(car.survey)
        session.commit()
    # HTMX выполнит переход сам: обычный редирект он отрисовал бы внутри страницы
    return Response(
        status_code=204, headers={"HX-Redirect": f"/cars/{car.id}/survey/edit"}
    )
