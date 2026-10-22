"""Запуск веб-приложения «Помощник автовладельца»."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Помощник автовладельца")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Главная страница."""
    return templates.TemplateResponse(request, "index.html")
