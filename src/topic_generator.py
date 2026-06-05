from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlmodel import Session, select

from src.database import slugify
from src.models import Keyword, QueryTemplate, Source, Topic, utc_now
from src.scorer import normalize_domain


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class TopicGenerationRequest:
    interest_description: str
    preferred_language: str = "mixed"
    daily_limit: int = 8
    min_score: int = 45
    exclude_entertainment: bool = True
    exclude_sports: bool = False
    include_zh_sources: bool = True
    include_en_sources: bool = True


def generate_topic_suggestion(settings, request: TopicGenerationRequest) -> dict:
    if not getattr(settings, "llm_api_key", ""):
        raise ValueError("LLM_API_KEY/OPENAI_API_KEY 为空，无法生成主题")

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("未安装 openai，请先运行 pip install -r requirements.txt") from exc

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    response = client.chat.completions.create(
        model=settings.llm_model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "你是新闻订阅主题配置助手。只输出合法 JSON，不输出 Markdown，不输出解释文字。"
                    "不要编造不存在的来源域名；不确定的来源宁可不给。"
                    "所有字段必须适合保存到数据库，slug 只能包含小写字母、数字和连字符。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请只输出 JSON，为下面兴趣生成新闻订阅主题配置。\n"
                    f"兴趣描述：{request.interest_description}\n"
                    f"preferred_language={request.preferred_language}, daily_limit={request.daily_limit}, min_score={request.min_score}\n"
                    f"exclude_entertainment={request.exclude_entertainment}, exclude_sports={request.exclude_sports}, "
                    f"include_zh_sources={request.include_zh_sources}, include_en_sources={request.include_en_sources}\n"
                    "JSON 结构必须是："
                    '{"topic":{"name":"","slug":"","description":"","language":"mixed","daily_limit":8,"min_score":45,"priority":5,"summary_style":"concise"},'
                    '"keywords":[{"word":"","language":"mixed","weight":8,"match_type":"contains","note":""}],'
                    '"query_templates":[{"query_text":"","language":"mixed","priority":8}],'
                    '"sources":[{"domain":"","name":"","base_score":80,"note":""}]}'
                ),
            },
        ],
    )
    content = response.choices[0].message.content or ""
    return validate_topic_suggestion(json.loads(_extract_json(content)))


