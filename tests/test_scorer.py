from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.scorer import calculate_score, calculate_score_breakdown, get_freshness_score
from src.runner import score_and_store_items
from src.database import get_session, init_db
from src.models import Topic


def test_freshness_score_uses_expected_buckets():
    now = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)

    assert get_freshness_score((now - timedelta(hours=5)).isoformat(), now) == 100
    assert get_freshness_score((now - timedelta(hours=10)).isoformat(), now) == 85
    assert get_freshness_score((now - timedelta(hours=20)).isoformat(), now) == 70
    assert get_freshness_score((now - timedelta(hours=36)).isoformat(), now) == 40
    assert get_freshness_score((now - timedelta(hours=60)).isoformat(), now) == 10
    assert get_freshness_score("", now) == 50


def test_calculate_score_filters_disabled_source():
    topic = SimpleNamespace(id=1, min_score=40)
    news_item = {
        "title": "OpenAI releases a model",
        "content": "OpenAI model update",
        "url": "https://blocked.example/news",
        "domain": "blocked.example",
        "published_at": "2026-05-23T08:00:00+00:00",
    }
    sources = [{"topic_id": 1, "domain": "blocked.example", "base_score": 90, "enabled": False}]

    assert calculate_score(news_item, topic, [], sources) is None


def test_calculate_score_uses_source_freshness_keywords_and_preference():
    now = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
    topic = SimpleNamespace(id=1, min_score=40)
    news_item = {
        "title": "OpenAI releases a model",
        "content": "OpenAI model update",
        "url": "https://example.com/news",
        "domain": "example.com",
        "published_at": now.isoformat(),
    }
    keywords = [
        {"topic_id": 1, "word": "OpenAI", "weight": 9, "enabled": True},
        {"topic_id": 1, "word": "model", "weight": 3, "enabled": True},
    ]
    sources = [{"topic_id": 1, "domain": "example.com", "base_score": 80, "enabled": True}]

    score = calculate_score(news_item, topic, keywords, sources, now)

    assert score == 83.0


def test_keywords_only_affect_their_own_topic():
    now = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
    topic = SimpleNamespace(id=1, min_score=40)
    news_item = {
        "title": "Minecraft update adds new mobs",
        "content": "Minecraft gameplay news",
        "url": "https://example.com/minecraft",
        "domain": "example.com",
        "published_at": now.isoformat(),
    }
    keywords = [
        {"topic_id": 2, "word": "Minecraft", "weight": 10, "enabled": True},
    ]
    sources = [{"topic_id": None, "domain": "example.com", "base_score": 80, "enabled": True}]

    breakdown = calculate_score_breakdown(news_item, topic, keywords, sources, now)

    assert breakdown is not None
    assert breakdown.keyword_score == 0
    assert breakdown.preference_score == 0


def test_topic_source_score_overrides_global_source_score():
    now = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
    topic = SimpleNamespace(id=1, min_score=40)
    news_item = {
        "title": "OpenAI releases a model",
        "content": "OpenAI model update",
        "url": "https://example.com/news",
        "domain": "example.com",
        "published_at": now.isoformat(),
    }
    sources = [
        {"topic_id": None, "domain": "example.com", "base_score": 40, "enabled": True},
        {"topic_id": 1, "domain": "example.com", "base_score": 90, "enabled": True},
    ]

    breakdown = calculate_score_breakdown(news_item, topic, [], sources, now)

    assert breakdown is not None
    assert breakdown.source_score == 90


def test_global_source_score_and_unknown_source_default():
    now = datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)
    topic = SimpleNamespace(id=1, min_score=40)
    news_item = {
        "title": "General report",
        "content": "general content",
        "url": "https://example.com/news",
        "domain": "example.com",
        "published_at": now.isoformat(),
    }

    global_breakdown = calculate_score_breakdown(
        news_item,
        topic,
        [],
        [{"topic_id": None, "domain": "example.com", "base_score": 75, "enabled": True}],
        now,
    )
    unknown_breakdown = calculate_score_breakdown(
        {**news_item, "url": "https://unknown.example/news", "domain": "unknown.example"},
        topic,
        [],
        [],
        now,
    )

    assert global_breakdown is not None
    assert global_breakdown.source_score == 75
    assert unknown_breakdown is not None
    assert unknown_breakdown.source_score == 45


def test_runner_filters_items_below_topic_min_score(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        topic = Topic(name="Strict", slug="strict", min_score=90)
        session.add(topic)
        session.commit()
        session.refresh(topic)
        items = [
            {
                "topic_id": topic.id,
                "title": "Low score item",
                "content": "no keyword",
                "url": "https://example.com/low",
                "domain": "example.com",
                "published_at": "2026-05-23T08:00:00+00:00",
            }
        ]

        scored = score_and_store_items(session, items, [topic], {topic.id: []}, [])

        assert scored == []
