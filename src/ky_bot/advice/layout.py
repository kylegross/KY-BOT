"""Advice settings use the Command Center's branded groups and row spacing."""

from ky_bot.views.menu_strips import GROUPS, group_sections, strip_files

LAYOUTS = {
    "channels": (("DESTINATION", ("ASK CHANNEL",)),),
    "main": (
        ("CHANNEL & DELIVERY", ("CHANNELS & ROLES", "DELIVERY & PRIVACY")),
        ("DESIGN", ("APPEARANCE", "ACCESS PANEL SECTIONS")),
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
        ("DESIGN", ("HEADER & FOOTER", "COLORS & FOOTER TEXT", "DECORATIVE TEXT FINISH")),
        ("MESSAGE", ("ASK CHANNEL PANEL", "ACCESS PANEL TEXT")),
    ),
    "artwork": (
        (
            "DESIGN",
            (
                "HEADER IMAGE",
                "FOOTER IMAGE",
                "HEADER IMAGE TITLE & COLOR",
                "HEADER IMAGE TITLE FINISH",
                "ARTWORK FINISH",
                "CUSTOM HEADER COLORS",
                "CUSTOM FOOTER COLORS",
            ),
        ),
        ("REVIEW & PUBLISH", ("STANDARD ARTWORK", "PREVIEW ARTWORK")),
    ),
    "sections": (("MESSAGE", ("PANEL SECTIONS", "ADD SECTION")),),
    "section": (
        ("MESSAGE", ("HEADING", "DECORATIVE SIDE LINES", "TEXT ROWS", "ADD TEXT ROW")),
        ("ADMINISTRATION", ("MOVE UP", "MOVE DOWN", "REMOVE")),
    ),
    "row": (
        ("MESSAGE", ("ROW TEXT & BUTTON", "BUTTON ACTION")),
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
