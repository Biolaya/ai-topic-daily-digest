from __future__ import annotations

import json
import logging
import re
from typing import Iterable


def summarize_digest(news_items: Iterable[dict], settings, logger: logging.Logger | None = None, topics: list | None = None) -> dict:
    logger = logger or logging.getLogger(__name__)
    items = list(news_items)
    topic_snapshots = [_topic_snapshot(topic) for topic in (topics or [])]

    if not items:
        return _fallback_digest(items, topic_snapshots)

    if not settings.openai_api_key:
        logger.warning("LLM_API_KEY/OPENAI_API_KEY 为空，使用 Tavily 原始摘要生成邮件。")
        return _fallback_digest(items, topic_snapshots)

    try:
        return _summarize_with_openai(items, topic_snapshots, settings)
    except Exception as exc:
        logger.warning("LLM 摘要生成失败，使用 Tavily 原始摘要降级。错误类型: %s", exc.__class__.__name__)
        return _fallback_digest(items, topic_snapshots)


def _summarize_with_openai(items: list[dict], topics: list[dict], settings) -> dict:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - covered by runtime dependency install
        raise RuntimeError("未安装 openai，请先运行 pip install -r requirements.txt") from exc

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    payload = [_compact_item(item) for item in items]

    request_kwargs = {
        "model": settings.openai_model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严谨的中文新闻编辑，目标是让读者不点开原文也能理解新闻核心。"
                    "只基于用户提供的主题、标题、来源、时间、链接和内容写摘要，不要编造未提供的信息。"
                    "必须提取具体事实、主体、动作、数字、影响和结论；信息不足时明确写“原始信息未说明具体细节”。"
                    "禁止使用空泛表述，例如“分析了局势”“介绍了变动”“详情见原文”“需要阅读全文”。"
                    "输出必须是 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请把下面新闻整理成多主题中文日报。\n"
                    f"全局摘要风格：{settings.summary_style}。\n"
                    "每个主题生成一个 section，并优先遵循启用主题里对应 topic 的 summary_style。"
                    "topic_summary 用 1-2 句概括该主题今天最关键的变化。"
                    "每条新闻 summary 写 2-4 句中文，必须覆盖文章中可得的关键事实、具体变动、涉及主体、结果、影响或下一步。"
                    "如果原始内容没有给出细节，不要模糊带过，要直接说明“原始信息未说明具体细节”。"
                    "最后选出今日最值得关注的 5 条，reason 用一句具体理由说明为什么重要。\n"
                    "请严格返回 JSON，格式为："
                    '{"overview":["看点一","看点二"],'
                    '"sections":[{"topic_id":1,"topic":"AI","topic_summary":"","news_items":[{"title":"","source":"","published_at":"","summary":"","url":""}]}],'
                    '"top5":[{"topic":"","title":"","reason":"","url":""}]}'
                    "\n启用主题：\n"
                    f"{json.dumps(topics, ensure_ascii=False)}"
                    "\n新闻数据：\n"
                    f"{json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ],
    }

    if "deepseek" in settings.openai_base_url.lower():
        request_kwargs["extra_body"] = {
            "thinking": {"type": "enabled" if settings.deepseek_thinking else "disabled"}
        }

    response = client.chat.completions.create(**request_kwargs)

    content = response.choices[0].message.content or ""
    parsed = json.loads(_extract_json(content))
    return _normalize_digest(parsed, items, topics)


