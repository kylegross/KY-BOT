"""Anonymous member entry points and administrator-only, persistent moderation controls."""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ky_bot.errors import report_error, send_private
from ky_bot.log_style import send_log

from .cards import Card, author_log_embed, post_card, privacy, safe, uploads
from .store import Store

log = logging.getLogger(__name__)


async def administrator(interaction):
    if interaction.guild is None:
        raise ValueError("Use this inside your server.")
    member = await interaction.guild.fetch_member(interaction.user.id)
    if not member.guild_permissions.administrator:
        raise ValueError("Only server administrators can manage confessions or review submissions.")
    return member


def destination(guild, channel_id, *, private=False):
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Choose a text channel in this server.")
    permissions = channel.permissions_for(guild.me)
    if not (permissions.view_channel and permissions.send_messages and permissions.attach_files):
        raise ValueError(
            "KY BOT needs View Channel, Send Messages and Attach Files in this channel."
        )
    if private:
        # An author-bearing queue must never inherit ordinary-member access.
        if channel.permissions_for(guild.default_role).view_channel:
            raise ValueError("The log/review channel must be private to administrators and KY BOT.")
        for target, overwrite in channel.overwrites.items():
            if overwrite.view_channel is not True:
                continue
            if isinstance(target, discord.Role):
                bot_role = target.is_bot_managed() and target.tags.bot_id == guild.me.id
                if not target.permissions.administrator and not bot_role:
                    raise ValueError(
                        "Only administrator roles and KY BOT may view author logs or review queues."
                    )
            elif target.id != guild.me.id and not target.guild_permissions.administrator:
                raise ValueError("Remove non-administrator access from the log/review channel.")
    return channel


