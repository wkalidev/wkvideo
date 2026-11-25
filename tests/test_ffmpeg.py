import json
import shutil
import subprocess
from pathlib import Path

import pytest

from wkvideo import ffmpeg
from wkvideo.cli import main

from conftest import make_mp3, requires_ffmpeg


def test_frame_count():
    assert ffmpeg.frame_count(2.0, 30) == 60
    assert ffmpeg.frame_count(0.01, 30) == 1


def test_zoompan_filter_upscales_and_zooms():
    vf = ffmpeg.zoompan_filter(91, 30)
    assert vf.startswith("scale=2160:3840")
    assert "zoompan=" in vf
    assert "min(1.0+0.0004444444*on,1.04)" in vf
    assert ":d=91:s=1080x1920:fps=30" in vf


def test_scene_command():
    cmd = ffmpeg.scene_command(Path("f.png"), 1.5, Path("o.mp4"), 30)
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-frames:v") + 1] == "45"
    assert cmd[-1] == "o.mp4"


def test_mux_command_maps_audio():
    cmd = ffmpeg.mux_command(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"))
    assert ["-map", "1:a:0"] == cmd[cmd.index("1:a:0") - 1 : cmd.index("1:a:0") + 1]
    assert "-shortest" in cmd


def test_concat_list_escapes_quotes(tmp_path):
    text = ffmpeg.concat_list([tmp_path / "it's.mp4"])
    assert text.startswith("file '") and r"'\''" in text


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@requires_ffmpeg
@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_end_to_end(project, font, tmp_path):
    make_mp3(project / "voice.mp3", seconds=2.0)
    (project / "durations.json").write_text(json.dumps([0.5, 0.5]), encoding="utf-8")
    out = tmp_path / "out" / "video.mp4"

    code = main([str(project), "-o", str(out), "--font", str(font), "--durations", str(project / "durations.json")])
    assert code == 0 and out.is_file()

    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert video["r_frame_rate"] == "30/1"
    assert int(video["nb_frames"]) == 30
    assert audio["codec_name"] == "aac"
    assert float(info["format"]["duration"]) == pytest.approx(1.0, abs=0.1)


def test_cli_missing_folder(tmp_path, capsys):
    assert main([str(tmp_path / "missing")]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_cli_requires_single_mp3(project, font, capsys):
    assert main([str(project), "--font", str(font)]) == 1
    assert "exactly one .mp3" in capsys.readouterr().err
