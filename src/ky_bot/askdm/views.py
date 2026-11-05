"""Dark silver panels and participant-owned inline request controls."""

from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.menu_strips import group_sections, strip_files
from ky_bot.views.sections import section_dividers

from .store import KINDS


def _base_uploads(profile=None):
    if profile is not None:
        import io

        from ky_bot.design.menu_design import render_menu

        header, footer = render_menu(profile, "ASK TO DM/FR")
        return [
            discord.File(io.BytesIO(data), filename=name)
            for data, name in ((header, "ask_header.png"), (footer, "ask_footer.png"))
        ]
    return [
        discord.File(files("ky_bot.askdm").joinpath("assets", name).open("rb"), filename=name)
        for name in ("ask_header.png", "ask_footer.png")
    ]


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


class Panel(discord.ui.LayoutView):
    def __init__(self, *, timeout=600):
        super().__init__(timeout=timeout)
        self.panel = discord.ui.Container(accent_colour=0xBDC3CA)
        self.panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://ask_header.png"))
        )
        self.add_item(self.panel)

    def text(self, text):
        self.panel.add_item(discord.ui.TextDisplay(text))

    def row(self, text, button):
        self.panel.add_item(discord.ui.Section(text, accessory=button))

    def finish(self):
        if getattr(self, "category_menu", None):
            group_sections(self.panel, self.category_menu)
        section_dividers(self.panel)
        self.panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://ask_footer.png"))
        )

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)


def button(label, callback, *, custom_id=None):
    result = discord.ui.Button(
        label=label,
        style=discord.ButtonStyle.secondary,
        **({"custom_id": custom_id} if custom_id else {}),
    )
    result.callback = callback
    return result


class PublicPanel(Panel):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.text("Ask first. Wait for an explicit yes. Permission can be withdrawn at any time.")
        self.row(
            "**MAKE A REQUEST**\nChoose a member, request type and optional private reason.",
            button("Ask to DM/FR", cog.open_form, custom_id="ky:ask:entry"),
        )
        self.row(
            "**YOUR REQUESTS**\nRespond, cancel an unanswered request or withdraw permission.",
            button("My requests", cog.open_inbox, custom_id="ky:ask:inbox"),
        )
        self.text(
            "-# Unanswered requests expire after 48 hours. Reasons stay private. "
            "Requests and outcomes are recorded for staff. If DMs are blocked, "
            "KY BOT uses a private thread; staff with Manage Threads can access it."
        )
        self.finish()


