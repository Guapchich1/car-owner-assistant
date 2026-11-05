"""Регистрация, вход и выход."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.routers.pages import DbSession, optional_user, templates
from app.services.users import (
    PRE_LOGIN_CSRF_COOKIE,
    PRE_LOGIN_CSRF_MAX_AGE,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    authenticate,
    end_session,
    hash_password,
    new_pre_login_csrf_token,
    safe_next,
    start_session,
    tokens_match,
    validate_registration,
)

# Открыт без входа: /register и /login
router = APIRouter()
# Подключается в app/main.py с проверкой входа и CSRF-токена
protected_router = APIRouter()

# Поле формы — строка, по умолчанию пустая: сообщения об ошибках — наши, а не FastAPI
FormField = Annotated[str, Form()]

LOGIN_ERROR = "Неверный логин или пароль"
TAKEN_USERNAME_ERROR = "Этот логин уже занят"


def render_auth_page(
    request: Request, template: str, context: dict, status_code: int = 200
) -> HTMLResponse:
    """Страница входа или регистрации с токеном в скрытом поле и в cookie.

    Выданную cookie не заменяем: иначе форма в другой вкладке получит 403.
    """
    cookie = request.cookies.get(PRE_LOGIN_CSRF_COOKIE)
    token = cookie or new_pre_login_csrf_token()
    response = templates.TemplateResponse(
        request, template, {**context, "csrf_token": token}, status_code=status_code
    )
    if not cookie:
        response.set_cookie(
            PRE_LOGIN_CSRF_COOKIE,
            token,
            max_age=PRE_LOGIN_CSRF_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
    return response


def check_pre_login_csrf(request: Request, sent: str) -> None:
    """Токен из формы должен совпасть с cookie этого браузера; иначе — 403."""
    if not tokens_match(request.cookies.get(PRE_LOGIN_CSRF_COOKIE, ""), sent):
        raise HTTPException(status_code=403)


def signed_in_redirect(session_token: str, target: str) -> RedirectResponse:
    """Переход после входа: запоминает сессию в cookie, токен до входа больше не нужен."""
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(PRE_LOGIN_CSRF_COOKIE)
    return response


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, session: DbSession) -> Response:
    """Форма регистрации. Вошедшему она не нужна — на главную."""
    if optional_user(request, session):
        return RedirectResponse("/", status_code=303)
    return render_auth_page(request, "register.html", {"username": "", "errors": {}})


@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    session: DbSession,
    username: FormField = "",
    password: FormField = "",
    password_repeat: FormField = "",
    csrf_token: FormField = "",
) -> Response:
    """Создаёт пользователя и сразу выполняет вход. При ошибке логин не теряется."""
    if optional_user(request, session):
        return RedirectResponse("/", status_code=303)
    check_pre_login_csrf(request, csrf_token)
    errors = validate_registration(session, username, password, password_repeat)
    if not errors:
        user = User(username=username, password_hash=hash_password(password))
        session.add(user)
        try:
            session.flush()
        except IntegrityError:
            # Такой же логин только что занял другой запрос
            session.rollback()
            errors = {"username": TAKEN_USERNAME_ERROR}
    if errors:
        context = {"username": username, "errors": errors}
        return render_auth_page(request, "register.html", context, status_code=422)
    token = start_session(session, user)
    session.commit()
    return signed_in_redirect(token, "/")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session: DbSession, next: str = "") -> Response:
    """Форма входа. Вошедшему она не нужна — на главную."""
    if optional_user(request, session):
        return RedirectResponse("/", status_code=303)
    context = {"username": "", "error": None, "next": safe_next(next)}
    return render_auth_page(request, "login.html", context)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    session: DbSession,
    username: FormField = "",
    password: FormField = "",
    next: FormField = "",
    csrf_token: FormField = "",
) -> Response:
    """Вход. Сообщение об ошибке не подсказывает, что именно неверно."""
    if optional_user(request, session):
        return RedirectResponse("/", status_code=303)
    check_pre_login_csrf(request, csrf_token)
    user = authenticate(session, username, password)
    if user is None:
        context = {"username": username, "error": LOGIN_ERROR, "next": safe_next(next)}
        return render_auth_page(request, "login.html", context, status_code=401)
    token = start_session(session, user)
    session.commit()
    return signed_in_redirect(token, safe_next(next))


@protected_router.post("/logout")
def logout(request: Request, session: DbSession) -> Response:
    """Выход: сессия удаляется из базы, cookie — из браузера."""
    end_session(session, request.cookies[SESSION_COOKIE])
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
