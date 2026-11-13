"""Editable opening panels with standard persistent ticket controls."""

import asyncio
import copy

import discord
from ky_bot.views.media import NoAltMediaGalleryItem

from .artwork import public_files
from .store import new_panel
from .views import Panel, PublicPanel, TicketButton, safe, ticket_controls


def opening_design(panel, *, welcome=None):
    value = new_panel("Opened ticket")
    value.update(
        title="Ticket Support",
        footer="",
        description="",
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
        status = ticket["state"].replace("_", " ").title()
        owner = f"<@{ticket['owner']}>" if not preview else "@Member"
        assigned = f"<@{ticket['claimed']}>" if ticket.get("claimed") else "Unclaimed"
        reference = f"Ticket No. {ticket['number']}" if ticket.get("number") else "Ticket"
        self.text(
            f"**{safe(ticket['type_name'])} · {reference}**\n"
            f"Opened by {owner} · {status} · {assigned}"
        )
        if ticket["state"] == "pending_approval":
            self.text("Waiting for staff approval. You can reply after approval.")
        self.text_block(design, "before")
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
            row.add_item(TicketButton(ticket["id"], action, label=label, disabled=preview))
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
            raise ValueError("The opened ticket panel is too long. Shorten or remove a section.")
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
        type_name="Support",
        state="waiting_staff",
        claimed=0,
        settings={"opening_panel": design},
    )


def validate_opening(design):
    from .store import defaults, validate

    config = defaults()
    config["panels"]["opening"] = opening_design({"opening_panel": design})
    validate(config)
    from .sections import content_budget

    content, images, characters = content_budget(config["panels"]["opening"])
    if content + 14 > 40 or images + 2 > 10 or characters > 3400:
        raise ValueError("The opened ticket panel is too long. Shorten or remove a section.")


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
