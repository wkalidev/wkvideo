"""Loading and validating scenes.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union


class SceneError(ValueError):
    """Raised when scenes.json is missing or malformed."""


@dataclass
class Scene:
    image: Path
    duration: Optional[float]
    top_text: str = ""
    accent_text: str = ""


def _pick(entry: dict, *keys: str, default=None):
    for key in keys:
        if key in entry and entry[key] is not None:
            return entry[key]
    return default


def parse_scenes(data: Union[list, dict], base_dir: Path) -> List[Scene]:
    """Turn decoded scenes.json data into Scene objects.

    Accepts either a list of scene objects or ``{"scenes": [...]}``.
    Each scene needs ``image``; ``duration`` (seconds), ``top`` / ``top_text``
    and ``accent`` / ``accent_text`` are optional. ``duration`` may be omitted
    when durations are supplied separately (see :mod:`wkvideo.durations`).
    """
    if isinstance(data, dict):
        data = data.get("scenes")
    if not isinstance(data, list) or not data:
        raise SceneError("scenes.json must contain a non-empty list of scenes")

    scenes: List[Scene] = []
    for i, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise SceneError(f"scene {i}: expected an object, got {type(entry).__name__}")
        image = _pick(entry, "image")
        if not image or not isinstance(image, str):
            raise SceneError(f"scene {i}: missing 'image'")
        image_path = Path(image)
        if not image_path.is_absolute():
            image_path = base_dir / image_path
        if not image_path.is_file():
            raise SceneError(f"scene {i}: image not found: {image_path}")

        duration = _pick(entry, "duration")
        if duration is not None:
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                raise SceneError(f"scene {i}: duration must be a number") from None
            if duration <= 0:
                raise SceneError(f"scene {i}: duration must be > 0")

        scenes.append(
            Scene(
                image=image_path,
                duration=duration,
                top_text=str(_pick(entry, "top", "top_text", default="")),
                accent_text=str(_pick(entry, "accent", "accent_text", default="")),
            )
        )
    return scenes


def load_scenes(path: Union[str, Path]) -> List[Scene]:
    """Read scenes.json; image paths are resolved relative to its folder."""
    path = Path(path)
    if not path.is_file():
        raise SceneError(f"scenes file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SceneError(f"invalid JSON in {path}: {exc}") from None
    return parse_scenes(data, path.parent)
