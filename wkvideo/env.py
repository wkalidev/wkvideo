"""Read API keys from the environment or a ``.env`` file.

Values are never printed. Variables already set to a non-empty value win
over the file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional


def parse_env(text: str) -> dict:
    """Parse ``KEY=VALUE`` lines (``#`` comments, optional ``export``, quotes)."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def env_files(dirs: Iterable[Path]) -> List[Path]:
    """``.env`` files in each directory and its parents, nearest first, no repeats."""
    found: List[Path] = []
    for start in dirs:
        try:
            start = Path(start).resolve()
        except OSError:
            continue
        for d in [start, *start.parents]:
            f = d / ".env"
            if f.is_file() and f not in found:
                found.append(f)
    return found


def load_env(dirs: Iterable[Path] = (), env_file: Optional[Path] = None) -> List[Path]:
    """Fill unset (or empty) variables from ``env_file`` or the nearest ``.env`` files.

    Looks in ``dirs`` and the current directory, and their parents. Returns
    the files read (paths only, never values).
    """
    files = [Path(env_file)] if env_file else env_files([*dirs, Path.cwd()])
    for f in files:
        for key, value in parse_env(f.read_text(encoding="utf-8-sig")).items():
            if not os.environ.get(key):
                os.environ[key] = value
    return files


def get_key(name: str) -> str:
    """The variable's value, stripped; empty string when unset."""
    return (os.environ.get(name) or "").strip()
