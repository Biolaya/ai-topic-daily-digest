from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlmodel import select

from src.database import get_session
from src.models import TavilyKeyStatus, utc_now


INVALID_STATUS_CODES = {401, 403}
RATE_LIMIT_STATUS_CODES = {429}


class TavilyKeyManager:
    def __init__(self, api_keys: list[str] | tuple[str, ...], db_path=None):
        self.api_keys = dedupe_keys(api_keys)
        self.db_path = db_path

    @classmethod
    def from_settings(cls, settings) -> "TavilyKeyManager":
        keys = list(getattr(settings, "tavily_api_keys", ()) or [])
        if not keys and getattr(settings, "tavily_api_key", ""):
            keys = [settings.tavily_api_key]
        return cls(keys, getattr(settings, "db_path", None))

    def has_keys(self) -> bool:
        return bool(self.api_keys)

    def search(self, **kwargs):
        try:
            from tavily import TavilyClient
        except ImportError as exc:  # pragma: no cover - covered by dependency install
            raise RuntimeError("未安装 tavily-python，请先运行 pip install -r requirements.txt") from exc

        return self.search_with_failover(lambda key: TavilyClient(api_key=key), kwargs)

    def search_with_failover(self, client_factory: Callable[[str], object], search_kwargs: dict, logger: logging.Logger | None = None):
        logger = logger or logging.getLogger(__name__)
        if not self.api_keys:
            raise ValueError("TAVILY_API_KEYS 或 TAVILY_API_KEY 为空，无法搜索新闻")

        keys = self.available_keys()
        if not keys:
            raise RuntimeError("所有 Tavily API Key 当前都不可用")

        failures: list[str] = []
        for key in keys:
            fingerprint = key_fingerprint(key)
            try:
                self.mark_used(key)
                client = client_factory(key)
                response = client.search(**search_kwargs)
                self.record_success(key)
                return response
            except TypeError:
                raise
            except Exception as exc:
                status = self.record_failure(key, exc)
                failures.append(f"{fingerprint}:{status}")
                logger.warning(
                    "Tavily key 搜索失败，fingerprint=%s，status=%s，error=%s",
                    fingerprint,
                    status,
                    exc.__class__.__name__,
                )
                continue

        raise RuntimeError(f"所有 Tavily API Key 均失败：{', '.join(failures)}")

    def available_keys(self) -> list[str]:
        if not self.db_path:
            return list(self.api_keys)

        now = utc_now()
        available = []
        with get_session(self.db_path) as session:
            for key in self.api_keys:
                row = self._get_or_create(session, key)
                if row.status == "invalid":
                    continue
                if row.disabled_until and _as_aware(row.disabled_until) > now:
                    continue
                available.append(key)
            session.commit()
        return available

    def mark_used(self, key: str) -> None:
        if not self.db_path:
            return
        with get_session(self.db_path) as session:
            row = self._get_or_create(session, key)
            row.last_used_at = utc_now()
            row.updated_at = utc_now()
            session.add(row)
            session.commit()

    def record_success(self, key: str) -> None:
        if not self.db_path:
            return
        with get_session(self.db_path) as session:
            row = self._get_or_create(session, key)
            row.status = "active"
            row.failure_count = 0
            row.last_error = ""
            row.disabled_until = None
            row.last_used_at = utc_now()
            row.updated_at = utc_now()
            session.add(row)
            session.commit()

    def record_failure(self, key: str, exc: Exception) -> str:
        status, disabled_until = classify_error(exc)
        if not self.db_path:
            return status

        with get_session(self.db_path) as session:
            row = self._get_or_create(session, key)
            row.status = status
            row.failure_count += 1
            row.last_error = sanitize_error(exc, self.api_keys)
            row.disabled_until = disabled_until
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
        return status

    def _get_or_create(self, session, key: str) -> TavilyKeyStatus:
        fingerprint = key_fingerprint(key)
        row = session.exec(select(TavilyKeyStatus).where(TavilyKeyStatus.key_fingerprint == fingerprint)).first()
        if row:
            return row
        row = TavilyKeyStatus(key_fingerprint=fingerprint, status="active")
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def dedupe_keys(api_keys: list[str] | tuple[str, ...]) -> list[str]:
    seen = set()
    result = []
    for raw in api_keys:
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]


def classify_error(exc: Exception) -> tuple[str, datetime | None]:
    status_code = get_status_code(exc)
    if status_code in INVALID_STATUS_CODES:
        return "invalid", None
    if status_code in RATE_LIMIT_STATUS_CODES:
        return "rate_limited", utc_now() + timedelta(hours=1)
    if status_code and status_code >= 500:
        return "transient_error", None
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "transient_error", None

    message = str(exc).lower()
    if re.search(r"\b(401|403)\b", message):
        return "invalid", None
    if re.search(r"\b429\b|rate limit|too many requests", message):
        return "rate_limited", utc_now() + timedelta(hours=1)
    if re.search(r"\b5\d\d\b|timeout|connection", message):
        return "transient_error", None
    return "failed", None


def get_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def sanitize_error(exc: Exception, secrets: list[str] | tuple[str, ...]) -> str:
    message = str(exc) or exc.__class__.__name__
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
