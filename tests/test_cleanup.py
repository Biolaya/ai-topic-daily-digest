from datetime import datetime, timedelta, timezone

from sqlmodel import select

from src.cleanup import prune_old_data
from src.database import get_session, init_db
from src.models import NewsItem, Run, SentNews


NOW = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)


def _seed(session):
    """造新旧各一份 Run / NewsItem,外加一条 SentNews。返回各自的关键标识。"""
    old_time = NOW - timedelta(days=40)
    recent_time = NOW - timedelta(days=5)

    old_run = Run(run_type="send", started_at=old_time, html_snapshot="<h1>old</h1>")
    recent_run = Run(run_type="send", started_at=recent_time, html_snapshot="<h1>recent</h1>")
    session.add(old_run)
    session.add(recent_run)

    session.add(NewsItem(url="https://example.com/old", title="old", created_at=old_time))
    session.add(NewsItem(url="https://example.com/recent", title="recent", created_at=recent_time))

    session.add(SentNews(url="https://example.com/sent", title="sent", sent_at=old_time))
    session.commit()
    session.refresh(old_run)
    session.refresh(recent_run)
    return old_run.id, recent_run.id


def test_prune_clears_old_snapshot_and_keeps_run_row(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        old_id, recent_id = _seed(session)

        stats = prune_old_data(session, retention_days=30, now=NOW)

        assert stats["runs_cleared"] == 1
        # 两条 run 行都还在(只清字段,不删行)
        old_run = session.get(Run, old_id)
        recent_run = session.get(Run, recent_id)
        assert old_run is not None and old_run.html_snapshot == ""
        assert recent_run is not None and recent_run.html_snapshot == "<h1>recent</h1>"


def test_prune_deletes_old_news_items_only(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        _seed(session)

        stats = prune_old_data(session, retention_days=30, now=NOW)

        assert stats["news_deleted"] == 1
        remaining = {item.url for item in session.exec(select(NewsItem)).all()}
        assert remaining == {"https://example.com/recent"}


def test_prune_never_touches_sent_news(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        _seed(session)

        prune_old_data(session, retention_days=30, now=NOW)

        sent = session.exec(select(SentNews)).all()
        assert [row.url for row in sent] == ["https://example.com/sent"]


def test_prune_disabled_when_retention_zero(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        old_id, _ = _seed(session)

        stats = prune_old_data(session, retention_days=0, now=NOW)

        assert stats == {"runs_cleared": 0, "news_deleted": 0}
        # 什么都没动
        assert session.get(Run, old_id).html_snapshot == "<h1>old</h1>"
        assert len(session.exec(select(NewsItem)).all()) == 2
