"""验证 #3:发送失败兜底告警。

关键约束:告警发送本身失败时,绝不能掩盖原始运行错误;dry-run 不触发告警。
"""
from dataclasses import replace

import pytest

from src.config import get_settings
from src.database import get_session, init_db
import src.runner as runner
from src.runner import run_digest


def _fake_settings(db_path):
    return replace(get_settings(), db_path=db_path)


class _BoomError(RuntimeError):
    pass


def test_original_error_raised_even_when_alert_fails(tmp_path, monkeypatch):
    """send=True 时管道抛错 -> 告警被调用;即便告警自身抛错,原始异常仍向上抛出。"""
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    settings = _fake_settings(db_path)

    # 管道一开始就炸
    def boom(*a, **k):
        raise _BoomError("fetch 失败")

    monkeypatch.setattr(runner, "fetch_news", boom)

    # 告警自身也炸 —— 必须被吞掉,不能盖住原始 _BoomError
    notify_called = {"n": 0}

    def failing_notify(*a, **k):
        notify_called["n"] += 1
        raise RuntimeError("告警 SMTP 也挂了")

    monkeypatch.setattr(runner, "_notify_failure", failing_notify)

    with get_session(db_path) as session:
        with pytest.raises(_BoomError):
            run_digest(settings, session, run_type="send", send=True)

    assert notify_called["n"] == 1, "send=True 失败时应调用一次告警"


def test_dry_run_failure_does_not_alert(tmp_path, monkeypatch):
    """send=False(dry-run/preview)失败时不应触发告警,避免打扰。"""
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    settings = _fake_settings(db_path)

    monkeypatch.setattr(runner, "fetch_news", lambda *a, **k: (_ for _ in ()).throw(_BoomError("fetch 失败")))

    notify_called = {"n": 0}
    monkeypatch.setattr(runner, "_notify_failure", lambda *a, **k: notify_called.__setitem__("n", notify_called["n"] + 1))

    with get_session(db_path) as session:
        with pytest.raises(_BoomError):
            run_digest(settings, session, run_type="dry-run", send=False)

    assert notify_called["n"] == 0, "dry-run 失败不应发告警"
