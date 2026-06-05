from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlmodel import Session, SQLModel, create_engine, select

from src.config import get_settings
from src.dedupe import normalize_url
from src.models import Keyword, QueryTemplate, SentNews, SettingRecord, Source, Topic, utc_now


DEFAULT_SETTINGS = {
    "mail_to": "",
    "send_time": "06:00",
    "summary_style": "清晰、具体、简洁",
    "auto_send_enabled": "true",
    "retention_days": "30",
}

DEFAULT_TOPICS = [
    {
        "name": "AI",
        "slug": "ai",
        "description": "AI、模型、芯片、产品和监管新闻",
        "language": "mixed",
        "daily_limit": 8,
        "min_score": 40,
        "priority": 10,
        "summary_style": "concrete",
    },
    {
        "name": "Football",
        "slug": "football",
        "description": "足球比赛、转会、球队和教练新闻",
        "language": "mixed",
        "daily_limit": 8,
        "min_score": 40,
        "priority": 9,
        "summary_style": "concrete",
    },
]

DEFAULT_KEYWORDS = [
    ("ai", "OpenAI", "en", 9, "OpenAI 相关新闻"),
    ("ai", "DeepSeek", "mixed", 9, "DeepSeek 相关新闻"),
    ("ai", "Anthropic", "en", 8, "Anthropic 相关新闻"),
    ("ai", "Google DeepMind", "en", 8, "Google DeepMind 相关新闻"),
    ("ai", "NVIDIA", "en", 8, "AI 芯片和算力"),
    ("ai", "人工智能", "zh", 7, "AI 综合新闻"),
    ("ai", "大模型", "zh", 9, "中文大模型新闻"),
    ("football", "Manchester United", "en", 8, "曼联"),
    ("football", "Premier League", "en", 8, "英超"),
    ("football", "Champions League", "en", 8, "欧冠"),
    ("football", "英超", "zh", 8, "英超"),
    ("football", "欧冠", "zh", 8, "欧冠"),
    ("football", "曼联", "zh", 8, "曼联"),
    ("football", "中超", "zh", 6, "中超"),
    ("football", "转会", "zh", 7, "转会"),
]

DEFAULT_QUERY_TEMPLATES = [
    ("ai", "today AI news OpenAI Anthropic Google DeepMind NVIDIA artificial intelligence", "en", 10),
    ("ai", "今日 AI 新闻 大模型 OpenAI DeepSeek Anthropic 英伟达 人工智能", "zh", 10),
    ("football", "today football news Premier League Champions League transfer Manchester United", "en", 10),
    ("football", "今日 足球 新闻 英超 欧冠 中超 转会 曼联", "zh", 10),
]

DEFAULT_SOURCES = [
    (None, "reuters.com", "Reuters", 92, "国际通讯社"),
    (None, "apnews.com", "Associated Press", 90, "国际通讯社"),
    (None, "bbc.com", "BBC", 86, "国际新闻"),
    (None, "theguardian.com", "The Guardian", 78, "综合新闻"),
    ("ai", "openai.com", "OpenAI", 96, "官方来源"),
    ("ai", "anthropic.com", "Anthropic", 94, "官方来源"),
    ("ai", "deepmind.google", "Google DeepMind", 94, "官方来源"),
    ("ai", "nvidia.com", "NVIDIA", 88, "官方来源"),
    ("ai", "theverge.com", "The Verge", 78, "科技媒体"),
    ("ai", "techcrunch.com", "TechCrunch", 76, "科技媒体"),
    ("football", "skysports.com", "Sky Sports", 84, "足球媒体"),
    ("football", "espn.com", "ESPN", 82, "体育媒体"),
    ("football", "theathletic.com", "The Athletic", 86, "体育媒体"),
    ("football", "premierleague.com", "Premier League", 90, "官方来源"),
    ("football", "uefa.com", "UEFA", 90, "官方来源"),
]


def get_engine(db_path: Path | str | None = None):
    settings = get_settings()
    path = Path(db_path) if db_path else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def init_db(db_path: Path | str | None = None) -> None:
    settings = get_settings()
    path = Path(db_path) if db_path else settings.db_path
    engine = get_engine(path)

    drop_legacy_category_indexes(path)
    SQLModel.metadata.create_all(engine)
    migrate_legacy_schema(path)
    migrate_runs_schema(path)
    drop_legacy_category_indexes(path)
    SQLModel.metadata.create_all(engine)
    migrate_legacy_data(path)
    migrate_legacy_runs(path)

    with Session(engine) as session:
        seed_defaults(session, settings)
        session.commit()

    migrate_legacy_sent_news(path)


