from types import SimpleNamespace

from sqlmodel import select

from src.database import get_session, init_db
from src.emailer import parse_recipients
from src.models import EmailRecipient
from src.runner import resolve_recipients


def test_mail_to_single_and_multiple_recipients_parse():
    assert parse_recipients("a@gmail.com") == ["a@gmail.com"]
    assert parse_recipients("a@gmail.com,b@qq.com; c@outlook.com") == [
        "a@gmail.com",
        "b@qq.com",
        "c@outlook.com",
    ]
    assert parse_recipients("a@gmail.com, A@gmail.com") == ["a@gmail.com"]


def test_email_recipients_override_env_mail_to(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        session.add(EmailRecipient(email="db@example.com", name="DB", enabled=True, is_default=True))
        session.add(EmailRecipient(email="disabled@example.com", enabled=False))
        session.commit()

        recipients = resolve_recipients(session, SimpleNamespace(mail_to="env@example.com"))

        assert recipients == ["db@example.com"]
        assert len(session.exec(select(EmailRecipient)).all()) == 2


def test_email_recipients_fall_back_to_mail_to_when_db_empty(tmp_path):
    db_path = tmp_path / "app.sqlite3"
    init_db(db_path)

    with get_session(db_path) as session:
        recipients = resolve_recipients(session, SimpleNamespace(mail_to="env1@example.com,env2@example.com"))

        assert recipients == ["env1@example.com", "env2@example.com"]
