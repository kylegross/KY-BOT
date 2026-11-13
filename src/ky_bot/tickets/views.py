"""Inline Components V2 menus shared by Command Center and /ticket setup."""

import asyncio
import copy
import uuid

import discord
from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_context import private_parent
from ky_bot.views.menu_strips import GROUPS, group_sections, heading
from ky_bot.views.sections import section_dividers

from .artwork import normalize
from .log_access import scope_destinations, viewer_roles
from .presentation import (
    BODY_STYLES,
    HEADING_STYLES,
    PANEL_TEXT_STYLES,
    button_label,
    format_text,
    heading_file,
    panel_text,
    visible_types,
)
from .presentation import heading as section_heading
from .store import new_panel, new_type

STARTER_TOPICS = {
    "Support": "Get help or ask a question.",
    "Member Reports": "Privately report a member or incident to staff.",
    "Appeals": "Ask staff to review a moderation decision.",
    "Applications": "Apply for a staff position or another server role.",
    "Other": "Contact staff about something else.",
}


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def button(label, callback, *, disabled=False):
    item = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=disabled)
    item.callback = callback
    return item


class Panel(discord.ui.LayoutView):
    def __init__(self, *, timeout=600, artwork=False):
        super().__init__(timeout=timeout)
        self.panel = discord.ui.Container(accent_colour=0xBDC3CA)
        self.add_item(self.panel)
        self.artwork = artwork
        if artwork:
            self.panel.add_item(
                discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://tickets_header.png"))
            )

    def text(self, content):
        self.panel.add_item(discord.ui.TextDisplay(content))

    def row(self, heading, description, label, callback, *, disabled=False):
        self.panel.add_item(
            discord.ui.Section(
                f"**{heading}**\n-# {description}",
                accessory=button(label, callback, disabled=disabled),
            )
        )

    def picker(self, control, callback):
        control.callback = callback
        self.panel.add_item(discord.ui.ActionRow(control))

    def finish(self):
        if getattr(self, "groups", None):
            group_sections(self.panel, self.groups)
        if self.artwork:
            self.panel.add_item(
                discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://tickets_footer.png"))
            )
        section_dividers(self.panel)

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)


