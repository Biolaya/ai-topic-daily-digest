from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def save_digest_archive(html: str, archive_dir: Path, timezone_name: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    digest_date = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
    archive_path = _available_path(archive_dir / f"{digest_date}.html")
    archive_path.write_text(html, encoding="utf-8")
    return archive_path


def _available_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"归档文件过多，无法生成可用文件名: {path}")
