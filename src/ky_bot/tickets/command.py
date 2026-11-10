"""Ticket lifecycle, persistent controls and shared setup entry points."""

import asyncio
import copy
import html
import io
import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ky_bot.errors import send_private
from ky_bot.log_style import send_log, style_log
from ky_bot.views.menu_context import defer_menu, display_menu, menu_parent

from .store import Store, effective
from .views import (
    Admin,
    Confirm,
    Edit,
    Intake,
    Manage,
    PublicPanel,
    TicketButton,
    private_admin_parent,
    safe,
    ticket_controls,
)

log = logging.getLogger(__name__)
ACTIVE = {"open", "waiting_staff", "waiting_user", "pending_approval"}


def staff(member, settings):
    return member.guild_permissions.administrator or bool(
        {role.id for role in member.roles}
        & set(
            settings["support_roles"]
            + settings["manager_roles"]
            + settings.get("approver_roles", [])
        )
    )


def manager(member, settings):
    return member.guild_permissions.administrator or bool(
        {role.id for role in member.roles} & set(settings["manager_roles"])
    )


def approver(member, settings):
    return manager(member, settings) or bool(
        {role.id for role in member.roles} & set(settings.get("approver_roles", []))
    )


def channel_name(value):
    return re.sub(r"[^\w-]+", "-", value.lower()).strip("-")[:90] or "ticket"


