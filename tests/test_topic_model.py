from sqlmodel import select

from src.database import get_session, init_db
from src.models import Keyword, QueryTemplate, Source, Topic
from src.runner import get_enabled_topics


def test_custom_topic_can_be_created_with_related_config(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        topic = Topic(name="Minecraft", slug="minecraft", daily_limit=5, min_score=45, priority=7)
        session.add(topic)
        session.commit()
        session.refresh(topic)

        session.add(Keyword(topic_id=topic.id, word="Minecraft", language="mixed", weight=10))
        session.add(QueryTemplate(topic_id=topic.id, query_text="today Minecraft news", language="en", priority=10))
        session.add(Source(topic_id=topic.id, domain="minecraft.net", name="Minecraft", base_score=95))
        session.commit()

        enabled_topics = get_enabled_topics(session)
        slugs = [item.slug for item in enabled_topics]

        assert "minecraft" in slugs
        assert session.exec(select(Keyword).where(Keyword.topic_id == topic.id)).one().word == "Minecraft"
        assert session.exec(select(QueryTemplate).where(QueryTemplate.topic_id == topic.id)).one().query_text == "today Minecraft news"
        assert session.exec(select(Source).where(Source.topic_id == topic.id)).one().base_score == 95


def test_topic_model_defaults_are_generic():
    topic = Topic(name="Gaming", slug="gaming")

    assert topic.language == "mixed"
    assert topic.daily_limit == 8
    assert topic.min_score == 40
    assert topic.priority == 5
    assert topic.summary_style == "concise"
    assert topic.enabled is True


def test_disabled_topic_is_not_returned_for_digest_runs(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        topic = Topic(name="Anime", slug="anime", enabled=False)
        session.add(topic)
        session.commit()

        slugs = [item.slug for item in get_enabled_topics(session)]

        assert "anime" not in slugs
