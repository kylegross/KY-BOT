"""Persistent advice actions, permission-checked roles, delivery and moderation."""

import asyncio
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ky_bot.confessions.command import destination
from ky_bot.errors import report_error, send_private
from ky_bot.services.anonymous_threads import reply_reference, reply_thread

from .panels import access_panel, post_panel, safe, submission_panel
from .store import Store

log = logging.getLogger(__name__)


async def administrator(i):
    if not i.guild:
        raise ValueError("Use this inside your server.")
    member = await i.guild.fetch_member(i.user.id)
    if not member.guild_permissions.administrator:
        raise ValueError(
            "Only server administrators can change advice settings or review submissions."
        )
    return member


def roles_for(guild, config):
    if not guild.me.guild_permissions.manage_roles:
        raise ValueError("KY BOT needs Manage Roles to manage advice access.")
    roles = [guild.get_role(config[key]) for key in ("access_role", "advice_role")]
    for role in roles:
        if role is None or role.is_default() or role.managed or role >= guild.me.top_role:
            raise ValueError("Choose two editable roles below KY BOT’s highest role.")
        dangerous = (
            role.permissions.administrator
            or role.permissions.manage_roles
            or role.permissions.manage_guild
            or role.permissions.manage_channels
            or role.permissions.ban_members
            or role.permissions.kick_members
            or role.permissions.moderate_members
            or role.permissions.manage_webhooks
        )
        if dangerous:
            raise ValueError("Choose dedicated access/advice roles without staff permissions.")
    if roles[0].id == roles[1].id:
        raise ValueError("Access and advice must use different roles.")
    return roles


