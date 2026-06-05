from __future__ import annotations

import argparse
import logging
import sys

from src.cleanup import prune_old_data
from src.config import get_settings
from src.database import get_session, get_settings_map, init_db
from src.models import utc_now
from src.runner import redact_error, run_digest, safe_int
from src.schedule import should_send_now


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通用主题订阅新闻中文邮件简报")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="生成邮件并打印，不发送")
    mode.add_argument("--send", action="store_true", help="生成邮件并通过 Gmail SMTP 发送")
    mode.add_argument("--init-db", action="store_true", help="初始化或迁移 SQLite 数据库")
    mode.add_argument("--prune", action="store_true", help="清理超过保留期的历史数据(html_snapshot 和新闻条目)")
    parser.add_argument("--force", action="store_true", help="与 --send 配合:绕过 send_time 守卫强制发送(调试/补发)")
    return parser.parse_args()


def setup_logging(log_path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def main() -> int:
    args = parse_args()
    settings = get_settings()
    setup_logging(settings.log_path)
    logger = logging.getLogger("digest")

    try:
        if args.init_db:
            init_db(settings.db_path)
            logger.info("数据库初始化完成：%s", settings.db_path)
            print(f"initialized {settings.db_path}")
            return 0

        if args.prune:
            init_db(settings.db_path)
            with get_session(settings.db_path) as session:
                retention_days = safe_int(get_settings_map(session).get("retention_days", "30"), 30)
                stats = prune_old_data(session, retention_days, logger=logger)
            print(
                f"pruned: html_snapshot cleared={stats['runs_cleared']}, "
                f"news deleted={stats['news_deleted']} (retention_days={retention_days})"
            )
            return 0

        settings.validate(send=args.send)
        init_db(settings.db_path)

        if not settings.llm_api_key:
            logger.warning("LLM_API_KEY/OPENAI_API_KEY 未配置，将使用 Tavily 原始摘要降级。")

        with get_session(settings.db_path) as session:
            if args.send and not args.force:
                ok, reason = should_send_now(session, settings, utc_now())
                if not ok:
                    logger.info("跳过发送:%s", reason)
                    print(f"skipped: {reason}")
                    return 0

            result = run_digest(
                settings,
                session,
                run_type="send" if args.send else "dry-run",
                send=args.send,
                logger=logger,
            )

        if args.dry_run:
            print(f"Subject: {result['subject']}")
            print(result["html"])
            logger.info("dry-run 完成，未发送邮件")
            return 0

        logger.info("邮件发送成功")
        return 0

    except Exception as exc:
        logger.error("运行失败：%s", redact_error(exc, settings))
        return 1


if __name__ == "__main__":
    sys.exit(main())
