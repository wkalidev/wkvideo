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