def get_session(db_path: Path | str | None = None) -> Session:
    return Session(get_engine(db_path))


def seed_defaults(session: Session, settings=None) -> None:
    settings = settings or get_settings()

    defaults = dict(DEFAULT_SETTINGS)
    if settings.mail_to:
        defaults["mail_to"] = settings.mail_to
    defaults["summary_style"] = settings.summary_style

    for key, value in defaults.items():
        if not session.exec(select(SettingRecord).where(SettingRecord.key == key)).first():
            session.add(SettingRecord(key=key, value=value))

    for topic_data in DEFAULT_TOPICS:
        if not get_topic_by_slug(session, topic_data["slug"]):
            session.add(Topic(**topic_data))
    session.commit()

    topic_map = get_topic_map(session)

    for slug, word, language, weight, note in DEFAULT_KEYWORDS:
        topic = topic_map.get(slug)
        if not topic:
            continue
        exists = session.exec(
            select(Keyword).where(Keyword.topic_id == topic.id, Keyword.word == word)
        ).first()
        if not exists:
            session.add(
                Keyword(
                    topic_id=topic.id,
                    word=word,
                    language=language,
                    weight=weight,
                    match_type="contains",
                    enabled=True,
                    note=note,
                )
            )

    for slug, query_text, language, priority in DEFAULT_QUERY_TEMPLATES:
        topic = topic_map.get(slug)
        if not topic:
            continue
        exists = session.exec(
            select(QueryTemplate).where(
                QueryTemplate.topic_id == topic.id,
                QueryTemplate.query_text == query_text,
            )
        ).first()
        if not exists:
            session.add(
                QueryTemplate(
                    topic_id=topic.id,
                    query_text=query_text,
                    language=language,
                    priority=priority,
                    enabled=True,
                )
            )

    for slug, domain, name, base_score, note in DEFAULT_SOURCES:
        topic_id = topic_map[slug].id if slug and slug in topic_map else None
        exists = session.exec(
            select(Source).where(Source.topic_id == topic_id, Source.domain == domain)
        ).first()
        if not exists:
            session.add(
                Source(
                    topic_id=topic_id,
                    domain=domain,
                    name=name,
                    base_score=base_score,
                    enabled=True,
                    note=note,
                )
            )


def get_topic_by_slug(session: Session, slug: str) -> Topic | None:
    return session.exec(select(Topic).where(Topic.slug == slug)).first()


def get_topic_map(session: Session) -> dict[str, Topic]:
    return {topic.slug: topic for topic in session.exec(select(Topic)).all()}


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.exec(select(SettingRecord).where(SettingRecord.key == key)).first()
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.exec(select(SettingRecord).where(SettingRecord.key == key)).first()
    if row:
        row.value = value
        row.updated_at = utc_now()
        session.add(row)
    else:
        session.add(SettingRecord(key=key, value=value))


def get_settings_map(session: Session) -> dict[str, str]:
    return {row.key: row.value for row in session.exec(select(SettingRecord)).all()}


def is_sent(session: Session, url: str) -> bool:
    normalized = normalize_url(url)
    if not normalized:
        return False
    return session.exec(select(SentNews).where(SentNews.url == normalized)).first() is not None


def mark_sent(session: Session, news_items: Iterable[dict]) -> None:
    for item in news_items:
        url = normalize_url(str(item.get("url", "")))
        title = str(item.get("title", "")).strip()
        if not url or not title or is_sent(session, url):
            continue
        session.add(
            SentNews(
                topic_id=item.get("topic_id"),
                url=url,
                title=title,
                source=str(item.get("source", "")).strip(),
                sent_at=utc_now(),
            )
        )


