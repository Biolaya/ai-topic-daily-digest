from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlsplit


DEFAULT_SOURCE_SCORE = 45


@dataclass(frozen=True)
class ScoreBreakdown:
    source_score: float
    freshness_score: float
    keyword_score: float
    duplicate_score: float
    preference_score: float
    final_score: float


def calculate_score(news_item: dict, topic, keywords: Iterable, sources: Iterable, now: datetime | None = None) -> float | None:
    breakdown = calculate_score_breakdown(news_item, topic, keywords, sources, now)
    return None if breakdown is None else breakdown.final_score


def calculate_score_breakdown(
    news_item: dict,
    topic,
    keywords: Iterable,
    sources: Iterable,
    now: datetime | None = None,
) -> ScoreBreakdown | None:
    source_score = get_source_score(news_item, topic, sources)
    if source_score is None:
        return None

    freshness_score = get_freshness_score(news_item.get("published_at"), now)
    keyword_score = get_keyword_score(news_item, topic, keywords)
    duplicate_score = get_duplicate_score(news_item)
    preference_score = get_preference_score(news_item, topic, keywords)

    final_score = (
        source_score * 0.35
        + freshness_score * 0.25
        + keyword_score * 0.25
        + duplicate_score * 0.10
        + preference_score * 0.05
    )
    return ScoreBreakdown(
        source_score=source_score,
        freshness_score=freshness_score,
        keyword_score=keyword_score,
        duplicate_score=duplicate_score,
        preference_score=preference_score,
        final_score=round(final_score, 2),
    )


def get_source_score(news_item: dict, topic, sources: Iterable) -> float | None:
    domain = normalize_domain(news_item.get("domain") or news_item.get("url") or news_item.get("source", ""))
    topic_id = _get(topic, "id")
    topic_match = None
    global_match = None

    for source in sources:
        source_domain = normalize_domain(_get(source, "domain", ""))
        if not source_domain:
            continue
        if domain != source_domain and not domain.endswith("." + source_domain):
            continue
        if _get(source, "topic_id") == topic_id:
            topic_match = source
            break
        if _get(source, "topic_id") is None:
            global_match = source

    matched = topic_match or global_match
    if matched is None:
        return DEFAULT_SOURCE_SCORE
    if not bool(_get(matched, "enabled", True)):
        return None
    return float(_get(matched, "base_score", DEFAULT_SOURCE_SCORE))


def get_freshness_score(published_at, now: datetime | None = None) -> float:
    if not published_at:
        return 50

    published = parse_datetime(str(published_at))
    if published is None:
        return 50

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    hours = max((now - published).total_seconds() / 3600, 0)
    if hours <= 6:
        return 100
    if hours <= 12:
        return 85
    if hours <= 24:
        return 70
    if hours <= 48:
        return 40
    return 10


def get_keyword_score(news_item: dict, topic, keywords: Iterable) -> float:
    total_weight = sum(_get(keyword, "weight", 0) for keyword in _matched_keywords(news_item, topic, keywords))
    return float(min(max(total_weight * 10, 0), 100))


def get_duplicate_score(news_item: dict) -> float:
    return 0


def get_preference_score(news_item: dict, topic, keywords: Iterable) -> float:
    matched_weight = sum(
        _get(keyword, "weight", 0)
        for keyword in _matched_keywords(news_item, topic, keywords)
        if _get(keyword, "weight", 0) >= 9
    )
    return float(min(matched_weight * 15, 100))


def _matched_keywords(news_item: dict, topic, keywords: Iterable) -> list:
    text = " ".join(
        str(news_item.get(key, ""))
        for key in ("title", "content", "raw_summary", "summary", "url")
    ).lower()
    topic_id = _get(topic, "id")

    matched = []
    for keyword in keywords:
        if not bool(_get(keyword, "enabled", True)):
            continue
        if _get(keyword, "topic_id") != topic_id:
            continue
        word = str(_get(keyword, "word", "")).strip().lower()
        if not word:
            continue
        match_type = str(_get(keyword, "match_type", "contains"))
        if match_type == "exact":
            found = re.search(rf"\b{re.escape(word)}\b", text) is not None
        else:
            found = word in text
        if found:
            matched.append(keyword)
    return matched


def parse_datetime(value: str) -> datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        pass

    match = re.search(r"\d{4}-\d{2}-\d{2}", cleaned)
    if match:
        try:
            return datetime.fromisoformat(match.group(0)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def normalize_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlsplit(raw).netloc
    raw = raw.split("/")[0]
    raw = raw.split(":")[0]
    return raw.removeprefix("www.")


def _get(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