def _compact_item(item: dict) -> dict:
    content = _clean_text(str(item.get("content") or item.get("raw_summary") or ""))
    return {
        "topic_id": item.get("topic_id"),
        "topic": item.get("topic_name") or item.get("topic", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "published_at": item.get("published_at", ""),
        "url": item.get("url", ""),
        "score": item.get("score", 0),
        "content": content[:1600],
    }


def _topic_snapshot(topic) -> dict:
    return {
        "topic_id": getattr(topic, "id", None),
        "topic": getattr(topic, "name", ""),
        "summary_style": getattr(topic, "summary_style", ""),
        "daily_limit": getattr(topic, "daily_limit", 0),
    }


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("OpenAI 返回内容不是 JSON")
    return stripped[start : end + 1]


def _normalize_digest(parsed: dict, original_items: list[dict], topics: list[dict]) -> dict:
    overview = _normalize_overview(parsed.get("overview") or parsed.get("总体摘要看点") or [])
    sections = _normalize_sections(parsed.get("sections") or [], original_items, topics)
    top5 = _normalize_top_items(parsed.get("top5") or parsed.get("top3") or [])

    if not sections:
        return _fallback_digest(original_items, topics)

    if not overview:
        overview = _fallback_overview(sections)
    if not top5:
        top5 = _fallback_top5(sections)

    return {"overview": overview[:5], "sections": sections, "top5": top5[:5]}


def _normalize_sections(raw_sections, original_items: list[dict], topics: list[dict]) -> list[dict]:
    topic_order = {topic["topic_id"]: index for index, topic in enumerate(topics)}
    sections_by_id = {}

    if isinstance(raw_sections, list):
        for section in raw_sections:
            if not isinstance(section, dict):
                continue
            topic_id = section.get("topic_id")
            if topic_id is None:
                topic_id = _topic_id_by_name(str(section.get("topic", "")), topics)
            topic_name = str(section.get("topic") or _topic_name_by_id(topic_id, topics) or "").strip()
            if topic_id is None and not topic_name:
                continue
            sections_by_id[topic_id] = {
                "topic_id": topic_id,
                "topic": topic_name,
                "topic_summary": _clean_text(str(section.get("topic_summary", ""))),
                "news_items": _normalize_summary_items(section.get("news_items") or []),
            }

    for topic in topics:
        topic_id = topic["topic_id"]
        sections_by_id.setdefault(
            topic_id,
            {
                "topic_id": topic_id,
                "topic": topic["topic"],
                "topic_summary": "",
                "news_items": [],
            },
        )

    for section in sections_by_id.values():
        if not section["news_items"]:
            fallback_items = [_fallback_item(item) for item in original_items if item.get("topic_id") == section["topic_id"]]
            section["news_items"] = fallback_items
        if not section["topic_summary"]:
            if section["news_items"]:
                section["topic_summary"] = f"{section['topic']} 今日有 {len(section['news_items'])} 条高质量更新。"
            else:
                section["topic_summary"] = "今日暂无高质量更新。"

    return sorted(sections_by_id.values(), key=lambda item: topic_order.get(item["topic_id"], 999))


def _normalize_overview(value) -> list[str]:
    if isinstance(value, str):
        lines = re.split(r"[\n；;]+", value)
    elif isinstance(value, list):
        lines = [str(item.get("text", item.get("summary", ""))) if isinstance(item, dict) else str(item) for item in value]
    else:
        lines = []
    return [_clean_text(line)[:220] for line in lines if _clean_text(line)]


def _normalize_summary_items(items: list[dict]) -> list[dict]:
    normalized = []
    for item in items:
        summary = item.get("summary", "")
        if isinstance(summary, list):
            summary = "\n".join(str(line) for line in summary[:4])
        normalized.append(
            {
                "title": str(item.get("title", "")).strip(),
                "source": str(item.get("source", "")).strip(),
                "published_at": str(item.get("published_at", "")).strip(),
                "summary": _trim_summary(str(summary)),
                "url": str(item.get("url", "")).strip(),
            }
        )
    return [item for item in normalized if item["title"] and item["url"]]


def _normalize_top_items(items: list[dict]) -> list[dict]:
    normalized = []
    for item in items:
        normalized.append(
            {
                "topic": str(item.get("topic", "")).strip(),
                "title": str(item.get("title", "")).strip(),
                "reason": _clean_text(str(item.get("reason") or item.get("summary") or ""))[:280],
                "url": str(item.get("url", "")).strip(),
            }
        )
    return [item for item in normalized if item["title"] and item["url"]]


def _fallback_digest(items: list[dict], topics: list[dict]) -> dict:
    sections = []
    for topic in topics:
        topic_items = [_fallback_item(item) for item in items if item.get("topic_id") == topic["topic_id"]]
        sections.append(
            {
                "topic_id": topic["topic_id"],
                "topic": topic["topic"],
                "topic_summary": f"{topic['topic']} 今日有 {len(topic_items)} 条高质量更新。" if topic_items else "今日暂无高质量更新。",
                "news_items": topic_items,
            }
        )
    if not sections and items:
        grouped = {}
        for item in items:
            grouped.setdefault(item.get("topic_id"), []).append(item)
        for topic_id, topic_items in grouped.items():
            topic_name = topic_items[0].get("topic_name") or "主题"
            sections.append(
                {
                    "topic_id": topic_id,
                    "topic": topic_name,
                    "topic_summary": f"{topic_name} 今日有 {len(topic_items)} 条高质量更新。",
                    "news_items": [_fallback_item(item) for item in topic_items],
                }
            )
    return {"overview": _fallback_overview(sections), "sections": sections, "top5": _fallback_top5(sections)}


def _fallback_item(item: dict) -> dict:
    content = _clean_text(str(item.get("content") or item.get("raw_summary") or ""))
    summary = content[:320].rstrip()
    if len(content) > 320:
        summary += "..."
    if not summary:
        summary = "原始信息未说明具体细节。"

    return {
        "title": str(item.get("title", "")).strip(),
        "source": str(item.get("source", "")).strip(),
        "published_at": str(item.get("published_at", "")).strip(),
        "summary": _trim_summary(summary),
        "url": str(item.get("url", "")).strip(),
        "score": float(item.get("score", 0) or 0),
    }


def _fallback_overview(sections: list[dict]) -> list[str]:
    overview = []
    for section in sections:
        if section.get("news_items"):
            overview.append(f"{section['topic']}：{section['news_items'][0].get('title', '')}")
    if not overview:
        overview.append("今日暂无高质量更新。")
    return overview[:5]


def _fallback_top5(sections: list[dict]) -> list[dict]:
    candidates = []
    for section in sections:
        for item in section.get("news_items", []):
            candidates.append(
                (
                    float(item.get("score", 0) or 0),
                    {
                        "topic": section.get("topic", ""),
                        "title": item.get("title", ""),
                        "reason": _clean_text(str(item.get("summary", "")))[:160] or "今日高质量新闻条目。",
                        "url": item.get("url", ""),
                    },
                )
            )
    return [item for _, item in sorted(candidates, key=lambda pair: pair[0], reverse=True)[:5]]


def _topic_name_by_id(topic_id, topics: list[dict]) -> str:
    for topic in topics:
        if topic["topic_id"] == topic_id:
            return topic["topic"]
    return ""


def _topic_id_by_name(name: str, topics: list[dict]):
    lowered = name.lower()
    for topic in topics:
        if topic["topic"].lower() == lowered:
            return topic["topic_id"]
    return None


def _trim_summary(summary: str) -> str:
    lines = [_clean_text(line) for line in summary.splitlines() if _clean_text(line)]
    if not lines:
        lines = [_clean_text(summary)]
    return "\n".join(lines[:4])


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
