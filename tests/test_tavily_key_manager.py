from datetime import timezone

from sqlmodel import select

from src.database import get_session, init_db
from src.models import TavilyKeyStatus
from src.tavily_key_manager import TavilyKeyManager, classify_error, key_fingerprint


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_tavily_key_manager_dedupes_keys_and_stores_fingerprint_only(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    manager = TavilyKeyManager(["key-one", "key-one", "key-two"], db_path=db_path)

    assert manager.api_keys == ["key-one", "key-two"]
    manager.record_failure("key-one", HttpError(401))

    with get_session(db_path) as session:
        row = session.exec(select(TavilyKeyStatus)).one()
        assert row.key_fingerprint == key_fingerprint("key-one")
        assert row.key_fingerprint != "key-one"
        assert row.status == "invalid"
        assert "key-one" not in row.last_error


def test_tavily_key_manager_classifies_rate_limit_with_disabled_until():
    status, disabled_until = classify_error(HttpError(429))

    assert status == "rate_limited"
    assert disabled_until is not None
    assert disabled_until.tzinfo is not None


def test_tavily_key_manager_failover_bad_key_to_valid_key(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)
    manager = TavilyKeyManager(["bad_key", "valid_key"], db_path=db_path)
    used_keys = []

    class Client:
        def __init__(self, key):
            self.key = key

        def search(self, **kwargs):
            used_keys.append(self.key)
            if self.key == "bad_key":
                raise HttpError(401)
            return {"results": [{"title": "ok", "url": "https://example.com"}]}

    result = manager.search_with_failover(Client, {"query": "news"})

    assert result["results"][0]["title"] == "ok"
    assert used_keys == ["bad_key", "valid_key"]
    with get_session(db_path) as session:
        rows = {row.key_fingerprint: row for row in session.exec(select(TavilyKeyStatus)).all()}
        assert rows[key_fingerprint("bad_key")].status == "invalid"
        assert rows[key_fingerprint("valid_key")].status == "active"
        assert rows[key_fingerprint("valid_key")].last_used_at.tzinfo in (None, timezone.utc)
