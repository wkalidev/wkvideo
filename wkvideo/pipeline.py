"""End-to-end build: scenes -> frames -> zoomed clips -> concatenated MP4 -> captions + audio."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import ffmpeg
from .captions import caption_center, caption_y, max_image_bottom, write_caption_track
from .hook import FAST_ZOOM_AMOUNT, write_pop_frames
from .render import HEIGHT, IMAGE_TOP, image_bottom, image_size, render_frame, render_logo_layer, render_overlay
from .scenes import Scene
from .transcribe import DEFAULT_MODEL, Word, load_or_transcribe


def find_audio(folder: Path) -> Path:
    mp3s = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() == ".mp3")
    if len(mp3s) != 1:
        found = ", ".join(p.name for p in mp3s) or "none"
        raise FileNotFoundError(f"expected exactly one .mp3 in {folder} (found: {found}); use --audio")
    return mp3s[0]


AUDIO_TAIL = 0.6  # seconds of video kept after the voiceover ends


def extend_to_audio(scenes: Sequence[Scene], audio_seconds: float, tail: float = AUDIO_TAIL) -> float:
    """Lengthen the last scene so the video ends ``tail`` seconds after the audio.

    Returns the number of seconds added. Scenes that already run past that
    point are left alone (the extra video plays after the audio, silent).
    """
    total = sum(s.duration for s in scenes)
    extra = audio_seconds + tail - total
    if extra <= 0:
        return 0.0
    scenes[-1].duration += extra
    return extra


def max_zoom(hook: bool) -> float:
    """Largest zoom a scene reaches (the hook scene zooms further)."""
    return ffmpeg.ZOOM_END + (FAST_ZOOM_AMOUNT if hook else 0.0)


def zoomed_y(y: float, zoom: float) -> float:
    """Where row ``y`` of a frame lands at ``zoom`` (zoompan zooms about the centre)."""
    return HEIGHT / 2 + (y - HEIGHT / 2) * zoom


def unzoomed_y(y: float, zoom: float) -> float:
    """The frame row that lands on ``y`` at ``zoom``."""
    return HEIGHT / 2 + (y - HEIGHT / 2) / zoom


def build_video(
    scenes: Sequence[Scene],
    audio: Path,
    output: Path,
    font: Path,
    fps: int = ffmpeg.DEFAULT_FPS,
    work_dir: Optional[Path] = None,
    log: Callable[[str], None] = print,
    captions: bool = True,
    whisper_model: str = DEFAULT_MODEL,
    words: Optional[List[Word]] = None,
    music: Optional[Path] = None,
) -> Path:
    """Render the video. ``words`` skips transcription (used by tests)."""
    if music is not None and not Path(music).is_file():
        raise FileNotFoundError(f"music file not found: {music}")
    missing = [i for i, s in enumerate(scenes, start=1) if not s.duration]
    if missing:
        raise ValueError(f"scenes without a duration: {missing}; set 'duration' or pass --durations")

    exe = ffmpeg.ffmpeg_binary()
    audio_seconds = ffmpeg.probe_duration(Path(audio), ffmpeg.ffprobe_binary())
    total = sum(s.duration for s in scenes)
    added = extend_to_audio(scenes, audio_seconds)
    if added:
        log(f"Audio is {audio_seconds:.2f}s: extending last scene by {added:.2f}s")
    elif total > audio_seconds + AUDIO_TAIL:
        log(f"Note: scenes ({total:.2f}s) run past the audio ({audio_seconds:.2f}s) + {AUDIO_TAIL}s")
    if captions and words is None:
        words = load_or_transcribe(Path(audio), whisper_model, log=log)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    keep = work_dir is not None
    work = Path(work_dir) if keep else Path(tempfile.mkdtemp(prefix="wkvideo_"))
    work.mkdir(parents=True, exist_ok=True)

    try:
        clips: List[Path] = []
        caption_tops: List[int] = []
        for i, scene in enumerate(scenes, start=1):
            log(f"[{i}/{len(scenes)}] {scene.media.name} ({scene.duration:.2f}s)")
            frame_png = work / f"scene_{i:03d}.png"
            clip = work / f"scene_{i:03d}.mp4"
            if scene.video is not None:
                # Video scenes aren't zoomed: the footage moves on its own.
                limit = max_image_bottom(font) if captions else None
                fg = image_size(ffmpeg.probe_video_size(scene.video, ffmpeg.ffprobe_binary()), limit)
                if captions:
                    caption_tops.append(caption_y(caption_center(IMAGE_TOP + fg[1], font)))
                hook = i == 1
                render_overlay(
                    scene.top_text, scene.accent_text, font, draw_top=not hook, draw_logo=not hook,
                ).save(frame_png)
                pop = logo = None
                if hook:
                    pop = write_pop_frames(scene.top_text, scene.accent_text, font, work / "pop", fps)
                    logo = work / "logo.png"
                    render_logo_layer(font).save(logo)
                ffmpeg.run(ffmpeg.video_scene_command(
                    scene.video, frame_png, fg, IMAGE_TOP, scene.duration, clip, fps, exe,
                    pop_pattern=pop, logo_png=logo,
                ))
                clips.append(clip)
                continue
            # With captions, keep the image high enough (even fully zoomed in)
            # for the captions to fit below it, and place them under it.
            limit = None
            if captions:
                zoom = max_zoom(hook=i == 1)
                limit = unzoomed_y(max_image_bottom(font), zoom)
                bottom = zoomed_y(image_bottom(scene.image, limit), zoom)
                caption_tops.append(caption_y(caption_center(bottom, font)))
            if i == 1:
                # Hook punch: headline pops in, background zooms in fast.
                render_frame(
                    scene.image, scene.top_text, scene.accent_text, font, draw_top=False, draw_logo=False,
                    image_max_bottom=limit,
                ).save(frame_png)
                pop = write_pop_frames(scene.top_text, scene.accent_text, font, work / "pop", fps)
                logo = work / "logo.png"
                render_logo_layer(font).save(logo)
                ffmpeg.run(ffmpeg.hook_scene_command(frame_png, pop, logo, scene.duration, clip, fps, exe))
            else:
                render_frame(
                    scene.image, scene.top_text, scene.accent_text, font, image_max_bottom=limit
                ).save(frame_png)
                ffmpeg.run(ffmpeg.scene_command(frame_png, scene.duration, clip, fps, exe))
            clips.append(clip)

        list_file = work / "clips.txt"
        list_file.write_text(ffmpeg.concat_list(clips), encoding="utf-8")
        silent = work / "video_silent.mp4"
        ffmpeg.run(ffmpeg.concat_command(list_file, silent, exe))

        frames = [ffmpeg.frame_count(s.duration, fps) for s in scenes]
        video_seconds = sum(frames) / fps
        track, overlay_y = None, 0
        if captions:
            log(f"Rendering captions ({len(words)} words)")
            track = write_caption_track(words, font, work / "captions", video_seconds)
            starts = [sum(frames[:k]) / fps for k in range(1, len(frames))]
            overlay_y = ffmpeg.overlay_y_expr(caption_tops, starts, fps)
        log(f"Muxing audio {Path(audio).name}" + (f" with music {Path(music).name}" if music else ""))
        ffmpeg.run(ffmpeg.final_command(
            silent, Path(audio), output, track, overlay_y, fps, exe, music=music, duration=video_seconds,
        ))
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return output
