"""Shared, recoverable reply threads without exposing or subscribing anonymous authors."""

import discord


def thread_permissions(channel, member):
    permissions = channel.permissions_for(member)
    if not (
        permissions.view_channel
        and permissions.read_message_history
        and permissions.attach_files
        and permissions.send_messages_in_threads
    ):
        raise ValueError(
            "KY BOT needs View Channel, Read Message History, Attach Files and "
            "Send Messages in Threads to publish anonymous replies."
        )


async def reply_thread(guild, channel, root, store, *, label):
    """Caller holds a per-root lock; message thread IDs recover across restarts/races."""
    if root["guild"] != guild.id or root["channel"] != channel.id or root["parent"]:
        raise ValueError("The original post is unavailable.")
    message_id = root["data"].get("message")
    if root["state"] != "published" or not message_id:
        raise ValueError("The original post has not been published.")
    thread_permissions(channel, guild.me)
    thread_id = root["data"].get("thread") or message_id
    thread = guild.get_thread(thread_id)
    if thread is None:
        try:
            thread = await guild.fetch_channel(thread_id)
        except discord.NotFound:
            thread = None
    if thread is None:
        if not channel.permissions_for(guild.me).create_public_threads:
            raise ValueError("KY BOT needs Create Public Threads in the original post’s channel.")
        try:
            thread = await channel.get_partial_message(message_id).create_thread(
                name=f"{label} #{root['data']['number']} · Replies"[:100],
                auto_archive_duration=1440,
                reason="Anonymous replies to the original post",
            )
        except discord.HTTPException:
            # Another process or a lost success response may have created this same thread.
            thread = await guild.fetch_channel(message_id)
    if (
        not isinstance(thread, discord.Thread)
        or thread.guild.id != guild.id
        or (thread.parent_id != channel.id or thread.id != message_id)
    ):
        raise ValueError("The reply thread does not belong to this original post.")
    if thread.locked:
        raise ValueError(
            "The reply thread is locked. A moderator must reopen it before replies publish."
        )
    thread_permissions(thread, guild.me)
    if thread.archived:
        thread = await thread.edit(archived=False, reason="New approved anonymous reply")
    if root["data"].get("thread") != thread.id:
        await store.transition(root["id"], {"published"}, "published", thread=thread.id)
    # Never add a user to this thread on their behalf: it would expose their identity.
    return thread


async def message_destination(guild, post):
    """Locate a stored reply for moderation, including replies published before threading."""
    channel_id = post["data"].get("message_channel", post["channel"])
    channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
    if channel is None:
        channel = await guild.fetch_channel(channel_id)
    if channel.guild.id != guild.id or (
        channel.id != post["channel"] and getattr(channel, "parent_id", None) != post["channel"]
    ):
        raise ValueError("This message is outside its original channel.")
    return channel


async def reply_reference(store, post, channel):
    """Reference the advice/reply being answered when it is in this same thread."""
    token = post["data"].get("reply_to")
    target = await store.post(token) if token else None
    if (
        target
        and target["guild"] == post["guild"]
        and target["state"] == "published"
        and (target["data"].get("message_channel", target["channel"]) == channel.id)
        and target["data"].get("message")
    ):
        return channel.get_partial_message(target["data"]["message"])
    return None
