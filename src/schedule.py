from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from src.database import get_settings_map
from src.models import Run

logger = logging.getLogger(__name__)


def parse_send_time(raw: str) -> tuple[int, int]:
    """解析 "HH:MM" 为 (hour, minute);非法输入回退到 06:00。"""
    try:
        hour_str, minute_str = (raw or "").strip().split(":", 1)
        hour, minute = int(hour_str), int(minute_str)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    return 6, 0


def should_send_now(
    session: Session,
    settings,
    now_utc: datetime,
    window_minutes: int = 59,
) -> tuple[bool, str]:
    """守卫式调度:判断当前 cron 运行是否应真实发送日报。

    设计前提:cron 按 ``0 * * * *`` 每整点运行,本函数决定是否真正发送。
    返回 ``(是否发送, 跳过原因)``;放行时原因为空串。

    判定(全部在 ``settings.timezone`` 本地时区下进行,对齐 ``make_subject``):
    - 读 DB settings 的 ``send_time``(HH:MM),换成本地当天的目标时刻 ``target``。
    - 本地当前时刻 < ``target`` → 跳过(未到发送时间)。
    - 本地当前时刻 > ``target + window_minutes`` → 跳过(已错过当日发送窗口)。
    - 本地当天已存在 ``run_type='send'`` 且 ``success=True`` 的 Run → 跳过(今日已发送)。
    - 否则放行。

    ``window_minutes`` 默认 59:配合每小时一次的 cron,使 target 之后的首个整点
    tick 必落入 ``[target, target+59]`` 窗口、且仅有该 tick 落入,从而"到点后当天
    首次运行才发送、且当天仅发一次"。手动「立即发送」会写入一条 ``run_type='send'``
    成功 Run,因此也会被"今日已发送"识别,避免 cron 重复发。
    """
    tz = ZoneInfo(settings.timezone)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(tz)

    hour, minute = parse_send_time(get_settings_map(session).get("send_time", "06:00"))
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if local_now < target:
        return False, "未到发送时间"
    if local_now > target + timedelta(minutes=window_minutes):
        return False, "已错过当日发送窗口"

    # 本地当天 [00:00, 次日00:00) 对应的 UTC 区间,用于查"今日已发送"
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_local.astimezone(timezone.utc)
    day_end_utc = (day_start_local + timedelta(days=1)).astimezone(timezone.utc)

    already_sent = session.exec(
        select(Run).where(
            Run.run_type == "send",
            Run.success == True,  # noqa: E712
            Run.started_at >= day_start_utc,
            Run.started_at < day_end_utc,
        )
    ).first()
    if already_sent is not None:
        return False, "今日已发送"

    return True, ""
