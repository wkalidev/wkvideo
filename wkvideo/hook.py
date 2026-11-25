"""Hook punch for the first scene: headline pop-in and a fast opening zoom."""

from __future__ import annotations

from pathlib import Path
from typing import List

from PIL import Image

from .render import render_top_layer

POP_START_SCALE = 1.3
POP_SECONDS = 0.25
FAST_ZOOM_SECONDS = 0.6
FAST_ZOOM_AMOUNT = 0.08   # zoom 1.00 -> 1.08 in the first 0.6s, then the usual +0.04


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def pop_scale(t: float) -> float:
    """Headline scale at time ``t``: 1.3 at t=0, easing to 1.0 at 0.25s."""
    return POP_START_SCALE - (POP_START_SCALE - 1.0) * ease_out(t / POP_SECONDS)


def pop_frame_count(fps: int) -> int:
    """Frames covering the pop, including the final frame at scale 1.0."""
    return round(POP_SECONDS * fps) + 1


def scaled_layer(layer: Image.Image, scale: float) -> Image.Image:
    """Scale the layer's content about its own centre, keeping the canvas size."""
    bbox = layer.getbbox()
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    if bbox is None:
        return out
    content = layer.crop(bbox)
    w, h = max(1, round(content.width * scale)), max(1, round(content.height * scale))
    content = content.resize((w, h), Image.LANCZOS)
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    # paste() clips anything that falls outside the canvas at large scales.
    out.paste(content, (round(cx - w / 2), round(cy - h / 2)), content)
    return out


def write_pop_frames(top_text: str, accent_text: str, font_path, work: Path, fps: int) -> str:
    """Render the pop-in frames as PNGs; returns an ffmpeg image-sequence pattern."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    layer = render_top_layer(top_text, accent_text, font_path)
    for k in range(pop_frame_count(fps)):
        scaled_layer(layer, pop_scale(k / fps)).save(work / f"pop_{k:03d}.png")
    return str(work / "pop_%03d.png")


def pop_scales(fps: int) -> List[float]:
    return [pop_scale(k / fps) for k in range(pop_frame_count(fps))]
