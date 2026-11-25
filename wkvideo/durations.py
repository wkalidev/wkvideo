"""Helper for reading per-line voiceover durations from a JSON file.

Supported shapes (durations in seconds)::

    [2.4, 3.1, 1.8]
    [{"text": "First line", "duration": 2.4}, ...]
    [{"start": 0.0, "end": 2.4}, ...]          # duration = end - start
    {"lines": [ ...any of the above... ]}
    {"durations": [2.4, 3.1, 1.8]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Sequence, Union


class DurationsError(ValueError):
    """Raised when a durations file is missing or malformed."""


def _line_duration(item, index: int) -> float:
    if isinstance(item, bool):
        raise DurationsError(f"line {index}: invalid duration {item!r}")
    if isinstance(item, (int, float)):
        value = float(item)
    elif isinstance(item, dict):
        if "duration" in item:
            value = item["duration"]
        elif "start" in item and "end" in item:
            try:
                value = float(item["end"]) - float(item["start"])
            except (TypeError, ValueError):
                raise DurationsError(f"line {index}: start/end must be numbers") from None
        else:
            raise DurationsError(f"line {index}: needs 'duration' or 'start'/'end'")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise DurationsError(f"line {index}: duration must be a number") from None
    else:
        raise DurationsError(f"line {index}: unsupported entry {item!r}")
    if value <= 0:
        raise DurationsError(f"line {index}: duration must be > 0 (got {value})")
    return value


def parse_line_durations(data) -> List[float]:
    if isinstance(data, dict):
        data = data.get("lines", data.get("durations"))
    if not isinstance(data, list) or not data:
        raise DurationsError("durations JSON must contain a non-empty list")
    return [_line_duration(item, i) for i, item in enumerate(data, start=1)]


def read_line_durations(path: Union[str, Path]) -> List[float]:
    """Return the per-line durations (seconds) stored in a JSON file."""
    path = Path(path)
    if not path.is_file():
        raise DurationsError(f"durations file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DurationsError(f"invalid JSON in {path}: {exc}") from None
    return parse_line_durations(data)


def apply_durations(scenes: Sequence, durations: Sequence[float]) -> None:
    """Overwrite each scene's duration with the matching line duration."""
    if len(durations) != len(scenes):
        raise DurationsError(
            f"got {len(durations)} line durations for {len(scenes)} scenes"
        )
    for scene, duration in zip(scenes, durations):
        scene.duration = float(duration)
