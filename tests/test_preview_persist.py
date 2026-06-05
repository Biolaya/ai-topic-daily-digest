"""验证 #4:实时预览(persist_items=False)零 DB 写入,默认(persist_items=True)照常落库。"""
from dataclasses import replace
from types import SimpleNamespace

from sqlmodel import func, select

from src.config import get_settings
from src.database import get_session, init_db
from src.models import NewsItem, Run, Topic
import src.runner as runner
from src.runner import run_digest, score_and_store_items


def _fake_settings(db_path):
    return replace(get_settings(), db_path=db_path)


def _patch_pipeline(monkeypatch, enabled_topic):
    """把 run_digest 依赖的外部调用替换为可控桩,隔离出 persist 行为。"""
    def fake_fetch_news(settings, topics, keywords_by_topic, templates_by_topic, logger):
        return [
            {
                "topic_id": enabled_topic.id,
                "title": "桩新闻",
                "content": "正文",
                "url": "https://example.com/stub",
                "domain": "example.com",
                "source": "Example",
                "published_at": "2026-06-05T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr(runner, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(runner, "calculate_score", lambda *a, **k: 100.0)
    monkeypatch.setattr(runner, "summarize_digest", lambda *a, **k: {"sections": []})
    monkeypatch.setattr(runner, "render_email", lambda digest, subject: "<h1>stub</h1>")


def _first_enabled_topic(session):
    return session.exec(select(Topic).where(Topic.enabled == True)).first()  # noqa: E712


def _counts(session):
    news = session.exec(select(func.count(NewsItem.id))).one()
    runs = session.exec(select(func.count(Run.id))).one()
    return news, runs


def test_live_preview_writes_nothing(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    settings = _fake_settings(db_path)

    with get_session(db_path) as session:
        topic = _first_enabled_topic(session)
        _patch_pipeline(monkeypatch, topic)
        news_before, runs_before = _counts(session)

        result = run_digest(settings, session, run_type="dry-run", send=False, persist_items=False)

        news_after, runs_after = _counts(session)
        assert news_after == news_before, "实时预览不应写入 news_items"
        assert runs_after == runs_before, "实时预览不应新建 Run 行"
        assert result["html"] == "<h1>stub</h1>"
        # 返回的 run 是游离对象,未持久化(无 id)
        assert result["run"].id is None


def test_default_persist_still_writes(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    settings = _fake_settings(db_path)

    with get_session(db_path) as session:
        topic = _first_enabled_topic(session)
        _patch_pipeline(monkeypatch, topic)
        news_before, runs_before = _counts(session)

        run_digest(settings, session, run_type="dry-run", send=False)  # persist_items 默认 True

        news_after, runs_after = _counts(session)
        assert news_after == news_before + 1, "默认应写入 1 条 news_items"
        assert runs_after == runs_before + 1, "默认应新建 1 条 Run 行"


def test_score_and_store_persist_false_returns_scores_without_writing(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        topic = _first_enabled_topic(session)
        items = [
            {
                "topic_id": topic.id,
                "title": "可打分但不落库",
                "content": "openai 模型 " + topic.name,
                "url": "https://example.com/nopersist",
                "domain": "example.com",
                "published_at": "2026-06-05T00:00:00+00:00",
            }
        ]
        before = session.exec(select(func.count(NewsItem.id))).one()
        scored = score_and_store_items(session, items, [topic], {topic.id: []}, [], persist=False)
        after = session.exec(select(func.count(NewsItem.id))).one()

        assert after == before, "persist=False 不应写入 news_items"
        # 不管打分高低,函数都不应落库;若通过了 min_score 则返回带 score 的条目
        for row in scored:
            assert "score" in row
