import hashlib
import logging
import re
from datetime import timedelta
from urllib.parse import unquote

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Car, CarSurvey, User, UserSession
from app.routers import auth, cars
from app.services.users import (
    SESSION_COOKIE,
    csrf_token_for,
    safe_next,
    start_session,
    utc_now,
)
from tests.conftest import TEST_PASSWORD, make_test_car

PASSWORD = "надёжный-пароль"
LOGIN_ERROR = "Неверный логин или пароль"

# Все адреса с номером машины: метод и путь. Новый адрес без строки здесь уронит
# test_car_urls_list_is_complete — так он не останется без проверки владельца
CAR_URLS = [
    ("GET", "/cars/{car_id}/edit"),
    ("POST", "/cars/{car_id}/edit"),
    ("POST", "/cars/{car_id}/delete"),
    ("GET", "/cars/{car_id}/survey"),
    ("GET", "/cars/{car_id}/survey/edit"),
    ("POST", "/cars/{car_id}/survey/edit"),
    ("GET", "/cars/{car_id}/survey/long-trip"),
    ("POST", "/cars/{car_id}/survey/clear"),
]
# Работающая форма чужой машины: если бы проверки не было, она бы что-то изменила
CAR_FORM = {
    "brand": "Lada",
    "model": "Vesta",
    "generation": "I 2015-2022",
    "engine": "1.8 (122 л.с.)",
    "year": "2017",
    "mileage": "1",
    "gearbox": "robot",
    "purposes_other": "Чужой ответ",
}
OPEN_URLS = ("/login", "/register")


def app_routes():
    """Все маршруты из app.routes: (метод, путь). У /static методов нет — он не входит.

    Подключённые роутеры лежат в app.routes обёртками, их разворачивает FastAPI.
    """
    return [
        (method, route.path)
        for route in iter_route_contexts(app.routes)
        for method in route.methods or ()
    ]


def fill_path(path, car_id=1):
    return path.replace("{car_id}", str(car_id))


def page_token(client, url):
    """CSRF-токен из скрытого поля страницы."""
    html = client.get(url).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def register(client, username="Arsen", password=PASSWORD, repeat=None, **kwargs):
    """Отправляет форму регистрации с токеном со страницы, как браузер."""
    data = {
        "username": username,
        "password": password,
        "password_repeat": password if repeat is None else repeat,
        "csrf_token": page_token(client, "/register"),
    }
    return client.post("/register", data=data, follow_redirects=False, **kwargs)


def login(client, username, password, next_url=""):
    """Отправляет форму входа с токеном со страницы, как браузер."""
    data = {
        "username": username,
        "password": password,
        "next": next_url,
        "csrf_token": page_token(client, "/login"),
    }
    return client.post("/login", data=data, follow_redirects=False)


def users_in(session):
    session.expire_all()
    return session.scalars(select(User)).all()


@pytest.fixture
def neighbour(make_user):
    return make_user("neighbour")


@pytest.fixture
def neighbour_client(make_client, neighbour):
    return make_client(neighbour)


# --- Регистрация ---


