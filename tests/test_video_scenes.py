import json
import subprocess
from pathlib import Path

import pytest

from wkvideo import ffmpeg
from wkvideo.pipeline import build_video
from wkvideo.render import HEIGHT, WIDTH, render_overlay
from wkvideo.scenes import SceneError, load_scenes, parse_scenes

from conftest import make_mp3, requires_ffmpeg


def test_video_scene_is_parsed(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"x")
    scenes = parse_scenes([{"video": "clip.mp4", "duration": 2, "top": "hi"}], tmp_path)
    assert scenes[0].video == tmp_path / "clip.mp4" and scenes[0].image is None
    assert scenes[0].media == scenes[0].video


def test_query_without_a_file_points_to_wkvideo_assets(tmp_path):
    with pytest.raises(SceneError, match="wkvideo assets"):
        parse_scenes([{"image_query": "phone"}], tmp_path)


def test_video_must_have_a_video_suffix(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    with pytest.raises(SceneError, match="video must be"):
        parse_scenes([{"video": "a.jpg"}], tmp_path)


def test_extra_keys_like_credit_are_ignored(tmp_path, project):
    data = json.loads((project / "scenes.json").read_text(encoding="utf-8"))
    data[0]["image_query"] = "phone"
    data[0]["credit"] = {"provider": "Pixabay", "id": "1"}
    (project / "scenes.json").write_text(json.dumps(data), encoding="utf-8")
    assert load_scenes(project / "scenes.json")[0].image.name == "wide.jpg"


def test_overlay_is_a_transparent_full_frame(font):
    layer = render_overlay("headline", "accent", font)
    assert layer.size == (WIDTH, HEIGHT) and layer.mode == "RGBA"
    assert layer.getpixel((WIDTH // 2, HEIGHT - 10))[3] == 0      # transparent below the text
    assert layer.getbbox()[3] < HEIGHT * 0.3                         # text stays in the top band


def test_video_scene_command():
    cmd = ffmpeg.video_scene_command(Path("v.mp4"), Path("o.png"), (720, 1280), 576, 2.0, Path("out.mp4"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-stream_loop") + 1] == "-1"           # short clips loop
    assert "scale=720:1280" in graph and "overlay=(W-w)/2:576" in graph and "gblur" in graph
    assert "-an" in cmd and cmd[cmd.index("-frames:v") + 1] == "60"


def test_hook_video_scene_adds_pop_and_logo():
    cmd = ffmpeg.video_scene_command(Path("v.mp4"), Path("o.png"), (720, 1280), 576, 2.0, Path("out.mp4"),
                                     pop_pattern="pop/%04d.png", logo_png=Path("logo.png"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[2:v]overlay" in graph and "[3:v]overlay" in graph and "pop/%04d.png" in cmd


@requires_ffmpeg
def test_render_with_a_video_scene(project, font, tmp_path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=360x640:rate=30:duration=0.4", "-pix_fmt", "yuv420p", str(project / "clip.mp4")],
                   check=True)
    data = json.loads((project / "scenes.json").read_text(encoding="utf-8"))
    data.append({"video": "clip.mp4", "duration": 0.8, "top": "video scene"})  # longer than the clip: loops
    data.insert(0, {"video": "clip.mp4", "duration": 0.5, "top": "hook video"})
    (project / "scenes.json").write_text(json.dumps(data), encoding="utf-8")
    assert ffmpeg.probe_video_size(project / "clip.mp4") == (360, 640)
    make_mp3(project / "v.mp3", 2.0)
    out = build_video(load_scenes(project / "scenes.json"), project / "v.mp3", tmp_path / "o.mp4", font,
                      captions=False, log=lambda m: None)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,nb_frames", "-of", "json", str(out)],
                           capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (1080, 1920)
    assert ffmpeg.probe_duration(out) == pytest.approx(2.6, abs=0.1)
