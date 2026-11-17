"""Confessions uses the shared category strips and control-block layout."""

from ky_bot.views.menu_strips import GROUPS, group_sections, strip_files

LAYOUTS = {
    "channels": (("DESTINATION", ("CONFESSION CHANNELS",)),),
    "channel": (
        ("CHANNEL & DELIVERY", ("CONFESSIONS", "SUBMISSION PANEL")),
        ("DESIGN", ("PANEL TEXT", "APPEARANCE")),
        ("ACCESS & PRIVACY", ("SUBMISSIONS & DELIVERY", "LOGGING & APPROVAL")),
    ),
    "behavior": (
        ("REQUEST SETTINGS", ("ANONYMOUS REPLIES", "APPROVAL & REPORT CHANNEL", "REPORTING")),
        ("DELIVERY", ("STICKY SUBMISSION PANEL", "STICKY FREQUENCY")),
        ("ACCESS & PRIVACY", ("LIMITS", "ROLE ACCESS")),
    ),
    "logging": (
        ("CHANNELS & LOGGING", ("AUTHOR LOG CHANNEL", "AUTHOR LOGGING")),
        ("ACCESS & PRIVACY", ("APPROVAL & REPORT CHANNEL", "ADMINISTRATOR APPROVAL")),
        ("ADMINISTRATION", ("PENDING APPROVALS", "REPORTS", "CONFESSION BANS")),
    ),
    "appearance": (
        (
            "DESIGN",
            ("HEADER IMAGE", "FOOTER IMAGE", "PANEL HEADING COLORS", "EMBED COLOR & TITLES"),
        ),
        ("MESSAGE", ("SUBMISSION PANEL TEXT",)),
        ("REVIEW & PUBLISH", ("PREVIEW",)),
    ),
    "artwork": (
        (
            "DESIGN",
            (
                "KY BOT IMAGE COLORS",
                "HEADER ARTWORK",
                "FOOTER ARTWORK",
                "CUSTOM HEADER COLORS",
                "CUSTOM FOOTER COLORS",
            ),
        ),
        ("MESSAGE", ("TEXT ON IMAGES",)),
        ("REVIEW & PUBLISH", ("USE STANDARD ARTWORK", "PREVIEW")),
    ),
    "image_text": (
        ("MESSAGE", ("HEADER TITLE", "TITLE ON UPLOADED HEADER", "FOOTER IMAGE TEXT")),
        ("REVIEW & PUBLISH", ("PREVIEW",)),
    ),
    "lettering": (
        ("DESIGN", ("PANEL HEADING COLORS", "CUSTOM TEXT COLOR")),
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
for part in ("header", "footer"):
    LAYOUTS[part + "_artwork"] = (
        (
            part.upper() + " IMAGE",
            ("IMAGE SOURCE", "IMAGE COLORS", "CUSTOM IMAGE COLORS", "RESET IMAGE"),
        ),
        ("TITLE", ("IMAGE TEXT & COLORS", "TITLE ON UPLOADED HEADER", "SAVED CUSTOM COLORS")),
        ("REVIEW & PUBLISH", ("PREVIEW",)),
    )
GROUPS.update({"confessions:" + page: groups for page, groups in LAYOUTS.items()})


def arrange(panel, page):
    group_sections(panel, "confessions:" + page)


def attachments(page, config):
    return strip_files("confessions:" + page, config["text_finish"], config["text_color"])
