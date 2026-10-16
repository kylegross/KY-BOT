"""Guild-scoped event logs; raw message events also cover uncached messages."""

import asyncio
import logging
from importlib.resources import files
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from ky_bot.bot import KYBot

log = logging.getLogger(__name__)
MEMBER_EVENTS = {"MEMBER JOINED", "MEMBER LEFT"}
MEMBER_COLOURS = {
    "MEMBER JOINED": 0xC9DDF0, "MEMBER LEFT": 0xC9DDF0,
}
MEMBER_DESCRIPTIONS = {
    "MEMBER JOINED": "A new member joined the server.",
    "MEMBER LEFT": "A member left the server.",
}


def text(value: str, limit: int = 1000) -> str:
    value = discord.utils.escape_markdown(value).replace("\x00", "")
    return (value[: limit - 1] + "…") if len(value) > limit else value or "(No text)"


def person(user) -> str:
    return f"{text(str(user), 200)} · `{user.id}`"


def member_person(user) -> str:
    return member_reference(user.id)


def member_reference(user_id) -> str:
    return f"<@{user_id}> · User ID: `{user_id}`" if user_id is not None else "Unknown"


class Activity(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot
        self.invite_snapshots = {}
        self.invite_locks = {}

    def invite_lock(self, guild_id):
        return self.invite_locks.setdefault(guild_id, asyncio.Lock())

    async def read_invites(self, guild):
        if not guild.me or not guild.me.guild_permissions.manage_guild:
            return None
        try:
            return {i.code: (i.uses or 0, i.inviter) for i in await guild.invites()}
        except discord.HTTPException:
            log.warning("Invite attribution unavailable in guild %s", guild.id)
            return None

    async def prime_invites(self, guild):
        async with self.invite_lock(guild.id):
            snapshot = await self.read_invites(guild)
            if snapshot is None:
                self.invite_snapshots.pop(guild.id, None)
            else:
                self.invite_snapshots[guild.id] = snapshot

    @commands.Cog.listener()
    async def on_ready(self):
        # Reconnects establish a fresh baseline; offline joins cannot be attributed.
        for guild in self.bot.guilds:
            await self.prime_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self.prime_invites(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.invite_snapshots.pop(guild.id, None)
        self.invite_locks.pop(guild.id, None)

    async def join_invite(self, guild):
        async with self.invite_lock(guild.id):
            before = self.invite_snapshots.get(guild.id)
            after = await self.read_invites(guild)
            if after is None:
                self.invite_snapshots.pop(guild.id, None)
                return None
            self.invite_snapshots[guild.id] = after
            if before is None:
                return None
            changes = [(code, uses - before[code][0], inviter)
                       for code, (uses, inviter) in after.items()
                       if code in before and uses > before[code][0]]
            # Concurrent/batched joins, expired single-use links and vanity links
            # cannot be assigned safely to a particular member.
            if len(changes) != 1 or changes[0][1] != 1:
                return None
            _, _, inviter = changes[0]
            return inviter.id if inviter else None

    async def emit(
        self,
        guild_id: int | None,
        title: str,
        fields: dict[str, str],
        *,
        source_channel_id: int | None = None,
        subject=None,
    ) -> None:
        if guild_id is None or title in {"INVITE CREATED", "INVITE DELETED"}:
            return
        checklists = getattr(self.bot, "checklists", None)
        if (
            checklists is not None
            and title in {"MESSAGE DELETED", "MESSAGES BULK DELETED", "MESSAGE EDITED"}
            and checklists.store.is_checklist_channel(guild_id, source_channel_id)
        ):
            return
        settings = await self.bot.server_settings.repository.get(guild_id)
        member_event = title in MEMBER_EVENTS
        destination = settings.member_log_channel_id if member_event else settings.log_channel_id
        if destination is None or source_channel_id in {
            settings.log_channel_id, settings.member_log_channel_id
        } - {None}:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            channel = await self.bot.server_settings.validate_channel(
                guild, destination, **({"require_images": True} if member_event else {})
            )
            embed = discord.Embed(
                title=f"\\*ੈ𑁍  {title}  𑁍ੈ\\*",
                colour=MEMBER_COLOURS.get(title, 0xC9DDF0),
                description=MEMBER_DESCRIPTIONS.get(title),
                timestamp=discord.utils.utcnow(),
            )
            for name, value in fields.items():
                embed.add_field(name=name, value=value[:1024] or "(Unavailable)", inline=False)
            if member_event and subject is not None:
                avatar = getattr(subject, "display_avatar", None)
                if avatar:
                    embed.set_thumbnail(url=str(avatar.url))
            if member_event:
                embed.set_footer(text="KY BOT", icon_url="attachment://ky_bot_logo.png")
                logo = discord.File(
                    files("ky_bot.design").joinpath(
                        "assets", "footer", "ky_member_log_logo.png"
                    ).open("rb"),
                    filename="ky_bot_logo.png",
                )
                try:
                    await channel.send(
                        embed=embed, file=logo, allowed_mentions=discord.AllowedMentions.none()
                    )
                finally:
                    logo.close()
            else:
                embed.set_footer(text="KY BOT · SERVER ACTIVITY")
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (ValueError, discord.HTTPException):
            log.warning("Activity log delivery failed in guild %s", guild_id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        message = payload.cached_message
        fields = {"Channel": f"<#{payload.channel_id}>", "Message ID": str(payload.message_id)}
        if message:
            fields["Author"] = person(message.author)
            fields["Deleted text"] = text(message.content)
            if message.attachments:
                fields["Attachments"] = text(", ".join(a.filename for a in message.attachments))
        else:
            fields["Deleted text"] = "Unavailable — this message was not in the bot's cache."
        await self.emit(
            payload.guild_id, "MESSAGE DELETED", fields, source_channel_id=payload.channel_id
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        lines = [
            f"{person(m.author)}: {text(m.content, 160)}"
            for m in sorted(payload.cached_messages, key=lambda m: m.id)[:5]
        ]
        await self.emit(
            payload.guild_id,
            "MESSAGES BULK DELETED",
            {
                "Channel": f"<#{payload.channel_id}>",
                "Deleted messages": str(len(payload.message_ids)),
                "Cached text (up to 5 messages)": text("\n".join(lines))
                if lines
                else "Unavailable",
                "Message IDs": text(", ".join(str(i) for i in sorted(payload.message_ids))),
            },
            source_channel_id=payload.channel_id,
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Embed loading and pin changes do not count as edits to a message's text/files.
        if "content" not in payload.data and "attachments" not in payload.data:
            return
        before = payload.cached_message
        raw_author = payload.data.get("author", {})
        bot_user = getattr(self.bot, "user", None)
        author_id = before.author.id if before else raw_author.get("id")
        if bot_user is not None and str(author_id) == str(bot_user.id):
            return
        # Checklist navigation reuploads artwork, but is not a user message edit.
        checklists = getattr(self.bot, "checklists", None)
        if checklists is not None:
            board = checklists.store.board(payload.channel_id)
            if (
                board
                and board["guild"] == payload.guild_id
                and board["message"] == payload.message_id
            ):
                return
        content = payload.data.get("content")
        attachments = payload.data.get("attachments")
        if (
            before
            and (content is None or content == before.content)
            and (
                attachments is None
                or [a["id"] for a in attachments] == [str(a.id) for a in before.attachments]
            )
        ):
            return
        author = before.author if before else None
        fields = {
            "Channel": f"<#{payload.channel_id}>",
            "Message": f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}",
            "Author": person(author)
            if author
            else text(
                f"{raw_author.get('username', 'Unavailable')} · {raw_author.get('id', 'Unknown')}"
            ),
            "Before": text(before.content) if before else "Unavailable — message was not cached.",
            "After": text(content)
            if content is not None
            else "Text unchanged; attachments updated.",
        }
        if attachments is not None:
            fields["Attachments after edit"] = text(
                ", ".join(a.get("filename", "File") for a in attachments)
            )
        await self.emit(
            payload.guild_id, "MESSAGE EDITED", fields, source_channel_id=payload.channel_id
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        inviter_id = await self.join_invite(member.guild)
        await self.bot.server_settings.repository.save_member_inviter(
            member.guild.id, member.id, inviter_id
        )
        await self.emit(
            member.guild.id,
            "MEMBER JOINED",
            {
                "Member": member_person(member),
                "Account created": discord.utils.format_dt(member.created_at, "F"),
                "Invited by": member_reference(inviter_id),
            },
            subject=member,
        )

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        inviter_id = await self.bot.server_settings.repository.get_member_inviter(
            payload.guild_id, payload.user.id
        )
        await self.emit(
            payload.guild_id, "MEMBER LEFT",
            {"Member": member_person(payload.user), "Invited by": member_reference(inviter_id)},
            subject=payload.user,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        old, new = {r.id: r for r in before.roles}, {r.id: r for r in after.roles}
        added, removed = new.keys() - old.keys(), old.keys() - new.keys()
        if not added and not removed:
            return
        fields = {"Member": person(after)}
        if added:
            fields["Roles added"] = text(", ".join(f"{new[i].name} (`{i}`)" for i in sorted(added)))
        if removed:
            fields["Roles removed"] = text(
                ", ".join(f"{old[i].name} (`{i}`)" for i in sorted(removed))
            )
        await self.emit(after.guild.id, "MEMBER ROLES CHANGED", fields)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self.emit(role.guild.id, "ROLE CREATED", {"Role": person(role)})

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.emit(role.guild.id, "ROLE DELETED", {"Role": person(role)})

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        fields = {"Role": person(after)}
        for label, attribute in (
            ("Name", "name"),
            ("Color", "colour"),
            ("Permissions", "permissions"),
            ("Position", "position"),
            ("Show separately", "hoist"),
            ("Mentionable", "mentionable"),
        ):
            old, new = getattr(before, attribute), getattr(after, attribute)
            if old != new:
                fields[label] = text(f"{getattr(old, 'value', old)} → {getattr(new, 'value', new)}")
        if len(fields) > 1:
            await self.emit(after.guild.id, "ROLE UPDATED", fields)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.emit(guild.id, "MEMBER BANNED", {"Member": person(user)})

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        await self.emit(guild.id, "MEMBER UNBANNED", {"Member": person(user)})

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild:
            async with self.invite_lock(invite.guild.id):
                snapshot = self.invite_snapshots.get(invite.guild.id)
                if snapshot is not None:
                    snapshot.setdefault(
                        invite.code, (getattr(invite, "uses", 0) or 0, invite.inviter)
                    )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild:
            async with self.invite_lock(invite.guild.id):
                self.invite_snapshots.get(invite.guild.id, {}).pop(invite.code, None)


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(Activity(bot))
