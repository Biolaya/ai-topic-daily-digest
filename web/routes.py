from __future__ import annotations

import logging
import json
from datetime import datetime, time, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from src.config import get_settings
from src.database import get_settings_map, init_db, slugify
from src.emailer import send_email
from src.models import EmailRecipient, Keyword, NewsItem, QueryTemplate, Run, Source, Topic, utc_now
from src.runner import redact_error, resolve_recipients, run_digest, run_test_email, update_normal_settings
from src.scorer import normalize_domain
from src.security import COOKIE_NAME, make_session_token, verify_admin
from src.topic_generator import TopicGenerationRequest, generate_topic_suggestion, save_topic_suggestion, validate_topic_suggestion
from web.deps import AdminDep, SessionDep, redirect
from web.forms import checkbox, clamp_int, clean_text


router = APIRouter()
settings = get_settings()
templates = Jinja2Templates(directory=settings.root_dir / "templates")
logger = logging.getLogger("web")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: Session = SessionDep):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    session: Session = SessionDep,
):
    current_settings = get_settings()
    if not current_settings.admin_password:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "ADMIN_PASSWORD 未配置，拒绝登录。"},
            status_code=400,
        )
    if not verify_admin(username, password, current_settings):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "用户名或密码错误。"},
            status_code=401,
        )
    response = redirect("/")
    response.set_cookie(COOKIE_NAME, make_session_token(current_settings), httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout(admin=AdminDep):
    response = redirect("/login")
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = SessionDep, admin=AdminDep):
    latest_run = session.exec(select(Run).order_by(Run.started_at.desc())).first()
    recent_runs = session.exec(select(Run).order_by(Run.started_at.desc()).limit(7)).all()
    enabled_topic_count = session.exec(
        select(func.count(Topic.id)).where(Topic.enabled == True)  # noqa: E712
    ).one()
    total_keyword_count = session.exec(select(func.count(Keyword.id))).one()
    enabled_recipient_count = session.exec(
        select(func.count(EmailRecipient.id)).where(EmailRecipient.enabled == True)  # noqa: E712
    ).one()
    topic_counts = get_today_topic_counts(session)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "latest_run": latest_run,
            "recent_runs": recent_runs,
            "enabled_topic_count": enabled_topic_count,
            "total_keyword_count": total_keyword_count,
            "enabled_recipient_count": enabled_recipient_count,
            "topic_counts": topic_counts,
        },
    )


