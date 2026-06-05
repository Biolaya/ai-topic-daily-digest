import sys
from types import SimpleNamespace

from src.database import init_db
from src.models import QueryTemplate
from src.searcher import fetch_news


class AuthError(Exception):
    status_code = 401


class FailoverTavilyClient:
    used_keys = []

    def __init__(self, api_key):
        self.api_key = api_key

    def search(self, **kwargs):
        self.__class__.used_keys.append(self.api_key)
        if self.api_key == "bad_key":
            raise AuthError("bad key")
        return {
            "results": [
                {
                    "title": "Valid key result",
                    "url": "https://example.com/valid",
                    "content": "content",
                }
            ]
        }


def test_fetch_news_uses_tavily_key_failover(monkeypatch, tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    FailoverTavilyClient.used_keys = []
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=FailoverTavilyClient))
    settings = SimpleNamespace(tavily_api_keys=("bad_key", "valid_key"), tavily_api_key="", db_path=db_path)
    topic = SimpleNamespace(id=1, name="AI", slug="ai", daily_limit=8, priority=10, enabled=True)
    templates_by_topic = {1: [QueryTemplate(topic_id=1, query_text="today AI news", priority=10, enabled=True)]}

    items = fetch_news(settings, [topic], {}, templates_by_topic)

    assert len(items) == 1
    assert items[0]["title"] == "Valid key result"
    assert FailoverTavilyClient.used_keys == ["bad_key", "valid_key"]


def test_fetch_news_accepts_legacy_single_tavily_key(monkeypatch):
    FailoverTavilyClient.used_keys = []
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=FailoverTavilyClient))
    settings = SimpleNamespace(tavily_api_key="valid_key")
    topic = SimpleNamespace(id=1, name="AI", slug="ai", daily_limit=8, priority=10, enabled=True)
    templates_by_topic = {1: [QueryTemplate(topic_id=1, query_text="today AI news", priority=10, enabled=True)]}

    items = fetch_news(settings, [topic], {}, templates_by_topic)

    assert len(items) == 1
    assert FailoverTavilyClient.used_keys == ["valid_key"]