class Edit(discord.ui.Modal):
    def __init__(self, menu, title, fields, commit):
        super().__init__(title=title[:45])
        self.menu, self.commit = menu, commit
        self.fields = {}
        for key, label, value, limit in fields:
            item = discord.ui.TextInput(
                label=label[:45],
                default=str(value),
                required=False,
                max_length=limit,
                style=discord.TextStyle.paragraph if limit > 150 else discord.TextStyle.short,
            )
            self.fields[key] = item
            self.add_item(item)

    async def on_submit(self, i):
        try:
            if not await self.menu.interaction_check(i):
                return
            await i.response.defer()
            await self.commit(i, {key: item.value.strip() for key, item in self.fields.items()})
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class ArtworkUpload(discord.ui.Modal):
    def __init__(self, menu, kind):
        super().__init__(
            title="Upload footer icon" if kind == "footer_icon" else f"Upload ticket panel {kind}"
        )
        self.menu, self.kind = menu, kind
        self.upload = discord.ui.FileUpload(max_values=1, required=True)
        self.add_item(
            discord.ui.Label(
                text="Transparent PNG/WebP · up to 8 MB"
                if kind == "footer_icon"
                else "PNG, JPEG or WebP · up to 8 MB",
                component=self.upload,
            )
        )

    async def on_submit(self, i):
        try:
            if not await self.menu.interaction_check(i):
                return
            await i.response.defer()
            file = self.upload.values[0]
            if file.size > 8 * 1024 * 1024:
                raise ValueError("Upload a still image up to 8 MB.")
            value = await asyncio.to_thread(normalize, await file.read(), self.kind)
            changes = (
                {"footer_icon": value, "footer_content": "icon"}
                if self.kind == "footer_icon"
                else {self.kind + "_custom": value, self.kind + "_mode": "custom"}
            )
            await self.menu.set_panel_art(i, changes)
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class Admin(Panel):
    def __init__(
        self,
        cog,
        parent,
        revision,
        config,
        *,
        page="home",
        selected=None,
        pick=None,
        offset=0,
        notice=None,
        tickets=(),
        opening=False,
    ):
        super().__init__(timeout=None if parent.panel_manager else 600, artwork=True)
        self.cog, self.parent, self.revision = cog, parent, revision
        self.opening = opening
        self.original_config = config
        if opening:
            from .opening import opening_design

            config = copy.deepcopy(config)
            config["panels"][selected] = opening_design(
                config["panels"][selected],
                welcome=(config["defaults"] | config["panels"][selected].get("overrides", {}))[
                    "welcome"
                ],
            )
        self.config, self.page, self.selected, self.pick = config, page, selected, pick
        self.offset = offset
        if notice:
            self.text(safe(notice)[:600])
        if page == "home":
            self.groups = "tickets"
            self.row(
                "TICKET PANELS",
                f"{len(config['panels'])} panels · public channels and appearance.",
                "Manage",
                self.go("panels"),
            )
            self.row(
                "TICKET TYPES",
                f"{len(config['types'])} topics · forms, teams and destinations.",
                "Manage",
                self.go("types"),
            )
            self.row(
                "SHARED DEFAULTS",
                "Staff, access, channels, logs and closing behavior.",
                "Configure",
                self.go("defaults"),
            )
            self.row(
                "AUTOMATION",
                "Support hours, reminders, inactivity and overdue alerts.",
                "Configure",
                self.go("automation"),
            )
            self.row(
                "ACTIVE TICKETS",
                "Unclaimed, waiting and assigned tickets.",
                "Open queue",
                self.go("queue"),
            )
            self.row(
                "TICKETING STATUS",
                "Enabled"
                if config["enabled"]
                else "Disabled · published panels cannot open tickets.",
                "Disable" if config["enabled"] else "Enable",
                self.toggle_system,
            )
        elif page in {"panels", "types"}:
            collection = config[page]
            self.row(
                "CREATE PANEL" if page == "panels" else "CREATE TICKET TYPE",
                "Configure another independently managed entry."
                if page == "panels"
                else "Create a topic that can be used on several panels.",
                "Create",
                self.create,
            )
            entries = list(collection.items())[offset : offset + 25]
            if entries:
                control = discord.ui.Select(
                    placeholder="Choose a panel" if page == "panels" else "Choose a ticket type",
                    options=[
                        discord.SelectOption(label=value["name"][:100], value=key)
                        for key, value in entries
                    ],
                )

                async def choose(i):
                    await i.response.defer()
                    await self.show(i, "panel" if page == "panels" else "type", control.values[0])

                self.picker(control, choose)
            else:
                self.text("No entries yet. Create one to get started.")
            if len(collection) > 25:
                self.panel.add_item(
                    discord.ui.ActionRow(
                        button("Previous", self.turn(-25), disabled=offset == 0),
                        button(
                            "Next",
                            self.turn(25),
                            disabled=offset + 25 >= len(collection),
                        ),
                    )
                )
        elif page == "panel" and opening:
            self.row(
                "OPENED TICKET PANEL",
                "Shown inside each new private ticket channel.",
                "Edit title",
                self.edit_panel,
            )
            self.row(
                "MESSAGE SECTIONS",
                "Headers and text above or below the standard controls.",
                "Configure",
                self.go("panel_text", selected),
            )
            self.row(
                "APPEARANCE",
                "KY BOT artwork, custom uploads, footer text and icons.",
                "Configure",
                self.go("appearance", selected),
            )
            self.row(
                "PRIVATE PREVIEW",
                "Claim, Close, Transcript and Manage stay standard.",
                "Preview",
                self.preview,
            )
            self.text(
                "-# Saved changes apply to newly opened tickets. Existing tickets retain "
                "their opening design. Intake answers are posted separately below it."
            )
        elif page == "panel":
            item = config["panels"][selected]
            self.row("SELECTED PANEL", safe(item["name"]), "Edit text", self.edit_panel)
            self.setting("PANEL CHANNEL", "channel", item, channel=True)
            self.row(
                "STAFF & APPROVAL ROLES",
                "Support staff, ticket approvers and ticket managers.",
                "Choose roles",
                self.go("access", selected),
            )
            self.row(
                "CHANNEL SETTINGS",
                "Ticket category, logs, workflow and automation for this panel.",
                "Configure",
                self.go("panel_settings", selected),
            )
            names = (
                ", ".join(safe(config["types"][key]["name"]) for key in item["types"])
                or "No ticket types selected"
            )
            self.row("TICKET TYPES", names, "Choose types", self.go("panel_types", selected))
            self.row(
                "PANEL TEXT",
                "Styled text before and after the ticket buttons.",
                "Edit text",
                self.go("panel_text", selected),
            )
            self.row(
                "APPEARANCE",
                "KY BOT designs or your own header and footer images.",
                "Manage",
                self.go("appearance", selected),
            )
            self.row(
                "OPENED TICKET PANEL",
                "Design the message and standard controls inside new private tickets.",
                "Configure",
                self.open_opening,
            )
            self.row(
                "PUBLISH PANEL",
                "Preview privately, then publish or update the existing message.",
                "Review",
                self.go("publish", selected),
            )
            self.row(
                "PANEL STATUS",
                "Enabled" if item["enabled"] else "Disabled",
                "Toggle",
                self.toggle_entry,
            )
        elif page == "panel_text":
            for position, title in (
                ("before", "BEFORE THE BUTTONS"),
                ("after", "AFTER THE BUTTONS"),
            ):
                self.row(
                    title,
                    "Optional header, main text and additional sections.",
                    "Configure",
                    self.go("text_" + position, selected),
                )
            self.row(
                "PRIVATE PREVIEW",
                "Review both areas around the ticket buttons.",
                "Preview",
                self.preview,
            )
        elif page in {
            "text_before",
            "text_after",
            "text_format_before",
            "text_format_after",
            "panel_text_before",
            "panel_text_after",
        }:
            self.build_text_sections(page)
        elif page == "appearance":
            item = config["panels"][selected]
            for kind in ("header", "footer"):
                mode = item.get(kind + "_mode", "ky")
                label = {
                    "ky": "KY BOT design",
                    "custom": "Your uploaded image",
                    "none": "Hidden",
                }[mode]
                self.row(
                    kind.upper() + " ARTWORK",
                    label,
                    "Configure",
                    self.go("art_" + kind, selected),
                )
            self.row(
                "COLOR & CONTENT",
                "Accent color, optional body image and footer caption.",
                "Edit",
                self.edit_appearance,
            )
            self.row(
                "PRIVATE PREVIEW",
                "Review the finished member-facing panel.",
                "Preview",
                self.preview,
            )
        elif page in {"art_header", "art_footer"}:
            self.artwork_controls(page.removeprefix("art_"))
        elif page == "panel_settings":
            self.row(
                "STAFF & ACCESS",
                "Choose this panel's team and opening restrictions.",
                "Configure",
                self.go("access", selected),
            )
            self.row(
                "CHANNELS & LOGS",
                "Ticket category and log / transcript destinations.",
                "Configure",
                self.go("destinations", selected),
            )
            self.row(
                "WORKFLOW & AUTOMATION",
                "Messages, limits, support hours and reminders.",
                "Configure",
                self.go("type_workflow", selected),
            )
            self.text(
                "-# These settings apply to tickets opened through this panel. "
                "Ticket-type overrides take precedence."
            )
        elif page == "panel_types":
            item = config["panels"][selected]
            self.text(
                "**WHAT CAN MEMBERS OPEN A TICKET FOR?**\n"
                "Check the topics you want on this panel. Members will see these choices "
                "when opening a ticket. Selections save automatically."
            )
            entries = [(key, value, False) for key, value in config["types"].items()]
            existing_names = {value["name"].casefold() for value in config["types"].values()}
            entries += [
                (name, new_type(name) | dict(description=description), True)
                for name, description in STARTER_TOPICS.items()
                if name.casefold() not in existing_names
            ]
            order = {name.casefold(): index for index, name in enumerate(STARTER_TOPICS)}
            entries.sort(
                key=lambda entry: (
                    order.get(entry[1]["name"].casefold(), len(order)),
                    entry[1]["name"].casefold(),
                )
            )
            for key, value, starter in entries[offset : offset + 5]:
                checked = not starter and key in item["types"]

                async def choose_type(i, key=key, value=value, starter=starter):
                    data = copy.deepcopy(config)
                    token = key
                    if starter:
                        token = uuid.uuid4().hex
                        data["types"][token] = copy.deepcopy(value)
                    topics = data["panels"][selected]["types"]
                    if token in topics:
                        topics.remove(token)
                    else:
                        topics.append(token)
                    await self.save(i, data)

                self.row(
                    safe(value["name"]),
                    safe(value["description"]),
                    "☑ Selected" if checked else "☐ Select",
                    choose_type,
                )
            if len(entries) > 5:
                self.panel.add_item(
                    discord.ui.ActionRow(
                        button("Previous", self.turn(-5), disabled=offset == 0),
                        button("Next", self.turn(5), disabled=offset + 5 >= len(entries)),
                    )
                )
            self.row(
                "CUSTOM TOPIC",
                "Add your own topic and include it on this panel.",
                "Add topic",
                self.create_panel_topic,
            )
            self.text(
                f"-# {len(item['types'])}/25 topics selected. "
                "New topics use this panel's settings. "
                "Next: go Back, then Review to preview and publish the panel."
            )
            if item["types"]:
                self.row(
                    "CUSTOMIZE SELECTED TOPICS",
                    "Edit section lettering, descriptions, button text and icons.",
                    "Customize",
                    self.go("panel_type_edit", selected),
                )
        elif page == "panel_type_edit":
            item = config["panels"][selected]
            self.text(
                "**EDIT A TOPIC SECTION**\nChoose a topic to customize. "
                "Edits appear wherever this type is used after each panel is published."
            )
            if item["types"]:
                control = discord.ui.Select(
                    placeholder="Choose a topic section",
                    options=[
                        discord.SelectOption(label=config["types"][key]["name"], value=key)
                        for key in item["types"]
                    ],
                )

                async def choose_section(i):
                    await i.response.defer()
                    await self.show(i, "type_appearance", control.values[0])

                self.picker(control, choose_section)
        elif page == "publish":
            self.row("PREVIEW", "Only you see this preview.", "Preview", self.preview)
            self.row(
                "PUBLISH / UPDATE",
                "Checks channels, staff access and log privacy before posting.",
                "Publish",
                self.publish,
            )
            item = config["panels"][selected]
            self.text(
                f"-# Saved message: {item['message'] or 'Not published'} · "
                "Moving a panel retires its old message."
            )
        elif page == "type":
            item = config["types"][selected]
            self.row("TICKET TYPE NAME", safe(item["name"]), "Edit", self.edit_type_name)
            self.row(
                "SECTION & BUTTON",
                "Edit the section heading, description, button label and icon.",
                "Customize",
                self.go("type_appearance", selected),
            )
            self.row(
                "INTAKE QUESTIONS",
                " · ".join(safe(q) for q in item["questions"]) or "No form",
                "Edit form",
                self.edit_questions,
            )
            self.row(
                "STAFF & ACCESS",
                "Use shared defaults or override this type.",
                "Configure",
                self.go("access", selected),
            )
            self.row(
                "CHANNELS & LOGS",
                "Ticket category and multiple log / transcript destinations.",
                "Configure",
                self.go("destinations", selected),
            )
            self.row(
                "WORKFLOW & AUTOMATION",
                "Messages, limits, closing and support hours.",
                "Configure",
                self.go("type_workflow", selected),
            )
            self.row(
                "TICKET TYPE STATUS",
                "Enabled" if item["enabled"] else "Disabled",
                "Toggle",
                self.toggle_entry,
            )
        elif page == "type_appearance":
            item = config["types"][selected]
            self.row(
                "SECTION & BUTTON TEXT",
                safe(section_heading(item)) + " · " + safe(button_label(item)),
                "Edit text",
                self.edit_type,
            )
            for key, title, choices, default in (
                ("heading_style", "HEADING FORMAT", HEADING_STYLES, "bold"),
                ("description_style", "DESCRIPTION FORMAT", BODY_STYLES, "subtext"),
            ):
                value = item.get(key, default)
                self.row(title, choices[value], "Choose", self.go(page, selected, pick=key))
                if pick == key:
                    control = discord.ui.Select(
                        placeholder="Choose formatting",
                        options=[
                            discord.SelectOption(label=label, value=option, default=option == value)
                            for option, label in choices.items()
                        ],
                    )

                    async def choose(i, key=key, control=control):
                        await self.set_type_art(i, {key: control.values[0]})

                    self.picker(control, choose)
            if item.get("heading_style", "bold") == "decorative":
                self.row(
                    "KY BOT LETTERING",
                    "Finish and optional custom text color.",
                    "Configure",
                    self.go("type_lettering", selected),
                )
            self.text(
                "-# These controls change the member-facing section, not the ticket type's "
                "internal name. Custom formatting accepts Discord Markdown."
            )
        elif page == "type_lettering":
            from ky_bot.design.menu_design import FINISHES

            self.text("**KY BOT SECTION LETTERING**\nChoose a finish and optional text color.")

            item = config["types"][selected]
            control = discord.ui.Select(
                placeholder="Choose the KY BOT lettering finish",
                options=[
                    discord.SelectOption(
                        label="Follow panel header",
                        value="follow",
                        default=not item.get("heading_finish"),
                    )
                ]
                + [
                    discord.SelectOption(
                        label=label,
                        value=key,
                        default=key == item.get("heading_finish"),
                    )
                    for key, label in FINISHES.items()
                ],
            )

            async def finish(i):
                await self.set_type_art(
                    i,
                    {
                        "heading_finish": None
                        if control.values[0] == "follow"
                        else control.values[0]
                    },
                )

            self.picker(control, finish)
            self.row(
                "TEXT COLOR",
                item.get("heading_color") or "Follow selected finish",
                "Edit color",
                self.edit_type_color,
            )
        elif page in {"defaults", "type_workflow"}:
            if page == "defaults":
                self.row(
                    "STAFF & ACCESS",
                    "Support roles, manager roles and opening restrictions.",
                    "Configure",
                    self.go("access"),
                )
                self.row(
                    "CHANNELS & LOGS",
                    "Default category and multiple logging destinations.",
                    "Configure",
                    self.go("destinations"),
                )
            else:
                self.row(
                    "AUTOMATION",
                    "Override support hours, reminders and auto-close.",
                    "Configure",
                    self.go("automation", selected),
                )
            self.row(
                "LIMITS & RETENTION",
                "Cooldown, active ticket limit and archive retention.",
                "Edit",
                self.edit_limits,
            )
            self.row(
                "TICKET MESSAGES",
                "Welcome and closing messages.",
                "Edit",
                self.edit_messages,
            )
            self.row(
                "SAVED REPLIES",
                "Reusable answers for staff.",
                "Manage",
                self.go("replies", selected),
            )
            for key, title in (
                ("user_close", "MEMBER CLOSURE"),
                ("dm_transcript", "DM TRANSCRIPT COPY"),
                ("feedback", "FEEDBACK"),
                ("ping_staff", "NOTIFY SUPPORT ROLES"),
            ):
                self.boolean(title, key)
        elif page == "replies":
            self.row(
                "SAVED REPLIES",
                "Add, replace or remove a reusable answer.",
                "Edit reply",
                self.edit_reply,
            )
            names = (
                ", ".join(safe(key) for key in self.settings["saved_replies"])
                or "No saved replies yet."
            )
            self.text(names[:2000])
        elif page == "access":
            self.boolean("REQUIRE TICKET APPROVAL", "require_approval")
            for title, key in (
                ("SUPPORT ROLES", "support_roles"),
                ("TICKET APPROVERS", "approver_roles"),
                ("TICKET MANAGERS", "manager_roles"),
                ("ALLOWED TO OPEN", "allowed_roles"),
                ("BLOCKED FROM OPENING", "blocked_roles"),
            ):
                self.setting(title, key, self.settings, roles=True)
            self.row(
                "LOG ACCESS",
                "Choose who can view activity logs and saved transcripts.",
                "Configure",
                self.go("log_access", selected),
            )
            self.text(
                "-# Empty allowed roles permits everyone. Blocked roles take "
                "priority. Administrators retain access to private channels."
            )
            self.text(
                "-# Support staff handle tickets. Approvers review waiting requests. "
                "Managers can also approve, reassign and transfer tickets. "
                "Require approval keeps the member's ticket read-only until approved."
            )
        elif page == "log_access":
            self.setting("LOG VIEWER ROLES", "log_viewer_roles", self.settings, roles=True)

            async def follow_managers(i):
                await self.set_value(i, "log_viewer_roles", None)

            self.row(
                "FOLLOW TICKET MANAGERS",
                "Keep log viewers matched to manager roles automatically.",
                "Use managers",
                follow_managers,
            )
            targets = sorted(scope_destinations(config, selected, self.settings))
            labels = (
                ", ".join(f"<#{cid}>" for cid in targets[:20])
                or "Choose log channels under Channels & Logs first."
            )
            self.text(
                "**CHANNELS TO UPDATE**\n"
                + labels
                + (f" · {len(targets)} channels total" if len(targets) > 20 else "")
            )
            self.row(
                "CHANNEL PERMISSIONS",
                "Hide these channels from other roles and grant the chosen viewers access.",
                "Apply Channel Access",
                self.apply_log_access,
            )
            self.text(
                "-# Log access is separate from ticket access. "
                "Support roles are not automatically included. Clear the role selection "
                "for administrators only. Discord administrators retain access. "
                "Channels shared by panels use their combined log viewer roles."
            )
        elif page == "panel_logs":
            item = config["defaults"] | config["panels"][selected].get("overrides", {})
            self.setting("ACTIVITY LOG CHANNELS", "log_channels", item, channels=True)
            self.setting("TRANSCRIPT CHANNELS", "transcript_channels", item, channels=True)
            self.text(
                "-# Type overrides take precedence over panel overrides; otherwise "
                "shared defaults apply."
            )
            self.row(
                "SHARED LOG DEFAULTS",
                "Remove this panel's destination overrides.",
                "Reset defaults",
                self.reset_panel_logs,
            )
        elif page == "destinations":
            self.setting("TICKET CATEGORY", "category", self.settings, category=True)
            self.setting("ACTIVITY LOG CHANNELS", "log_channels", self.settings, channels=True)
            self.setting(
                "TRANSCRIPT CHANNELS",
                "transcript_channels",
                self.settings,
                channels=True,
            )
            self.text(
                "-# Select several staff-only destinations. Activity logs contain "
                "metadata; transcripts contain the conversation."
            )
        elif page == "automation":
            self.row(
                "INACTIVITY",
                "Reminder and auto-close hours; zero disables each timer.",
                "Edit",
                self.edit_automation,
            )
            self.row(
                "SUPPORT HOURS",
                "Daily opening window and timezone. 0–24 stays open all day.",
                "Edit",
                self.edit_hours,
            )
            self.text(
                "-# Auto-close runs only while waiting for the member. Overdue "
                "staff replies generate an activity-log alert. Staff can pause "
                "automation per ticket."
            )
        elif page == "queue":
            active = [
                t
                for t in tickets
                if t["state"]
                in {
                    "open",
                    "waiting_staff",
                    "waiting_user",
                    "creating",
                    "pending_approval",
                }
            ]
            self.text(
                f"**ACTIVE TICKETS**\n{len(active)} active · "
                f"{sum(not t['claimed'] for t in active)} unclaimed"
            )
            for ticket in active[offset : offset + 5]:
                location = f"<#{ticket['channel']}>" if ticket["channel"] else "Creating…"
                self.text(
                    f"**{safe(ticket['type_name'])} · {ticket['id'][:8]}**\n"
                    f"{location} · <@{ticket['owner']}> · "
                    f"{ticket['state'].replace('_', ' ')} · {ticket['priority']}\n-# Assigned: "
                    + (f"<@{ticket['claimed']}>" if ticket["claimed"] else "Unclaimed")
                )
            if len(active) > 5:
                self.panel.add_item(
                    discord.ui.ActionRow(
                        button("Previous", self.turn(-5), disabled=offset == 0),
                        button("Next", self.turn(5), disabled=offset + 5 >= len(active)),
                    )
                )
            closed = [t for t in tickets if t["closed_at"]]
            ratings = [t["rating"] for t in closed if t["rating"]]
            response_times = [
                (t["first_response"] - t["created"]) / 60
                for t in tickets
                if t.get("first_response")
            ]
            resolution_times = [(t["closed_at"] - t["created"]) / 60 for t in closed]
            if response_times or resolution_times:
                self.text(
                    "-# Average first reply: "
                    + (
                        f"{sum(response_times) / len(response_times):.0f} min"
                        if response_times
                        else "—"
                    )
                    + " · Average resolution: "
                    + (
                        f"{sum(resolution_times) / len(resolution_times):.0f} min"
                        if resolution_times
                        else "—"
                    )
                )
            self.text(
                f"-# {len(closed)} closed · Average rating: "
                + (
                    f"{sum(ratings) / len(ratings):.1f}/5 ({len(ratings)} ratings)"
                    if ratings
                    else "No ratings yet"
                )
            )

        if selected and page in {
            "access",
            "destinations",
            "type_workflow",
            "automation",
        }:
            self.row(
                "CHANNEL OVERRIDES" if selected in config["panels"] else "TYPE OVERRIDES",
                "Reset this panel to shared defaults."
                if selected in config["panels"]
                else "Reset this type to all current shared defaults.",
                "Reset defaults",
                self.reset_defaults,
            )
        if page != "home":
            category = {
                "access": "ACCESS & PRIVACY",
                "log_access": "ACCESS & PRIVACY",
                "destinations": "CHANNELS & LOGGING",
                "panel_logs": "CHANNELS & LOGGING",
                "automation": "AUTOMATION",
                "publish": "REVIEW & PUBLISH",
                "queue": "ADMINISTRATION",
            }.get(page, "SETUP")
            self.groups = "tickets:" + page
            labels = tuple(
                label
                for item in self.panel.children
                if (label := heading(item)) and not label.startswith("COMMAND CENTER")
            )
            GROUPS[self.groups] = ((category, labels),)
        destination = (
            "panel_text"
            if page in {"panel_text_before", "panel_text_after"}
            else "panel"
            if page == "panel_text"
            else "panel_types"
            if page == "panel_type_edit"
            else "type_appearance"
            if page == "type_lettering"
            else "type"
            if page == "type_appearance"
            else "access"
            if page == "log_access"
            else "appearance"
            if page in {"art_header", "art_footer"}
            else "panel"
            if page == "appearance"
            else "panels"
            if page == "panel"
            else "types"
            if page == "type"
            else "panel"
            if page in {"publish", "panel_types", "panel_logs", "panel_settings"}
            else "panel_settings"
            if selected in config["panels"]
            else "type"
            if selected
            else "home"
        )
        if page in {"text_before", "text_after"}:
            destination = "panel_text"
        elif page.startswith("text_format_"):
            destination = "text_" + page.removeprefix("text_format_")
        elif page in {"panel_text_before", "panel_text_after"}:
            destination = "text_format_" + page.removeprefix("panel_text_")
        if page == "home":
            if getattr(parent, "back_menu", None):
                self.row("LOGS", "Return to the log categories.", "Back", self.back_logs)
            else:
                self.row("COMMAND CENTER", "Return to the main menu.", "Home", self.home)
        else:
            self.row(
                "PREVIOUS MENU",
                "Return to the previous menu.",
                "Back",
                self.go(
                    destination,
                    selected
                    if destination
                    in {
                        "panel",
                        "type",
                        "panel_settings",
                        "appearance",
                        "access",
                        "type_appearance",
                        "panel_types",
                        "panel_text",
                        "text_before",
                        "text_after",
                        "text_format_before",
                        "text_format_after",
                    }
                    else None,
                    offset=self.offset if destination.startswith(("text_", "panel_text_")) else 0,
                ),
            )
        self.finish()

    @property
    def settings(self):
        if self.selected in self.config["panels"]:
            return self.config["defaults"] | self.config["panels"][self.selected].get(
                "overrides", {}
            )
        if self.selected:
            kind = self.config["types"][self.selected]
            return self.config["defaults"] | kind["overrides"]
        return self.config["defaults"]

    async def interaction_check(self, i):
        return await self.parent.interaction_check(i)

    async def show(self, i, page=None, selected=None, *, notice=None, pick=None, offset=0):
        await self.cog.admin(
            i,
            self.parent,
            page=page or self.page,
            selected=selected,
            notice=notice,
            pick=pick,
            offset=offset,
            opening=self.opening,
        )

    async def open_opening(self, i):
        await i.response.defer()
        await self.cog.admin(i, self.parent, page="panel", selected=self.selected, opening=True)

    def go(self, page, selected=None, *, pick=None, offset=0):
        async def callback(i):
            await i.response.defer()
            if self.opening and page == "panels":
                await self.cog.admin(i, self.parent, page="panel", selected=self.selected)
            else:
                await self.show(i, page, selected, pick=pick, offset=offset)

        return callback

    def turn(self, delta):
        async def callback(i):
            await i.response.defer()
            await self.show(i, self.page, self.selected, offset=max(0, self.offset + delta))

        return callback

    async def save(self, i, data, *, page=None, selected=None, offset=None):
        if not i.response.is_done():
            await i.response.defer()
        if self.opening:
            original = copy.deepcopy(self.original_config)
            original["panels"][self.selected]["opening_panel"] = data["panels"][self.selected]
            data = original
        await self.cog.store.configure(self.parent.guild_id, self.revision, data)
        await self.show(
            i,
            page or self.page,
            self.selected if selected is None else selected,
            notice="Saved for newly opened tickets."
            if self.opening
            else "Saved. Publish a panel to update its public appearance.",
            offset=(self.offset if (page or self.page) == self.page else 0)
            if offset is None
            else offset,
        )

    async def set_value(self, i, key, value):
        data = copy.deepcopy(self.config)
        if self.page == "panel":
            data["panels"][self.selected][key] = value
        elif self.selected in self.config["panels"]:
            data["panels"][self.selected].setdefault("overrides", {})[key] = value
        elif self.selected:
            data["types"][self.selected]["overrides"][key] = value
        else:
            data["defaults"][key] = value
        await self.save(i, data)

    async def set_panel_art(self, i, changes):
        data = copy.deepcopy(self.config)
        data["panels"][self.selected].update(changes)
        await self.save(i, data)

    def text_section(self, position):
        from .sections import blocks

        values = blocks(self.config["panels"][self.selected], position)
        self.offset = min(self.offset, len(values) - 1)
        return values[self.offset]

    async def set_text_section(self, i, position, changes):
        data = copy.deepcopy(self.config)
        item = data["panels"][self.selected]
        if self.offset:
            item[position + "_sections"][self.offset - 1].update(changes)
        else:
            keys = {
                "heading": "_heading",
                "heading_style": "_heading_style",
                "body": "_text",
                "body_style": "_style",
                "finish": "_finish",
                "color": "_color",
            }
            item.update({position + keys[key]: value for key, value in changes.items()})
        await self.save(i, data)

    async def edit_section_part(self, i, position, part):
        block = self.text_section(position)
        limit = (
            80
            if part == "heading" or block.get(part + "_style") == "decorative"
            else (500 if self.offset else 1500 if position == "before" else 1000)
        )

        async def commit(i, values):
            await self.set_text_section(i, position, {part: values["text"]})

        await i.response.send_modal(
            Edit(
                self,
                "Section " + ("header" if part == "heading" else "text"),
                [
                    (
                        "text",
                        "Optional header (blank hides)"
                        if part == "heading"
                        else "Section text (blank removes)",
                        block[part],
                        limit,
                    )
                ],
                commit,
            )
        )

    def build_text_sections(self, page):
        from ky_bot.design.menu_design import FINISHES

        from .sections import MAX_SECTIONS, blocks, new_section

        position = "before" if page.endswith("before") else "after"
        item = self.config["panels"][self.selected]
        block = self.text_section(position)
        if page.startswith("text_format_"):
            for key, title, choices, default in (
                ("heading_style", "HEADER FORMAT", HEADING_STYLES, "bold"),
                ("body_style", "TEXT FORMAT", PANEL_TEXT_STYLES, "plain"),
            ):
                value = block.get(key, default)
                self.row(
                    title,
                    choices[value],
                    "Choose",
                    self.go(page, self.selected, pick=key, offset=self.offset),
                )
                if self.pick == key:
                    control = discord.ui.Select(
                        placeholder="Choose formatting",
                        options=[
                            discord.SelectOption(label=label, value=style, default=style == value)
                            for style, label in choices.items()
                        ],
                    )

                    async def choose(i, key=key, control=control):
                        await self.set_text_section(i, position, {key: control.values[0]})

                    self.picker(control, choose)
            if "decorative" in {block.get("heading_style"), block.get("body_style")}:
                self.row(
                    "KY BOT LETTERING",
                    "Finish and optional custom color.",
                    "Configure",
                    self.go("panel_text_" + position, self.selected, offset=self.offset),
                )
            return
        if page.startswith("panel_text_"):
            self.text("**SECTION LETTERING**\nChoose a finish and optional custom color.")
            finish = discord.ui.Select(
                placeholder="Choose the section finish",
                options=[
                    discord.SelectOption(
                        label="Follow header finish",
                        value="follow",
                        default=not block.get("finish"),
                    )
                ]
                + [
                    discord.SelectOption(label=label, value=key, default=key == block.get("finish"))
                    for key, label in FINISHES.items()
                ],
            )

            async def choose_finish(i):
                await self.set_text_section(
                    i,
                    position,
                    {"finish": None if finish.values[0] == "follow" else finish.values[0]},
                )

            self.picker(finish, choose_finish)

            async def color(i):
                async def commit(i, values):
                    await self.set_text_section(
                        i, position, {"color": values["color"].lstrip("#") or None}
                    )

                await i.response.send_modal(
                    Edit(
                        self,
                        "Section text color",
                        [
                            (
                                "color",
                                "Hex color (blank follows finish)",
                                block.get("color") or "",
                                7,
                            )
                        ],
                        commit,
                    )
                )

            self.row("TEXT COLOR", block.get("color") or "Follow finish", "Edit color", color)
            return
        self.text(
            "**"
            + position.upper()
            + " THE BUTTONS**\nThe main header is optional and has no divider. "
            "Add extra sections when needed."
        )
        options = blocks(item, position)
        picker = discord.ui.Select(
            placeholder="Choose the main text or an extra section",
            options=[
                discord.SelectOption(
                    label="Main header & text"
                    if index == 0
                    else f"{index}. {section['heading'] or 'Additional section'}"[:100],
                    value=str(index),
                    default=index == self.offset,
                )
                for index, section in enumerate(options)
            ],
        )

        async def choose_section(i):
            await i.response.defer()
            await self.show(i, page, self.selected, offset=int(picker.values[0]))

        self.picker(picker, choose_section)

        async def add(i):
            data = copy.deepcopy(self.config)
            extra = data["panels"][self.selected].setdefault(position + "_sections", [])
            if len(extra) >= MAX_SECTIONS:
                raise ValueError("Use up to three additional sections on each side.")
            extra.append(new_section())
            await self.save(i, data, offset=len(extra))

        self.row(
            "ADDITIONAL SECTION",
            "Add another header and text block. Its divider can be switched off.",
            "Add section",
            add,
            disabled=len(options) > MAX_SECTIONS,
        )

        async def edit_header(i):
            await self.edit_section_part(i, position, "heading")

        async def edit_body(i):
            await self.edit_section_part(i, position, "body")

        self.row(
            "HEADER · OPTIONAL",
            safe(block["heading"]) or "No header",
            "Edit header",
            edit_header,
        )
        self.row("TEXT", safe(block["body"])[:180] or "No text", "Edit text", edit_body)
        self.row(
            "FORMATTING",
            "Style the header and text independently.",
            "Configure",
            self.go("text_format_" + position, self.selected, offset=self.offset),
        )
        if self.offset:

            async def divider(i):
                await self.set_text_section(
                    i, position, {"divider": not block.get("divider", True)}
                )

            async def remove(i):
                data = copy.deepcopy(self.config)
                data["panels"][self.selected][position + "_sections"].pop(self.offset - 1)
                await self.save(i, data, offset=max(0, self.offset - 1))

            self.row(
                "SECTION DIVIDER",
                "Shown" if block.get("divider") else "Hidden",
                "Toggle",
                divider,
            )
            self.row(
                "REMOVE SECTION",
                "Remove this additional draft section.",
                "Remove",
                remove,
            )
        self.row(
            "PRIVATE PREVIEW",
            "Review both areas around the ticket buttons.",
            "Preview",
            self.preview,
        )

    async def set_type_art(self, i, changes):
        data = copy.deepcopy(self.config)
        data["types"][self.selected].update(changes)
        await self.save(i, data)

    async def apply_log_access(self, i):
        await i.response.defer()
        count = await self.cog.apply_log_access(
            i.guild, self.selected, self.settings, self.revision
        )
        await self.show(
            i,
            "log_access",
            self.selected,
            notice=f"Updated {count} log channel(s). Ticket access is unchanged.",
        )

    def artwork_controls(self, kind):
        from ky_bot.design.menu_design import FINISHES

        item = self.config["panels"][self.selected]
        if kind == "header":
            self.row(
                "HEADER IMAGE TEXT",
                safe(
                    item.get("header_title")
                    if item.get("header_title") is not None
                    else item["title"]
                )
                or "No header lettering",
                "Edit header text",
                self.edit_header_text,
            )
        else:
            content = item.get("footer_content", "emblem")
            labels = {
                "emblem": "Default design",
                "text": "Text inside the footer",
                "icon": "Custom footer icon",
            }
            self.row(
                "FOOTER CONTENT",
                labels[content],
                "Choose",
                self.go(self.page, self.selected, pick="content"),
            )
            if self.pick == "content":
                control = discord.ui.Select(
                    placeholder="Choose footer content",
                    options=[
                        discord.SelectOption(label=label, value=key, default=key == content)
                        for key, label in labels.items()
                    ],
                )

                async def choose_content(i):
                    value = control.values[0]
                    if value == "icon" and not item.get("footer_icon"):
                        return await i.response.send_modal(ArtworkUpload(self, "footer_icon"))
                    if value == "text" and not item.get("footer_banner_text"):
                        return await self.edit_footer_text(i)
                    await self.set_panel_art(i, {"footer_content": value})

                self.picker(control, choose_content)
            if content == "text":
                self.row(
                    "FOOTER IMAGE TEXT",
                    safe(item.get("footer_banner_text", "")) or "No text yet",
                    "Edit footer text",
                    self.edit_footer_text,
                )
            elif content == "icon":

                async def icon_upload(i):
                    await i.response.send_modal(ArtworkUpload(self, "footer_icon"))

                self.row(
                    "FOOTER ICON",
                    "Use your own transparent image.",
                    "Upload / replace",
                    icon_upload,
                )
        mode = item.get(kind + "_mode", "ky")
        labels = {
            "ky": "KY BOT design",
            "custom": "Your uploaded image",
            "none": "Hidden",
        }
        self.row(
            kind.upper() + " SOURCE",
            labels[mode],
            "Choose",
            self.go(self.page, self.selected, pick="source"),
        )
        if self.pick == "source":
            select = discord.ui.Select(
                placeholder="Choose artwork source",
                options=[
                    discord.SelectOption(label=label, value=key, default=key == mode)
                    for key, label in labels.items()
                ],
            )

            async def source(i):
                value = select.values[0]
                if value == "custom" and not item.get(kind + "_custom"):
                    return await i.response.send_modal(ArtworkUpload(self, kind))
                await self.set_panel_art(i, {kind + "_mode": value})

            self.picker(select, source)
        finish = item.get(kind + "_finish", "dark_silver")
        self.row(
            "KY BOT FINISH",
            FINISHES[finish],
            "Choose",
            self.go(self.page, self.selected, pick="finish"),
        )
        if self.pick == "finish":
            select_finish = discord.ui.Select(
                placeholder="Choose a KY BOT finish",
                options=[
                    discord.SelectOption(label=label, value=key, default=key == finish)
                    for key, label in FINISHES.items()
                ],
            )

            async def choose_finish(i):
                await self.set_panel_art(
                    i, {kind + "_finish": select_finish.values[0], kind + "_mode": "ky"}
                )

            self.picker(select_finish, choose_finish)

        async def upload(i):
            await i.response.send_modal(ArtworkUpload(self, kind))

        self.row(
            "YOUR OWN IMAGE",
            "Upload a still PNG, JPEG or WebP image. Your image keeps its own design.",
            "Upload",
            upload,
        )
        self.row(
            "PRIVATE PREVIEW",
            "See the header and footer together before publishing.",
            "Preview",
            self.preview,
        )

    def setting(
        self,
        title,
        key,
        values,
        *,
        roles=False,
        channels=False,
        channel=False,
        category=False,
    ):
        current = values[key]
        entries = current if isinstance(current, list) else ([current] if current else [])
        if key == "log_viewer_roles" and current is None:
            entries = sorted(viewer_roles(values))
        label = (
            ", ".join(f"<@&{value}>" if roles else f"<#{value}>" for value in entries)
            or "Not configured"
        )
        if key == "log_viewer_roles":
            label = (
                ("Follow ticket managers · " + (label if entries else "Administrators only"))
                if current is None
                else (label if entries else "Administrators only")
            )
        if self.selected and self.page != "panel":
            collection = (
                self.config["panels"]
                if self.selected in self.config["panels"]
                else self.config["types"]
            )
            label += (
                " · Override"
                if key in collection[self.selected]["overrides"]
                else " · Shared default"
            )
        self.row(
            title,
            label,
            "Choose roles" if roles else "Choose channel(s)",
            self.go(self.page, self.selected, pick=key),
        )
        if self.pick != key:
            return
        control = (
            discord.ui.RoleSelect(
                min_values=0,
                max_values=10,
                default_values=[discord.Object(id=value) for value in entries],
            )
            if roles
            else discord.ui.ChannelSelect(
                channel_types=[
                    discord.ChannelType.category if category else discord.ChannelType.text
                ],
                min_values=0,
                max_values=10 if channels else 1,
                default_values=[discord.Object(id=value) for value in entries],
            )
        )

        async def choose(i):
            await i.response.defer()
            ids = [value.id for value in control.values]
            if roles and any(
                i.guild.get_role(value) is None or i.guild.get_role(value).is_default()
                for value in ids
            ):
                raise ValueError("Choose roles in this server other than @everyone.")
            if not roles:
                for value in ids:
                    target = i.guild.get_channel(value)
                    if channels:
                        self.cog.validate_log_destination(target)
                    else:
                        self.cog.validate_destination(target, category=category)
            await self.set_value(i, key, ids if roles or channels else (ids[0] if ids else 0))

        self.picker(control, choose)

    def boolean(self, title, key):
        async def toggle(i):
            await self.set_value(i, key, not self.settings[key])

        self.row(title, "Enabled" if self.settings[key] else "Disabled", "Toggle", toggle)

    async def home(self, i):
        await i.response.defer()
        if self.parent.panel_manager:
            await self.parent.panel_manager.reset(self.parent.guild_id)
        else:
            self.parent.section = "overview"
            await self.parent.refresh(i)

    async def back_logs(self, i):
        await i.response.defer()
        await self.parent.back_menu.return_to(i)

    async def create(self, i):
        async def commit(i, values):
            name = values["name"]
            if not name:
                raise ValueError("Enter a name.")
            key = uuid.uuid4().hex
            data = copy.deepcopy(self.config)
            default_channel = getattr(self.parent, "ticket_default_channel", 0)
            data[self.page][key] = (
                new_panel(name, default_channel) if self.page == "panels" else new_type(name)
            )
            await self.save(
                i, data, page="panel" if self.page == "panels" else "type", selected=key
            )

        await i.response.send_modal(
            Edit(
                self,
                "Create ticket panel" if self.page == "panels" else "Create ticket type",
                [("name", "Name", "", 80)],
                commit,
            )
        )

    async def create_panel_topic(self, i):
        async def commit(i, values):
            name = values["name"].strip()
            if not name:
                raise ValueError("Enter a topic name.")
            if any(
                kind["name"].casefold() == name.casefold() for kind in self.config["types"].values()
            ):
                raise ValueError("That topic already exists. Select it from the checklist.")
            data = copy.deepcopy(self.config)
            token = uuid.uuid4().hex
            data["types"][token] = new_type(name)
            data["panels"][self.selected]["types"].append(token)
            await self.save(i, data)

        await i.response.send_modal(
            Edit(self, "Add a ticket topic", [("name", "Topic name", "", 80)], commit)
        )

    async def edit_panel(self, i):
        item = self.config["panels"][self.selected]

        async def commit(i, values):
            data = copy.deepcopy(self.config)
            data["panels"][self.selected].update(values)
            if not values["name"] or not values["title"]:
                raise ValueError("Panel name and title are required.")
            await self.save(i, data)

        await i.response.send_modal(
            Edit(
                self,
                "Panel text",
                [
                    ("name", "Internal name", item["name"], 80),
                    ("title", "Public title", item["title"], 120),
                    (
                        "description",
                        "Default text before buttons",
                        item["description"],
                        1500,
                    ),
                ],
                commit,
            )
        )

    async def edit_appearance(self, i):
        item = self.config["panels"][self.selected]

        async def commit(i, values):
            data = copy.deepcopy(self.config)
            values["color"] = values["color"].lstrip("#")
            data["panels"][self.selected].update(values)
            await self.save(i, data)

        await i.response.send_modal(
            Edit(
                self,
                "Panel appearance",
                [
                    ("color", "Accent hex color", item["color"], 7),
                    ("image", "Image URL (HTTPS; blank removes)", item["image"], 500),
                    ("footer", "Footer", item["footer"], 200),
                ],
                commit,
            )
        )

    async def edit_header_text(self, i):
        item = self.config["panels"][self.selected]
        value = item.get("header_title")

        async def commit(i, values):
            await self.set_panel_art(i, {"header_title": values["text"]})

        await i.response.send_modal(
            Edit(
                self,
                "Header image text",
                [
                    (
                        "text",
                        "Text inside the header (blank removes)",
                        item["title"] if value is None else value,
                        80,
                    )
                ],
                commit,
            )
        )

    async def edit_panel_text(self, i, position):
        item = self.config["panels"][self.selected]

        async def commit(i, values):
            await self.set_panel_art(i, {position + "_text": values["text"]})

        limit = (
            80
            if item.get(position + "_style", "plain") == "decorative"
            else (1500 if position == "before" else 1000)
        )
        await i.response.send_modal(
            Edit(
                self,
                "Text " + position + " the buttons",
                [
                    (
                        "text",
                        "Panel text (blank removes)",
                        panel_text(item, position),
                        limit,
                    )
                ],
                commit,
            )
        )

    async def edit_footer_text(self, i):
        item = self.config["panels"][self.selected]

        async def commit(i, values):
            await self.set_panel_art(
                i,
                {
                    "footer_banner_text": values["text"],
                    "footer_content": "text" if values["text"] else "emblem",
                },
            )

        await i.response.send_modal(
            Edit(
                self,
                "Footer image text",
                [
                    (
                        "text",
                        "Text inside the footer (blank resets)",
                        item.get("footer_banner_text", ""),
                        80,
                    )
                ],
                commit,
            )
        )

    async def edit_type_name(self, i):
        async def commit(i, values):
            await self.set_type_art(i, {"name": values["name"]})

        await i.response.send_modal(
            Edit(
                self,
                "Ticket type name",
                [
                    (
                        "name",
                        "Internal topic name",
                        self.config["types"][self.selected]["name"],
                        80,
                    )
                ],
                commit,
            )
        )

    async def edit_type_color(self, i):
        async def commit(i, values):
            await self.set_type_art(i, {"heading_color": values["color"].lstrip("#") or None})

        await i.response.send_modal(
            Edit(
                self,
                "Decorative heading color",
                [
                    (
                        "color",
                        "Hex color (blank follows finish)",
                        self.config["types"][self.selected].get("heading_color") or "",
                        7,
                    )
                ],
                commit,
            )
        )

    async def edit_type(self, i):
        item = self.config["types"][self.selected]

        async def commit(i, values):
            data = copy.deepcopy(self.config)
            data["types"][self.selected].update(values)
            if (
                values["emoji"]
                and discord.PartialEmoji.from_str(values["emoji"]).id is None
                and len(values["emoji"]) > 12
            ):
                raise ValueError("Use a single emoji or a Discord custom emoji.")
            await self.save(i, data)

        await i.response.send_modal(
            Edit(
                self,
                "Ticket type",
                [
                    ("section_title", "Section heading", section_heading(item), 80),
                    (
                        "description",
                        "Section description (Discord formatting)",
                        item["description"],
                        300,
                    ),
                    ("button_label", "Button label", button_label(item), 80),
                    ("emoji", "Button emoji (optional)", item["emoji"], 100),
                ],
                commit,
            )
        )

    async def edit_questions(self, i):
        async def commit(i, values):
            data = copy.deepcopy(self.config)
            data["types"][self.selected]["questions"] = [
                q.strip() for q in values["questions"].splitlines() if q.strip()
            ]
            await self.save(i, data)

        await i.response.send_modal(
            Edit(
                self,
                "Intake form",
                [
                    (
                        "questions",
                        "One required question per line (maximum 5)",
                        "\n".join(self.config["types"][self.selected]["questions"]),
                        230,
                    )
                ],
                commit,
            )
        )

    async def setting_form(self, i, title, fields, integers=()):
        async def commit(i, values):
            try:
                values.update({key: int(values[key]) for key in integers})
            except ValueError:
                raise ValueError("Enter whole numbers for numeric settings.") from None
            data = copy.deepcopy(self.config)
            collection = "panels" if self.selected in data["panels"] else "types"
            target = (
                data[collection][self.selected]["overrides"] if self.selected else data["defaults"]
            )
            target.update(values)
            await self.save(i, data)

        await i.response.send_modal(
            Edit(
                self,
                title,
                [(key, label, self.settings[key], limit) for key, label, limit in fields],
                commit,
            )
        )

    async def edit_limits(self, i):
        await self.setting_form(
            i,
            "Limits & archive retention",
            [
                ("cooldown", "Opening cooldown in seconds (0–86400)", 5),
                ("max_open", "Active tickets per member (1–20)", 2),
                ("retention_days", "Stored archive retention in days (1–3650)", 4),
            ],
            ("cooldown", "max_open", "retention_days"),
        )

    async def edit_messages(self, i):
        await self.setting_form(
            i,
            "Ticket messages",
            [
                ("welcome", "Welcome message", 1000),
                ("close_message", "Closing message", 1000),
            ],
        )

    async def edit_reply(self, i):
        async def commit(i, values):
            if not values["name"]:
                raise ValueError("Enter a reply name.")
            replies = copy.deepcopy(self.settings["saved_replies"])
            if values["body"]:
                replies[values["name"]] = values["body"]
            else:
                replies.pop(values["name"], None)
            await self.set_value(i, "saved_replies", replies)

        await i.response.send_modal(
            Edit(
                self,
                "Saved reply",
                [
                    ("name", "Reply name (match an existing name to edit)", "", 80),
                    ("body", "Reply text (blank deletes this reply)", "", 1500),
                ],
                commit,
            )
        )

    async def edit_automation(self, i):
        await self.setting_form(
            i,
            "Ticket timers",
            [
                ("reminder_hours", "Remind waiting member after hours (0=off)", 4),
                ("auto_close_hours", "Close waiting member after hours (0=off)", 4),
                ("escalation_hours", "Alert for overdue staff reply (0=off)", 4),
            ],
            ("reminder_hours", "auto_close_hours", "escalation_hours"),
        )

    async def edit_hours(self, i):
        await self.setting_form(
            i,
            "Daily support hours",
            [
                ("support_start", "Opening hour (0–23)", 2),
                ("support_end", "Closing hour (1–24)", 2),
                ("timezone", "Timezone (e.g. America/New_York)", 80),
            ],
            ("support_start", "support_end"),
        )

    async def reset_defaults(self, i):
        data = copy.deepcopy(self.config)
        collection = "panels" if self.selected in data["panels"] else "types"
        data[collection][self.selected]["overrides"] = {}
        await self.save(i, data)

    async def reset_panel_logs(self, i):
        data = copy.deepcopy(self.config)
        overrides = data["panels"][self.selected]["overrides"]
        overrides.pop("log_channels", None)
        overrides.pop("transcript_channels", None)
        await self.save(i, data)

    async def toggle_system(self, i):
        data = copy.deepcopy(self.config)
        data["enabled"] = not data["enabled"]
        if data["enabled"] and not data["panels"]:
            raise ValueError("Create and configure a ticket panel first.")
        await self.save(i, data)

    async def toggle_entry(self, i):
        data = copy.deepcopy(self.config)
        item = data["panels" if self.page == "panel" else "types"][self.selected]
        item["enabled"] = not item["enabled"]
        await self.save(i, data)

    async def preview(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        if self.opening:
            from .opening import preview_opening

            await preview_opening(i, self.config["panels"][self.selected])
        else:
            await self.cog.preview_panel(i, self.config, self.selected)

    async def publish(self, i):
        await i.response.defer()
        await self.cog.publish(i.guild, self.selected, self.revision)
        await self.show(
            i,
            "publish",
            self.selected,
            notice="Panel published. Its controls remain available after bot restarts.",
        )


class TicketButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=(
        "ky:ticket:(?P<token>[0-9a-f]{32}):(?P<action>open|manage|close|reo"
        "pen|transcript|rate|accept|keep|approve|decline|claim):(?P<kind>[0-9a-f]{32}|0)"
    ),
):
    def __init__(self, token, action, kind="0", *, label=None, emoji=None, disabled=False):
        self.token, self.action, self.kind = token, action, kind
        super().__init__(
            discord.ui.Button(
                label=label or action.title(),
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
                custom_id=f"ky:ticket:{token}:{action}:{kind}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["token"], match["action"], match["kind"])

    async def callback(self, i):
        cog = i.client.get_cog("Tickets")
        try:
            if cog is None:
                raise ValueError("Tickets are temporarily unavailable.")
            await cog.action(i, self.token, self.action, self.kind)
        except ValueError as error:
            await send_private(i, str(error))
        except Exception as error:
            await report_error(i, error)


class PanelPage(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ky:ticket-page:(?P<token>[0-9a-f]{32}):(?P<page>[0-9]+)",
):
    def __init__(self, token, page, *, label="Next", disabled=False):
        self.token, self.page = token, page
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"ky:ticket-page:{token}:{page}",
                disabled=disabled,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["token"], int(match["page"]))

    async def callback(self, i):
        try:
            cog = i.client.get_cog("Tickets")
            if cog is None:
                raise ValueError("Tickets are temporarily unavailable.")
            await cog.page_panel(i, self.token, self.page)
        except ValueError as error:
            await send_private(i, str(error))
        except Exception as error:
            await report_error(i, error)


class PublicPanel(Panel):
    def __init__(self, config, token, *, preview=False, page=0):
        super().__init__(timeout=None)
        item = config["panels"][token]
        enabled, page, pages = visible_types(config, token, page)
        self.panel.accent_colour = int(item["color"], 16)
        if item.get("header_mode", "ky") != "none":
            self.panel.add_item(
                discord.ui.MediaGallery(
                    NoAltMediaGalleryItem("attachment://ticket_panel_header.png")
                )
            )
        if not (
            item.get("header_mode", "ky") == "ky"
            or (item.get("header_mode") == "custom" and item.get("header_title"))
        ):
            self.text(f"## {safe(item['title'])}")
        self.text_block(item, "before")
        if item["image"]:
            self.panel.add_item(discord.ui.MediaGallery(NoAltMediaGalleryItem(item["image"])))
        for index, (key, kind) in enumerate(enabled):
            if kind and kind["enabled"]:
                control = TicketButton(
                    token,
                    "open",
                    key,
                    label=button_label(kind),
                    emoji=kind["emoji"] or None,
                    disabled=preview or not item["enabled"],
                )
                style = kind.get("heading_style", "bold")
                if style == "decorative":
                    self.panel.add_item(
                        discord.ui.MediaGallery(
                            NoAltMediaGalleryItem("attachment://" + heading_file(key))
                        )
                    )
                    title = ""
                else:
                    title = format_text(section_heading(kind), style)
                body = format_text(kind["description"], kind.get("description_style", "subtext"))
                self.panel.add_item(
                    discord.ui.Section(
                        "\n".join(filter(None, (title, body))) or "\u200b",
                        accessory=control,
                    )
                )
        self.text_block(item, "after")
        if pages > 1:
            self.text(f"-# Page {page + 1} of {pages}")
            self.panel.add_item(
                discord.ui.ActionRow(
                    PanelPage(
                        token,
                        max(0, page - 1),
                        label="Previous",
                        disabled=preview or page == 0,
                    ),
                    PanelPage(
                        token,
                        min(pages - 1, page + 1),
                        disabled=preview or page + 1 >= pages,
                    ),
                )
            )
        if item["footer"].strip():
            self.text(f"-# {safe(item['footer'])}")
        if item.get("footer_mode", "ky") != "none":
            self.panel.add_item(
                discord.ui.MediaGallery(
                    NoAltMediaGalleryItem("attachment://ticket_panel_footer.png")
                )
            )
        self.finish()
        if self.content_length() > 4000:
            raise ValueError(
                "This panel page has too much formatted text. "
                "Shorten the panel message or topic descriptions."
            )

    def text_block(self, item, position):
        from .sections import blocks, image_file

        for block in blocks(item, position):
            if not (block.get("heading", "").strip() or block.get("body", "").strip()):
                continue
            if block.get("divider"):
                self.panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            for part in ("heading", "body"):
                content = block.get(part, "")
                if not content.strip():
                    continue
                style = block.get(part + "_style", "plain")
                if style == "decorative":
                    self.panel.add_item(
                        discord.ui.MediaGallery(
                            NoAltMediaGalleryItem(
                                "attachment://" + image_file(position, block, part)
                            )
                        )
                    )
                else:
                    self.text(format_text(content, style))


class Intake(discord.ui.Modal):
    def __init__(self, cog, panel, kind, questions, user, message):
        super().__init__(title="Open a ticket", timeout=600)
        self.cog, self.token, self.kind, self.user, self.message = (
            cog,
            panel,
            kind,
            user,
            message,
        )
        self.questions = questions
        self.fields = []
        for question in questions:
            field = discord.ui.TextInput(
                label=question, style=discord.TextStyle.paragraph, max_length=800
            )
            self.add_item(field)
            self.fields.append(field)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            if i.user.id != self.user:
                raise ValueError("Open your own ticket form.")
            answers = list(zip(self.questions, [field.value for field in self.fields], strict=True))
            await self.cog.create_ticket(i, self.token, self.kind, answers, self.message)
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


def ticket_controls(ticket):
    view = discord.ui.View(timeout=None)
    actions = (
        ("manage", "approve", "decline", "transcript")
        if ticket["state"] == "pending_approval"
        else ("manage", "reopen", "transcript")
        if ticket["state"] == "closed"
        else ("claim", "close", "transcript", "manage")
    )
    for action in actions:
        view.add_item(TicketButton(ticket["id"], action))
    return view


class Manage(Panel):
    def __init__(self, cog, ticket, user, *, staff=False, types=(), can_approve=False):
        super().__init__()
        self.cog, self.ticket, self.user, self.staff = cog, ticket, user, staff
        self.text(
            f"**TICKET {ticket['id'][:8]} · {safe(ticket['type_name'])}**\n"
            f"{ticket['state'].replace('_', ' ').title()} · {ticket['priority']}"
        )
        if ticket["state"] == "pending_approval":
            self.text(
                "This ticket is waiting for staff approval. The member can reply after approval."
            )
            if can_approve:
                self.row(
                    "APPROVE TICKET",
                    "Allow this ticket to proceed to the support team.",
                    "Approve",
                    self.do("approve"),
                )
                self.row(
                    "DECLINE TICKET",
                    "Close this request with a recorded reason.",
                    "Decline",
                    self.do("decline"),
                )
        if staff and ticket["state"] in {"open", "waiting_staff", "waiting_user"}:
            self.row(
                "OWNERSHIP",
                f"<@{ticket['claimed']}>" if ticket["claimed"] else "Unclaimed",
                "Unclaim" if ticket["claimed"] == user else "Claim",
                self.do("claim"),
            )
            self.row(
                "STAFF NOTES",
                "Private notes are never posted into the member's ticket.",
                "View / add",
                self.do("notes"),
            )
            self.row(
                "AUTOMATION",
                "Paused" if ticket["paused"] else "Running",
                "Resume" if ticket["paused"] else "Pause",
                self.do("pause"),
            )
            self.row(
                "PARTICIPANTS & ROUTING",
                "Add or remove members, assign staff, rename or transfer the ticket.",
                "Manage",
                self.do("edit"),
            )
            state = discord.ui.Select(
                placeholder="Ticket status",
                options=[
                    discord.SelectOption(label=label, value=key)
                    for key, label in (
                        ("waiting_staff", "Waiting on staff"),
                        ("waiting_user", "Waiting on member"),
                    )
                ],
            )
            self.picker(state, self.selection("state", state))
            priority = discord.ui.Select(
                placeholder="Priority",
                options=[
                    discord.SelectOption(label=label)
                    for label in ("Low", "Normal", "High", "Urgent")
                ],
            )
            self.picker(priority, self.selection("priority", priority))
            if ticket["state"] != "closed":
                self.row(
                    "CONFIRM RESOLUTION",
                    "Ask the member whether their issue is resolved.",
                    "Request close",
                    self.do("request_close"),
                )
                replies = ticket["settings"].get("saved_replies", {})
                if replies:
                    reply = discord.ui.Select(
                        placeholder="Send a saved reply",
                        options=[discord.SelectOption(label=name) for name in replies],
                    )
                    self.picker(reply, self.selection("reply", reply))
        if staff and ticket["state"] == "closed":
            self.row(
                "STAFF NOTES",
                "View the private staff record.",
                "View / add",
                self.do("notes"),
            )
        for action, title, description in (
            ("transcript", "TRANSCRIPT", "Download the current conversation record."),
            (
                "reopen" if ticket["state"] == "closed" else "close",
                "RESOLUTION",
                "Close with a reason or reopen the conversation.",
            ),
        ):
            self.row(title, description, action.title(), self.do(action))
        if staff and ticket["state"] == "closed":
            self.row(
                "DELETE CHANNEL",
                "Requires a saved transcript and a second confirmation.",
                "Delete",
                self.do("delete"),
            )
        if (
            user == ticket["owner"]
            and ticket["state"] == "closed"
            and ticket["settings"]["feedback"]
        ):
            rating = discord.ui.Select(
                placeholder="Rate your support experience",
                options=[discord.SelectOption(label=f"{n} / 5", value=str(n)) for n in range(1, 6)],
            )
            self.picker(rating, self.selection("rating", rating))
        self.finish()

    async def interaction_check(self, i):
        if i.user.id == self.user and i.guild_id == self.ticket["guild"]:
            return True
        await send_private(i, "Open your own ticket controls.")
        return False

    def do(self, action):
        async def callback(i):
            await self.cog.action(i, self.ticket["id"], action)

        return callback

    def selection(self, action, control):
        async def callback(i):
            await self.cog.change(i, self.ticket["id"], action, control.values[0])

        return callback


class Confirm(Panel):
    def __init__(self, cog, ticket, user, action, reason):
        super().__init__()
        self.cog, self.ticket, self.user, self.action, self.reason = (
            cog,
            ticket,
            user,
            action,
            reason,
        )
        self.text(
            f"**{action.upper()} TICKET {ticket['id'][:8]}?**\n"
            + (
                "Save its transcript and permanently remove the channel."
                if action == "delete"
                else safe(reason)
            )
        )
        self.row(
            "CONFIRM ACTION",
            "This action is recorded in the ticket activity logs.",
            f"Confirm {action}",
            self.confirm,
        )
        self.row("CANCEL", "Keep the ticket as it is.", "Cancel", self.cancel)
        self.finish()

    async def interaction_check(self, i):
        return i.user.id == self.user and i.guild_id == self.ticket["guild"]

    async def confirm(self, i):
        await i.response.defer(ephemeral=True, thinking=True)
        await self.cog.resolve(i, self.ticket["id"], self.action, self.reason)
        self.stop()

    async def cancel(self, i):
        panel = Panel()
        panel.text("Cancelled.")
        await i.response.edit_message(view=panel)
        self.stop()


async def private_admin_parent(cog, i):
    from ky_bot.admin_functions.settings_view import SettingsView

    parent = SettingsView(i.user.id, i.guild_id, cog.bot.server_settings)
    parent.guild = i.guild
    parent.settings = await cog.bot.server_settings.repository.get(i.guild_id)
    parent.ticket_default_channel = (
        i.channel_id if isinstance(getattr(i, "channel", None), discord.TextChannel) else 0
    )
    return private_parent(parent, i)
