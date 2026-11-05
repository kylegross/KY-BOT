"""Validated optional lettering color, independent of artwork finish."""

import re


def text_colour(value):
    if value is None or value == "":
        return None
    value = str(value).strip().removeprefix("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", value):
        raise ValueError(
            "Enter a six-digit text color such as #39DCCB, or leave blank for the finish."
        )
    return "#" + value
