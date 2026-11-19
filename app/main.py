"""Запуск веб-приложения «Помощник автовладельца»."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.routers import auth, cars, pages, survey
from app.routers.pages import get_current_user, templates, verify_csrf
from app.services.users import LoginRequired

BASE_DIR = Path(__file__).resolve().parent

# Встроенная документация API не нужна, а без входа она была бы открыта:
# зависимости роутеров на неё не действуют
app = FastAPI(
    title="Помощник автовладельца", docs_url=None, redoc_url=None, openapi_url=None
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Без входа открыты только /register, /login и /static. Каждый POST здесь проверяет
# CSRF-токен; у /login и /register, где сессии ещё нет, проверка своя (routers/auth.py)
PROTECTED = [Depends(get_current_user), Depends(verify_csrf)]
app.include_router(auth.router)
app.include_router(auth.protected_router, dependencies=PROTECTED)
app.include_router(pages.router, dependencies=PROTECTED)
app.include_router(cars.router, dependencies=PROTECTED)
app.include_router(survey.router, dependencies=PROTECTED)


def is_htmx(request: Request) -> bool:
    return "HX-Request" in request.headers


@app.exception_handler(LoginRequired)
def redirect_to_login(request: Request, exc: LoginRequired) -> Response:
    """Без входа — на /login; после входа обычный переход вернётся на ту же страницу."""
    if is_htmx(request):
        # Обычный редирект HTMX отрисовал бы внутри страницы
        return Response(status_code=204, headers={"HX-Redirect": "/login"})
    url = "/login"
    target = request.url.path
    if request.url.query:
        target += f"?{request.url.query}"
    if request.method == "GET" and target != "/":
        url += "?" + urlencode({"next": target})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(404)
def not_found(request: Request, exc: Exception) -> Response:
    """Страница 404. Для чужой машины — та же, что для несуществующего адреса."""
    if is_htmx(request):
        return Response(status_code=404)
    return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)


@app.exception_handler(403)
def forbidden(request: Request, exc: Exception) -> Response:
    """Страница 403: неверный CSRF-токен, ничего не изменилось."""
    if is_htmx(request):
        # Страницу 403 HTMX не вставит: перезагружаем текущую, она получит новый токен
        return Response(status_code=403, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "forbidden.html", {}, status_code=403)
