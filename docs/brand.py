"""Build the README brand assets (docs/logo.svg, docs/header.svg).

All text is converted to outlines from the bundled Inter Bold, so the SVGs
look identical everywhere (GitHub renders SVGs as images, without web fonts).

    pip install fonttools uharfbuzz
    python docs/brand.py
"""

from __future__ import annotations

from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "wkvideo" / "fonts" / "Inter-Bold.ttf"
DOCS = ROOT / "docs"

INK = "#0E0F12"      # brand background
LIME = "#D6F048"
WHITE = "#FFFFFF"
MUTED = "#9CA1AC"
DIM = "#5D626D"
EDGE = "#2A2E37"     # card border, so the dark card reads on GitHub dark mode too
PANEL = "#181B21"


def num(v: float) -> str:
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


class Inter:
    def __init__(self, path: Path = FONT):
        self.tt = TTFont(path)
        self.glyphs = self.tt.getGlyphSet()
        self.upm = self.tt["head"].unitsPerEm
        self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))

    def _shape(self, text: str):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, {"kern": True, "liga": True})
        return [(self.tt.getGlyphName(i.codepoint), p.x_advance) for i, p in zip(buf.glyph_infos, buf.glyph_positions)]

    def width(self, text: str, size: float, tracking: float = 0) -> float:
        shaped = self._shape(text)
        return sum(a for _, a in shaped) * size / self.upm + tracking * (len(shaped) - 1)

    def path(self, text: str, size: float, x: float, baseline: float, tracking: float = 0) -> str:
        """SVG path data for ``text`` with its left edge at ``x``."""
        pen = SVGPathPen(self.glyphs, ntos=num)
        s = size / self.upm
        for name, advance in self._shape(text):
            self.glyphs[name].draw(TransformPen(pen, (s, 0, 0, -s, x, baseline)))
            x += advance * s + tracking
        return pen.getCommands()

    def glyph_bounds(self, char: str):
        pen = BoundsPen(self.glyphs)
        self.glyphs[self.tt.getBestCmap()[ord(char)]].draw(pen)
        return pen.bounds  # font units, y up

    def glyph_path(self, char: str, height: float, cx: float, cy: float) -> str:
        """A single glyph scaled to ``height`` px, centred on (cx, cy)."""
        x0, y0, x1, y1 = self.glyph_bounds(char)
        s = height / (y1 - y0)
        ox = cx - (x0 + x1) / 2 * s
        oy = cy + (y0 + y1) / 2 * s
        pen = SVGPathPen(self.glyphs, ntos=num)
        self.glyphs[self.tt.getBestCmap()[ord(char)]].draw(TransformPen(pen, (s, 0, 0, -s, ox, oy)))
        return pen.getCommands()


def mark(inter: Inter, size: float = 256, x: float = 0, y: float = 0, tile: bool = True) -> str:
    """The {wk} mark: lime braces around a vertical (9:16) video frame holding "wk".

    Drawn on a 256 grid and scaled to ``size``. The frame's progress bar makes
    it read as video; the braces keep the {wk} signature.
    """
    k = size / 256
    parts = []
    if tile:
        parts.append(f'<rect width="256" height="256" rx="58" fill="{INK}"/>')
    # Vertical frame (9:16), centred.
    fw, fh = 104, 184
    fx, fy = (256 - fw) / 2, (256 - fh) / 2
    parts.append(
        f'<rect x="{num(fx)}" y="{num(fy)}" width="{fw}" height="{fh}" rx="20" '
        f'fill="{PANEL}" stroke="{LIME}" stroke-width="9"/>'
    )
    # "wk", centred slightly above the middle of the frame.
    wk_size = 50
    wk_w = inter.width("wk", wk_size)
    parts.append(f'<path fill="{WHITE}" d="{inter.path("wk", wk_size, 128 - wk_w / 2, 136)}"/>')
    # Progress bar: track + lime played part.
    bx, bw, by = fx + 18, fw - 36, fy + fh - 32
    parts.append(f'<rect x="{num(bx)}" y="{num(by)}" width="{num(bw)}" height="7" rx="3.5" fill="{WHITE}" fill-opacity="0.22"/>')
    parts.append(f'<rect x="{num(bx)}" y="{num(by)}" width="{num(bw * 0.58)}" height="7" rx="3.5" fill="{LIME}"/>')
    # Braces either side of the frame.
    parts.append(f'<path fill="{LIME}" d="{inter.glyph_path("{", 150, 46, 128)}"/>')
    parts.append(f'<path fill="{LIME}" d="{inter.glyph_path("}", 150, 210, 128)}"/>')
    return f'<g transform="translate({num(x)} {num(y)}) scale({k:.4f})">' + "".join(parts) + "</g>"


def logo_svg(inter: Inter) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256" '
        'role="img" aria-label="wkvideo logo">'
        f"<title>wkvideo</title>{mark(inter)}</svg>\n"
    )


def chip(inter: Inter, text: str, x: float, y: float, lime: bool = False) -> tuple[str, float]:
    """A rounded label; returns (svg, width)."""
    size, pad, h = 19, 16, 38
    w = inter.width(text, size) + 2 * pad
    stroke = LIME if lime else EDGE
    fill = LIME if lime else "none"
    color = INK if lime else MUTED
    svg = (
        f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{h}" rx="{h / 2}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1.5"/>'
        f'<path fill="{color}" d="{inter.path(text, size, x + pad, y + h / 2 + size * 0.36)}"/>'
    )
    return svg, w


