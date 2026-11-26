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


def test_final_command_without_captions_copies_video():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"))
    assert ["-map", "1:a:0"] == cmd[cmd.index("1:a:0") - 1 : cmd.index("1:a:0") + 1]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-shortest" not in cmd and "-filter_complex" not in cmd


def test_final_command_overlays_captions():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"), Path("c.ffconcat"), 1244)
    i = cmd.index("c.ffconcat")
    assert cmd[i - 5 : i] == ["-f", "concat", "-safe", "0", "-i"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v][2:v]overlay=0:1244" in graph
    assert cmd[cmd.index("-c:v") + 1] == "libx264"


def test_overlay_y_expr_switches_per_scene():
    assert ffmpeg.overlay_y_expr([1474, 1474], [2.0]) == 1474
    expr = ffmpeg.overlay_y_expr([1474, 1600, 1474], [2.0, 5.0], fps=30)
    assert expr == "if(lt(t,1.9833),1474,if(lt(t,4.9833),1600,1474))"
    with pytest.raises(ValueError):
        ffmpeg.overlay_y_expr([1, 2], [])


def test_final_command_quotes_overlay_expression():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"), Path("c.ffconcat"),
                               "if(lt(t,1),10,20)")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=0:'if(lt(t,1),10,20)':" in graph


def test_concat_list_escapes_quotes(tmp_path):
    text = ffmpeg.concat_list([tmp_path / "it's.mp4"])
    assert text.startswith("file '") and r"'\''" in text


@requires_ffmpeg
def test_probe_duration(tmp_path):
    mp3 = tmp_path / "a.mp3"
    make_mp3(mp3, seconds=1.5)
    assert ffmpeg.probe_duration(mp3, ffmpeg.ffprobe_binary()) == pytest.approx(1.5, abs=0.1)


def test_probe_duration_bad_file(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not installed")
    with pytest.raises(ffmpeg.FFmpegError, match="could not read duration"):
        ffmpeg.probe_duration(bad, ffmpeg.ffprobe_binary())


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

    code = main([
        str(project), "-o", str(out), "--font", str(font),
        "--durations", str(project / "durations.json"), "--captions", "off",
    ])
    assert code == 0 and out.is_file()

    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert video["r_frame_rate"] == "30/1"
    # 0.5s + 0.5s of scenes, ~2.0s of audio: the last scene is extended so the
    # video ends 0.6s after the audio. Audio is not cut. The expected length
    # comes from the MP3 as ffprobe measures it, since ffmpeg versions differ
    # in whether they count the MP3 encoder padding (2.0s vs ~2.04s).
    voice_seconds = ffmpeg.probe_duration(project / "voice.mp3", ffmpeg.ffprobe_binary())
    expected_frames = ffmpeg.frame_count(0.5) + ffmpeg.frame_count(voice_seconds + 0.6 - 0.5)
    assert int(video["nb_frames"]) == expected_frames
    assert float(video["duration"]) == pytest.approx(voice_seconds + 0.6, abs=1.5 / 30)
    assert audio["codec_name"] == "aac"
    assert float(audio["duration"]) == pytest.approx(2.0, abs=0.1)


def test_cli_missing_folder(tmp_path, capsys):
    assert main([str(tmp_path / "missing")]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_cli_requires_single_mp3(project, font, capsys):
    assert main([str(project), "--font", str(font)]) == 1
    assert "exactly one .mp3" in capsys.readouterr().err
