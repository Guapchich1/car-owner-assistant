"""Расшифровка VIN через apipoint: формат, кэш, лимит, ошибки и сопоставление."""

import copy
import json
import logging
from datetime import timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select

from app.models import Car, VinDecode, VinRequest
from app.services import catalog
from app.services import vin as vin_service
from app.services.vin import DAILY_LIMIT, match_catalog, normalize_vin
from tests.conftest import TEST_APIPOINT_TOKEN

# Настоящий _send — до подмены: в остальных тестах его заменяет фикстура
real_send = vin_service._send

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = json.loads((FIXTURES / "vindecode_sample.json").read_text(encoding="utf-8"))
VIN = "XW8ZZZ61ZCG060786"
NOT_FOUND = {
    "status": 200,
    "price": "1.10",
    "balance": "7.10",
    "result": {"vindecode": {"f": False, "error": False, "decode": {"Reports": []}}},
}

# Остальные поля формы: машина выбирается по VIN, пробег и коробку вводит пользователь
FORM = {
    "brand": "Lada",
    "model": "Vesta",
    "generation": "I 2015-2022",
    "engine": "1.8 (122 л.с.)",
    "year": "2017",
    "mileage": "120000",
    "gearbox": "robot",
}


@pytest.fixture(autouse=True)
def sample_catalog(tmp_path, monkeypatch):
    """Справочник — выборка из git, а не полный файл с компьютера разработчика."""
    monkeypatch.setattr(catalog, "FULL_PATH", tmp_path / "нет.json")


@pytest.fixture
def fake_apipoint(monkeypatch):
    """Подменяет запрос к apipoint: ответ — словарь или исключение.

    Возвращает список вызовов (vin, token) — по нему видно, был ли запрос.
    """

    def install(answer=SAMPLE):
        calls = []

        def send(vin: str, token: str) -> dict:
            calls.append((vin, token))
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        monkeypatch.setattr(vin_service, "_send", send)
        return calls

    return install


def sample_vindecode(**data) -> dict:
    """result.vindecode из примера; поля Data всех вариантов можно переопределить."""
    vindecode = copy.deepcopy(SAMPLE["result"]["vindecode"])
    for report in vindecode["decode"]["Reports"]:
        report["Data"].update(data)
    return vindecode


def with_reports(*datas) -> dict:
    """result.vindecode из примера, где варианты — первый вариант с правками."""
    vindecode = sample_vindecode()
    first = vindecode["decode"]["Reports"][0]
    vindecode["decode"]["Reports"] = [
        {**first, "Data": {**first["Data"], **data}} for data in datas
    ]
    return vindecode


def fill(client, vin=VIN, **fields):
    """Нажатие «Заполнить по VIN»: HTMX отправляет всю форму."""
    return client.post("/cars/new/vin", data={**FORM, **fields, "vin": vin})


def http_error(status: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", vin_service.API_URL)
    response = httpx2.Response(status, request=request)
    return httpx2.HTTPStatusError("ошибка", request=request, response=response)


def assert_fields_untouched(response):
    """Расшифровка не удалась: пришло только сообщение, поля формы не меняются."""
    assert response.headers["HX-Reswap"] == "none"
    assert 'id="car-details"' not in response.text
    assert 'id="vin-status" aria-live="polite" hx-swap-oob="true"' in response.text


def selected(html: str, field: str) -> str:
    """Выбранное значение выпадающего списка ('' — ничего не выбрано)."""
    block = html.split(f'id="{field}"', 1)[1].split("</select>", 1)[0]
    if " selected>" not in block:
        return ""
    return block.split(" selected>", 1)[0].rsplit('value="', 1)[1].split('"', 1)[0]


# --- Формат VIN ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        (VIN, VIN),
        ("xw8zzz61zcg060786", VIN),  # строчные → заглавные
        (f"  {VIN} ", VIN),
        ("XW8ZZZ61ZCG06078I", None),  # I
        ("XW8ZZZ61ZCG06078O", None),  # O
        ("XW8ZZZ61ZCG06078Q", None),  # Q
        ("XW8ZZZ61ZCG06078", None),  # 16 символов
        ("XW8ZZZ61ZCG0607861", None),  # 18 символов
        ("ХW8ZZZ61ZCG060786", None),  # первая буква кириллическая
        ("XW8ZZZ61ZCG-60786", None),
        ("", None),
    ],
)
def test_normalize_vin(raw, expected):
    assert normalize_vin(raw) == expected


