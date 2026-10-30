import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.core.config import settings
from app.core.security import auth_gate
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services import events, recognition, workers
    events.set_main_loop(asyncio.get_running_loop())
    init_db()
    recognition.load_known()
    workers.resume_all()
    yield
    workers.stop_all()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

from app.routes import api, auth, pages  # noqa: E402
for r in (pages.router, api.router, auth.router):
    app.include_router(r)

app.middleware("http")(auth_gate)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=86400)