class Action(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ky:ask:(?P<token>[0-9a-f]{32}):(?P<action>confirm|discard|accept|deny|cancel|revoke)",
):
    def __init__(self, token, action, *, disabled=False):
        self.token, self.action = token, action
        labels = dict(
            confirm="Confirm request",
            discard="Go back",
            accept="Accept",
            deny="Decline",
            cancel="Cancel request",
            revoke="Withdraw permission",
        )
        super().__init__(
            discord.ui.Button(
                label=labels[action],
                disabled=disabled,
                style=discord.ButtonStyle.secondary,
                custom_id=f"ky:ask:{token}:{action}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["token"], match["action"])

    async def callback(self, interaction):
        cog = interaction.client.get_cog("AskDM")
        if cog is None:
            return await send_private(interaction, "Requests are temporarily unavailable.")
        try:
            await cog.action(interaction, self.token, self.action)
        except ValueError as error:
            await send_private(interaction, str(error))
        except Exception as error:
            await report_error(interaction, error)


class Reason(discord.ui.Modal):
    def __init__(self, form):
        super().__init__(title="Private request reason")
        self.form = form
        self.reason = discord.ui.TextInput(
            label="Reason (recipient and staff only)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
            default=form.reason,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        if not await self.form.interaction_check(interaction):
            return
        self.form.reason = self.reason.value.strip()
        await interaction.response.defer()
        await send_private(interaction, "Your private reason has been saved for this request.")

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class Form(Panel):
    def __init__(self, cog, owner, guild):
        super().__init__()
        self.cog, self.owner, self.guild = cog, owner, guild
        self.target, self.kind, self.reason = None, "dm", ""
        self.text(
            "**NEW REQUEST**\nChoose a member and request type. "
            "Reasons are shared only with the recipient and staff."
        )
        people = discord.ui.UserSelect(placeholder="Who would you like to ask?")

        async def choose(i):
            self.target = people.values[0].id
            await i.response.defer()

        people.callback = choose
        self.panel.add_item(discord.ui.ActionRow(people))
        kinds = discord.ui.Select(
            placeholder="Choose request type",
            options=[
                discord.SelectOption(label=label, value=key, default=key == "dm")
                for key, label in KINDS.items()
            ],
        )

        async def kind(i):
            self.kind = kinds.values[0]
            await i.response.defer()

        kinds.callback = kind
        self.panel.add_item(discord.ui.ActionRow(kinds))

        async def reason(i):
            await i.response.send_modal(Reason(self))

        self.row(
            "**PRIVATE REASON**\nOptional context for the recipient and staff.",
            button("Add / edit", reason),
        )

        async def submit(i):
            await i.response.defer()
            try:
                await cog.prepare(i, self)
            except ValueError as error:
                await send_private(i, str(error))

        self.row(
            "**REVIEW**\nSending a request does not grant permission.", button("Continue", submit)
        )
        self.finish()

    async def interaction_check(self, i):
        if i.user.id == self.owner and i.guild_id == self.guild:
            return True
        await send_private(i, "Open your own private request form.")
        return False


class Confirmation(Panel):
    def __init__(self, token, target, kind):
        super().__init__(timeout=None)
        self.text(
            f"**REVIEW YOUR REQUEST**\n{KINDS[kind]} · <@{target}>\n"
            "Take a moment to get to know them in the server first. "
            "Do not send DMs or friend requests until they explicitly accept."
        )
        self.row(
            "**READY TO ASK?**\nThe recipient can decline without giving a reason.",
            Action(token, "confirm"),
        )
        self.row(
            "**CHANGE YOUR MIND?**\nReturn without sending a request.", Action(token, "discard")
        )
        self.finish()


class RequestPanel(Panel):
    def __init__(self, request, data, *, public=False, actor=None):
        super().__init__(timeout=None)
        self.guild = request["guild"]
        state, rid = request["state"], request["id"]
        sender, recipient = request["sender"], request["recipient"]
        self.text(
            f"**{KINDS[request['kind']].upper()} · {state.upper()}**\n"
            f"<@{sender}> → <@{recipient}>\n"
            f"Requested <t:{int(request['created'])}:R> · "
            "Permission applies only to this request and can be withdrawn."
        )
        if not public and data.get("reason"):
            self.text("**PRIVATE REASON**\n" + safe(data["reason"]))
        if not public and data.get("decline_reason"):
            self.text("**DECLINE REASON**\n" + safe(data["decline_reason"]))
        if not public:
            if state == "pending" and actor == recipient:
                self.row("**ACCEPT**\nGive permission for this request.", Action(rid, "accept"))
                self.row(
                    "**DECLINE**\nAn optional reason is shared privately.", Action(rid, "deny")
                )
            if state == "pending" and actor == sender:
                self.row("**CANCEL**\nCancel your unanswered request.", Action(rid, "cancel"))
            if state == "accepted" and actor == recipient:
                self.row(
                    "**WITHDRAW PERMISSION**\nYou can change your mind at any time.",
                    Action(rid, "revoke"),
                )
        self.finish()


class Decline(discord.ui.Modal):
    def __init__(self, cog, rid):
        super().__init__(title="Decline request")
        self.cog, self.rid = cog, rid
        self.reason = discord.ui.TextInput(
            label="Optional private reason",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.cog.decide(i, self.rid, "denied", self.reason.value.strip())
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class Inbox(Panel):
    def __init__(self, cog, guild, owner, rows):
        super().__init__()
        self.owner, self.guild, self.cog = owner, guild, cog
        self.text(
            "**YOUR REQUESTS**\nYour 25 most recent pending or accepted requests. "
            "Use the original notification for older accepted requests."
        )
        if rows:
            select = discord.ui.Select(
                placeholder="Choose a request",
                options=[
                    discord.SelectOption(
                        value=rid,
                        label=f"{KINDS[kind]} · {state}",
                        description=f"{'From' if recipient == owner else 'To'} member "
                        f"{sender if recipient == owner else recipient}",
                    )
                    for rid, kind, state, sender, recipient in rows
                ],
            )

            async def choose(i):
                await i.response.defer()
                request = await cog.store.get(select.values[0])
                await cog.show_request(i, request)

            select.callback = choose
            self.panel.add_item(discord.ui.ActionRow(select))
        else:
            self.text("You have no pending or accepted requests in this server.")
        self.finish()

    async def interaction_check(self, i):
        if i.user.id == self.owner and i.guild_id == self.guild:
            return True
        await send_private(i, "Open your own request inbox.")
        return False


class Admin(Panel):
    ROLE_FIELDS = {
        "sender_roles": ("WHO CAN SEND", "Any member when empty"),
        "recipient_roles": ("WHO CAN RECEIVE", "Any member when empty"),
        "blocked_sender_roles": ("BLOCKED FROM SENDING", "No blocked roles when empty"),
        "blocked_recipient_roles": ("BLOCKED FROM RECEIVING", "No blocked roles when empty"),
    }

    def __init__(self, cog, parent, revision, config, *, picker=None, notice=None):
        super().__init__(timeout=None if parent.panel_manager else 600)
        self.cog, self.parent, self.revision = cog, parent, revision
        self.category_menu = (
            "askdm:roles" if picker == "roles" or picker in self.ROLE_FIELDS else "askdm"
        )
        guild = parent.guild_id
        self.text("**COMMAND CENTER · ASK TO DM/FR**")
        if notice:
            self.text(notice)

        async def change(i, **values):
            await i.response.defer()
            try:
                async with cog.config_lock:
                    if values.get("enabled"):
                        await cog.validate_channels(i.guild, config)
                    if values.get("public"):
                        await cog.validate_channels(i.guild, config)
                    await cog.store.configure(guild, revision, **values)
                await cog.admin(i, parent)
            except (ValueError, discord.HTTPException) as error:
                await cog.admin(i, parent, notice=safe(str(error))[:1000])

        if picker == "roles" or picker in self.ROLE_FIELDS:
            for key, (label, empty) in self.ROLE_FIELDS.items():

                async def roles(i, key=key):
                    await i.response.defer()
                    await cog.admin(i, parent, picker=key)

                role_text = ", ".join(f"<@&{r}>" for r in config[key]) or empty
                self.row(f"**{label}**\n{role_text}", button("Choose roles", roles))
                if picker == key:
                    select = discord.ui.RoleSelect(
                        placeholder="Choose roles; clear to remove restriction",
                        min_values=0,
                        max_values=10,
                        default_values=[discord.Object(id=r) for r in config[key]],
                    )

                    async def choose_roles(i, key=key, select=select):
                        if any(role.guild.id != guild for role in select.values):
                            return await send_private(i, "Choose roles in this server.")
                        await change(i, **{key: [role.id for role in select.values]})

                    select.callback = choose_roles
                    self.panel.add_item(discord.ui.ActionRow(select))
                    self.panel.add_item(discord.ui.Separator())

            self.text(
                "-# Allowed roles use any match. Blocked roles take priority, including "
                "for administrators. Clear a selection to remove that restriction. "
                "Roles are checked again when a request is confirmed."
            )

            async def back(i):
                await i.response.defer()
                await cog.admin(i, parent)

            self.row(
                "**ASK TO DM/FR SETTINGS**\nReturn to channels and request visibility.",
                button("Back", back),
            )
            self.finish()
            return

        async def toggle(i):
            await change(i, enabled=not config["enabled"])

        self.row(
            "**REQUESTS**\n" + ("Enabled" if config["enabled"] else "Disabled"),
            button("Disable" if config["enabled"] else "Enable", toggle),
        )
        for key, label in (("channel", "REQUEST CHANNEL"), ("log_channel", "STAFF LOG CHANNEL")):

            async def open_picker(i, key=key):
                await i.response.defer()
                await cog.admin(i, parent, picker=key)

            destination = f"<#{config[key]}>" if config[key] else "Not configured"
            self.row(f"**{label}**\n{destination}", button("Choose channel", open_picker))
            if picker == key:
                select = discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text], placeholder=f"Choose {label.lower()}"
                )

                async def choose_channel(i, key=key, select=select):
                    await i.response.defer()
                    try:
                        channel = await cog.channel(select.values[0].id, guild)
                        await cog.check_channel(channel, logs=key == "log_channel")
                        async with cog.config_lock:
                            await cog.store.configure(guild, revision, **{key: channel.id})
                        await cog.admin(i, parent)
                    except (ValueError, discord.HTTPException) as error:
                        await cog.admin(i, parent, notice=safe(str(error))[:1000])

                select.callback = choose_channel
                self.panel.add_item(discord.ui.ActionRow(select))
                self.panel.add_item(discord.ui.Separator())

        async def visibility(i):
            await change(i, public=not config["public"])

        self.row(
            "**CHANNEL REQUEST VISIBILITY**\n"
            + ("Shown in the channel" if config["public"] else "Hidden from the channel")
            + " · Private reasons stay private. Staff logging stays on.",
            button("Hide requests" if config["public"] else "Show requests", visibility),
        )

        async def role_settings(i):
            await i.response.defer()
            await cog.admin(i, parent, picker="roles")

        self.row(
            "**ROLE PERMISSIONS & RESTRICTIONS**\nWho can send, receive or be blocked.",
            button("Manage roles", role_settings),
        )
        self.text(
            "-# Changing visibility also hides existing public request cards; it does not "
            "publish previously private requests. The entry panel remains visible. "
            "It stays at the bottom while public requests are enabled."
        )

        async def publish(i):
            await i.response.defer()
            try:
                await cog.publish(i.guild)
                await cog.admin(i, parent, notice="Member panel published / refreshed.")
            except (ValueError, discord.HTTPException) as error:
                await cog.admin(i, parent, notice=safe(str(error))[:1000])

        self.row(
            "**MEMBER PANEL**\nPublish or refresh the dark silver request menu.",
            button("Publish / refresh", publish),
        )

        async def home(i):
            await i.response.defer()
            if parent.panel_manager:
                await parent.panel_manager.reset(guild)
            else:
                parent.section = "overview"
                await parent.refresh(i)
            self.stop()

        self.row("**COMMAND CENTER**\nReturn to the main menu.", button("Home", home))
        self.finish()

    async def interaction_check(self, i):
        return await self.parent.interaction_check(i)


def uploads(profile=None, view=None):
    return _base_uploads(profile) + strip_files(
        getattr(view, "category_menu", "askdm"),
        (profile or {}).get("style", "dark_silver"),
        (profile or {}).get("text_color"),
    )