@pytest.mark.parametrize("bad", ["XW8ZZZ61ZCG06078I", "XW8ZZZ61ZCG06078", "ХW8ZZZ61"])
def test_wrong_format_without_request(client, session, fake_apipoint, bad):
    """Неверный формат — ошибка у поля VIN, запрос не отправляется и не считается."""
    calls = fake_apipoint()

    response = fill(client, bad)

    assert response.status_code == 200
    assert 'id="vin-error"' in response.text
    assert "латинские буквы без I, O, Q" in response.text
    assert calls == []
    assert session.scalars(select(VinRequest)).all() == []


# --- Запрос к apipoint ---


def test_send_request_format(monkeypatch):
    """Запрос: POST, заголовок с токеном, тело с vindecode, таймаут 15 секунд."""
    sent = {}

    def post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return httpx2.Response(200, json=SAMPLE, request=httpx2.Request("POST", url))

    monkeypatch.setattr(vin_service.httpx2, "post", post)

    assert real_send(VIN, "token-1") == SAMPLE
    assert sent == {
        "url": "https://apipoint.ru/api/call",
        "headers": {"Authorization": "Bearer token-1"},
        "json": {"sources": "vindecode", "vin": VIN},
        "timeout": 15,
    }


def test_send_raises_on_http_error(monkeypatch):
    """Ответ 503 — исключение httpx2, которое decode_vin превращает в сообщение."""
    monkeypatch.setattr(
        vin_service.httpx2,
        "post",
        lambda url, **kwargs: httpx2.Response(503, request=httpx2.Request("POST", url)),
    )
    with pytest.raises(httpx2.HTTPStatusError):
        real_send(VIN, "token-1")


def test_fill_by_vin(client, session, fake_apipoint):
    """Верный ответ: подставлены марка, модель, поколение, двигатель и год."""
    calls = fake_apipoint()

    response = fill(client, VIN.lower())

    assert response.status_code == 200
    html = response.text
    assert calls == [(VIN, TEST_APIPOINT_TOKEN)]
    assert selected(html, "brand") == "Volkswagen"
    assert selected(html, "model") == "Polo"
    assert selected(html, "generation") == "V 2009-2015"
    assert selected(html, "engine") == "1.6 (105 л.с.)"
    assert 'name="year"' in html and 'value="2012"' in html
    # Gear в ответе нет — коробка остаётся той, что была в форме
    assert selected(html, "gearbox") == "robot"
    assert "Данные по VIN подставлены" in html
    # Блок полей приходит открытым, пробег — как был введён; сообщение — отдельно
    assert '<div class="car-details" id="car-details">' in html
    assert 'value="120000"' in html
    assert 'id="vin-status" aria-live="polite" hx-swap-oob="true"' in html
    assert "HX-Reswap" not in response.headers


def test_decode_saved_without_price_and_balance(client, session, fake_apipoint):
    """В базе — только result.vindecode: цены запроса и баланса счёта там нет."""
    fake_apipoint()

    fill(client)

    saved = session.get(VinDecode, VIN)
    assert saved.found is True
    assert saved.response == SAMPLE["result"]["vindecode"]
    stored = json.dumps(saved.response)
    assert "price" not in stored and "balance" not in stored


def test_repeated_vin_without_request(
    client, session, fake_apipoint, make_user, make_client
):
    """Тот же VIN в apipoint повторно не запрашиваем — ни этому, ни другому.

    Своя повторная проверка не учитывается, а у другого пользователя первая
    проверка учитывается, хотя ответ взят из базы.
    """
    calls = fake_apipoint()
    fill(client)
    other = make_client(make_user("second"))

    first = fill(client)
    second = fill(other)

    assert len(calls) == 1
    for response in (first, second):
        assert selected(response.text, "engine") == "1.6 (105 л.с.)"
    assert len(session.scalars(select(VinRequest)).all()) == 2


