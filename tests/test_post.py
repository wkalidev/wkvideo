import json

import pytest

from wkvideo.post import check_post, graphemes, main, render_md, x_length, youtube_tags_length

GOOD = {
    "youtube_title": "your phone stopped getting updates? read this first",
    "youtube_description": "updates patch security holes. check settings, software update.",
    "youtube_tags": ["phone updates", "phone security", "android"],
    "tiktok_caption": "check this #phonetips #cybersecurity #android #iphone #techtok",
    "instagram_caption": "check this #phonetips #cybersecurity #android #iphone #techtips",
    "facebook_caption": "when did your phone get its last update? drop the year below 👇",
    "bluesky_post": "your phone stopped getting updates? check settings > software update.",
    "x_post": "your phone stopped getting updates? those patches close holes hackers already know.",
}


def problems(**changes):
    return check_post({**GOOD, **changes})[1]


def test_good_post_passes():
    checks, errs = check_post(GOOD)
    assert errs == [] and len(checks) == 8


@pytest.mark.parametrize("field,limit", [
    ("youtube_title", 100), ("tiktok_caption", 2200), ("instagram_caption", 2200), ("bluesky_post", 300),
    ("x_post", 280),
])
def test_text_limits(field, limit):
    assert problems(**{field: "a" * limit}) == []
    assert problems(**{field: "a" * (limit + 1)}) == [f"{field}: {limit + 1}/{limit} " +
                                                      {"bluesky_post": "graphemes", "x_post": "weighted chars"}
                                                      .get(field, "chars")]


def test_x_counts_links_as_23_and_emoji_as_2():
    assert x_length("see https://example.com/a/very/long/path/that/is/long") == 4 + 23
    assert x_length("ok 👇") == 3 + 2
    assert x_length("👍🏽") == 2          # skin tone joins the emoji
    assert x_length("é") == 1
    assert problems(x_post="a" * 278 + "👇") == []
    assert problems(x_post="a" * 279 + "👇") == ["x_post: 281/280 weighted chars"]


def test_bluesky_counts_graphemes():
    assert graphemes("👨‍👩‍👧") == 1
    assert graphemes("é") == 1
    assert problems(bluesky_post="👇" * 300) == []


def test_youtube_tags_quote_tags_with_spaces():
    assert youtube_tags_length(["a b", "cd"]) == 5 + 2 + 1
    assert problems(youtube_tags=["x" * 250, "y" * 249]) == []
    assert problems(youtube_tags=["x" * 250, "y" * 250]) == ["youtube_tags: 501/500 chars"]


def test_youtube_description_is_counted_in_bytes():
    assert problems(youtube_description="é" * 2500) == []
    assert problems(youtube_description="é" * 2501) == ["youtube_description: 5002/5000 bytes"]


def test_youtube_rejects_angle_brackets():
    assert problems(youtube_title="a <b>") == ["youtube_title: YouTube doesn't allow < or >"]


def test_hashtag_limits():
    six = " ".join(f"#t{i}" for i in range(6))
    assert problems(instagram_caption=six) == ["instagram_caption: 6 hashtags (max 5)"]
    assert problems(tiktok_caption=six) == ["tiktok_caption: 6 hashtags (max 5)"]


def test_missing_and_unknown_fields():
    post = dict(GOOD)
    del post["x_post"]
    post["myspace"] = "hi"
    assert check_post(post)[1] == ["x_post: missing", "unknown fields: myspace"]


def test_render_md_shows_counts():
    checks, _ = check_post(GOOD)
    md = render_md("ep6", GOOD, checks)
    assert md.startswith("# ep6\n") and "## YouTube Shorts title (51/100 chars)" in md
    assert "phone updates, phone security, android" in md
    assert "## X (" in md and "/280 chars)" in md


def test_main_writes_post_md(tmp_path, capsys):
    (tmp_path / "post.json").write_text(json.dumps(GOOD), encoding="utf-8")
    assert main([str(tmp_path), "--note", "Sources: x"]) == 0
    md = (tmp_path / "post.md").read_text(encoding="utf-8")
    assert "- Sources: x" in md and "ok  X:" in capsys.readouterr().out


def test_main_refuses_to_write_when_a_limit_fails(tmp_path, capsys):
    (tmp_path / "post.json").write_text(json.dumps({**GOOD, "x_post": "a" * 300}), encoding="utf-8")
    assert main([str(tmp_path)]) == 1
    assert not (tmp_path / "post.md").exists()
    assert "x_post: 300/280" in capsys.readouterr().err
