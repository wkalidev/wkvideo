"""Command-line entry point: ``wkvideo FOLDER [-o out.mp4]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .durations import DurationsError, apply_durations, read_line_durations
from .ffmpeg import DEFAULT_FPS, FFmpegError
from .pipeline import build_video, find_audio
from .render import find_font
from .scenes import SceneError, load_scenes


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wkvideo",
        description="Build a 1080x1920 MP4 from a folder of images, an MP3 voiceover and scenes.json.",
    )
    p.add_argument("folder", type=Path, help="input folder containing images, the MP3 and scenes.json")
    p.add_argument("-o", "--output", type=Path, help="output MP4 (default: FOLDER/output.mp4)")
    p.add_argument("--scenes", type=Path, help="scenes file (default: FOLDER/scenes.json)")
    p.add_argument("--audio", type=Path, help="voiceover MP3 (default: the only .mp3 in FOLDER)")
    p.add_argument("--durations", type=Path, help="JSON file with per-line durations; overrides scene durations")
    p.add_argument("--font", help="path to Inter Bold (default: search FOLDER, WKVIDEO_FONT, system fonts)")
    p.add_argument("--fps", type=int, default=DEFAULT_FPS, help=f"frame rate (default: {DEFAULT_FPS})")
    p.add_argument("--keep-temp", type=Path, metavar="DIR", help="keep intermediate frames and clips in DIR")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    folder: Path = args.folder
    if not folder.is_dir():
        print(f"error: not a folder: {folder}", file=sys.stderr)
        return 2
    try:
        scenes = load_scenes(args.scenes or folder / "scenes.json")
        if args.durations:
            apply_durations(scenes, read_line_durations(args.durations))
        audio = args.audio or find_audio(folder)
        font = find_font(args.font, [folder, folder / "fonts"])
        output = args.output or folder / "output.mp4"
        build_video(scenes, audio, output, font, fps=args.fps, work_dir=args.keep_temp)
    except (SceneError, DurationsError, FFmpegError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
