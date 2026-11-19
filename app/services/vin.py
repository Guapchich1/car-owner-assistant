"""Расшифровка VIN через apipoint.ru и сопоставление со справочником моделей.

Запрос к apipoint платный, поэтому:
- до запроса проверяем формат VIN;
- ответ сохраняем в vin_decodes (общий для всех пользователей) и тот же VIN
  повторно в apipoint не запрашиваем;
- не больше DAILY_LIMIT проверок VIN в сутки (по Москве) на пользователя.

Пользователь не должен узнать, что VIN уже кто-то проверял. Поэтому лимит
считает проверки пользователя, а не платные запросы: первая проверка VIN
учитывается, даже если ответ взят из базы, а после лимита не работает и он.
Бесплатна только повторная проверка VIN, который пользователь уже проверял
сам и получил ответ, — тут он ничего нового не узнаёт.

Токен (APIPOINT_TOKEN) нигде не печатается: ни в логах, ни в сообщениях.
"""

import logging
import math
import os
import re
from datetime import UTC, datetime, time

import httpx2
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MSK, User, VinDecode, VinRequest, msk_today
from app.services import catalog

logger = logging.getLogger(__name__)

API_URL = "https://apipoint.ru/api/call"
TIMEOUT_SECONDS = 15
DAILY_LIMIT = 10

# 17 символов: латиница без I, O, Q и цифры
VIN_PATTERN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")

# Код FuelType бензина. Код дизеля нам неизвестен, поэтому для других
# значений пробуем оба варианта двигателя: «1.6 (90 л.с.)» и «1.6d (90 л.с.)»
PETROL = ["PO"]

# Коробка из поля Gear → код GEARBOXES (без учёта регистра)
GEAR_CODES = {
    "mt": "manual",
    "manual": "manual",
    "at": "automatic",
    "automatic": "automatic",
    "cvt": "cvt",
    "amt": "robot",
    "dsg": "robot",
    "dct": "robot",
    "robot": "robot",
}

FORMAT_ERROR = (
    "VIN — 17 символов: латинские буквы без I, O, Q и цифры. Проверьте написание."
)
NO_TOKEN = "Расшифровка по VIN сейчас недоступна. Заполните поля вручную."
LIMIT_REACHED = (
    f"Сегодня уже проверено {DAILY_LIMIT} VIN — это лимит на сутки. "
    "Попробуйте завтра или добавьте машину вручную."
)
SERVICE_ERROR = (
    "Сервис расшифровки VIN не ответил. Попробуйте позже или заполните поля вручную."
)
NOT_FOUND = "Машина с таким VIN не найдена. Заполните поля вручную."


