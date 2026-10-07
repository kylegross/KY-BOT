"""Layered footer rendering; uploaded images are processed in memory only."""

import io
import warnings
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageOps

from ky_bot.services.typography import TAGLINE, TypographyError, text_mask

SIZE = (2176, 320)
SLOGAN = TAGLINE
STYLES = ("silver_neon", "gold", "dark_silver")
MAX_UPLOAD = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000


class FooterError(ValueError):
    """An actionable, safe-to-display input error."""


def read_upload(data: bytes, *, icon: bool = False) -> Image.Image:
    if not data or len(data) > MAX_UPLOAD:
        raise FooterError("Use an image under 8 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise FooterError("Use a PNG, JPEG, or WebP image.")
                if source.width * source.height > MAX_PIXELS or max(source.size) > 8192:
                    raise FooterError("Use an image up to 16 megapixels and 8192 pixels per side.")
                if getattr(source, "n_frames", 1) != 1:
                    raise FooterError("Use a still image rather than an animated image.")
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.load()
    except FooterError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise FooterError("That image could not be opened. Try a PNG, JPEG, or WebP.") from None
    if icon:
        alpha = image.getchannel("A")
        if alpha.getextrema()[0] == 255:
            raise FooterError("For an icon, upload a PNG or WebP with a transparent background.")
        bounds = alpha.getbbox()
        if bounds is None:
            raise FooterError("That icon is completely transparent.")
        image = image.crop(bounds)
    return image


def asset(name: str) -> Image.Image:
    with files("ky_bot").joinpath("assets", "footer", name).open("rb") as stream:
        return Image.open(stream).copy()


def capsule() -> Image.Image:
    mask = Image.new("L", (SIZE[0] * 3, SIZE[1] * 3))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, SIZE[0] * 3 - 1, SIZE[1] * 3 - 1), radius=24, fill=255
    )
    return mask.resize(SIZE, Image.Resampling.LANCZOS)


def rectangular_frame(
    style: str, size: tuple[int, int], *, radius: int = 8, inset: int = 0
) -> Image.Image:
    """A quiet, proportionate outline that matches the illustrated floral collection."""
    from ky_bot.services.collection import COLOURS

    if style not in STYLES:
        raise FooterError("Choose silver neon, gold, or dark silver.")
    frame = Image.new("RGBA", size)
    width, height = size
    ImageDraw.Draw(frame).rounded_rectangle(
        (inset, inset, width - 1 - inset, height - 1 - inset),
        radius=radius,
        outline=COLOURS[style],
        width=3,
    )
    return frame


def metal(mask: Image.Image, style: str, *, sheen: float = 1.0) -> Image.Image:
    """Tint alpha artwork consistently, with a restrained optional dual-color halo."""
    palettes = {
        "gold": ("#704009", "#e9b64e", "#fff4cf"),
        "dark_silver": ("#030303", "#22201e", "#dededc"),
        "silver_neon": ("#677b99", "#d5e1f0", "#ffffff"),
    }
    low, mid, high = palettes[style]
    high = tuple(
        round(base + (highlight - base) * sheen)
        for base, highlight in zip(ImageColor.getrgb(mid), ImageColor.getrgb(high))
    )
    h = mask.height
    ramp = Image.new("L", (1, h))
    anchors = [(0, 100), (0.25, 205), (0.43, 255), (0.51, 100), (0.68, 195), (1, 80)]
    values = []
    for y in range(h):
        f = y / max(1, h - 1)
        for (a, av), (b, bv) in zip(anchors, anchors[1:]):
            if a <= f <= b:
                values.append(round(av + (bv - av) * (f - a) / (b - a)))
                break
    ramp.putdata(values)
    colored = ImageOps.colorize(ramp.resize(mask.size), low, high, mid=mid).convert("RGBA")
    if style == "silver_neon":
        stops = [
            (255, 184, 221),
            (206, 184, 255),
            (165, 217, 255),
            (172, 255, 226),
            (255, 236, 172),
            (255, 184, 221),
        ]
        tint = Image.new("RGBA", (mask.width, 1))
        values = []
        for x in range(mask.width):
            position = x / max(1, mask.width - 1) * (len(stops) - 1)
            index = min(int(position), len(stops) - 2)
            fraction = position - index
            values.append(
                tuple(round(a + (b - a) * fraction) for a, b in zip(stops[index], stops[index + 1]))
                + (255,)
            )
        tint.putdata(values)
        colored = Image.blend(colored, tint.resize(mask.size), 0.2)
    colored.putalpha(mask)
    result = Image.new("RGBA", mask.size)
    if style == "dark_silver":
        bevel = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask)
        edge = Image.new("RGBA", mask.size, "#c7d2df")
        edge.putalpha(bevel.point(lambda x: x * 0.65 * sheen))
        result.alpha_composite(edge)
    result.alpha_composite(colored)
    return result


