"""Editable opening panels with standard persistent ticket controls."""

import asyncio
import copy
import string

import discord
from ky_bot.views.media import NoAltMediaGalleryItem

from .artwork import public_files
from .presentation import BODY_STYLES, format_text
from .store import new_panel
from .views import Edit, Panel, PublicPanel, TicketButton, safe, ticket_controls


def opening_design(panel, *, welcome=None):
    value = new_panel("Opened ticket")
    value.update(
        title="Ticket Support",
        footer="",
        description="",
        info_title="{type} · Ticket No. {number}",
        info_title_style="bold",
        info_status="Opened by {owner} · {status} · {assignee}",
        info_status_style="plain",
        approval_text="Waiting for staff approval. You can reply after approval.",
        approval_style="plain",
        intake_identity="{username} ({user_id})",
        intake_identity_style="plain",
        intake_response_style="medium",
        control_labels={},
        status_labels={},
        before_text=welcome or "Thanks for contacting staff. Please explain your issue below.",
    )
    value.update(copy.deepcopy(panel.get("opening_panel") or {}))
    value.pop("opening_panel", None)
    value["types"] = []
    value["overrides"] = {}
    return value


class OpenedPanel(Panel):
    text_block = PublicPanel.text_block

    def __init__(self, design, ticket, *, preview=False):
        super().__init__(timeout=None)
        design = opening_design({"opening_panel": design})
        self.panel.accent_colour = int(design["color"], 16)
        if design.get("header_mode", "ky") != "none":
            self.panel.add_item(
                discord.ui.MediaGallery(
                    NoAltMediaGalleryItem("attachment://ticket_panel_header.png")
                )
            )
        if not (
            design.get("header_mode", "ky") == "ky"
            or (design.get("header_mode") == "custom" and design.get("header_title"))
        ):
            self.text("## " + safe(design["title"]))
        context = template_context(design, ticket, preview=preview)
        for key in ("info_title", "info_status"):
            content = render_template(design.get(key, ""), context)
            if content.strip():
                self.text(display_style(content, design.get(key + "_style", "plain")))
        if ticket["state"] == "pending_approval" and design.get("approval_text", "").strip():
            self.text(
                display_style(
                    render_template(design["approval_text"], context),
                    design.get("approval_style", "plain"),
                )
            )
        self.text_block(design, "before")
        if ticket.get("answers"):
            self.panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            identity = render_template(
                design.get("intake_identity", "{username} ({user_id})"), context
            )
            response = []
            if identity.strip():
                response.append(
                    display_style(identity, design.get("intake_identity_style", "plain"))
                )
            for question, answer in ticket["answers"]:
                if len(ticket["answers"]) > 1:
                    response.append(safe(question))
                text = safe(answer) or "—"
                style = design.get("intake_response_style", "medium")
                response.append(
                    "\n".join("## " + line for line in text.splitlines())
                    if style == "medium"
                    else format_text(text, style)
                )
            self.text("\n".join(response))
        if design["image"]:
            self.panel.add_item(discord.ui.MediaGallery(NoAltMediaGalleryItem(design["image"])))
        controls = ticket_controls(ticket)
        row = discord.ui.ActionRow()
        for control in list(controls.children):
            action = control.action
            label = (
                ("Unclaim" if ticket.get("claimed") else "Claim")
                if action == "claim"
                else control.item.label
            )
            key = "unclaim" if action == "claim" and ticket.get("claimed") else action
            custom = design.get("control_labels", {}).get(key, {})
            control = TicketButton(
                ticket["id"],
                action,
                label=custom.get("label") or label,
                emoji=custom.get("emoji") or None,
                disabled=preview,
            )
            control.item.style = BUTTON_STYLES[custom.get("style", "gray")]
            row.add_item(control)
        self.panel.add_item(row)
        self.text_block(design, "after")
        if design["footer"].strip():
            self.text("-# " + safe(design["footer"]))
        if design.get("footer_mode", "ky") != "none":
            self.panel.add_item(
                discord.ui.MediaGallery(
                    NoAltMediaGalleryItem("attachment://ticket_panel_footer.png")
                )
            )
        # Only explicitly requested section dividers belong in this member-facing panel.
        if self.content_length() > 4000 or len(list(self.walk_children())) > 40:
            raise ValueError(
                "The ticket message is too long for this panel. Shorten the response or panel text."
            )
        from .sections import content_budget

        if (
            content_budget(design)[1]
            + (design.get("header_mode") != "none")
            + (design.get("footer_mode") != "none")
            > 10
        ):
            raise ValueError("Use fewer image headings in the opened ticket panel.")


