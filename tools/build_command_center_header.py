"""Build the packaged Command Center header using the established KY BOT renderer."""

import sys
from pathlib import Path

from artwork_io import save_artwork
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ky_bot.services.footers import asset, metal, rectangular_frame  # noqa: E402
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
    image.alpha_composite(overlay("dark_silver", framed=framed, height=280))
    mask = text_mask("COMMAND CENTER", role="display", size=80, max_width=1280)
    x, y = (image.width - mask.width) // 2, (image.height - mask.height) // 2
    title = metal(mask, "dark_silver", sheen=0.3)
    image.alpha_composite(title, (x, y))
    draw = ImageDraw.Draw(image)
    for line_y in (y - 18, y + mask.height + 18):
        draw.line((x, line_y, x + mask.width, line_y), fill="#292724", width=2)
    return image


def main() -> None:
    header = build_header(framed=True)
    header = header.crop(header.getchannel("A").getbbox())
    header = ImageOps.contain(header, (1600, 280), Image.Resampling.LANCZOS)
    save_artwork(header, ROOT / "src/ky_bot/assets/header/ky_header_command_center.png")
    # Keep a compact background crop and a proportionate centered icon at the new height.
    background = Image.open(
        ROOT / "assets/branding/footers/masters/approved-materials/floral_wreath_background.png"
    ).convert("RGBA")
    footer = ImageOps.fit(background, (2176, 210), Image.Resampling.LANCZOS)
    footer.alpha_composite(rectangular_frame("dark_silver", footer.size))
    icon = ImageOps.contain(asset("dark_silver_icon.png").convert("RGBA"), (145, 98))
    footer.alpha_composite(
        icon, ((footer.width - icon.width) // 2, (footer.height - icon.height) // 2)
    )
    save_artwork(
        footer, ROOT / "src/ky_bot/assets/footer/ky_footer_floral_wreath_centered_framed.png"
    )
    mask = text_mask("SERVER SETTINGS", role="display", size=44, min_size=44, max_width=1500)
    lettering = Image.new("RGBA", mask.size, "#D8BB78")
    lettering.putalpha(mask)
    label = Image.new("RGBA", (1600, mask.height + 8))
    label.alpha_composite(lettering, (0, 4))
    save_artwork(label, ROOT / "src/ky_bot/assets/header/ky_settings_title.png")


if __name__ == "__main__":
    main()
