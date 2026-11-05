"""Пользователи: проверка форм, пароли, сессии входа, CSRF-токены и адрес возврата."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from pwdlib import PasswordHash
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import User, UserSession

SESSION_COOKIE = "session_token"
SESSION_DAYS = 30
SESSION_MAX_AGE = SESSION_DAYS * 24 * 60 * 60

# CSRF-токен страниц /login и /register, где сессии ещё нет
PRE_LOGIN_CSRF_COOKIE = "csrf_pre"
PRE_LOGIN_CSRF_MAX_AGE = 24 * 60 * 60

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_]{3,30}")

_hasher = PasswordHash.recommended()
# Проверяем по нему пароль, если логина нет: ответ занимает столько же времени,
# и по нему нельзя понять, существует ли логин
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class LoginRequired(Exception):
    """Страница требует входа; ответ формирует обработчик в app/main.py."""


def utc_now() -> datetime:
    """Текущее время в UTC без часового пояса — так даты хранятся в базе."""
    return datetime.now(UTC).replace(tzinfo=None)


def hash_password(password: str) -> str:
    """Хеш Argon2 для хранения в базе."""
    return _hasher.hash(password)


def find_user(session: Session, username: str) -> User | None:
    """Пользователь по логину без учёта регистра."""
    query = select(User).where(func.lower(User.username) == username.lower())
    return session.scalars(query).first()


def is_valid_username(username: str) -> bool:
    return USERNAME_PATTERN.fullmatch(username) is not None


def validate_registration(
    session: Session, username: str, password: str, password_repeat: str
) -> dict[str, str]:
    """Ошибки формы регистрации по полям; если ошибок нет, словарь пустой."""
    errors = {}
    if not is_valid_username(username):
        errors["username"] = "Логин — от 3 до 30 символов: латиница, цифры и _"
    elif find_user(session, username):
        errors["username"] = "Этот логин уже занят"
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        errors["password"] = (
            f"Пароль — от {MIN_PASSWORD_LENGTH} до {MAX_PASSWORD_LENGTH} символов"
        )
    if password != password_repeat:
        errors["password_repeat"] = "Пароли не совпадают"
    return errors


def authenticate(session: Session, username: str, password: str) -> User | None:
    """Пользователь, если логин и пароль верны; иначе None."""
    user = find_user(session, username) if is_valid_username(username) else None
    if user is None or len(password) > MAX_PASSWORD_LENGTH:
        _hasher.verify(password[:MAX_PASSWORD_LENGTH], _DUMMY_HASH)
        return None
    return user if _hasher.verify(password, user.password_hash) else None


def hash_token(token: str) -> str:
    """Хеш токена сессии. Токен случайный и длинный, поэтому хватает SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(session: Session, user: User) -> str:
    """Создаёт сессию и возвращает токен для cookie. Сохраняет вызывающий код.

    Заодно удаляет просроченные сессии этого пользователя.
    """
    session.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.expires_at <= utc_now()
        )
    )
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utc_now() + timedelta(days=SESSION_DAYS),
        )
    )
    return token


def user_by_token(session: Session, token: str) -> User | None:
    """Пользователь действующей сессии; просроченная сессия — как её отсутствие."""
    query = (
        select(User)
        .join(UserSession)
        .where(
            UserSession.token_hash == hash_token(token),
            UserSession.expires_at > utc_now(),
        )
    )
    return session.scalars(query).first()


def end_session(session: Session, token: str) -> None:
    """Удаляет сессию: после этого её токен больше не работает."""
    session.execute(
        delete(UserSession).where(UserSession.token_hash == hash_token(token))
    )
    session.commit()


def csrf_token_for(session_token: str) -> str:
    """CSRF-токен сессии. В базе не хранится и после выхода перестаёт действовать.

    По нему нельзя восстановить токен сессии, поэтому его можно показывать в HTML.
    """
    return hmac.new(session_token.encode(), b"csrf", hashlib.sha256).hexdigest()


def new_pre_login_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str, sent: object) -> bool:
    """Сравнение токенов за постоянное время; пустой токен не подходит."""
    if not expected or not isinstance(sent, str):
        return False
    return hmac.compare_digest(expected.encode(), sent.encode())


def safe_next(target: str | None) -> str:
    """Адрес возврата после входа: только путь внутри сайта, иначе — главная.

    Обратная косая черта и управляющие символы запрещены: браузер считает «\\»
    за «/», а табуляцию и перевод строки удаляет, и путь стал бы чужим адресом.
    """
    home = "/"
    if not target or not target.startswith("/") or target.startswith("//"):
        return home
    if "\\" in target or any(ord(char) < 32 or ord(char) == 127 for char in target):
        return home
    try:
        parts = urlsplit(target)
    except ValueError:
        return home
    return home if parts.scheme or parts.netloc else target
