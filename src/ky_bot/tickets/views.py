"""Inline Components V2 menus shared by Command Center and /ticket setup."""

import copy
import uuid

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_context import private_parent
from ky_bot.views.menu_strips import GROUPS, group_sections, heading
from ky_bot.views.sections import section_dividers

from .store import new_panel, new_type


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
    ):
        super().__init__(timeout=None if parent.panel_manager else 600, artwork=True)
        self.cog, self.parent, self.revision = cog, parent, revision
        self.config, self.page, self.selected, self.pick = config, page, selected, pick
        self.offset = offset
        self.text("**COMMAND CENTER · TICKETS**")
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
                        button("Next", self.turn(25), disabled=offset + 25 >= len(collection)),
                    )
                )
        elif page == "panel":
            item = config["panels"][selected]
            self.row("SELECTED PANEL", safe(item["name"]), "Edit text", self.edit_panel)
            self.setting("PANEL CHANNEL", "channel", item, channel=True)
            names = (
                ", ".join(safe(config["types"][key]["name"]) for key in item["types"])
                or "No ticket types selected"
            )
            self.row("TICKET TYPES", names, "Choose types", self.go("panel_types", selected))
            self.row(
                "APPEARANCE", f"#{item['color']} · image and footer.", "Edit", self.edit_appearance
            )
            self.row(
                "PANEL LOG CHANNELS",
                "Override log destinations for tickets opened here.",
                "Configure",
                self.go("panel_logs", selected),
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
        elif page == "panel_types":
            item = config["panels"][selected]
            self.text(
                "**PANEL TICKET TYPES**\nChoose up to 25 topics. Use Previous/Next "
                "to browse all types; changes apply to the current page."
            )
            entries = list(config["types"].items())[offset : offset + 25]
            if entries:
                control = discord.ui.Select(
                    placeholder="Topics offered on this panel",
                    min_values=0,
                    max_values=len(entries),
                    options=[
                        discord.SelectOption(
                            label=value["name"], value=key, default=key in item["types"]
                        )
                        for key, value in entries
                    ],
                )

                async def choose_types(i):
                    visible = {key for key, _ in entries}
                    data = copy.deepcopy(config)
                    data["panels"][selected]["types"] = [
                        key for key in item["types"] if key not in visible
                    ] + list(control.values)
                    await self.save(i, data)

                self.picker(control, choose_types)
            if len(config["types"]) > 25:
                self.panel.add_item(
                    discord.ui.ActionRow(
                        button("Previous", self.turn(-25), disabled=offset == 0),
                        button("Next", self.turn(25), disabled=offset + 25 >= len(config["types"])),
                    )
                )
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
            self.row("SELECTED TICKET TYPE", safe(item["name"]), "Edit", self.edit_type)
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
            self.row("TICKET MESSAGES", "Welcome and closing messages.", "Edit", self.edit_messages)
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
            for title, key in (
                ("SUPPORT ROLES", "support_roles"),
                ("TICKET MANAGERS", "manager_roles"),
                ("ALLOWED TO OPEN", "allowed_roles"),
                ("BLOCKED FROM OPENING", "blocked_roles"),
            ):
                self.setting(title, key, self.settings, roles=True)
            self.text(
                "-# Empty allowed roles permits everyone. Blocked roles take "
                "priority. Administrators retain access to private channels."
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
            self.setting("TRANSCRIPT CHANNELS", "transcript_channels", self.settings, channels=True)
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
                if t["state"] in {"open", "waiting_staff", "waiting_user", "creating"}
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

        if selected and page in {"access", "destinations", "type_workflow", "automation"}:
            self.row(
                "TYPE OVERRIDES",
                "Reset this type to all current shared defaults.",
                "Reset defaults",
                self.reset_defaults,
            )
        if page != "home":
            category = {
                "access": "ACCESS & PRIVACY",
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
            "panels"
            if page == "panel"
            else "types"
            if page == "type"
            else "panel"
            if page in {"publish", "panel_types", "panel_logs"}
            else "type"
            if selected
            else "home"
        )
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
                self.go(destination, selected if destination in {"panel", "type"} else None),
            )
        self.finish()

    @property
    def settings(self):
        if self.selected and self.page in {"panel", "panel_types", "publish", "panel_logs"}:
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
        )

    def go(self, page, selected=None, *, pick=None):
        async def callback(i):
            await i.response.defer()
            await self.show(i, page, selected, pick=pick)

        return callback

    def turn(self, delta):
        async def callback(i):
            await i.response.defer()
            await self.show(i, self.page, self.selected, offset=max(0, self.offset + delta))

        return callback

    async def save(self, i, data, *, page=None, selected=None):
        if not i.response.is_done():
            await i.response.defer()
        await self.cog.store.configure(self.parent.guild_id, self.revision, data)
        await self.show(
            i,
            page or self.page,
            self.selected if selected is None else selected,
            notice="Saved. Publish a panel to update its public appearance.",
        )

    async def set_value(self, i, key, value):
        data = copy.deepcopy(self.config)
        if self.page == "panel":
            data["panels"][self.selected][key] = value
        elif self.page == "panel_logs":
            data["panels"][self.selected].setdefault("overrides", {})[key] = value
        elif self.selected:
            data["types"][self.selected]["overrides"][key] = value
        else:
            data["defaults"][key] = value
        await self.save(i, data)

    def setting(
        self, title, key, values, *, roles=False, channels=False, channel=False, category=False
    ):
        current = values[key]
        entries = current if isinstance(current, list) else ([current] if current else [])
        label = (
            ", ".join(f"<@&{value}>" if roles else f"<#{value}>" for value in entries)
            or "Not configured"
        )
        if self.selected and self.page not in {"panel", "panel_logs"}:
            label += (
                " · Override"
                if key in self.config["types"][self.selected]["overrides"]
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
                        await self.cog.validate_log(target, self.settings)
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
            data[self.page][key] = new_panel(name) if self.page == "panels" else new_type(name)
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
                    ("description", "Public message", item["description"], 1500),
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
                    ("name", "Name / button label", item["name"], 80),
                    ("description", "Topic description", item["description"], 100),
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
            target = (
                data["types"][self.selected]["overrides"] if self.selected else data["defaults"]
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
            [("welcome", "Welcome message", 1000), ("close_message", "Closing message", 1000)],
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
        data["types"][self.selected]["overrides"] = {}
        await self.save(i, data)

    async def reset_panel_logs(self, i):
        data = copy.deepcopy(self.config)
        data["panels"][self.selected]["overrides"] = {}
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
        await i.followup.send(
            view=PublicPanel(self.config, self.selected, preview=True), ephemeral=True
        )

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
        "pen|transcript|rate|accept|keep):(?P<kind>[0-9a-f]{32}|0)"
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


class PublicPanel(Panel):
    def __init__(self, config, token, *, preview=False):
        super().__init__(timeout=None)
        item = config["panels"][token]
        self.panel.accent_colour = int(item["color"], 16)
        self.text(f"## {safe(item['title'])}\n{item['description']}")
        if item["image"]:
            self.panel.add_item(discord.ui.MediaGallery(NoAltMediaGalleryItem(item["image"])))
        enabled = [
            (key, config["types"][key])
            for key in item["types"]
            if key in config["types"] and config["types"][key]["enabled"]
        ]
        for index, (key, kind) in enumerate(enabled):
            if kind and kind["enabled"]:
                control = TicketButton(
                    token,
                    "open",
                    key,
                    label=kind["name"],
                    emoji=kind["emoji"] or None,
                    disabled=preview or not item["enabled"],
                )
                if len(enabled) <= 5:
                    self.panel.add_item(
                        discord.ui.Section(
                            f"**{safe(kind['name'])}**\n-# {safe(kind['description'])}",
                            accessory=control,
                        )
                    )
                else:
                    if index % 5 == 0:
                        row = discord.ui.ActionRow()
                        self.panel.add_item(row)
                    row.add_item(control)
        self.text(f"-# {safe(item['footer'])}")
        self.finish()


class Intake(discord.ui.Modal):
    def __init__(self, cog, panel, kind, questions, user, message):
        super().__init__(title="Open a ticket", timeout=600)
        self.cog, self.token, self.kind, self.user, self.message = cog, panel, kind, user, message
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
        ("manage", "reopen", "transcript")
        if ticket["state"] == "closed"
        else ("manage", "close", "transcript")
    )
    for action in actions:
        view.add_item(TicketButton(ticket["id"], action))
    return view


class Manage(Panel):
    def __init__(self, cog, ticket, user, *, staff=False, types=()):
        super().__init__()
        self.cog, self.ticket, self.user, self.staff = cog, ticket, user, staff
        self.text(
            f"**TICKET {ticket['id'][:8]} · {safe(ticket['type_name'])}**\n"
            f"{ticket['state'].replace('_', ' ').title()} · {ticket['priority']}"
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
                "EDIT TICKET",
                "Change channel name or transfer to another ticket type.",
                "Edit",
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
                "STAFF NOTES", "View the private staff record.", "View / add", self.do("notes")
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
    return private_parent(parent, i)