@pytest.mark.parametrize(
    "answer",
    [
        http_error(503),
        http_error(401),
        httpx2.ConnectError("нет сети"),
        httpx2.ReadTimeout("таймаут"),
        ValueError("не JSON"),
        {"status": 500, "result": None},
        {"status": 200, "result": {"vindecode": {"error": True, "decode": None}}},
    ],
)
def test_service_error(client, session, fake_apipoint, answer):
    """Ошибка сервиса: понятное сообщение, поля не тронуты, ответ не сохранён."""
    fake_apipoint(answer)

    response = fill(client)

    assert response.status_code == 200
    assert "Сервис расшифровки VIN не ответил" in response.text
    assert_fields_untouched(response)
    assert session.get(VinDecode, VIN) is None
    # Запрос всё равно отправлен — он учтён в лимите
    assert len(session.scalars(select(VinRequest)).all()) == 1


def test_service_error_can_be_retried(client, fake_apipoint):
    """После ошибки сервиса тот же VIN можно запросить снова."""
    fake_apipoint(http_error(503))
    fill(client)
    calls = fake_apipoint()

    response = fill(client)

    assert len(calls) == 1
    assert selected(response.text, "model") == "Polo"


def test_vin_not_found(client, session, fake_apipoint):
    """VIN не найден: сообщение; ответ сохранён, повторно не запрашиваем."""
    calls = fake_apipoint(NOT_FOUND)

    first = fill(client)
    second = fill(client)

    for response in (first, second):
        assert "Машина с таким VIN не найдена" in response.text
        assert_fields_untouched(response)
    assert len(calls) == 1
    assert session.get(VinDecode, VIN).found is False


def test_no_token(client, session, fake_apipoint, monkeypatch):
    """Токена нет — сообщение, запрос не отправляется, форма работает вручную."""
    monkeypatch.delenv("APIPOINT_TOKEN")
    calls = fake_apipoint()

    response = fill(client)

    assert response.status_code == 200
    assert "Расшифровка по VIN сейчас недоступна" in response.text
    assert calls == []
    assert session.scalars(select(VinRequest)).all() == []
    saved = client.post("/cars/new", data={**FORM, "vin": VIN}, follow_redirects=False)
    assert saved.status_code == 303


def test_daily_limit(client, session, fake_apipoint):
    """10 запросов в сутки проходят, 11-й — сообщение без запроса."""
    calls = fake_apipoint()
    vins = [f"XW8ZZZ61ZCG0607{i:02d}" for i in range(DAILY_LIMIT + 1)]

    for vin in vins[:DAILY_LIMIT]:
        assert "Данные по VIN подставлены" in fill(client, vin).text
    response = fill(client, vins[-1])

    assert "лимит на сутки" in response.text
    assert len(calls) == DAILY_LIMIT
    assert "осталось 0 из 10" in response.text
    # Сохранённый VIN лимит не тратит — его можно подставить и после лимита
    assert "Данные по VIN подставлены" in fill(client, vins[0]).text


def test_limit_per_user(client, fake_apipoint, make_user, make_client, session, user):
    """Лимит у каждого пользователя свой."""
    session.add_all(VinRequest(user_id=user.id, vin=VIN) for _ in range(DAILY_LIMIT))
    session.commit()
    calls = fake_apipoint()

    assert "лимит на сутки" in fill(client).text
    other = make_client(make_user("second"))
    assert "Данные по VIN подставлены" in fill(other).text
    assert len(calls) == 1


def test_yesterday_requests_not_counted(client, session, fake_apipoint, user):
    """Запросы прошлых суток (по Москве) в лимит не входят."""
    yesterday = vin_service._day_start_utc() - timedelta(minutes=1)
    session.add_all(
        VinRequest(user_id=user.id, vin=VIN, created_at=yesterday)
        for _ in range(DAILY_LIMIT)
    )
    session.commit()
    calls = fake_apipoint()

    response = fill(client)

    assert "Данные по VIN подставлены" in response.text
    assert len(calls) == 1


def test_requests_left_shown(client, fake_apipoint):
    """Остаток лимита: на форме, уменьшается после запроса, после кэша — нет."""
    fake_apipoint()

    assert "осталось 10 из 10" in client.get("/cars/new?by=vin").text
    assert "осталось 9 из 10" in fill(client).text
    assert "осталось 9 из 10" in fill(client).text
    assert "осталось 9 из 10" in client.get("/cars/new?by=vin").text


