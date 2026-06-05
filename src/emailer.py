from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate


def send_email(settings, subject: str, html: str, recipients: list[str] | None = None) -> None:
    recipients = recipients or parse_recipients(settings.mail_to)
    if not recipients:
        raise ValueError("MAIL_TO 为空，无法发送邮件")
    if not settings.smtp_user:
        raise ValueError("SMTP_USER 为空，无法发送邮件")
    if not settings.smtp_pass:
        raise ValueError("SMTP_PASS 为空，无法发送邮件")

    message = MIMEText(html, "html", "utf-8")
    message["Subject"] = str(Header(subject, "utf-8"))
    message["From"] = settings.smtp_user
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.login(settings.smtp_user, settings.smtp_pass)
            server.sendmail(settings.smtp_user, recipients, message.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError("Gmail SMTP 认证失败：请确认 SMTP_USER 是完整 Gmail 地址，SMTP_PASS 是 Gmail App Password。") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"Gmail SMTP 发送失败：{exc.__class__.__name__}") from exc


def parse_recipients(mail_to: str) -> list[str]:
    normalized = (mail_to or "").replace(";", ",")
    recipients = []
    seen = set()
    for item in normalized.split(","):
        email = item.strip()
        if email and email.lower() not in seen:
            seen.add(email.lower())
            recipients.append(email)
    return recipients
