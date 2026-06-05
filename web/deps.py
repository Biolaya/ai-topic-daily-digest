from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from src.config import get_settings
from src.database import get_session
from src.security import COOKIE_NAME, read_session_token


def db_session():
    settings = get_settings()
    with get_session(settings.db_path) as session:
        yield session


def require_admin(request: Request):
    settings = get_settings()
    token = request.cookies.get(COOKIE_NAME, "")
    if read_session_token(token, settings):
        return True
    raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


SessionDep = Depends(db_session)
AdminDep = Depends(require_admin)