@pytest.mark.parametrize(
    "answer, env",
    [
        (http_error(503), True),
        (httpx2.ConnectError("нет сети"), True),
        (NOT_FOUND, True),
        (SAMPLE, True),
        (SAMPLE, False),
    ],
)
def test_token_never_shown(client, fake_apipoint, caplog, monkeypatch, answer, env):
    """Токен не попадает ни на страницу, ни в логи."""
    if not env:
        monkeypatch.delenv("APIPOINT_TOKEN")
    fake_apipoint(answer)
    caplog.set_level(logging.DEBUG)

    pages = [fill(client).text, client.get("/cars/new?by=vin").text]

    for html in pages:
        assert TEST_APIPOINT_TOKEN not in html
    assert TEST_APIPOINT_TOKEN not in caplog.text


def test_fill_requires_login(anon_client, fake_apipoint):
    """Без входа кнопка не работает и запрос не отправляется."""
    calls = fake_apipoint()

    response = anon_client.post(
        "/cars/new/vin", data={"vin": VIN}, follow_redirects=False
    )

    assert response.status_code in (303, 403)
    assert calls == []


# --- Сопоставление со справочником ---


def test_match_sample():
    """Пример: 1.4 TSI (125 л.с.) нет в «V 2009-2015», остаётся 1.6 (105 л.с.)."""
    result = match_catalog(sample_vindecode())

    assert result["values"] == {
        "brand": "Volkswagen",
        "model": "Polo",
        "generation": "V 2009-2015",
        "engine": "1.6 (105 л.с.)",
        "year": "2012",
        "gearbox": "",
    }
    assert result["candidates"] == []
    assert result["notes"] == []


@pytest.mark.parametrize("generation", ["", None, "VII"])
def test_generation_name_skipped(generation):
    """Generation пустой или не совпал — отбор только по году."""
    result = match_catalog(sample_vindecode(Generation=generation))

    assert result["values"]["generation"] == "V 2009-2015"


def test_generation_first_word_whole():
    """«VI» не подходит к «V …»: первое слово сравнивается целиком."""
    result = match_catalog(sample_vindecode(Generation="VI", ModelYear=2021))

    assert result["values"]["generation"] == "VI 2020-2022"


def test_several_generations_to_choose():
    """На стыке (2014 для Polo V) подходит два поколения — пользователь выбирает."""
    result = match_catalog(sample_vindecode(ModelYear=2014))

    assert result["values"]["generation"] == ""
    assert result["values"]["engine"] == ""
    assert result["values"]["year"] == "2014"
    assert sorted(result["candidates"]) == ["V 2009-2015", "V Рестайлинг 2014-2020"]
    assert "несколько поколений" in result["notes"][0]


def test_no_generation_fits():
    """Год вне всех поколений — поколение выбирает пользователь, год подставлен."""
    result = match_catalog(sample_vindecode(ModelYear=1999))

    assert result["values"]["generation"] == ""
    assert result["values"]["year"] == "1999"
    assert result["candidates"] == []
    assert "Поколение по VIN определить не удалось" in result["notes"][0]


def test_several_engines_to_choose():
    """Два варианта из справочника — двигатель выбирает пользователь."""
    result = match_catalog(
        with_reports(
            {"EnginePower": {"PS": 104.69}},
            {"EnginePower": {"PS": 85.0}},
        )
    )

    assert result["values"]["generation"] == "V 2009-2015"
    assert result["values"]["engine"] == ""
    assert "1.6 (85 л.с.)" in result["notes"][0]
    assert "1.6 (105 л.с.)" in result["notes"][0]


def test_power_rounding_half_up():
    """Мощность округляется до целого: 84.5 → 85 (а не 84)."""
    result = match_catalog(with_reports({"EnginePower": {"PS": 84.5}}))

    assert result["values"]["engine"] == "1.6 (85 л.с.)"


def test_not_petrol_only_diesel_in_catalog():
    """Не бензин: пробуем «1.6» и «1.6d»; в справочнике только дизель — он и подставлен."""
    result = match_catalog(
        with_reports({"FuelType": ["XX"], "EnginePower": {"PS": 90.0}})
    )

    assert result["values"]["engine"] == "1.6d (90 л.с.)"


def test_not_petrol_both_in_catalog():
    """Не бензин, а в справочнике есть и «1.6 (105)», и «1.6d (105)» — выбор."""
    result = match_catalog(sample_vindecode(FuelType=None))

    assert result["values"]["engine"] == ""
    assert "1.6 (105 л.с.)" in result["notes"][0]
    assert "1.6d (105 л.с.)" in result["notes"][0]


