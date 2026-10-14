"""Persistent AutoDelete rules; existing preview rules retain their OFF state."""

import json
import sqlite3
import time
from pathlib import Path

ROLE_IDS = set()
CONTENT = {
    "messages": (
        "Messages",
        {
            "messages_all": "All messages (bot & user)",
            "messages_bot": "Only bot messages",
            "messages_user": "Only user messages",
        },
    ),
    "threads": (
        "Threads",
        {
            "threads_all": "All thread types",
            "threads_text": "Threads from messages",
            "threads_image": "Threads from images",
            "threads_video": "Threads from videos",
            "threads_gif": "Threads from GIFs",
        },
    ),
    "media": (
        "Pictures & Videos",
        {
            "media_all": "Both pictures & videos",
            "media_image": "Only pictures",
            "media_video": "Only videos",
            "media_gif": "Only GIFs",
        },
    ),
}
LABELS = {k: v for _, options in CONTENT.values() for k, v in options.items()}
BOT_MEDIA = {
    f"botmedia_{action}_{kind}": f"{action.title()} bot {label}"
    for kind, label in [("image", "images (including GIFs)"), ("video", "videos")]
    for action in ("keep", "delete")
}
LABELS.update(BOT_MEDIA)
PRESETS = {
    "5 minutes": 300,
    "10 minutes": 600,
    "15 minutes": 900,
    "30 minutes": 1800,
    "1 hour": 3600,
    "6 hours": 21600,
    "12 hours": 43200,
    "1 day": 86400,
    "3 days": 259200,
    "1 week": 604800,
    "1 month": 2592000,
    "6 months": 15552000,
    "1 year": 31536000,
}
UNITS = {"Minutes": 60, "Hours": 3600, "Days": 86400, "Months": 2592000}


def authorized(interaction):
    return (
        interaction.guild is not None
        and not interaction.user.bot
        and (
            interaction.user.guild_permissions.administrator
            or interaction.user.guild_permissions.manage_guild
        )
    )


def normalize(keys):
    keys = set(keys)
    if not keys or not keys <= LABELS.keys():
        raise ValueError("Select at least one content type.")
    if not any(not k.startswith("botmedia_keep_") for k in keys):
        raise ValueError("Choose content to delete as well as what to keep.")
    for kind in ("image", "video"):
        if {f"botmedia_keep_{kind}", f"botmedia_delete_{kind}"} <= keys:
            raise ValueError("Choose either Keep or Delete for each bot media type.")
    for group in CONTENT:
        if group + "_all" in keys:
            keys = {k for k in keys if not k.startswith(group + "_") or k == group + "_all"}
    return sorted(keys)


class RuleStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS rules (id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, channel INTEGER NOT NULL, content TEXT NOT NULL, seconds INTEGER NOT NULL, timeframe TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, actor INTEGER NOT NULL, UNIQUE(guild,channel,content,seconds))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS thread_sources (guild INTEGER NOT NULL, thread INTEGER NOT NULL, kinds TEXT NOT NULL, PRIMARY KEY(guild,thread))"
        )
        # One-time upgrade: existing rules begin at deployment, never at channel creation.
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(rules)")}
        if "starts_at" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE rules ADD COLUMN starts_at REAL NOT NULL DEFAULT 0")
                self.db.execute("UPDATE rules SET starts_at=?", (time.time(),))

    def save(self, guild, channels, content, seconds, timeframe, actor):
        if not channels:
            raise ValueError("Select at least one channel.")
        if not isinstance(seconds, int) or not 60 <= seconds <= 31536000:
            raise ValueError("Choose a timeframe from one minute to one year.")
        content = json.dumps(normalize(content))
        with self.db:
            for channel in set(channels):
                self.db.execute(
                    "INSERT OR IGNORE INTO rules(guild,channel,content,seconds,timeframe,actor,enabled,starts_at) VALUES (?,?,?,?,?,?,1,?)",
                    (guild, channel, content, seconds, timeframe, actor, time.time()),
                )

    def update_rule(self, guild, original, content, seconds, timeframe, actor):
        if not isinstance(seconds, int) or not 60 <= seconds <= 31536000:
            raise ValueError("Choose a timeframe from one minute to one year.")
        keys = normalize(content)
        with self.db:
            current = next((r for r in self.list(guild) if r["id"] == original["id"]), None)
            if current != original:
                raise ValueError("This rule changed in another panel. Reopen it before saving.")
            try:
                self.db.execute(
                    "UPDATE rules SET content=?,seconds=?,timeframe=?,actor=? WHERE guild=? AND id=?",
                    (json.dumps(keys), seconds, timeframe, actor, guild, original["id"]),
                )
            except sqlite3.IntegrityError:
                raise ValueError("An identical rule already exists in this channel.") from None

    def delete(self, guild, rule_id, expected=None):
        with self.db:
            current = next((r for r in self.list(guild) if r["id"] == rule_id), None)
            if current is None:
                return False
            if expected is not None and current != expected:
                raise ValueError("This rule changed. Reopen View Rules before deleting it.")
            return bool(
                self.db.execute(
                    "DELETE FROM rules WHERE guild=? AND id=?", (guild, rule_id)
                ).rowcount
            )

    def list(self, guild):
        return [
            dict(row, content=json.loads(row["content"]))
            for row in self.db.execute(
                "SELECT * FROM rules WHERE guild=? ORDER BY channel,id", (guild,)
            )
        ]

    def toggle(self, guild, channels, enabled):
        count = 0
        with self.db:
            for channel in set(channels):
                count += self.db.execute(
                    "UPDATE rules SET enabled=? WHERE guild=? AND channel=? AND enabled!=?",
                    (int(enabled), guild, channel, int(enabled)),
                ).rowcount
        return count

    def enabled(self, rule_id, expected=None):
        row = self.db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        if not row or not row["enabled"]:
            return False
        if expected is not None:
            current = dict(row, content=json.loads(row["content"]))
            return all(
                current[k] == expected[k]
                for k in ("guild", "channel", "content", "seconds", "starts_at")
            )
        return True

    def remember_thread(self, guild, thread, kinds):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO thread_sources VALUES (?,?,?)",
                (guild, thread, json.dumps(sorted(kinds))),
            )

    def thread_kinds(self, guild, thread):
        row = self.db.execute(
            "SELECT kinds FROM thread_sources WHERE guild=? AND thread=?", (guild, thread)
        ).fetchone()
        return set(json.loads(row["kinds"])) if row else None

    def forget_thread(self, guild, thread):
        with self.db:
            self.db.execute(
                "DELETE FROM thread_sources WHERE guild=? AND thread=?", (guild, thread)
            )
