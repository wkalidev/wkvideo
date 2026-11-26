"""ffmpeg command builders and runners."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Union

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
    frame_png: Path,
    pop_pattern: str,
    logo_png: Path,
    duration: float,
    out: Path,
    fps: int = DEFAULT_FPS,
    exe: str = "ffmpeg",
) -> List[str]:
    """First scene: pop-in headline and fast zoom, with a steady logo.

    The headline frames (an image sequence; overlay holds the last one) are
    composited onto the frame *before* zoompan, so the headline zooms with
    the accent line and the image. zoompan runs with d=1, one output frame
    per input frame. The logo is overlaid after the zoom so it stays put.
    """
    frames = frame_count(duration, fps)
    uw, uh = UPSCALE_SIZE
    ow, oh = OUT_SIZE
    graph = (
        f"[0:v][1:v]overlay=0:0:eof_action=repeat:format=auto[c];"
        f"[c]scale={uw}:{uh}:flags=lanczos,"
        f"zoompan=z='{zoom_expr(frames, fps, hook=True)}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={ow}x{oh}:fps={fps}[z];"
        f"[z][2:v]overlay=0:0:eof_action=repeat:format=auto,format=yuv420p[v]"
    )
    return [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", str(fps), "-i", str(frame_png),
        "-framerate", str(fps), "-i", pop_pattern,
        "-i", str(logo_png),
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


def overlay_y_expr(tops: Sequence[int], starts: Sequence[float], fps: int = DEFAULT_FPS) -> Union[int, str]:
    """Overlay ``y`` that switches to ``tops[i]`` when scene ``i`` starts.

    ``starts`` are the start times of scenes 1..n-1 (scene 0 starts at 0).
    Each switch happens half a frame early so the first frame of a scene gets
    its position despite timestamp rounding. Returns a plain int if all
    positions are equal.
    """
    if len(starts) != len(tops) - 1:
        raise ValueError("need one start time per scene after the first")
    if len(set(tops)) == 1:
        return tops[0]
    expr = str(tops[-1])
    for top, start in zip(reversed(tops[:-1]), reversed(list(starts))):
        expr = f"if(lt(t,{start - 0.5 / fps:.4f}),{top},{expr})"
    return expr


MUSIC_GAIN_DB = -22
MUSIC_FADE_SECONDS = 1.0


def final_command(
    video: Path,
    voice: Path,
    out: Path,
    captions: Optional[Path] = None,
    caption_y: Union[int, str] = 0,
    fps: int = DEFAULT_FPS,
    exe: str = "ffmpeg",
    music: Optional[Path] = None,
    duration: Optional[float] = None,
) -> List[str]:
    """Add the voiceover, and optionally captions and background music.

    Captions are overlaid at ``caption_y``, a pixel row or a time expression
    from :func:`overlay_y_expr` (the video is then re-encoded; otherwise it
    is copied). Music is looped if needed, lowered by
    MUSIC_GAIN_DB, cut to ``duration`` (the video length) and faded out over
    the last MUSIC_FADE_SECONDS, then mixed under the voice.
    """
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(voice)]
    graph: List[str] = []
    next_input = 2
    video_map, audio_map = "0:v:0", "1:a:0"

    if captions is not None:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(captions)]
        y = caption_y if isinstance(caption_y, int) else f"'{caption_y}'"
        graph.append(f"[0:v][{next_input}:v]overlay=0:{y}:eof_action=pass:format=auto,format=yuv420p[v]")
        video_map = "[v]"
        next_input += 1

    if music is not None:
        if duration is None:
            raise ValueError("duration is required when mixing music")
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fade_start = max(duration - MUSIC_FADE_SECONDS, 0.0)
        graph.append(
            f"[{next_input}:a]volume={MUSIC_GAIN_DB}dB,atrim=0:{duration:.3f},asetpts=N/SR/TB,"
            f"afade=t=out:st={fade_start:.3f}:d={MUSIC_FADE_SECONDS}[m];"
            # Pad the voice to the video length and end the mix on it:
            # amix's duration=longest drops the final frames.
            f"[1:a]apad=whole_dur={duration:.3f}[vo];"
            f"[vo][m]amix=inputs=2:duration=first:normalize=0[a]"
        )
        audio_map = "[a]"
        next_input += 1

    if graph:
        cmd += ["-filter_complex", ";".join(graph)]
    cmd += ["-map", video_map, "-map", audio_map]
    if captions is not None:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(fps)]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    return cmd


def run(cmd: Sequence[str]) -> None:
    proc = subprocess.run(list(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()}")
