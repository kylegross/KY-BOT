"""Guild-scoped event logs; raw message events also cover uncached messages."""

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from ky_bot.bot import KYBot

log = logging.getLogger(__name__)


def text(value: str, limit: int = 1000) -> str:
    value = discord.utils.escape_markdown(value).replace("\x00", "")
    return (value[: limit - 1] + "…") if len(value) > limit else value or "(No text)"


def person(user) -> str:
    return f"{text(str(user), 200)} · `{user.id}`"


class Activity(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot

    async def emit(
        self,
        guild_id: int | None,
        title: str,
        fields: dict[str, str],
        *,
        source_channel_id: int | None = None,
    ) -> None:
        if guild_id is None:
            return
        settings = await self.bot.server_settings.repository.get(guild_id)
        if settings.log_channel_id is None or settings.log_channel_id == source_channel_id:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            channel = await self.bot.server_settings.validate_channel(
                guild, settings.log_channel_id
            )
            embed = discord.Embed(
                title=f"\\*ੈ𑁍  {title}  𑁍ੈ\\*",
                colour=0xC9DDF0,
                timestamp=discord.utils.utcnow(),
            )
            for name, value in fields.items():
                embed.add_field(name=name, value=value[:1024] or "(Unavailable)", inline=False)
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
        raw_author = payload.data.get("author", {})
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
        await self.emit(
            member.guild.id,
            "MEMBER JOINED",
            {
                "Member": person(member),
                "Account created": discord.utils.format_dt(member.created_at, "F"),
            },
        )

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        await self.emit(payload.guild_id, "MEMBER LEFT", {"Member": person(payload.user)})

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
            fields = {"Code": text(invite.code), "Channel": f"<#{invite.channel.id}>"}
            if invite.inviter:
                fields["Created by"] = person(invite.inviter)
            await self.emit(invite.guild.id, "INVITE CREATED", fields)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild:
            await self.emit(
                invite.guild.id,
                "INVITE DELETED",
                {"Code": text(invite.code), "Channel": f"<#{invite.channel.id}>"},
            )


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(Activity(bot))
