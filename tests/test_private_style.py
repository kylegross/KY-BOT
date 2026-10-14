from types import SimpleNamespace

import discord
import pytest

from ky_bot.checklists.inline import InlineView
from ky_bot.checklists.manage import ItemControls
from ky_bot.checklists.store import ChecklistStore
from ky_bot.views.private_style import private_heading, private_title, style_private_heading


def test_literal_stars_spacing_and_safe_user_titles():
    assert private_title("CONFIGURE RULES") == r"\*ੈ𑁍  CONFIGURE RULES  𑁍ੈ\*"
    assert private_heading("CONFIGURE RULES") == r"## \*ੈ𑁍  CONFIGURE RULES  𑁍ੈ\*"
    assert r"\*\*Example\*\*" in private_title("**Example**")
    assert "@everyone" not in private_title("@everyone")
    title = private_heading("CATEGORIES")
    assert style_private_heading(title) == title
    assert (
        style_private_heading("**CATEGORIES**\nChoose a category.")
        == title + "\nChoose a category."
    )
    assert style_private_heading("Task saved.") == "Task saved."


@pytest.mark.asyncio
async def test_future_private_views_get_card_title_and_inline_controls_by_default():
    view = InlineView(timeout=600)
    view.add_item(discord.ui.TextDisplay("**NEW MENU**\nDescription."))
    button = discord.ui.Button(label="Manage")
    view.add_item(button)
    assert isinstance(view.children[0], discord.ui.Container)
    assert view.children[0].children[0].content == private_heading("NEW MENU") + "\nDescription."
    assert isinstance(button.parent, discord.ui.Section)
    public = InlineView(timeout=None)
    public.add_item(discord.ui.TextDisplay("**PUBLIC TASK**"))
    assert public.children[0].content == "**PUBLIC TASK**"


@pytest.mark.asyncio
async def test_item_edit_menu_uses_shared_heading(tmp_path):
    store = ChecklistStore(tmp_path / "checklist.sqlite3")
    try:
        store.create(10, 1, 0)
        category = store.add_category(10, "Administration")
        view = ItemControls(SimpleNamespace(store=store), store.category(category, 10), "category")
        assert view.children[0].children[0].content.startswith(private_heading("Administration"))
    finally:
        store.db.close()
