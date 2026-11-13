"""Portable silver title artwork, using packaged brand masks and the menu ornament."""

import io
from functools import lru_cache
from importlib.resources import files

from PIL import Image, ImageDraw, ImageOps

from ky_bot.design.collection import COLOURS
from ky_bot.design.footers import text_ink
from ky_bot.design.headers import asset
from ky_bot.design.typography import SECTION_HEADING_LEFT
from ky_bot.design.welcome import font, readable

DIVIDER = " 𖥸 "
PROMPT = "GOT A SECRET, A HOT TAKE, OR A THOUGHT YOU CAN'T SAY OUTLOUD?"
MASKS = {
    "CONFESSIONS": "title_confessions.png",
    PROMPT: "title_secret_prompt.png",
    "ANONYMOUS CONFESSION": "title_confession.png",
    "ANONYMOUS REPLY": "title_reply.png",
}

MASKS.update(
    {
        "SUBMIT CONFESSION": "title_submit_confession.png",
        "REVIEW YOUR CONFESSION": "title_review_your_confession.png",
        "REVIEW YOUR REPLY": "title_review_your_reply.png",
        "REPORT CONFESSION": "title_report_confession.png",
        "CONFESSION REPORTS": "title_confession_reports.png",
        "ADMINISTRATOR APPROVAL": "title_administrator_approval.png",
        "CHANNELS": "title_channels.png",
        "CHANNEL SETTINGS": "title_channel_settings.png",
        "CONFESSIONS · CHANNEL SETTINGS": "title_confessions_channel_settings.png",
        "SUBMISSIONS & DELIVERY": "title_submissions_delivery.png",
        "LOGGING & APPROVAL": "title_logging_approval.png",
        "LOGGING & REVIEW": "title_logging_review.png",
        "APPEARANCE": "title_appearance.png",
        "HEADER & FOOTER DESIGN": "title_header_footer_design.png",
        "DECORATIVE TEXT": "title_decorative_text.png",
        "ROLE ACCESS": "title_role_access.png",
        "PENDING APPROVALS": "title_pending_approvals.png",
        "REPORTS": "title_reports.png",
        "CONFESSION BANS": "title_confession_bans.png",
        "LOGS": "title_logs.png",
        "AUTHOR LOGS": "title_author_logs.png",
    }
)


def lettering(text):
    if text in MASKS:
        with files("ky_bot.confessions").joinpath("assets", MASKS[text]).open("rb") as stream:
            return Image.open(stream).convert("L")
    face = font("display", 78)
    text = readable(text)[:80] or "CONFESSIONS"
    bounds = face.getbbox(text)
    mask = Image.new("L", (max(1, bounds[2] - bounds[0] + 4), max(1, bounds[3] - bounds[1] + 4)))
    ImageDraw.Draw(mask).text((2 - bounds[0], 2 - bounds[1]), text, font=face, fill=255)
    return mask


@lru_cache(maxsize=128)
def title_image(title, finish="silver_neon", text_color=None):
    def paint(mask):
        return text_ink(mask, finish, text_color)

    if title == PROMPT:
        mask = lettering(title)
        # Two compact menu-sized lines; the archived prompt mask was oversized.
        mask = mask.resize((round(mask.width * 66 / mask.height), 66), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (1120, mask.height + 32))
        left = (1120 - mask.width) // 2 + 2
        image.alpha_composite(paint(mask), (left, 16))
        middle = 16 + mask.height // 2
        pen = ImageDraw.Draw(image)
        rule_color = text_color or ("#BFCAD9" if finish == "silver_neon" else COLOURS[finish])
        if left > 24:
            pen.line((2, middle, left - 18, middle), fill=rule_color, width=2)
            pen.line((left + mask.width + 18, middle, 1118, middle), fill=rule_color, width=2)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()
    parts = title.split(DIVIDER, 1)
    glyphs = [lettering(parts[0])]
    if len(parts) == 2:
        # This is the same independently inverted lily used by submenu headers.
        lily = asset("ky_divider_bamum.png").getchannel("A").transpose(Image.Transpose.ROTATE_180)
        lily = lily.crop(lily.getbbox())
        glyphs.extend((lily, lettering(parts[1])))
    height, gap = 26, 16
    scaled = []
    for index, glyph in enumerate(glyphs):
        target_height = 28 if len(parts) == 2 and index == 1 else height
        scaled.append(
            glyph.resize(
                (max(1, round(glyph.width * target_height / glyph.height)), target_height),
                Image.Resampling.LANCZOS,
            )
        )
    line_height = max(glyph.height for glyph in scaled)
    mask = Image.new("L", (sum(g.width for g in scaled) + gap * (len(scaled) - 1), line_height))
    x = 0
    for glyph in scaled:
        mask.paste(glyph, (x, (line_height - glyph.height) // 2))
        x += glyph.width + gap
    mask = ImageOps.contain(mask, (1080, line_height), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (1120, mask.height + 32))
    image.alpha_composite(paint(mask), (SECTION_HEADING_LEFT, 16))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()
