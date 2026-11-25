import json

import pytest

from wkvideo.scenes import SceneError, load_scenes, parse_scenes


def test_load_scenes_resolves_images_and_aliases(project):
    scenes = load_scenes(project / "scenes.json")
    assert len(scenes) == 2
    assert scenes[0].image == project / "wide.jpg"
    assert scenes[0].duration == 0.5
    assert scenes[0].top_text == "Hello world"
    assert scenes[0].accent_text == "Lime accent"
    assert scenes[1].top_text == "Second"


def test_wrapped_scenes_key(project):
    scenes = parse_scenes({"scenes": [{"image": "wide.jpg"}]}, project)
    assert scenes[0].duration is None
    assert scenes[0].top_text == ""


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "non-empty"),
        ([{"duration": 1}], "missing 'image'"),
        ([{"image": "nope.jpg"}], "image not found"),
        ([{"image": "wide.jpg", "duration": 0}], "> 0"),
        ([{"image": "wide.jpg", "duration": "abc"}], "number"),
    ],
)
def test_invalid_scenes(project, data, message):
    with pytest.raises(SceneError, match=message):
        parse_scenes(data, project)


def test_invalid_json(tmp_path):
    (tmp_path / "scenes.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(SceneError, match="invalid JSON"):
        load_scenes(tmp_path / "scenes.json")
