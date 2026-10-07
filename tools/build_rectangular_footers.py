"""Refresh rectangular footer presets and reusable frameworks from approved backgrounds."""

import json
import sys
import zipfile
from pathlib import Path

from artwork_io import save_artwork

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ky_bot.services.collection import floral_background  # noqa: E402
from ky_bot.services.footers import (  # noqa: E402
    SIZE,
    STYLES,
    capsule,
    centered_overlay,
    overlay,
    rectangular_frame,
)

FINISHES = {style: style for style in STYLES}

BASE = ROOT / "assets/branding/footers"
FRAMEWORKS = BASE / "frameworks"
STANDARD = BASE / "standardized"


def centered(style, framed):
    return centered_overlay(style, SIZE, framed=framed)


def main():
    for style in STYLES:
        save_artwork(rectangular_frame(style, SIZE), FRAMEWORKS / f"ky_framework_{style}_frame.png")
        save_artwork(centered(style, False), FRAMEWORKS / f"ky_framework_{style}.png")
        save_artwork(overlay(style), FRAMEWORKS / f"ky_framework_{style}_custom_slogan.png")
        save_artwork(
            rectangular_frame(style, SIZE), ROOT / f"src/ky_bot/assets/footer/{style}_frame.png"
        )
        for framed in (True, False):
            suffix = "framed" if framed else "borderless"
            save_artwork(
                centered(style, framed), FRAMEWORKS / f"ky_framework_{style}_centered_{suffix}.png"
            )
    save_artwork(capsule(), FRAMEWORKS / "ky_framework_background_mask.png")
    save_artwork(capsule(), FRAMEWORKS / "ky_framework_image_only_mask.png")
    for name, style in FINISHES.items():
        bg = floral_background(style, SIZE)
        bg.putalpha(capsule())
        save_artwork(bg, STANDARD / f"ky_footer_{name}_image_only.png")
        for framed in (True, False):
            result = bg.copy()
            result.alpha_composite(centered(style, framed))
            suffix = "framed" if framed else "borderless"
            save_artwork(result, STANDARD / f"ky_footer_{name}_centered_{suffix}.png")
            save_artwork(result, ROOT / f"src/ky_bot/assets/footer/ky_footer_{name}_{suffix}.png")
        compact = floral_background(style, (2176, 210))
        compact.alpha_composite(centered_overlay(style, compact.size))
        save_artwork(compact, ROOT / f"src/ky_bot/assets/footer/ky_footer_{name}_compact.png")
        save_artwork(compact, STANDARD / f"ky_footer_{name}_compact.png")
    data = {
        "collection": "Illustrated KY BOT florals on matte charcoal",
        "finishes": list(STYLES),
        "canvas": list(SIZE),
        "compact_height": 210,
        "shape": "Wide rectangle with subtle 8-pixel corners",
        "default": "Borderless with a small centered KY BOT botanical icon",
        "background": "Upload your own still PNG, JPEG or WebP beneath the transparent template",
        "crop": "Cover crop across full rectangular canvas",
        "optional": "Thin outline; custom slogan and left/right icons via /footer",
    }
    for directory in (FRAMEWORKS, STANDARD):
        (directory / "manifest.json").write_text(json.dumps(data, indent=2) + "\n")
    for archive, directory in [
        ("ky_footer_frameworks.zip", FRAMEWORKS),
        ("ky_footer_library.zip", STANDARD),
    ]:
        with zipfile.ZipFile(BASE / archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in directory.iterdir():
                if path.suffix in {".png", ".json"}:
                    bundle.write(path, path.name)


if __name__ == "__main__":
    main()
