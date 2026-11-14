import asyncio
import tempfile
import unittest
from pathlib import Path

from ky_bot.tickets.opening import OpenedPanel, example_ticket, opening_design
from ky_bot.tickets.store import Store, defaults, new_panel, new_type


class TicketNumberTests(unittest.IsolatedAsyncioTestCase):
    async def test_numbers_are_atomic_guild_scoped_and_persist_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tickets.sqlite3"
            store = await Store.open(path)
            try:
                value = defaults()
                value["enabled"] = True
                value["defaults"].update(cooldown=0, max_open=20)
                value["panels"]["a" * 32] = new_panel("Support") | dict(types=["b" * 32])
                value["types"]["b" * 32] = new_type("Support")
                for guild in (1, 2):
                    revision, _ = await store.config(guild)
                    await store.configure(guild, revision, value)

                async def reserve(guild, owner):
                    return await store.reserve(guild, owner, "a" * 32, "b" * 32, [])

                tickets = await asyncio.gather(*(reserve(1, owner) for owner in range(10, 15)))
                self.assertEqual(sorted(t["number"] for t in tickets), [1, 2, 3, 4, 5])
                self.assertTrue(all(t["member_number"] == 1 for t in tickets))
                self.assertEqual((await reserve(1, 10))["member_number"], 2)
                self.assertEqual((await reserve(2, 10))["member_number"], 1)
                self.assertEqual((await reserve(2, 30))["number"], 2)
                await store.update(tickets[0], state="deleted")
            finally:
                await store.close()
            store = await Store.open(path)
            try:
                ticket = await store.reserve(1, 40, "a" * 32, "b" * 32, [])
                self.assertEqual(ticket["number"], 7)
                ticket = await store.reserve(1, 10, "a" * 32, "b" * 32, [])
                self.assertEqual(ticket["member_number"], 3)
            finally:
                await store.close()

    async def test_heading_and_preview_use_readable_number(self):
        design = opening_design({})
        view = OpenedPanel(design, example_ticket(design))
        content = str(view.to_components())
        self.assertIn("Support · Ticket No. 1", content)
        self.assertNotIn("Support · a1b2c3d4", content)
        view.stop()