class Tickets(commands.Cog):
    ticket = app_commands.Group(name="ticket", description="Private support tickets and setup")

    def __init__(self, bot):
        self.bot = bot
        self.locks = {}
        self.config_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()
        self.last_prune = 0

    def lock(self, guild):
        return self.locks.setdefault(guild, asyncio.Lock())

    async def cog_load(self):
        self.store = await Store.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.bot.add_dynamic_items(TicketButton)
        self.maintenance.start()

    async def cog_unload(self):
        self.maintenance.cancel()
        task = self.maintenance.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.bot.remove_dynamic_items(TicketButton)
        await self.store.close()

    @ticket.command(
        name="setup", description="Configure panels, ticket types, staff and logs privately"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_command(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        await self.admin(i, await private_admin_parent(self, i))

    @ticket.command(name="manage", description="Open controls for this ticket")
    @app_commands.guild_only()
    async def manage_command(self, i: discord.Interaction):
        ticket = await self.store.by_channel(i.guild_id, i.channel_id)
        if not ticket:
            return await send_private(i, "Use this command inside a ticket channel.")
        await self.action(i, ticket["id"], "manage")

    async def open_menu(self, i, *, command_center=None):
        parent = menu_parent(command_center, i)
        if parent is None or not await parent.interaction_check(i):
            return
        await defer_menu(i, parent)
        await self.admin(i, parent)

    async def admin(self, i, parent, **kwargs):
        parent = menu_parent(parent, i)
        revision, config = await self.store.config(parent.guild_id)
        selected = kwargs.get("selected")
        if selected and selected not in config["panels"] and selected not in config["types"]:
            kwargs = dict(notice="That entry is no longer available.")
        records = await self.store.listed(parent.guild_id) if kwargs.get("page") == "queue" else ()
        view = Admin(self, parent, revision, config, tickets=records, **kwargs)
        uploads = await self.appearance(parent.guild_id, view)
        try:
            await display_menu(i, view, uploads, parent)
        finally:
            for upload in uploads:
                upload.close()

    async def appearance(self, guild, view):
        from ky_bot.design.menu_design import automation_profile, render_menu
        from ky_bot.views.menu_strips import strip_files

        _, profiles = await self.bot.server_settings.repository.get_designs(guild)
        profile = automation_profile(profiles, "tickets")
        profile["title"] = "COMMAND CENTER 𖥸 TICKETS"
        profile["_rotated_divider"] = True
        header, footer = await asyncio.to_thread(render_menu, profile, "TICKETS")
        from ky_bot.design.collection import COLOURS

        view.panel.accent_colour = int(COLOURS[profile["style"]][1:], 16)
        return [
            discord.File(io.BytesIO(header), filename="tickets_header.png"),
            discord.File(io.BytesIO(footer), filename="tickets_footer.png"),
        ] + (
            strip_files(view.groups, profile["style"], profile.get("text_color"))
            if getattr(view, "groups", None)
            else []
        )

    def validate_destination(self, channel, *, category=False):
        expected = discord.CategoryChannel if category else discord.TextChannel
        if not isinstance(channel, expected):
            raise ValueError(
                "Choose an existing category." if category else "Choose an existing text channel."
            )
        required = (
            ("view_channel", "manage_channels", "manage_roles")
            if category
            else ("view_channel", "send_messages", "embed_links", "read_message_history")
        )
        missing = [
            key.replace("_", " ")
            for key in required
            if not getattr(channel.permissions_for(channel.guild.me), key)
        ]
        if missing:
            raise ValueError("KY BOT needs: " + ", ".join(missing) + ".")

    async def validate_log(self, channel, settings):
        self.validate_destination(channel)
        if not channel.permissions_for(channel.guild.me).attach_files:
            raise ValueError("KY BOT needs Attach Files in log and transcript channels.")
        permitted = set(
            settings["support_roles"]
            + settings["manager_roles"]
            + settings.get("approver_roles", [])
        )
        _, config = await self.store.config(channel.guild.id)
        permitted.update(config["defaults"]["support_roles"] + config["defaults"]["manager_roles"])
        for kind in list(config["types"].values()) + list(config["panels"].values()):
            combined = config["defaults"] | kind["overrides"]
            permitted.update(
                combined["support_roles"]
                + combined["manager_roles"]
                + combined.get("approver_roles", [])
            )
        if channel.permissions_for(channel.guild.default_role).view_channel:
            raise ValueError("Choose a staff-only log channel hidden from @everyone.")
        for role in channel.guild.roles:
            if role.is_default() or role.managed or role.permissions.administrator:
                continue
            if role.id not in permitted and channel.permissions_for(role).view_channel:
                raise ValueError(
                    "Log access includes a role outside the configured ticket staff. "
                    "Adjust channel permissions or ticket staff roles."
                )
        for member in channel.overwrites:
            if (
                isinstance(member, discord.Member)
                and not member.bot
                and channel.permissions_for(member).view_channel
                and not staff(member, settings)
            ):
                raise ValueError(
                    "Log access includes a member outside the configured ticket staff."
                )

    async def validate_type(self, guild, settings):
        category = guild.get_channel(settings["category"])
        self.validate_destination(category, category=True)
        if not settings["support_roles"] and not settings["manager_roles"]:
            raise ValueError("Select at least one support or ticket manager role.")
        if settings.get("require_approval") and not (
            settings.get("approver_roles") or settings["manager_roles"]
        ):
            raise ValueError(
                "Select ticket approvers or ticket managers before requiring approval."
            )
        if any(
            guild.get_role(key) is None or guild.get_role(key).is_default()
            for key in settings["support_roles"]
            + settings["manager_roles"]
            + settings.get("approver_roles", [])
        ):
            raise ValueError("A staff role was removed. Reselect staff roles.")
        if not settings["log_channels"] or not settings["transcript_channels"]:
            raise ValueError("Choose at least one activity log and one transcript destination.")
        for key in set(settings["log_channels"] + settings["transcript_channels"]):
            await self.validate_log(guild.get_channel(key), settings)

    async def publish(self, guild, token, expected):
        async with self.config_lock:
            revision, config = await self.store.config(guild.id)
            if revision != expected:
                raise ValueError("Settings changed. Refresh before publishing.")
            panel = config["panels"][token]
            destination = guild.get_channel(panel["channel"])
            self.validate_destination(destination)
            if not panel["types"]:
                raise ValueError("Choose at least one ticket type for this panel.")
            for kind in panel["types"]:
                if config["types"][kind]["enabled"]:
                    await self.validate_type(guild, effective(config, kind, token))
            from .artwork import public_files

            uploads = await asyncio.to_thread(public_files, panel)
            try:
                if uploads and not destination.permissions_for(guild.me).attach_files:
                    raise ValueError("KY BOT needs Attach Files to publish panel artwork.")
                await self._publish_panel(guild, token, revision, config, destination, uploads)
            finally:
                for file in uploads:
                    file.close()

    async def _publish_panel(self, guild, token, revision, config, destination, uploads):
        panel = config["panels"][token]
        view = PublicPanel(config, token)
        old_channel = guild.get_channel(panel["published_channel"])
        old = (
            old_channel.get_partial_message(panel["message"])
            if old_channel and panel["message"]
            else None
        )
        message = None
        if old and old_channel.id == destination.id:
            try:
                message = await old.edit(
                    view=view, attachments=uploads, allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.NotFound:
                pass
        created = message is None
        if created:
            message = await destination.send(
                view=view, files=uploads, allowed_mentions=discord.AllowedMentions.none()
            )
        updated = copy.deepcopy(config)
        updated["panels"][token].update(message=message.id, published_channel=destination.id)
        try:
            await self.store.configure(guild.id, revision, updated)
        except BaseException:
            if created:
                await message.delete()
            raise
        if old and old.id != message.id:
            try:
                await old.delete()
            except discord.HTTPException:
                log.warning("Old ticket panel could not be removed; its message ID is retired.")

    async def eligible(self, i, panel_id, kind_id, message_id):
        if i.guild is None or not isinstance(i.user, discord.Member):
            raise ValueError("Open tickets in the server.")
        _, config = await self.store.config(i.guild_id)
        panel = config["panels"].get(panel_id)
        if (
            not config["enabled"]
            or not panel
            or not panel["enabled"]
            or panel["message"] != message_id
            or kind_id not in panel["types"]
        ):
            raise ValueError("This panel is disabled or has moved. Use the current ticket panel.")
        settings = effective(config, kind_id, panel_id)
        roles = {role.id for role in i.user.roles}
        if roles & set(settings["blocked_roles"]) or (
            settings["allowed_roles"] and not roles & set(settings["allowed_roles"])
        ):
            raise ValueError("Your roles do not allow opening this ticket type.")
        now = datetime.now(ZoneInfo(settings["timezone"]))
        if not settings["support_start"] <= now.hour < settings["support_end"]:
            raise ValueError(
                f"Support opens daily from {settings['support_start']:02d}:00 "
                f"to {settings['support_end']:02d}:00 ({settings['timezone']})."
            )
        return config, settings

    def overwrites(self, guild, ticket, *, closed=False):
        values = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                manage_roles=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        for role_id in (
            ticket["settings"]["support_roles"]
            + ticket["settings"]["manager_roles"]
            + ticket["settings"].get("approver_roles", [])
        ):
            role = guild.get_role(role_id)
            if role:
                values[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                )
        for member_id in [ticket["owner"]] + ticket["guests"]:
            member = guild.get_member(member_id)
            if member:
                values[member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=not closed,
                    read_message_history=True,
                    attach_files=not closed,
                    embed_links=not closed,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                )
        return values

    async def create_ticket(self, i, panel_id, kind_id, answers, message_id):
        async with self.lock(i.guild_id):
            config, settings = await self.eligible(i, panel_id, kind_id, message_id)
            questions = config["types"][kind_id]["questions"]
            if [q for q, _ in answers] != questions:
                raise ValueError("The intake form changed. Reopen the ticket form.")
            await self.validate_type(i.guild, settings)
            ticket = await self.store.reserve(
                i.guild_id, i.user.id, panel_id, kind_id, answers, expected_config=config
            )
            try:
                channel = await i.guild.create_text_channel(
                    name=f"{channel_name(ticket['type_name'])[:55]}-{ticket['id'][:8]}",
                    category=i.guild.get_channel(settings["category"]),
                    overwrites=self.overwrites(
                        i.guild, ticket, closed=settings.get("require_approval", False)
                    ),
                    topic=f"KY BOT ticket {ticket['id']}",
                    reason="Private support ticket",
                )
            except discord.HTTPException as error:
                if 400 <= error.status < 500:
                    await self.store.update(ticket, state="failed")
                raise
            ticket = await self.store.update(
                ticket,
                channel=channel.id,
                state="pending_approval" if settings.get("require_approval") else "waiting_staff",
            )
            await self.store.event(ticket, "TICKET OPENED", i.user.id)
            try:
                await self.send_intro(ticket, channel)
            except discord.HTTPException:
                log.warning("Ticket introduction will be retried after channel creation")
        await i.edit_original_response(content=f"Your private ticket is ready: {channel.mention}")

    async def send_intro(self, ticket, channel):
        settings = ticket["settings"]
        answers = ticket["answers"]
        embed = discord.Embed(
            title=f"{ticket['type_name']} · {ticket['id'][:8]}",
            description=(
                "Waiting for staff approval. You can reply after approval.\n\n"
                if ticket["state"] == "pending_approval"
                else ""
            )
            + settings["welcome"],
            color=0xBDC3CA,
        )
        embed.add_field(name="Opened by", value=f"<@{ticket['owner']}>")
        for question, answer in answers:
            embed.add_field(name=question, value=answer[:1000] or "—", inline=False)
        message = await channel.send(
            content=(
                "Support team: " + " ".join(f"<@&{role}>" for role in settings["support_roles"])
            )
            if settings["ping_staff"]
            else None,
            embed=embed,
            view=ticket_controls(ticket),
            allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=role) for role in settings["support_roles"]]
                if settings["ping_staff"]
                else False,
                users=False,
                everyone=False,
                replied_user=False,
            ),
        )
        await self.store.update(ticket, intro_sent=True, intro_message=message.id)

    async def access(self, i, token, *, staff_only=False):
        ticket = await self.store.get(token)
        if not ticket or i.guild_id != ticket["guild"] or not isinstance(i.user, discord.Member):
            raise ValueError("This ticket is unavailable here.")
        if ticket["state"] in {"deleted", "failed", "creating"}:
            raise ValueError("This ticket is no longer available.")
        is_staff = staff(i.user, ticket["settings"])
        if not is_staff and (staff_only or i.user.id not in [ticket["owner"]] + ticket["guests"]):
            raise ValueError("You do not have permission to manage this ticket.")
        return ticket, is_staff

    async def action(self, i, token, action, kind="0"):
        if action == "open":
            config, _ = await self.eligible(i, token, kind, i.message.id)
            questions = config["types"][kind]["questions"]
            if questions:
                await i.response.send_modal(
                    Intake(self, token, kind, questions, i.user.id, i.message.id)
                )
            else:
                await i.response.defer(ephemeral=True, thinking=True)
                await self.create_ticket(i, token, kind, [], i.message.id)
            return
        ticket, is_staff = await self.access(i, token)
        if action == "approve":
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.lock(i.guild_id):
                ticket, _ = await self.access(i, token, staff_only=True)
                if not approver(i.user, ticket["settings"]):
                    raise ValueError("Only ticket approvers or managers can approve requests.")
                if ticket["state"] != "pending_approval":
                    raise ValueError("This ticket is not waiting for approval.")
                channel = await self.get_channel(ticket)
                await channel.edit(overwrites=self.overwrites(i.guild, ticket))
                ticket = await self.store.update(
                    ticket,
                    state="waiting_staff",
                    approved_by=i.user.id,
                    waiting_since=time.time(),
                    last_activity=time.time(),
                )
                await self.store.event(ticket, "TICKET APPROVED", i.user.id)
                await channel.send(
                    "Ticket approved. You can now reply below.", view=ticket_controls(ticket)
                )
            await send_private(i, "Ticket approved.")
        elif action in {"accept", "keep"}:
            if i.user.id != ticket["owner"] or not ticket.get("close_requested_by"):
                raise ValueError("Only the ticket opener can answer a pending close request.")
            await i.response.defer(ephemeral=True, thinking=True)
            if action == "accept":
                await self.resolve(i, token, "close", "Member confirmed resolution")
            else:
                async with self.lock(i.guild_id):
                    ticket, _ = await self.access(i, token)
                    await self.store.update(ticket, close_requested_by=0)
                    await self.store.event(ticket, "TICKET CLOSE REQUEST DECLINED", i.user.id)
                await send_private(i, "The ticket will stay open.")
        elif action in {"manage", "rate"}:
            await i.response.defer(ephemeral=True, thinking=True)
            await i.followup.send(
                view=Manage(
                    self,
                    ticket,
                    i.user.id,
                    staff=is_staff,
                    can_approve=approver(i.user, ticket["settings"]),
                ),
                ephemeral=True,
            )
        elif action in {"close", "delete", "decline"}:
            if action == "decline" and not approver(i.user, ticket["settings"]):
                raise ValueError("Only ticket approvers or managers can decline requests.")
            if action == "delete" and not is_staff:
                raise ValueError("Only ticket staff can delete channels.")
            if (
                action == "close"
                and not is_staff
                and (
                    i.user.id != ticket["owner"]
                    or (
                        not ticket["settings"]["user_close"]
                        and not ticket.get("close_requested_by")
                    )
                )
            ):
                raise ValueError("Only staff can close this ticket.")
            form = Manage(self, ticket, i.user.id, staff=is_staff)

            async def commit(i, values):
                reason = values["reason"] or "Resolved"
                await i.followup.send(
                    view=Confirm(self, ticket, i.user.id, action, reason), ephemeral=True
                )

            await i.response.send_modal(
                Edit(form, f"{action.title()} ticket", [("reason", "Reason", "", 500)], commit)
            )
        elif action == "transcript":
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.lock(i.guild_id):
                ticket, _ = await self.access(i, token)
                body = await self.transcript(ticket)
            await i.followup.send(
                file=discord.File(io.BytesIO(body.encode()), filename=f"ticket-{token[:8]}.html"),
                ephemeral=True,
            )
        elif action == "reopen":
            if not is_staff:
                raise ValueError("Ask staff to reopen a closed ticket.")
            await i.response.defer(ephemeral=True, thinking=True)
            async with self.lock(i.guild_id):
                ticket, _ = await self.access(i, token, staff_only=True)
                if ticket["state"] != "closed":
                    raise ValueError("This ticket is already open.")
                pending = ticket["settings"].get("require_approval", False) and not ticket.get(
                    "approved_by"
                )
                channel = await self.get_channel(ticket)
                await channel.edit(overwrites=self.overwrites(i.guild, ticket, closed=pending))
                ticket = await self.store.update(
                    ticket,
                    state="pending_approval" if pending else "waiting_staff",
                    closed_at=0,
                    paused=False,
                    last_activity=time.time(),
                    waiting_since=time.time(),
                    reminded=False,
                    escalated=False,
                )
                await self.store.event(ticket, "TICKET REOPENED", i.user.id)
                await channel.send("Ticket reopened.", view=ticket_controls(ticket))
            await send_private(i, "Ticket reopened.")
        elif action in {"claim", "pause", "request_close"}:
            await self.change(i, token, action, None)
        elif action == "notes":
            await self.access(i, token, staff_only=True)
            await i.response.defer(ephemeral=True, thinking=True)
            view = Manage(self, ticket, i.user.id, staff=True)

            async def commit(i, values):
                await self.change(i, token, "note", values["note"])

            from .views import button

            panel = discord.ui.View(timeout=300)

            async def add_note(i):
                await i.response.send_modal(
                    Edit(
                        view,
                        "Private staff note",
                        [("note", "Note (staff only)", "", 1000)],
                        commit,
                    )
                )

            panel.add_item(button("Add note", add_note))

            async def allowed(i):
                return i.user.id == view.user

            panel.interaction_check = allowed
            text = (
                "\n\n".join(f"<@{n['actor']}>: {safe(n['text'])}" for n in ticket["notes"][-3:])
                or "No staff notes."
            )
            await i.followup.send(text[:3500], view=panel, ephemeral=True)
        elif action == "edit":
            await self.access(i, token, staff_only=True)
            await i.response.defer(ephemeral=True, thinking=True)
            view = await self.editor(i, ticket)
            await i.followup.send(view=view, ephemeral=True)

    async def editor(self, i, ticket, offset=0):
        from .views import Panel, button

        view = Panel()
        owner = i.user.id

        async def allowed(i):
            return i.user.id == owner and i.guild_id == ticket["guild"]

        view.interaction_check = allowed
        view.text("**EDIT TICKET**\nAll changes are recorded for staff.")
        form = Manage(self, ticket, owner, staff=True)

        async def rename(i):
            async def commit(i, values):
                await self.change(i, ticket["id"], "rename", values["name"])

            await i.response.send_modal(
                Edit(form, "Rename ticket", [("name", "Channel name", "", 80)], commit)
            )

        view.row("CHANNEL NAME", "Give this ticket a descriptive name.", "Rename", rename)
        people = discord.ui.UserSelect(placeholder="Add a participant", min_values=1, max_values=1)

        async def add(i):
            await self.change(i, ticket["id"], "add", people.values[0].id)

        view.picker(people, add)
        if ticket["guests"]:
            remove = discord.ui.Select(
                placeholder="Remove a participant",
                options=[
                    discord.SelectOption(
                        label=str(i.guild.get_member(key) or key)[:100], value=str(key)
                    )
                    for key in ticket["guests"][:25]
                ],
            )

            async def remove_guest(i):
                await self.change(i, ticket["id"], "remove", int(remove.values[0]))

            view.picker(remove, remove_guest)
        assign = discord.ui.UserSelect(
            placeholder="Assign to a staff member", min_values=1, max_values=1
        )

        async def assign_staff(i):
            await self.change(i, ticket["id"], "assign", assign.values[0].id)

        view.picker(assign, assign_staff)
        _, config = await self.store.config(i.guild_id)
        kinds = [(key, kind) for key, kind in config["types"].items() if kind["enabled"]]
        # Native selects have 25 options; page through all configured types.
        if kinds:
            transfer = discord.ui.Select(
                placeholder="Transfer ticket type",
                options=[
                    discord.SelectOption(label=kind["name"], value=key)
                    for key, kind in kinds[offset : offset + 25]
                ],
            )

            async def transfer_ticket(i):
                await self.change(i, ticket["id"], "transfer", transfer.values[0])

            view.picker(transfer, transfer_ticket)
            if len(kinds) > 25:

                def turn(delta):
                    async def callback(event):
                        await event.response.defer()
                        current, _ = await self.access(event, ticket["id"], staff_only=True)
                        await event.edit_original_response(
                            view=await self.editor(event, current, max(0, offset + delta))
                        )

                    return callback

                view.panel.add_item(
                    discord.ui.ActionRow(
                        button("Previous", turn(-25), disabled=offset == 0),
                        button("Next", turn(25), disabled=offset + 25 >= len(kinds)),
                    )
                )
        view.finish()
        return view

    async def change(self, i, token, action, value):
        if not i.response.is_done():
            await i.response.defer(ephemeral=True, thinking=True)
        async with self.lock(i.guild_id):
            ticket, _ = await self.access(i, token, staff_only=action != "rating")
            if ticket["state"] == "pending_approval" and action in {
                "state",
                "reply",
                "transfer",
                "add",
                "remove",
            }:
                raise ValueError(
                    "Approve this ticket before changing its status, team or participants."
                )
            if action == "rating":
                if (
                    i.user.id != ticket["owner"]
                    or ticket["state"] != "closed"
                    or not ticket["settings"]["feedback"]
                ):
                    raise ValueError("Only the opener can rate a closed ticket.")
                if ticket["rating"]:
                    raise ValueError("You have already rated this ticket.")
                if str(value) not in {"1", "2", "3", "4", "5"}:
                    raise ValueError("Choose a rating from one to five.")
                ticket = await self.store.update(ticket, rating=int(value))
            else:
                if action != "note" and ticket["state"] == "closed":
                    raise ValueError("Reopen this ticket before changing it.")
                changes = {}
                if action == "claim":
                    if ticket["claimed"] not in {0, i.user.id} and not manager(
                        i.user, ticket["settings"]
                    ):
                        raise ValueError(
                            "This ticket is already claimed. A ticket manager can reassign it."
                        )
                    changes["claimed"] = 0 if ticket["claimed"] == i.user.id else i.user.id
                elif action == "assign":
                    if not manager(i.user, ticket["settings"]):
                        raise ValueError("Only a ticket manager can reassign ownership.")
                    member = i.guild.get_member(value)
                    if member is None or member.bot or not staff(member, ticket["settings"]):
                        raise ValueError("Choose a configured ticket staff member.")
                    changes["claimed"] = value
                elif action == "request_close":
                    controls = discord.ui.View(timeout=None)
                    controls.add_item(TicketButton(token, "accept", label="Yes, resolved"))
                    controls.add_item(TicketButton(token, "keep", label="Keep open"))
                    await self.store.update(ticket, close_requested_by=i.user.id)
                    await (await self.get_channel(ticket)).send(
                        "Has your issue been resolved? Only the ticket opener can respond.",
                        view=controls,
                    )
                elif action == "reply":
                    reply = ticket["settings"].get("saved_replies", {}).get(value)
                    if not reply:
                        raise ValueError("That saved reply is unavailable.")
                    await (await self.get_channel(ticket)).send(
                        reply, allowed_mentions=discord.AllowedMentions.none()
                    )
                    changes.update(
                        state="waiting_user",
                        last_activity=time.time(),
                        waiting_since=time.time(),
                        reminded=False,
                        warning_at=0,
                        escalated=False,
                    )
                elif action == "pause":
                    changes["paused"] = not ticket["paused"]
                    if ticket["paused"]:
                        changes.update(
                            last_activity=time.time(),
                            waiting_since=time.time(),
                            reminded=False,
                            warning_at=0,
                            escalated=False,
                        )
                elif action == "state":
                    if value not in {"waiting_staff", "waiting_user"}:
                        raise ValueError("Unknown ticket status.")
                    changes.update(
                        state=value, waiting_since=time.time(), reminded=False, escalated=False
                    )
                elif action == "priority":
                    if value not in {"Low", "Normal", "High", "Urgent"}:
                        raise ValueError("Unknown priority.")
                    changes["priority"] = value
                elif action == "note":
                    if not value:
                        raise ValueError("Enter a staff note.")
                    if ticket["state"] == "closed" and not await self.store.archive(ticket):
                        raise ValueError(
                            "This archive has expired. Reopen the ticket before adding new notes."
                        )
                    if len(ticket["notes"]) >= 100:
                        raise ValueError("This ticket already contains 100 staff notes.")
                    changes["notes"] = ticket["notes"] + [
                        dict(actor=i.user.id, text=value, created=time.time())
                    ]
                elif action == "rename":
                    channel = await self.get_channel(ticket)
                    await channel.edit(name=channel_name(value), reason="Ticket renamed by staff")
                elif action in {"add", "remove"}:
                    member = i.guild.get_member(value)
                    if value == ticket["owner"] or member is None or member.bot:
                        raise ValueError("Choose a current member other than the ticket opener.")
                    guests = set(ticket["guests"])
                    guests.add(value) if action == "add" else guests.discard(value)
                    if len(guests) > 25:
                        raise ValueError("A ticket can have at most 25 extra participants.")
                    changes["guests"] = sorted(guests)
                    updated = ticket | changes
                    await (await self.get_channel(ticket)).edit(
                        overwrites=self.overwrites(i.guild, updated)
                    )
                elif action == "transfer":
                    if not manager(i.user, ticket["settings"]):
                        raise ValueError("Only ticket managers can transfer tickets between teams.")
                    _, config = await self.store.config(i.guild_id)
                    settings = effective(config, value, ticket["panel"])
                    if not staff(i.user, settings):
                        raise ValueError("You must also have access to the destination team.")
                    await self.validate_type(i.guild, settings)
                    changes.update(
                        type=value,
                        type_name=config["types"][value]["name"],
                        settings=settings,
                        claimed=0,
                    )
                    updated = ticket | changes
                    await (await self.get_channel(ticket)).edit(
                        category=i.guild.get_channel(settings["category"]),
                        overwrites=self.overwrites(i.guild, updated),
                        sync_permissions=False,
                    )
                    await self.store.event(
                        ticket, "TICKET TRANSFERRED OUT", i.user.id, changes["type_name"]
                    )
                else:
                    raise ValueError("Unknown ticket action.")
                ticket = await self.store.update(ticket, **changes)
            await self.store.event(
                ticket,
                "TICKET " + action.upper(),
                i.user.id,
                "Staff note updated" if action == "note" else str(value or ""),
            )
        await send_private(i, "Ticket updated. Reopen the controls to see its current state.")

    async def get_channel(self, ticket):
        channel = self.bot.get_channel(ticket["channel"]) or await self.bot.fetch_channel(
            ticket["channel"]
        )
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != ticket["guild"]:
            raise ValueError("This ticket channel is unavailable.")
        return channel

    async def transcript(self, ticket):
        channel = await self.get_channel(ticket)
        self.validate_destination(channel)
        entries = []
        size = 0
        budget = min(channel.guild.filesize_limit, 7_500_000) - 100_000
        async for message in channel.history(limit=None, oldest_first=True):
            content = html.escape(message.content)
            embeds = "\n".join(
                json.dumps(embed.to_dict(), ensure_ascii=False) for embed in message.embeds
            )
            links = "".join(
                f'<li><a href="{html.escape(a.url, quote=True)}">{html.escape(a.filename)}</a></li>'
                for a in message.attachments
            )
            row = (
                f"<article><header>{html.escape(str(message.author))} ({message.author.id}) · "
                f"{message.created_at.isoformat()} · message {message.id}</header>"
                f"<pre>{content}</pre><pre>{html.escape(embeds)}</pre>"
                f"<ul>{links}</ul></article>"
            )
            size += len(row.encode())
            if size > budget:
                raise ValueError(
                    "This transcript exceeds the safe upload size. The ticket channel "
                    "has been preserved; export it in smaller parts before deletion."
                )
            entries.append(row)
        metadata = json.dumps(
            {
                key: ticket[key]
                for key in ("id", "type_name", "owner", "created", "priority", "reason")
            },
            ensure_ascii=False,
            indent=2,
        )
        body = (
            (
                "<!doctype html><html><head><meta charset='utf-8'><meta "
                "http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
                "style-src 'unsafe-inline'\"><title>KY BOT "
                "ticket</title><style>body{background:#181a20;color:#e5e7eb;font:15"
                "px system-ui;max-width:960px;margin:40px "
                "auto;padding:20px}article{border-top:1px solid #555;padding:15px "
                "0}header{color:#b9c3d2}pre{white-space:pre-wrap;overflow-wrap:anyw"
                "here}a{color:#b6d4ff}</style></head><body><h1>KY BOT ticket "
                "transcript</h1><p>Current messages at export time. Attachments "
                "are Discord links and may expire. Deleted messages and prior "
                "edits are not available.</p><pre>"
            )
            + html.escape(metadata)
            + "</pre>"
            + "".join(entries)
            + "</body></html>"
        )
        await self.store.archive(ticket, body)
        return body

    async def resolve(self, i, token, action, reason):
        async with self.lock(i.guild_id):
            ticket, is_staff = await self.access(i, token)
            if action == "delete" and not is_staff:
                raise ValueError("Only staff can delete a ticket.")
            if not is_staff and (
                i.user.id != ticket["owner"]
                or (not ticket["settings"]["user_close"] and not ticket.get("close_requested_by"))
            ):
                raise ValueError("Only authorized staff can close this ticket.")
            if action == "delete":
                if ticket["state"] != "closed":
                    raise ValueError("Close the ticket before deleting its channel.")
                ticket = await self.store.update(ticket, reason=reason)
                # Export and queue a fresh archive before irreversible removal.
                await self.transcript(ticket)
                await self.store.event(ticket, "TICKET TRANSCRIPT", i.user.id, archive=True)
                await self.deliver_events(only_ticket=token)
                if await self.store.pending_archives(token):
                    raise ValueError(
                        "A transcript destination could not be reached. The ticket channel "
                        "has been preserved; fix delivery and try again."
                    )
                await (await self.get_channel(ticket)).delete(
                    reason=f"Ticket {token[:8]} deleted by staff"
                )
                ticket = await self.store.update(ticket, state="deleted")
                await self.store.event(ticket, "TICKET DELETED", i.user.id, reason)
            elif action in {"close", "decline"}:
                if action == "decline":
                    if (
                        not approver(i.user, ticket["settings"])
                        or ticket["state"] != "pending_approval"
                    ):
                        raise ValueError("Only an approver can decline a pending ticket.")
                    reason = "Declined: " + reason
                elif (
                    ticket["state"] == "pending_approval"
                    and is_staff
                    and i.user.id != ticket["owner"]
                    and not approver(i.user, ticket["settings"])
                ):
                    raise ValueError(
                        "Only ticket approvers or managers can resolve pending requests."
                    )
                if ticket["state"] == "closed":
                    raise ValueError("This ticket is already closed.")
                await self.close_ticket(ticket, i.user.id, reason)
            else:
                raise ValueError("Unknown resolution action.")
        await send_private(
            i,
            "Ticket deleted."
            if action == "delete"
            else "Ticket declined and transcript saved."
            if action == "decline"
            else "Ticket closed and transcript saved.",
        )

    async def close_ticket(self, ticket, actor, reason):
        channel = await self.get_channel(ticket)
        ticket = ticket | {"reason": reason}
        await self.transcript(ticket)
        await channel.edit(overwrites=self.overwrites(channel.guild, ticket, closed=True))
        ticket = await self.store.update(
            ticket, state="closed", closed_at=time.time(), reason=reason, close_requested_by=0
        )
        if ticket.get("intro_message"):
            try:
                await channel.get_partial_message(ticket["intro_message"]).edit(
                    view=ticket_controls(ticket)
                )
            except discord.HTTPException:
                log.warning("Ticket header controls could not be refreshed after closure")
        await self.store.event(ticket, "TICKET CLOSED", actor, reason)
        await self.store.event(ticket, "TICKET TRANSCRIPT", actor, archive=True)
        controls = discord.ui.View(timeout=None)
        controls.add_item(TicketButton(ticket["id"], "reopen"))
        if ticket["settings"]["feedback"]:
            controls.add_item(TicketButton(ticket["id"], "rate", label="Rate support"))
        await channel.send(
            "Your ticket request was declined.\n" + safe(reason.removeprefix("Declined: "))
            if reason.startswith("Declined: ")
            else ticket["settings"]["close_message"],
            view=controls,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if ticket["settings"]["dm_transcript"]:
            member = channel.guild.get_member(ticket["owner"])
            if member:
                body = await self.store.archive(ticket)
                try:
                    await member.send(
                        "Your ticket transcript:",
                        file=discord.File(
                            io.BytesIO(body.encode()), filename=f"ticket-{ticket['id'][:8]}.html"
                        ),
                    )
                except discord.HTTPException:
                    await self.store.event(
                        ticket,
                        "TICKET DM UNAVAILABLE",
                        0,
                        "The transcript remains in the staff archive.",
                    )

    async def deliver_events(self, *, only_ticket=None):
        async with self.delivery_lock:
            await self._deliver_events(only_ticket=only_ticket)

    async def _deliver_events(self, *, only_ticket=None):
        for event_id, token, destination, raw in await self.store.events(only_ticket):
            if only_ticket and token != only_ticket:
                continue
            ticket = await self.store.get(token)
            data = json.loads(raw)
            try:
                channel = self.bot.get_channel(destination) or await self.bot.fetch_channel(
                    destination
                )
                if channel.guild.id != ticket["guild"]:
                    raise ValueError("Wrong guild log destination.")
                await self.validate_log(channel, ticket["settings"])
                embed = discord.Embed(
                    title=data["title"],
                    color=0xBDC3CA,
                    description=f"**Ticket:** {token[:8]} · {safe(ticket['type_name'])}\n"
                    f"**Member:** <@{ticket['owner']}>\n**Actor:** "
                    + (f"<@{data['actor']}>" if data["actor"] else "Automation")
                    + f"\n{safe(data['detail'])}",
                )
                if data["archive"]:
                    body = await self.store.archive(ticket)
                    if not body:
                        raise ValueError("Ticket archive is unavailable.")
                    style_log(embed)
                    embed.set_footer(text="KY BOT")
                    await channel.send(
                        embed=embed,
                        file=discord.File(
                            io.BytesIO(body.encode()), filename=f"ticket-{token[:8]}.html"
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await send_log(channel, embed)
                await self.store.delivered(event_id)
            except (discord.HTTPException, ValueError, AttributeError):
                log.warning(
                    "Ticket event %s delivery pending for channel %s", event_id, destination
                )
                await self.store.retry(event_id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if (
            not message.guild
            or message.author.bot
            or not isinstance(message.author, discord.Member)
        ):
            return
        async with self.lock(message.guild.id):
            ticket = await self.store.by_channel(message.guild.id, message.channel.id)
            if (
                ticket is None
                or ticket["state"] not in ACTIVE
                or ticket["state"] == "pending_approval"
            ):
                return
            if message.author.id not in [ticket["owner"]] + ticket["guests"] and not staff(
                message.author, ticket["settings"]
            ):
                return
            state = (
                "waiting_user"
                if staff(message.author, ticket["settings"])
                and message.author.id != ticket["owner"]
                else "waiting_staff"
            )
            now = time.time()
            changes = dict(state=state, last_activity=now)
            if not ticket.get("first_response") and state == "waiting_user":
                changes["first_response"] = now
            if state != ticket["state"]:
                changes.update(waiting_since=now, reminded=False, escalated=False, warning_at=0)
            # User replies restart the inactivity clock even in the same state.
            if state == "waiting_staff":
                changes["reminded"] = False
                changes["warning_at"] = 0
            await self.store.update(ticket, **changes)

    @tasks.loop(seconds=60)
    async def maintenance(self):
        try:
            await self.deliver_events()
            for ticket in await self.store.listed():
                async with self.lock(ticket["guild"]):
                    ticket = await self.store.get(ticket["id"])
                    try:
                        await self.maintain_ticket(ticket)
                    except (discord.HTTPException, ValueError):
                        log.warning(
                            "Ticket %s maintenance deferred", ticket["id"][:8], exc_info=True
                        )
            if time.time() - self.last_prune > 86400:
                for guild, config in await self.store.all_configs():
                    # Honor the longest configured retention within this guild.
                    retention = max(
                        [config["defaults"]["retention_days"]]
                        + [
                            kind["overrides"].get(
                                "retention_days", config["defaults"]["retention_days"]
                            )
                            for kind in config["types"].values()
                        ]
                    )
                    await self.store.prune(guild, retention)
                self.last_prune = time.time()
        except Exception:
            log.exception("Ticket maintenance will retry next minute")

    @maintenance.before_loop
    async def before_maintenance(self):
        await self.bot.wait_until_ready()

    async def maintain_ticket(self, ticket):
        guild = self.bot.get_guild(ticket["guild"])
        if guild is None:
            return
        if ticket["state"] == "creating" and time.time() - ticket["created"] > 120:
            # Recover a successful Discord create if the process stopped before its DB update.
            channels = await guild.fetch_channels()
            channel = next(
                (
                    c
                    for c in channels
                    if getattr(c, "topic", None) == f"KY BOT ticket {ticket['id']}"
                ),
                None,
            )
            if channel:
                ticket = await self.store.update(
                    ticket,
                    channel=channel.id,
                    state="pending_approval"
                    if ticket["settings"].get("require_approval")
                    else "waiting_staff",
                )
                await self.send_intro(ticket, channel)
                await self.store.event(ticket, "TICKET RECOVERED", 0)
            else:
                await self.store.update(ticket, state="failed")
            return
        if ticket["state"] not in ACTIVE or ticket["paused"]:
            return
        try:
            channel = await self.get_channel(ticket)
        except discord.NotFound:
            ticket = await self.store.update(ticket, state="deleted")
            await self.store.event(ticket, "TICKET CHANNEL REMOVED", 0)
            return
        if not ticket.get("intro_sent", True):
            await self.send_intro(ticket, channel)
        settings = ticket["settings"]
        hours = (time.time() - ticket["last_activity"]) / 3600
        if ticket["state"] == "waiting_user":
            if (
                settings["reminder_hours"]
                and hours >= settings["reminder_hours"]
                and not ticket["reminded"]
            ):
                channel = await self.get_channel(ticket)
                await channel.send(
                    "This ticket is waiting for your response."
                    + (
                        f" It will close after {settings['auto_close_hours']} hours "
                        "without a reply."
                        if settings["auto_close_hours"]
                        else ""
                    )
                )
                ticket = await self.store.update(ticket, reminded=True, warning_at=time.time())
            if settings["auto_close_hours"] and hours >= settings["auto_close_hours"]:
                # Always deliver a warning, including when the explicit reminder is disabled.
                if not ticket["reminded"]:
                    await (await self.get_channel(ticket)).send(
                        "This inactive ticket will close in at least one hour unless you reply."
                    )
                    await self.store.update(ticket, reminded=True, warning_at=time.time())
                elif not ticket.get("warning_at") or time.time() - ticket["warning_at"] >= 3600:
                    await self.close_ticket(ticket, 0, "Closed after member inactivity")
        elif (
            settings["escalation_hours"]
            and not ticket["escalated"]
            and (time.time() - ticket["waiting_since"]) / 3600 >= settings["escalation_hours"]
        ):
            await self.store.event(ticket, "TICKET STAFF RESPONSE OVERDUE", 0)
            await self.store.update(ticket, escalated=True)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
