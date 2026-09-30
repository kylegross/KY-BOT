"""Review-only artwork. Does not modify packaged or active footer assets."""

import hashlib
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]
ACTIVE = ROOT / "src/ky_bot/assets/footer"
FRAMEWORKS = ROOT / "assets/branding/footers/frameworks"
SIZE = (2176, 320)


def hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in (ACTIVE, FRAMEWORKS)
            for p in folder.glob("*") if p.is_file()}


def finish(image, style):
    if style == "gold":
        return image.copy()
    a = np.array(image.convert("RGBA"))
    rgb = a[:, :, :3].astype(np.float32) / 255
    lum = .5 * rgb.max(2) + .5 * (rgb @ np.array([.2126, .7152, .0722]))
    if style == "dark_silver":
        reference = np.asarray(Image.open(
            ROOT / "assets/branding/footers/standardized/ky_footer_stone_minimal.png"
        ).crop((96, 85, 316, 235)))[:, :, :3].astype(float)
        neutral = (reference.max(2)-reference.min(2)<18) & (reference.mean(2)<210)
        metal_pixels = reference[neutral] / 255
        percentiles = np.array([0,.1,.25,.5,.75,.9,.97,.99,.997,1])
        source_levels = np.quantile(lum[a[:,:,3]>220],percentiles)
        target = np.quantile(metal_pixels,percentiles,axis=0)
        target[-2] = [.87,.88,.88]
        target[-1] = [.98,.98,.97]
        colors = np.stack([np.interp(lum,source_levels,target[:,c]) for c in range(3)],axis=-1)
    else:
        y, x = np.mgrid[:a.shape[0], :a.shape[1]]
        t = (x / a.shape[1] * .82 + y / a.shape[0] * .3) % 1
        stops = np.array([[1,.68,.86],[.8,.72,1],[.62,.85,1],[.65,1,.92],
                          [1,.91,.64],[1,.72,.85]], dtype=float)
        tint = np.stack([np.interp(t, np.linspace(0,1,len(stops)), stops[:,c])
                         for c in range(3)], axis=-1)
        value = np.clip(lum ** .78 * 1.09, 0, 1)
        # Keep specular highlights silver-white; let middle tones carry the rainbow.
        strength = .72 * (1 - np.clip((value - .8) / .2, 0, 1))
        colors = value[:, :, None] * (1 - strength[:, :, None] * (1 - tint))
    a[:, :, :3] = np.uint8(np.clip(colors * 255, 0, 255))
    a[a[:, :, 3] == 0] = 0
    return Image.fromarray(a)


before = hashes()
original = OUT / "ky_icon_gold_reference.png"
if not original.exists():
    shutil.copy2(
        "C:/Users/kylel/AppData/Local/Temp/codex-clipboard-746ad7c0-8b93-498e-b894-6c49ae83f1c0.png",
        original,
    )
gold = Image.open(original).convert("RGBA")
font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 27)
small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 21)
styles = [("gold", "Original gold"), ("dark_silver", "Dark silver / black metallic"),
          ("rainbow_silver", "Iridescent silver")]
icons = Image.new("RGB", (1500, 530), "#25282e")
draw = ImageDraw.Draw(icons)
draw.text((30, 18), "KY BOT  /  EXACT ICON · THREE FINISHES", font=font, fill="#f0f0f3")
draw.text((30, 60), "Preview only · original shape and leaf detail preserved", font=small, fill="#b9bec8")
footers = Image.new("RGB", (1400, 820), "#25282e")
fd = ImageDraw.Draw(footers)
fd.text((28, 20), "KY BOT  /  UPDATED CUSTOMIZABLE FRAMEWORKS", font=font, fill="#f0f0f3")
fd.text((28, 63), "Preview only · sample backgrounds are replaceable", font=small, fill="#b9bec8")
for i, (style, label) in enumerate(styles):
    icon = finish(gold, style)
    icon.save(OUT / f"ky_icon_{style}.png")
    assert np.array_equal(np.array(icon)[:, :, 3], np.array(gold)[:, :, 3])
    # Neutral cards make each finish visible without baking a background into the PNG.
    card = Image.new("RGBA", (470, 380), "#cdd0d6" if style == "dark_silver" else "#15191f")
    bounds = gold.getchannel("A").point(lambda a: 255 if a > 30 else 0).getbbox()
    display = ImageOps.contain(icon.crop(bounds), (430, 305), Image.Resampling.LANCZOS)
    card.alpha_composite(display, ((470-display.width)//2, 8))
    ImageDraw.Draw(card).text((20, 330), label, font=small,
                              fill="#20252e" if style == "dark_silver" else "#e3e6ed")
    icons.paste(card.convert("RGB"), (15+i*495, 123))
    frame = finish(Image.open(FRAMEWORKS / "ky_framework_gold_frame.png"), style)
    slogan = finish(Image.open(FRAMEWORKS / "ky_framework_gold_slogan.png"), style)
    if style == "dark_silver":
        # Reuse the approved black-metal border itself, rather than approximating its color.
        frame = Image.open(ROOT / "assets/branding/footers/standardized/ky_footer_stone_minimal.png").convert("RGBA")
        inner = Image.new("L",SIZE)
        ImageDraw.Draw(inner).rounded_rectangle((34,43,2141,276),radius=118,fill=255)
        frame.putalpha(ImageChops.subtract(frame.getchannel("A"),inner))
        text_alpha = slogan.getchannel("A")
        slogan = Image.new("RGBA",SIZE,(25,24,23,0))
        slogan.putalpha(text_alpha)
    frame.save(OUT / f"ky_framework_{style}_frame.png")
    layer = frame.copy()
    layer.alpha_composite(slogan)
    icon_small = ImageOps.contain(icon.crop(bounds), (220, 150), Image.Resampling.LANCZOS)
    for center in (206, 1970):
        layer.alpha_composite(icon_small, (center-icon_small.width//2, 160-icon_small.height//2))
    layer.save(OUT / f"ky_framework_{style}.png")
    mask = Image.new("L", (SIZE[0]*3,SIZE[1]*3))
    ImageDraw.Draw(mask).rounded_rectangle((48,72,6479,887),radius=408,fill=255)
    mask = mask.resize(SIZE,Image.Resampling.LANCZOS)
    sample = Image.new("RGBA", SIZE, "#d8d3d0" if style=="dark_silver" else "#202735")
    sample.putalpha(mask)
    sample.alpha_composite(layer)
    sample.save(OUT / f"ky_framework_{style}_example.png")
    y=118+i*228
    fd.text((28,y),label,font=small,fill="#e3e6ed")
    rendered=sample.resize((1340,197),Image.Resampling.LANCZOS)
    footers.paste(rendered,(30,y+24),rendered)
icons.save(OUT / "ky_icon_finishes_review.png")
footers.save(OUT / "ky_frameworks_review.png")
assert before == hashes(), "Active assets must remain unchanged."
print("Review images exported; active assets unchanged; all icon alpha channels preserved exactly.")
