"""Optional headings and additional text sections around ticket buttons."""

import re
import uuid

MAX_SECTIONS = 3


def new_section():
    return dict(
        id=uuid.uuid4().hex,
        heading="New section",
        body="",
        heading_style="bold",
        body_style="plain",
        divider=True,
        finish=None,
        color=None,
    )


def blocks(panel, position):
    from .presentation import panel_text

    main = dict(
        id="main",
        heading=panel.get(position + "_heading", ""),
        body=panel_text(panel, position),
        heading_style=panel.get(position + "_heading_style", "bold"),
        body_style=panel.get(position + "_style", "plain"),
        divider=False,
        finish=panel.get(position + "_finish"),
        color=panel.get(position + "_color"),
    )
    return [main] + panel.get(position + "_sections", [])


def image_file(position, block, part):
    if block["id"] == "main":
        return (
            f"ticket_panel_{position}.png"
            if part == "body"
            else f"ticket_panel_{position}_heading.png"
        )
    return f"ticket_{position}_{block['id']}_{part}.png"


def validate_sections(panel):
    from .presentation import HEADING_STYLES, PANEL_TEXT_STYLES

    for position in ("before", "after"):
        extras = panel.get(position + "_sections", [])
        if not isinstance(extras, list) or len(extras) > MAX_SECTIONS:
            raise ValueError("Add up to three additional sections on each side of the buttons.")
        ids = set()
        for index, block in enumerate(blocks(panel, position)):
            if index:
                token = block.get("id", "")
                if not re.fullmatch(r"[0-9a-f]{32}", token) or token in ids:
                    raise ValueError("This section is invalid. Remove it and add a new section.")
                ids.add(token)
            heading = block.get("heading", "")
            body = block.get("body", "")
            if not isinstance(heading, str) or len(heading) > 80 or "\n" in heading:
                raise ValueError("Section headers use one line of up to 80 characters.")
            if index and (not isinstance(body, str) or len(body) > 500):
                raise ValueError("Additional section text can contain up to 500 characters.")
            if (
                block.get("heading_style", "bold") not in HEADING_STYLES
                or block.get("body_style", "plain") not in PANEL_TEXT_STYLES
            ):
                raise ValueError("Choose a supported section format.")
            for part in ("heading", "body"):
                if (
                    block.get(part + "_style") == "decorative"
                    and block.get(part)
                    and (len(block[part]) > 80 or "\n" in block[part])
                ):
                    raise ValueError("KY BOT special text uses one line of up to 80 characters.")
            if block.get("finish") not in {
                None,
                "gold",
                "dark_silver",
                "silver_neon",
                "red_iridescent",
            }:
                raise ValueError("Choose a supported section finish.")
            color = block.get("color")
            if color is not None and not re.fullmatch(r"[0-9a-fA-F]{6}", color):
                raise ValueError("Use a six-digit section color.")
            if not isinstance(block.get("divider", False), bool):
                raise ValueError("Choose whether the section divider is enabled.")


def content_budget(panel):
    from .presentation import format_text

    count, images, characters = 0, 0, 0
    for position in ("before", "after"):
        for block in blocks(panel, position):
            if not (block.get("heading", "").strip() or block.get("body", "").strip()):
                continue
            count += bool(block.get("divider"))
            for part in ("heading", "body"):
                text = block.get(part, "")
                if not text.strip():
                    continue
                if block.get(part + "_style", "plain") == "decorative":
                    count += 2
                    images += 1
                else:
                    count += 1
                    characters += len(format_text(text, block.get(part + "_style", "plain")))
    return count, images, characters
