import re
import subprocess
from pathlib import Path

import pytest

from wkvideo import ffmpeg
from wkvideo.cli import main

from conftest import requires_ffmpeg


def test_music_command_without_captions():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("voice.mp3"), Path("o.mp4"), music=Path("m.mp3"), duration=30.0)
    i = cmd.index("m.mp3")
    assert cmd[i - 3 : i] == ["-stream_loop", "-1", "-i"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[2:a]volume=-22dB,atrim=0:30.000" in graph
    assert "afade=t=out:st=29.000:d=1.0" in graph
    assert "[1:a]apad=whole_dur=30.000[vo]" in graph
    assert "[vo][m]amix=inputs=2:duration=first:normalize=0[a]" in graph
    assert cmd[cmd.index("[a]") - 1] == "-map"
    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_music_command_with_captions_uses_next_input():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("voice.mp3"), Path("o.mp4"), Path("c.ffconcat"), 100,
                               music=Path("m.mp3"), duration=10.0)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v][2:v]overlay" in graph and "[3:a]volume=-22dB" in graph
    assert ["-map", "[v]"] == cmd[cmd.index("[v]") - 1 : cmd.index("[v]") + 1]


def test_music_requires_duration():
    with pytest.raises(ValueError):
        ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"), music=Path("m.mp3"))


def lavfi_mp3(path, source):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source, "-c:a", "libmp3lame", "-b:a", "192k",
                    str(path)], check=True)


def mean_volume(path, start, length):
    err = subprocess.run(["ffmpeg", "-nostats", "-ss", str(start), "-t", str(length), "-i", str(path),
                          "-map", "0:a", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+|-inf) dB", err).group(1))


@requires_ffmpeg
def test_music_end_to_end(project, font, tmp_path):
    # Silent "voice" so the measured level is the music alone.
    lavfi_mp3(project / "voice.mp3", "anullsrc=r=44100:cl=mono:d=2.5")
    (tmp_path / "bg").mkdir()
    music = tmp_path / "bg" / "music.mp3"   # not in the input folder's top level
    lavfi_mp3(music, "sine=frequency=220:duration=0.7")  # shorter than the video: must loop
    out = tmp_path / "out.mp4"

    assert main([str(project), "-o", str(out), "--font", str(font), "--captions", "off", "--music", str(music)]) == 0

    # 0.5 + 0.4 s of scenes, extended to 2.5 + 0.6 = 3.1 s.
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout
    assert float(probe) == pytest.approx(3.1, abs=0.1)

    source = mean_volume(music, 0.1, 0.5)
    body = mean_volume(out, 0.2, 1.6)        # includes looped music past 0.7 s
    assert body - source == pytest.approx(-22, abs=1.5)
    looped = mean_volume(out, 1.0, 0.5)
    assert looped == pytest.approx(body, abs=1.5)
    # Fade-out: the last 0.2 s is far quieter than the body.
    assert mean_volume(out, 2.9, 0.2) < body - 10


def test_missing_music_file(project, font, tmp_path, capsys):
    (project / "voice.mp3").write_bytes(b"")
    code = main([str(project), "--font", str(font), "--captions", "off", "--music", str(tmp_path / "nope.mp3")])
    assert code == 1
    assert "music file not found" in capsys.readouterr().err
