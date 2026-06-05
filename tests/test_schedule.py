from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.database import get_session, init_db, set_setting
from src.models import Run
from src.schedule import parse_send_time, should_send_now


SH = SimpleNamespace(timezone="Asia/Shanghai")


def _settings(tz="Asia/Shanghai"):
    return SimpleNamespace(timezone=tz)


def _set_send_time(session, value):
    set_setting(session, "send_time", value)
    session.commit()


def test_parse_send_time_valid_and_fallback():
    assert parse_send_time("06:30") == (6, 30)
    assert parse_send_time("23:59") == (23, 59)
    assert parse_send_time("") == (6, 0)
    assert parse_send_time("nonsense") == (6, 0)
    assert parse_send_time("25:00") == (6, 0)


def test_skip_before_send_time(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 上海 05:00 = UTC 前一天 21:00,未到 06:00
        now_utc = datetime(2026, 6, 4, 21, 0, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is False
        assert reason == "未到发送时间"


def test_allow_at_send_time(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 上海 06:00 = UTC 前一天 22:00,恰好到点
        now_utc = datetime(2026, 6, 4, 22, 0, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is True
        assert reason == ""


def test_skip_after_window(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 上海 07:00(target+60min)= UTC 前一天 23:00,已错过 59 分钟窗口
        now_utc = datetime(2026, 6, 4, 23, 0, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is False
        assert reason == "已错过当日发送窗口"


def test_allow_at_window_boundary(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 上海 06:59(恰好 target+59min,窗口末端)= UTC 前一天 22:59,应放行
        now_utc = datetime(2026, 6, 4, 22, 59, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is True
        assert reason == ""


def test_skip_when_already_sent_today(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 当天上海 06:30 已有一条成功 send(UTC 前一天 22:30)
        sent_at = datetime(2026, 6, 4, 22, 30, tzinfo=timezone.utc)
        session.add(Run(run_type="send", success=True, started_at=sent_at))
        session.commit()
        # 同一上海日的 06:40 再次检查 → 今日已发送
        now_utc = datetime(2026, 6, 4, 22, 40, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is False
        assert reason == "今日已发送"


def test_failed_send_does_not_block(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 当天有一条失败的 send,不应算作"今日已发送"
        sent_at = datetime(2026, 6, 4, 22, 10, tzinfo=timezone.utc)
        session.add(Run(run_type="send", success=False, started_at=sent_at))
        session.commit()
        now_utc = datetime(2026, 6, 4, 22, 20, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is True
        assert reason == ""


def test_yesterday_send_does_not_block_today(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # 昨天上海日的成功 send 不应阻止今天
        yesterday = datetime(2026, 6, 3, 22, 0, tzinfo=timezone.utc)
        session.add(Run(run_type="send", success=True, started_at=yesterday))
        session.commit()
        now_utc = datetime(2026, 6, 4, 22, 0, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(), now_utc)
        assert ok is True
        assert reason == ""


def test_timezone_conversion_utc(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    with get_session(db_path) as session:
        _set_send_time(session, "06:00")
        # UTC 时区下,06:00 UTC 恰好到点
        now_utc = datetime(2026, 6, 4, 6, 0, tzinfo=timezone.utc)
        ok, reason = should_send_now(session, _settings(tz="UTC"), now_utc)
        assert ok is True
        # UTC 05:59 未到点
        now_early = datetime(2026, 6, 4, 5, 59, tzinfo=timezone.utc)
        ok2, reason2 = should_send_now(session, _settings(tz="UTC"), now_early)
        assert ok2 is False
        assert reason2 == "未到发送时间"