def test_petrol_never_diesel():
    """Бензин (["PO"]): дизельный вариант не подставляется."""
    result = match_catalog(
        with_reports({"FuelType": ["PO"], "EnginePower": {"PS": 90.0}})
    )

    assert result["values"]["engine"] == ""
    assert "Двигатель по VIN определить не удалось" in result["notes"][0]


def test_model_not_in_catalog():
    """Модели нет в справочнике — марка и год подставлены, остальное вручную."""
    result = match_catalog(sample_vindecode(Model="Phaeton"))

    assert result["values"]["brand"] == "Volkswagen"
    assert result["values"]["model"] == ""
    assert result["values"]["generation"] == ""
    assert result["values"]["year"] == "2012"
    assert "нет в справочнике" in result["notes"][0]


def test_brand_not_in_catalog():
    """Марки нет в справочнике — подставлен только год."""
    result = match_catalog(sample_vindecode(Brand="BUGATTI", Model="Veyron"))

    assert (result["values"]["brand"], result["values"]["model"]) == ("", "")
    assert result["values"]["year"] == "2012"


@pytest.mark.parametrize(
    "gear, expected",
    [
        (None, ""),
        ("AT", "automatic"),
        ("mt", "manual"),
        ("CVT", "cvt"),
        ("DSG", "robot"),
        ("???", ""),
    ],
)
def test_gearbox_from_gear(gear, expected):
    """Gear пришёл — коробка сопоставлена с GEARBOXES; нет или непонятен — пусто."""
    result = match_catalog(sample_vindecode(Gear=gear))

    assert result["values"]["gearbox"] == expected


def test_gear_fills_gearbox_in_form(client, fake_apipoint):
    """Gear в ответе — коробка в форме подставлена."""
    answer = copy.deepcopy(SAMPLE)
    for report in answer["result"]["vindecode"]["decode"]["Reports"]:
        report["Data"]["Gear"] = "AT"
    fake_apipoint(answer)

    response = fill(client, gearbox="")

    assert selected(response.text, "gearbox") == "automatic"


def test_several_generations_highlighted(client, fake_apipoint):
    """Подходящие поколения — первыми в группе «Подходят по VIN», список полный."""
    answer = copy.deepcopy(SAMPLE)
    for report in answer["result"]["vindecode"]["decode"]["Reports"]:
        report["Data"]["ModelYear"] = 2014
    fake_apipoint(answer)

    html = fill(client).text

    block = html.split('id="generation"', 1)[1].split("</select>", 1)[0]
    fitting, others = block.split('label="Другие поколения"')
    assert 'label="Подходят по VIN"' in fitting
    assert 'value="V 2009-2015"' in fitting
    assert 'value="V Рестайлинг 2014-2020"' in fitting
    assert 'value="VI 2020-2022"' in others
    assert selected(html, "generation") == ""
    assert "несколько поколений" in html


def test_no_highlight_when_choosing_by_steps(client):
    """При обычном выборе по шагам групп «Подходят по VIN» нет."""
    html = client.get(
        "/cars/new/fields", params={"brand": "Volkswagen", "model": "Polo"}
    ).text

    assert "Подходят по VIN" not in html
    assert 'value="VI 2020-2022"' in html


# --- Сохранение VIN с машиной ---


def test_save_car_with_vin(client, session):
    """VIN сохраняется с машиной заглавными буквами."""
    response = client.post(
        "/cars/new", data={**FORM, "vin": VIN.lower()}, follow_redirects=False
    )

    assert response.status_code == 303
    [car] = session.scalars(select(Car)).all()
    assert car.vin == VIN


def test_save_car_without_vin(client, session):
    """VIN необязательный."""
    client.post("/cars/new", data={**FORM, "vin": "  "})

    [car] = session.scalars(select(Car)).all()
    assert car.vin is None


def test_save_car_wrong_vin(client, session):
    """Неверный VIN — ошибка у поля, машина не сохранена, введённое не потеряно."""
    response = client.post("/cars/new", data={**FORM, "vin": "XW8ZZZ61ZCG06078I"})

    assert response.status_code == 422
    assert 'id="vin-error"' in response.text
    assert 'value="XW8ZZZ61ZCG06078I"' in response.text
    assert session.scalars(select(Car)).all() == []


