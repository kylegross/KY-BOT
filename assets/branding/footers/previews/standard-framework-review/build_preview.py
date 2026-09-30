"""Preview approved framework over the five backgrounds; never writes active assets."""

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]
BASE = ROOT / "assets/branding/footers"
APPROVED = BASE / "previews/icon-review"
SIZE = (2176, 320)
VARIANTS = [
    ("volcanic", "gold", "Volcanic · Original gold"),
    ("botanical", "gold", "Botanical · Original gold"),
    ("abstract_nature", "rainbow_silver", "Abstract Nature · Iridescent silver"),
    ("floral_wreath", "dark_silver", "Floral Wreath · Black metallic"),
    ("stone_minimal", "dark_silver", "Stone Minimal · Black metallic"),
]


def hashes():
    folders = [BASE / "standardized", BASE / "frameworks", ROOT / "src/ky_bot/assets/footer"]
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in folders
        for p in folder.glob("*")
        if p.is_file()
    }


def patch(image, box, donor):
    # Clone nearby source texture with soft seams to clear the old baked-in icon.
    x0, y0, x1, y1 = box
    replacement = image.crop(donor).resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    mask = Image.new("L", replacement.size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (8, 8, mask.width - 9, mask.height - 9), radius=18, fill=255
    )
    mask = mask.filter(ImageFilter.GaussianBlur(5))
    image.paste(replacement, (x0, y0), mask)


before = hashes()
sheet = Image.new("RGB", (1500, 1350), "#25282e")
draw = ImageDraw.Draw(sheet)
font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 28)
small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 23)
draw.text(
    (30, 20), "KY BOT / FIVE STANDARD FOOTERS · APPROVED FRAMEWORK", font=font, fill="#eef0f4"
)
draw.text(
    (30, 65), "Preview only — existing footer files remain unchanged", font=small, fill="#b8bec9"
)
for index, (name, style, label) in enumerate(VARIANTS):
    archived = BASE / f"masters/before-approved-framework/ky_footer_{name}.png"
    source = Image.open(
        archived if archived.exists() else BASE / f"standardized/ky_footer_{name}.png"
    ).convert("RGBA")
    bg = source.copy()
    patch(bg, (76, 65, 336, 253), (340, 65, 565, 253))
    patch(bg, (1836, 65, 2100, 253), (1612, 65, 1834, 253))
    # Replace the old slogan with texture interpolated from directly above and below it.
    pixels = np.array(bg)
    x0, x1, y0, y1 = 565, 1610, 127, 194
    upper = pixels[120:127, x0:x1, :3].mean(axis=0)
    lower = pixels[194:201, x0:x1, :3].mean(axis=0)
    t = np.linspace(0, 1, y1 - y0)[:, None, None]
    fill = np.uint8(upper[None, :, :] * (1 - t) + lower[None, :, :] * t)
    replacement = Image.fromarray(fill).convert("RGBA")
    mask = Image.new("L", replacement.size)
    ImageDraw.Draw(mask).rectangle((8, 8, mask.width - 9, mask.height - 9), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    bg.paste(replacement, (x0, y0), mask)
    # Remove the previous perimeter; retain the source interior artwork.
    interior = bg.crop((36, 45, 2140, 275)).resize((2144, 272), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", SIZE)
    result.paste(interior, (16, 24))
    if name == "volcanic":
        # Transfer the reference's real gold ribbons and flecks onto the stone.
        banner = Image.open(
            ROOT / "assets/branding/footers/masters/volcanic_gold_banner_reference.png"
        ).convert("RGBA")
        gold = banner.crop((530, 300, 980, 572))
        g = np.array(gold).astype(float)
        r, green, b = g[:, :, 0], g[:, :, 1], g[:, :, 2]
        selected = (r > 1.8 * b) & (green > 0.42 * r) & (green < 0.89 * r) & (r > 65)
        opacity = Image.fromarray(np.uint8(selected * 215)).filter(ImageFilter.GaussianBlur(0.5))
        gold.putalpha(opacity)
        result.alpha_composite(gold, (315, 24))
        result.alpha_composite(ImageOps.mirror(gold), (1411, 24))
    alpha = Image.new("L", (SIZE[0] * 3, SIZE[1] * 3))
    ImageDraw.Draw(alpha).rounded_rectangle((48, 72, 6479, 887), radius=408, fill=255)
    result.putalpha(alpha.resize(SIZE, Image.Resampling.LANCZOS))
    result.alpha_composite(Image.open(APPROVED / f"ky_framework_{style}.png").convert("RGBA"))
    a = np.array(result)
    a[a[:, :, 3] == 0] = 0
    result = Image.fromarray(a)
    result.save(OUT / f"ky_footer_{name}_preview.png")
    y = 122 + index * 240
    draw.text((30, y), label, font=small, fill="#eef0f4")
    thumb = result.resize((1440, 212), Image.Resampling.LANCZOS)
    sheet.paste(thumb, (30, y + 27), thumb)
sheet.save(OUT / "ky_standard_frameworks_review.png")
assert before == hashes(), "Active footer files must stay unchanged."
print("Five previews saved; existing footer files and runtime assets verified unchanged.")
