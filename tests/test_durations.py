import json

import pytest

from wkvideo.durations import DurationsError, apply_durations, parse_line_durations, read_line_durations
from wkvideo.scenes import load_scenes


@pytest.mark.parametrize(
    "data",
    [
        [1.5, 2, 0.25],
        [{"text": "a", "duration": 1.5}, {"text": "b", "duration": 2}, {"duration": "0.25"}],
        [{"start": 0, "end": 1.5}, {"start": 1.5, "end": 3.5}, {"start": 3.5, "end": 3.75}],
        {"lines": [1.5, 2, 0.25]},
        {"durations": [1.5, 2, 0.25]},
    ],
)
def test_parse_shapes(data):
    assert parse_line_durations(data) == pytest.approx([1.5, 2.0, 0.25])


@pytest.mark.parametrize("data", [[], {}, [0], [-1], ["x"], [True], [{"text": "no duration"}]])
def test_parse_invalid(data):
    with pytest.raises(DurationsError):
        parse_line_durations(data)


def test_read_line_durations_file(tmp_path):
    path = tmp_path / "durations.json"
    path.write_text(json.dumps([{"text": "hi", "duration": 2.2}]), encoding="utf-8")
    assert read_line_durations(path) == [2.2]


def test_read_missing_file(tmp_path):
    with pytest.raises(DurationsError, match="not found"):
        read_line_durations(tmp_path / "missing.json")


def test_apply_durations(project):
    scenes = load_scenes(project / "scenes.json")
    apply_durations(scenes, [3.0, 4.5])
    assert [s.duration for s in scenes] == [3.0, 4.5]
    with pytest.raises(DurationsError, match="1 line durations for 2 scenes"):
        apply_durations(scenes, [1.0])
