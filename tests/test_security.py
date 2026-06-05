from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import web.deps as deps
from web import app
from src.config import get_settings
from src.security import COOKIE_NAME, make_session_token, verify_admin


def make_request(cookie_value: str = "") -> Request:
    headers = []
    if cookie_value:
        headers.append((b"cookie", f"{COOKIE_NAME}={cookie_value}".encode()))
    return Request({"type": "http", "headers": headers})


def test_unauthenticated_admin_route_redirects_to_login():
    with pytest.raises(HTTPException) as exc:
        deps.require_admin(make_request())

    assert exc.value.status_code == 303
    assert exc.value.headers["Location"] == "/login"


def test_signed_cookie_allows_admin(monkeypatch):
    fake_settings = SimpleNamespace(
        admin_username="admin",
        admin_password="strong-password",
        session_secret="test-secret",
    )
    token = make_session_token(fake_settings)
    monkeypatch.setattr(deps, "get_settings", lambda: fake_settings)

    assert deps.require_admin(make_request(token)) is True
    assert verify_admin("admin", "strong-password", fake_settings) is True
    assert verify_admin("admin", "wrong", fake_settings) is False


def test_templates_do_not_contain_real_secret_values():
    settings = get_settings()
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in settings.root_dir.joinpath("templates").glob("*.html")
    )

    for secret in (
        settings.smtp_pass,
        settings.openai_api_key,
        settings.tavily_api_key,
        settings.admin_password,
    ):
        if secret:
            assert secret not in template_text


def test_new_admin_pages_require_login():
    for path in ("/recipients", "/topics/generate"):
        route = next(route for route in app.routes if getattr(route, "path", "") == path)
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert deps.require_admin in dependency_calls
