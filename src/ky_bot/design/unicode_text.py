"""Grapheme-aware names and color emoji, adapted from the VICE card renderer."""

import logging
import unicodedata
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from struct import error as StructError
from threading import RLock

import regex
import uharfbuzz as hb
from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont

_emoji_lock = RLock()


@lru_cache(maxsize=2)
def emoji_source(path):
    return TTFont(path), hb.Face(Path(path).read_bytes())


@lru_cache(maxsize=256)
def emoji_bitmap(path, value, size):
    """Shape joined emoji then read the font's PNG: works even without Pillow RAQM."""
    if Path(path).name != "NotoColorEmoji.ttf":
        return None
    with _emoji_lock:
        source, face = emoji_source(path)
        buffer = hb.Buffer()
        buffer.add_str(value)
        buffer.guess_segment_properties()
        hb.shape(hb.Font(face), buffer)
        if len(buffer.glyph_infos) != 1:
            return None
        name = source.getGlyphName(buffer.glyph_infos[0].codepoint)
        glyph = source["CBDT"].strikeData[0].get(name)
        if glyph is None:
            return None
        image = Image.open(BytesIO(glyph.imageData)).convert("RGBA")
        scale = size / source["CBLC"].strikes[0].bitmapSizeTable.ppemY
        tile = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        return (
            tile,
            round(glyph.metrics.BearingX * scale),
            -round(glyph.metrics.BearingY * scale),
            glyph.metrics.Advance * scale,
        )


