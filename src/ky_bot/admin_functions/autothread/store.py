"""Persistent Autothread rules, delivery records, and warning cleanup."""

import json
import sqlite3
import time
from pathlib import Path

STARTER = "♡ It's time for you to shine! Welcome to your thread, {user}. Anyone can comment here ♡"
COLORS = {
    "Dark silver": 0xBDC3CA,
    "Gold": 0xE8C47C,
    "Multicolor silver": 0xD6E2F3,
    "Hot Pink": 0xFF69B4,
    "Blue": 0x3498DB,
    "Green": 0x2ECC71,
    "Yellow": 0xFFD600,
    "Red": 0xE53935,
    "Teal": 0x39DCCB,
    "Purple": 0x9B59B6,
}


def category_color(channel):
    return 0xBDC3CA


class RuleStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS rules (id INTEGER PRIMARY KEY, guild "
            "INTEGER NOT NULL, channel INTEGER NOT NULL, content TEXT NOT "
            "NULL, enabled INTEGER NOT NULL DEFAULT 0)"
        )
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(rules)")}
        with self.db:
            for name, definition in {
                "require_attachment": "INTEGER NOT NULL DEFAULT 0",
                "media_only": "INTEGER NOT NULL DEFAULT 0",
                "ignore_webhooks": "INTEGER NOT NULL DEFAULT 1",
                "archive": "INTEGER NOT NULL DEFAULT 1440",
                "starts_at": "REAL NOT NULL DEFAULT 0",
                "starter_message": "TEXT",
                "embed_color": "INTEGER",
            }.items():
                if name not in columns:
                    self.db.execute(f"ALTER TABLE rules ADD COLUMN {name} {definition}")
            self.db.execute(
                "UPDATE rules SET starter_message=? WHERE starter_message IS NULL", (STARTER,)
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS processed (message INTEGER PRIMARY "
                "KEY, guild INTEGER NOT NULL, channel INTEGER NOT NULL, rule_id "
                "INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0, attempts "
                "INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS welcome_deliveries(message INTEGER "
                "PRIMARY KEY,description TEXT,color INTEGER)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS notices (message INTEGER PRIMARY KEY, "
                "channel INTEGER NOT NULL, expires REAL NOT NULL)"
            )
            self.db.execute("CREATE TABLE IF NOT EXISTS retired_rules (id INTEGER PRIMARY KEY)")

    def save(self, guild, channels, settings):
        settings = validate(settings)
        if not channels:
            raise ValueError("Select at least one channel.")
        with self.db:
            for channel in set(channels):
                values = (
                    guild,
                    channel,
                    json.dumps(settings["content"]),
                    int(settings["require_attachment"]),
                    int(settings["media_only"]),
                    int(settings["ignore_webhooks"]),
                    settings["archive"],
                    settings["starter_message"],
                    settings["embed_color"],
                )
                existing = self.db.execute(
                    (
                        "SELECT id FROM rules WHERE guild=? AND channel=? AND content=? "
                        "AND require_attachment=? AND media_only=? AND ignore_webhooks=? "
                        "AND archive=? AND starter_message=? AND embed_color IS ?"
                    ),
                    values,
                ).fetchone()
                if existing:
                    self.db.execute("UPDATE rules SET enabled=1 WHERE id=?", (existing["id"],))
                else:
                    next_id = self.db.execute(
                        "SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM rules UNION "
                        "ALL SELECT id FROM retired_rules) AS reserved_ids"
                    ).fetchone()[0]
                    self.db.execute(
                        (
                            "INSERT INTO "
                            "rules(id,guild,channel,content,require_attachment,media_only,igno"
                            "re_webhooks,archive,starter_message,embed_color,enabled,starts_at"
                            ") VALUES (?,?,?,?,?,?,?,?,?,?,1,?)"
                        ),
                        (next_id, *values, time.time()),
                    )

    def save_configuration(self, guild, channels, settings, original=None):
        settings = validate(settings)
        with self.db:
            if original is None:
                if any(r["channel"] in channels for r in self.list(guild)):
                    raise ValueError(
                        "A selected channel already has rules. Reopen Configure Rule(s) "
                        "to edit one."
                    )
                self.save(guild, channels, settings)
                return
            current = self.rule(original["id"])
            if not current or current["guild"] != guild or set(channels) != {current["channel"]}:
                raise ValueError(
                    "This rule was removed or its channel selection changed. Reopen "
                    "Configure Rule(s)."
                )
            if any(current[key] != original[key] for key in defaults()):
                raise ValueError(
                    "This rule was edited in another panel. Reopen Configure Rule(s) "
                    "to load its latest settings."
                )
            self.db.execute(
                (
                    "UPDATE rules SET "
                    "content=?,require_attachment=?,media_only=?,ignore_webhooks=?,arc"
                    "hive=?,starter_message=?,embed_color=? WHERE guild=? AND id=?"
                ),
                (
                    json.dumps(settings["content"]),
                    int(settings["require_attachment"]),
                    int(settings["media_only"]),
                    int(settings["ignore_webhooks"]),
                    settings["archive"],
                    settings["starter_message"],
                    settings["embed_color"],
                    guild,
                    current["id"],
                ),
            )

    def initialize_colors(self, guild):
        # NULL marks pre-upgrade rules or an explicit category-default selection.
        # Persist once; never overwrite an administrator's later color choice.
        with self.db:
            for row in self.db.execute(
                "SELECT id,channel FROM rules WHERE guild=? AND embed_color IS NULL", (guild.id,)
            ).fetchall():
                channel = guild.get_channel(row["channel"])
                if channel is not None:
                    self.db.execute(
                        "UPDATE rules SET embed_color=? WHERE id=? AND embed_color IS NULL",
                        (category_color(channel), row["id"]),
                    )

    def rule(self, rule_id):
        row = self.db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        return (
            dict(
                row,
                content=json.loads(row["content"]),
                starter_message=row["starter_message"] or STARTER,
            )
            if row
            else None
        )

    def delete(self, guild, rule_id):
        with self.db:
            row = self.db.execute(
                "SELECT id FROM rules WHERE guild=? AND id=?", (guild, rule_id)
            ).fetchone()
            if row is None:
                return False
            self.db.execute("INSERT OR IGNORE INTO retired_rules VALUES (?)", (rule_id,))
            self.db.execute(
                "UPDATE processed SET done=1 WHERE guild=? AND rule_id=?", (guild, rule_id)
            )
            self.db.execute("DELETE FROM rules WHERE guild=? AND id=?", (guild, rule_id))
        return True

    def welcome(self, message, description, color):
        # Keep the first attempted payload stable across edits/restarts so delivery
        # can recover without putting an internal identifier in the public embed.
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO welcome_deliveries VALUES (?,?,?)",
                (message, description, color),
            )
        return dict(
            self.db.execute(
                "SELECT * FROM welcome_deliveries WHERE message=?", (message,)
            ).fetchone()
        )

    def job(self, message):
        row = self.db.execute("SELECT * FROM processed WHERE message=?", (message,)).fetchone()
        return dict(row) if row else None

    def claim(self, message, rule):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO processed(message,guild,channel,rule_id) VALUES (?,?,?,?)",
                (message.id, message.guild.id, message.channel.id, rule["id"]),
            )

    def finish(self, message):
        with self.db:
            self.db.execute("UPDATE processed SET done=1 WHERE message=?", (message,))

    def retry(self, message):
        with self.db:
            self.db.execute(
                "UPDATE processed SET attempts=attempts+1,retry_at=? WHERE message=?",
                (time.time() + 60, message),
            )

    def pending(self):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM processed WHERE done=0 AND attempts<5 AND retry_at<=? LIMIT 25",
                (time.time(),),
            )
        ]

    def archive_rules(self, guild, thread_ids):
        """Only completed AutoThread deliveries whose original rule is still enabled."""
        result = {}
        ids = list(thread_ids)
        for start in range(0, len(ids), 250):
            batch = ids[start : start + 250]
            rows = self.db.execute(
                "SELECT p.message,p.channel,r.id AS rule_id,r.archive FROM processed p "
                "JOIN rules r ON r.id=p.rule_id AND r.guild=p.guild AND r.channel=p.channel "
                "WHERE p.guild=? AND p.done=1 AND r.enabled=1 AND p.message IN ("
                + ",".join("?" for _ in batch)
                + ")",
                (guild, *batch),
            )
            result.update((row["message"], dict(row)) for row in rows)
        return result

    def notice(self, message):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO notices VALUES (?,?,?)",
                (message.id, message.channel.id, time.time() + 10),
            )

    def expired_notices(self):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM notices WHERE expires<=? LIMIT 25", (time.time(),)
            )
        ]

    def forget_notice(self, message):
        with self.db:
            self.db.execute("DELETE FROM notices WHERE message=?", (message,))

    def list(self, guild):
        return [
            dict(
                row,
                content=json.loads(row["content"]),
                starter_message=row["starter_message"] or STARTER,
            )
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


CONTENT = {
    "text": "Text messages",
    "image": "Pictures",
    "video": "Videos",
    "gif": "GIFs",
    "voice": "Voice Messages",
    "links": "Media Links / GIF Links",
}
ARCHIVES = {60: "1 hour", 1440: "24 hours", 4320: "3 days", 10080: "7 days"}


def defaults():
    return dict(
        content=[],
        require_attachment=False,
        media_only=False,
        ignore_webhooks=True,
        archive=1440,
        starter_message=STARTER,
        embed_color=None,
    )


def validate(settings):
    result = dict(defaults(), **settings)
    text = result["starter_message"]
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Enter a starter message between 1 and 2,000 characters.")
    if len(text.replace("{user}", "x" * 24).replace("{display_name}", "x" * 128)) > 4096:
        raise ValueError("Use fewer name placeholders so the welcome fits in an embed.")
    color = result["embed_color"]
    if color is not None and (type(color) is not int or color not in COLORS.values()):
        raise ValueError("Choose an embed color from the menu.")
    content = set(result["content"])
    if not content or not content <= CONTENT.keys():
        raise ValueError("Choose at least one content type.")
    if result["archive"] not in ARCHIVES:
        raise ValueError("Choose a supported archive duration.")
    if result["media_only"]:
        result["require_attachment"] = True
        if not content <= {"image", "video"}:
            raise ValueError("Media-only channels support Pictures and Videos only.")
    if result["require_attachment"] and not content & {"image", "video"}:
        raise ValueError("Require Attachment needs Pictures or Videos selected.")
    result["content"] = sorted(content)
    return result