def validate_topic_suggestion(data: dict, existing_slugs: set[str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("生成结果不是 JSON object")

    topic_data = dict(data.get("topic") or {})
    name = clean_text(topic_data.get("name"))
    if not name:
        raise ValueError("topic.name 不能为空")

    slug = clean_text(topic_data.get("slug")) or slugify(name)
    slug = slugify(slug)
    if not SLUG_RE.match(slug):
        raise ValueError("slug 只能包含小写字母、数字和连字符")
    if existing_slugs and slug in existing_slugs:
        raise ValueError(f"slug 已存在：{slug}")

    topic = {
        "name": name,
        "slug": slug,
        "description": clean_text(topic_data.get("description")),
        "language": clean_choice(topic_data.get("language"), {"zh", "en", "mixed"}, "mixed"),
        "daily_limit": clamp_int(topic_data.get("daily_limit"), 8, 1, 20),
        "min_score": clamp_int(topic_data.get("min_score"), 45, 0, 100),
        "priority": clamp_int(topic_data.get("priority"), 5, 0, 100),
        "summary_style": clean_text(topic_data.get("summary_style")) or "concise",
    }

    keywords = []
    seen_keywords = set()
    for item in data.get("keywords") or []:
        if not isinstance(item, dict):
            continue
        word = clean_text(item.get("word"))
        key = word.lower()
        if not word or key in seen_keywords:
            continue
        seen_keywords.add(key)
        keywords.append(
            {
                "word": word,
                "language": clean_choice(item.get("language"), {"zh", "en", "mixed"}, topic["language"]),
                "weight": clamp_int(item.get("weight"), 5, 1, 10),
                "match_type": clean_choice(item.get("match_type"), {"contains", "exact"}, "contains"),
                "note": clean_text(item.get("note")),
            }
        )

    queries = []
    seen_queries = set()
    for item in data.get("query_templates") or []:
        if not isinstance(item, dict):
            continue
        query_text = clean_text(item.get("query_text"))
        key = query_text.lower()
        if not query_text or key in seen_queries:
            continue
        seen_queries.add(key)
        queries.append(
            {
                "query_text": query_text,
                "language": clean_choice(item.get("language"), {"zh", "en", "mixed"}, topic["language"]),
                "priority": clamp_int(item.get("priority"), 5, 0, 100),
            }
        )

    sources = []
    seen_domains = set()
    for item in data.get("sources") or []:
        if not isinstance(item, dict):
            continue
        domain = normalize_domain(clean_text(item.get("domain")))
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        sources.append(
            {
                "domain": domain,
                "name": clean_text(item.get("name")),
                "base_score": clamp_int(item.get("base_score"), 45, 0, 100),
                "note": clean_text(item.get("note")),
            }
        )

    return {"topic": topic, "keywords": keywords[:30], "query_templates": queries[:10], "sources": sources[:20]}


def save_topic_suggestion(session: Session, suggestion: dict, merge_existing: bool = False) -> Topic:
    existing_slugs = {row.slug for row in session.exec(select(Topic)).all()}
    slug = suggestion.get("topic", {}).get("slug", "")
    if slug in existing_slugs and not merge_existing:
        raise ValueError(f"slug 已存在：{slug}")

    normalized = validate_topic_suggestion(suggestion)
    topic_data = normalized["topic"]
    topic = session.exec(select(Topic).where(Topic.slug == topic_data["slug"])).first()
    if not topic:
        topic = Topic(**topic_data, enabled=True)
    else:
        for key, value in topic_data.items():
            setattr(topic, key, value)
        topic.updated_at = utc_now()
    session.add(topic)
    session.commit()
    session.refresh(topic)

    upsert_keywords(session, topic, normalized["keywords"])
    upsert_queries(session, topic, normalized["query_templates"])
    upsert_sources(session, topic, normalized["sources"])
    session.commit()
    return topic


def upsert_keywords(session: Session, topic: Topic, items: list[dict]) -> None:
    existing = {row.word.lower(): row for row in session.exec(select(Keyword).where(Keyword.topic_id == topic.id)).all()}
    for item in items:
        row = existing.get(item["word"].lower()) or Keyword(topic_id=topic.id, word=item["word"])
        row.topic_id = topic.id
        row.word = item["word"]
        row.language = item["language"]
        row.weight = item["weight"]
        row.match_type = item["match_type"]
        row.note = item["note"]
        row.enabled = True
        row.updated_at = utc_now()
        session.add(row)


def upsert_queries(session: Session, topic: Topic, items: list[dict]) -> None:
    existing = {row.query_text.lower(): row for row in session.exec(select(QueryTemplate).where(QueryTemplate.topic_id == topic.id)).all()}
    for item in items:
        row = existing.get(item["query_text"].lower()) or QueryTemplate(topic_id=topic.id, query_text=item["query_text"])
        row.topic_id = topic.id
        row.query_text = item["query_text"]
        row.language = item["language"]
        row.priority = item["priority"]
        row.enabled = True
        row.updated_at = utc_now()
        session.add(row)


def upsert_sources(session: Session, topic: Topic, items: list[dict]) -> None:
    existing = {row.domain.lower(): row for row in session.exec(select(Source).where(Source.topic_id == topic.id)).all()}
    for item in items:
        row = existing.get(item["domain"].lower()) or Source(topic_id=topic.id, domain=item["domain"])
        row.topic_id = topic.id
        row.domain = item["domain"]
        row.name = item["name"]
        row.base_score = item["base_score"]
        row.note = item["note"]
        row.enabled = True
        row.updated_at = utc_now()
        session.add(row)


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM 返回内容不是 JSON")
    return stripped[start : end + 1]


def clean_text(value) -> str:
    return str(value or "").strip()


def clean_choice(value, allowed: set[str], default: str) -> str:
    value = clean_text(value)
    return value if value in allowed else default


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))
