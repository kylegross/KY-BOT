"""Normalize approved raster masters without generating or replacing artwork."""

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "assets/branding/footers"
SOURCES = {
    "stone_minimal": (
        "e86c36da-f596-4bc1-8b79-bddc1a6d3742",
        (580, 1590, 329, 365),
        (70, 297, 267, 432),
        (1875, 2097),
    ),
    "floral_wreath": (
        "46e2fe68-ab62-4fb5-8a23-ade5b8205bb5",
        (630, 1543, 326, 362),
        (56, 282, 268, 432),
        (1893, 2110),
    ),
    "abstract_nature": (
        "f1de93b2-bd6c-4ace-85cb-4bc1841670f9",
        (608, 1592, 323, 357),
        (98, 345, 272, 427),
        (1827, 2070),
    ),
    "volcanic": (
        "efa0d7e2-e7af-4edd-a0c5-f348b3745f4d",
        (546, 1641, 326, 363),
        (112, 354, 276, 427),
        (1822, 2058),
    ),
    "botanical": (
        "c598ae36-154a-4028-ac5d-e34dfb86a839",
        (554, 1620, 338, 375),
        (126, 304, 285, 419),
        (1873, 2055),
    ),
}
W, H = 2176, 320
OUT = ROOT / "standardized"
OUT.mkdir(parents=True, exist_ok=True)
report = {}
for name, (uid, text, icon, right) in SOURCES.items():
    master = ROOT / "masters" / f"ky_footer_{name}_master.png"
    if not master.exists():
        shutil.copy2(
            Path("C:/Users/kylel/AppData/Local/Temp") / f"codex-clipboard-{uid}.png", master
        )
    im = Image.open(master).convert("RGBA")
    a = np.asarray(im).astype(np.float32)
    tx0, tx1, ty0, ty1 = text
    ix0, ix1, iy0, iy1 = icon
    # Common horizontal landmarks: icons and slogan retain their own artwork.
    dx = [16, 96, 316, 588, 1588, 1860, 2080, 2160]
    valid = np.where((a[210:485, :, 3] > 192).sum(axis=0) > 40)[0]
    sx = [int(valid[0]) + 2, ix0, ix1, tx0, tx1, right[0], right[1], int(valid[-1]) - 2]
    xx = np.interp(np.arange(W), dx, sx)
    sample_x = np.clip(np.round(xx).astype(int), 0, 2171)
    yy = np.empty((H, W), np.float32)
    mask = Image.new("L", (W * 4, H * 4))
    ImageDraw.Draw(mask).rounded_rectangle(
        (16 * 4, 24 * 4, 2160 * 4 - 1, 296 * 4 - 1), radius=136 * 4, fill=255
    )
    mask = np.asarray(mask.resize((W, H), Image.Resampling.LANCZOS)).astype(np.float32) / 255
    for x in range(W):
        col = a[195:496, sample_x[x], 3]
        hit = np.where(col > 192)[0]
        if len(hit) < 10:
            top, bot = 218, 475
        else:
            top, bot = np.quantile(hit, [0.01, 0.99]) + 195
        delta = max(152 - x, x - 2024, 0)
        extent = np.sqrt(max(136**2 - delta**2, 1))
        dt, db = 160 - extent, 160 + extent
        # Full-height mapping at rounded ends; feature alignment in the interior.
        if x < 96 or x > 2080 or db - dt < 200:
            yy[:, x] = np.interp(np.arange(H), [dt, db], [top, bot])
        else:
            blend = np.clip(min((x - 340) / 200, (1836 - x) / 200), 0, 1)
            st0 = iy0 * (1 - blend) + ty0 * blend
            st1 = iy1 * (1 - blend) + ty1 * blend
            d0 = 85 * (1 - blend) + 142 * blend
            d1 = 235 * (1 - blend) + 178 * blend
            feature = np.interp(
                np.arange(H),
                [dt, dt + 20, d0, d1, db - 20, db],
                [top, top + 20, st0, st1, bot - 20, bot],
            )
            base = np.interp(np.arange(H), [dt, db], [top, bot])
            weight = np.clip(min((x - 96) / 45, (2080 - x) / 45), 0, 1)
            yy[:, x] = base * (1 - weight) + feature * weight
    # Bilinear resampling in premultiplied alpha avoids dark edge fringes.
    a[:, :, :3] *= a[:, :, 3:4] / 255
    x0 = np.floor(xx).astype(int)
    x1 = np.minimum(x0 + 1, 2171)
    y0 = np.clip(np.floor(yy).astype(int), 0, 722)
    y1 = y0 + 1
    fx = (xx - x0)[None, :, None]
    fy = (yy - y0)[:, :, None]
    b = (a[y0, x0] * (1 - fx) + a[y0, x1] * fx) * (1 - fy) + (
        a[y1, x0] * (1 - fx) + a[y1, x1] * fx
    ) * fy
    b[:, :, :3] = np.divide(
        b[:, :, :3] * 255, b[:, :, 3:4], out=np.zeros_like(b[:, :, :3]), where=b[:, :, 3:4] > 0
    )
    b[:, :, 3] = 255 * mask
    b[mask == 0] = 0
    result = Image.fromarray(np.uint8(np.clip(b, 0, 255)))
    result.save(OUT / f"ky_footer_{name}.png")
    report[name] = {
        "canvas": [W, H],
        "alpha_bbox": result.getchannel("A").getbbox(),
        "source": master.name,
    }

order = ["volcanic", "botanical", "abstract_nature", "floral_wreath", "stone_minimal"]
sheet = Image.new("RGB", (1200, 1080), "#25282c")
d = ImageDraw.Draw(sheet)
font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 22)
d.text((28, 16), "KY BOT / FOOTER COMPARISON", font=font, fill="#e6d8b9")
for i, name in enumerate(order):
    y = 64 + i * 200
    d.text((28, y), name.replace("_", " ").title(), font=font, fill="#e6d8b9")
    preview = Image.open(OUT / f"ky_footer_{name}.png").resize(
        (1142, 168), Image.Resampling.LANCZOS
    )
    sheet.paste(preview, (29, y + 28), preview)
sheet.save(ROOT / "ky_footer_comparison.png")
(ROOT / "normalization.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
