import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from wkvideo.captions import (
    CAPTION_BOTTOM_MARGIN, CAPTION_CENTER_RATIO, IMAGE_GAP, STRIP_HEIGHT, caption_center, caption_y, clean,
    group_words, ink_extent, max_image_bottom, render_caption, timeline, write_caption_track,
)
from wkvideo.render import HEIGHT, LIME, WIDTH
from wkvideo.transcribe import Word, cache_path, load_or_transcribe

from conftest import make_mp3, requires_ffmpeg


def words(text, step=0.3, gap_after=()):
    """Evenly spaced words; ``gap_after`` lists indices followed by a 1s pause."""
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append(Word(w, t, t + step * 0.8))
        t += step + (1.0 if i in gap_after else 0)
    return out


def texts(groups):
    return [[w.text for w in g] for g in groups]


def test_clean_strips_trailing_punctuation():
    assert [w.text for w in clean(words("hello, world. why? ok"))] == ["hello", "world", "why?", "ok"]


def test_groups_have_two_to_four_words():
    groups = group_words(words("one two three four five six seven eight nine ten eleven"))
    assert all(2 <= len(g) <= 4 for g in groups)
    assert sum(len(g) for g in groups) == 11


def test_groups_break_at_phrase_end_and_pause():
    groups = group_words(words("a recruiter messaged you. check this before replying", gap_after=()))
    assert texts(groups)[0] == ["a", "recruiter", "messaged", "you"]
    groups = group_words(words("never run it here please", gap_after=(2,)))
    assert texts(groups) == [["never", "run", "it"], ["here", "please"]]


def test_no_single_word_groups():
    for n in range(2, 20):
        groups = group_words(words(" ".join(f"w{i}" for i in range(n))))
        assert all(len(g) >= 2 for g in groups), texts(groups)
    # A phrase end right after one word does not create a lone group.
    groups = group_words(words("stop. do not run that code"))
    assert all(len(g) >= 2 for g in groups)


def test_phrase_end_after_full_group_carries_a_word():
    groups = group_words(words("a repo to clone and run on your laptop. that code can steal"))
    assert ["your", "laptop"] in texts(groups)
    assert all(not (g[0] == "laptop") for g in texts(groups))


def test_real_transcript_groups():
    raw = ("A recruiter from a big crypto exchange just messaged you on LinkedIn? Check this before you reply. "
           "Fake recruiters are hitting devs right now.")
    groups = texts(group_words(words(raw), lambda ws: len(" ".join(ws)) <= 18))
    assert all(2 <= len(g) <= 4 for g in groups)
    # No group spans a sentence end.
    for g in groups:
        assert not any(w.endswith(("?", ".")) for w in g[:-1]), g


@requires_ffmpeg
def test_load_audio_decodes_16k_mono(tmp_path):
    from wkvideo.transcribe import SAMPLE_RATE, load_audio

    make_mp3(tmp_path / "a.mp3", seconds=1.0)
    samples = load_audio(tmp_path / "a.mp3")
    assert str(samples.dtype) == "float32"
    assert len(samples) == pytest.approx(SAMPLE_RATE, rel=0.1)
    assert 0.1 < abs(samples).max() <= 1.0


def test_width_limit_breaks_groups():
    fits = lambda ws: len(" ".join(ws)) <= 12
    groups = group_words(words("aaaa bbbb cccc dddd eeee ffff"), fits)
    assert all(len(" ".join(g)) <= 12 for g in texts(groups))


def test_timeline_highlights_each_word_in_order():
    ws = words("one two three four five six", gap_after=(2,))
    groups = group_words(ws)
    states = timeline(groups, total=10)
    flat = [w for g in groups for w in g]
    assert len(states) == len(flat)
    for st, w in zip(states, flat):
        assert groups[st.group][st.highlight] is w
        assert st.start <= w.start + 1e-9 or st.highlight == 0
    # States never overlap and run forward in time.
    for a, b in zip(states, states[1:]):
        assert a.end <= b.start + 1e-9 and a.start < a.end
    # The gap after "three" is shown blank once the hold time passes.
    assert states[2].end < states[3].start


