from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    def load_dotenv(*args, **kwargs):
        return False


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    tavily_api_key: str
    tavily_api_keys: tuple[str, ...]
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    admin_username: str
    admin_password: str
    session_secret: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    mail_to: str
    timezone: str
    summary_style: str
    deepseek_thinking: bool
    archive_dir: Path
    db_path: Path
    log_path: Path

    def validate(self, send: bool = False) -> None:
        missing = []
        if not self.tavily_api_keys:
            missing.append("TAVILY_API_KEYS 或 TAVILY_API_KEY")

        if send:
            for name, value in (
                ("SMTP_HOST", self.smtp_host),
                ("SMTP_PORT", str(self.smtp_port) if self.smtp_port else ""),
                ("SMTP_USER", self.smtp_user),
                ("SMTP_PASS", self.smtp_pass),
            ):
                if not value:
                    missing.append(name)

        if missing:
            names = ", ".join(missing)
            raise ValueError(f"缺少必要环境变量: {names}")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前值为: {raw}") from exc


def get_settings(root_dir: Path | None = None) -> Settings:
    root = root_dir or ROOT_DIR
    load_dotenv(root / ".env")
    tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
    tavily_api_keys = tuple(_env_list("TAVILY_API_KEYS") or ([tavily_api_key] if tavily_api_key else []))
    llm_api_key = os.getenv("LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    llm_base_url = (
        os.getenv("LLM_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
        or "https://api.deepseek.com"
    )
    llm_model = (
        os.getenv("LLM_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "deepseek-v4-pro"
    )

    return Settings(
        root_dir=root,
        tavily_api_key=tavily_api_key,
        tavily_api_keys=tavily_api_keys,
        llm_provider=os.getenv("LLM_PROVIDER", "deepseek").strip() or "deepseek",
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        openai_api_key=llm_api_key,
        openai_base_url=llm_base_url,
        openai_model=llm_model,
        admin_username=os.getenv("ADMIN_USERNAME", "admin").strip(),
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
        session_secret=os.getenv("SESSION_SECRET", "").strip() or os.getenv("ADMIN_PASSWORD", "").strip() or "change-me",
        smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
        smtp_port=_env_int("SMTP_PORT", 465),
        smtp_user=os.getenv("SMTP_USER", "").strip(),
        smtp_pass=os.getenv("SMTP_PASS", "").strip(),
        mail_to=os.getenv("MAIL_TO", "").strip(),
        timezone=os.getenv("TIMEZONE", "Asia/Shanghai").strip(),
        summary_style=os.getenv("SUMMARY_STYLE", "清晰、具体、简洁").strip(),
        deepseek_thinking=_env_bool("DEEPSEEK_THINKING", True),
        archive_dir=_env_path("ARCHIVE_DIR", root / "data" / "archives", root),
        db_path=root / "data" / "app.sqlite3",
        log_path=root / "logs" / "digest.log",
    )


def _env_path(name: str, default: Path, root: Path) -> Path:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else root / path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值，当前值为: {raw}")


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    seen = set()
    values = []
    for item in raw.replace(";", ",").split(","):
        value = item.strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values
