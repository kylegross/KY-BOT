"""Preview the illustrated floral collection without changing active packaged artwork."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ky_bot.services.collection import COLOURS, floral_background  # noqa: E402
from ky_bot.services.headers import asset  # noqa: E402
from ky_bot.services.typography import text_mask  # noqa: E402

OUTPUT = ROOT / "previews/floral-collection"


def background(style, size):
    return floral_background(style, size)


def lettering(mask, colour):
    result = Image.new("RGBA", mask.size, colour)
    result.putalpha(mask)
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    board = Image.new("RGB", (1700, 1820), "#202127")
    for index, (style, colour) in enumerate(COLOURS.items()):
        y = 30 + index * 600
        label = text_mask(style.replace("_", " ").upper(), role="heading", size=38)
        board.paste(lettering(label, colour), (50, y), label)
        header = background(style, (1600, 280))
        badge = asset(f"{style}_brand.png").crop((100, 68, 371, 149))
        header.alpha_composite(badge.resize((190, 57), Image.Resampling.LANCZOS), (24, 24))
        mask = text_mask("COMMAND CENTER", role="display", size=76, max_width=1040)
        x, top = (1600 - mask.width) // 2, (280 - mask.height) // 2
        header.alpha_composite(lettering(mask, colour), (x, top))
        draw = ImageDraw.Draw(header)
        for rule_y in (top - 16, top + mask.height + 16):
            draw.line((x, rule_y, x + mask.width, rule_y), fill=colour, width=2)
        footer = background(style, (1600, 155))
        icon = Image.open(ROOT / f"src/ky_bot/assets/footer/{style}_icon.png").convert("RGBA")
        icon = ImageOps.contain(icon.crop(icon.getchannel("A").getbbox()), (90, 90))
        footer.alpha_composite(
            lettering(icon.getchannel("A"), colour),
            ((1600 - icon.width) // 2, (155 - icon.height) // 2),
        )
        header.save(OUTPUT / f"{style}_header_preview.png")
        footer.save(OUTPUT / f"{style}_footer_preview.png")
        board.paste(header, (50, y + 70), header)
        board.paste(footer, (50, y + 375), footer)
    board.save(OUTPUT / "three_finishes_preview.png")


if __name__ == "__main__":
    main()