def test_two_users_same_vin(client, session, make_user, make_client):
    """Одну машину могут добавить разные пользователи — сохраняются обе."""
    other = make_client(make_user("second"))

    for c in (client, other):
        response = c.post(
            "/cars/new", data={**FORM, "vin": VIN}, follow_redirects=False
        )
        assert response.status_code == 303

    cars = session.scalars(select(Car)).all()
    assert [c.vin for c in cars] == [VIN, VIN]


def test_edit_shows_and_changes_vin(client, session, car):
    """Форма редактирования показывает VIN; его можно исправить или стереть."""
    car.vin = VIN
    session.commit()

    assert f'value="{VIN}"' in client.get(f"/cars/{car.id}/edit").text

    client.post(f"/cars/{car.id}/edit", data={**FORM, "vin": ""})
    session.expire_all()
    assert car.vin is None


# --- Не выдаём, что VIN уже кто-то проверял ---


def test_cached_vin_looks_like_new(client, fake_apipoint, make_user, make_client):
    """Для другого пользователя VIN из базы выглядит как новый: тот же ответ,
    та же отметка в лимите."""
    fake_apipoint()
    first = fill(client)
    other = make_client(make_user("second"))

    second = fill(other)

    assert second.text == first.text
    assert "осталось 9 из 10" in second.text


def test_cached_vin_after_limit(client, session, fake_apipoint, make_user, make_client):
    """После лимита не работает и VIN, который уже есть в базе у других."""
    calls = fake_apipoint()
    fill(client)  # VIN теперь в базе
    second = make_user("second")
    session.add_all(
        VinRequest(user_id=second.id, vin="XW8ZZZ61ZCG060700", answered=True)
        for _ in range(DAILY_LIMIT)
    )
    session.commit()

    response = fill(make_client(second))

    assert "лимит на сутки" in response.text
    assert len(calls) == 1


def test_retry_after_service_error_counts(client, fake_apipoint):
    """Повтор после ошибки сервиса — снова платный запрос, он учитывается."""
    fake_apipoint(http_error(503))
    assert "осталось 9 из 10" in fill(client).text
    fake_apipoint()

    assert "осталось 8 из 10" in fill(client).text


# --- Добавление по VIN отличается от ручного ---


def test_vin_mode_hides_fields_until_decoded(client):
    """По VIN сначала только поле VIN: характеристики скрыты до расшифровки."""
    html = client.get("/cars/new?by=vin").text

    assert '<div class="car-details" id="car-details" hidden>' in html
    assert 'name="vin"' in html and "required autofocus" in html
    assert 'href="/cars/new?by=manual"' in html


def test_manual_mode_shows_fields(client):
    html = client.get("/cars/new?by=manual").text

    assert '<div class="car-details" id="car-details">' in html


def test_vin_mode_requires_vin(client, session):
    """По VIN без VIN сохранить нельзя — подсказка добавить вручную."""
    response = client.post("/cars/new", data={**FORM, "by": "vin", "vin": ""})

    assert response.status_code == 422
    assert "Введите VIN" in response.text
    assert session.scalars(select(Car)).all() == []
    # После ошибки поля открыты: пользователь их уже заполнял
    assert '<div class="car-details" id="car-details">' in response.text


def test_vin_mode_error_offers_manual(client, fake_apipoint):
    """Расшифровать не удалось — ссылка на добавление вручную."""
    fake_apipoint(NOT_FOUND)

    response = client.post(
        "/cars/new/vin", data={"by": "vin", "vin": VIN, "mileage": ""}
    )

    assert "Машина с таким VIN не найдена" in response.text
    assert 'href="/cars/new?by=manual"' in response.text
    assert response.headers["HX-Reswap"] == "none"


def test_vin_mode_save_after_fill(client, session, fake_apipoint):
    """По VIN: расшифровали, дополнили пробег и коробку — машина сохранена."""
    fake_apipoint()
    fill(client)

    response = client.post(
        "/cars/new",
        data={
            "by": "vin",
            "vin": VIN,
            "brand": "Volkswagen",
            "model": "Polo",
            "generation": "V 2009-2015",
            "engine": "1.6 (105 л.с.)",
            "year": "2012",
            "mileage": "150000",
            "gearbox": "manual",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    [car] = session.scalars(select(Car)).all()
    assert (car.vin, car.model, car.engine) == (VIN, "Polo", "1.6 (105 л.с.)")
