from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SettingRecord(SQLModel, table=True):
    __tablename__ = "settings"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str = ""
    updated_at: datetime = Field(default_factory=utc_now)


class Topic(SQLModel, table=True):
    __tablename__ = "topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    slug: str = Field(index=True, unique=True)
    description: str = ""
    language: str = "mixed"
    daily_limit: int = 8
    min_score: int = 40
    priority: int = 5
    summary_style: str = "concise"
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Keyword(SQLModel, table=True):
    __tablename__ = "keywords"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    word: str = Field(index=True)
    language: str = Field(index=True)
    weight: int = 5
    match_type: str = "contains"
    enabled: bool = True
    note: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    domain: str = Field(index=True)
    name: str = ""
    base_score: int = 45
    enabled: bool = True
    note: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmailRecipient(SQLModel, table=True):
    __tablename__ = "email_recipients"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    name: str = ""
    enabled: bool = True
    is_default: bool = False
    note: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TavilyKeyStatus(SQLModel, table=True):
    __tablename__ = "tavily_key_status"

    id: Optional[int] = Field(default=None, primary_key=True)
    key_fingerprint: str = Field(index=True, unique=True)
    status: str = "active"
    failure_count: int = 0
    last_error: str = ""
    last_used_at: Optional[datetime] = None
    disabled_until: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class QueryTemplate(SQLModel, table=True):
    __tablename__ = "query_templates"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="topics.id", index=True)
    query_text: str
    language: str = "mixed"
    priority: int = 5
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class NewsItem(SQLModel, table=True):
    __tablename__ = "news_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    url: str = Field(index=True)
    title: str = ""
    source: str = ""
    domain: str = Field(default="", index=True)
    published_at: str = ""
    raw_summary: str = ""
    final_summary: str = ""
    score: float = 0.0
    created_at: datetime = Field(default_factory=utc_now)


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_type: str = Field(index=True)
    started_at: datetime = Field(default_factory=utc_now, index=True)
    finished_at: Optional[datetime] = None
    success: bool = False
    topic_counts: str = ""
    email_subject: str = ""
    error_message: str = ""
    html_snapshot: str = ""


class SentNews(SQLModel, table=True):
    __tablename__ = "sent_news"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    url: str = Field(index=True, unique=True)
    title: str
    source: str = ""
    sent_at: datetime = Field(default_factory=utc_now)