def migrate_legacy_schema(app_db_path: Path) -> None:
    with sqlite3.connect(app_db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_default_topics_sql(conn)
        for table in ("keywords", "sources", "news_items", "sent_news"):
            if should_rebuild_table(conn, table):
                legacy_name = f"{table}_legacy_category"
                if not table_exists(conn, legacy_name):
                    conn.execute(f"ALTER TABLE {table} RENAME TO {legacy_name}")
        drop_legacy_category_indexes_conn(conn)
        conn.commit()


def migrate_runs_schema(app_db_path: Path) -> None:
    with sqlite3.connect(app_db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "runs"):
            return
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "topic_counts" in columns and "ai_count" not in columns and "football_count" not in columns:
            return
        if not table_exists(conn, "runs_legacy_fixed_counts"):
            conn.execute("ALTER TABLE runs RENAME TO runs_legacy_fixed_counts")
            conn.commit()


def drop_legacy_category_indexes(app_db_path: Path) -> None:
    if not app_db_path.exists():
        return
    with sqlite3.connect(app_db_path) as conn:
        drop_legacy_category_indexes_conn(conn)
        conn.commit()


def drop_legacy_category_indexes_conn(conn: sqlite3.Connection) -> None:
    legacy_tables = {
        "keywords_legacy_category",
        "sources_legacy_category",
        "news_items_legacy_category",
        "sent_news_legacy_category",
        "runs_legacy_fixed_counts",
    }
    rows = conn.execute(
        """
        SELECT name, tbl_name
        FROM sqlite_master
        WHERE type='index' AND sql IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        name = row[0]
        table_name = row[1]
        if table_name in legacy_tables:
            conn.execute(f'DROP INDEX IF EXISTS "{name}"')


def migrate_legacy_data(app_db_path: Path) -> None:
    with sqlite3.connect(app_db_path) as conn:
        conn.row_factory = sqlite3.Row
        topic_ids = get_topic_ids_sql(conn)
        copy_legacy_keywords(conn, topic_ids)
        copy_legacy_sources(conn, topic_ids)
        copy_legacy_news_items(conn, topic_ids)
        copy_legacy_sent_news(conn, topic_ids)
        conn.commit()


def migrate_legacy_runs(app_db_path: Path) -> None:
    with sqlite3.connect(app_db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "runs_legacy_fixed_counts"):
            return
        if conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] > 0:
            return
        rows = conn.execute("SELECT * FROM runs_legacy_fixed_counts").fetchall()
        for row in rows:
            topic_counts = ""
            if "topic_counts" in row.keys() and row["topic_counts"]:
                topic_counts = row["topic_counts"]
            elif "ai_count" in row.keys() or "football_count" in row.keys():
                counts = {}
                if "ai_count" in row.keys() and row["ai_count"]:
                    counts["AI"] = row["ai_count"]
                if "football_count" in row.keys() and row["football_count"]:
                    counts["Football"] = row["football_count"]
                topic_counts = json.dumps(counts, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO runs
                (id, run_type, started_at, finished_at, success, topic_counts, email_subject, error_message, html_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["run_type"],
                    row["started_at"],
                    row["finished_at"] if "finished_at" in row.keys() else None,
                    row["success"] if "success" in row.keys() else 0,
                    topic_counts,
                    row["email_subject"] if "email_subject" in row.keys() else "",
                    row["error_message"] if "error_message" in row.keys() else "",
                    row["html_snapshot"] if "html_snapshot" in row.keys() else "",
                ),
            )
        conn.commit()


def ensure_default_topics_sql(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] > 0:
        return
    now = utc_now().isoformat()
    for topic in DEFAULT_TOPICS:
        conn.execute(
            """
            INSERT INTO topics
            (name, slug, description, language, daily_limit, min_score, priority, summary_style, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                topic["name"],
                topic["slug"],
                topic["description"],
                topic["language"],
                topic["daily_limit"],
                topic["min_score"],
                topic["priority"],
                topic["summary_style"],
                now,
                now,
            ),
        )


def should_rebuild_table(conn: sqlite3.Connection, table: str) -> bool:
    if not table_exists(conn, table):
        return False
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if table == "keywords":
        return "category" in columns or "topic_id" not in columns or "match_type" not in columns
    if table == "sources":
        return "category" in columns or "topic_id" not in columns
    if table in {"news_items", "sent_news"}:
        return "category" in columns or "topic_id" not in columns
    return False


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def get_topic_ids_sql(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["slug"]: row["id"]
        for row in conn.execute("SELECT id, slug FROM topics").fetchall()
    }


def copy_legacy_keywords(conn: sqlite3.Connection, topic_ids: dict[str, int]) -> None:
    if not table_exists(conn, "keywords_legacy_category"):
        return
    if conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0] > 0:
        return
    rows = conn.execute("SELECT * FROM keywords_legacy_category").fetchall()
    for row in rows:
        topic_id = topic_id_from_legacy(row, topic_ids)
        if not topic_id:
            continue
        conn.execute(
            """
            INSERT INTO keywords
            (topic_id, word, language, weight, match_type, enabled, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                row["word"],
                row["language"] if "language" in row.keys() else "mixed",
                row["weight"] if "weight" in row.keys() else 5,
                "contains",
                row["enabled"] if "enabled" in row.keys() else 1,
                row["note"] if "note" in row.keys() else "",
                row["created_at"] if "created_at" in row.keys() else utc_now().isoformat(),
                row["updated_at"] if "updated_at" in row.keys() else utc_now().isoformat(),
            ),
        )


def copy_legacy_sources(conn: sqlite3.Connection, topic_ids: dict[str, int]) -> None:
    if not table_exists(conn, "sources_legacy_category"):
        return
    if conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] > 0:
        return
    rows = conn.execute("SELECT * FROM sources_legacy_category").fetchall()
    for row in rows:
        category = str(row["category"] if "category" in row.keys() else "").strip().lower()
        topic_id = None if category == "general" else topic_id_from_legacy(row, topic_ids)
        conn.execute(
            """
            INSERT INTO sources
            (topic_id, domain, name, base_score, enabled, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                row["domain"],
                row["name"] if "name" in row.keys() else "",
                row["base_score"] if "base_score" in row.keys() else 45,
                row["enabled"] if "enabled" in row.keys() else 1,
                row["note"] if "note" in row.keys() else "",
                row["created_at"] if "created_at" in row.keys() else utc_now().isoformat(),
                row["updated_at"] if "updated_at" in row.keys() else utc_now().isoformat(),
            ),
        )


