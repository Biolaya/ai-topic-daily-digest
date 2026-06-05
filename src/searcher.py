from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

from src.dedupe import normalize_url
from src.scorer import normalize_domain
from src.tavily_key_manager import TavilyKeyManager


def fetch_news(settings, topics: list, keywords_by_topic: dict[int, list], templates_by_topic: dict[int, list], logger: logging.Logger | None = None) -> list[dict]:
    logger = logger or logging.getLogger(__name__)

    client = TavilyKeyManager.from_settings(settings)
    if not client.has_keys():
        raise ValueError("TAVILY_API_KEYS 或 TAVILY_API_KEY 为空，无法搜索新闻")

    all_items: list[dict] = []
    for topic in sorted(topics, key=lambda item: (-int(getattr(item, "priority", 5)), getattr(item, "name", ""))):
        if not getattr(topic, "enabled", True):
            continue
        try:
            all_items.extend(
                search_topic_news(
                    client,
                    topic,
                    keywords_by_topic.get(topic.id, []),
                    templates_by_topic.get(topic.id, []),
                    logger,
                )
            )
        except Exception as exc:
            logger.warning("主题搜索失败，topic=%s，已跳过该主题：%s", getattr(topic, "name", ""), exc.__class__.__name__)
    return all_items


def search_topic_news(client, topic, keywords: list, query_templates: list, logger: logging.Logger | None = None) -> list[dict]:
    logger = logger or logging.getLogger(__name__)
    candidate_limit = max(int(topic.daily_limit) * 2, 1)
    queries = build_topic_queries(topic, keywords, query_templates)
    results: list[dict] = []
    seen_urls: set[str] = set()

    for query in queries:
        try:
            response = _search_once(client, query, candidate_limit, logger)
        except Exception as exc:
            logger.warning("搜索模板失败，topic=%s，query=%s，已继续下一个 query：%s", topic.name, query, exc.__class__.__name__)
            continue
        for result in _extract_results(response):
            item = _normalize_result(result, topic)
            if not _is_valid_item(item) or item["url"] in seen_urls:
                continue
            results.append(item)
            seen_urls.add(item["url"])
            if len(results) >= candidate_limit:
                return results

    return results[:candidate_limit]


def build_topic_queries(topic, keywords: list, query_templates: list) -> list[str]:
    enabled_templates = [
        template
        for template in query_templates
        if bool(getattr(template, "enabled", True)) and str(getattr(template, "query_text", "")).strip()
    ]
    if enabled_templates:
        return [
            template.query_text.strip()
            for template in sorted(enabled_templates, key=lambda item: int(item.priority), reverse=True)
        ]

    enabled_keywords = [
        keyword
        for keyword in keywords
        if bool(getattr(keyword, "enabled", True)) and str(getattr(keyword, "word", "")).strip()
    ]
    enabled_keywords = sorted(enabled_keywords, key=lambda item: int(item.weight), reverse=True)
    if not enabled_keywords:
        return [f"today {topic.name} news", f"今日 {topic.name} 新闻"]

    queries = []
    for language, prefix in (("en", "today news"), ("zh", "今日 新闻"), ("mixed", "today news 今日 新闻")):
        words = [
            keyword.word.strip()
            for keyword in enabled_keywords
            if keyword.language == language or keyword.language == "mixed"
        ]
        if words:
            queries.append(" ".join([prefix, topic.name] + words[:8]))

    return queries or [" ".join(["today news", topic.name] + [keyword.word for keyword in enabled_keywords[:8]])]


def _search_once(client, query: str, max_results: int, logger: logging.Logger):
    attempts = [
        {"query": query, "topic": "news", "max_results": max_results, "days": 1, "timeout": 10},
        {"query": query, "topic": "news", "max_results": max_results, "time_range": "day", "timeout": 10},
        {"query": query, "max_results": max_results, "time_range": "day", "timeout": 10},
    ]

    last_type_error: TypeError | None = None
    for kwargs in attempts:
        for retry_index in range(2):
            try:
                return client.search(**kwargs)
            except TypeError as exc:
                last_type_error = exc
                break
            except Exception as exc:
                logger.warning(
                    "Tavily 搜索失败，query=%s，attempt=%s，error=%s",
                    query,
                    retry_index + 1,
                    exc.__class__.__name__,
                )
                if retry_index == 0:
                    time.sleep(0.3)
                    continue
                raise RuntimeError("Tavily 搜索失败，已重试并跳过该 query") from exc

    raise RuntimeError("当前 tavily-python 版本不支持预期的 search 参数") from last_type_error


def _extract_results(response) -> list[dict]:
    if isinstance(response, dict):
        results = response.get("results", [])
        return results if isinstance(results, list) else []
    if isinstance(response, list):
        return response
    return []


def _normalize_result(result: dict, topic) -> dict:
    url = str(result.get("url", "")).strip()
    normalized_url = normalize_url(url)
    parsed = urlsplit(url)
    source = (
        str(result.get("source", "")).strip()
        or str(result.get("publisher", "")).strip()
        or parsed.netloc.replace("www.", "")
    )

    return {
        "topic_id": topic.id,
        "topic_name": topic.name,
        "topic_slug": topic.slug,
        "title": str(result.get("title", "")).strip(),
        "url": normalized_url,
        "source": source,
        "domain": normalize_domain(url or source),
        "published_at": str(
            result.get("published_date")
            or result.get("published_at")
            or result.get("date")
            or ""
        ).strip(),
        "content": str(
            result.get("content")
            or result.get("snippet")
            or result.get("description")
            or result.get("raw_content")
            or ""
        ).strip(),
    }


def _is_valid_item(item: dict) -> bool:
    title = str(item.get("title", "")).strip()
    url = str(item.get("url", "")).strip()
    if not title or not url:
        return False
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