class VinError(Exception):
    """Расшифровка не удалась; message — готовый текст для пользователя."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def normalize_vin(raw: str) -> str | None:
    """VIN заглавными буквами; неверный формат — None."""
    vin = raw.strip().upper()
    return vin if VIN_PATTERN.fullmatch(vin) else None


def _send(vin: str, token: str) -> dict:
    """Запрос к apipoint — единственное место, где уходит запрос по сети.

    Возвращает разобранный JSON ответа. Ошибки сети и HTTP — исключения httpx2.
    """
    response = httpx2.post(
        API_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"sources": "vindecode", "vin": vin},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _day_start_utc() -> datetime:
    """Начало сегодняшних суток по Москве — в UTC без часового пояса, как в базе."""
    start = datetime.combine(msk_today(), time.min, tzinfo=MSK)
    return start.astimezone(UTC).replace(tzinfo=None)


def requests_left(session: Session, user: User) -> int:
    """Сколько проверок VIN у пользователя осталось на сегодня."""
    sent = session.scalar(
        select(func.count())
        .select_from(VinRequest)
        .where(VinRequest.user_id == user.id, VinRequest.created_at >= _day_start_utc())
    )
    return max(DAILY_LIMIT - sent, 0)


def _reports(vindecode: dict) -> list[dict]:
    """Варианты машины из ответа (Reports[*].Data)."""
    decode = vindecode.get("decode") or {}
    reports = decode.get("Reports") or []
    return [r["Data"] for r in reports if isinstance(r, dict) and r.get("Data")]


def _vindecode(data: object) -> dict | None:
    """result.vindecode из ответа со status 200; иначе None.

    Цена запроса и баланс счёта (price, balance) дальше не передаются.
    """
    if not isinstance(data, dict) or data.get("status") != 200:
        return None
    result = data.get("result")
    vindecode = result.get("vindecode") if isinstance(result, dict) else None
    return vindecode if isinstance(vindecode, dict) else None


def _save_decode(session: Session, vin: str, vindecode: dict, found: bool) -> None:
    """Сохраняет расшифровку. Если её уже сохранил параллельный запрос — не страшно."""
    session.add(VinDecode(vin=vin, found=found, response=vindecode))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()


def _answered_before(session: Session, user: User, vin: str) -> bool:
    """Пользователь уже проверял этот VIN и получил ответ (найден или нет)."""
    query = select(VinRequest.id).where(
        VinRequest.user_id == user.id, VinRequest.vin == vin, VinRequest.answered
    )
    return session.scalars(query).first() is not None


def _result(saved: VinDecode) -> dict:
    """Сохранённая расшифровка; «не найден» — VinError."""
    if not saved.found:
        raise VinError(NOT_FOUND)
    return saved.response


def decode_vin(session: Session, user: User, vin: str) -> dict:
    """Расшифровка VIN (result.vindecode): из базы или запросом к apipoint.

    vin — уже проверенный normalize_vin. Ошибки — VinError с текстом
    для пользователя; ошибки сервиса не сохраняем, их можно повторить.
    Ответ и лимит не зависят от того, проверял ли VIN кто-то другой.
    """
    saved = session.get(VinDecode, vin)
    # Ответ этот пользователь уже видел — повторная проверка бесплатна.
    # Раз он получил ответ, расшифровка сохранена (из базы она не удаляется)
    if saved is not None and _answered_before(session, user, vin):
        return _result(saved)

    token = os.environ.get("APIPOINT_TOKEN", "").strip()
    if not token:
        raise VinError(NO_TOKEN)
    if requests_left(session, user) <= 0:
        raise VinError(LIMIT_REACHED)

    # Проверку учитываем до запроса: он платный, даже если ответ не дойдёт
    request = VinRequest(user_id=user.id, vin=vin)
    session.add(request)
    session.commit()

    if saved is not None:
        request.answered = True
        session.commit()
        return _result(saved)

    try:
        data = _send(vin, token)
    except httpx2.HTTPStatusError as exc:
        # В лог — только код ответа: в тексте исключения могут быть детали запроса
        logger.warning("apipoint: ответ %s", exc.response.status_code)
        raise VinError(SERVICE_ERROR) from None
    except (httpx2.HTTPError, ValueError) as exc:
        logger.warning("apipoint: ошибка %s", type(exc).__name__)
        raise VinError(SERVICE_ERROR) from None

    vindecode = _vindecode(data)
    if vindecode is None:
        logger.warning("apipoint: неожиданный ответ")
        raise VinError(SERVICE_ERROR)
    if vindecode.get("error"):
        logger.warning("apipoint: vindecode вернул ошибку")
        raise VinError(SERVICE_ERROR)

    found = bool(_reports(vindecode))
    _save_decode(session, vin, vindecode, found)
    request.answered = True
    session.commit()
    if not found:
        raise VinError(NOT_FOUND)
    return vindecode


def _pick(names: list[str], value: str) -> str:
    """Название из списка без учёта регистра; не нашли — пустая строка."""
    value = value.strip().casefold()
    return next((n for n in names if n.casefold() == value), "")


def _round_half_up(value: float) -> int:
    """Округление до целого: 104.69 → 105, 90.5 → 91 (round() дал бы 90)."""
    return math.floor(value + 0.5)


def _engine_names(report: dict) -> list[str]:
    """Варианты названия двигателя в формате справочника: «1.6 (105 л.с.)».

    Бензин — один вариант; иначе ещё и дизельный «1.6d (105 л.с.)».
    """
    volume = (report.get("EngineVolume") or {}).get("L")
    power = (report.get("EnginePower") or {}).get("PS")
    if not isinstance(volume, int | float) or not isinstance(power, int | float):
        return []
    litres = f"{volume:.1f}"
    hp = f"({_round_half_up(power)} л.с.)"
    if report.get("FuelType") == PETROL:
        return [f"{litres} {hp}"]
    return [f"{litres} {hp}", f"{litres}d {hp}"]


def _generations(generations: list[dict], name: str, year: int | None) -> list[dict]:
    """Поколения справочника, подходящие по Generation и ModelYear.

    1) Generation есть — оставляем поколения, у которых первое слово названия
       равно ему целиком («V» подходит к «V 2009-2015», но не к «VI 2020-2022»).
       Generation пустой или ничего не совпало — шаг пропускаем.
    2) Из оставшихся — те, где ModelYear в диапазоне годов выпуска.
    """
    remaining = generations
    by_name = []
    if name:
        by_name = [
            g for g in generations if g["name"].split()[0].casefold() == name.casefold()
        ]
        if by_name:
            remaining = by_name
    if year is None:
        # Без года отбираем только по названию, если оно что-то дало
        return by_name
    return [g for g in remaining if g["year_from"] <= year <= g["year_to"]]


def match_catalog(vindecode: dict) -> dict:
    """Сопоставляет расшифровку со справочником.

    Возвращает значения формы (brand, model, generation, engine, year, gearbox —
    пустые, если не определились), подходящие поколения для подсветки
    (если их несколько) и подсказки пользователю.
    """
    reports = _reports(vindecode)
    first = reports[0]
    values = dict.fromkeys(("brand", "model", "generation", "engine", "gearbox"), "")
    year = first.get("ModelYear")
    year = year if isinstance(year, int) else None
    values["year"] = str(year) if year else ""
    notes = []
    candidates: list[str] = []

    gear = first.get("Gear")
    if isinstance(gear, str):
        values["gearbox"] = GEAR_CODES.get(gear.strip().casefold(), "")

    raw_brand = str(first.get("Brand") or "")
    raw_model = str(first.get("Model") or "")
    values["brand"] = _pick(catalog.list_brands(), raw_brand)
    if values["brand"]:
        values["model"] = _pick(catalog.list_models(values["brand"]), raw_model)
    if not values["model"]:
        notes.append(
            f"Модели {raw_brand} {raw_model} нет в справочнике — "
            "выберите машину вручную."
        )
        return {"values": values, "candidates": candidates, "notes": notes}

    generations = catalog.list_generations(values["brand"], values["model"])
    found = _generations(generations, str(first.get("Generation") or ""), year)
    if len(found) > 1:
        candidates = [g["name"] for g in found]
        notes.append(
            "По VIN подходит несколько поколений: "
            + ", ".join(candidates)
            + ". Выберите своё — они в начале списка."
        )
        return {"values": values, "candidates": candidates, "notes": notes}
    if not found:
        notes.append("Поколение по VIN определить не удалось — выберите вручную.")
        return {"values": values, "candidates": candidates, "notes": notes}
    values["generation"] = found[0]["name"]

    # Варианты из Reports, которых нет в справочнике для этого поколения, отбрасываем
    names = {n.casefold() for report in reports for n in _engine_names(report)}
    engines = catalog.list_engines(
        values["brand"], values["model"], values["generation"]
    )
    matched = [e for e in engines if e.casefold() in names]
    if len(matched) == 1:
        values["engine"] = matched[0]
    elif matched:
        notes.append(
            "По VIN подходит несколько двигателей: "
            + ", ".join(matched)
            + ". Выберите свой."
        )
    else:
        notes.append("Двигатель по VIN определить не удалось — выберите вручную.")
    return {"values": values, "candidates": candidates, "notes": notes}
