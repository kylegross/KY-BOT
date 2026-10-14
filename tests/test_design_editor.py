import io
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.admin_functions.settings_service import SettingsService
from ky_bot.checklists.store import ChecklistStore
from ky_bot.design.editor import DesignSession, Editor
from ky_bot.design.menu_design import default_profile, render_menu


async def test_design_atomic_revision_and_guild_isolation(tmp_path):
    async with open_settings(tmp_path / "settings.db") as repo:
        assert await repo.get_designs(1) == (None, {})
        profile = default_profile("autoreact")
        rev = await repo.apply_designs(1, {"autoreact": profile, "autothread": profile}, None)
        assert await repo.get_designs(2) == (None, {})
        with pytest.raises(ValueError):
            await repo.apply_designs(1, {"autoreact": {}}, None)
        with pytest.raises(ValueError):
            await repo.apply_designs(1, {}, "stale")
        actual, profiles = await repo.get_designs(1)
        assert actual == rev and set(profiles) == {"autoreact", "autothread"}
    async with open_settings(tmp_path / "settings.db") as repo:
        assert (await repo.get_designs(1))[0] == rev


@pytest.fixture
async def session(tmp_path):
    async with open_settings(tmp_path / "settings.db") as repo:
        store = ChecklistStore(tmp_path / "checklists.db")
        service = SettingsService(repo)
        guild = NS(id=1, name="Server", get_channel=lambda _: NS(name="tasks"))
        avatar = NS(
            with_format=lambda _: NS(with_size=lambda _: NS(read=AsyncMock(return_value=b"")))
        )
        member = NS(id=10, display_name="Example member", guild=guild, display_avatar=avatar)
        bot = NS(checklists=NS(store=store, wakeup=NS(set=lambda: None)), server_settings=service)
        bot.get_cog = lambda _: NS(bot=bot, store=NS(list=lambda _: []))
        interaction = NS(
            user=member,
            guild_id=1,
            guild=guild,
            client=bot,
            channel=None,
            permissions=NS(administrator=True),
            edit_original_response=AsyncMock(),
            response=NS(send_message=AsyncMock()),
        )
        yield await DesignSession.create(interaction, service), interaction, store
        store.db.close()


async def test_draft_preview_disabled_and_apply_only_on_approval(session):
    s, interaction, store = session
    await s.select(
        [
            "settings:overview",
            "settings:welcome",
            "settings:logging",
            "autoreact",
            "autodelete",
            "autothread",
            "checklist",
        ]
    )
    s.change(style="gold", height=320, footer_text="HELLO")
    assert await s.service.repository.get_designs(1) == (None, {})
    for page in ("edit", "targets", "finish"):
        view = Editor(s, page)
        assert view.total_children_count <= 40
        assert view.to_components()
    for key in s.selected:
        await s.preview(interaction, key)
        options = interaction.edit_original_response.call_args.kwargs
        view = options["view"]
        assert view.total_children_count <= 40
        controls = [c for c in view.walk_children() if isinstance(c, discord.ui.Button)]
        assert any(c.disabled for c in controls)
        assert [c.label for c in controls if not c.disabled] == [
            "Apply design",
            "Back to editing",
            "Cancel",
        ]
        assert all(f.description is None for f in options["attachments"])
    assert await s.service.repository.get_designs(1) == (None, {})
    await s.apply(interaction)
    _, profiles = await s.service.repository.get_designs(1)
    assert len(profiles) == 7
    assert all(p["height"] == 320 for p in profiles.values())
    assert store.boards() == []


async def test_welcome_image_preview_and_draft_cancel(session):
    s, i, _ = session
    await s.select(["welcome_image"])
    s.change(title="HELLO", style="gold")
    await s.preview(i, "welcome_image")
    assert i.edit_original_response.call_args.kwargs["view"].total_children_count <= 40
    assert await s.service.repository.get_designs(1) == (None, {})
    with pytest.raises(ValueError):
        await s.select(["welcome_image", "autoreact"])


async def test_existing_checklist_draft_retains_custom_title(session):
    s, interaction, store = session
    store.create(100, 1, 10, name="CUSTOM BOARD")
    s.targets["checklist:100"] = "Custom board"
    await s.select(["checklist:100"])
    assert s.drafts["checklist:100"]["title"] == "CUSTOM BOARD"
    await s.preview(interaction, "checklist:100")
    assert interaction.edit_original_response.call_args.kwargs["view"].total_children_count <= 40
    s.change(style="dark_silver")
    await s.apply(interaction)
    assert store.board(100)["style"] == "dark_silver"
    assert store.board(100)["dirty"]


async def test_editor_owner_permissions_and_expired_generation(session):
    s, i, _ = session
    assert await s.allowed(i, 0)
    i.user.id = 11
    assert not await s.allowed(i, 0)
    i.user.id = 10
    i.permissions.administrator = False
    assert not await s.allowed(i, 0)
    i.permissions.administrator = True
    s.generation = 1
    assert not await s.allowed(i, 0)


def test_render_menu_keeps_exact_header_height_and_matching_finish():
    profile = default_profile("autoreact")
    profile.update(style="gold", height=400, footer_text="FINISHED")
    header, footer = render_menu(profile, "AUTOREACT")
    assert Image.open(io.BytesIO(header)).size == (1600, 400)
    assert Image.open(io.BytesIO(footer)).size == (1600, 150)
