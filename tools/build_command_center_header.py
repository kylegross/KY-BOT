"""Build the packaged Command Center header using the established KY BOT renderer."""

import io
import sys
from pathlib import Path

from artwork_io import save_artwork
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ky_bot.services.collection import floral_background  # noqa: E402
from ky_bot.services.footers import centered_overlay  # noqa: E402
from ky_bot.services.headers import render_header  # noqa: E402
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
    save_artwork(header, ROOT / "src/ky_bot/assets/header/ky_header_command_center.png")
    footer = floral_background("silver_neon", (2176, 210))
    footer.alpha_composite(centered_overlay("silver_neon", footer.size, framed=True))
    save_artwork(footer, ROOT / "src/ky_bot/assets/footer/ky_footer_command_center.png")
    mask = text_mask("SERVER SETTINGS", role="display", size=44, min_size=44, max_width=1500)
    lettering = Image.new("RGBA", mask.size, "#D6E2F3")
    lettering.putalpha(mask)
    label = Image.new("RGBA", (1600, mask.height + 8))
    label.alpha_composite(lettering, (0, 4))
    save_artwork(label, ROOT / "src/ky_bot/assets/header/ky_settings_title.png")


if __name__ == "__main__":
    main()
