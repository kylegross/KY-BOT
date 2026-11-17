"""One-shot confirmations shown only after successful edits."""

import json


def mark_saved(owner, message="Saved."):
    owner._save_notice = message


def take_notice(owner, explicit=None):
    pending = getattr(owner, "_save_notice", None)
    owner._save_notice = None
    return explicit or pending


def draft_notice(owner, values, message="Draft updated. Save to keep these changes."):
    snapshot = json.dumps(values, sort_keys=True, default=str)
    previous = getattr(owner, "_notice_snapshot", None)
    owner._notice_snapshot = snapshot
    return take_notice(owner) or (
        message if previous is not None and previous != snapshot else None
    )
