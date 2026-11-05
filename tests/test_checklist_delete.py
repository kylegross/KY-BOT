import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from ky_bot.checklists.board import BoardView
from ky_bot.checklists.deletion import DeleteChecklistConfirm
from ky_bot.checklists.service import ChecklistService
from ky_bot.checklists.store import ChecklistStore


class DeleteChecklistTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ChecklistStore(":memory:")
        self.store.create(10, 1, 20)
        self.store.update_board(10, message=99)
        self.store.create(11, 1, 20)
        self.store.add(10, 1000, 50, "Keep history", [])
        self.task = self.store.db.execute("SELECT id FROM tasks WHERE channel=10").fetchone()[0]
        self.store.add_subtask(self.task, "Subtask", 50)
        self.store.add_category(10, "Planning")
        self.store.db.execute(
            "INSERT INTO logs(channel,task,title,actor,created,log_channel) VALUES (?,?,?,?,?,?)",
            (10, self.task, "Keep history", 50, 0, 20),
        )
        self.panel = SimpleNamespace(delete=AsyncMock())
        self.channel = SimpleNamespace(get_partial_message=Mock(return_value=self.panel))
        self.service = SimpleNamespace(
            store=self.store,
            lock=asyncio.Lock(),
            wakeup=asyncio.Event(),
            channel=AsyncMock(return_value=self.channel),
        )

    async def asyncTearDown(self):
        self.store.db.close()

    def interaction(self, *, admin=True, user=50, channel=10, guild=1):
        return SimpleNamespace(
            guild=SimpleNamespace(id=guild),
            guild_id=guild,
            channel_id=channel,
            user=SimpleNamespace(id=user, guild_permissions=SimpleNamespace(administrator=admin)),
            response=SimpleNamespace(
                defer=AsyncMock(),
                edit_message=AsyncMock(),
                is_done=lambda: False,
                send_message=AsyncMock(),
            ),
            edit_original_response=AsyncMock(),
        )

    async def test_confirmation_deletes_only_target_board_and_keeps_logs(self):
        view = DeleteChecklistConfirm(self.service, self.store.board(10), 50)
        await view.remove(self.interaction())
        self.assertIsNone(self.store.board(10))
        self.assertIsNotNone(self.store.board(11))
        for table in ("tasks", "subtasks", "categories"):
            self.assertEqual(
                self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
            )
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM logs").fetchone()[0], 1)
        self.panel.delete.assert_awaited_once()
        self.assertTrue(view.is_finished())
        self.assertTrue(self.service.wakeup.is_set())

    async def test_owner_role_guild_and_channel_are_rechecked(self):
        view = DeleteChecklistConfirm(self.service, self.store.board(10), 50)
        for changes in (dict(admin=False), dict(user=51), dict(channel=11), dict(guild=2)):
            await view.remove(self.interaction(**changes))
            self.assertIsNotNone(self.store.board(10))
        self.panel.delete.assert_not_awaited()
        view.stop()

    async def test_cancel_keeps_board(self):
        view = DeleteChecklistConfirm(self.service, self.store.board(10), 50)
        await view.keep(self.interaction())
        self.assertIsNotNone(self.store.board(10))
        self.assertTrue(view.is_finished())
        self.panel.delete.assert_not_awaited()

    async def test_changed_or_recreated_board_rejects_old_confirmation(self):
        board = self.store.board(10)
        self.store.update_board(10, name="Changed")
        with self.assertRaises(ValueError):
            self.store.delete_board(10, 1, board["revision"], board["message"])
        board = self.store.board(10)
        self.store.delete_board(10, 1, board["revision"], board["message"])
        self.store.create(10, 1, 20)
        self.store.update_board(10, message=101)
        with self.assertRaises(ValueError):
            self.store.delete_board(10, 1, board["revision"], board["message"])

    async def test_failed_message_removal_still_disables_checklist(self):
        self.panel.delete.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "No access"
        )
        view = DeleteChecklistConfirm(self.service, self.store.board(10), 50)
        i = self.interaction()
        await view.remove(i)
        self.assertIsNone(self.store.board(10))
        payload = str(i.edit_original_response.await_args.kwargs["view"].to_components())
        self.assertIn("manually", payload)

    async def test_queued_message_does_not_create_task_after_delete(self):
        service = ChecklistService(Mock(), self.store)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=10),
            webhook_id=None,
            type=discord.MessageType.default,
            id=1001,
            content="Queued message",
            attachments=[],
            author=SimpleNamespace(
                id=50,
                bot=False,
                guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
            ),
        )
        await service.lock.acquire()
        task = asyncio.create_task(service.on_message(message))
        await asyncio.sleep(0)
        board = self.store.board(10)
        self.store.delete_board(10, 1, board["revision"], board["message"])
        service.lock.release()
        await task
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    async def test_full_board_fits_component_limit_with_delete_button(self):
        self.store.db.execute("DELETE FROM tasks")
        for index in range(4):
            category = self.store.add_category(10, f"Category {index}")
            self.store.add(10, 2000 + index, 50, f"Task {index}", [])
            tid = self.store.db.execute(
                "SELECT id FROM tasks WHERE source=?", (2000 + index,)
            ).fetchone()[0]
            self.store.move_task(tid, category, 0)
        view = BoardView(self.service, self.store.board(10))
        self.assertLessEqual(view.total_children_count, 40)
        self.assertIn("Delete checklist", str(view.to_components()))
        view.stop()


if __name__ == "__main__":
    unittest.main()
