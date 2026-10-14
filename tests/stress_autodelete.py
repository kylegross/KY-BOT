"""10,000 isolated AutoDelete scenarios; no live Discord or production storage."""

import asyncio
import datetime
import io
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
from PIL import Image

from ky_bot.admin_functions.autodelete.autodelete_service import delete_matching
from ky_bot.admin_functions.autodelete.autodelete_store import RuleStore
from ky_bot.admin_functions.autodelete.command import Menu, Session, artwork


async def run(output):
    start = time.monotonic()
    store = RuleStore(":memory:")
    user = NS(id=90, guild_permissions=NS(manage_guild=True, administrator=True))
    guild = NS(id=1, get_channel=lambda _: None, get_member=lambda _: user)
    pages = [
        "main",
        "configure",
        "clean",
        "channels",
        "content",
        "time",
        "review",
        "confirm_clean",
        "rules",
        "all",
        "remove",
        "toggle",
        "logs",
        "log_review",
    ]
    footer_checks = 0
    for n in range(10000):
        store.save(1, [10], ["messages_all"], 60, "1 minute", 90)
        saved = store.list(1)[0]
        assert store.list(2) == []
        now = discord.utils.utcnow()
        source = NS(
            created_at=now - datetime.timedelta(seconds=61),
            attachments=[],
            embeds=[],
            author=NS(bot=False),
            delete=AsyncMock(),
        )
        assert not await delete_matching(source, [saved], store=store)
        with store.db:
            store.db.execute(
                "UPDATE rules SET starts_at=? WHERE id=?",
                ((now - datetime.timedelta(seconds=120)).timestamp(), saved["id"]),
            )
        current = store.list(1)[0]
        mode = n % 4
        if mode == 0:
            assert await delete_matching(source, [current], store=store) == 1
            source.delete.assert_awaited_once()
        elif mode == 1:
            store.toggle(1, [10], False)
            assert not await delete_matching(source, [current], store=store)
            source.delete.assert_not_awaited()
        elif mode == 2:
            store.update_rule(1, current, ["messages_bot"], 60, "1 minute", 90)
            assert not await delete_matching(source, [current], store=store)
            source.delete.assert_not_awaited()
        else:
            store.delete(1, current["id"])
            store.save(1, [10], ["messages_all"], 60, "1 minute", 90)
            assert not await delete_matching(source, [current], store=store)
            source.delete.assert_not_awaited()
        s = Session(
            NS(store=store, service=NS()), NS(guild=guild, user=user, channel=None, channel_id=10)
        )
        s.channels, s.content = [10], ["messages_all"]
        s.original = store.list(1)[0]
        menu = Menu(s, pages[n % len(pages)])
        assert menu.total_children_count <= 40
        assert menu.to_components()
        menu.stop()
        if n % 250 == 0:
            uploads = artwork()
            try:
                f = next(u for u in uploads if u.filename == "autodelete_footer.png")
                assert Image.open(io.BytesIO(f.fp.read())).size == (1600, 150)
                footer_checks += 1
            finally:
                for f in uploads:
                    f.close()
        for r in store.list(1):
            store.delete(1, r["id"])
        if (n + 1) % 1000 == 0:
            print(f"{n + 1}/10000 passed", flush=True)
    store.db.close()
    report = dict(
        completed_rounds=10000,
        failures=0,
        layout_checks=10000,
        real_footer_renders=footer_checks,
        simulated_discord=True,
        backend="SQLite",
        elapsed_seconds=round(time.monotonic() - start, 2),
    )
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1])))