def test_register_signs_in_and_goes_home(anon_client, session):
    """После регистрации пользователь сразу вошёл и попадает на главную."""
    response = register(anon_client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    [user] = users_in(session)
    assert user.username == "Arsen"
    assert ">Arsen<" in anon_client.get("/").text


@pytest.mark.parametrize(
    "username, ok",
    [
        ("ab", False),
        ("abc", True),
        ("a" * 30, True),
        ("a" * 31, False),
        ("Arsen_2026", True),
        ("Арсений", False),
        ("with space", False),
        ("with-dash", False),
        ("arsen\n", False),
        ("", False),
    ],
)
def test_register_username_rules(anon_client, session, username, ok):
    """Логин: 3–30 символов, латиница, цифры и _."""
    response = register(anon_client, username=username)

    assert response.status_code == (303 if ok else 422)
    assert len(users_in(session)) == (1 if ok else 0)
    if not ok:
        assert "от 3 до 30 символов" in response.text


@pytest.mark.parametrize(
    "length, ok", [(7, False), (8, True), (128, True), (129, False)]
)
def test_register_password_length(anon_client, session, length, ok):
    """Пароль: 8–128 символов."""
    response = register(anon_client, password="п" * length)

    assert response.status_code == (303 if ok else 422)
    assert len(users_in(session)) == (1 if ok else 0)
    if not ok:
        assert "от 8 до 128 символов" in response.text


def test_register_passwords_must_match(anon_client, session):
    response = register(anon_client, repeat="другой-пароль")

    assert response.status_code == 422
    assert "Пароли не совпадают" in response.text
    assert users_in(session) == []


def test_register_username_taken_in_other_case(anon_client, session):
    """Логин уникален без учёта регистра."""
    register(anon_client, username="Arsen")
    other = TestClient(app)

    response = register(other, username="ARSEN")

    assert response.status_code == 422
    assert "Этот логин уже занят" in response.text
    assert len(users_in(session)) == 1


def test_register_error_stands_at_field_and_keeps_username(anon_client):
    """Ошибка — у своего поля, логин не теряется, пароль обратно не выводится."""
    response = register(anon_client, username="arsen", password="коротко")

    html = response.text
    assert 'value="arsen"' in html
    assert "коротко" not in html
    field = html.split('for="password"', 1)[1].split('for="password_repeat"', 1)[0]
    assert "от 8 до 128 символов" in field


# --- Вход ---


def test_login_opens_session(anon_client, user):
    response = login(anon_client, "driver", TEST_PASSWORD)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert ">driver<" in anon_client.get("/").text


def test_login_ignores_username_case(anon_client, user):
    assert login(anon_client, "DRIVER", TEST_PASSWORD).status_code == 303


@pytest.mark.parametrize(
    "username, password",
    [
        ("driver", "неверный-пароль"),
        ("nobody", TEST_PASSWORD),
        ("", ""),
        ("driver", "п" * 129),
    ],
)
def test_login_failure_has_one_message(anon_client, user, username, password):
    """Сообщение не подсказывает, что именно неверно."""
    response = login(anon_client, username, password)

    assert response.status_code == 401
    assert LOGIN_ERROR in response.text
    assert SESSION_COOKIE not in anon_client.cookies


def test_wrong_password_and_unknown_user_look_the_same(anon_client, user):
    """Ответы отличаются только введённым логином."""
    wrong = login(anon_client, "driver", "неверный-пароль").text
    unknown = login(anon_client, "nobody", "неверный-пароль").text

    assert wrong.replace("driver", "X") == unknown.replace("nobody", "X")


@pytest.mark.parametrize("url", OPEN_URLS)
@pytest.mark.parametrize("method", ["get", "post"])
def test_signed_in_user_goes_home(client, session, method, url):
    """Вошедшему /login и /register не нужны — на главную, ничего не меняется."""
    response = client.request(
        method,
        url,
        data={"username": "other", "password": PASSWORD} if method == "post" else None,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert len(users_in(session)) == 1


# --- Возврат после входа ---


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.com",
        "http://evil.com/history",
        "//evil.com",
        "/\\evil.com",
        "\\\\evil.com",
        "/\t/evil.com",
        "/\n/evil.com",
        "javascript:alert(1)",
        "evil.com",
        "",
    ],
)
def test_next_outside_site_leads_home(anon_client, user, target):
    """Чужой адрес не принимается: и в проверке, и в настоящем входе."""
    assert safe_next(target) == "/"
    response = login(anon_client, "driver", TEST_PASSWORD, next_url=target)
    assert response.headers["location"] == "/"


@pytest.mark.parametrize("target", ["/history", "/history?q=масло&type=oil", "/cars/1"])
def test_next_inside_site_is_kept(anon_client, user, target):
    response = login(anon_client, "driver", TEST_PASSWORD, next_url=target)

    # Кириллицу в адресе ответ кодирует процентами: путь остаётся тем же
    assert unquote(response.headers["location"]) == target


def test_redirect_to_login_remembers_page(anon_client, user):
    """Без входа — на /login, а после входа назад на ту же страницу."""
    response = anon_client.get("/history?type=oil", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fhistory%3Ftype%3Doil"

    html = anon_client.get(response.headers["location"]).text
    assert 'name="next" value="/history?type=oil"' in html


def test_login_error_keeps_next(anon_client, user):
    response = login(anon_client, "driver", "неверный-пароль", next_url="/history")

    assert 'name="next" value="/history"' in response.text


def test_login_page_drops_foreign_next(anon_client):
    html = anon_client.get("/login", params={"next": "//evil.com"}).text

    assert "evil.com" not in html


# --- Выход ---


def test_logout_is_button_in_post_form(client):
    html = client.get("/").text
    header = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]

    assert '<form method="post" action="/logout">' in header
    assert ">Выйти</button>" in header
    assert ">driver<" in header
    assert 'href="/logout"' not in html


