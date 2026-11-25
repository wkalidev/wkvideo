import json
import shutil
import subprocess

import pytest
from PIL import Image


@pytest.fixture
def project(tmp_path):
    """A minimal input folder: two images and a scenes.json."""
    Image.new("RGB", (1600, 900), (200, 40, 40)).save(tmp_path / "wide.jpg")
    Image.new("RGB", (800, 1400), (40, 200, 40)).save(tmp_path / "tall.png")
    scenes = [
        {"image": "wide.jpg", "duration": 0.5, "top": "Hello world", "accent": "Lime accent"},
        {"image": "tall.png", "duration": 0.4, "top_text": "Second", "accent_text": ""},
    ]
    (tmp_path / "scenes.json").write_text(json.dumps(scenes), encoding="utf-8")
    return tmp_path


@pytest.fixture
def font():
    from wkvideo.render import find_font
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return find_font()


requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_mp3(path, seconds=1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "libmp3lame", str(path)],
        check=True,
    )
