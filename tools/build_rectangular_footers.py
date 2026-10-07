"""Refresh rectangular footer presets and reusable frameworks from approved backgrounds."""

import json
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ky_bot.services.footers import (  # noqa: E402
    SIZE,
    STYLES,
    asset,
    capsule,
    overlay,
    rectangular_frame,
)

FINISHES = {
    "volcanic": "gold",
    "botanical": "gold",
    "abstract_nature": "silver_neon",
    "floral_wreath": "dark_silver",
    "stone_minimal": "dark_silver",
}
BASE = ROOT / "assets/branding/footers"
FRAMEWORKS = BASE / "frameworks"
STANDARD = BASE / "standardized"


def centered(style, framed):
    image = rectangular_frame(style, SIZE) if framed else Image.new("RGBA", SIZE)
    icon = ImageOps.contain(asset(f"{style}_icon.png").convert("RGBA"), (220, 150))
    image.alpha_composite(icon, ((SIZE[0] - icon.width) // 2, (SIZE[1] - icon.height) // 2))
    return image


def main():
    for style in STYLES:
        rectangular_frame(style, SIZE).save(FRAMEWORKS / f"ky_framework_{style}_frame.png")
        overlay(style).save(FRAMEWORKS / f"ky_framework_{style}.png")
        for framed in (True, False):
            suffix = "framed" if framed else "borderless"
            centered(style, framed).save(FRAMEWORKS / f"ky_framework_{style}_centered_{suffix}.png")
    capsule().save(FRAMEWORKS / "ky_framework_background_mask.png")
    capsule().save(FRAMEWORKS / "ky_framework_image_only_mask.png")
    for name, style in FINISHES.items():
        bg = ImageOps.fit(
            Image.open(BASE / f"masters/approved-materials/{name}_background.png").convert("RGBA"),
            SIZE,
            Image.Resampling.LANCZOS,
        )
        bg.putalpha(capsule())
        bg.save(STANDARD / f"ky_footer_{name}.png")
        for framed in (True, False):
            result = bg.copy()
            result.alpha_composite(centered(style, framed))
            suffix = "framed" if framed else "borderless"
            result.save(STANDARD / f"ky_footer_{name}_centered_{suffix}.png")
    for path in FRAMEWORKS.glob("*manifest.json"):
        data = json.loads(path.read_text())
        data["shape"] = "Wide rectangle with subtle 8-pixel corners"
        if "crop" in data:
            data["crop"] = "Cover crop across full rectangular canvas"
        path.write_text(json.dumps(data, indent=2) + "\n")
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
