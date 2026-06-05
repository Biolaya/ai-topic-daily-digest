from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from src.models import NewsItem, Run, utc_now


def prune_old_data(
    session: Session,
    retention_days: int,
    now: datetime | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """清理超过保留期的历史数据。

    - 把超期 ``Run`` 的 ``html_snapshot`` 字段置空(保留 run 元数据作审计)。
    - 删除超期的 ``NewsItem`` 行(去重依赖 ``sent_news``,与本表无关,删除安全)。

    绝不触碰 ``sent_news``(去重的唯一数据源)和 run 元数据本身。

    ``retention_days <= 0`` 表示关闭清理,直接返回零计数。
    返回 ``{"runs_cleared": n, "news_deleted": m}``。
    """
    logger = logger or logging.getLogger(__name__)
    if retention_days <= 0:
        return {"runs_cleared": 0, "news_deleted": 0}

    now = now or utc_now()
    cutoff = now - timedelta(days=retention_days)

    runs_cleared = 0
    stale_runs = session.exec(
        select(Run).where(Run.started_at < cutoff, Run.html_snapshot != "")
    ).all()
    for run in stale_runs:
        run.html_snapshot = ""
        session.add(run)
        runs_cleared += 1

    news_deleted = 0
    stale_news = session.exec(select(NewsItem).where(NewsItem.created_at < cutoff)).all()
    for item in stale_news:
        session.delete(item)
        news_deleted += 1

    session.commit()
    logger.info(
        "数据清理完成:清空 %s 条 html_snapshot,删除 %s 条历史新闻(保留 %s 天)",
        runs_cleared,
        news_deleted,
        retention_days,
    )
    return {"runs_cleared": runs_cleared, "news_deleted": news_deleted}
