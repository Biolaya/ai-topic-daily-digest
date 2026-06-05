import sys
from types import SimpleNamespace

from src.models import Keyword, QueryTemplate
from src.searcher import build_topic_queries, fetch_news, search_topic_news


class FakeTavilyClient:
    instances = []

    def __init__(self, api_key):
        self.api_key = api_key
        self.queries = []
        FakeTavilyClient.instances.append(self)

    def search(self, **kwargs):
        query = kwargs["query"]
        self.queries.append(query)
        return {
            "results": [
                {
                    "title": f"Result for {query}",
                    "url": f"https://example.com/{len(self.queries)}",
                    "source": "Example",
                    "published_date": "2026-05-23T08:00:00+00:00",
                    "content": "news content",
                }
            ]
        }


def test_search_topic_news_uses_query_templates_before_keywords():
    topic = SimpleNamespace(id=10, name="Minecraft", slug="minecraft", daily_limit=8, priority=5, enabled=True)
    templates = [
        QueryTemplate(topic_id=10, query_text="low priority query", priority=1, enabled=True),
        QueryTemplate(topic_id=10, query_text="high priority query", priority=10, enabled=True),
    ]
    keywords = [Keyword(topic_id=10, word="Minecraft", language="mixed", weight=10, enabled=True)]
    client = FakeTavilyClient("test")

    items = search_topic_news(client, topic, keywords, templates)

    assert client.queries[0] == "high priority query"
    assert items[0]["topic_id"] == 10
    assert items[0]["topic_slug"] == "minecraft"


def test_build_topic_queries_falls_back_to_keywords():
    topic = SimpleNamespace(id=10, name="Coding Agents", slug="coding-agents", daily_limit=8)
    keywords = [
        Keyword(topic_id=10, word="Codex", language="en", weight=10, enabled=True),
        Keyword(topic_id=10, word="编程智能体", language="zh", weight=9, enabled=True),
    ]

    queries = build_topic_queries(topic, keywords, [])

    assert any("Codex" in query for query in queries)
    assert any("编程智能体" in query for query in queries)


def test_fetch_news_skips_disabled_topics(monkeypatch):
    FakeTavilyClient.instances = []
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=FakeTavilyClient))
    settings = SimpleNamespace(tavily_api_key="test")
    enabled_topic = SimpleNamespace(id=1, name="AI", slug="ai", daily_limit=8, priority=10, enabled=True)
    disabled_topic = SimpleNamespace(id=2, name="Anime", slug="anime", daily_limit=8, priority=9, enabled=False)
    templates_by_topic = {
        1: [QueryTemplate(topic_id=1, query_text="today AI news", priority=10, enabled=True)],
        2: [QueryTemplate(topic_id=2, query_text="today anime news", priority=10, enabled=True)],
    }

    items = fetch_news(settings, [enabled_topic, disabled_topic], {}, templates_by_topic)

    assert len(items) == 1
    assert FakeTavilyClient.instances[0].queries == ["today AI news"]


def test_search_topic_news_filters_invalid_and_duplicate_results():
    topic = SimpleNamespace(id=10, name="Minecraft", slug="minecraft", daily_limit=8, priority=5, enabled=True)

    class InvalidResultClient:
        def search(self, **kwargs):
            return {
                "results": [
                    {"title": "", "url": "https://example.com/empty-title"},
                    {"title": "Bad URL", "url": "mailto:test@example.com"},
                    {"title": "Valid", "url": "https://example.com/news?utm_source=x"},
                    {"title": "Duplicate", "url": "https://example.com/news"},
                ]
            }

    items = search_topic_news(
        InvalidResultClient(),
        topic,
        [],
        [QueryTemplate(topic_id=10, query_text="minecraft news", priority=10, enabled=True)],
    )

    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/news"


def test_fetch_news_continues_when_one_topic_search_fails(monkeypatch):
    class PartiallyFailingClient:
        def __init__(self, api_key):
            pass

        def search(self, **kwargs):
            if "broken" in kwargs["query"]:
                raise RuntimeError("upstream failed")
            return {
                "results": [
                    {
                        "title": "AI news",
                        "url": "https://example.com/ai",
                        "content": "content",
                    }
                ]
            }

    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=PartiallyFailingClient))
    settings = SimpleNamespace(tavily_api_key="test")
    topics = [
        SimpleNamespace(id=1, name="Broken", slug="broken", daily_limit=8, priority=10, enabled=True),
        SimpleNamespace(id=2, name="AI", slug="ai", daily_limit=8, priority=9, enabled=True),
    ]
    templates_by_topic = {
        1: [QueryTemplate(topic_id=1, query_text="broken news", priority=10, enabled=True)],
        2: [QueryTemplate(topic_id=2, query_text="ai news", priority=10, enabled=True)],
    }

    items = fetch_news(settings, topics, {}, templates_by_topic)

    assert len(items) == 1
    assert items[0]["topic_id"] == 2


def test_fetch_news_requires_tavily_api_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "tavily", SimpleNamespace(TavilyClient=FakeTavilyClient))
    settings = SimpleNamespace(tavily_api_key="")
    topic = SimpleNamespace(id=1, name="AI", slug="ai", daily_limit=8, priority=10, enabled=True)

    try:
        fetch_news(settings, [topic], {}, {})
    except ValueError as exc:
        assert "TAVILY_API_KEY" in str(exc)
    else:
        raise AssertionError("fetch_news should require TAVILY_API_KEY")
