"""Build slogan-free header designs and reusable layers from approved KY BOT assets."""

import io
import json
import sys
import zipfile
from pathlib import Path

from artwork_io import save_artwork, write_artwork
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ky_bot.services.collection import floral_background  # noqa: E402
from ky_bot.services.footers import rectangular_frame  # noqa: E402
from ky_bot.services.headers import SIZE, STYLES, overlay, render_header  # noqa: E402
from ky_bot.services.typography import text_mask  # noqa: E402

PACKAGE = ROOT / "src/ky_bot/assets/header"
OUTPUT = ROOT / "assets/branding/headers"
FINISHES = {style: style for style in STYLES}


def main():
    PACKAGE.mkdir(parents=True, exist_ok=True)
    for directory in ("designs", "templates", "layers"):
        (OUTPUT / directory).mkdir(parents=True, exist_ok=True)
    lettering = text_mask("KY BOT", role="heading", size=40, min_size=40, max_width=200)
    for style in STYLES:
        frame = rectangular_frame(style, SIZE)
        save_artwork(frame, OUTPUT / "layers" / f"ky_header_{style}_frame.png")

        icon = Image.open(ROOT / f"src/ky_bot/assets/footer/{style}_icon.png").convert("RGBA")
        bounds = icon.getchannel("A").getbbox()
        icon = ImageOps.contain(icon.crop(bounds), (50, 50), Image.Resampling.LANCZOS)
        brand = Image.new("RGBA", SIZE)
        # A quiet plate makes the fixed brand readable on arbitrary uploaded images.
        plate = (8, 10, 12, 205)
        if style == "dark_silver":
            alpha = icon.getchannel("A")
            icon = Image.new("RGBA", icon.size, "#BDC3CA")
            icon.putalpha(alpha)
        ImageDraw.Draw(brand).rounded_rectangle((100, 68, 370, 148), radius=16, fill=plate)
        brand.alpha_composite(icon, (118 + (50 - icon.width) // 2, 83 + (50 - icon.height) // 2))
        colour = {"gold": "#e8c47c", "silver_neon": "#dbe7ef", "dark_silver": "#BDC3CA"}[style]
        text = Image.new("RGBA", lettering.size, colour)
        text.putalpha(lettering)
        brand.alpha_composite(text, (184, 108 - lettering.height // 2))
        save_artwork(brand, PACKAGE / f"{style}_brand.png")
        save_artwork(
            overlay(style, framed=False), OUTPUT / "layers" / f"ky_header_{style}_brand.png"
        )
        for framed in (True, False):
            name = f"ky_header_{style}_{'framed' if framed else 'borderless'}_template.png"
            save_artwork(overlay(style, framed=framed), OUTPUT / "templates" / name)
            compact_name = name.replace("_template.png", "_compact_template.png")
            save_artwork(
                overlay(style, framed=framed, height=280), OUTPUT / "templates" / compact_name
            )
    for design, finish in FINISHES.items():
        stream = io.BytesIO()
        floral_background(finish, SIZE).save(stream, format="PNG")
        bg = stream.getvalue()
        for framed in (True, False):
            content = render_header(bg, finish, framed=framed)
            name = f"ky_header_{design}{'' if framed else '_borderless'}.png"
            write_artwork((OUTPUT / "designs" / name), content)
            write_artwork((PACKAGE / name), content)
            stream = io.BytesIO()
            floral_background(finish, (1600, 280)).save(stream, format="PNG")
            compact = render_header(stream.getvalue(), finish, framed=framed, height=280)
            compact_name = name.replace(".png", "_compact.png")
            write_artwork((OUTPUT / "designs" / compact_name), compact)
            write_artwork((PACKAGE / compact_name), compact)
    manifest = {
        "canvas": list(SIZE),
        "shape": "Wide rectangle with 8-pixel corner radius",
        "collection": "Illustrated KY BOT florals on matte charcoal",
        "brand": "KY BOT",
        "slogan": None,
        "brand_position": "Top left; icon before KY BOT",
        "brand_bounds": {"framed": [24, 24, 214, 81], "borderless": [24, 24, 214, 81]},
        "brand_plate": "Soft charcoal plate for all finishes; pale silver on dark silver",
        "brand_font": "Vonca-Medium.otf",
        "title_font": "Vonca-Bold.otf",
        "title_size": {"default": 108, "minimum": 40, "maximum_width": 1280},
        "height_range": [240, 800],
        "compact_height": 280,
        "designs": FINISHES,
        "styles": list(STYLES),
        "templates": ["framed", "borderless"],
        "background": "User supplied; cover crop with configurable focal point",
        "layers": ["background", "optional_frame", "top_left_brand"],
        "title_treatment": (
            "Horizontally and vertically centered VONCA Bold with matching metallic finish "
            "and a thin rule above and below; no title means no rules"
        ),
        "font_binaries_included": False,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(OUTPUT / "ky_header_templates.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(OUTPUT / "manifest.json", "manifest.json")
        for path in (OUTPUT / "templates").glob("*.png"):
            bundle.write(path, path.name)

    print(
        "Built standard and compact versions of three floral finishes and six reusable templates."
    )


if __name__ == "__main__":
    main()
