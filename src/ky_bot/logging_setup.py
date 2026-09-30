"""Rotating UTF-8 logs with token redaction, including exception tracebacks."""

import logging
from logging.handlers import RotatingFileHandler

from ky_bot.config import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secret: str) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        self.secret = secret

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return rendered.replace(self.secret, "[REDACTED]") if self.secret else rendered


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(settings.token)
    console = logging.StreamHandler()
    rotating = RotatingFileHandler(
        settings.log_dir / "ky-bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    for handler in (console, rotating):
        handler.setFormatter(formatter)
    logging.basicConfig(level=settings.log_level, handlers=[console, rotating], force=True)
    # HTTP debug output can contain user data. Keep it out of ordinary bot logs.
    logging.getLogger("discord.http").setLevel(logging.WARNING)
