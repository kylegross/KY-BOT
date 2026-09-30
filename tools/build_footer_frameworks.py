"""Extract reusable approved artwork and export three editable footer frameworks."""

import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ky_bot.services.footers import (  # noqa: E402 -- allow running the tool from a source checkout
    SIZE,
    STYLES,
    capsule,
    metal,
    overlay,
    render_footer,
)

PACKAGE = ROOT / "src/ky_bot/assets/footer"
PACKAGE.mkdir(parents=True, exist_ok=True)
OUTPUT = ROOT / "assets/branding/footers/frameworks"
OUTPUT.mkdir(parents=True, exist_ok=True)
BASE = ROOT / "assets/branding/footers"
source = Image.open(BASE / "standardized/ky_footer_abstract_nature.png").convert("RGBA")


def gold_mask(image):
    a = np.asarray(image).astype(float)
    # Gold foreground against the near-black source background; keep antialiasing.
    signal = np.maximum(a[:, :, 0] - a[:, :, 2] * 0.6 - 22, 0)
    return Image.fromarray(np.uint8(np.clip(signal * 1.65, 0, 255)))


icon = gold_mask(source.crop((96, 85, 316, 235)))
isolation = Image.new("L", icon.size)
pen = ImageDraw.Draw(isolation)
pen.polygon(
    [
        (110, 0),
        (136, 37),
        (129, 60),
        (119, 74),
        (119, 93),
        (146, 58),
        (177, 48),
        (160, 82),
        (132, 97),
        (119, 109),
        (132, 124),
        (119, 133),
        (110, 150),
        (100, 134),
        (86, 124),
        (103, 109),
        (94, 98),
        (67, 91),
        (49, 48),
        (79, 53),
        (101, 75),
        (103, 93),
        (104, 72),
        (91, 57),
        (89, 34),
    ],
    fill=255,
)
for x, y in [(23, 80), (201, 78)]:
    pen.polygon(
        [
            (x, y - 29),
            (x + 7, y - 9),
            (x + 21, y),
            (x + 7, y + 8),
            (x, y + 30),
            (x - 7, y + 8),
            (x - 21, y),
            (x - 7, y - 8),
        ],
        fill=255,
    )
ImageChops.multiply(icon, isolation).save(PACKAGE / "icon.png")
gold_mask(source.crop((588, 142, 1588, 178))).save(PACKAGE / "slogan.png")
# Preserve the original braided border texture as an independent layer.
ring = capsule()
inner = Image.new("L", SIZE)
ImageDraw.Draw(inner).rounded_rectangle((34, 43, 2141, 276), radius=118, fill=255)
ring = ImageChops.subtract(ring, inner.filter(ImageFilter.GaussianBlur(0.7)))
border = gold_mask(source)
border = ImageChops.multiply(border, ring)
for style in STYLES:
    obsolete_swatch = OUTPUT / f"ky_framework_{style}_icons.png"
    if obsolete_swatch.exists():
        obsolete_swatch.unlink()
    metal(border, style).save(PACKAGE / f"{style}_frame.png")
    metal(border, style).save(OUTPUT / f"ky_framework_{style}_frame.png")
    overlay(style).save(OUTPUT / f"ky_framework_{style}.png")
    for label, mask_name, position in [
        ("left_icon", "icon.png", (96, 85)),
        ("right_icon", "icon.png", (1860, 85)),
        ("slogan", "slogan.png", (588, 142)),
    ]:
        layer = Image.new("L", SIZE)
        layer.paste(Image.open(PACKAGE / mask_name).convert("L"), position)
        metal(layer, style).save(OUTPUT / f"ky_framework_{style}_{label}.png")

# The uploaded stone reference becomes the texture, using mirrored repeats to avoid seams.
reference_path = BASE / "masters/volcanic_stone_reference.png"
if not reference_path.exists():
    Image.open(
        "C:/Users/kylel/AppData/Local/Temp/codex-clipboard-888abc44-23d5-46e6-ae48-6f47f487d915.png"
    ).save(reference_path)
reference = Image.open(reference_path).convert("RGB")
tile = reference.crop((0, 0, 194, 170)).resize((388, 340), Image.Resampling.LANCZOS)
texture = Image.new("RGBA", SIZE)
for i, x in enumerate(range(0, SIZE[0], tile.width)):
    texture.paste(ImageOps.mirror(tile) if i % 2 else tile, (x, 0))
shade = Image.new("L", SIZE, 45)
ImageDraw.Draw(shade).rounded_rectangle((450, 110, 1730, 210), radius=50, fill=170)
shade = shade.filter(ImageFilter.GaussianBlur(40))
dark = Image.new("RGBA", SIZE, (0, 0, 0, 0))
dark.putalpha(shade)
texture.alpha_composite(dark)
texture.putalpha(capsule())
archive = BASE / "masters/ky_footer_volcanic_before_stone_update.png"
old = Image.open(
    archive if archive.exists() else BASE / "standardized/ky_footer_volcanic.png"
).convert("RGBA")
if not archive.exists():
    old.save(archive)
