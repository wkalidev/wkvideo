import subprocess

import pytest
from PIL import Image, ImageChops

from wkvideo import ffmpeg
from wkvideo.hook import (
    FAST_ZOOM_AMOUNT, POP_START_SCALE, pop_frame_count, pop_scale, pop_scales, scaled_layer, write_pop_frames,
)
from wkvideo.render import IMAGE_TOP, WIDTH, render_frame, render_top_layer

from conftest import make_mp3, requires_ffmpeg


def white_bbox(img):
    """Bounding box of pure-white pixels (the headline fill)."""
    r, g, b = [c.point(lambda v: 255 if v > 245 else 0) for c in img.convert("RGB").split()]
    return ImageChops.multiply(ImageChops.multiply(r, g), b).getbbox()


def test_pop_scale_curve():
    assert pop_scale(0) == pytest.approx(POP_START_SCALE)
    assert pop_scale(0.25) == pytest.approx(1.0)
    assert pop_scale(1.0) == pytest.approx(1.0)
    scales = pop_scales(30)
    assert len(scales) == pop_frame_count(30) == 9
    assert all(a > b for a, b in zip(scales, scales[1:]))
    assert scales[-1] == pytest.approx(1.0)


def test_scaled_layer_grows_about_centre(font):
    layer = render_top_layer("Hello", "", font)
    big = scaled_layer(layer, 1.3)
    b0, b1 = layer.getbbox(), big.getbbox()
    assert (b1[2] - b1[0]) == pytest.approx((b0[2] - b0[0]) * 1.3, abs=3)
    assert (b1[0] + b1[2]) / 2 == pytest.approx((b0[0] + b0[2]) / 2, abs=2)
    assert big.size == layer.size


def test_headline_layer_matches_full_frame(project, font):
    full = render_frame(project / "wide.jpg", "Hello world", "Lime accent", font)
    no_top = render_frame(project / "wide.jpg", "Hello world", "Lime accent", font, draw_top=False)
    layer = render_top_layer("Hello world", "Lime accent", font)
    assert layer.size == (WIDTH, IMAGE_TOP) and layer.mode == "RGBA"
    assert white_bbox(no_top.crop((0, 0, WIDTH, IMAGE_TOP))) is None      # headline removed
    composed = no_top.copy()
    composed.paste(layer, (0, 0), layer)
    assert white_bbox(composed) == white_bbox(full)                         # same position


def test_write_pop_frames(tmp_path, font):
    pattern = write_pop_frames("Hello", "", font, tmp_path, 30)
    files = sorted(tmp_path.glob("pop_*.png"))
    assert pattern.endswith("pop_%03d.png") and len(files) == 9
    first, last = Image.open(files[0]), Image.open(files[-1])
    w0 = first.getbbox()[2] - first.getbbox()[0]
    w1 = last.getbbox()[2] - last.getbbox()[0]
    assert w0 == pytest.approx(w1 * 1.3, rel=0.03)


def test_hook_zoom_expression():
    expr = ffmpeg.zoom_expr(150, 30, hook=True)
    assert expr.startswith("if(lt(on,18),")
    assert f"1.0+{FAST_ZOOM_AMOUNT}*(1-pow(1-on/18,3))" in expr
    assert f",{1.0 + FAST_ZOOM_AMOUNT + 0.04:.4f}))" in expr
    assert ffmpeg.zoom_expr(150, 30) == "min(1.0+0.0002684564*on,1.04)"
    # Very short scene: fast zoom shortened to fit.
    assert ffmpeg.zoom_expr(5, 30, hook=True).startswith("if(lt(on,4),")


def test_hook_scene_command():
    cmd = ffmpeg.hook_scene_command("f.png", "pop/pop_%03d.png", "logo.png", 2.0, "o.mp4", 30)
    assert cmd[cmd.index("f.png") - 5 : cmd.index("f.png")] == ["-loop", "1", "-framerate", "30", "-i"]
    assert cmd[cmd.index("pop/pop_%03d.png") - 3 : cmd.index("pop/pop_%03d.png")] == ["-framerate", "30", "-i"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    # Headline composited before the zoom, logo after it.
    assert graph.index("[0:v][1:v]overlay") < graph.index("zoompan") < graph.index("[z][2:v]overlay")
    assert "zoompan=z='if(lt(on,18)" in graph and ":d=1:" in graph
    assert cmd[cmd.index("-frames:v") + 1] == "60"


def frame_at(video, n, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", rf"select=eq(n\,{n})",
                    "-frames:v", "1", str(out)], check=True)
    return Image.open(out).convert("RGB")


def image_top_edge(frame, x=1000):
    """First row (from y=450) where the red foreground image starts."""
    for y in range(450, 700):
        r, g, b = frame.getpixel((x, y))
        if r > 160 and g < 90:
            return y
    raise AssertionError("image edge not found")


def hook_zoom(n, fps=30):
    """Python mirror of the hook zoom curve (for long scenes)."""
    fast = round(0.6 * fps)
    if n < fast:
        return 1 + FAST_ZOOM_AMOUNT * (1 - (1 - n / fast) ** 3)
    return None


def lime_bbox(img, box):
    crop = img.crop(box)
    r, g, b = crop.split()
    mask = ImageChops.multiply(ImageChops.multiply(r.point(lambda v: 255 if v > 180 else 0),
                                                   g.point(lambda v: 255 if v > 200 else 0)),
                               b.point(lambda v: 255 if v < 120 else 0))
    return mask.getbbox()


@requires_ffmpeg
def test_hook_end_to_end(project, font, tmp_path):
    from wkvideo.pipeline import build_video
    from wkvideo.scenes import load_scenes

    scenes = load_scenes(project / "scenes.json")
    scenes[0].duration = 1.5   # 45 frames
    make_mp3(project / "voice.mp3", seconds=1.0)
    out = tmp_path / "hook.mp4"
    build_video(scenes, project / "voice.mp3", out, font, log=lambda _: None, captions=False)

    f0, f8, f18, f44 = (frame_at(out, n, tmp_path / f"{n}.png") for n in (0, 8, 18, 44))
    peak, end = 1 + FAST_ZOOM_AMOUNT, 1 + FAST_ZOOM_AMOUNT + 0.04
    # Foreground top edge sits at 576 at zoom 1.0 and moves up as the frame
    # zooms about its centre (y=960): fast to `peak` by 0.6s, then to `end`.
    assert image_top_edge(f0) == pytest.approx(576, abs=3)
    assert image_top_edge(f18) == pytest.approx(960 - 384 * peak, abs=3)
    assert image_top_edge(f44) == pytest.approx(960 - 384 * end, abs=3)

    # Headline pops 1.3x -> 1.0x by frame 8, and zooms with the frame.
    band = lambda f: white_bbox(f.crop((0, 0, WIDTH, IMAGE_TOP)))
    w = lambda bb: bb[2] - bb[0]
    base = w(band(f44)) / end                       # unzoomed headline width
    assert w(band(f0)) == pytest.approx(base * 1.3, rel=0.05)
    assert w(band(f8)) == pytest.approx(base * hook_zoom(8), rel=0.04)

    # Logo stays put while everything else zooms.
    logo = (WIDTH - 320, 0, WIDTH, 160)
    assert lime_bbox(f0, logo) is not None
    assert lime_bbox(f0, logo) == lime_bbox(f44, logo)
