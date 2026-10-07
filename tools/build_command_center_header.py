"""Build the packaged Command Center header using the established KY BOT renderer."""

import io
import sys
from pathlib import Path

from artwork_io import save_artwork
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ky_bot.services.collection import floral_background  # noqa: E402
from ky_bot.services.footers import centered_overlay  # noqa: E402
from ky_bot.services.headers import render_header  # noqa: E402
from ky_bot.services.settings import SETTINGS_TITLES  # noqa: E402
from ky_bot.services.typography import text_mask  # noqa: E402


def build_header(*, framed: bool = False) -> Image.Image:
    stream = io.BytesIO()
    floral_background("silver_neon", (1600, 280)).save(stream, format="PNG")
    return Image.open(
        io.BytesIO(
            render_header(
                stream.getvalue(), "silver_neon", framed=framed, height=280, title="COMMAND CENTER"
            )
        )
    ).convert("RGBA")


def main() -> None:
    header = build_header(framed=True)
    # Keep the frame inside Discord's rounded media clip instead of touching its edge.
    save_artwork(
        ImageOps.expand(header, border=12, fill=(0, 0, 0, 0)),
        ROOT / "src/ky_bot/assets/header/ky_header_command_center.png",
    )
    footer = floral_background("silver_neon", (2176, 210))
    footer.alpha_composite(centered_overlay("silver_neon", footer.size, framed=True))
    save_artwork(
        ImageOps.expand(footer, border=12, fill=(0, 0, 0, 0)),
        ROOT / "src/ky_bot/assets/footer/ky_footer_command_center.png",
    )
    # Render at 2x the usual 560px panel width, with larger Bold lettering and more leading.
    for title, filename in SETTINGS_TITLES.values():
        mask = text_mask(title, role="display", size=36, min_size=36, max_width=1080)
        lettering = Image.new("RGBA", mask.size, "#D6E2F3")
        lettering.putalpha(mask)
        label = Image.new("RGBA", (1120, mask.height + 16))
        label.alpha_composite(lettering, (0, 8))
        save_artwork(label, ROOT / "src/ky_bot/assets/header" / filename)


if __name__ == "__main__":
    main()