class Action(
    discord.ui.DynamicItem[discord.ui.Button],
    template=(
        "ky:conf:(?P<token>[0-9a-f]{32}|[0-9]{1,20}):(?P<action>submit|reply|re"
        "port|approve|reject|remove|ban|dismiss)"
    ),
):
    def __init__(self, token, action, *, label=None):
        self.token, self.action = str(token), action
        labels = dict(
            submit="Submit confession",
            reply="Reply anonymously",
            report="Report",
            approve="Approve",
            reject="Reject",
            remove="Remove confession",
            ban="Ban author from confessions",
            dismiss="Dismiss reports",
        )
        super().__init__(
            discord.ui.Button(
                label=label or labels[action],
                style=discord.ButtonStyle.secondary,
                custom_id=f"ky:conf:{token}:{action}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["token"], match["action"], label=item.label)

    async def callback(self, interaction):
        cog = interaction.client.get_cog("Confessions")
        if cog is None:
            return await send_private(interaction, "Confessions are temporarily unavailable.")
        try:
            await cog.action(interaction, self.token, self.action)
        except ValueError as error:
            await send_private(interaction, str(error))
        except Exception as error:
            await report_error(interaction, error)


class ModalEntry(Card):
    """A styled private submenu before Discord's native input popup."""

    def __init__(self, config, form, title, body, label, detail=None):
        button = discord.ui.Button(label=label)

        async def write(interaction):
            if interaction.user.id != form.owner or interaction.guild_id != form.guild:
                return await send_private(interaction, "Open your own confession submenu.")
            await interaction.response.send_modal(form)

        button.callback = write
        super().__init__(
            config,
            title,
            body,
            detail=detail,
            buttons=[button],
            timeout=600,
            show_footer_text=False,
        )


class Submission(discord.ui.Modal):
    def __init__(self, cog, owner, guild, channel, revision, config, parent=None):
        super().__init__(title="Anonymous reply" if parent else "Submit confession", timeout=600)
        self.cog, self.owner, self.guild, self.channel = cog, owner, guild, channel
        self.revision, self.config, self.parent = revision, config, parent
        self.text = discord.ui.TextInput(
            label="Your reply" if parent else "Your confession",
            style=discord.TextStyle.paragraph,
            max_length=config["max_length"],
            required=True,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner or interaction.guild_id != self.guild:
            return await send_private(interaction, "Open your own confession form.")
        # The final privacy notice precedes the action that actually submits content.
        view = ReviewSubmission(self, str(self.text))
        files = uploads(self.config, view)
        try:
            await interaction.response.send_message(
                view=view,
                files=files,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for file in files:
                file.close()

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class ReviewSubmission(Card):
    def __init__(self, form, body):
        self.used = False
        self.lock = asyncio.Lock()
        submit = discord.ui.Button(
            label="Submit for approval" if form.config["approval"] else "Publish anonymously"
        )
        cancel = discord.ui.Button(label="Cancel")

        async def confirm(interaction):
            if interaction.user.id != form.owner or interaction.guild_id != form.guild:
                return await send_private(interaction, "This submission belongs to another member.")
            await interaction.response.defer(ephemeral=True)
            async with self.lock:
                if self.used:
                    return await send_private(
                        interaction, "This submission has already been handled."
                    )
                self.used = True
                try:
                    await form.cog.submit(
                        interaction, form.channel, form.revision, body, form.parent
                    )
                except ValueError as error:
                    await send_private(interaction, str(error))
                except Exception as error:
                    await report_error(interaction, error)
                finally:
                    self.stop()

        async def discard(interaction):
            if interaction.user.id != form.owner or interaction.guild_id != form.guild:
                return await send_private(interaction, "This submission belongs to another member.")
            async with self.lock:
                if self.used:
                    return await send_private(
                        interaction, "This submission has already been handled."
                    )
                self.used = True
                await interaction.response.edit_message(
                    view=discord.ui.LayoutView().add_item(
                        discord.ui.TextDisplay("Cancelled. Nothing was submitted.")
                    )
                )
                self.stop()

        submit.callback, cancel.callback = confirm, discard
        super().__init__(
            form.config,
            "REVIEW YOUR REPLY" if form.parent else "REVIEW YOUR CONFESSION",
            body,
            detail=privacy(form.config),
            buttons=[submit, cancel],
            timeout=600,
            show_footer_text=False,
        )


class Reason(discord.ui.Modal):
    def __init__(self, cog, token, action, owner, guild):
        super().__init__(
            title={
                "report": "Report confession",
                "reject": "Reject confession",
                "ban": "Confession ban",
                "remove": "Remove confession",
            }[action]
        )
        self.cog, self.token, self.action, self.owner, self.guild = cog, token, action, owner, guild
        self.reason = discord.ui.TextInput(
            label="Reason", style=discord.TextStyle.paragraph, max_length=1000, required=True
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner or interaction.guild_id != self.guild:
            return await send_private(interaction, "Open your own form.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.action == "report":
                await self.cog.report(interaction, self.token, str(self.reason))
            else:
                await self.cog.moderate(interaction, self.token, self.action, str(self.reason))
        except ValueError as error:
            await send_private(interaction, str(error))

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class Confessions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.panel_locks = {}

    async def cog_load(self):
        self.store = await Store.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.bot.add_dynamic_items(Action)
        self.sticky.start()

    async def cog_unload(self):
        self.sticky.cancel()
        task = self.sticky.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.bot.remove_dynamic_items(Action)
        await self.store.close()

    async def send_card(self, channel, config, view, **kwargs):
        attachments = uploads(config, view)
        try:
            return await channel.send(
                view=view,
                files=attachments,
                allowed_mentions=discord.AllowedMentions.none(),
                **kwargs,
            )
        finally:
            for attachment in attachments:
                attachment.close()

    @app_commands.command(name="confess", description="Privately submit an anonymous confession.")
    @app_commands.guild_only()
    @app_commands.describe(channel="Confession channel (choose privately if omitted)")
    async def confess(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ):
        try:
            if channel:
                return await self.begin(interaction, channel.id)
            configured = [
                (g, c, s)
                for g, c, s in await self.store.channels(interaction.guild_id)
                if s["enabled"]
                and interaction.guild.get_channel(c)
                and interaction.guild.get_channel(c).permissions_for(interaction.user).view_channel
            ]
            if not configured:
                raise ValueError(
                    "No confession channels are available. Ask an administrator to configure one."
                )
            if len(configured) == 1:
                return await self.begin(interaction, configured[0][1])
            from .menus import ChannelChooser

            await interaction.response.send_message(
                view=ChannelChooser(self, interaction, configured), ephemeral=True
            )
        except ValueError as error:
            await send_private(interaction, str(error))

    async def begin(self, interaction, channel, parent=None):
        target = destination(interaction.guild, channel)
        member = await interaction.guild.fetch_member(interaction.user.id)
        if not target.permissions_for(member).view_channel:
            raise ValueError("You cannot access this confession channel.")
        revision, config = await self.store.config(interaction.guild_id, channel)
        if not config["enabled"]:
            raise ValueError("Confessions are paused in this channel.")
        if parent and not config["replies"]:
            raise ValueError("Anonymous replies are disabled.")
        form = Submission(self, member.id, interaction.guild_id, channel, revision, config, parent)
        view = ModalEntry(
            config,
            form,
            "ANONYMOUS REPLY" if parent else "SUBMIT CONFESSION",
            "Your words get the spotlight—your name stays off the public post.",
            "Write reply" if parent else "Write confession",
            detail=privacy(config),
        )
        files = uploads(config, view)
        try:
            await interaction.response.send_message(
                view=view,
                files=files,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for file in files:
                file.close()

    async def submit(self, interaction, channel, revision, body, parent):
        _, config = await self.store.config(interaction.guild_id, channel)
        public = destination(interaction.guild, channel)
        member = await interaction.guild.fetch_member(interaction.user.id)
        if not public.permissions_for(member).view_channel:
            raise ValueError("You cannot access this confession channel.")
        if config["approval"] or config["reports"]:
            destination(interaction.guild, config["queue_channel"], private=True)
        if config["logging"]:
            destination(interaction.guild, config["log_channel"], private=True)
        post = await self.store.submit(
            interaction.guild_id,
            channel,
            member.id,
            [r.id for r in member.roles],
            body,
            interaction.id,
            revision,
            parent,
        )
        if post["state"] == "pending":
            try:
                queue = destination(interaction.guild, config["queue_channel"], private=True)
                message = await self.send_card(
                    queue,
                    config,
                    post_card(
                        config,
                        post,
                        staff=True,
                        buttons=[
                            Action(post["id"], a)
                            for a in ("approve", "reject", "ban")
                            if a != "ban" or config["bans"]
                        ],
                    ),
                )
                await self.store.transition(
                    post["id"], {"pending"}, "pending", queue=message.id, queue_channel=queue.id
                )
            except discord.HTTPException:
                # Remains visible in the persisted admin queue; no automatic duplicate sends.
                await send_private(
                    interaction,
                    (
                        "Saved for administrator approval. The queue notification could not be "
                        "confirmed; administrators can still open Pending approvals."
                    ),
                )
                return
            await send_private(
                interaction,
                (
                    "Submitted for administrator approval. Your identity is visible in the "
                    "private review queue."
                ),
            )
        elif post["state"] == "ready":
            await self.publish(interaction.guild, post, config)
            await send_private(interaction, "Your confession was published anonymously.")
        else:
            await send_private(interaction, "This submission has already been processed.")

    async def publish(self, guild, post, config):
        channel = destination(guild, post["channel"])
        if post["parent"]:
            parent = await self.store.post(post["parent"])
            if not parent or parent["state"] != "published":
                raise ValueError(
                    "The original confession is no longer available. Reject this reply instead."
                )
        # Respect the privacy mode accepted at submission; never introduce retroactive author logs.
        should_log = post["data"]["logging"] and config["logging"]
        if should_log:
            log_channel = destination(guild, config["log_channel"], private=True)
        post = await self.store.transition(post["id"], {"ready"}, "publishing", logging=should_log)
        try:
            buttons = [
                Action(post["id"], a)
                for a in ("reply", "report")
                if config["replies" if a == "reply" else "reports"]
            ]
            message = await self.send_card(
                channel, config, post_card(config, post, buttons=buttons)
            )
            post = await self.store.transition(
                post["id"], {"publishing"}, "published", message=message.id
            )
        except BaseException:
            await self.store.transition(post["id"], {"publishing"}, "uncertain")
            raise
        if config["sticky"]:
            # Count successful confession/reply posts, but never the bot's own panel.
            await self.store.panel(guild.id, post["channel"], count=True)
        if post["data"]["logging"]:
            try:
                logged = await self.send_author_log(log_channel, config, post)
                await self.store.transition(post["id"], {"published"}, "published", log=logged.id)
            except discord.HTTPException:
                # Published state is retained: retrying must never duplicate the public confession.
                log.warning("Confession author log delivery unconfirmed in guild %s", guild.id)
        await self.refresh_queue(guild, post, config)

    async def send_author_log(self, channel, config, post):
        member = None
        guild = getattr(channel, "guild", None)
        if guild is not None and post["author"]:
            member = guild.get_member(post["author"])
            if member is None:
                try:
                    member = await guild.fetch_member(post["author"])
                except discord.HTTPException:
                    pass
        return await send_log(
            channel, author_log_embed(config, post, getattr(member, "display_name", None))
        )

    async def refresh_queue(self, guild, post, config):
        if not post["data"].get("queue"):
            return
        try:
            channel = destination(
                guild, post["data"].get("queue_channel", config["queue_channel"]), private=True
            )
            message = channel.get_partial_message(post["data"]["queue"])
            view = post_card(config, post, staff=True)
            files = uploads(config, view)
            try:
                await message.edit(
                    view=view,
                    attachments=files,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                for file in files:
                    file.close()
        except (ValueError, discord.HTTPException):
            log.warning("Confession review status refresh unavailable in guild %s", guild.id)

    async def action(self, interaction, token, action):
        if action == "submit":
            return await self.begin(interaction, int(token))
        post = await self.store.post(token)
        if not post or interaction.guild_id != post["guild"]:
            raise ValueError("This confession is unavailable in this server.")
        _, config = await self.store.config(post["guild"], post["channel"])
        if action == "reply":
            if post["state"] != "published":
                raise ValueError("This confession is no longer available for replies.")
            return await self.begin(interaction, post["channel"], token)
        if action == "report":
            if not config["reports"]:
                raise ValueError("Reporting is disabled for this channel.")
        else:
            await administrator(interaction)
        if action == "report":
            form = Reason(self, token, action, interaction.user.id, interaction.guild_id)
            view = ModalEntry(
                config,
                form,
                "REPORT CONFESSION",
                "Tell administrators what needs reviewing. Your report stays private.",
                "Write report",
            )
            files = uploads(config, view)
            try:
                return await interaction.response.send_message(
                    view=view,
                    files=files,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                for file in files:
                    file.close()
        if action in {"reject", "ban", "remove"}:
            return await interaction.response.send_modal(
                Reason(self, token, action, interaction.user.id, interaction.guild_id)
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.moderate(interaction, token, action, "")

    async def moderate(self, interaction, token, action, reason):
        await administrator(interaction)
        post = await self.store.post(token)
        if not post or post["guild"] != interaction.guild_id:
            raise ValueError("This confession is unavailable in this server.")
        _, config = await self.store.config(post["guild"], post["channel"])
        if action == "approve":
            if not config["enabled"]:
                raise ValueError(
                    "Confessions are paused in this channel. Enable them before approving."
                )
            # Only admins can advance the queue, even when they have moderation permissions.
            destination(interaction.guild, post["channel"])
            if post["parent"]:
                parent = await self.store.post(post["parent"])
                if not parent or parent["state"] != "published":
                    raise ValueError(
                        "The original confession was removed. Reject this reply instead."
                    )
            async with self.store.lock:
                banned = await self.store.execute(
                    "SELECT reason FROM confession_bans WHERE guild=? AND author_key=?",
                    (post["guild"], post["author_key"]),
                )
            if config["bans"] and banned:
                raise ValueError(
                    "This author is banned from confessions. Reject the submission or lift "
                    "the ban first."
                )
            if post["data"]["logging"] and config["logging"]:
                destination(interaction.guild, config["log_channel"], private=True)
            post = await self.store.transition(
                token, {"pending"}, "ready", reviewer=interaction.user.id
            )
            await self.publish(interaction.guild, post, config)
        elif action == "reject":
            post = await self.store.transition(
                token, {"pending"}, "rejected", reviewer=interaction.user.id, reason=reason
            )
            await self.refresh_queue(interaction.guild, post, config)
        elif action == "ban":
            if not config["bans"]:
                raise ValueError("Confession bans are disabled for this channel.")
            await self.store.set_ban(interaction.guild_id, interaction.user.id, reason, token=token)
        elif action == "remove":
            if post["state"] != "published":
                raise ValueError("This confession is not currently published.")
            channel = destination(interaction.guild, post["channel"])
            try:
                await channel.get_partial_message(post["data"]["message"]).delete()
            except discord.NotFound:
                pass
            post = await self.store.transition(
                token, {"published"}, "removed", reviewer=interaction.user.id, reason=reason
            )
            async with self.store.transaction():
                await self.store.execute(
                    "UPDATE confession_reports SET state='resolved' WHERE post=? AND guild=?",
                    (token, interaction.guild_id),
                )
            await self.refresh_queue(interaction.guild, post, config)
        elif action == "dismiss":
            async with self.store.transaction():
                await self.store.execute(
                    "UPDATE confession_reports SET state='dismissed' WHERE post=? AND guild=?",
                    (token, interaction.guild_id),
                )
        else:
            raise ValueError("Unknown confession action.")
        await send_private(
            interaction,
            {
                "approve": "Approved and published anonymously.",
                "reject": "Submission rejected.",
                "ban": "Author banned from confessions. This does not ban them from the server.",
                "remove": "Confession removed.",
                "dismiss": "Reports dismissed.",
            }[action],
        )

    async def report(self, interaction, token, reason):
        post = await self.store.post(token)
        if not post or post["guild"] != interaction.guild_id:
            raise ValueError("This confession is unavailable.")
        _, config = await self.store.config(post["guild"], post["channel"])
        public = destination(interaction.guild, post["channel"])
        member = await interaction.guild.fetch_member(interaction.user.id)
        if not config["reports"] or not public.permissions_for(member).view_channel:
            raise ValueError("Reporting is unavailable for this channel.")
        queue = destination(interaction.guild, config["queue_channel"], private=True)
        await self.store.report(interaction.guild_id, token, member.id, reason)
        details = (
            f"**Reporter:** <@{member.id}> · `{member.id}`\n"
            f"**Reason:** {safe(reason)}\n"
            f"**Confession:** {post['data']['number']} · <#{post['channel']}>"
        )
        view = Card(
            config,
            "CONFESSION REPORT",
            post["body"],
            detail=details,
            buttons=[
                Action(token, a)
                for a in ("remove", "ban", "dismiss")
                if a != "ban" or config["bans"]
            ],
        )
        try:
            await self.send_card(queue, config, view)
        except discord.HTTPException:
            await send_private(
                interaction,
                "Your report was saved. Administrators can find it in the Reports menu.",
            )
            return
        await send_private(interaction, "Your report was sent privately to administrators.")

    async def publish_panel(self, guild, channel_id):
        key = guild.id, channel_id
        async with self.panel_locks.setdefault(key, asyncio.Lock()):
            _, config = await self.store.config(*key)
            if not config["enabled"]:
                raise ValueError("Enable this confession channel first.")
            channel = destination(guild, channel_id)
            _, _, retired = await self.store.panel(*key)
            view = Card(
                config,
                config["panel_title"],
                config["panel_text"],
                detail=("Use the button below or `/confess`."),
                show_footer_text=config["footer_text"] != "KY BOT · Confessions",
                buttons=[Action(channel_id, "submit", label=config["submit_label"])],
            )
            message = await self.send_card(channel, config, view)
            _, _, retired = await self.store.panel(*key, message=message.id, clear=True)
            for old in retired:
                try:
                    await channel.get_partial_message(old).delete()
                    await self.store.retired_done(*key, old)
                except discord.NotFound:
                    await self.store.retired_done(*key, old)
                except discord.HTTPException:
                    pass
            return message

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild and not message.author.bot:
            _, config = await self.store.config(message.guild.id, message.channel.id)
            if config["enabled"] and config["sticky"]:
                await self.store.panel(message.guild.id, message.channel.id, count=True)

    @tasks.loop(seconds=15)
    async def sticky(self):
        for guild_id, channel, config in await self.store.channels():
            guild = self.bot.get_guild(guild_id)
            if guild and config["enabled"] and config["sticky"]:
                message, count, _ = await self.store.panel(guild_id, channel)
                if message and count >= config["sticky_count"]:
                    try:
                        await self.publish_panel(guild, channel)
                    except (ValueError, discord.HTTPException):
                        log.warning("Confession sticky panel unavailable in guild %s", guild_id)

    @sticky.before_loop
    async def before_sticky(self):
        await self.bot.wait_until_ready()

    async def open_menu(self, interaction, *, command_center=None, logs=False):
        from ky_bot.views.menu_context import defer_menu, menu_parent

        from .menus import Admin

        await administrator(interaction)
        command_center = menu_parent(command_center, interaction)
        await defer_menu(interaction, command_center)
        menu = Admin(self, interaction.user.id, interaction.guild_id, parent=command_center)
        menu.start_logging = logs
        await menu.show(interaction, "channels")


async def setup(bot):
    await bot.add_cog(Confessions(bot))
    from .logs import Logs

    await bot.add_cog(Logs(bot))
