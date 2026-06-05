from __future__ import annotations

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo


CN_NUMBERS = "一二三四五六七八九十"


def make_subject(timezone_name: str = "Asia/Shanghai", current_date: date | None = None, topic_count: int = 0) -> str:
    digest_date = current_date or datetime.now(ZoneInfo(timezone_name)).date()
    count_text = f"今日 {topic_count} 个主题更新 - " if topic_count else ""
    return f"【每日简报】{count_text}{digest_date.isoformat()}"


def render_email(digest: dict, subject: str, generated_at: datetime | None = None) -> str:
    generated = generated_at or datetime.now()
    generated_label = generated.strftime("%Y-%m-%d %H:%M")

    overview_html = _render_overview_section("总体摘要看点", digest.get("overview", []))
    sections_html = "\n".join(
        _render_topic_section(index + 1, section)
        for index, section in enumerate(digest.get("sections", []))
    )
    top_html = _render_top_section("今日最值得关注的 5 条", digest.get("top5", []))

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(subject)}</title>
  <style>
    body {{ margin: 0; padding: 0; background: #f5f7fb; color: #1f2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .wrap {{ width: 100%; padding: 18px 0; }}
    .container {{ max-width: 720px; margin: 0 auto; padding: 0 14px; }}
    .header {{ padding: 18px 16px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; }}
    h1 {{ margin: 0 0 8px; font-size: 22px; line-height: 1.35; color: #111827; }}
    .meta {{ margin: 0; font-size: 13px; color: #6b7280; }}
    .section {{ margin-top: 14px; padding: 16px; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; line-height: 1.4; color: #111827; }}
    .topic-summary {{ margin: 0 0 12px; font-size: 14px; line-height: 1.7; color: #4b5563; }}
    .item {{ padding: 14px 0; border-top: 1px solid #eef2f7; }}
    .item:first-of-type {{ border-top: 0; padding-top: 0; }}
    .title {{ margin: 0 0 6px; font-size: 16px; line-height: 1.45; font-weight: 700; }}
    .title a {{ color: #0f766e; text-decoration: none; }}
    .byline {{ margin: 0 0 8px; font-size: 12px; color: #6b7280; }}
    .summary {{ margin: 0 0 10px; font-size: 14px; line-height: 1.7; color: #374151; white-space: pre-line; }}
    .read {{ font-size: 13px; color: #2563eb; text-decoration: none; }}
    .empty {{ margin: 0; font-size: 14px; color: #6b7280; }}
    .overview-list {{ margin: 0; padding-left: 20px; }}
    .overview-list li {{ margin: 0 0 8px; font-size: 14px; line-height: 1.7; color: #374151; }}
    ol {{ margin: 0; padding-left: 22px; }}
    li {{ margin: 0 0 12px; padding-left: 2px; }}
    li:last-child {{ margin-bottom: 0; }}
    .top-title {{ font-size: 15px; font-weight: 700; line-height: 1.5; }}
    .top-title a {{ color: #0f766e; text-decoration: none; }}
    .topic-label {{ color: #6b7280; font-size: 12px; margin-right: 4px; }}
    .reason {{ margin: 4px 0 0; font-size: 13px; line-height: 1.6; color: #4b5563; }}
    .footer {{ padding: 14px 4px 0; font-size: 12px; color: #9ca3af; text-align: center; }}
    @media (max-width: 520px) {{
      .wrap {{ padding: 8px 0; }}
      .container {{ padding: 0 8px; }}
      .header, .section {{ padding: 14px; }}
      h1 {{ font-size: 19px; }}
      h2 {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="container">
      <div class="header">
        <h1>{escape(subject)}</h1>
        <p class="meta">生成时间：{escape(generated_label)}</p>
      </div>
      {overview_html}
      {sections_html}
      {top_html}
      <p class="footer">本邮件由 ai-football-digest 自动生成。</p>
    </div>
  </div>
</body>
</html>"""


def _render_overview_section(title: str, items: list[str]) -> str:
    cleaned_items = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned_items:
        body = '<p class="empty">暂无总体看点。</p>'
    else:
        body = '<ul class="overview-list">' + "\n".join(
            f"<li>{escape(item)}</li>" for item in cleaned_items[:5]
        ) + "</ul>"

    return f"""
      <section class="section">
        <h2>{escape(title)}</h2>
        {body}
      </section>"""


def _render_topic_section(index: int, section: dict) -> str:
    title = f"{_cn_index(index)}、{section.get('topic', '主题')} 今日重点"
    items = section.get("news_items", [])
    summary = escape(str(section.get("topic_summary", "") or "今日暂无高质量更新。"))
    if not items:
        body = '<p class="empty">今日暂无高质量更新。</p>'
    else:
        body = "\n".join(_render_news_item(item) for item in items)

    return f"""
      <section class="section">
        <h2>{escape(title)}</h2>
        <p class="topic-summary">{summary}</p>
        {body}
      </section>"""


def _render_news_item(item: dict) -> str:
    title = escape(str(item.get("title", "")))
    source = escape(str(item.get("source", "") or "未知来源"))
    published_at = escape(str(item.get("published_at", "") or "时间未知"))
    summary = escape(str(item.get("summary", "") or "原始信息未说明具体细节。"))
    url = escape(str(item.get("url", "")), quote=True)

    return f"""
        <article class="item">
          <p class="title"><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></p>
          <p class="byline">{source} · {published_at}</p>
          <p class="summary">{summary}</p>
          <a class="read" href="{url}" target="_blank" rel="noopener noreferrer">阅读原文</a>
        </article>"""


def _render_top_section(title: str, items: list[dict]) -> str:
    if not items:
        body = '<p class="empty">暂无可推荐的重点新闻。</p>'
    else:
        body = "<ol>" + "\n".join(_render_top_item(item) for item in items[:5]) + "</ol>"

    return f"""
      <section class="section">
        <h2>{escape(title)}</h2>
        {body}
      </section>"""


def _render_top_item(item: dict) -> str:
    topic = escape(str(item.get("topic", "")))
    title = escape(str(item.get("title", "")))
    reason = escape(str(item.get("reason", "") or "建议关注。"))
    url = escape(str(item.get("url", "")), quote=True)

    return f"""
          <li>
            <div class="top-title"><span class="topic-label">{topic}</span><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a></div>
            <p class="reason">{reason}</p>
          </li>"""


def _cn_index(index: int) -> str:
    if 1 <= index <= len(CN_NUMBERS):
        return CN_NUMBERS[index - 1]
    return str(index)