def example_ticket(design):
    return dict(
        id="a1b2c3d4" + "0" * 24,
        owner=1,
        number=1,
        owner_username="ExampleMember",
        answers=[("How can we help?", "Hi! I would like to request access to the NSFW channels.")],
        type_name="Support",
        state="waiting_staff",
        claimed=0,
        settings={"opening_panel": design},
    )


def validate_opening(design):
    validate_templates(design)
    from .store import defaults, validate

    config = defaults()
    config["panels"]["opening"] = opening_design({"opening_panel": design})
    validate(config)
    from .sections import content_budget

    content, images, characters = content_budget(config["panels"]["opening"])
    characters += sum(len(design.get(key, "")) for key in TEXT_FIELDS)
    if content + 16 > 40 or images + 2 > 10 or characters > 3400:
        raise ValueError(
            "The ticket message is too long for this panel. Shorten the response or panel text."
        )


async def preview_opening(interaction, design):
    view = OpenedPanel(design, example_ticket(design), preview=True)
    files = await asyncio.to_thread(public_files, design)
    try:
        await interaction.followup.send(
            view=view, files=files, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
    finally:
        for file in files:
            file.close()


BUTTON_STYLES = {
    "gray": discord.ButtonStyle.secondary,
    "blue": discord.ButtonStyle.primary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
}
CONTROL_NAMES = {
    "claim": "Claim",
    "unclaim": "Unclaim",
    "close": "Close",
    "transcript": "Save Transcript",
    "manage": "Manage Ticket",
    "approve": "Approve",
    "decline": "Decline",
    "reopen": "Reopen",
}
TEMPLATE_FIELDS = {"type", "number", "username", "user_id", "owner", "status", "assignee"}
TEXT_FIELDS = {
    "info_title": "Ticket title line",
    "info_status": "Member and status line",
    "approval_text": "Approval notice",
    "intake_identity": "Submitted message identity",
}
STYLE_FIELDS = {
    "info_title_style": "Ticket title line",
    "info_status_style": "Member and status line",
    "approval_style": "Approval notice",
    "intake_identity_style": "Submitted message identity",
    "intake_response_style": "Submitted message",
}


def template_context(design, ticket, *, preview=False):
    labels = design.get("status_labels", {})
    username = safe(ticket.get("owner_username") or f"User {ticket['owner']}")
    return dict(
        type=safe(ticket["type_name"]),
        number=str(ticket.get("number", "")),
        username=username,
        user_id=str(ticket["owner"]),
        owner=username,
        status=safe(labels.get(ticket["state"], ticket["state"].replace("_", " ").title())),
        assignee=(
            f"<@{ticket['claimed']}>"
            if ticket.get("claimed")
            else safe(labels.get("unclaimed", "Unclaimed"))
        ),
    )


def render_template(text, context):
    # Expand only exact names, never attribute/index lookup or Python format expressions.
    result = []
    for literal, field, spec, conversion in string.Formatter().parse(text):
        result.append(literal)
        if field is not None:
            if field not in TEMPLATE_FIELDS or spec or conversion:
                raise ValueError("Use only the listed ticket placeholders in text templates.")
            result.append(context[field])
    return "".join(result)


def validate_templates(design):
    for key in TEXT_FIELDS:
        text = design.get(key, "")
        if not isinstance(text, str) or len(text) > 300:
            raise ValueError("Keep each ticket text template under 300 characters.")
        try:
            render_template(text, dict.fromkeys(TEMPLATE_FIELDS, "Example"))
        except (ValueError, KeyError):
            raise ValueError("Use only the listed ticket placeholders in text templates.") from None
    for key in STYLE_FIELDS:
        if design.get(key, "plain") not in BODY_STYLES | {"medium": "Medium bold heading"}:
            raise ValueError("Choose a supported ticket text style.")
    for key, custom in design.get("control_labels", {}).items():
        if key not in CONTROL_NAMES or not custom.get("label", "").strip():
            raise ValueError("Choose a standard ticket action and a nonempty button label.")
        if len(custom["label"]) > 80 or len(custom.get("emoji", "")) > 100:
            raise ValueError("Use button labels up to 80 characters and a single emoji.")
        if custom.get("style", "gray") not in BUTTON_STYLES:
            raise ValueError("Choose gray, blue, green or red for the button color.")
    if any(len(text) > 80 for text in design.get("status_labels", {}).values()):
        raise ValueError("Keep status labels under 80 characters.")


def build_opening_editor(menu, page, pick):
    design = menu.config["panels"][menu.selected]
    if page == "opening_details":
        menu.row(
            "PANEL TEXT",
            "Edit the title, member/status line, approval notice and identity.",
            "Edit text",
            lambda i: edit_templates(menu, i),
        )
        menu.row(
            "TEXT STYLES",
            "Format each line independently, including the submitted message.",
            "Configure",
            menu.go("opening_formats", menu.selected),
        )
        menu.row(
            "STATUS LABELS",
            "Edit waiting, approval, closed and unclaimed wording.",
            "Edit labels",
            lambda i: edit_status_labels(menu, i),
        )
        menu.row(
            "BUTTONS",
            "Edit labels, icons and colors; actions and permissions stay attached.",
            "Configure",
            menu.go("opening_buttons", menu.selected),
        )
        menu.text(
            "-# Placeholders: {type}, {number}, {username}, {user_id}, {owner}, "
            "{status}, {assignee}. Blank text hides that line. Submitted messages appear "
            "inside this panel, below a divider, above the buttons."
        )
    elif page == "opening_formats":
        control = discord.ui.Select(
            placeholder="Choose text to format",
            options=[
                discord.SelectOption(label=label, value=key, default=pick == key)
                for key, label in STYLE_FIELDS.items()
            ],
        )

        async def choose(i):
            await i.response.defer()
            await menu.show(i, page, menu.selected, pick=control.values[0])

        menu.picker(control, choose)
        if pick in STYLE_FIELDS:
            styles = {"medium": "Medium bold heading", **BODY_STYLES}
            current = design.get(pick, "plain")
            selector = discord.ui.Select(
                placeholder="Choose formatting",
                options=[
                    discord.SelectOption(label=label, value=key, default=current == key)
                    for key, label in styles.items()
                ],
            )

            async def style(i):
                await menu.set_panel_art(i, {pick: selector.values[0]})

            menu.picker(selector, style)
    else:
        control = discord.ui.Select(
            placeholder="Choose a standard ticket button",
            options=[
                discord.SelectOption(label=label, value=key) for key, label in CONTROL_NAMES.items()
            ],
        )

        async def choose(i):
            key = control.values[0]
            custom = design.get("control_labels", {}).get(key, {})

            async def commit(i, values):
                labels = copy.deepcopy(design.get("control_labels", {}))
                labels[key] = values
                await menu.set_panel_art(i, {"control_labels": labels})

            await i.response.send_modal(
                Edit(
                    menu,
                    "Edit ticket button",
                    [
                        ("label", "Button text", custom.get("label", CONTROL_NAMES[key]), 80),
                        ("emoji", "Emoji (optional)", custom.get("emoji", ""), 100),
                        (
                            "style",
                            "Color: gray, blue, green or red",
                            custom.get("style", "gray"),
                            10,
                        ),
                    ],
                    commit,
                )
            )

        menu.picker(control, choose)
    menu.row(
        "PRIVATE PREVIEW",
        "Review the whole opening panel and example submitted message.",
        "Preview",
        menu.preview,
    )


async def edit_templates(menu, interaction):
    design = menu.config["panels"][menu.selected]

    async def commit(i, values):
        await menu.set_panel_art(i, values)

    await interaction.response.send_modal(
        Edit(
            menu,
            "Ticket text templates",
            [(key, label, design.get(key, ""), 300) for key, label in TEXT_FIELDS.items()],
            commit,
        )
    )


async def edit_status_labels(menu, interaction):
    design = menu.config["panels"][menu.selected]
    defaults = {
        "waiting_staff": "Waiting Staff",
        "waiting_user": "Waiting User",
        "pending_approval": "Pending Approval",
        "closed": "Closed",
        "unclaimed": "Unclaimed",
    }
    labels = design.get("status_labels", {})

    async def commit(i, values):
        await menu.set_panel_art(i, {"status_labels": values})

    await interaction.response.send_modal(
        Edit(
            menu,
            "Ticket status labels",
            [(key, label, labels.get(key, label), 80) for key, label in defaults.items()],
            commit,
        )
    )


def display_style(text, style):
    if style == "medium":
        return "\n".join("## " + line for line in text.splitlines() if line.strip())
    return format_text(text, style)
