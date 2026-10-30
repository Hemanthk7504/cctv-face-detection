from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.core.config import APP_DIR

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def render(req: Request, name: str, **ctx):
    return templates.TemplateResponse(req, name, ctx)