foreground = Image.new("L", SIZE)
for cx in [206, 1970]:
    foreground.paste(
        ImageChops.multiply(
            gold_mask(old.crop((cx - 110, 85, cx + 110, 235))),
            isolation.filter(ImageFilter.MaxFilter(13)),
        ),
        (cx - 110, 85),
    )
foreground.paste(gold_mask(old.crop((580, 140, 1604, 181))), (580, 140))
foreground = ImageChops.lighter(foreground, ring)
old.putalpha(ImageChops.multiply(old.getchannel("A"), foreground))
texture.alpha_composite(old)
texture.putalpha(capsule())
pixels = np.array(texture)
pixels[pixels[:, :, 3] == 0] = 0
texture = Image.fromarray(pixels)
texture.save(BASE / "standardized/ky_footer_volcanic.png")

# Same demonstration background for all three styles, plus the empty framework overlays.
demo = Image.new("RGB", SIZE)
draw = ImageDraw.Draw(demo)
for x in range(SIZE[0]):
    t = x / (SIZE[0] - 1)
    draw.line((x, 0, x, SIZE[1]), fill=(int(26 + 65 * t), int(40 + 25 * t), int(65 + 40 * t)))
bio = io.BytesIO()
demo.save(bio, format="PNG")
sheet = Image.new("RGB", (1200, 910), "#24272c")
d = ImageDraw.Draw(sheet)
font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 23)
d.text((28, 18), "KY BOT / CUSTOM BACKGROUND FRAMEWORKS", font=font, fill="#e8eaf1")
d.text(
    (28, 54),
    "Separate frame, slogan and icon layers • backgrounds remain replaceable",
    font=font,
    fill="#aeb8c6",
)
for i, style in enumerate(STYLES):
    y = 108 + i * 256
    d.text((28, y), style.replace("_", " ").title(), font=font, fill="#e8eaf1")
    if style == "dark_silver":
        light = ImageOps.colorize(demo.convert("L"), "#d0cbd1", "#f4e9e0")
        light_bytes = io.BytesIO()
        light.save(light_bytes, format="PNG")
        background = light_bytes.getvalue()
    else:
        background = bio.getvalue()
    content = render_footer(background, style, dim=0)
    (OUTPUT / f"ky_framework_{style}_example.png").write_bytes(content)
    im = Image.open(io.BytesIO(content)).resize((1142, 168), Image.Resampling.LANCZOS)
    sheet.paste(im, (29, y + 40), im)
sheet.save(OUTPUT / "ky_framework_comparison.png")
manifest = {
    "canvas": list(SIZE),
    "styles": list(STYLES),
    "layers": ["background", "frame", "left_icon", "right_icon", "slogan"],
    "background_crop": "cover; focal position 0–100 on each axis",
    "icon_centers": [[206, 160], [1970, 160]],
    "slogan_center": [1088, 160],
    "custom_font": "Set FOOTER_FONT_PATH to a licensed Vonca OTF/TTF",
    "icon_uploads": "Transparent PNG/WebP; alpha silhouette tinted to match frame",
    "default_slogan": "POWERFUL BY NATURE. SIMPLE BY DESIGN.",
}
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
with zipfile.ZipFile(OUTPUT / "ky_footer_frameworks.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in OUTPUT.iterdir():
        if p.suffix in {".png", ".json"}:
            z.write(p, p.name)
print("Exported three layered frameworks and updated volcanic stone.")

# Refresh the existing five-footer comparison and library to include the revised volcanic asset.
order = ["volcanic", "botanical", "abstract_nature", "floral_wreath", "stone_minimal"]
comparison = Image.new("RGB", (1200, 1080), "#25282c")
draw = ImageDraw.Draw(comparison)
draw.text((28, 16), "KY BOT / FOOTER COMPARISON", font=font, fill="#e6d8b9")
for i, name in enumerate(order):
    y = 64 + i * 200
    draw.text((28, y), name.replace("_", " ").title(), font=font, fill="#e6d8b9")
    preview = Image.open(BASE / f"standardized/ky_footer_{name}.png").resize(
        (1142, 168), Image.Resampling.LANCZOS
    )
    comparison.paste(preview, (29, y + 28), preview)
comparison.save(BASE / "ky_footer_comparison.png")
with zipfile.ZipFile(BASE / "ky_footer_library.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for name in order:
        z.write(BASE / f"standardized/ky_footer_{name}.png", f"ky_footer_{name}.png")
    z.write(BASE / "ky_footer_comparison.png", "ky_footer_comparison.png")
