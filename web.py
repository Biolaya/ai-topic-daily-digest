from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import get_settings
from src.database import init_db


__path__ = [str(Path(__file__).resolve().parent / "web")]

from web.routes import router  # noqa: E402


settings = get_settings()
init_db(settings.db_path)

app = FastAPI(title="通用主题日报管理后台")
app.mount("/static", StaticFiles(directory=settings.root_dir / "static"), name="static")
app.include_router(router)