def slogan_mask(text: str, font_path: Path | None) -> Image.Image:
    if text == SLOGAN:
        return asset("slogan.png").convert("L")
    try:
        return text_mask(text, font_path=font_path)
    except TypographyError as exc:
        raise FooterError(str(exc)) from None


def overlay(
    style: str,
    *,
    slogan: str = SLOGAN,
    left_icon: bytes | None = None,
    right_icon: bytes | None = None,
    icon_scale: int = 100,
    font_path: Path | None = None,
) -> Image.Image:
    if style not in STYLES:
        raise FooterError("Choose silver neon, gold, or dark silver.")
    if not 60 <= icon_scale <= 120:
        raise FooterError("Icon size must be between 60 and 120 percent.")
    result = rectangular_frame(style, SIZE)
    for data, cx in ((left_icon, 206), (right_icon, 1970)):
        if data is None:
            icon = asset(f"{style}_icon.png").convert("RGBA")
            icon = ImageOps.contain(
                icon,
                (round(220 * icon_scale / 100), round(150 * icon_scale / 100)),
                Image.Resampling.LANCZOS,
            )
            result.alpha_composite(icon, (cx - icon.width // 2, 160 - icon.height // 2))
            continue
        mask = read_upload(data, icon=True).getchannel("A")
        limit = (round(220 * icon_scale / 100), round(150 * icon_scale / 100))
        mask = ImageOps.contain(mask, limit, Image.Resampling.LANCZOS)
        layer = Image.new("L", SIZE)
        layer.paste(mask, (cx - mask.width // 2, 160 - mask.height // 2))
        result.alpha_composite(metal(layer, style))
    if slogan == SLOGAN:
        result.alpha_composite(asset(f"{style}_slogan.png").convert("RGBA"))
        return result
    mask = slogan_mask(slogan, font_path)
    layer = Image.new("L", SIZE)
    layer.paste(mask, ((SIZE[0] - mask.width) // 2, (SIZE[1] - mask.height) // 2))
    result.alpha_composite(metal(layer, style))
    return result


def centered_overlay(
    style: str, size: tuple[int, int], *, framed: bool = False, icon_scale: int = 100
) -> Image.Image:
    """Small fixed botanical icon for the new minimal footer template."""
    from ky_bot.services.collection import COLOURS

    if style not in STYLES or not 60 <= icon_scale <= 120:
        raise FooterError("Choose a finish and an icon size from 60 to 120 percent.")
    result = rectangular_frame(style, size) if framed else Image.new("RGBA", size)
    icon = asset(f"{style}_icon.png").convert("RGBA")
    icon = icon.crop(icon.getchannel("A").getbbox())
    limit = round(size[1] * 0.58 * icon_scale / 100)
    mask = ImageOps.contain(icon.getchannel("A"), (limit, limit), Image.Resampling.LANCZOS)
    tinted = Image.new("RGBA", mask.size, COLOURS[style])
    tinted.putalpha(mask)
    result.alpha_composite(tinted, ((size[0] - mask.width) // 2, (size[1] - mask.height) // 2))
    return result


def render_footer(
    background: bytes,
    style: str,
    *,
    crop_x: int = 50,
    crop_y: int = 50,
    dim: int = 20,
    centered: bool = False,
    framed: bool = False,
    **branding,
) -> bytes:
    if not all(0 <= value <= 100 for value in (crop_x, crop_y)) or not 0 <= dim <= 80:
        raise FooterError("Crop positions must be 0–100 and dimming must be 0–80.")
    image = read_upload(background)
    # Fill the rectangle to its edges so artwork and text share a consistent left edge.
    image = ImageOps.fit(
        image, SIZE, Image.Resampling.LANCZOS, centering=(crop_x / 100, crop_y / 100)
    )
    image.alpha_composite(Image.new("RGBA", image.size, (0, 0, 0, round(255 * dim / 100))))
    result = Image.new("RGBA", SIZE)
    result.alpha_composite(image, (0, 0))
    result.putalpha(ImageChops.multiply(result.getchannel("A"), capsule()))
    result.alpha_composite(
        centered_overlay(style, SIZE, framed=framed, icon_scale=branding.get("icon_scale", 100))
        if centered
        else overlay(style, **branding)
    )
    output = io.BytesIO()
    result.save(output, format="PNG")
    return output.getvalue()
