"""Approved illustrated floral collection, with proportional end artwork at any size."""

from importlib.resources import files

from PIL import Image, ImageOps

COLOURS = {"dark_silver": "#BDC3CA", "gold": "#E8C47C", "silver_neon": "#D6E2F3"}


def floral_background(style: str, size: tuple[int, int]) -> Image.Image:
    if style not in COLOURS:
        raise ValueError("Choose dark silver, gold, or silver neon.")
    with (
        files("ky_bot")
        .joinpath("assets", "header", f"floral_{style}_background.png")
        .open("rb") as stream
    ):
        source = Image.open(stream).convert("RGBA")
    third = source.width // 3
    centre = source.crop((third, 0, source.width - third, source.height))
    result = ImageOps.fit(centre, size, Image.Resampling.LANCZOS)
    # Recompose the quiet centre instead of stretching flowers or cropping them out.
    limit = min(size[1], size[0] // 3)
    for bounds, right in [
        ((0, 0, third, source.height), False),
        ((source.width - third, 0, source.width, source.height), True),
    ]:
        end = ImageOps.contain(source.crop(bounds), (limit, size[1]), Image.Resampling.LANCZOS)
        result.alpha_composite(
            end, (size[0] - end.width if right else 0, (size[1] - end.height) // 2)
        )
    return result
