"""Repeatable offline soak test: real repositories, layouts and sampled real renders.

Run: python tests/stress_automation.py --rounds 10000 --output /path/report.json
No bot token, network calls, live guilds, or production database are used.
"""

import argparse
import asyncio
import copy
import datetime
import json
import random
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.admin_functions.autoreact.command import Menu as ReactionMenu
from ky_bot.admin_functions.autoreact.command import Session as ReactionSession
from ky_bot.admin_functions.autoreact.service import AutoReactService, parse_emojis
from ky_bot.admin_functions.autoreact.store import Store
from ky_bot.admin_functions.autoreact.store import defaults as react_defaults
from ky_bot.admin_functions.autothread.command import Menu as ThreadMenu
from ky_bot.admin_functions.autothread.command import Session as ThreadSession
from ky_bot.admin_functions.autothread.service import AutothreadService, qualifies
from ky_bot.admin_functions.autothread.store import RuleStore
from ky_bot.admin_functions.autothread.store import defaults as thread_defaults
from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.admin_functions.settings_service import SettingsService
from ky_bot.checklists.store import ChecklistStore
from ky_bot.design.editor import DesignSession, Editor
from ky_bot.design.menu_design import FINISHES

SEED = 20261014
TARGETS = [
    "settings:overview",
    "settings:welcome",
    "settings:logging",
    "settings:autoreact",
    "settings:autothread",
    "checklist",
]


