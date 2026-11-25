"""ffmpeg command builders and runners."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Sequence

OUT_SIZE = (1080, 1920)
UPSCALE_SIZE = (2160, 3840)
ZOOM_START = 1.00
ZOOM_END = 1.04
DEFAULT_FPS = 30


class FFmpegError(RuntimeError):
    pass


def ffmpeg_binary() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FFmpegError("ffmpeg not found on PATH")
    return exe


def frame_count(duration: float, fps: int = DEFAULT_FPS) -> int:
    return max(1, round(duration * fps))


def zoompan_filter(frames: int, fps: int = DEFAULT_FPS) -> str:
    """Upscale to 4K, then zoom linearly from 1.00 to 1.04 over ``frames``.

    Upscaling first gives zoompan sub-pixel room, which avoids the jitter it
    produces when it crops at the output resolution.
    """
    step = (ZOOM_END - ZOOM_START) / max(frames - 1, 1)
    uw, uh = UPSCALE_SIZE
    ow, oh = OUT_SIZE
    return (
        f"scale={uw}:{uh}:flags=lanczos,"
        f"zoompan=z='min({ZOOM_START}+{step:.10f}*on,{ZOOM_END})'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={ow}x{oh}:fps={fps},"
        f"format=yuv420p"
    )


def scene_command(frame_png: Path, duration: float, out: Path, fps: int = DEFAULT_FPS, exe: str = "ffmpeg") -> List[str]:
    frames = frame_count(duration, fps)
    return [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(frame_png),
        "-vf", zoompan_filter(frames, fps),
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out),
    ]


def concat_list(clips: Sequence[Path]) -> str:
    lines = []
    for clip in clips:
        path = Path(clip).resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{path}'")
    return "\n".join(lines) + "\n"


def concat_command(list_file: Path, out: Path, exe: str = "ffmpeg") -> List[str]:
    return [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out),
    ]


def mux_command(video: Path, audio: Path, out: Path, exe: str = "ffmpeg") -> List[str]:
    return [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out),
    ]


def run(cmd: Sequence[str]) -> None:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")