def test_logout_ends_session(client, session):
    """После выхода страницы требуют входа, а старый токен больше не работает."""
    token = client.cookies[SESSION_COOKIE]

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303
    session.expire_all()
    assert session.scalars(select(UserSession)).all() == []

    client.cookies.set(SESSION_COOKIE, token)
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/login"


def test_logout_by_get_does_not_sign_out(client):
    assert client.get("/logout").status_code == 405
    assert client.get("/", follow_redirects=False).status_code == 200


def test_logout_ends_only_this_session(client, make_client, user):
    other_device = make_client(user)

    client.post("/logout")

    assert other_device.get("/", follow_redirects=False).status_code == 200


# --- Без входа ---


def protected_routes():
    return [(m, p) for m, p in app_routes() if p not in OPEN_URLS]


def test_routes_are_found():
    """Список адресов не пуст: иначе тесты ниже ничего бы не проверяли."""
    assert {
        ("GET", "/"),
        ("GET", "/history"),
        ("GET", "/cars/new"),
        ("POST", "/logout"),
        ("GET", "/cars/{car_id}/edit"),
    } <= set(protected_routes())


@pytest.mark.parametrize("method, path", protected_routes())
def test_every_page_requires_login(anon_client, method, path):
    """Любой адрес, кроме /login и /register, без входа ведёт на /login."""
    response = anon_client.request(method, fill_path(path), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("url", OPEN_URLS)
def test_open_pages_work_without_login(anon_client, url):
    assert anon_client.get(url).status_code == 200


def test_static_is_open(anon_client):
    assert anon_client.get("/static/style.css").status_code == 200


@pytest.mark.parametrize("url", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_closed(anon_client, url):
    assert anon_client.get(url, follow_redirects=False).status_code == 404


def test_wrong_car_number_without_login_leads_to_login(anon_client):
    """Адрес проверяется уже после входа."""
    response = anon_client.get("/cars/abc/edit", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_htmx_request_without_login(anon_client):
    """Обычный редирект HTMX отрисовал бы страницу входа внутри формы."""
    response = anon_client.get(
        "/cars/new/fields", headers={"HX-Request": "true"}, follow_redirects=False
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/login"


# --- Что и как хранится ---


def test_password_stored_only_as_hash(anon_client, session):
    register(anon_client)

    [user] = users_in(session)
    assert user.password_hash.startswith("$argon2")
    assert PASSWORD not in user.password_hash


def test_password_not_in_logs(anon_client, caplog):
    with caplog.at_level(logging.DEBUG):
        register(anon_client)
        login(anon_client, "Arsen", PASSWORD)

    assert PASSWORD not in caplog.text


def test_session_cookie_flags_and_stored_hash(anon_client, session, user):
    """В базе только хеш случайного токена; cookie закрыта от скриптов и живёт 30 дней."""
    response = login(anon_client, "driver", TEST_PASSWORD)

    cookie = next(
        header
        for header in response.headers.get_list("set-cookie")
        if header.startswith(SESSION_COOKIE)
    )
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert f"Max-Age={30 * 24 * 60 * 60}" in cookie
    assert "Path=/" in cookie

    token = anon_client.cookies[SESSION_COOKIE]
    assert len(token) >= 32
    session.expire_all()
    [stored] = session.scalars(select(UserSession)).all()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token != stored.token_hash
    left = stored.expires_at - utc_now()
    assert timedelta(days=29, hours=23) < left <= timedelta(days=30)


def test_expired_session_does_not_work(client, session):
    stored = session.scalars(select(UserSession)).one()
    stored.expires_at = utc_now() - timedelta(seconds=1)
    session.commit()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_removes_own_expired_sessions_only(anon_client, session, user, make_user):
    """При входе удаляются просроченные сессии этого пользователя, и только они."""
    neighbour = make_user("neighbour")
    past = utc_now() - timedelta(days=1)
    start_session(session, user)
    start_session(session, neighbour)
    session.flush()
    stored = session.scalars(select(UserSession)).all()
    stored[0].expires_at = past
    stored[1].expires_at = past
    start_session(session, user)  # действующая
    session.commit()
    active_hash = session.scalars(
        select(UserSession.token_hash).where(
            UserSession.user_id == user.id, UserSession.expires_at > utc_now()
        )
    ).one()

    login(anon_client, "driver", TEST_PASSWORD)

    session.expire_all()
    left = session.scalars(select(UserSession)).all()
    mine = [s for s in left if s.user_id == user.id]
    others = [s for s in left if s.user_id == neighbour.id]
    assert len(mine) == 2  # прежняя действующая и новая
    assert all(s.expires_at > utc_now() for s in mine)
    assert active_hash in {s.token_hash for s in mine}
    assert len(others) == 1  # просроченная соседа осталась


# --- Два пользователя ---


def test_car_urls_list_is_complete():
    """В CAR_URLS есть все адреса с номером машины из приложения."""
    found = {(m, p) for m, p in app_routes() if "{car_id}" in p}

    assert found == set(CAR_URLS)


@pytest.mark.parametrize("method, path", CAR_URLS)
def test_foreign_car_is_404_and_unchanged(
    client, neighbour_client, session, car, method, path
):
    """На каждом адресе чужая машина — 404: данные не видны и не меняются."""
    session.add(CarSurvey(car=car, purposes_other="Секретная рыбалка"))
    session.commit()

    response = neighbour_client.request(
        method,
        fill_path(path, car.id),
        data=CAR_FORM if method == "POST" else None,
        follow_redirects=False,
    )

    assert response.status_code == 404
    for secret in ("Granta", "Секретная рыбалка", "84500", "84 500"):
        assert secret not in response.text
    session.expire_all()
    assert session.get(Car, car.id).model == "Granta"
    assert session.get(Car, car.id).mileage == 84500
    assert session.get(Car, car.id).owner_id == car.owner_id
    assert car.survey.purposes_other == "Секретная рыбалка"


@pytest.mark.parametrize("method, path", CAR_URLS)
def test_car_without_owner_is_404(client, session, method, path):
    """Машина, добавленная до появления пользователей, не видна никому."""
    old = Car(make="Lada", model="Niva", year=2010, mileage=1)
    session.add(old)
    session.commit()

    response = client.request(
        method,
        fill_path(path, old.id),
        data=CAR_FORM if method == "POST" else None,
        follow_redirects=False,
    )

    assert response.status_code == 404
    session.expire_all()
    assert session.get(Car, old.id) is not None


@pytest.mark.parametrize(
    "url", ["/", "/history", "/maintenance", "/expenses", "/assistant"]
)
def test_each_user_sees_own_car(client, neighbour_client, neighbour, session, car, url):
    make_test_car(session, neighbour, make="Kia", model="Rio", year=2018)

    mine = client.get(url).text
    theirs = neighbour_client.get(url).text

    assert "Lada Granta" in mine
    assert "Kia Rio" not in mine
    assert "Kia Rio" in theirs
    assert "Lada Granta" not in theirs
    assert ">driver<" in mine
    assert ">neighbour<" in theirs


def test_old_car_is_not_shown(client, session):
    """Машина без владельца не попадает даже в карточку нового пользователя."""
    session.add(Car(make="Lada", model="Niva", year=2010, mileage=1))
    session.commit()

    html = client.get("/").text

    assert "Lada Niva" not in html
    assert "Машина не добавлена" in html


def test_new_car_gets_owner_and_second_is_not_created(client, session, user):
    response = client.post("/cars/new", data=CAR_FORM, follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    [car] = session.scalars(select(Car)).all()
    assert car.owner_id == user.id

    response = client.post("/cars/new", data=CAR_FORM, follow_redirects=False)

    assert response.headers["location"] == "/"
    session.expire_all()
    assert len(session.scalars(select(Car)).all()) == 1


def test_two_users_can_have_cars(client, neighbour_client, session):
    client.post("/cars/new", data=CAR_FORM)
    neighbour_client.post("/cars/new", data=CAR_FORM)

    session.expire_all()
    assert len(session.scalars(select(Car)).all()) == 2


def test_car_can_be_added_again_after_delete(client, session, car):
    client.post(f"/cars/{car.id}/delete")

    assert client.get("/cars/new", follow_redirects=False).status_code == 200
    assert (
        client.post("/cars/new", data=CAR_FORM, follow_redirects=False)
        .headers["location"]
        .endswith("/survey/edit")
    )


# --- CSRF ---


def post_routes():
    return [path for method, path in app_routes() if method == "POST"]


def test_post_routes_are_found():
    """Список адресов не пуст: иначе тесты ниже ничего бы не проверяли."""
    assert {"/login", "/register", "/logout", "/cars/new"} <= set(post_routes())


def without_token(client):
    del client.headers["X-CSRF-Token"]
    return client


def db_state(session):
    """Всё, что мог бы изменить POST: пользователи, сессии, машины и анкеты."""
    session.expire_all()
    return (
        [(u.id, u.username) for u in session.scalars(select(User))],
        [s.token_hash for s in session.scalars(select(UserSession))],
        [(c.id, c.model, c.mileage) for c in session.scalars(select(Car))],
        [(s.car_id, s.purposes_other) for s in session.scalars(select(CarSurvey))],
    )


@pytest.mark.parametrize("path", post_routes())
def test_every_post_without_token_is_403(client, anon_client, session, car, path):
    """Каждый POST-адрес приложения без токена — 403, ничего не меняется."""
    session.add(CarSurvey(car=car, purposes_other="Ответ"))
    session.commit()
    sender = anon_client if path in OPEN_URLS else without_token(client)
    before = db_state(session)

    response = sender.post(
        fill_path(path, car.id),
        data={**CAR_FORM, "username": "newbie", "password": PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert db_state(session) == before


@pytest.mark.parametrize("path", post_routes())
def test_every_post_with_wrong_token_is_403(client, anon_client, session, car, path):
    sender = anon_client if path in OPEN_URLS else without_token(client)
    anon_client.get("/login")  # выдаёт cookie со своим токеном
    before = db_state(session)

    response = sender.post(
        fill_path(path, car.id),
        data={**CAR_FORM, "username": "newbie", "password": PASSWORD}
        | {"csrf_token": "неверный"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert db_state(session) == before


def test_token_of_other_user_is_403(client, neighbour_client, session, car):
    """Токен привязан к сессии: токен соседа к этой сессии не подходит."""
    client.headers["X-CSRF-Token"] = neighbour_client.headers["X-CSRF-Token"]

    response = client.post(f"/cars/{car.id}/delete")

    assert response.status_code == 403
    session.expire_all()
    assert session.get(Car, car.id) is not None


def test_token_of_ended_session_is_403(client, make_client, user, session, car):
    """После выхода и нового входа прежний токен страницы больше не подходит."""
    old_token = client.headers["X-CSRF-Token"]
    client.post("/logout")

    new_client = make_client(user)
    new_client.headers["X-CSRF-Token"] = old_token

    assert new_client.post(f"/cars/{car.id}/delete").status_code == 403


def test_token_is_tied_to_session():
    assert csrf_token_for("сессия-1") != csrf_token_for("сессия-2")
    assert csrf_token_for("сессия-1") == csrf_token_for("сессия-1")
    assert "сессия-1" not in csrf_token_for("сессия-1")


def test_token_in_query_string_is_not_accepted(client, session, car):
    token = client.headers["X-CSRF-Token"]
    without_token(client)

    response = client.post(f"/cars/{car.id}/delete?csrf_token={token}")

    assert response.status_code == 403


def test_form_field_is_accepted(client, session, car):
    """Обычная форма передаёт токен скрытым полем — со страницы, как браузер."""
    token = page_token(client, f"/cars/{car.id}/edit")
    without_token(client)

    response = client.post(
        f"/cars/{car.id}/edit",
        data={**CAR_FORM, "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    assert session.get(Car, car.id).model == "Vesta"


def test_survey_form_field_is_accepted(client, session, car):
    """Анкета читает форму сама: поле с токеном не должно ей мешать."""
    url = f"/cars/{car.id}/survey/edit"
    token = page_token(client, url)
    without_token(client)

    response = client.post(
        url,
        data={"purposes": ["work"], "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    assert session.scalars(select(CarSurvey)).one().purposes == ["work"]


def test_htmx_header_is_accepted(client, session, car):
    """Удаление машины идёт через HTMX: токен приходит заголовком из <body>."""
    html = client.get(f"/cars/{car.id}/edit").text
    token = re.search(r'"X-CSRF-Token": "([^"]+)"', html).group(1)
    client.headers["X-CSRF-Token"] = token

    assert client.post(f"/cars/{car.id}/delete").status_code == 204


def test_every_post_form_has_token(client, session, car):
    """Каждая POST-форма страниц содержит токен, иначе браузер получит 403."""
    session.add(CarSurvey(car=car))
    session.commit()
    urls = [
        "/",
        "/history",
        "/maintenance",
        "/expenses",
        "/assistant",
        f"/cars/{car.id}/edit",
        f"/cars/{car.id}/survey",
        f"/cars/{car.id}/survey/edit",
    ]
    for url in urls:
        forms = re.findall(
            r'<form[^>]*method="post"[^>]*>.*?</form>', client.get(url).text, re.DOTALL
        )
        assert forms, url
        for form in forms:
            assert 'name="csrf_token"' in form, url


def test_new_car_form_has_token(make_client, make_user):
    html = make_client(make_user("newbie")).get("/cars/new").text

    form = html.split('<form class="car-form"', 1)[1].split("</form>", 1)[0]
    assert 'name="csrf_token"' in form


def test_token_stays_out_of_htmx_get_urls(client):
    """hx-get с hx-include отправил бы скрытое поле в адресе и в логи сервера."""
    assert 'hx-params="not csrf_token"' in client.get("/").text


def test_pre_login_cookie_flags(anon_client):
    response = anon_client.get("/login")

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("csrf_pre=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=86400" in cookie


def test_pre_login_cookie_is_not_replaced(anon_client):
    """Форма во второй вкладке не должна сломать форму в первой."""
    first = page_token(anon_client, "/login")

    assert page_token(anon_client, "/register") == first
    assert "set-cookie" not in anon_client.get("/login").headers


def test_login_token_from_other_browser_is_403(anon_client, user):
    """Токен в форме должен совпасть с cookie этого браузера."""
    other_token = page_token(TestClient(app), "/login")
    anon_client.get("/login")

    response = anon_client.post(
        "/login",
        data={
            "username": "driver",
            "password": TEST_PASSWORD,
            "csrf_token": other_token,
        },
    )

    assert response.status_code == 403
    assert SESSION_COOKIE not in anon_client.cookies


def test_forbidden_page_has_hint(client, car):
    without_token(client)

    response = client.post(f"/cars/{car.id}/delete")

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "Обновите страницу и повторите" in response.text
    assert '<header class="topbar">' in response.text


def test_forbidden_htmx_refreshes_page(client, car):
    without_token(client)

    response = client.post(f"/cars/{car.id}/delete", headers={"HX-Request": "true"})

    assert response.status_code == 403
    assert response.headers["HX-Refresh"] == "true"


# --- Страница 404 ---


def test_not_found_page_in_app_style(client):
    response = client.get("/нет-такой-страницы")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Страница не найдена" in response.text
    assert '<header class="topbar">' in response.text
    assert "/static/style.css" in response.text


def test_not_found_page_without_login(anon_client):
    response = anon_client.get("/нет-такой-страницы")

    assert response.status_code == 404
    assert "Страница не найдена" in response.text


def test_foreign_car_page_equals_missing_car_page(client, neighbour_client, car):
    """Чужая машина выглядит точно как несуществующая: номер нельзя подобрать."""
    foreign = neighbour_client.get(f"/cars/{car.id}/edit")
    missing = neighbour_client.get("/cars/999/edit")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.content == missing.content
    assert "Машина" not in foreign.text


def test_not_found_for_htmx_has_no_page(client):
    response = client.get("/cars/999/survey/long-trip", headers={"HX-Request": "true"})

    assert response.status_code == 404
    assert response.content == b""


# --- Гонки: проверка прошла, но ограничение в базе сработало ---


def test_register_race_shows_taken_username(anon_client, session, user, monkeypatch):
    """Логин заняли между проверкой и сохранением: та же понятная ошибка."""
    monkeypatch.setattr(auth, "validate_registration", lambda *args: {})

    response = register(anon_client, username="DRIVER")

    assert response.status_code == 422
    assert "Этот логин уже занят" in response.text
    assert len(users_in(session)) == 1


def test_create_car_race_keeps_one_car(client, session, car, monkeypatch):
    """Форму отправили дважды одновременно: вторая машина не появляется."""
    monkeypatch.setattr(cars, "get_car", lambda *args: None)

    response = client.post("/cars/new", data=CAR_FORM, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    session.expire_all()
    assert len(session.scalars(select(Car)).all()) == 1
