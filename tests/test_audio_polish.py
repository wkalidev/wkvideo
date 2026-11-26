import re
import subprocess
from pathlib import Path

import pytest

from wkvideo import ffmpeg
from wkvideo.cli import main

from conftest import requires_ffmpeg

def audio_graph_of(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_polish_compresses_and_de_esses_the_voice_then_sets_the_level():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("voice.mp3"), Path("o.mp4"), polish=True, gain_db=6.5)
    graph = audio_graph_of(cmd)
    assert graph.startswith("[1:a]acompressor=threshold=-21dB:ratio=2.5")
    assert "deesser=i=0.35" in graph
    # Fixed gain, then a limiter at 4x the sample rate, 0.3 dB under -1 dBTP.
    assert "volume=6.50dB,aresample=192000,alimiter=limit=0.8610:" in graph
    assert graph.endswith("aresample=48000[a]") and "loudnorm" not in graph
    assert cmd[cmd.index("[a]") - 1] == "-map"


def test_polish_with_music_sets_the_level_after_the_mix():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("voice.mp3"), Path("o.mp4"), music=Path("m.mp3"),
                               duration=20.0, polish=True, gain_db=3.0)
    graph = audio_graph_of(cmd)
    assert "[1:a]acompressor" in graph and "s=o,apad=whole_dur=20.000[vo]" in graph   # voice polished first
    assert "[vo][m]amix=inputs=2:duration=first:normalize=0,volume=3.00dB,aresample=192000" in graph


def test_polish_without_a_gain_only_compresses_and_de_esses():
    graph = audio_graph_of(ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"), polish=True))
    assert "volume" not in graph and "alimiter" not in graph and graph.endswith("s=o[a]")


def test_no_polish_leaves_the_voice_untouched():
    cmd = ffmpeg.final_command(Path("v.mp4"), Path("a.mp3"), Path("o.mp4"))
    assert "-filter_complex" not in cmd and cmd[cmd.index("1:a:0") - 1] == "-map"


def test_measure_command_prints_json_and_outputs_nothing():
    cmd = ffmpeg.measure_loudness_command(Path("voice.mp3"), music=Path("m.mp3"), duration=10.0, gain_db=2.0)
    graph = audio_graph_of(cmd)
    assert graph.startswith("[1:a]volume=-22dB") and "[0:a]acompressor" in graph
    assert "volume=2.00dB" in graph and graph.endswith("loudnorm=I=-14:TP=-1:print_format=json[a]")
    assert cmd[-3:] == ["-f", "null", "-"]


def test_parse_loudnorm_json():
    stderr = ('noise {not json}\n[Parsed_loudnorm_2 @ 0x1]\n{\n "input_i" : "-27.61",\n "input_tp" : "-9.20",\n'
              ' "input_lra" : "3.10",\n "input_thresh" : "-37.90",\n "target_offset" : "0.40"\n}\n')
    assert ffmpeg.parse_loudnorm_json(stderr) == {"lufs": -27.61, "true_peak": -9.2}
    assert ffmpeg.parse_loudnorm_json(stderr.replace('"-27.61"', '"-inf"')) is None
    assert ffmpeg.parse_loudnorm_json("no json here") is None


def test_loudness_gain_corrects_for_the_limiter():
    calls = []

    def fake_measure(voice, exe, music, duration, polish, gain):
        calls.append(gain)
        if gain is None:
            return {"lufs": -20.0, "true_peak": 1.5}
        return {"lufs": -20.0 + gain - 0.4 * gain / 6, "true_peak": -1.3}   # the limiter eats some level

    gain, before, after = ffmpeg.loudness_gain(Path("v.mp3"), measure=fake_measure)
    assert before == -20.0 and abs(after["lufs"] + 14) < ffmpeg.LOUDNESS_TOLERANCE
    assert calls[:2] == [None, 6.0] and gain > 6.0


def test_loudness_gain_leaves_silence_alone():
    assert ffmpeg.loudness_gain(Path("v.mp3"), measure=lambda *a: None) is None


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
    assert integrated == pytest.approx(-14.0, abs=0.5)
    assert peak <= -1.0
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout.strip()
    assert rate == "48000"

    flat = tmp_path / "flat.mp4"
    assert main([str(project), "-o", str(flat), "--font", str(font), "--captions", "off", "--polish", "off"]) == 0
    assert ebur128(flat)[0] < -25          # off: the level is left alone