@lru_cache(maxsize=1)
def fallback_paths():
    windows = Path("C:/Windows/Fonts")
    paths = [Path(__file__).parent / "assets/fonts/NotoColorEmoji.ttf"]
    paths += [
        windows / n
        for n in (
            "seguiemj.ttf",
            "seguisym.ttf",
            "cambria.ttc",
            "arial.ttf",
            "msyh.ttc",
            "malgun.ttf",
            "meiryo.ttc",
        )
    ]
    paths += [Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")]
    for folder in (
        Path(__file__).parent / "assets/fonts",
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/share/fonts/opentype/noto"),
        Path("/usr/share/fonts/truetype/dejavu"),
    ):
        if folder.is_dir():
            paths.extend(
                sorted(p for p in folder.iterdir() if p.suffix.lower() in (".ttf", ".otf", ".ttc"))
            )
    return tuple(dict.fromkeys(str(p) for p in paths if p.is_file()))


@lru_cache(maxsize=128)
def font_info(path):
    try:
        with TTFont(path, fontNumber=0, lazy=True) as face:
            points = frozenset((face.getBestCmap() or {}).keys())
            color = any(table in face for table in ("COLR", "CBDT", "sbix"))
            strikes = (
                tuple(s.bitmapSizeTable.ppemY for s in face["CBLC"].strikes)
                if "CBLC" in face
                else ()
            )
            return points, color, strikes
    except (OSError, ValueError, TTLibError, StructError) as exc:
        logging.warning("Skipping unreadable font %s: %s", path, exc)
        return frozenset(), False, ()


def first_grapheme(text):
    found = regex.match(r"\X", text or "")
    return found.group() if found else "?"


def needs_fallback(text, primary):
    return any(choose_font(primary, cluster) != primary for cluster in regex.findall(r"\X", text))


@lru_cache(maxsize=4096)
def choose_font(primary, cluster):
    required = {
        ord(ch)
        for ch in cluster
        if ch not in ("\u200d", "\ufe0f", "\ufe0e") and not 0xE0020 <= ord(ch) <= 0xE007F
    }
    candidates = (primary,) + fallback_paths()
    emoji = "\ufe0e" not in cluster and bool(
        regex.search(r"\p{Extended_Pictographic}|\p{Regional_Indicator}|\u20e3", cluster)
    )
    if emoji:
        colored = tuple(
            p for p in fallback_paths() if "emoji" in p.lower() or "seguiemj" in p.lower()
        )
        candidates = colored + candidates
    if "\ufe0e" in cluster:
        candidates = tuple(path for path in candidates if not font_info(path)[1])
    for path in dict.fromkeys(candidates):
        if path and required <= font_info(path)[0]:
            return path
    logging.getLogger(__name__).warning(
        "KY BOT font coverage missing for codepoints: %s",
        ",".join(f"U+{p:04X}" for p in sorted(required)),
    )
    return primary


@lru_cache(maxsize=128)
def face_at(path, size):
    try:
        return ImageFont.truetype(path, size), 1.0
    except OSError:
        strikes = font_info(path)[2]
        if not strikes:
            raise
        strike = min(strikes, key=lambda s: abs(s - size))
        return ImageFont.truetype(path, strike), size / strike


def runs(text, primary):
    result = []
    for cluster in regex.findall(r"\X", unicodedata.normalize("NFC", text)):
        path = choose_font(primary, cluster)
        if result and result[-1][0] == path and not font_info(path)[1]:
            result[-1] = (path, result[-1][1] + cluster)
        else:
            result.append((path, cluster))
    return result


def measure(text, size, primary):
    width = 0
    for path, value in runs(text, primary):
        if not font_info(path)[1]:
            # Presentation selectors are invisible instructions, not extra glyphs.
            value = value.replace("\ufe0e", "").replace("\ufe0f", "")
        bitmap = emoji_bitmap(path, value, size)
        width += (
            bitmap[3]
            if bitmap
            else face_at(path, size)[0].getlength(value) * face_at(path, size)[1]
        )
    return width


def text_image(text, size, primary, color, stroke_width=0, stroke_fill=None, metallic=None):
    pieces = []
    advance = 0
    top = 0
    bottom = 0
    for path, value in runs(text, primary):
        if not font_info(path)[1]:
            # Presentation selectors are invisible instructions, not extra glyphs.
            value = value.replace("\ufe0e", "").replace("\ufe0f", "")
        bitmap = emoji_bitmap(path, value, size)
        if bitmap:
            tile, left, y, width = bitmap
            pieces.append((tile, round(advance + left), y))
            top = min(top, y)
            bottom = max(bottom, y + tile.height)
            advance += width
            continue
        face, scale = face_at(path, size)
        colored = font_info(path)[1]
        stroke = 0 if colored else stroke_width
        box = face.getbbox(value, anchor="ls", stroke_width=stroke)
        width = max(1, box[2] - box[0] + 4)
        height = max(1, box[3] - box[1] + 4)
        tile = Image.new("RGBA", (width, height))
        ImageDraw.Draw(tile).text(
            (2 - box[0], 2 - box[1]),
            value,
            font=face,
            anchor="ls",
            fill=color,
            stroke_width=stroke,
            stroke_fill=stroke_fill or color,
            embedded_color=colored,
        )
        if metallic and not colored:
            from ky_bot.design.footers import metal

            tile = metal(tile.getchannel("A"), metallic)
        if scale != 1:
            tile = tile.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            )
        left = round(advance + (box[0] - 2) * scale)
        y = round((box[1] - 2) * scale)
        pieces.append((tile, left, y))
        top = min(top, y)
        bottom = max(bottom, y + tile.height)
        advance += face.getlength(value) * scale
    if not pieces:
        return Image.new("RGBA", (1, 1))
    left = min(x for _, x, _ in pieces)
    right = max(x + im.width for im, x, _ in pieces)
    result = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    for tile, x, y in pieces:
        result.alpha_composite(tile, (x - left, y - top))
    bounds = result.getbbox()
    return result.crop(bounds) if bounds else Image.new("RGBA", (max(1, round(advance)), 1))


def fit_text(text, size, primary, color, width, *, metallic=None, height=None):
    """Render the complete name to fit, without clipping a grapheme or flattening emoji color."""
    while measure(text, size, primary) > width and size > 18:
        size = max(18, size - 2)
    tile = text_image(text, size, primary, color, metallic=metallic)
    scale = min(1.0, width / tile.width, height / tile.height if height else 1.0)
    if scale < 1:
        tile = tile.resize(
            (max(1, round(tile.width * scale)), max(1, round(tile.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return tile


def wrap_text(text, size, primary, width, max_lines=5):
    lines, line = [], ""
    for word in text.split():
        candidate = (line + " " + word).strip()
        if line and measure(candidate, size, primary) > width:
            lines.append(line)
            line = ""
        for cluster in regex.findall(r"\X", word):
            if line and measure(line + cluster, size, primary) > width:
                lines.append(line)
                line = ""
            line += cluster
        line += " "
    if line.strip():
        lines.append(line.strip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = regex.findall(r"\X", lines[-1].rstrip())
        while last and measure("".join(last) + "…", size, primary) > width:
            last.pop()
        lines[-1] = "".join(last).rstrip() + "…"
    return [line.rstrip() for line in lines]
