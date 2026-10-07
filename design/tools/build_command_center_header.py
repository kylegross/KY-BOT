"""Build the packaged Command Center header using the established KY BOT renderer."""

import io
import sys
from pathlib import Path

from artwork_io import save_artwork
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ky_bot.admin_functions.settings_service import SETTINGS_TITLES  # noqa: E402
from ky_bot.design.collection import floral_background  # noqa: E402
from ky_bot.design.footers import centered_overlay, rectangular_frame  # noqa: E402
from ky_bot.design.headers import render_header  # noqa: E402
from ky_bot.design.typography import text_mask  # noqa: E402


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
    header = build_header(framed=False)
    # Same source width, outline color and thickness produce the same displayed border.
    # Keep the entire outline inside Discord's rounded media clipping area.
    header.alpha_composite(rectangular_frame("silver_neon", header.size, radius=24, inset=10))
    save_artwork(header, ROOT / "src/ky_bot/design/assets/header/ky_header_command_center.png")
    footer = floral_background("silver_neon", (1600, 150))
    footer.alpha_composite(centered_overlay("silver_neon", footer.size))
    footer.alpha_composite(rectangular_frame("silver_neon", footer.size, radius=24, inset=10))
    save_artwork(footer, ROOT / "src/ky_bot/design/assets/footer/ky_footer_command_center.png")
    # Render at 2x the usual 560px panel width, with larger Bold lettering and more leading.
    for title, filename in SETTINGS_TITLES.values():
        mask = text_mask(title, role="display", size=36, min_size=36, max_width=1080)
        lettering = Image.new("RGBA", mask.size, "#D6E2F3")
        lettering.putalpha(mask)
        label = Image.new("RGBA", (1120, mask.height + 32))
        # Two source pixels give about one displayed pixel of left clearance at 560px.
        label.alpha_composite(lettering, (2, 16))
        save_artwork(label, ROOT / "src/ky_bot/design/assets/header" / filename)


if __name__ == "__main__":
    main()
