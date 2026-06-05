from __future__ import annotations

import json
import logging
from dataclasses import replace
from html import escape as html_escape
from typing import Any

from sqlmodel import Session, select

from src.archiver import save_digest_archive
from src.cleanup import prune_old_data
from src.database import get_settings_map, init_db, is_sent, mark_sent, set_setting
from src.dedupe import filter_new_items
from src.emailer import parse_recipients, send_email
from src.models import EmailRecipient, Keyword, NewsItem, QueryTemplate, Run, Source, Topic, utc_now
from src.renderer import make_subject, render_email
from src.scorer import calculate_score
from src.searcher import fetch_news
from src.summarizer import summarize_digest


def run_digest(
    settings,
    session: Session,
    run_type: str,
    send: bool = False,
    respect_auto_send: bool = True,
    persist_items: bool = True,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    logger = logger or logging.getLogger(__name__)
    init_db(settings.db_path)

    # persist_items=False 时(实时预览):Run 仅作返回壳,不入库;NewsItem 也不落库,纯只读。
    run = Run(run_type=run_type, started_at=utc_now())
    if persist_items:
        session.add(run)
        session.commit()
        session.refresh(run)

    # 先兜底,确保 except 块即便在 apply_db_settings 之前抛错也能引用(避免 NameError)
    runtime_settings = settings
    try:
        runtime_settings = apply_db_settings(settings, session)

        if send and respect_auto_send and not str_to_bool(get_settings_map(session).get("auto_send_enabled", "true")):
            raise RuntimeError("auto_send_enabled=false，已跳过自动发送")

        topics = get_enabled_topics(session)
        keywords = session.exec(select(Keyword).where(Keyword.enabled == True)).all()  # noqa: E712
        templates = session.exec(select(QueryTemplate).where(QueryTemplate.enabled == True)).all()  # noqa: E712
        sources = session.exec(select(Source)).all()
        keywords_by_topic = group_by_topic_id(keywords)
        templates_by_topic = group_by_topic_id(templates)

        logger.info("开始获取新闻，启用主题数=%s", len(topics))
        raw_items = fetch_news(runtime_settings, topics, keywords_by_topic, templates_by_topic, logger)
        logger.info("获取到 %s 条原始新闻", len(raw_items))

        new_items = filter_new_items(raw_items, is_sent=lambda url: is_sent(session, url))
        logger.info("去重后保留 %s 条未发送新闻", len(new_items))

        scored_items = score_and_store_items(session, new_items, topics, keywords_by_topic, sources, persist=persist_items)
        selected_items = select_items(scored_items, topics)
        logger.info("评分过滤后保留 %s 条新闻", len(selected_items))

        digest_items = [to_digest_item(item) for item in selected_items]
        digest = summarize_digest(digest_items, runtime_settings, logger, topics=topics)
        subject = make_subject(runtime_settings.timezone, topic_count=len(topics))
        html = render_email(digest, subject)

        run.topic_counts = json.dumps(build_topic_counts(selected_items, topics), ensure_ascii=False)
        run.email_subject = subject
        run.html_snapshot = html

        if send:
            recipients = resolve_recipients(session, runtime_settings)
            send_email(runtime_settings, subject, html, recipients=recipients)
            try:
                archive_path = save_digest_archive(html, runtime_settings.archive_dir, runtime_settings.timezone)
                logger.info("日报 HTML 已归档：%s", archive_path)
            except Exception as archive_exc:
                logger.warning("日报 HTML 归档失败：%s", archive_exc)
            mark_sent(session, digest_items)
            update_final_summaries(session, digest)
            try:
                retention_days = safe_int(get_settings_map(session).get("retention_days", "30"), 30)
                prune_old_data(session, retention_days, logger=logger)
            except Exception as prune_exc:
                logger.warning("历史数据清理失败:%s", prune_exc.__class__.__name__)

        run.success = True
        run.finished_at = utc_now()
        if persist_items:
            session.add(run)
            session.commit()
        return {"run": run, "subject": subject, "html": html, "items": digest_items}
    except Exception as exc:
        run.success = False
        run.finished_at = utc_now()
        run.error_message = redact_error(exc, settings)
        if persist_items:
            session.add(run)
            session.commit()
        else:
            session.rollback()  # 实时预览:回滚只读事务,坚守 persist_items=False 零副作用契约
        logger.error("运行失败：%s", run.error_message)
        # 仅真实发送失败才告警,dry-run/preview 不打扰;告警任何异常都吞掉,绝不能盖掉原始错误
        if send:
            try:
                _notify_failure(session, runtime_settings, run.error_message, logger)
            except Exception:
                logger.warning("失败告警发送失败,忽略")
        raise


def run_test_email(
    settings,
    session: Session,
    logger: logging.Logger | None = None,
    recipients: list[str] | None = None,
) -> Run:
    logger = logger or logging.getLogger(__name__)
    run = Run(run_type="test", started_at=utc_now())
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        runtime_settings = apply_db_settings(settings, session)
        subject = "ai-football-digest 测试邮件"
        html = "<h1>ai-football-digest 测试邮件</h1><p>这是一封 Web 后台触发的测试邮件。</p>"
        send_email(runtime_settings, subject, html, recipients=recipients or resolve_recipients(session, runtime_settings))
        run.success = True
        run.topic_counts = "{}"
        run.email_subject = subject
        run.html_snapshot = html
        logger.info("测试邮件发送成功")
    except Exception as exc:
        run.success = False
        run.error_message = redact_error(exc, settings)
        logger.error("测试邮件发送失败：%s", run.error_message)
    finally:
        run.finished_at = utc_now()
        session.add(run)
        session.commit()
    return run


def apply_db_settings(settings, session: Session):
    values = get_settings_map(session)
    updates = {}
    if values.get("mail_to"):
        updates["mail_to"] = values["mail_to"]
    if values.get("summary_style"):
        updates["summary_style"] = values["summary_style"]
    return replace(settings, **updates)


def resolve_recipients(session: Session, settings) -> list[str]:
    rows = session.exec(
        select(EmailRecipient)
        .where(EmailRecipient.enabled == True)  # noqa: E712
        .order_by(EmailRecipient.is_default.desc(), EmailRecipient.email)
    ).all()
    db_recipients = [row.email.strip() for row in rows if row.email.strip()]
    if db_recipients:
        return db_recipients
    return parse_recipients(settings.mail_to)


def _notify_failure(session: Session, settings, error_message: str, logger: logging.Logger) -> None:
    """日报真实发送失败时,给管理员发一封极简纯文本告警邮件。

    error_message 已在调用处经过 redact_error 脱敏,可安全放入正文。
    收件人复用 resolve_recipients(优先 DB 启用收件人,其次 .env mail_to)。
    本函数的任何异常由调用方吞掉,绝不能掩盖原始运行错误。
    """
    recipients = resolve_recipients(session, settings)
    subject = "【日报告警】今日生成失败"
    safe_message = html_escape(error_message or "未知错误")
    body = (
        "<h2>ai-football-digest 日报生成失败</h2>"
        f"<p>错误信息:</p><pre>{safe_message}</pre>"
        "<p>请登录 Web 后台查看运行记录与日志,或手动「立即发送」补发。</p>"
    )
    send_email(settings, subject, body, recipients=recipients)
    logger.info("已发送失败告警邮件给 %s 位收件人", len(recipients))


def get_enabled_topics(session: Session) -> list[Topic]:
    return session.exec(
        select(Topic).where(Topic.enabled == True).order_by(Topic.priority.desc(), Topic.name)  # noqa: E712
    ).all()


def group_by_topic_id(items: list) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for item in items:
        topic_id = getattr(item, "topic_id", None)
        if topic_id is None:
            continue
        grouped.setdefault(topic_id, []).append(item)
    return grouped


def score_and_store_items(session: Session, items: list[dict], topics: list[Topic], keywords_by_topic: dict[int, list], sources: list, persist: bool = True) -> list[dict]:
    topics_by_id = {topic.id: topic for topic in topics}
    scored_items = []
    for item in items:
        topic = topics_by_id.get(item.get("topic_id"))
        if not topic:
            continue
        score = calculate_score(item, topic, keywords_by_topic.get(topic.id, []), sources)
        if score is None or score < topic.min_score:
            continue
        copied = dict(item)
        copied["score"] = score
        copied["topic_name"] = topic.name
        copied["topic_slug"] = topic.slug
        if persist:
            session.add(
                NewsItem(
                    topic_id=topic.id,
                    url=str(copied.get("url", "")),
                    title=str(copied.get("title", "")),
                    source=str(copied.get("source", "")),
                    domain=str(copied.get("domain", "")),
                    published_at=str(copied.get("published_at", "")),
                    raw_summary=str(copied.get("content", "")),
                    score=float(score),
                )
            )
        scored_items.append(copied)
    if persist:
        session.commit()
    return scored_items


def select_items(items: list[dict], topics: list[Topic]) -> list[dict]:
    selected = []
    for topic in topics:
        topic_items = [item for item in items if item.get("topic_id") == topic.id]
        selected.extend(sorted(topic_items, key=lambda item: float(item.get("score", 0)), reverse=True)[: topic.daily_limit])
    return selected


def to_digest_item(item: dict) -> dict:
    return dict(item)


def update_final_summaries(session: Session, digest: dict) -> None:
    summary_by_url = {}
    for section in digest.get("sections", []):
        for item in section.get("news_items", []):
            summary_by_url[item.get("url")] = item.get("summary", "")
    if not summary_by_url:
        return
    rows = session.exec(select(NewsItem).where(NewsItem.url.in_(list(summary_by_url.keys())))).all()
    for row in rows:
        row.final_summary = summary_by_url.get(row.url, "")
        session.add(row)
    session.commit()


def build_topic_counts(items: list[dict], topics: list[Topic]) -> dict[str, int]:
    return {
        topic.name: sum(1 for item in items if item.get("topic_id") == topic.id)
        for topic in topics
    }


def redact_error(exc: Exception, settings) -> str:
    message = str(exc) or exc.__class__.__name__
    secrets = [
        getattr(settings, "tavily_api_key", ""),
        getattr(settings, "openai_api_key", ""),
        getattr(settings, "llm_api_key", ""),
        getattr(settings, "smtp_pass", ""),
        getattr(settings, "admin_password", ""),
    ]
    secrets.extend(getattr(settings, "tavily_api_keys", ()) or ())
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message


def safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def str_to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def update_normal_settings(session: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        set_setting(session, key, value)
    session.commit()
