"""ffmpeg command builders and runners."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

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


def ffprobe_binary() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise FFmpegError("ffprobe not found on PATH")
    return exe


def probe_duration(path: Path, exe: str = "ffprobe") -> float:
    """Return a media file's duration in seconds, as reported by ffprobe."""
    proc = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise FFmpegError(f"could not read duration of {path}: {proc.stderr.strip()}") from None


def frame_count(duration: float, fps: int = DEFAULT_FPS) -> int:
    return max(1, round(duration * fps))


def zoom_expr(frames: int, fps: int = DEFAULT_FPS, hook: bool = False) -> str:
    """zoompan ``z`` expression.

    Normal: linear 1.00 -> 1.04 over the scene. Hook: an ease-out zoom from
    1.00 to 1.00 + FAST_ZOOM_AMOUNT over the first FAST_ZOOM_SECONDS, then the
    usual slow +0.04 over the rest of the scene.
    """
    slow = ZOOM_END - ZOOM_START
    if not hook:
        step = slow / max(frames - 1, 1)
        return f"min({ZOOM_START}+{step:.10f}*on,{ZOOM_END})"
    from .hook import FAST_ZOOM_AMOUNT, FAST_ZOOM_SECONDS

    fast = max(1, min(round(FAST_ZOOM_SECONDS * fps), frames - 1))
    peak = ZOOM_START + FAST_ZOOM_AMOUNT
    step = slow / max(frames - 1 - fast, 1)
    return (
        f"if(lt(on,{fast}),{ZOOM_START}+{FAST_ZOOM_AMOUNT}*(1-pow(1-on/{fast},3)),"
        f"min({peak}+{step:.10f}*(on-{fast}),{peak + slow:.4f}))"
    )


def zoompan_filter(frames: int, fps: int = DEFAULT_FPS, hook: bool = False) -> str:
    """Upscale to 4K, then zoom over ``frames`` (see :func:`zoom_expr`).

    Upscaling first gives zoompan sub-pixel room, which avoids the jitter it
    produces when it crops at the output resolution.
    """
    uw, uh = UPSCALE_SIZE
    ow, oh = OUT_SIZE
    return (
        f"scale={uw}:{uh}:flags=lanczos,"
        f"zoompan=z='{zoom_expr(frames, fps, hook)}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={ow}x{oh}:fps={fps},"
        f"format=yuv420p"
    )


def hook_scene_command(
    frame_png: Path, pop_pattern: str, duration: float, out: Path, fps: int = DEFAULT_FPS, exe: str = "ffmpeg"
) -> List[str]:
    """First scene: fast-zoom background with the pop-in headline overlaid.

    The headline frames are an image sequence; overlay holds the last one
    (scale 1.0) for the rest of the scene.
    """
    frames = frame_count(duration, fps)
    graph = (
        f"[0:v]{zoompan_filter(frames, fps, hook=True)}[bg];"
        f"[bg][1:v]overlay=0:0:eof_action=repeat:format=auto,format=yuv420p[v]"
    )
    return [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(frame_png),
        "-framerate", str(fps), "-i", pop_pattern,
        "-filter_complex", graph, "-map", "[v]",
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out),
    ]


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


def final_command(
    video: Path,
    voice: Path,
    out: Path,
    captions: Optional[Path] = None,
    caption_y: int = 0,
    fps: int = DEFAULT_FPS,
    exe: str = "ffmpeg",
) -> List[str]:
    """Add the voiceover and, optionally, overlay the caption track.

    Without captions the video stream is copied; with them it is re-encoded.
    """
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(voice)]
    if captions is not None:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(captions)]
        cmd += [
            "-filter_complex",
            f"[0:v][2:v]overlay=0:{caption_y}:eof_action=pass:format=auto,format=yuv420p[v]",
            "-map", "[v]", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(fps),
        ]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    return cmd


def run(cmd: Sequence[str]) -> None:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")
