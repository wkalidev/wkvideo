from pathlib import Path

import pytest

from wkvideo.render import BUNDLED_FONT, HEIGHT, LIME, WIDTH, find_font, fit_box, render_frame, wrap_text, load_font


def close(a, b, tol=12):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_fit_box_width_and_height_limits():
    assert fit_box((1600, 900), 1037, 1284) == (1037, 583)
    # Very tall image: height-limited, narrower than 96%.
    w, h = fit_box((500, 2000), 1037, 1284)
    assert h == 1284 and w < 1037


def test_frame_layout(project, font):
    frame = render_frame(project / "wide.jpg", "Hello world", "Lime accent", font)
    assert frame.size == (WIDTH, HEIGHT)
    assert frame.mode == "RGB"

    # Foreground starts at 30% height (576px) and spans 96% width.
    img_left = (WIDTH - round(WIDTH * 0.96)) // 2
    assert close(frame.getpixel((WIDTH // 2, 580)), (200, 40, 40))
    assert close(frame.getpixel((img_left + 2, 700)), (200, 40, 40))
    assert not close(frame.getpixel((WIDTH // 2, 570)), (200, 40, 40))

    # Background below the image is the darkened, blurred image.
    r, g, b = frame.getpixel((WIDTH // 2, HEIGHT - 40))
    assert 60 < r < 120 and g < 40 and b < 40


def test_text_and_logo_colours(project, font):
    frame = render_frame(project / "wide.jpg", "Hello world", "Lime accent", font)
    band = frame.crop((0, 0, WIDTH, 576))
    colours = {c for _, c in band.getcolors(band.width * band.height)}
    assert (255, 255, 255) in colours  # white top text
    assert LIME in colours              # lime accent / logo
    logo = frame.crop((WIDTH - 300, 0, WIDTH, 140))
    assert LIME in {c for _, c in logo.getcolors(logo.width * logo.height)}


def test_long_text_wraps_within_margins(font):
    f = load_font(str(font), 84)
    lines = wrap_text("word " * 40, f, 960)
    assert len(lines) > 1
    assert all(f.getlength(line) <= 960 for line in lines)


def test_find_font_explicit_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_font(str(tmp_path / "nope.ttf"))


def test_bundled_inter_is_default(monkeypatch):
    monkeypatch.delenv("WKVIDEO_FONT", raising=False)
    path = find_font()
    assert path == BUNDLED_FONT and path.is_file()
    assert (path.parent / "OFL.txt").is_file()
    assert load_font(str(path), 40).getname() == ("Inter", "Bold")


def test_font_overrides(tmp_path, monkeypatch, font):
    custom = tmp_path / "custom.ttf"
    custom.write_bytes(Path(font).read_bytes())
    monkeypatch.setenv("WKVIDEO_FONT", str(custom))
    assert find_font() == custom
    monkeypatch.delenv("WKVIDEO_FONT")
    assert find_font(str(custom)) == custom


# --- shrink-before-wrap and orphan handling ---------------------------------

from wkvideo.render import MIN_SINGLE_LINE_SIZE, TOP_SIZE, _layout_block, fit_text

MAX_W = WIDTH - 2 * 60
BAND_H = 392


def text_of_width(font_path, size, target):
    """Repeat a word until the text is at least ``target`` px wide at ``size``."""
    font = load_font(str(font_path), size)
    words = ["word"]
    while font.getlength(" ".join(words)) < target:
        words.append("word")
    return " ".join(words)


def test_short_text_keeps_full_size(font):
    size, lines = fit_text("check this", str(font), MAX_W, TOP_SIZE)
    assert size == TOP_SIZE and lines == ["check this"]


def test_shrinks_to_stay_on_one_line(font):
    # Too wide at 84px, but fits at 84 * 0.8 = ~67px.
    text = text_of_width(font, TOP_SIZE, MAX_W * 1.15)
    size, lines = fit_text(text, str(font), MAX_W, TOP_SIZE)
    assert lines == [text]
    assert MIN_SINGLE_LINE_SIZE <= size < TOP_SIZE


def test_wraps_at_60px_only_when_needed(font):
    text = text_of_width(font, MIN_SINGLE_LINE_SIZE, MAX_W * 1.6)
    size, lines = fit_text(text, str(font), MAX_W, TOP_SIZE)
    assert size == MIN_SINGLE_LINE_SIZE
    assert len(lines) == 2
    f = load_font(str(font), size)
    assert all(f.getlength(l) <= MAX_W for l in lines)


def test_no_single_word_on_last_line(font):
    f = load_font(str(font), 60)
    width = f.getlength("alpha bravo charlie") + 4
    # Greedy would give ["alpha bravo charlie", "delta"].
    assert wrap_text("alpha bravo charlie delta", f, width) == ["alpha bravo", "charlie delta"]


def test_orphan_left_if_unavoidable(font):
    f = load_font(str(font), 60)
    width = f.getlength("supercalifragilistic") + 4
    # Joining the last two words can never fit, so the lone word stays.
    assert wrap_text("supercalifragilistic expialidocious", f, width) == [
        "supercalifragilistic", "expialidocious",
    ]


def test_wrapped_text_never_ends_in_orphan(font):
    f = load_font(str(font), 60)
    for n in range(4, 30):
        lines = wrap_text(" ".join(f"w{i}" for i in range(n)), f, 300)
        if len(lines) > 1:
            assert len(lines[-1].split()) > 1, lines


def test_real_lines_shrink_or_wrap_cleanly(font):
    # Fits at >= 60px: stays on one line.
    top_size, accent_size, top_lines, accent_lines, *_ = _layout_block(
        "the offer sounds perfect", "remote, any role, flexible hours", str(font), MAX_W, BAND_H
    )
    assert top_lines == ["the offer sounds perfect"]
    assert accent_lines == ["remote, any role, flexible hours"]
    assert MIN_SINGLE_LINE_SIZE <= accent_size <= top_size

    # Too wide even at 60px: wraps at 60px, two words on the last line.
    _, accent_size, _, accent_lines, *_ = _layout_block(
        "fake recruiters are hitting devs", "old accounts, new photo, big logo", str(font), MAX_W, BAND_H
    )
    assert accent_size == MIN_SINGLE_LINE_SIZE
    assert accent_lines == ["old accounts, new photo,", "big logo"]


def test_accent_never_larger_than_headline(font):
    top = text_of_width(font, TOP_SIZE, MAX_W * 1.2)  # forces the headline to shrink
    top_size, accent_size, *_ = _layout_block(top, "short", str(font), MAX_W, BAND_H)
    assert accent_size <= top_size
