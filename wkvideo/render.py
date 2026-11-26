"""Render a single 1080x1920 scene frame with Pillow."""

from __future__ import annotations

import os
import sys
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

WIDTH, HEIGHT = 1080, 1920
LIME = (0xD6, 0xF0, 0x48)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

IMAGE_WIDTH_RATIO = 0.96
IMAGE_TOP_RATIO = 0.30
BOTTOM_MARGIN = 60
SIDE_MARGIN = 60
BLUR_RADIUS = 40
BACKGROUND_BRIGHTNESS = 0.45
TOP_SIZE = 84               # headline size before any shrinking
ACCENT_RATIO = 0.8          # accent starts at 80% of the headline size
MIN_SINGLE_LINE_SIZE = 60   # shrink to this before wrapping
MIN_TEXT_SIZE = 32          # last resort if wrapped text overflows the band
LOGO_TEXT = "{wk}"
LOGO_SIZE = 64
LOGO_MARGIN = 48

BUNDLED_FONT = Path(__file__).resolve().parent / "fonts" / "Inter-Bold.ttf"

INTER_NAMES = (
    "Inter-Bold.ttf",
    "Inter-Bold.otf",
    "Inter_24pt-Bold.ttf",
    "Inter_18pt-Bold.ttf",
    "InterDisplay-Bold.ttf",
    "InterVariable.ttf",
    "Inter-VariableFont_opsz,wght.ttf",
    "Inter.ttc",
)
FALLBACK_NAMES = ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")


