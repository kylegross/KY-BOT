"""Advice settings use the Command Center's branded groups and row spacing."""

from ky_bot.views.menu_strips import GROUPS, group_sections, strip_files

LAYOUTS = {
    "channels": (
        ("CHANNEL & DELIVERY", ("SAVED ASK CHANNELS",)),
        ("DESTINATION", ("ASK CHANNEL",)),
    ),
    "main": (
        ("CHANNEL & DELIVERY", ("CHANNELS & ROLES", "DELIVERY & PRIVACY")),
        ("DESIGN", ("APPEARANCE", "PANEL TEXT & BUTTONS")),
        ("REVIEW & PUBLISH", ("PREVIEW & PUBLISH",)),
        ("ADMINISTRATION", ("PENDING APPROVALS", "ASK FOR ADVICE STATUS")),
    ),
    "roles": (
        ("CHANNEL & DELIVERY", ("ACCESS & RESPONSE CHANNEL",)),
        ("ACCESS & PRIVACY", ("VIEW ADVICE ROLE", "GIVE ADVICE ROLE")),
    ),
    "delivery": (
        ("ACCESS & PRIVACY", ("APPROVAL CHANNEL", "QUESTION APPROVAL", "ADVICE RESPONSE APPROVAL")),
        ("CHANNELS & LOGGING", ("AUTHOR LOG CHANNEL", "AUTHOR LOGGING")),
        ("DELIVERY", ("NEW QUESTION ROLE NOTIFICATIONS", "REPOST SUBMISSION PANEL", "LIMITS")),
    ),
    "destinations": (("CHANNELS & LOGGING", ("APPROVAL CHANNEL", "AUTHOR LOG CHANNEL")),),
    "style": (
        ("DESIGN", ("HEADER & FOOTER", "PANEL TEXT & HIGHLIGHT", "ALL SECTION HEADINGS")),
        ("MESSAGE", ("ASK CHANNEL PANEL", "ACCESS PANEL TEXT")),
    ),
    "artwork": (
        ("DESIGN", ("HEADER IMAGE", "FOOTER IMAGE")),
        ("REVIEW & PUBLISH", ("PREVIEW PANELS", "PUBLISH CHANGES")),
    ),
    "header_artwork": (
        ("HEADER IMAGE", ("IMAGE SOURCE", "UPLOAD IMAGE", "IMAGE COLORS", "RESET IMAGE")),
        ("TITLE", ("TITLE TEXT", "TITLE COLOR", "TITLE ON YOUR UPLOADED IMAGE")),
        ("REVIEW & PUBLISH", ("PREVIEW PANELS", "PUBLISH CHANGES")),
    ),
    "footer_artwork": (
        ("FOOTER IMAGE", ("IMAGE SOURCE", "UPLOAD IMAGE", "IMAGE COLORS", "RESET IMAGE")),
        ("TITLE", ("FOOTER IMAGE TEXT",)),
        ("REVIEW & PUBLISH", ("PREVIEW PANELS", "PUBLISH CHANGES")),
    ),
    "sections": (("MESSAGE", ("PANEL SECTIONS", "ADD SECTION")),),
    "section": (
        (
            "MESSAGE",
            (
                "HEADING",
                "ALL SECTION HEADINGS",
                "DECORATIVE SIDE LINES",
                "TEXT ROWS",
                "ADD TEXT ROW",
            ),
        ),
        ("ADMINISTRATION", ("MOVE UP", "MOVE DOWN", "REMOVE")),
    ),
    "row": (
        ("MESSAGE", ("ROW HEADING, TEXT & BUTTON", "BUTTON ACTION")),
        ("ADMINISTRATION", ("MOVE UP", "MOVE DOWN", "REMOVE")),
    ),
    "publish": (
        ("ACCESS & PRIVACY", ("CONFIGURE CHANNEL VISIBILITY",)),
        (
            "REVIEW & PUBLISH",
            (
                "ACCESS PANEL",
                "PUBLISH ACCESS PANEL",
                "ASK CHANNEL PANEL",
                "PUBLISH ASK CHANNEL PANEL",
            ),
        ),
    ),
    "queue": (("REVIEW & PUBLISH", ("PENDING APPROVALS",)),),
}
GROUPS.update({"advice:" + page: groups for page, groups in LAYOUTS.items()})


def arrange(panel, page):
    group_sections(panel, "advice:" + page)


def attachments(page):
    return strip_files("advice:" + page, "silver_neon")
