"""End-to-end build: scenes -> frames -> zoomed clips -> concatenated MP4 + MP3."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import ffmpeg
from .render import render_frame
from .scenes import Scene


def find_audio(folder: Path) -> Path:
    mp3s = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() == ".mp3")
    if len(mp3s) != 1:
        found = ", ".join(p.name for p in mp3s) or "none"
        raise FileNotFoundError(f"expected exactly one .mp3 in {folder} (found: {found}); use --audio")
    return mp3s[0]


def build_video(
    scenes: Sequence[Scene],
    audio: Path,
    output: Path,
    font: Path,
    fps: int = ffmpeg.DEFAULT_FPS,
    work_dir: Optional[Path] = None,
    log: Callable[[str], None] = print,
) -> Path:
    missing = [i for i, s in enumerate(scenes, start=1) if not s.duration]
    if missing:
        raise ValueError(f"scenes without a duration: {missing}; set 'duration' or pass --durations")

    exe = ffmpeg.ffmpeg_binary()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    keep = work_dir is not None
    work = Path(work_dir) if keep else Path(tempfile.mkdtemp(prefix="wkvideo_"))
    work.mkdir(parents=True, exist_ok=True)

    try:
        clips: List[Path] = []
        for i, scene in enumerate(scenes, start=1):
            log(f"[{i}/{len(scenes)}] {scene.image.name} ({scene.duration:.2f}s)")
            frame_png = work / f"scene_{i:03d}.png"
            render_frame(scene.image, scene.top_text, scene.accent_text, font).save(frame_png)
            clip = work / f"scene_{i:03d}.mp4"
            ffmpeg.run(ffmpeg.scene_command(frame_png, scene.duration, clip, fps, exe))
            clips.append(clip)

        list_file = work / "clips.txt"
        list_file.write_text(ffmpeg.concat_list(clips), encoding="utf-8")
        silent = work / "video_silent.mp4"
        ffmpeg.run(ffmpeg.concat_command(list_file, silent, exe))

        log(f"Muxing audio {Path(audio).name}")
        ffmpeg.run(ffmpeg.mux_command(silent, Path(audio), output, exe))
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return output