class Action(
    discord.ui.DynamicItem[discord.ui.Button],
    template=(
        r"ky:advice:(?P<token>[0-9a-f]{32}|[0-9]{1,20}):"
        r"(?P<action>join|leave|give|ungive|ask|respond|approve|reject)"
    ),
):
    def __init__(self, token, action, *, label=None, emoji=None):
        self.token, self.action = str(token), action
        labels = dict(
            join="Join",
            leave="Leave",
            give="Give Advice",
            ungive="Remove Advice Role",
            ask="Ask anonymously",
            respond="Respond to question",
            approve="Approve",
            reject="Reject",
        )
        super().__init__(
            discord.ui.Button(
                label=label or labels[action],
                emoji=emoji or None,
                custom_id=f"ky:advice:{token}:{action}",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["token"], match["action"], label=item.label, emoji=item.emoji)

    async def callback(self, i):
        cog = i.client.get_cog("Advice")
        if not cog:
            return await send_private(i, "Ask for Advice is temporarily unavailable.")
        try:
            await cog.action(i, self.token, self.action)
        except ValueError as error:
            await send_private(i, str(error))
        except Exception as error:
            await report_error(i, error)


class Submission(discord.ui.Modal):
    def __init__(self, cog, i, channel, revision, config, parent=None):
        super().__init__(title="Give advice" if parent else "Ask anonymously", timeout=600)
        self.cog, self.owner, self.guild = cog, i.user.id, i.guild_id
        self.channel, self.revision, self.config, self.parent = channel, revision, config, parent
        self.body = discord.ui.TextInput(
            label="Your advice" if parent else "Your question",
            style=discord.TextStyle.paragraph,
            max_length=config["max_length"],
        )
        self.add_item(self.body)

    async def on_submit(self, i):
        if i.user.id != self.owner or i.guild_id != self.guild:
            return await send_private(i, "Open your own submission form.")
        approval = self.config["response_approval"] if self.parent else self.config["approval"]
        text = "Your name stays off the public post unless you explicitly choose Show My Name."
        if not self.parent:
            text = "Your question will be anonymous in the public channel."
        if approval:
            text += " Administrators can see your identity in the private approval queue."
        if self.config["logging"]:
            text += " Author logging is on; your identity and submission are saved in staff logs."
        elif approval:
            text += " Ongoing author logging is off."
        else:
            text += " Author logging is off."
        view = Confirm(self, str(self.body), text)
        await i.response.send_message(
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(self, i, error):
        await report_error(i, error)


class Confirm(discord.ui.LayoutView):
    def __init__(self, form, body, privacy):
        super().__init__(timeout=600)
        self.lock, self.used = asyncio.Lock(), False
        self.add_item(discord.ui.TextDisplay(privacy))
        self.add_item(discord.ui.TextDisplay(safe(body)))
        controls = discord.ui.ActionRow()
        self.add_item(controls)
        for label, named in (("Submit Anonymously", False), ("Show My Name", True)):
            if named and not form.parent:
                continue
            button = discord.ui.Button(label=label)

            async def submit(i, named=named):
                if i.user.id != form.owner or i.guild_id != form.guild:
                    return await send_private(i, "This submission belongs to another member.")
                await i.response.defer(ephemeral=True)
                async with self.lock:
                    if self.used:
                        return await send_private(i, "This form has already been submitted.")
                    try:
                        await form.cog.submit(i, form, body, named)
                    except ValueError as error:
                        return await send_private(i, str(error))
                    except Exception as error:
                        self.used = True
                        await report_error(i, error)
                        return
                    self.used = True
                    self.stop()

            button.callback = submit
            controls.add_item(button)
        cancel = discord.ui.Button(label="Cancel")

        async def discard(i):
            if i.user.id != form.owner or i.guild_id != form.guild:
                return await send_private(i, "This submission belongs to another member.")
            async with self.lock:
                if self.used:
                    return await send_private(i, "This form has already been handled.")
                self.used = True
                await i.response.edit_message(
                    view=discord.ui.LayoutView().add_item(
                        discord.ui.TextDisplay("Cancelled. Nothing was submitted.")
                    )
                )
                self.stop()

        cancel.callback = discard
        controls.add_item(cancel)


class Advice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.role_locks, self.panel_locks, self.delivery_locks = {}, {}, {}
        self.thread_locks = {}
        self.panel_inflight = set()

    async def cog_load(self):
        self.store = await Store.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.bot.add_dynamic_items(Action)
        self.maintenance.start()

    async def cog_unload(self):
        self.maintenance.cancel()
        task = self.maintenance.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.bot.remove_dynamic_items(Action)
        await self.store.close()

    async def send_panel(self, target, view, **kwargs):
        files = view.uploads()
        try:
            return await target.send(
                view=view, files=files, allowed_mentions=discord.AllowedMentions.none(), **kwargs
            )
        finally:
            for file in files:
                file.close()

    async def member(self, i, channel, *, responding=False, parent=None):
        if not i.guild:
            raise ValueError("Use this inside your server.")
        member = await i.guild.fetch_member(i.user.id)
        revision, config = await self.store.config(i.guild_id, channel)
        roles = {role.id for role in member.roles}
        if not config["enabled"] or config["access_role"] not in roles:
            raise ValueError("Join Ask for Advice before submitting or responding.")
        target = destination(i.guild, channel)
        if not target.permissions_for(member).view_channel:
            raise ValueError("You do not have access to this ask channel.")
        if responding and config["advice_role"] not in roles:
            if not parent or not await self.store.is_original(
                i.guild_id, channel, parent, member.id
            ):
                raise ValueError("Select Give Advice in the access panel before responding.")
        return member, revision, config

    async def action(self, i, token, action):
        if not i.guild:
            raise ValueError("Use this inside your server.")
        if action in {"join", "leave", "give", "ungive"}:
            await i.response.defer(ephemeral=True)
            return await self.role_action(i, int(token), action)
        if action == "ask":
            return await self.begin(i, int(token))
        post = await self.store.post(token)
        if not post or post["guild"] != i.guild_id:
            raise ValueError("This question or response is unavailable.")
        if action == "respond":
            if post["state"] != "published":
                raise ValueError("Choose a published question or advice response to reply to.")
            await self.store.root(token, post["guild"], post["channel"])
            return await self.begin(i, post["channel"], post["id"])
        await administrator(i)
        await i.response.defer(ephemeral=True)
        async with self.delivery_locks.setdefault(token, asyncio.Lock()):
            post = await self.store.post(token)
            if action == "reject":
                await self.store.transition(token, {"pending"}, "rejected")
                await send_private(i, "Submission rejected.")
            else:
                await self.store.transition(token, {"pending"}, "ready")
                await self._deliver(i.guild, await self.store.post(token))
                await send_private(i, "Submission approved and published.")
            if i.message:
                try:
                    await i.message.edit(
                        view=discord.ui.LayoutView().add_item(
                            discord.ui.TextDisplay(
                                "Submission "
                                + ("approved." if action == "approve" else "rejected.")
                            )
                        ),
                        attachments=[],
                    )
                except discord.HTTPException:
                    pass

    async def role_action(self, i, channel, action):
        async with self.role_locks.setdefault((i.guild_id, i.user.id), asyncio.Lock()):
            _, config = await self.store.config(i.guild_id, channel)
            if not config["enabled"] and action in {"join", "give"}:
                raise ValueError("Ask for Advice is paused.")
            if i.channel_id != config["access_channel"]:
                raise ValueError("Use the current access panel in the configured access channel.")
            panel = await self.store.access_panel(i.guild_id, channel)
            if (
                not i.message
                or i.message.id != panel["message"]
                or i.channel_id != panel["channel"]
            ):
                raise ValueError("This panel was replaced. Use the latest access panel.")
            access, advice = roles_for(i.guild, config)
            member = await i.guild.fetch_member(i.user.id)
            owned = {r.id for r in member.roles}
            if action == "join":
                await member.add_roles(access, reason="Ask for Advice: join")
                text = f"You can now view <#{channel}>."
            elif action == "give":
                if access.id not in owned:
                    raise ValueError("Select Join before choosing Give Advice.")
                await member.add_roles(advice, reason="Ask for Advice: opt in to advice")
                text = "You can now respond to questions and give advice."
            elif action == "ungive":
                await member.remove_roles(advice, reason="Ask for Advice: stop giving advice")
                text = "Your advice role was removed. Your channel access is unchanged."
            else:
                # Remove advice first so partial failure cannot leave advice without access.
                await member.remove_roles(advice, reason="Ask for Advice: leave")
                await member.remove_roles(access, reason="Ask for Advice: leave")
                text = "Your channel access and advice role were removed."
            await send_private(i, text)

    async def begin(self, i, channel, parent=None):
        # Acknowledge the member fetch before opening the modal via a private start button.
        await i.response.defer(ephemeral=True)
        _, revision, config = await self.member(i, channel, responding=bool(parent), parent=parent)
        form = Submission(self, i, channel, revision, config, parent)
        view = discord.ui.View(timeout=600)
        button = discord.ui.Button(label="Write advice" if parent else "Write question")

        async def write(click):
            if click.user.id != form.owner or click.guild_id != form.guild:
                return await send_private(click, "Open your own form.")
            await click.response.send_modal(form)

        button.callback = write
        view.add_item(button)
        await i.followup.send("Open your private submission form.", view=view, ephemeral=True)

    async def submit(self, i, form, body, named):
        member, _, config = await self.member(
            i, form.channel, responding=bool(form.parent), parent=form.parent
        )
        approval = config["response_approval"] if form.parent else config["approval"]
        if approval:
            destination(i.guild, config["queue_channel"], private=True)
        if config["logging"]:
            destination(i.guild, config["log_channel"], private=True)
        post = await self.store.submit(
            i.guild_id,
            form.channel,
            member.id,
            [r.id for r in member.roles],
            body,
            i.id,
            form.revision,
            parent=form.parent,
            named=named,
        )
        try:
            await self.deliver(i.guild, post)
        except (ValueError, discord.HTTPException):
            await send_private(i, "Your submission was saved. Delivery will retry automatically.")
            return
        await send_private(
            i, "Submitted for approval." if approval else "Your submission was published."
        )

    async def deliver(self, guild, post):
        async with self.delivery_locks.setdefault(post["id"], asyncio.Lock()):
            await self._deliver(guild, await self.store.post(post["id"]))

    async def _deliver(self, guild, post):
        _, config = await self.store.config(guild.id, post["channel"])
        if post["state"] == "pending":
            if not post["data"]["queue"]:
                queue = destination(guild, post["data"]["queue_channel"], private=True)
                message = await self.find_delivery(queue, post["id"], since=post["created"])
                if message is None:
                    message = await self.send_panel(queue, post_panel(post, config, review=True))
                await self.store.transition(post["id"], {"pending"}, "pending", queue=message.id)
            return
        if post["state"] in {"ready", "sending"}:
            channel = destination(guild, post["channel"])
            if post["parent"]:
                source = await self.store.root(post["parent"], guild.id, post["channel"])
                async with self.thread_locks.setdefault(source["id"], asyncio.Lock()):
                    channel = await reply_thread(
                        guild, channel, source, self.store, label="Question"
                    )
            if post["state"] == "ready":
                await self.store.transition(post["id"], {"ready"}, "sending")
            message = await self.find_delivery(channel, post["id"], since=post["created"])
            if message is None:
                view = post_panel(post, config)
                reference = (
                    await reply_reference(self.store, post, channel) if post["parent"] else None
                )
                message = await self.send_panel(
                    channel, view, **({"reference": reference} if reference else {})
                )
            await self.store.transition(
                post["id"],
                {"sending"},
                "published",
                message=message.id,
                message_channel=channel.id,
            )
            post = await self.store.post(post["id"])
        if post["state"] != "published":
            return
        if post["data"]["logging"] and not post["data"]["log"]:
            target = destination(guild, post["data"]["log_channel"], private=True)
            marker = f"Advice log · {post['id']}"
            found = await self.find_delivery(target, marker, since=post["created"])
            if found is None:
                view = discord.ui.LayoutView()
                view.add_item(
                    discord.ui.TextDisplay(
                        f"{marker}\nAuthor: <@{post['author']}> (`{post['author']}`)\n"
                        f"Channel: <#{post['channel']}>"
                    )
                )
                view.add_item(discord.ui.TextDisplay(safe(post["body"])))
                found = await target.send(
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await self.store.transition(post["id"], {"published"}, "published", log=found.id)
        post = await self.store.post(post["id"])
        if not post["parent"] and config["notifications"] and not post["data"].get("notified"):
            target = destination(guild, post["channel"])
            marker = f"New question #{post['data']['number']}"
            found = await self.find_delivery(
                target, marker + " · " + post["id"], since=post["created"]
            )
            if found is None:
                role = guild.get_role(config["advice_role"])
                if role is None:
                    raise ValueError("The configured advice role was deleted.")
                if not role.mentionable and not target.permissions_for(guild.me).mention_everyone:
                    raise ValueError(
                        "Make the advice role mentionable or allow KY BOT to mention it."
                    )
                await target.send(
                    f"<@&{role.id}> {marker} · {post['id']}\n"
                    f"https://discord.com/channels/{guild.id}/{post['channel']}/"
                    f"{post['data']['message']}",
                    allowed_mentions=discord.AllowedMentions(
                        roles=[role],
                        users=False,
                        everyone=False,
                        replied_user=False,
                    ),
                )
            await self.store.transition(post["id"], {"published"}, "published", notified=True)
        elif not post["data"].get("notified"):
            # Disabling notifications suppresses this post, rather than pinging it later.
            await self.store.transition(post["id"], {"published"}, "published", notified=True)

    async def find_delivery(self, channel, marker, *, since=None):
        if not channel.permissions_for(channel.guild.me).read_message_history:
            raise ValueError("KY BOT needs Read Message History for reliable advice delivery.")
        after = datetime.fromtimestamp(since, UTC) if since is not None else None
        async for message in channel.history(limit=None, after=after, oldest_first=True):
            if message.author.id != self.bot.user.id:
                continue
            # Components V2 content is nested in message components.
            if marker in (message.content or "") or any(
                marker in str(component.to_dict()) for component in message.components
            ):
                return message
        return None

    async def validate_setup(self, guild, channel, config):
        access, advice = roles_for(guild, config)
        public = destination(guild, channel)
        entrance = destination(guild, config["access_channel"])
        if public.id == entrance.id:
            raise ValueError("Use separate access and ask channels.")
        if (
            config["notifications"]
            and not advice.mentionable
            and not (public.permissions_for(guild.me).mention_everyone)
        ):
            raise ValueError("Make the advice role mentionable before enabling role notifications.")
        for target in (public, entrance):
            if not target.permissions_for(guild.me).read_message_history:
                raise ValueError("KY BOT needs Read Message History in both channels.")
        permissions = public.permissions_for(guild.me)
        if not permissions.create_public_threads or not permissions.send_messages_in_threads:
            raise ValueError(
                "KY BOT needs Create Public Threads and Send Messages in Threads "
                "in the ask channel."
            )
        if not guild.me.guild_permissions.manage_channels:
            raise ValueError("KY BOT needs Manage Channels to configure channel visibility.")
        for key in ("queue_channel", "log_channel"):
            if config[key]:
                private = destination(guild, config[key], private=True)
                if private.id in {public.id, entrance.id}:
                    raise ValueError(
                        "Log and approval destinations must be separate private channels."
                    )
        return public, entrance, access, advice

    async def apply_access(self, guild, channel, config):
        public, _, access, advice = await self.validate_setup(guild, channel, config)
        for target, overwrite in public.overwrites.items():
            if overwrite.view_channel is not True or target.id in {
                access.id,
                advice.id,
                guild.me.id,
            }:
                continue
            if target == guild.default_role:
                continue
            permissions = (
                target.permissions if isinstance(target, discord.Role) else target.guild_permissions
            )
            if not permissions.administrator:
                raise ValueError(
                    "Remove other non-administrator View Channel overrides from the ask channel "
                    "before applying access. The configured access role must control visibility."
                )
        # Only the selected advice channel is changed; preserve unrelated permission flags.
        for target, visible in (
            (guild.default_role, False),
            (advice, None),
            (access, True),
            (guild.me, True),
        ):
            overwrite = public.overwrites_for(target)
            overwrite.view_channel = visible
            await public.set_permissions(
                target, overwrite=overwrite, reason="Ask for Advice: configure joined-member access"
            )

    async def publish_panel(self, guild, channel, *, access=False):
        async with self.panel_locks.setdefault((guild.id, channel, access), asyncio.Lock()):
            _, config = await self.store.config(guild.id, channel)
            if not config["enabled"]:
                raise ValueError("Enable Ask for Advice first.")
            target_id = config["access_channel"] if access else channel
            if access:
                await self.apply_access(guild, channel, config)
                await self.store.access_panel(guild.id, channel)
            target = destination(guild, target_id)
            view = access_panel(channel, config) if access else submission_panel(channel, config)
            self.panel_inflight.add((guild.id, target_id))
            try:
                message = await self.send_panel(target, view)
                if access:
                    saved = await self.store.access_panel(
                        guild.id, channel, channel=target_id, message=message.id
                    )
                    retired = saved["retired"]
                else:
                    _, _, previous = await self.store.panel(
                        guild.id, target_id, message=message.id, clear=True
                    )
                    retired = [(target_id, old) for old in previous]
                for old_channel, old in retired:
                    try:
                        previous_target = (
                            target if old_channel == target_id else destination(guild, old_channel)
                        )
                        await previous_target.get_partial_message(old).delete()
                    except discord.NotFound:
                        pass
                    except (ValueError, discord.HTTPException):
                        continue
                    if access:
                        await self.store.access_retired_done(guild.id, channel, old_channel, old)
                    else:
                        await self.store.retired_done(guild.id, target_id, old)
                return message
            finally:
                self.panel_inflight.discard((guild.id, target_id))

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild:
            return
        key = message.guild.id, message.channel.id
        _, config = await self.store.config(*key)
        if not config["enabled"] or not config["sticky"]:
            return
        # Count member AND bot messages, excluding the submission panel itself.
        marker = f"ky:advice:{message.channel.id}:ask"
        if message.author.id == self.bot.user.id and any(
            marker in str(component.to_dict()) for component in getattr(message, "components", ())
        ):
            return
        panel, _, _ = await self.store.panel(*key)
        if message.id != panel:
            await self.store.panel(*key, count=True)

    @tasks.loop(seconds=10)
    async def maintenance(self):
        for guild_id, channel, config in await self.store.channels():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            try:
                if config["enabled"] and config["sticky"]:
                    message, count, _ = await self.store.panel(guild_id, channel)
                    if message and count >= config["sticky_count"]:
                        await self.publish_panel(guild, channel)
            except (ValueError, discord.HTTPException):
                log.warning("Advice panel unavailable in guild %s", guild_id)
        for post in await self.store.work():
            guild = self.bot.get_guild(post["guild"])
            if not guild:
                continue
            if post["state"] == "published" and (
                (not post["data"]["logging"] or post["data"]["log"])
                and (post["parent"] or post["data"].get("notified"))
            ):
                continue
            try:
                await self.deliver(guild, post)
            except (ValueError, discord.HTTPException):
                log.warning("Advice delivery pending in guild %s", guild.id)

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    async def open_menu(self, i, *, command_center=None):
        from ky_bot.views.menu_context import defer_menu, menu_parent

        from .menus import Admin

        await administrator(i)
        parent = menu_parent(command_center, i)
        await defer_menu(i, parent)
        await Admin(self, i.user.id, i.guild_id, parent=parent).show(i, "channels")

    @app_commands.command(name="ask-advice", description="Privately ask a question or seek advice.")
    @app_commands.guild_only()
    async def ask_advice(self, i: discord.Interaction, channel: discord.TextChannel):
        try:
            await self.begin(i, channel.id)
        except ValueError as error:
            await send_private(i, str(error))


async def setup(bot):
    await bot.add_cog(Advice(bot))
