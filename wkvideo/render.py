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


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, stroke: int = 0) -> List[str]:
    lines: List[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            continue
        line = words[0]
        for word in words[1:]:
            trial = f"{line} {word}"
            if font.getlength(trial) + 2 * stroke <= max_width:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _stroke(size: int) -> int:
    return max(2, size // 14)


def _layout_block(top: str, accent: str, font_path: str, max_w: int, max_h: int):
    """Pick the largest font size whose wrapped text fits the band."""
    for size in range(84, 31, -4):
        accent_size = round(size * 0.8)
        top_font, accent_font = load_font(font_path, size), load_font(font_path, accent_size)
        top_lines = wrap_text(top, top_font, max_w, _stroke(size))
        accent_lines = wrap_text(accent, accent_font, max_w, _stroke(accent_size))
        line_h, accent_h = round(size * 1.18), round(accent_size * 1.18)
        gap = round(size * 0.35) if top_lines and accent_lines else 0
        total = len(top_lines) * line_h + gap + len(accent_lines) * accent_h
        too_wide = any(top_font.getlength(l) > max_w for l in top_lines) or any(
            accent_font.getlength(l) > max_w for l in accent_lines
        )
        if total <= max_h and not too_wide:
            break
    return size, top_lines, accent_lines, line_h, accent_h, gap, total


def _draw_centered(draw, lines, font, y, line_h, fill, stroke):
    for line in lines:
        draw.text(
            (WIDTH / 2, y + line_h / 2), line, font=font, fill=fill, anchor="mm",
            stroke_width=stroke, stroke_fill=BLACK,
        )
        y += line_h
    return y


def render_frame(image_path, top_text: str, accent_text: str, font_path) -> Image.Image:
    """Compose one 1080x1920 RGB frame for a scene."""
    font_path = str(font_path)
    with Image.open(image_path) as src:
        src = ImageOps.exif_transpose(src).convert("RGB")

        # Background: cover-crop, heavy blur, darken.
        bg = cover(src, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        frame = ImageEnhance.Brightness(bg).enhance(BACKGROUND_BRIGHTNESS)

        # Foreground: 96% width, top edge at 30% height.
        img_top = round(HEIGHT * IMAGE_TOP_RATIO)
        fw, fh = fit_box(src.size, round(WIDTH * IMAGE_WIDTH_RATIO), HEIGHT - img_top - BOTTOM_MARGIN)
        fg = src.resize((fw, fh), Image.LANCZOS)
        frame.paste(fg, ((WIDTH - fw) // 2, img_top))

    draw = ImageDraw.Draw(frame)

    # Logo, top right.
    logo_font = load_font(font_path, LOGO_SIZE)
    draw.text(
        (WIDTH - LOGO_MARGIN, LOGO_MARGIN), LOGO_TEXT, font=logo_font, fill=LIME, anchor="ra",
        stroke_width=3, stroke_fill=BLACK,
    )
    logo_bottom = LOGO_MARGIN + round(LOGO_SIZE * 1.2)

    # Text block, centered in the band between the logo and the image.
    band_top, band_bottom = logo_bottom + 30, img_top - 30
    max_w = WIDTH - 2 * SIDE_MARGIN
    if top_text.strip() or accent_text.strip():
        size, top_lines, accent_lines, line_h, accent_h, gap, total = _layout_block(
            top_text, accent_text, font_path, max_w, band_bottom - band_top
        )
        accent_size = round(size * 0.8)
        y = band_top + max(0, (band_bottom - band_top - total) // 2)
        y = _draw_centered(draw, top_lines, load_font(font_path, size), y, line_h, WHITE, _stroke(size))
        _draw_centered(
            draw, accent_lines, load_font(font_path, accent_size), y + gap, accent_h, LIME, _stroke(accent_size)
        )
    return frame