def test_timeline_clamped_to_total():
    states = timeline(group_words(words("one two three")), total=0.5)
    assert states[-1].end <= 0.5


def test_render_caption_highlight(font):
    strip = render_caption(["check", "this", "first"], 1, font)
    assert strip.size == (WIDTH, STRIP_HEIGHT) and strip.mode == "RGBA"
    colours = {c[:3] for _, c in strip.getcolors(WIDTH * STRIP_HEIGHT) if c[3] == 255}
    assert LIME in colours and (255, 255, 255) in colours
    # The lime (highlighted) word is the middle one.
    xs = [x for x in range(WIDTH) if strip.getpixel((x, STRIP_HEIGHT // 2))[:3] == LIME]
    assert WIDTH * 0.3 < sum(xs) / len(xs) < WIDTH * 0.7
    assert render_caption([], 0, font).getbbox() is None


def test_caption_centre_at_82_percent():
    assert CAPTION_CENTER_RATIO == 0.82
    assert caption_y() + STRIP_HEIGHT / 2 == pytest.approx(HEIGHT * CAPTION_CENTER_RATIO, abs=1)


def test_caption_stays_at_82_percent_under_a_short_image(font):
    assert caption_center(1267, font) == round(HEIGHT * 0.82)


def test_caption_moves_down_to_clear_a_low_image(font):
    above, _ = ink_extent(font)
    centre = caption_center(1600, font)
    assert centre > round(HEIGHT * 0.82)
    assert centre - above - 1600 == pytest.approx(IMAGE_GAP, abs=1)


def test_image_limit_keeps_the_gap_and_the_bottom_margin(font):
    above, below = ink_extent(font)
    limit = max_image_bottom(font)
    centre = caption_center(limit, font)
    assert centre - above - limit >= IMAGE_GAP
    assert centre + below <= HEIGHT - CAPTION_BOTTOM_MARGIN
    assert caption_y(centre) + STRIP_HEIGHT <= HEIGHT


def test_write_caption_track(tmp_path, font):
    track = write_caption_track(words("one two three four five", gap_after=(1,)), font, tmp_path, total=4.0)
    lines = track.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ffconcat version 1.0"
    durations = [float(l.split()[1]) for l in lines if l.startswith("duration")]
    assert sum(durations) == pytest.approx(4.0, abs=0.05)
    assert lines[-1].startswith("file ")  # last entry repeated for the demuxer


def test_transcript_cache(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake")
    calls = []

    def fake(path, model):
        calls.append(model)
        return [Word("hi", 0.0, 0.5), Word("there", 0.5, 1.0)]

    first = load_or_transcribe(audio, "small", transcribe=fake, log=lambda _: None)
    second = load_or_transcribe(audio, "small", transcribe=fake, log=lambda _: None)
    assert first == second and calls == ["small"]
    assert cache_path(audio).is_file()
    load_or_transcribe(audio, "base", transcribe=fake, log=lambda _: None)
    assert calls == ["small", "base"]  # different model -> re-transcribed


@requires_ffmpeg
def test_end_to_end_with_captions(project, font, tmp_path):
    from wkvideo.pipeline import build_video
    from wkvideo.scenes import load_scenes

    make_mp3(project / "voice.mp3", seconds=1.0)
    out = tmp_path / "cap.mp4"
    ws = [Word("check", 0.0, 0.3), Word("this", 0.3, 0.6), Word("now", 0.6, 0.9)]
    build_video(load_scenes(project / "scenes.json"), project / "voice.mp3", out, font,
                log=lambda _: None, words=ws)

    # Grab a frame at 0.45s ("this" highlighted) and check the caption band.
    png = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.45", "-i", str(out), "-frames:v", "1", str(png)], check=True)
    frame = Image.open(png).convert("RGB")
    band = frame.crop((0, caption_y(), WIDTH, caption_y() + STRIP_HEIGHT))
    colours = [c for _, c in band.getcolors(WIDTH * STRIP_HEIGHT)]
    near = lambda c, ref: all(abs(a - b) < 40 for a, b in zip(c, ref))
    assert any(near(c, LIME) for c in colours)
    assert any(near(c, (255, 255, 255)) for c in colours)
