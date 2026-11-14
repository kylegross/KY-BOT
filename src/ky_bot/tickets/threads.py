"""Private ticket thread membership and parent-channel permissions."""

import discord


def validate_parent(channel, member=None):
    if not isinstance(channel, discord.TextChannel):
        raise ValueError(
            "Choose a text channel for the public ticket panel and its private threads."
        )
    permissions = channel.permissions_for(member or channel.guild.me)
    required = ("view_channel", "send_messages_in_threads", "read_message_history")
    if member is None:
        required += (
            "send_messages",
            "create_private_threads",
            "manage_threads",
            "attach_files",
            "embed_links",
        )
    missing = [key.replace("_", " ") for key in required if not getattr(permissions, key)]
    if missing:
        subject = "KY BOT needs" if member is None else "This member or staff role needs"
        raise ValueError(subject + ": " + ", ".join(missing) + " in the ticket panel channel.")
    return channel


async def sync_access(thread, ticket, *, closed=False, pending=False):
    guild = thread.guild
    if not guild.chunked:
        await guild.chunk(cache=True)
    role_ids = set(
        ticket["settings"]["support_roles"]
        + ticket["settings"]["manager_roles"]
        + ticket["settings"].get("approver_roles", [])
    )
    desired = {
        member.id: member
        for member in guild.members
        if not member.bot and role_ids.intersection(role.id for role in member.roles)
    }
    if not (closed or pending):
        for member_id in [ticket["owner"]] + ticket["guests"]:
            member = guild.get_member(member_id)
            if member:
                validate_parent(thread.parent, member)
                desired[member.id] = member
    current = {member.id for member in await thread.fetch_members()}
    for member_id in current - desired.keys() - {guild.me.id}:
        await thread.remove_user(discord.Object(id=member_id))
    for member_id in desired.keys() - current:
        await thread.add_user(desired[member_id])


async def notify_owner(guild, ticket, text):
    member = guild.get_member(ticket["owner"])
    if member:
        try:
            await member.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
