"""Render AutoReact with the established dark silver artwork and typography."""
import io
import sys
from pathlib import Path

from artwork_io import write_artwork

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from ky_bot.design.collection import floral_background  # noqa: E402
from ky_bot.design.headers import render_header  # noqa: E402

stream = io.BytesIO()
floral_background("dark_silver", (1600, 280)).save(stream, format="PNG")
write_artwork(
    ROOT / "src/ky_bot/design/assets/header/ky_header_autoreact.png",
    render_header(stream.getvalue(), "dark_silver", framed=True, height=280, title="AUTOREACT"),
)
