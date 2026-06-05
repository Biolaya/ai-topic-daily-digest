import pytest

from src.database import get_session, init_db
from src.models import Keyword, QueryTemplate, Source, Topic
from src.topic_generator import save_topic_suggestion, validate_topic_suggestion
from sqlmodel import select


def test_validate_topic_suggestion_normalizes_domain_and_clamps_scores():
    suggestion = validate_topic_suggestion(
        {
            "topic": {
                "name": "Minecraft Mods",
                "slug": "Minecraft Mods!",
                "language": "mixed",
                "daily_limit": 50,
                "min_score": -1,
                "priority": 200,
                "summary_style": "",
            },
            "keywords": [
                {"word": "Fabric", "language": "mixed", "weight": 99, "match_type": "contains"},
                {"word": "Fabric", "language": "mixed", "weight": 1},
            ],
            "query_templates": [
                {"query_text": "Minecraft Fabric news", "language": "en", "priority": 999},
                {"query_text": ""},
            ],
            "sources": [
                {"domain": "https://www.example.com/path?q=1", "name": "Example", "base_score": 999},
                {"domain": "example.com", "name": "Duplicate", "base_score": 1},
            ],
        }
    )

    assert suggestion["topic"]["slug"] == "minecraft-mods"
    assert suggestion["topic"]["daily_limit"] == 20
    assert suggestion["topic"]["min_score"] == 0
    assert suggestion["topic"]["priority"] == 100
    assert suggestion["keywords"][0]["weight"] == 10
    assert len(suggestion["keywords"]) == 1
    assert suggestion["query_templates"][0]["priority"] == 100
    assert suggestion["sources"][0]["domain"] == "example.com"
    assert suggestion["sources"][0]["base_score"] == 100


def test_validate_topic_suggestion_rejects_missing_topic_name():
    with pytest.raises(ValueError, match="topic.name"):
        validate_topic_suggestion({"topic": {"slug": "x"}})


def test_save_topic_suggestion_requires_confirmation_for_existing_slug(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    suggestion = {
        "topic": {
            "name": "AI",
            "slug": "ai",
            "description": "",
            "language": "mixed",
            "daily_limit": 8,
            "min_score": 45,
            "priority": 5,
            "summary_style": "concise",
        },
        "keywords": [{"word": "Agents", "language": "en", "weight": 8, "match_type": "contains", "note": ""}],
        "query_templates": [{"query_text": "AI agents news", "language": "en", "priority": 8}],
        "sources": [{"domain": "https://www.example.com/path", "name": "Example", "base_score": 80, "note": ""}],
    }

    with get_session(db_path) as session:
        with pytest.raises(ValueError, match="slug 已存在"):
            save_topic_suggestion(session, suggestion, merge_existing=False)

        topic = save_topic_suggestion(session, suggestion, merge_existing=True)

        assert topic.slug == "ai"
        assert session.exec(select(Keyword).where(Keyword.topic_id == topic.id, Keyword.word == "Agents")).one()
        assert session.exec(select(QueryTemplate).where(QueryTemplate.topic_id == topic.id, QueryTemplate.query_text == "AI agents news")).one()
        assert session.exec(select(Source).where(Source.topic_id == topic.id, Source.domain == "example.com")).one()