def phone(inter: Inter, x: float, y: float, h: float) -> str:
    """A wireframe of a wkvideo frame: headline, accent, image, word captions."""
    w = h * 9 / 16
    k = h / 1920  # draw in output-pixel coordinates
    p = [f'<g transform="translate({num(x)} {num(y)}) scale({k:.5f})">']
    p.append(f'<rect width="1080" height="1920" rx="90" fill="{PANEL}" stroke="{EDGE}" stroke-width="10"/>')
    # {wk} logo top right
    p.append(f'<path fill="{LIME}" d="{inter.path("{wk}", 64, 1080 - 48 - inter.width("{wk}", 64), 112)}"/>')
    # headline + accent as bars
    p.append(f'<rect x="150" y="250" width="780" height="70" rx="35" fill="{WHITE}"/>')
    p.append(f'<rect x="250" y="370" width="580" height="56" rx="28" fill="{LIME}"/>')
    # image at 96% width from 30% height
    p.append(f'<rect x="22" y="576" width="1036" height="760" rx="24" fill="#2A2F3A"/>')
    p.append(f'<path d="M22 1180 L300 900 L520 1110 L720 950 L1058 1260 L1058 1312 Q1058 1336 1034 1336 L46 1336 Q22 1336 22 1312 Z" fill="#363C49"/>')
    p.append(f'<circle cx="820" cy="760" r="70" fill="#3E4553"/>')
    # word captions at 70%: one highlighted word
    cy = 1344 - 34
    for cx, cw, col in ((170, 230, WHITE), (430, 300, LIME), (760, 150, WHITE)):
        p.append(f'<rect x="{cx}" y="{cy}" width="{cw}" height="68" rx="34" fill="{col}"/>')
    # progress bar
    p.append(f'<rect x="90" y="1800" width="900" height="16" rx="8" fill="{WHITE}" fill-opacity="0.18"/>')
    p.append(f'<rect x="90" y="1800" width="340" height="16" rx="8" fill="{LIME}"/>')
    p.append("</g>")
    return "".join(p), w


def header_svg(inter: Inter) -> str:
    W, H = 1200, 380
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="wkvideo: images and a voiceover in, a captioned vertical video out">',
        "<title>wkvideo</title>",
        "<defs>"
        f'<radialGradient id="glow" cx="0.82" cy="0.5" r="0.55"><stop offset="0" stop-color="{LIME}" stop-opacity="0.13"/>'
        f'<stop offset="1" stop-color="{LIME}" stop-opacity="0"/></radialGradient>'
        f'<clipPath id="card"><rect width="{W}" height="{H}" rx="28"/></clipPath>'
        "</defs>",
        f'<g clip-path="url(#card)"><rect width="{W}" height="{H}" fill="{INK}"/>'
        f'<rect width="{W}" height="{H}" fill="url(#glow)"/>',
    ]
    # Right: two output-frame wireframes, the back one dimmed, both tilted,
    # suggesting a sequence of scenes.
    ph = 292
    pw = ph * 9 / 16
    back, _ = phone(inter, 0, 0, ph)
    front, _ = phone(inter, 0, 0, ph)
    bx, by = 878, 44
    fx, fy = 958, 44
    out.append(
        f'<g opacity="0.35" transform="rotate(-9 {num(bx + pw / 2)} {num(by + ph / 2)}) translate({bx} {by})">{back}</g>'
    )
    out.append(
        f'<g transform="rotate(5 {num(fx + pw / 2)} {num(fy + ph / 2)}) translate({fx} {fy})">'
        f'<rect x="6" y="10" width="{num(pw)}" height="{ph}" rx="{num(90 * ph / 1920)}" fill="#000" fill-opacity="0.45"/>'
        f"{front}</g>"
    )
    out.append("</g>")
    out.append(f'<rect x="0.75" y="0.75" width="{W - 1.5}" height="{H - 1.5}" rx="27.25" fill="none" stroke="{EDGE}" stroke-width="1.5"/>')

    # Left: mark + wordmark + tagline + pipeline.
    left = 64
    out.append(mark(inter, size=112, x=left - 10, y=58, tile=False))
    out.append(f'<path fill="{WHITE}" d="{inter.path("wkvideo", 96, left + 116, 147, tracking=-2)}"/>')
    out.append(f'<path fill="{WHITE}" d="{inter.path("Images and a voiceover in.", 34, left, 236, tracking=-0.3)}"/>')
    out.append(f'<path fill="{LIME}" d="{inter.path("A captioned vertical video out.", 34, left, 282, tracking=-0.3)}"/>')

    x, y = left, 314
    for i, label in enumerate(("scenes.json", "images", "voiceover.mp3")):
        svg, w = chip(inter, label, x, y)
        out.append(svg)
        x += w + 10
    arrow_w = 34
    out.append(
        f'<path d="M{num(x + 6)} {y + 19} h{arrow_w - 12} m-9 -8 l9 8 l-9 8" fill="none" stroke="{LIME}" '
        f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    svg, _ = chip(inter, "1080×1920 MP4", x + arrow_w + 4, y, lime=True)
    out.append(svg)
    out.append("</svg>\n")
    return "".join(out)


def main() -> None:
    inter = Inter()
    (DOCS / "logo.svg").write_text(logo_svg(inter), encoding="utf-8")
    (DOCS / "header.svg").write_text(header_svg(inter), encoding="utf-8")
    for name in ("logo.svg", "header.svg"):
        print(f"wrote docs/{name} ({(DOCS / name).stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
