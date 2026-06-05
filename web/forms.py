from __future__ import annotations


def checkbox(value: str | None) -> bool:
    return value in {"on", "true", "1", "yes"}


def clamp_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(number, maximum))


def clean_text(value: str | None) -> str:
    return (value or "").strip()
