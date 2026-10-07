"""Start with python -m ky_bot from the project directory."""

import asyncio
import logging
import sys

import discord

from ky_bot.bot import KYBot
from ky_bot.config import ConfigurationError, Settings
from ky_bot.logging_setup import configure_logging


async def run(settings: Settings) -> None:
    async with KYBot(settings) as bot:
        await bot.start(settings.token)


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    configure_logging(settings)
    log = logging.getLogger("ky_bot")
    try:
        # Psycopg async sockets require the selector loop on Windows.
        factory = (
            asyncio.SelectorEventLoop if sys.platform == "win32" and settings.database_url else None
        )
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(run(settings))
    except KeyboardInterrupt:
        log.info("KY BOT stopped.")
    except discord.LoginFailure:
        log.error("Discord rejected the bot token. Check DISCORD_TOKEN locally.")
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Enable Server Members and Message Content intents in the Discord Developer Portal."
        )
        raise SystemExit(1) from None
    except Exception:
        log.exception("KY BOT stopped because startup or its connection failed.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