def rejects(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Expected the invalid/stale operation to be rejected.")


async def rejects_async(call):
    try:
        await call()
    except ValueError:
        return
    raise AssertionError("Expected the stale operation to be rejected.")


def budget(view):
    assert view.total_children_count <= 40
    assert view.to_components()
    view.stop()


async def run(rounds, directory, report):
    rng = random.Random(SEED)
    started = time.monotonic()
    stats = dict(
        requested_rounds=rounds,
        completed_rounds=0,
        seed=SEED,
        failures=[],
        reaction_delivery_rounds=0,
        thread_delivery_rounds=0,
        design_storage_rounds=0,
        layout_checks=0,
        real_preview_renders=0,
        real_apply_batches=0,
        simulated_discord=True,
        backend="SQLite",
        postgres_integration="Not exercised: no disposable PostgreSQL configured.",
    )
    reactions = Store(directory / "reactions.db")
    threads = RuleStore(directory / "threads.db")
    boards = ChecklistStore(directory / "boards.db")
    boards.create(100, 1, 10, name="EXAMPLE CHECKLIST")
    async with open_settings(directory / "settings.db") as repo:
        service = SettingsService(repo)
        guild = NS(
            id=1,
            name="Stress test",
            emojis=[],
            get_emoji=lambda _: None,
            get_channel=lambda _: NS(name="example"),
        )
        avatar = NS(
            with_format=lambda _: NS(with_size=lambda _: NS(read=AsyncMock(return_value=b"")))
        )
        user = NS(
            id=10,
            bot=False,
            display_name="Example",
            guild_permissions=NS(administrator=True, manage_guild=True),
            guild=guild,
            display_avatar=avatar,
        )
        bot = NS(checklists=NS(store=boards, wakeup=NS(set=lambda: None)), server_settings=service)
        bot.get_cog = lambda _: NS(bot=bot, store=NS(list=lambda _: []))
        interaction = NS(
            user=user,
            guild_id=1,
            guild=guild,
            client=bot,
            channel=None,
            channel_id=10,
            permissions=NS(administrator=True),
            response=NS(send_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        reaction_channel = Mock(spec=discord.TextChannel)
        reaction_channel.id, reaction_channel.name = 10, "example"
        reaction_channel.guild = NS(id=1, me=NS())
        reaction_channel.permissions_for.return_value = NS(
            view_channel=True, read_message_history=True, add_reactions=True
        )
        reactor = AutoReactService(NS(get_channel=lambda _: reaction_channel), reactions)
        configuration = dict(thread_defaults(), content=["text"])
        threads.save_configuration(1, [10], configuration)
        for index in range(rounds):
            try:
                # Emoji order, idempotency, retry checkpoints, stale saves and server isolation.
                emojis = rng.choice(
                    [["💛", "✨"], ["👩🏽‍💻", "🇺🇸", "👍"], ["🔴", "🟡", "🟢"], ["🌸"]]
                )
                values = dict(react_defaults(), emojis=emojis)
                assert parse_emojis(" ".join(emojis + emojis), guild) == emojis
                old = reactions.get(1, 10)
                reactions.save(1, [10], values, {10: old["revision"] if old else None})
                rule = reactions.get(1, 10)
                assert reactions.get(2, 10) is None
                rejects(lambda: reactions.save(1, [10], values, {10: "stale"}))
                msg = NS(
                    id=100000 + index,
                    guild=reaction_channel.guild,
                    channel=reaction_channel,
                    webhook_id=None,
                    author=user,
                    type=discord.MessageType.default,
                    content="New post",
                    attachments=[],
                    embeds=[],
                    flags=NS(voice=False),
                    created_at=discord.utils.utcnow() + datetime.timedelta(seconds=1),
                    add_reaction=AsyncMock(),
                )
                reaction_channel.fetch_message = AsyncMock(return_value=msg)
                await reactor.on_message(msg)
                await reactor.on_message(msg)
                jobs = reactions.jobs()
                assert len(jobs) == 1
                mode = index % 5
                if mode == 0:
                    reactions.action(1, {10: rule["revision"]}, "off")
                    await reactor.deliver(jobs[0])
                    msg.add_reaction.assert_not_awaited()
                    paused = reactions.get(1, 10)
                    reactions.save(1, [10], values, {10: paused["revision"]})
                    assert not reactions.get(1, 10)["enabled"]
                    reactions.action(1, {10: reactions.get(1, 10)["revision"]}, "on")
                elif mode == 1:
                    reactions.action(1, {10: rule["revision"]}, "remove")
                    await reactor.deliver(jobs[0])
                    msg.add_reaction.assert_not_awaited()
                else:
                    if mode == 2:
                        msg.add_reaction.side_effect = OSError("simulated temporary failure")
                        await reactor.deliver(jobs[0])
                        reactions.update_job(msg.id, retry=0)
                        assert reactions.jobs()[0]["attempts"] == 1
                        msg.add_reaction.reset_mock(side_effect=True)
                    await reactor.deliver(reactions.jobs()[0])
                    assert [c.args[0] for c in msg.add_reaction.await_args_list] == emojis
                assert reactions.jobs() == []
                stats["reaction_delivery_rounds"] += 1

                # Actual thread service: replay a source event, send only one welcome.
                thread = NS(owner_id=900, add_user=AsyncMock(), send=AsyncMock())

                async def history(**kwargs):
                    for item in ():
                        yield item

                thread.history = history
                tguild = NS(
                    id=1, me=NS(id=900), get_thread=lambda _: thread, get_member=lambda _: user
                )
                permissions = NS(
                    **dict.fromkeys(
                        [
                            "view_channel",
                            "read_message_history",
                            "create_public_threads",
                            "send_messages_in_threads",
                            "send_messages",
                            "embed_links",
                        ],
                        True,
                    )
                )
                tchannel = NS(id=10, guild=tguild, permissions_for=lambda _: permissions)
                source = NS(**vars(msg))
                source.guild, source.channel, source.id = tguild, tchannel, 200000 + index
                threading = AutothreadService(NS(user=NS(id=900)), threads)
                with (
                    patch("ky_bot.admin_functions.autothread.service.discord.TextChannel", NS),
                    patch("ky_bot.admin_functions.autothread.service.discord.Thread", NS),
                ):
                    await threading.on_message(source)
                    await threading.on_message(source)
                thread.send.assert_awaited_once()
                thread.add_user.assert_awaited_once()
                assert threads.job(source.id)["done"] == 1
                current = threads.list(1)[0]
                updated = dict(configuration, archive=rng.choice([60, 1440, 4320, 10080]))
                threads.toggle(1, [10], False)
                threads.save_configuration(1, [10], updated, current)
                assert not threads.list(1)[0]["enabled"]
                threads.toggle(1, [10], True)
                assert threads.list(2) == []
                assert qualifies(source, updated)
                stats["thread_delivery_rounds"] += 1

                # Draft editing and atomic approval storage; cancellation has no writes.
                session = await DesignSession.create(interaction, service)
                selected = rng.sample(TARGETS, rng.randint(1, len(TARGETS)))
                await session.select(selected)
                session.change(
                    style=rng.choice(list(FINISHES)),
                    height=rng.choice([240, 280, 320, 400, 520, 800]),
                    title=rng.choice([None, "WELCOME", "EXAMPLE MENU"]),
                    footer_text=rng.choice(["", "HELLO", "KY BOT"]),
                    framed=bool(index % 2),
                )
                snapshot = await repo.get_designs(1)
                for page in ("edit", "targets", "finish"):
                    budget(Editor(session, page))
                    stats["layout_checks"] += 1
                assert await repo.get_designs(1) == snapshot
                drafts = copy.deepcopy(session.profiles)
                drafts.update({key: copy.deepcopy(session.drafts[key]) for key in selected})
                rev = await repo.apply_designs(1, drafts, session.revision)
                await rejects_async(lambda: repo.apply_designs(1, {}, session.revision))
                assert (await repo.get_designs(1))[0] == rev
                assert await repo.get_designs(2) == (None, {})
                session.revision, session.profiles = rev, drafts
                stats["design_storage_rounds"] += 1

                # Live menu serializers cycle through every context; no raster stubs.
                rsession = ReactionSession(NS(store=reactions), interaction)
                rsession.channels, rsession.draft = [10], values
                rsession.design = session.drafts[selected[0]]
                rpages = [
                    "main",
                    "configure",
                    "channels",
                    "emojis",
                    "browse",
                    "server",
                    "content",
                    "filters",
                    "review",
                    "rules",
                    "all",
                    "toggle",
                    "remove",
                ]
                budget(ReactionMenu(rsession, rpages[index % len(rpages)]))
                tsession = ThreadSession(NS(store=threads), interaction)
                tsession.channels, tsession.draft = [10], updated
                tsession.design = session.drafts[selected[0]]
                tpages = [
                    "main",
                    "configure",
                    "content",
                    "uploads",
                    "archive",
                    "access",
                    "welcome",
                    "review",
                    "rules",
                    "all",
                    "toggle",
                    "delete",
                ]
                budget(ThreadMenu(tsession, tpages[index % len(tpages)]))
                stats["layout_checks"] += 2

                # 400+ real image/layout previews across the run, plus real apply batches.
                if index % 25 == 0:
                    key = selected[index % len(selected)]
                    await session.preview(interaction, key)
                    view = interaction.edit_original_response.call_args.kwargs["view"]
                    budget(view)
                    buttons = [c for c in view.walk_children() if isinstance(c, discord.ui.Button)]
                    assert all(
                        c.disabled
                        for c in buttons
                        if c.label not in ("Apply design", "Back to editing", "Cancel")
                    )
                    assert [c.label for c in buttons if not c.disabled] == [
                        "Apply design",
                        "Back to editing",
                        "Cancel",
                    ]
                    stats["real_preview_renders"] += 1
                if index % 250 == 0:
                    await session.apply(interaction)
                    stats["real_apply_batches"] += 1
                stats["completed_rounds"] = index + 1
                if (index + 1) % 250 == 0:
                    elapsed = time.monotonic() - started
                    print(f"{index + 1}/{rounds} rounds passed; {elapsed:.1f}s", flush=True)
                interaction.edit_original_response.reset_mock()
            except Exception:
                stats["failures"].append(dict(round=index + 1, traceback=traceback.format_exc()))
                break
    reactions.db.close()
    threads.db.close()
    boards.db.close()
    stats["elapsed_seconds"] = round(time.monotonic() - started, 2)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "failures"}), flush=True)
    if stats["failures"]:
        print(stats["failures"][0]["traceback"], flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ky-soak-", dir=args.output.parent) as folder:
        asyncio.run(run(args.rounds, Path(folder), args.output))
