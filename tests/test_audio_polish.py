import re
import subprocess
from pathlib import Path

import pytest

from wkvideo import ffmpeg
from wkvideo.cli import main

from conftest import requires_ffmpeg

MEASURED = {"input_i": "-27.61", "input_tp": "-9.20", "input_lra": "3.10", "input_thresh": "-37.90",
            "target_offset": "0.40"}


def audio_graph_of(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_polish_compresses_and_de_esses_the_voice_then_normalizes_the_mix():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("voice.mp3"), Path("o.mp4"), polish=True, loudness=MEASURED)
    graph = audio_graph_of(cmd)
    assert graph.startswith("[1:a]acompressor=threshold=-21dB:ratio=2.5")
    assert "deesser=i=0.35" in graph
    assert "loudnorm=I=-14:TP=-1:LRA=11:measured_I=-27.61:measured_TP=-9.20" in graph
    assert "offset=0.40:linear=true" in graph and graph.endswith("aresample=48000[a]")
    assert cmd[cmd.index("[a]") - 1] == "-map"


def test_polish_with_music_normalizes_after_the_mix():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("voice.mp3"), Path("o.mp4"), music=Path("m.mp3"),
                               duration=20.0, polish=True, loudness=MEASURED)
    graph = audio_graph_of(cmd)
    assert "[vp]apad=whole_dur=20.000[vo]" in graph            # the voice is polished before the mix
    assert "[vo][m]amix=inputs=2:duration=first:normalize=0[mx]" in graph
    assert "[mx]loudnorm=I=-14" in graph


def test_polish_without_a_measurement_skips_loudnorm():
    graph = audio_graph_of(ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"), polish=True))
    assert "loudnorm" not in graph and graph.endswith("s=o[a]")


def test_no_polish_leaves_the_voice_untouched():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"))
    assert "-filter_complex" not in cmd and cmd[cmd.index("1:a:0") - 1] == "-map"


def test_measure_command_prints_json_and_outputs_nothing():
    cmd = ffmpeg.measure_loudness_command(Path("voice.mp3"), music=Path("m.mp3"), duration=10.0)
    graph = audio_graph_of(cmd)
    assert graph.startswith("[0:a]acompressor") and "[1:a]volume=-22dB" in graph
    assert "loudnorm=I=-14:TP=-1:LRA=11:print_format=json" in graph
    assert cmd[-3:] == ["-f", "null", "-"]


def test_parse_loudnorm_json():
    stderr = 'noise {not json}\n[Parsed_loudnorm_2 @ 0x1]\n{\n "input_i" : "-27.61",\n "input_tp" : "-9.20",\n' \
             ' "input_lra" : "3.10",\n "input_thresh" : "-37.90",\n "output_i" : "-14.2",\n' \
             ' "normalization_type" : "dynamic",\n "target_offset" : "0.40"\n}\n'
    assert ffmpeg.parse_loudnorm_json(stderr) == MEASURED
    silent = stderr.replace('"-27.61"', '"-inf"')
    assert ffmpeg.parse_loudnorm_json(silent) is None
    assert ffmpeg.parse_loudnorm_json("no json here") is None


def ebur128(path):
    err = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-map", "0:a", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = err[err.rfind("Summary:"):]
    integrated = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))
    peak = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1))
    return integrated, peak


@requires_ffmpeg
def test_rendered_video_is_at_minus_14_lufs(project, font, tmp_path):
    # A quiet "voice" (around -33 LUFS) with some level changes.
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=4,volume='if(lt(t,2),0.03,0.06)':eval=frame",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(project / "voice.mp3")], check=True)
    out = tmp_path / "out.mp4"
    assert main([str(project), "-o", str(out), "--font", str(font), "--captions", "off"]) == 0
    integrated, peak = ebur128(out)
    assert integrated == pytest.approx(-14.0, abs=1.0)
    assert peak <= -0.9
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout.strip()
    assert rate == "48000"

    flat = tmp_path / "flat.mp4"
    assert main([str(project), "-o", str(flat), "--font", str(font), "--captions", "off", "--polish", "off"]) == 0
    assert ebur128(flat)[0] < -25          # off: the level is left alone
