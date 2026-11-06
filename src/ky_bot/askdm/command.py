"""Consent requests, durable deliveries and Command Center integration."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

import discord
from discord.ext import commands, tasks

from ky_bot.errors import send_private
from ky_bot.log_style import send_log, style_log
from ky_bot.views.menu_context import menu_parent

from .store import KINDS, Store
from .views import (
    Action,
    Admin,
    Confirmation,
    Decline,
    Form,
    Inbox,
    Panel,
    PublicPanel,
    RequestPanel,
    safe,
    uploads,
)

log = logging.getLogger(__name__)


def role_allowed(member, config, direction):
    roles = {r.id for r in member.roles}
    if roles & set(config[f"blocked_{direction}_roles"]):
        return False
    allowed = config[f"{direction}_roles"]
    return not allowed or bool(roles & set(allowed))


class AskDM(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_lock = asyncio.Lock()
        self.request_lock = asyncio.Lock()
        self.sticky_due = {}
        self.sticky_scan = 0

    async def cog_load(self):
        self.store = await Store.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.bot.add_dynamic_items(Action)
        self.entry_view = PublicPanel(self)
        self.bot.add_view(self.entry_view)
        self.deliver.start()
        self.sticky.start()

    async def cog_unload(self):
        self.sticky.cancel()
        sticky_task = self.sticky.get_task()
        if sticky_task:
            try:
                await sticky_task
            except asyncio.CancelledError:
                pass
        self.deliver.cancel()
        task = self.deliver.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.bot.remove_dynamic_items(Action)
        self.entry_view.stop()
        await self.store.close()

    async def channel(self, cid, guild):
        channel = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild:
            raise ValueError("Choose a text channel in this server.")
        return channel

    async def check_channel(self, channel, *, logs=False):
        p = channel.permissions_for(channel.guild.me)
        required = [
            "view_channel",
            "send_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
        ]
        if not logs:
            required += ["create_private_threads", "send_messages_in_threads", "manage_threads"]
        missing = [name.replace("_", " ") for name in required if not getattr(p, name)]
        if missing:
            raise ValueError("KY BOT needs these channel permissions: " + ", ".join(missing) + ".")
        if logs:
            # Discord channel visibility is the authority for staff log privacy.
            roles = [
                r
                for r in channel.guild.roles
                if not r.is_default()
                and not r.managed
                and not (r.permissions.administrator or r.permissions.manage_guild)
            ]
            if channel.permissions_for(channel.guild.default_role).view_channel or any(
                channel.permissions_for(role).view_channel for role in roles
            ):
                raise ValueError(
                    "Choose a staff-only log channel. Hide it from @everyone and "
                    "non-staff roles; grant staff Manage Server or Administrator."
                )
            for target in channel.overwrites:
                if isinstance(target, discord.Member) and not target.bot:
                    if channel.permissions_for(target).view_channel and not (
                        target.guild_permissions.administrator
                        or target.guild_permissions.manage_guild
                    ):
                        raise ValueError(
                            "The log channel has a member override allowing "
                            "non-staff access. Choose a staff-only channel."
                        )

    async def validate_channels(self, guild, config):
        if not config["channel"] or not config["log_channel"]:
            raise ValueError("Choose request and staff log channels first.")
        if config["channel"] == config["log_channel"]:
            raise ValueError("Use a separate staff log channel.")
        await self.check_channel(await self.channel(config["channel"], guild.id))
        await self.check_channel(await self.channel(config["log_channel"], guild.id), logs=True)

    async def eligible(self, interaction, target=None):
        if interaction.guild is None:
            raise ValueError("Start requests from your server's Ask to DM/FR panel.")
        revision, config = await self.store.config(interaction.guild_id)
        if not config["enabled"] or interaction.channel_id != config["channel"]:
            raise ValueError("Use the enabled Ask to DM/FR panel in this server's request channel.")
        sender = await interaction.guild.fetch_member(interaction.user.id)
        if not role_allowed(sender, config, "sender"):
            raise ValueError("Your roles do not allow sending requests in this server.")
        recipient = None
        if target is not None:
            if target == sender.id:
                raise ValueError("Choose someone other than yourself.")
            try:
                recipient = await interaction.guild.fetch_member(target)
            except discord.NotFound:
                raise ValueError("That member is no longer in this server.") from None
            if recipient.bot:
                raise ValueError("Choose a person rather than a bot.")
            if not role_allowed(recipient, config, "recipient"):
                raise ValueError("That member's roles do not allow receiving this request.")
        await self.validate_channels(interaction.guild, config)
        return revision, config, sender, recipient

    async def appearance(self, guild_id, view=None):
        service = getattr(self.bot, "server_settings", None)
        profile = None
        if guild_id and service is not None:
            from ky_bot.design.menu_design import profile_for

            _, profiles = await service.repository.get_designs(guild_id)
            if "askdm" in profiles:
                profile = profile_for(profiles, "askdm")
        if isinstance(view, Admin):
            from ky_bot.design.menu_design import default_profile

            profile = default_profile("settings:askdm")
        if profile is not None and view is not None:
            from ky_bot.design.collection import COLOURS

            view.panel.accent_colour = int(COLOURS[profile["style"]][1:], 16)
        if isinstance(view, Admin):
            return await asyncio.to_thread(uploads, profile, view)
        return await asyncio.to_thread(uploads, profile) if profile else uploads()

    async def refresh_design(self, guild):
        async with self.config_lock:
            _, config = await self.store.config(guild.id)
            if not config["panel"]:
                return
            channel = await self.channel(config["panel_channel"] or config["channel"], guild.id)
            view = PublicPanel(self)
            attachments = await self.appearance(guild.id, view)
            try:
                await channel.get_partial_message(config["panel"]).edit(
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                pass
            finally:
                for file in attachments:
                    file.close()

    async def send_panel(self, destination, view, *, private=False, edit=False):
        guild_id = (
            getattr(destination, "guild_id", None)
            or getattr(getattr(destination, "guild", None), "id", None)
            or getattr(view, "guild", None)
        )
        attachments = await self.appearance(guild_id, view)
        try:
            if edit:
                return await destination.edit_original_response(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return await destination.send(
                view=view,
                files=attachments,
                **({"ephemeral": True} if private else {}),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for file in attachments:
                file.close()

    async def open_form(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.eligible(interaction)
            await self.send_panel(
                interaction, Form(self, interaction.user.id, interaction.guild_id), edit=True
            )
        except (ValueError, discord.HTTPException) as error:
            await send_private(interaction, str(error))

    async def open_inbox(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        # Disabling new requests or changing role restrictions must not remove withdrawal rights.
        rows = await self.store.inbox(interaction.guild_id, interaction.user.id)
        await self.send_panel(
            interaction, Inbox(self, interaction.guild_id, interaction.user.id, rows), edit=True
        )

    async def prepare(self, interaction, form):
        if form.target is None:
            raise ValueError("Choose a member first.")
        await self.eligible(interaction, form.target)
        token = await self.store.draft(
            interaction.guild_id, interaction.user.id, form.target, form.kind, form.reason
        )
        await self.send_panel(interaction, Confirmation(token, form.target, form.kind), edit=True)
        form.stop()

    async def show_request(self, interaction, request):
        if (
            not request
            or interaction.user.id not in (request["sender"], request["recipient"])
            or interaction.guild_id not in (None, request["guild"])
        ):
            raise ValueError("This request belongs to other members.")
        await self.send_panel(
            interaction,
            RequestPanel(request, json.loads(request["data"]), actor=interaction.user.id),
            edit=True,
        )

    async def action(self, i, token, action):
        if action in {"confirm", "discard"}:
            draft = await self.store.get_draft(token)
            if not draft or (i.user.id, i.guild_id) != (draft["sender"], draft["guild"]):
                raise ValueError("This confirmation belongs to another member or has expired.")
            await i.response.defer()
            if action == "discard":
                await self.store.cancel_draft(token, i.user.id)
                return await self.send_panel(i, Form(self, i.user.id, i.guild_id), edit=True)
            async with self.request_lock:
                revision, _, sender, recipient = await self.eligible(i, draft["recipient"])
                await self.store.reserve(
                    token, i.user.id, revision, [sender.display_name, recipient.display_name]
                )
            view = Panel()
            view.text(
                "**REQUEST RECORDED**\nYour request is queued for private delivery. "
                "Wait for an explicit acceptance before contacting them. "
                "Use My requests to review or cancel it."
            )
            view.finish()
            await self.send_panel(i, view, edit=True)
            return
        request = await self.store.get(token)
        if not request or i.guild_id not in (None, request["guild"]):
            raise ValueError("This request is not available in this server.")
        owner = request["sender"] if action == "cancel" else request["recipient"]
        if i.user.id != owner:
            raise ValueError("Only the appropriate request participant can use this control.")
        if action == "deny":
            if request["state"] != "pending":
                raise ValueError("This request has already been handled.")
            await i.response.send_modal(Decline(self, token))
            return
        await i.response.defer(ephemeral=True, thinking=True)
        await self.decide(
            i, token, {"accept": "accepted", "cancel": "cancelled", "revoke": "revoked"}[action]
        )

    async def decide(self, i, rid, state, reason=""):
        request = await self.store.get(rid)
        if not request or i.guild_id not in (None, request["guild"]):
            raise ValueError("This request is unavailable here.")
        if state == "accepted":
            guild = self.bot.get_guild(request["guild"])
            if guild is None:
                raise ValueError("This server is unavailable.")
            _, config = await self.store.config(guild.id)
            try:
                sender = await guild.fetch_member(request["sender"])
                recipient = await guild.fetch_member(request["recipient"])
            except discord.NotFound:
                raise ValueError("Both members must still be in this server.") from None
            if not (
                role_allowed(sender, config, "sender")
                and role_allowed(recipient, config, "recipient")
            ):
                raise ValueError(
                    "Current role restrictions no longer allow accepting this request."
                )
        await self.store.transition(rid, i.user.id, state, reason)
        view = Panel()
        view.text(
            f"**{state.upper()}**\nYour choice has been recorded. "
            "Both members will receive a private update; staff logs are queued."
        )
        view.finish()
        await self.send_panel(i, view, edit=True)

    async def open_menu(self, i, *, command_center=None):
        command_center = menu_parent(command_center, i)
        if command_center is None or not await command_center.interaction_check(i):
            return
        await i.response.defer()
        await self.admin(i, command_center)

    async def admin(self, i, parent, *, picker=None, notice=None):
        parent = menu_parent(parent, i)
        revision, config = await self.store.config(parent.guild_id)
        view = Admin(self, parent, revision, config, picker=picker, notice=notice)
        attachments = await self.appearance(parent.guild_id, view)
        try:
            if parent.panel_manager:
                await parent.panel_manager.edit(parent, i, view, attachments)
            else:
                await i.edit_original_response(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        finally:
            for file in attachments:
                file.close()

    async def publish(self, guild):
        async with self.config_lock:
            _, config = await self.store.config(guild.id)
            await self.validate_channels(guild, config)
            channel = await self.channel(config["channel"], guild.id)
            view = PublicPanel(self)
            message = None
            old_message = None
            if config["panel"]:
                try:
                    old_channel = await self.channel(
                        config["panel_channel"] or config["channel"], guild.id
                    )
                    old_message = await old_channel.fetch_message(config["panel"])
                    if old_channel.id == channel.id:
                        message = old_message
                except discord.NotFound:
                    pass
            if message:
                attachments = await self.appearance(guild.id, view)
                try:
                    await message.edit(
                        view=view,
                        attachments=attachments,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                finally:
                    for file in attachments:
                        file.close()
            else:
                message = await self.send_panel(channel, view)
            try:
                await self.store.save_panel(guild.id, message.id, channel.id, config["panel"])
            except BaseException:
                if old_message is None or old_message.id != message.id:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        log.warning("Could not remove unsaved member panel in guild %s", guild.id)
                raise
            await self.clean_old_panels(guild.id)
        await self.bump_sticky(guild.id)

    async def clean_old_panels(self, guild_id):
        _, config = await self.store.config(guild_id)
        for old in config["retired_panels"]:
            # Never delete the active menu, even if a stale receipt points at it.
            if (old["message"], old["channel"]) == (config["panel"], config["panel_channel"]):
                await self.store.forget_panel(guild_id, old)
                continue
            try:
                channel = await self.channel(old["channel"], guild_id)
                await channel.get_partial_message(old["message"]).delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.warning("Old Ask to DM/FR menu deletion will retry in guild %s", guild_id)
                return False
            await self.store.forget_panel(guild_id, old)
        return True

    async def bump_sticky(self, guild_id):
        async with self.config_lock:
            if not await self.clean_old_panels(guild_id):
                return
            _, config = await self.store.config(guild_id)
            if not (config["enabled"] and config["public"] and config["panel"]):
                return
            channel = await self.channel(config["channel"], guild_id)
            latest = [message async for message in channel.history(limit=1)]
            if latest and latest[0].id == config["panel"]:
                return
            message = await self.send_panel(channel, PublicPanel(self))
            try:
                await self.store.save_panel(
                    guild_id, message.id, channel.id, config["panel"], sticky=True
                )
            except BaseException:
                # A concurrent settings change must not leave a second untracked menu.
                try:
                    await message.delete()
                except discord.HTTPException:
                    log.warning("Could not remove an unsaved request menu in guild %s", guild_id)
                raise
            # The new ID is durable before removing the old copy. Failures are retried on restart.
            await self.clean_old_panels(guild_id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or isinstance(message.channel, discord.Thread):
            return
        if message.author.id == self.bot.user.id:
            # Recognize our entry panel by its stable button ID, even before its new ID is saved.
            if any("ky:ask:entry" in json.dumps(c.to_dict()) for c in message.components):
                return
        _, config = await self.store.config(message.guild.id)
        if (
            config["enabled"]
            and config["public"]
            and config["panel"]
            and message.channel.id == config["channel"]
        ):
            self.sticky_due.setdefault(message.guild.id, time.monotonic() + 1.5)

    async def sticky_tick(self):
        now = time.monotonic()
        if now >= self.sticky_scan:
            for guild in await self.store.sticky_guilds():
                self.sticky_due.setdefault(guild, now)
            self.sticky_scan = now + 30
        for guild, due in list(self.sticky_due.items()):
            if due > now:
                continue
            del self.sticky_due[guild]
            try:
                await self.bump_sticky(guild)
            except (discord.HTTPException, ValueError):
                log.warning("Ask to DM/FR sticky menu queued for retry in guild %s", guild)
                self.sticky_due.setdefault(guild, time.monotonic() + 10)

    @tasks.loop(seconds=2)
    async def sticky(self):
        try:
            await self.sticky_tick()
        except Exception:
            log.exception("Ask to DM/FR sticky menu cycle failed; will retry")

    @sticky.before_loop
    async def sticky_ready(self):
        await self.bot.wait_until_ready()

    def record(self, request, data, state):
        colours = dict(
            pending=0xBDC3CA,
            accepted=0x57F287,
            denied=0xED4245,
            cancelled=0xF39C12,
            expired=0xFEE75C,
            revoked=0xED4245,
        )
        embed = discord.Embed(
            title=f"ASK TO DM/FR · {state.upper()}",
            colour=colours[state],
            timestamp=datetime.fromtimestamp(
                request["created"]
                if state == "pending"
                else data.get("resolved", request["created"]),
                UTC,
            ),
        )
        embed.add_field(name="Request", value=KINDS[request["kind"]], inline=False)
        for index, key in enumerate(("sender", "recipient")):
            embed.add_field(
                name="Requester" if index == 0 else "Recipient",
                value=f"**{safe(data['names'][index])}** (User ID: `{request[key]}`)",
                inline=False,
            )
        if data.get("reason"):
            embed.add_field(name="Private request reason", value=safe(data["reason"]), inline=False)
        if state == "denied" and data.get("decline_reason"):
            embed.add_field(
                name="Private decline reason", value=safe(data["decline_reason"]), inline=False
            )
        embed.add_field(name="Request ID", value=request["id"], inline=False)
        return style_log(embed)

    async def notify(self, event, request, data, actor, receipt):
        key = str(actor)
        if receipt.get(key):
            return
        guild = self.bot.get_guild(request["guild"])
        if not guild:
            raise ValueError("Request server is unavailable.")
        try:
            member = await guild.fetch_member(actor)
        except discord.NotFound:
            receipt[key] = "left server"
            await self.store.checkpoint(event, receipt)
            return
        view = RequestPanel(request, data, actor=actor)
        try:
            message = await self.send_panel(member, view)
        except discord.Forbidden:
            channel = await self.channel(data["channel"], request["guild"])
            thread_key = f"thread:{actor}"
            thread_id = receipt.get(thread_key)
            thread = self.bot.get_channel(thread_id) if thread_id else None
            if thread_id and thread is None:
                thread = await self.bot.fetch_channel(thread_id)
            if thread is None:
                thread = await channel.create_thread(
                    name="Private request · KY BOT",
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=1440,
                )
                receipt[thread_key] = thread.id
                await self.store.checkpoint(event, receipt)
            if thread.archived:
                await thread.edit(archived=False)
            await thread.add_user(member)
            message = await self.send_panel(thread, view)
        receipt[key] = dict(channel=message.channel.id, message=message.id)
        await self.store.checkpoint(event, receipt)

    async def process(self, eid, rid, state, raw):
        receipt = json.loads(raw)
        request = await self.store.get(rid)
        data = json.loads(request["data"])
        # Events remain durable history; Discord displays one current log per request.
        reference = await self.store.log_reference(rid)
        _, current_config = await self.store.config(request["guild"])
        message = None
        if reference:
            message_id, saved_channel = reference
            try:
                channel = await self.channel(saved_channel or data["log_channel"], request["guild"])
                await self.check_channel(channel, logs=True)
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                # Recreate only if Discord confirms the original message/channel was deleted.
                message = None
        if message is None:
            channel = await self.channel(
                current_config["log_channel"] or data["log_channel"], request["guild"]
            )
            await self.check_channel(channel, logs=True)
            message = await send_log(channel, self.record(request, data, request["state"]))
        else:
            await send_log(message, self.record(request, data, request["state"]), edit=True)
        await self.store.save_log_reference(rid, message.id, channel.id)
        receipt.update(log=message.id, log_channel=channel.id)
        await self.store.checkpoint(eid, receipt)
        # Do not send stale pending controls after a fast cancellation/expiry.
        current = dict(request, state=request["state"])
        for actor in (request["sender"], request["recipient"]):
            await self.notify(eid, current, data, actor, receipt)
        _, config = await self.store.config(request["guild"])
        if (
            state == "pending"
            and data["public"]
            and config["public"]
            and current["state"] == "pending"
            and not receipt.get("public")
        ):
            channel = await self.channel(data["channel"], request["guild"])
            message = await self.send_panel(channel, RequestPanel(current, data, public=True))
            receipt["public"] = dict(channel=channel.id, message=message.id)
        await self.store.checkpoint(eid, receipt, done=True)
        if receipt.get("public"):
            try:
                await self.bump_sticky(request["guild"])
            except (discord.HTTPException, ValueError):
                self.sticky_due[request["guild"]] = time.monotonic() + 2
                log.warning("Sticky menu move will retry in guild %s", request["guild"])

    async def refresh_cards(self):
        async with self.store.lock:
            rows = await self.store.execute("SELECT id,request,data FROM ask_events", many=True)
        for eid, rid, raw in rows:
            receipt = json.loads(raw)
            request = await self.store.get(rid)
            if request is None:
                continue
            data = json.loads(request["data"])
            _, config = await self.store.config(request["guild"])
            public = receipt.get("public")
            if public and not receipt.get("public_removed"):
                try:
                    channel = await self.channel(public["channel"], request["guild"])
                    message = await channel.fetch_message(public["message"])
                    if not config["public"]:
                        await message.delete()
                        receipt["public_removed"] = True
                    elif receipt.get("public_state", "pending") != request["state"]:
                        view = RequestPanel(request, data, public=True)
                        files = await self.appearance(request["guild"], view)
                        try:
                            await message.edit(
                                view=view,
                                attachments=files,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        finally:
                            for file in files:
                                file.close()
                        receipt["public_state"] = request["state"]
                except discord.NotFound:
                    receipt["public_removed"] = True
                except discord.HTTPException:
                    log.warning("Public request card refresh will retry for %s", rid)
            if data.get("resolved", time.time()) + 86400 < time.time():
                for key, thread_id in list(receipt.items()):
                    if key.startswith("thread:") and not receipt.get("closed:" + key):
                        try:
                            thread = self.bot.get_channel(
                                thread_id
                            ) or await self.bot.fetch_channel(thread_id)
                            await thread.edit(archived=True, locked=True)
                            receipt["closed:" + key] = True
                        except discord.NotFound:
                            receipt["closed:" + key] = True
                        except discord.HTTPException:
                            log.warning("Private thread closure will retry for %s", rid)
            async with self.store.lock:
                # Preserve the event's done flag while checkpointing cleanup.
                await self.store.execute(
                    "UPDATE ask_events SET data=? WHERE id=?", (json.dumps(receipt), eid)
                )

    @tasks.loop(seconds=20)
    async def deliver(self):
        try:
            await self.store.expire()
            for event in await self.store.events():
                try:
                    await self.process(*event)
                except (discord.HTTPException, ValueError):
                    log.warning("Ask to DM/FR delivery queued for retry: %s", event[0])
            await self.refresh_cards()
        except Exception:
            log.exception("Ask to DM/FR delivery cycle failed; retrying next cycle")

    @deliver.before_loop
    async def wait_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AskDM(bot))
