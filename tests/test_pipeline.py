from pathlib import Path

import pytest

from wkvideo.pipeline import AUDIO_TAIL, extend_to_audio
from wkvideo.scenes import Scene


def scenes(*durations):
    return [Scene(image=Path(f"{i}.jpg"), duration=d) for i, d in enumerate(durations)]


def test_tail_is_point_six_seconds():
    assert AUDIO_TAIL == 0.6


def test_extends_last_scene_to_audio_plus_tail():
    s = scenes(2.0, 3.0)
    added = extend_to_audio(s, audio_seconds=7.0)
    assert added == pytest.approx(2.6)
    assert [x.duration for x in s] == pytest.approx([2.0, 5.6])
    assert sum(x.duration for x in s) == pytest.approx(7.6)


def test_exact_fit_adds_nothing():
    s = scenes(4.0, 3.6)
    assert extend_to_audio(s, audio_seconds=7.0) == 0.0
    assert [x.duration for x in s] == [4.0, 3.6]


def test_longer_video_is_not_shortened():
    s = scenes(5.0, 5.0)
    assert extend_to_audio(s, audio_seconds=7.0) == 0.0
    assert [x.duration for x in s] == [5.0, 5.0]
