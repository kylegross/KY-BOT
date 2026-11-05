"""Confessions uses the shared category strips and control-block layout."""

from ky_bot.views.menu_strips import GROUPS, group_sections, strip_files

LAYOUTS = {
    "channels": (("DESTINATION", ("CONFESSION CHANNELS",)),),
    "channel": (
        ("CHANNEL & DELIVERY", ("CONFESSIONS", "SUBMISSION PANEL")),
        ("DESIGN", ("APPEARANCE",)),
        ("ACCESS & PRIVACY", ("SUBMISSIONS & DELIVERY", "LOGGING & APPROVAL")),
    ),
    "behavior": (
        ("REQUEST SETTINGS", ("ANONYMOUS REPLIES", "REPORTING")),
        ("DELIVERY", ("STICKY SUBMISSION PANEL", "STICKY FREQUENCY")),
        ("ACCESS & PRIVACY", ("LIMITS", "ROLE ACCESS")),
    ),
    "logging": (
        ("CHANNELS & LOGGING", ("AUTHOR LOGGING", "AUTHOR LOG CHANNEL")),
        ("ACCESS & PRIVACY", ("ADMINISTRATOR APPROVAL", "APPROVAL & REPORT CHANNEL")),
        ("ADMINISTRATION", ("PENDING APPROVALS", "REPORTS", "CONFESSION BANS")),
    ),
    "appearance": (
        ("DESIGN", ("HEADER & FOOTER DESIGN", "DECORATIVE TEXT", "EMBED COLOR & TITLES")),
        ("MESSAGE", ("SUBMISSION PANEL TEXT",)),
        ("REVIEW & PUBLISH", ("PREVIEW",)),
    ),
    "artwork": (
        ("DESIGN", ("ARTWORK FINISH", "HEADER TITLE", "HEADER ARTWORK", "FOOTER ARTWORK")),
        ("REVIEW & PUBLISH", ("USE STANDARD ARTWORK", "PREVIEW")),
    ),
    "lettering": (
        ("DESIGN", ("TEXT FINISH", "CUSTOM TEXT COLOR")),
        ("MESSAGE", ("CONFESSION & FOOTER TEXT", "PANEL HEADING & MESSAGE")),
        ("REVIEW & PUBLISH", ("PREVIEW",)),
    ),
    "permissions": (
        (
            "ACCESS & PRIVACY",
            ("ALLOWED ROLES (EMPTY = EVERYONE)", "BLOCKED ROLES (ALWAYS TAKES PRIORITY)"),
        ),
    ),
    "queue": (("REVIEW & PUBLISH", ("QUEUE CONTENT",)),),
    "reports": (("ADMINISTRATION", ("QUEUE CONTENT",)),),
    "bans": (("ACCESS & PRIVACY", ("CONFESSION BAN ENFORCEMENT", "BAN A MEMBER", "LIFT A BAN")),),
}
GROUPS.update({"confessions:" + page: groups for page, groups in LAYOUTS.items()})


def arrange(panel, page):
    group_sections(panel, "confessions:" + page)


def attachments(page, config):
    return strip_files("confessions:" + page, config["text_finish"], config["text_color"])
