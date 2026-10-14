import json
import sqlite3
import time
import uuid
from pathlib import Path

CONTENT = {
    "all": "All posts",
    "text": "Text",
    "image": "Photos",
    "video": "Videos",
    "gif": "GIFs",
    "links": "Media links",
    "voice": "Voice messages",
}
FILTERS = {
    "bots": "Include Bots",
    "webhooks": "Include Webhooks",
    "threads": "Include Thread Replies",
}


def defaults():
    return dict(emojis=[], content=["all"], bots=False, webhooks=False, threads=False)


def validate(settings):
    result = {k: settings[k] for k in defaults()}
    if not 1 <= len(result["emojis"]) <= 20 or len(set(result["emojis"])) != len(result["emojis"]):
        raise ValueError("Choose 1–20 distinct emojis.")
    if not result["content"] or not set(result["content"]) <= CONTENT.keys():
        raise ValueError("Choose content types.")
    if "all" in result["content"]:
        result["content"] = ["all"]
    for k in FILTERS:
        result[k] = bool(result[k])
    return result


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rules (guild INTEGER,channel INTEGER,
                settings TEXT NOT NULL, enabled INTEGER NOT NULL,revision TEXT NOT NULL,
                starts REAL NOT NULL,error TEXT NOT NULL DEFAULT '',PRIMARY KEY(guild,channel));
            CREATE TABLE IF NOT EXISTS jobs (message INTEGER PRIMARY KEY,guild INTEGER,
                channel INTEGER,parent INTEGER,revision TEXT,emojis TEXT,
                position INTEGER DEFAULT 0,done INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,
                retry REAL DEFAULT 0,created REAL);
        """)

    def list(self, guild):
        return [
            dict(r, settings=json.loads(r["settings"]))
            for r in self.db.execute("SELECT * FROM rules WHERE guild=? ORDER BY channel", (guild,))
        ]

    def get(self, guild, channel):
        return next((r for r in self.list(guild) if r["channel"] == channel), None)

    def save(self, guild, channels, settings, expected):
        settings = validate(settings)
        if not channels:
            raise ValueError("Select at least one channel.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for cid in channels:
                old = self.get(guild, cid)
                if (old["revision"] if old else None) != expected.get(cid):
                    raise ValueError(
                        "A selected rule changed. Cancel Setup and reopen Configure Reactions."
                    )
            for cid in channels:
                old = self.get(guild, cid)
                self.db.execute(
                    "INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?,?)",
                    (
                        guild,
                        cid,
                        json.dumps(settings),
                        old["enabled"] if old else 1,
                        uuid.uuid4().hex,
                        old["starts"] if old else time.time(),
                        "",
                    ),
                )

    def action(self, guild, expected, action):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for cid, revision in expected.items():
                old = self.get(guild, cid)
                if not old or old["revision"] != revision:
                    raise ValueError("A rule changed. Refresh the menu and try again.")
            for cid in expected:
                if action == "remove":
                    self.db.execute("DELETE FROM rules WHERE guild=? AND channel=?", (guild, cid))
                else:
                    self.db.execute(
                        "UPDATE rules SET enabled=?,revision=?,error=? WHERE guild=? AND channel=?",
                        (int(action == "on"), uuid.uuid4().hex, "", guild, cid),
                    )

    def enqueue(self, message, rule):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO jobs(message,guild,channel,parent,revision,emojis,created) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    message.id,
                    message.guild.id,
                    message.channel.id,
                    rule["channel"],
                    rule["revision"],
                    json.dumps(rule["settings"]["emojis"]),
                    time.time(),
                ),
            )

    def jobs(self):
        return [
            dict(r, emojis=json.loads(r["emojis"]))
            for r in self.db.execute(
                "SELECT * FROM jobs WHERE done=0 AND retry<=? ORDER BY created LIMIT 20",
                (time.time(),),
            )
        ]

    def update_job(self, mid, **values):
        assert set(values) <= {"position", "done", "attempts", "retry"}
        with self.db:
            self.db.execute(
                "UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE message=?",
                (*values.values(), mid),
            )

    def error(self, rule, text):
        with self.db:
            self.db.execute(
                "UPDATE rules SET error=? WHERE guild=? AND channel=? AND revision=?",
                (text[:400], rule["guild"], rule["channel"], rule["revision"]),
            )

    def prune(self):
        with self.db:
            self.db.execute(
                "DELETE FROM jobs WHERE done=1 AND created<?", (time.time() - 7 * 86400,)
            )
