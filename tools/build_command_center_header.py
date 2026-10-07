"""Build the packaged Command Center header using the established KY BOT renderer."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ky_bot.services.headers import background_mask, overlay  # noqa: E402
from ky_bot.services.typography import text_mask  # noqa: E402


def build_header(*, framed: bool = False) -> Image.Image:
    background = (
        ROOT / "assets/branding/footers/masters/approved-materials/floral_wreath_background.png"
    )
    image = ImageOps.fit(
        Image.open(background).convert("RGBA"), (1600, 280), Image.Resampling.LANCZOS
    )
    image.alpha_composite(Image.new("RGBA", image.size, (255, 255, 255, 32)))
    if framed:
        image.putalpha(background_mask(framed=True, height=280))
        image.alpha_composite(overlay("dark_silver", framed=True, height=280))
    else:
        image.alpha_composite(overlay("dark_silver", framed=False).crop((0, 0, 1600, 110)))
    mask = text_mask("COMMAND CENTER", role="display", size=80, max_width=1280)
    x, y = (1600 - mask.width) // 2, 140
    title = Image.new("RGBA", mask.size, "#292724")
    title.putalpha(mask)
    image.alpha_composite(title, (x, y))
    draw = ImageDraw.Draw(image)
    for line_y in (y - 18, y + mask.height + 18):
        draw.line((x, line_y, x + mask.width, line_y), fill="#292724", width=2)
    return image


def main() -> None:
    build_header(framed=True).save(ROOT / "src/ky_bot/assets/header/ky_header_command_center.png")


if __name__ == "__main__":
    main()