@router.get("/topics", response_class=HTMLResponse)
def topics_page(request: Request, edit_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    topics = session.exec(select(Topic).order_by(Topic.priority.desc(), Topic.name)).all()
    edit_topic = session.get(Topic, edit_id) if edit_id else None
    return templates.TemplateResponse(
        request,
        "topics.html",
        {"topics": topics, "edit_topic": edit_topic},
    )


@router.post("/topics/save")
def save_topic(
    id: int | None = Form(None),
    name: str = Form(""),
    slug: str = Form(""),
    description: str = Form(""),
    language: str = Form("mixed"),
    daily_limit: str = Form("8"),
    min_score: str = Form("40"),
    priority: str = Form("5"),
    summary_style: str = Form("concise"),
    enabled: str | None = Form(None),
    session: Session = SessionDep,
    admin=AdminDep,
):
    topic = session.get(Topic, id) if id else Topic()
    if topic is None:
        raise HTTPException(status_code=404, detail="主题不存在")
    if not clean_text(name):
        raise HTTPException(status_code=400, detail="主题名称不能为空")
    topic.name = clean_text(name)
    topic.slug = unique_slug(session, clean_text(slug) or slugify(topic.name), current_id=id)
    topic.description = clean_text(description)
    topic.language = language if language in {"zh", "en", "mixed"} else "mixed"
    topic.daily_limit = clamp_int(daily_limit, 8, 1, 20)
    topic.min_score = clamp_int(min_score, 40, 0, 100)
    topic.priority = clamp_int(priority, 5, 0, 100)
    topic.summary_style = clean_text(summary_style) or "concise"
    topic.enabled = checkbox(enabled)
    topic.updated_at = utc_now()
    session.add(topic)
    session.commit()
    return redirect("/topics")


@router.post("/topics/{topic_id}/toggle")
def toggle_topic(topic_id: int, session: Session = SessionDep, admin=AdminDep):
    topic = session.get(Topic, topic_id)
    if topic:
        topic.enabled = not topic.enabled
        topic.updated_at = utc_now()
        session.add(topic)
        session.commit()
    return redirect("/topics")


@router.post("/topics/{topic_id}/delete")
def delete_topic(topic_id: int, session: Session = SessionDep, admin=AdminDep):
    topic = session.get(Topic, topic_id)
    if topic:
        topic.enabled = False
        topic.updated_at = utc_now()
        topic.description = (topic.description + "\n已软删除：关联数据保留。").strip()
        session.add(topic)
        session.commit()
    return redirect("/topics")


@router.get("/topics/generate", response_class=HTMLResponse)
def topic_generate_page(request: Request, admin=AdminDep):
    return templates.TemplateResponse(
        request,
        "topic_generate.html",
        {"error": "", "form": default_topic_generate_form()},
    )


@router.post("/topics/generate/preview", response_class=HTMLResponse)
def topic_generate_preview(
    request: Request,
    interest_description: str = Form(""),
    preferred_language: str = Form("mixed"),
    daily_limit: str = Form("8"),
    min_score: str = Form("45"),
    exclude_entertainment: str | None = Form(None),
    exclude_sports: str | None = Form(None),
    include_zh_sources: str | None = Form(None),
    include_en_sources: str | None = Form(None),
    session: Session = SessionDep,
    admin=AdminDep,
):
    form = {
        "interest_description": clean_text(interest_description),
        "preferred_language": preferred_language if preferred_language in {"zh", "en", "mixed"} else "mixed",
        "daily_limit": clamp_int(daily_limit, 8, 1, 20),
        "min_score": clamp_int(min_score, 45, 0, 100),
        "exclude_entertainment": checkbox(exclude_entertainment),
        "exclude_sports": checkbox(exclude_sports),
        "include_zh_sources": checkbox(include_zh_sources),
        "include_en_sources": checkbox(include_en_sources),
    }
    if not form["interest_description"]:
        return templates.TemplateResponse(
            request,
            "topic_generate.html",
            {"error": "兴趣描述不能为空。", "form": form},
            status_code=400,
        )

    try:
        suggestion = generate_topic_suggestion(get_settings(), TopicGenerationRequest(**form))
        existing = session.exec(select(Topic).where(Topic.slug == suggestion["topic"]["slug"])).first()
        return templates.TemplateResponse(
            request,
            "topic_generate_preview.html",
            {
                "suggestion": suggestion,
                "payload": json.dumps(suggestion, ensure_ascii=False),
                "existing_topic": existing,
                "error": "",
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "topic_generate.html",
            {"error": redact_error(exc, get_settings()), "form": form},
            status_code=500,
        )


@router.post("/topics/generate/confirm")
def topic_generate_confirm(
    payload: str = Form(""),
    merge_existing: str | None = Form(None),
    session: Session = SessionDep,
    admin=AdminDep,
):
    try:
        suggestion = validate_topic_suggestion(json.loads(payload))
        topic = save_topic_suggestion(session, suggestion, merge_existing=checkbox(merge_existing))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=redact_error(exc, get_settings())) from exc
    return redirect(f"/topics/{topic.id}/keywords")


@router.get("/topics/{topic_id}/keywords", response_class=HTMLResponse)
def topic_keywords_page(request: Request, topic_id: int, edit_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    topic = get_topic_or_404(session, topic_id)
    keywords = session.exec(select(Keyword).where(Keyword.topic_id == topic.id).order_by(Keyword.weight.desc(), Keyword.word)).all()
    edit_keyword = session.get(Keyword, edit_id) if edit_id else None
    if edit_keyword and edit_keyword.topic_id != topic.id:
        edit_keyword = None
    return templates.TemplateResponse(
        request,
        "topic_keywords.html",
        {"topic": topic, "keywords": keywords, "edit_keyword": edit_keyword},
    )


@router.post("/topics/{topic_id}/keywords/save")
def save_topic_keyword(
    topic_id: int,
    id: int | None = Form(None),
    word: str = Form(""),
    language: str = Form("mixed"),
    weight: str = Form("5"),
    match_type: str = Form("contains"),
    enabled: str | None = Form(None),
    note: str = Form(""),
    session: Session = SessionDep,
    admin=AdminDep,
):
    topic = get_topic_or_404(session, topic_id)
    if id:
        keyword = session.get(Keyword, id)
        if not keyword or keyword.topic_id != topic.id:
            raise HTTPException(status_code=404, detail="关键词不存在")
    else:
        keyword = Keyword(topic_id=topic.id)
    if not clean_text(word):
        raise HTTPException(status_code=400, detail="关键词不能为空")
    if keyword.topic_id not in {None, topic.id}:
        raise HTTPException(status_code=404, detail="关键词不存在")
    keyword.topic_id = topic.id
    keyword.word = clean_text(word)
    keyword.language = language if language in {"zh", "en", "mixed"} else "mixed"
    keyword.weight = clamp_int(weight, 5, 1, 10)
    keyword.match_type = match_type if match_type in {"contains", "exact"} else "contains"
    keyword.enabled = checkbox(enabled)
    keyword.note = clean_text(note)
    keyword.updated_at = utc_now()
    session.add(keyword)
    session.commit()
    return redirect(f"/topics/{topic.id}/keywords")


@router.post("/topics/{topic_id}/keywords/{keyword_id}/toggle")
def toggle_topic_keyword(topic_id: int, keyword_id: int, session: Session = SessionDep, admin=AdminDep):
    keyword = session.get(Keyword, keyword_id)
    if keyword and keyword.topic_id == topic_id:
        keyword.enabled = not keyword.enabled
        keyword.updated_at = utc_now()
        session.add(keyword)
        session.commit()
    return redirect(f"/topics/{topic_id}/keywords")


@router.post("/topics/{topic_id}/keywords/{keyword_id}/delete")
def delete_topic_keyword(topic_id: int, keyword_id: int, session: Session = SessionDep, admin=AdminDep):
    keyword = session.get(Keyword, keyword_id)
    if keyword and keyword.topic_id == topic_id:
        session.delete(keyword)
        session.commit()
    return redirect(f"/topics/{topic_id}/keywords")


@router.get("/topics/{topic_id}/sources", response_class=HTMLResponse)
def topic_sources_page(request: Request, topic_id: int, edit_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    topic = get_topic_or_404(session, topic_id)
    sources = session.exec(select(Source).where(Source.topic_id == topic.id).order_by(Source.base_score.desc(), Source.domain)).all()
    edit_source = session.get(Source, edit_id) if edit_id else None
    if edit_source and edit_source.topic_id != topic.id:
        edit_source = None
    return templates.TemplateResponse(
        request,
        "topic_sources.html",
        {"topic": topic, "sources": sources, "edit_source": edit_source},
    )


@router.post("/topics/{topic_id}/sources/save")
def save_topic_source(
    topic_id: int,
    id: int | None = Form(None),
    domain: str = Form(""),
    name: str = Form(""),
    base_score: str = Form("45"),
    enabled: str | None = Form(None),
    note: str = Form(""),
    session: Session = SessionDep,
    admin=AdminDep,
):
    topic = get_topic_or_404(session, topic_id)
    if id:
        source = session.get(Source, id)
        if not source or source.topic_id != topic.id:
            raise HTTPException(status_code=404, detail="来源不存在")
    else:
        source = Source(topic_id=topic.id)
    if not clean_text(domain):
        raise HTTPException(status_code=400, detail="域名不能为空")
    source.topic_id = topic.id
    source.domain = normalize_domain(clean_text(domain))
    source.name = clean_text(name)
    source.base_score = clamp_int(base_score, 45, 0, 100)
    source.enabled = checkbox(enabled)
    source.note = clean_text(note)
    source.updated_at = utc_now()
    session.add(source)
    session.commit()
    return redirect(f"/topics/{topic.id}/sources")


@router.post("/topics/{topic_id}/sources/{source_id}/toggle")
def toggle_topic_source(topic_id: int, source_id: int, session: Session = SessionDep, admin=AdminDep):
    source = session.get(Source, source_id)
    if source and source.topic_id == topic_id:
        source.enabled = not source.enabled
        source.updated_at = utc_now()
        session.add(source)
        session.commit()
    return redirect(f"/topics/{topic_id}/sources")


@router.post("/topics/{topic_id}/sources/{source_id}/delete")
def delete_topic_source(topic_id: int, source_id: int, session: Session = SessionDep, admin=AdminDep):
    source = session.get(Source, source_id)
    if source and source.topic_id == topic_id:
        session.delete(source)
        session.commit()
    return redirect(f"/topics/{topic_id}/sources")


@router.get("/topics/{topic_id}/queries", response_class=HTMLResponse)
def topic_queries_page(request: Request, topic_id: int, edit_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    topic = get_topic_or_404(session, topic_id)
    queries = session.exec(select(QueryTemplate).where(QueryTemplate.topic_id == topic.id).order_by(QueryTemplate.priority.desc())).all()
    edit_query = session.get(QueryTemplate, edit_id) if edit_id else None
    if edit_query and edit_query.topic_id != topic.id:
        edit_query = None
    return templates.TemplateResponse(
        request,
        "topic_queries.html",
        {"topic": topic, "queries": queries, "edit_query": edit_query},
    )


@router.post("/topics/{topic_id}/queries/save")
def save_topic_query(
    topic_id: int,
    id: int | None = Form(None),
    query_text: str = Form(""),
    language: str = Form("mixed"),
    priority: str = Form("5"),
    enabled: str | None = Form(None),
    session: Session = SessionDep,
    admin=AdminDep,
):
    topic = get_topic_or_404(session, topic_id)
    if id:
        query = session.get(QueryTemplate, id)
        if not query or query.topic_id != topic.id:
            raise HTTPException(status_code=404, detail="搜索模板不存在")
    else:
        query = QueryTemplate(topic_id=topic.id, query_text="")
    if not clean_text(query_text):
        raise HTTPException(status_code=400, detail="搜索模板不能为空")
    query.topic_id = topic.id
    query.query_text = clean_text(query_text)
    query.language = language if language in {"zh", "en", "mixed"} else "mixed"
    query.priority = clamp_int(priority, 5, 0, 100)
    query.enabled = checkbox(enabled)
    query.updated_at = utc_now()
    session.add(query)
    session.commit()
    return redirect(f"/topics/{topic.id}/queries")


@router.post("/topics/{topic_id}/queries/{query_id}/toggle")
def toggle_topic_query(topic_id: int, query_id: int, session: Session = SessionDep, admin=AdminDep):
    query = session.get(QueryTemplate, query_id)
    if query and query.topic_id == topic_id:
        query.enabled = not query.enabled
        query.updated_at = utc_now()
        session.add(query)
        session.commit()
    return redirect(f"/topics/{topic_id}/queries")


@router.post("/topics/{topic_id}/queries/{query_id}/delete")
def delete_topic_query(topic_id: int, query_id: int, session: Session = SessionDep, admin=AdminDep):
    query = session.get(QueryTemplate, query_id)
    if query and query.topic_id == topic_id:
        session.delete(query)
        session.commit()
    return redirect(f"/topics/{topic_id}/queries")


@router.get("/keywords")
def old_keywords_redirect(admin=AdminDep):
    return redirect("/topics")


@router.get("/recipients", response_class=HTMLResponse)
def recipients_page(request: Request, edit_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    recipients = session.exec(select(EmailRecipient).order_by(EmailRecipient.is_default.desc(), EmailRecipient.email)).all()
    edit_recipient = session.get(EmailRecipient, edit_id) if edit_id else None
    return templates.TemplateResponse(
        request,
        "recipients.html",
        {"recipients": recipients, "edit_recipient": edit_recipient},
    )


@router.post("/recipients/save")
def save_recipient(
    id: int | None = Form(None),
    email: str = Form(""),
    name: str = Form(""),
    enabled: str | None = Form(None),
    is_default: str | None = Form(None),
    note: str = Form(""),
    session: Session = SessionDep,
    admin=AdminDep,
):
    recipient = session.get(EmailRecipient, id) if id else EmailRecipient()
    if recipient is None:
        raise HTTPException(status_code=404, detail="收件人不存在")
    email_value = clean_text(email)
    if not is_valid_email(email_value):
        raise HTTPException(status_code=400, detail="邮箱地址格式不正确")
    recipient.email = email_value
    recipient.name = clean_text(name)
    recipient.enabled = checkbox(enabled)
    recipient.is_default = checkbox(is_default)
    recipient.note = clean_text(note)
    recipient.updated_at = utc_now()
    session.add(recipient)
    session.commit()
    return redirect("/recipients")


@router.post("/recipients/{recipient_id}/toggle")
def toggle_recipient(recipient_id: int, session: Session = SessionDep, admin=AdminDep):
    recipient = session.get(EmailRecipient, recipient_id)
    if recipient:
        recipient.enabled = not recipient.enabled
        recipient.updated_at = utc_now()
        session.add(recipient)
        session.commit()
    return redirect("/recipients")


@router.post("/recipients/{recipient_id}/delete")
def delete_recipient(recipient_id: int, session: Session = SessionDep, admin=AdminDep):
    recipient = session.get(EmailRecipient, recipient_id)
    if recipient:
        session.delete(recipient)
        session.commit()
    return redirect("/recipients")


@router.post("/recipients/{recipient_id}/send-test")
def send_recipient_test(recipient_id: int, background_tasks: BackgroundTasks, session: Session = SessionDep, admin=AdminDep):
    recipient = session.get(EmailRecipient, recipient_id)
    if not recipient:
        raise HTTPException(status_code=404, detail="收件人不存在")
    background_tasks.add_task(_send_recipient_test_task, recipient.email)
    return redirect("/runs")


@router.get("/sources", response_class=HTMLResponse)
def global_sources_page(request: Request, edit_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    sources = session.exec(select(Source).where(Source.topic_id == None).order_by(Source.base_score.desc(), Source.domain)).all()  # noqa: E711
    edit_source = session.get(Source, edit_id) if edit_id else None
    if edit_source and edit_source.topic_id is not None:
        edit_source = None
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"sources": sources, "edit_source": edit_source},
    )


@router.post("/sources/save")
def save_global_source(
    id: int | None = Form(None),
    domain: str = Form(""),
    name: str = Form(""),
    base_score: str = Form("45"),
    enabled: str | None = Form(None),
    note: str = Form(""),
    session: Session = SessionDep,
    admin=AdminDep,
):
    if id:
        source = session.get(Source, id)
        if not source or source.topic_id is not None:
            raise HTTPException(status_code=404, detail="来源不存在")
    else:
        source = Source(topic_id=None)
    if not clean_text(domain):
        raise HTTPException(status_code=400, detail="域名不能为空")
    source.topic_id = None
    source.domain = normalize_domain(clean_text(domain))
    source.name = clean_text(name)
    source.base_score = clamp_int(base_score, 45, 0, 100)
    source.enabled = checkbox(enabled)
    source.note = clean_text(note)
    source.updated_at = utc_now()
    session.add(source)
    session.commit()
    return redirect("/sources")


@router.post("/sources/{source_id}/toggle")
def toggle_global_source(source_id: int, session: Session = SessionDep, admin=AdminDep):
    source = session.get(Source, source_id)
    if source and source.topic_id is None:
        source.enabled = not source.enabled
        source.updated_at = utc_now()
        session.add(source)
        session.commit()
    return redirect("/sources")


@router.post("/sources/{source_id}/delete")
def delete_global_source(source_id: int, session: Session = SessionDep, admin=AdminDep):
    source = session.get(Source, source_id)
    if source and source.topic_id is None:
        session.delete(source)
        session.commit()
    return redirect("/sources")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: Session = SessionDep, admin=AdminDep):
    values = get_settings_map(session)
    return templates.TemplateResponse(request, "settings.html", {"values": values})


@router.post("/settings")
def save_settings(
    mail_to: str = Form(""),
    send_time: str = Form("06:00"),
    summary_style: str = Form(""),
    auto_send_enabled: str | None = Form(None),
    retention_days: str = Form("30"),
    session: Session = SessionDep,
    admin=AdminDep,
):
    update_normal_settings(
        session,
        {
            "mail_to": clean_text(mail_to),
            "send_time": clean_text(send_time) or "06:00",
            "summary_style": clean_text(summary_style) or "清晰、具体、简洁",
            "auto_send_enabled": "true" if checkbox(auto_send_enabled) else "false",
            "retention_days": str(clamp_int(retention_days, 30, 0, 3650)),
        },
    )
    return redirect("/settings")


@router.get("/preview", response_class=HTMLResponse)
def preview(request: Request, live: int = 0, session: Session = SessionDep, admin=AdminDep):
    # 默认:展示最近一封真实发送日报的 HTML 快照,零 API 成本、不落库。
    if not live:
        last_run = session.exec(
            select(Run)
            .where(Run.run_type == "send", Run.html_snapshot != "")
            .order_by(Run.started_at.desc())
        ).first()
        if last_run:
            return templates.TemplateResponse(
                request,
                "preview.html",
                {
                    "subject": last_run.email_subject,
                    "html": last_run.html_snapshot,
                    "error": "",
                    "mode": "snapshot",
                    "snapshot_time": last_run.started_at,
                },
            )
        return templates.TemplateResponse(
            request,
            "preview.html",
            {
                "subject": "",
                "html": "",
                "error": "",
                "mode": "empty",
                "snapshot_time": None,
            },
        )

    # ?live=1:实时抓取生成预览,persist_items=False 保证零 DB 写入(不建 Run、不写 news_items)。
    try:
        result = run_digest(
            get_settings(), session, run_type="dry-run", send=False, persist_items=False, logger=logger
        )
        return templates.TemplateResponse(
            request,
            "preview.html",
            {"subject": result["subject"], "html": result["html"], "error": "", "mode": "live", "snapshot_time": None},
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "preview.html",
            {"subject": "", "html": "", "error": redact_error(exc, get_settings()), "mode": "live", "snapshot_time": None},
            status_code=500,
        )


@router.post("/send-test")
def send_test(background_tasks: BackgroundTasks, admin=AdminDep):
    background_tasks.add_task(_send_test_task)
    return redirect("/runs")


@router.post("/send-now")
def send_now(background_tasks: BackgroundTasks, admin=AdminDep):
    background_tasks.add_task(_send_now_task)
    return redirect("/runs")


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, run_id: int | None = None, session: Session = SessionDep, admin=AdminDep):
    runs = session.exec(select(Run).order_by(Run.started_at.desc()).limit(50)).all()
    selected_run = session.get(Run, run_id) if run_id else (runs[0] if runs else None)
    log_tail = read_log_tail()
    return templates.TemplateResponse(
        request,
        "runs.html",
        {"runs": runs, "selected_run": selected_run, "log_tail": log_tail},
    )


@router.post("/runs/{run_id}/resend")
def resend_run(run_id: int, background_tasks: BackgroundTasks, session: Session = SessionDep, admin=AdminDep):
    run = session.get(Run, run_id)
    if not run or not run.html_snapshot:
        raise HTTPException(status_code=404, detail="该运行无可重发的 HTML 快照")
    background_tasks.add_task(
        _resend_task,
        run.email_subject or "ai-football-digest 日报",
        run.html_snapshot,
    )
    return redirect(f"/runs?run_id={run_id}")


def _send_test_task() -> None:
    current_settings = get_settings()
    init_db(current_settings.db_path)
    from src.database import get_session

    with get_session(current_settings.db_path) as session:
        run_test_email(current_settings, session, logger)


def _send_now_task() -> None:
    """手动「立即发送今日日报」:无视 auto_send_enabled 开关,与 cron 发送同类型。

    run_type='send' 使 #1 的「今日已发送」守卫也能识别这封,避免后续 cron 重复发送。
    """
    current_settings = get_settings()
    init_db(current_settings.db_path)
    from src.database import get_session

    with get_session(current_settings.db_path) as session:
        try:
            run_digest(
                current_settings,
                session,
                run_type="send",
                send=True,
                respect_auto_send=False,
                logger=logger,
            )
        except Exception as exc:
            logger.error("手动发送失败:%s", redact_error(exc, current_settings))


def _send_recipient_test_task(email: str) -> None:
    current_settings = get_settings()
    init_db(current_settings.db_path)
    from src.database import get_session

    with get_session(current_settings.db_path) as session:
        run_test_email(current_settings, session, logger, recipients=[email])


def _resend_task(subject: str, html: str) -> None:
    """重发历史日报:纯重发已有 HTML 快照,不调 mark_sent、不写任何库,幂等安全。"""
    current_settings = get_settings()
    init_db(current_settings.db_path)
    from src.database import get_session

    with get_session(current_settings.db_path) as session:
        try:
            recipients = resolve_recipients(session, current_settings)
            send_email(current_settings, f"[重发] {subject}", html, recipients=recipients)
            logger.info("重发成功:%s", subject)
        except Exception as exc:
            logger.error("重发失败:%s", redact_error(exc, current_settings))


def get_topic_or_404(session: Session, topic_id: int) -> Topic:
    topic = session.get(Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="主题不存在")
    return topic


def unique_slug(session: Session, base_slug: str, current_id: int | None = None) -> str:
    base = slugify(base_slug)
    slug = base
    index = 2
    while True:
        existing = session.exec(select(Topic).where(Topic.slug == slug)).first()
        if not existing or existing.id == current_id:
            return slug
        slug = f"{base}-{index}"
        index += 1


def get_today_topic_counts(session: Session) -> list[dict]:
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    topics = session.exec(select(Topic).order_by(Topic.priority.desc(), Topic.name)).all()
    counts = []
    for topic in topics:
        count = session.exec(
            select(func.count(NewsItem.id)).where(
                NewsItem.topic_id == topic.id,
                NewsItem.created_at >= today_start,
            )
        ).one()
        counts.append({"topic": topic.name, "count": count, "enabled": topic.enabled})
    return counts


def read_log_tail(lines: int = 120) -> str:
    path = get_settings().log_path
    if not Path(path).exists():
        return ""
    content = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def default_topic_generate_form() -> dict:
    return {
        "interest_description": "",
        "preferred_language": "mixed",
        "daily_limit": 8,
        "min_score": 45,
        "exclude_entertainment": True,
        "exclude_sports": False,
        "include_zh_sources": True,
        "include_en_sources": True,
    }


def is_valid_email(value: str) -> bool:
    return bool(value and "@" in value and "." in value.rsplit("@", 1)[-1])