def _font_dirs(extra: Iterable[Path] = ()) -> List[Path]:
    dirs = [Path(p) for p in extra]
    home = Path.home()
    if sys.platform.startswith("win"):
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        dirs += [windir / "Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [home / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
    else:
        dirs += [home / ".local/share/fonts", home / ".fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    return dirs


def _search(names: Iterable[str], dirs: Iterable[Path]) -> Optional[Path]:
    names = list(names)
    for d in dirs:
        if not d.is_dir():
            continue
        for name in names:
            direct = d / name
            if direct.is_file():
                return direct
        # Linux font trees are nested one or two levels deep.
        for name in names:
            for found in d.glob(f"**/{name}"):
                return found
    return None


def find_font(explicit: Optional[str] = None, search_dirs: Iterable[Path] = ()) -> Path:
    """Return the font to use, defaulting to the bundled Inter Bold.

    Order: ``explicit`` path (``--font``), ``WKVIDEO_FONT`` env var, the
    bundled ``wkvideo/fonts/Inter-Bold.ttf``. Only if the bundled file is
    missing (e.g. a broken install) are ``search_dirs`` and system fonts
    searched, falling back to a common bold sans with a warning.
    """
    for candidate in (explicit, os.environ.get("WKVIDEO_FONT")):
        if candidate:
            path = Path(candidate)
            if not path.is_file():
                raise FileNotFoundError(f"font not found: {path}")
            return path
    if BUNDLED_FONT.is_file():
        return BUNDLED_FONT
    dirs = _font_dirs(search_dirs)
    found = _search(INTER_NAMES, dirs)
    if found:
        return found
    fallback = _search(FALLBACK_NAMES, dirs)
    if fallback:
        warnings.warn(f"Inter Bold not found; falling back to {fallback.name}. Use --font to set it.")
        return fallback
    raise FileNotFoundError("no usable bold font found; pass --font /path/to/Inter-Bold.ttf")


@lru_cache(maxsize=64)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    try:  # variable fonts (e.g. InterVariable.ttf): select the Bold instance
        font.set_variation_by_name("Bold")
    except Exception:
        pass
    return font


def cover(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Scale and center-crop ``img`` so it fills ``size``."""
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.5))


def fit_box(src: Tuple[int, int], max_w: int, max_h: int) -> Tuple[int, int]:
    """Scale ``src`` to ``max_w`` wide, shrinking further if it exceeds ``max_h``."""
    w, h = src
    scale = max_w / w
    if h * scale > max_h:
        scale = max_h / h
    return max(1, round(w * scale)), max(1, round(h * scale))


def _fits(font: ImageFont.FreeTypeFont, text: str, max_width: int, stroke: int) -> bool:
    return font.getlength(text) + 2 * stroke <= max_width


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, stroke: int = 0) -> List[str]:
    """Greedy word wrap that never leaves a single word alone on the last line."""
    lines: List[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            continue
        para: List[List[str]] = [[words[0]]]
        for word in words[1:]:
            if _fits(font, " ".join(para[-1] + [word]), max_width, stroke):
                para[-1].append(word)
            else:
                para.append([word])
        # Orphan fix: pull the previous line's last word down to join it.
        if len(para) >= 2 and len(para[-1]) == 1 and len(para[-2]) > 1:
            candidate = [para[-2][-1]] + para[-1]
            if _fits(font, " ".join(candidate), max_width, stroke):
                para[-2] = para[-2][:-1]
                para[-1] = candidate
        lines.extend(" ".join(line) for line in para)
    return lines


def _stroke(size: int) -> int:
    return max(2, size // 14)


def fit_text(text: str, font_path: str, max_w: int, start: int, floor: int = MIN_SINGLE_LINE_SIZE):
    """Return ``(size, lines)`` for one text element.

    Shrinks from ``start`` down to ``floor`` to keep each paragraph on a
    single line; only if it still doesn't fit at ``floor`` is it wrapped
    (at ``floor``).
    """
    paragraphs = [" ".join(p.split()) for p in text.splitlines() if p.strip()]
    if not paragraphs:
        return start, []
    for size in range(start, floor - 1, -1):
        font = load_font(font_path, size)
        if all(_fits(font, p, max_w, _stroke(size)) for p in paragraphs):
            return size, paragraphs
    return floor, wrap_text("\n".join(paragraphs), load_font(font_path, floor), max_w, _stroke(floor))


def _layout_block(top: str, accent: str, font_path: str, max_w: int, max_h: int):
    """Size and wrap the top and accent text so the block fits the band.

    Normally sizes stay within [MIN_SINGLE_LINE_SIZE, TOP_SIZE]. Only if the
    wrapped block is still too tall for the band is everything shrunk further.
    """
    for cap in range(TOP_SIZE, MIN_TEXT_SIZE - 1, -4):
        floor = min(MIN_SINGLE_LINE_SIZE, cap)
        top_size, top_lines = fit_text(top, font_path, max_w, cap, floor)
        accent_start = round(cap * ACCENT_RATIO)
        if top_lines:  # keep the accent no larger than the headline
            accent_start = min(accent_start, top_size)
        accent_size, accent_lines = fit_text(accent, font_path, max_w, max(accent_start, floor), floor)
        line_h, accent_h = round(top_size * 1.18), round(accent_size * 1.18)
        gap = round(top_size * 0.35) if top_lines and accent_lines else 0
        total = len(top_lines) * line_h + gap + len(accent_lines) * accent_h
        too_wide = any(
            not _fits(load_font(font_path, sz), line, max_w, 0)
            for sz, lines in ((top_size, top_lines), (accent_size, accent_lines))
            for line in lines
        )
        if total <= max_h and not too_wide:
            break
    return top_size, accent_size, top_lines, accent_lines, line_h, accent_h, gap, total


def _draw_centered(draw, lines, font, y, line_h, fill, stroke):
    for line in lines:
        draw.text(
            (WIDTH / 2, y + line_h / 2), line, font=font, fill=fill, anchor="mm",
            stroke_width=stroke, stroke_fill=BLACK,
        )
        y += line_h
    return y


IMAGE_TOP = round(HEIGHT * IMAGE_TOP_RATIO)
LOGO_BOTTOM = LOGO_MARGIN + round(LOGO_SIZE * 1.2)


def _draw_text_block(draw, top_text: str, accent_text: str, font_path: str, top: bool, accent: bool) -> None:
    """Draw the top and/or accent text, centred between the logo and the image.

    The layout always accounts for both texts, so drawing them separately
    (e.g. the animated hook headline) puts them in the same place.
    """
    if not (top_text.strip() or accent_text.strip()):
        return
    band_top, band_bottom = LOGO_BOTTOM + 30, IMAGE_TOP - 30
    size, accent_size, top_lines, accent_lines, line_h, accent_h, gap, total = _layout_block(
        top_text, accent_text, font_path, WIDTH - 2 * SIDE_MARGIN, band_bottom - band_top
    )
    y = band_top + max(0, (band_bottom - band_top - total) // 2)
    if top:
        _draw_centered(draw, top_lines, load_font(font_path, size), y, line_h, WHITE, _stroke(size))
    if accent:
        y += len(top_lines) * line_h + gap
        _draw_centered(draw, accent_lines, load_font(font_path, accent_size), y, accent_h, LIME, _stroke(accent_size))


def _draw_logo(draw, font_path: str) -> None:
    draw.text(
        (WIDTH - LOGO_MARGIN, LOGO_MARGIN), LOGO_TEXT, font=load_font(font_path, LOGO_SIZE), fill=LIME,
        anchor="ra", stroke_width=3, stroke_fill=BLACK,
    )


def image_size(src: Tuple[int, int], max_bottom: Optional[float] = None) -> Tuple[int, int]:
    """Size of the foreground image: 96% of the width, top edge at 30% height.

    Its bottom edge stays above ``HEIGHT - BOTTOM_MARGIN``, or above
    ``max_bottom`` if that is higher (used to leave room for captions).
    """
    bottom = HEIGHT - BOTTOM_MARGIN
    if max_bottom is not None:
        bottom = min(bottom, int(max_bottom))
    return fit_box(src, round(WIDTH * IMAGE_WIDTH_RATIO), max(1, bottom - IMAGE_TOP))


def image_bottom(image_path, max_bottom: Optional[float] = None) -> int:
    """Y of the foreground image's bottom edge in the (unzoomed) frame."""
    with Image.open(image_path) as src:
        size = ImageOps.exif_transpose(src).size
    return IMAGE_TOP + image_size(size, max_bottom)[1]


def render_frame(
    image_path, top_text: str, accent_text: str, font_path, draw_top: bool = True, draw_logo: bool = True,
    image_max_bottom: Optional[float] = None,
) -> Image.Image:
    """Compose one 1080x1920 RGB frame for a scene.

    ``draw_top=False`` / ``draw_logo=False`` leave the headline or logo out so
    they can be layered separately (see :func:`render_top_layer` and
    :func:`render_logo_layer`). ``image_max_bottom`` limits how low the
    foreground image may reach (see :func:`image_size`).
    """
    font_path = str(font_path)
    with Image.open(image_path) as src:
        src = ImageOps.exif_transpose(src).convert("RGB")

        # Background: cover-crop, heavy blur, darken.
        bg = cover(src, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        frame = ImageEnhance.Brightness(bg).enhance(BACKGROUND_BRIGHTNESS)

        # Foreground: 96% width, top edge at 30% height.
        fw, fh = image_size(src.size, image_max_bottom)
        fg = src.resize((fw, fh), Image.LANCZOS)
        frame.paste(fg, ((WIDTH - fw) // 2, IMAGE_TOP))

    draw = ImageDraw.Draw(frame)

    if draw_logo:
        _draw_logo(draw, font_path)
    _draw_text_block(draw, top_text, accent_text, font_path, top=draw_top, accent=True)
    return frame


def render_top_layer(top_text: str, accent_text: str, font_path) -> Image.Image:
    """Just the headline on a transparent WIDTH x IMAGE_TOP layer."""
    layer = Image.new("RGBA", (WIDTH, IMAGE_TOP), (0, 0, 0, 0))
    _draw_text_block(ImageDraw.Draw(layer), top_text, accent_text, str(font_path), top=True, accent=False)
    return layer


def render_logo_layer(font_path) -> Image.Image:
    """Just the {wk} logo on a transparent WIDTH x IMAGE_TOP layer."""
    layer = Image.new("RGBA", (WIDTH, IMAGE_TOP), (0, 0, 0, 0))
    _draw_logo(ImageDraw.Draw(layer), str(font_path))
    return layer


def render_overlay(top_text: str, accent_text: str, font_path, draw_top: bool = True,
                   draw_logo: bool = True) -> Image.Image:
    """The headline, accent line and logo on a transparent full frame (for video scenes)."""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if draw_logo:
        _draw_logo(draw, str(font_path))
    _draw_text_block(draw, top_text, accent_text, str(font_path), top=draw_top, accent=True)
    return layer
