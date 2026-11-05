"""Custom timeout durations and fresh authorization for moderation actions."""

import re
from datetime import timedelta
from decimal import Decimal

import discord

MAX_TIMEOUT = 28 * 86400
PERMISSIONS = {"ban": "ban_members", "timeout": "moderate_members"}
TOKEN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(weeks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)",
    re.IGNORECASE,
)


def parse_duration(value):
    value = value.strip()
    if not value or len(value) > 100:
        raise ValueError("Enter a duration such as 30m, 2h, 3d 12h or 1 hour 30 minutes.")
    total, end = Decimal(0), 0
    for match in TOKEN.finditer(value):
        if value[end : match.start()].strip(" ,"):
            raise ValueError("Use seconds, minutes, hours, days or weeks, for example 2h 30m.")
        unit = match[2][0].lower()
        total += Decimal(match[1]) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        end = match.end()
    if end == 0 or value[end:].strip(" ,"):
        raise ValueError("Include a unit: 30m, 2h, 3d 12h or 1 hour 30 minutes.")
    if total < 1 or total > MAX_TIMEOUT or total != total.to_integral_value():
        raise ValueError(
            "Timeouts must be whole seconds, from 1 second up to Discord’s 28-day limit."
        )
    return timedelta(seconds=int(total))


def duration_label(duration):
    seconds = int(duration.total_seconds())
    parts = []
    for size, label in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        number, seconds = divmod(seconds, size)
        if number:
            parts.append(f"{number} {label}{'s' if number != 1 else ''}")
    return ", ".join(parts)


async def authorize(guild, actor_id, target_id, bot_id, action):
    if action not in PERMISSIONS:
        raise ValueError("Unknown moderation action.")
    if guild is None:
        raise ValueError("Use moderation controls inside a server.")
    try:
        # Fetch all three so cached roles/permissions cannot authorize an old confirmation.
        actor = await guild.fetch_member(actor_id)
        target = await guild.fetch_member(target_id)
        bot = await guild.fetch_member(bot_id)
    except discord.NotFound:
        raise ValueError(
            "The moderator, target member or KY BOT is no longer in this server."
        ) from None
    permission = PERMISSIONS[action]
    if not getattr(actor.guild_permissions, permission):
        raise ValueError("You need " + permission.replace("_", " ").title() + " to do that.")
    if not getattr(bot.guild_permissions, permission):
        raise ValueError("KY BOT needs " + permission.replace("_", " ").title() + " to do that.")
    if target_id in {actor_id, bot_id, guild.owner_id}:
        raise ValueError("You cannot target yourself, KY BOT or the server owner.")
    if actor_id != guild.owner_id and actor.top_role <= target.top_role:
        raise ValueError("Your highest role must be above the member’s highest role.")
    if bot.top_role <= target.top_role:
        raise ValueError("Move KY BOT’s role above the member’s highest role before continuing.")
    if action == "timeout" and (target.bot or target.guild_permissions.administrator):
        raise ValueError("Bots and members with Administrator cannot be timed out.")
    return actor, target


async def apply(guild, actor_id, target_id, bot_id, action, *, duration=None, reason=""):
    actor, target = await authorize(guild, actor_id, target_id, bot_id, action)
    reason = reason.strip()
    if len(reason) > 400:
        raise ValueError("Keep the reason to 400 characters or fewer.")
    audit = f"KY BOT | Moderator {actor.id} | {reason or 'No reason provided'}"
    if action == "ban":
        # Banning alone does not authorize deleting the member's message history.
        await target.ban(reason=audit, delete_message_seconds=0)
        return target, None
    if duration is None or not 1 <= duration.total_seconds() <= MAX_TIMEOUT:
        raise ValueError("Enter a timeout between 1 second and 28 days.")
    until = discord.utils.utcnow() + duration
    await target.timeout(until, reason=audit)
    return target, until
