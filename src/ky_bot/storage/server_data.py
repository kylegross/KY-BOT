"""Server-scoped checklist access for an authenticated future web backend.

The caller must verify Discord membership and administrator/authorized-role
permissions before constructing this service with an approved server ID.
"""

from contextlib import contextmanager

from ky_bot.storage.postgres import CHECKLIST_LOCK


class ServerChecklistData:
    def __init__(self, store, guild_id):
        self.store = store
        self.guild_id = guild_id

    @contextmanager
    def channel(self, channel_id):
        # Authorization scope and mutation share the same transaction.
        with self.store.operation_lock:
            with self.store.db:
                self.store.db.execute("SELECT pg_advisory_xact_lock(?)", (CHECKLIST_LOCK,))
                board = self.store.board(channel_id)
                if not board or board["guild"] != self.guild_id:
                    raise ValueError("This checklist is not in the authorized server.")
                yield board

    def tasks(self, channel_id):
        with self.channel(channel_id):
            return self.store.tasks(channel_id)

    def categories(self, channel_id):
        with self.channel(channel_id):
            return self.store.categories(channel_id)

    def complete(self, channel_id, task_id, actor_id, revision, done=True):
        with self.channel(channel_id):
            task = self.store.task(task_id)
            if task["channel"] != channel_id:
                raise ValueError("This task is not in the authorized checklist.")
            self.store.set_done(task_id, actor_id, revision, done)
            return self.store.task(task_id)

    def edit(self, channel_id, task_id, revision, *, title=None, priority=None):
        with self.channel(channel_id):
            task = self.store.task(task_id)
            if task["channel"] != channel_id:
                raise ValueError("This task is not in the authorized checklist.")
            self.store.edit_task(task_id, revision, title=title, priority=priority)
            return self.store.task(task_id)

    def add_category(self, channel_id, name):
        with self.channel(channel_id):
            return self.store.add_category(channel_id, name)
