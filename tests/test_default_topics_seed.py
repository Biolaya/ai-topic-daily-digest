import sqlite3

from sqlmodel import select

from src.database import get_session, init_db
from src.models import Keyword, QueryTemplate, Topic


def test_init_db_seeds_default_ai_and_football_topics(tmp_path):
    db_path = tmp_path / "app.sqlite3"

    init_db(db_path)

    with get_session(db_path) as session:
        topics = session.exec(select(Topic).order_by(Topic.slug)).all()
        slugs = {topic.slug for topic in topics}

        assert {"ai", "football"}.issubset(slugs)
        assert session.exec(select(Keyword).join(Topic, Keyword.topic_id == Topic.id).where(Topic.slug == "ai")).all()
        assert session.exec(select(Keyword).join(Topic, Keyword.topic_id == Topic.id).where(Topic.slug == "football")).all()
        assert session.exec(select(QueryTemplate).join(Topic, QueryTemplate.topic_id == Topic.id).where(Topic.slug == "ai")).all()
        assert session.exec(select(QueryTemplate).join(Topic, QueryTemplate.topic_id == Topic.id).where(Topic.slug == "football")).all()


def test_init_db_is_idempotent_for_default_topics_keywords_and_queries(tmp_path):
    db_path = tmp_path / "app.sqlite3"

    init_db(db_path)
    init_db(db_path)

    with get_session(db_path) as session:
        assert len(session.exec(select(Topic).where(Topic.slug == "ai")).all()) == 1
        assert len(session.exec(select(Topic).where(Topic.slug == "football")).all()) == 1
        ai = session.exec(select(Topic).where(Topic.slug == "ai")).one()
        assert len(session.exec(select(Keyword).where(Keyword.topic_id == ai.id, Keyword.word == "OpenAI")).all()) == 1
        assert len(
            session.exec(
                select(QueryTemplate).where(
                    QueryTemplate.topic_id == ai.id,
                    QueryTemplate.query_text == "today AI news OpenAI Anthropic Google DeepMind NVIDIA artificial intelligence",
                )
            ).all()
        ) == 1


def test_init_db_migrates_legacy_category_tables_without_index_conflicts(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    seed_legacy_category_schema(db_path)

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "keywords_legacy_category" in tables
        assert "sources_legacy_category" in tables
        assert "news_items_legacy_category" in tables
        assert "sent_news_legacy_category" in tables

        keyword_index_table = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='ix_keywords_word'"
        ).fetchone()
        source_index_table = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name='ix_sources_domain'"
        ).fetchone()

        assert keyword_index_table == ("keywords",)
        assert source_index_table == ("sources",)


def test_init_db_migrates_legacy_runs_fixed_counts(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                run_type VARCHAR NOT NULL,
                started_at DATETIME NOT NULL,
                finished_at DATETIME,
                success BOOLEAN NOT NULL,
                ai_count INTEGER NOT NULL,
                football_count INTEGER NOT NULL,
                email_subject VARCHAR NOT NULL,
                error_message VARCHAR NOT NULL,
                html_snapshot VARCHAR NOT NULL
            );
            CREATE INDEX ix_runs_run_type ON runs (run_type);
            INSERT INTO runs
            (id, run_type, started_at, finished_at, success, ai_count, football_count, email_subject, error_message, html_snapshot)
            VALUES (1, 'dry-run', '2026-05-23T00:00:00+00:00', '2026-05-23T00:01:00+00:00', 1, 2, 3, 'subject', '', '<html></html>');
            """
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        assert "topic_counts" in columns
        assert "ai_count" not in columns
        assert "football_count" not in columns
        row = conn.execute("SELECT topic_counts FROM runs WHERE id=1").fetchone()
        assert "AI" in row[0]
        assert "Football" in row[0]


def seed_legacy_category_schema(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE keywords (
                id INTEGER PRIMARY KEY,
                word VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                language VARCHAR NOT NULL,
                weight INTEGER NOT NULL,
                enabled BOOLEAN NOT NULL,
                note VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE INDEX ix_keywords_word ON keywords (word);
            CREATE INDEX ix_keywords_category ON keywords (category);

            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                domain VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                base_score INTEGER NOT NULL,
                enabled BOOLEAN NOT NULL,
                note VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE UNIQUE INDEX ix_sources_domain ON sources (domain);
            CREATE INDEX ix_sources_category ON sources (category);

            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY,
                url VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                domain VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                published_at VARCHAR NOT NULL,
                raw_summary VARCHAR NOT NULL,
                final_summary VARCHAR NOT NULL,
                score FLOAT NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE INDEX ix_news_items_url ON news_items (url);
            CREATE INDEX ix_news_items_domain ON news_items (domain);
            CREATE INDEX ix_news_items_category ON news_items (category);

            CREATE TABLE sent_news (
                id INTEGER PRIMARY KEY,
                url VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                sent_at DATETIME NOT NULL
            );
            CREATE UNIQUE INDEX ix_sent_news_url ON sent_news (url);
            """
        )