def copy_legacy_news_items(conn: sqlite3.Connection, topic_ids: dict[str, int]) -> None:
    if not table_exists(conn, "news_items_legacy_category"):
        return
    if conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] > 0:
        return
    rows = conn.execute("SELECT * FROM news_items_legacy_category").fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT INTO news_items
            (topic_id, url, title, source, domain, published_at, raw_summary, final_summary, score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id_from_legacy(row, topic_ids),
                row["url"],
                row["title"] if "title" in row.keys() else "",
                row["source"] if "source" in row.keys() else "",
                row["domain"] if "domain" in row.keys() else "",
                row["published_at"] if "published_at" in row.keys() else "",
                row["raw_summary"] if "raw_summary" in row.keys() else "",
                row["final_summary"] if "final_summary" in row.keys() else "",
                row["score"] if "score" in row.keys() else 0,
                row["created_at"] if "created_at" in row.keys() else utc_now().isoformat(),
            ),
        )


def copy_legacy_sent_news(conn: sqlite3.Connection, topic_ids: dict[str, int]) -> None:
    if not table_exists(conn, "sent_news_legacy_category"):
        return
    if conn.execute("SELECT COUNT(*) FROM sent_news").fetchone()[0] > 0:
        return
    rows = conn.execute("SELECT * FROM sent_news_legacy_category").fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO sent_news
            (topic_id, url, title, source, sent_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                topic_id_from_legacy(row, topic_ids),
                normalize_url(row["url"]),
                row["title"] if "title" in row.keys() else "",
                row["source"] if "source" in row.keys() else "",
                row["sent_at"] if "sent_at" in row.keys() else utc_now().isoformat(),
            ),
        )


def topic_id_from_legacy(row: sqlite3.Row, topic_ids: dict[str, int]) -> int | None:
    if "topic_id" in row.keys() and row["topic_id"]:
        return row["topic_id"]
    category = str(row["category"] if "category" in row.keys() else "").strip().lower()
    if category in {"ai", "人工智能"}:
        return topic_ids.get("ai")
    if category in {"football", "soccer", "足球"}:
        return topic_ids.get("football")
    return None


def migrate_legacy_sent_news(app_db_path: Path) -> None:
    legacy_path = app_db_path.parent / "sent_news.sqlite3"
    if not legacy_path.exists() or legacy_path == app_db_path:
        return

    engine = get_engine(app_db_path)
    with Session(engine) as session:
        if session.exec(select(SentNews)).first():
            return
        topic_map = get_topic_map(session)

        try:
            with sqlite3.connect(legacy_path) as conn:
                rows = conn.execute(
                    "SELECT url, title, category, source, sent_at FROM sent_news"
                ).fetchall()
        except sqlite3.Error:
            return

        for url, title, category, source, sent_at in rows:
            normalized = normalize_url(url)
            if not normalized:
                continue
            session.add(
                SentNews(
                    topic_id=topic_id_from_category(category, topic_map),
                    url=normalized,
                    title=title or "",
                    source=source or "",
                    sent_at=parse_legacy_datetime(sent_at),
                )
            )
        session.commit()


def topic_id_from_category(category: str, topic_map: dict[str, Topic]) -> int | None:
    lowered = str(category or "").strip().lower()
    if lowered in {"ai", "人工智能"} and "ai" in topic_map:
        return topic_map["ai"].id
    if lowered in {"football", "soccer", "足球"} and "football" in topic_map:
        return topic_map["football"].id
    return None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "topic"


def parse_legacy_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return utc_now()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    init_db()
    print(f"initialized {get_settings().db_path}")
