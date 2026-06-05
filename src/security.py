from __future__ import annotations

import hmac

from itsdangerous import BadSignature, URLSafeSerializer


COOKIE_NAME = "afd_session"


def verify_admin(username: str, password: str, settings) -> bool:
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password,
        settings.admin_password,
    )


def make_session_token(settings) -> str:
    serializer = URLSafeSerializer(settings.session_secret, salt="ai-football-digest")
    return serializer.dumps({"user": settings.admin_username})


def read_session_token(token: str, settings) -> dict | None:
    serializer = URLSafeSerializer(settings.session_secret, salt="ai-football-digest")
    try:
        data = serializer.loads(token)
    except BadSignature:
        return None
    if data.get("user") != settings.admin_username:
        return None
    return data
