"""Word-by-word captions: grouping, timing and rendering of caption images.

Captions are rendered as transparent PNG strips (one per highlighted-word
state) and stitched into a timed track with ffmpeg's concat demuxer, which is
then overlaid on the finished video.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

from PIL import Image, ImageDraw

from .render import BLACK, HEIGHT, LIME, WHITE, WIDTH, load_font
from .transcribe import Word

CAPTION_CENTER_RATIO = 0.70
CAPTION_SIZE = 76
CAPTION_MIN_SIZE = 56
CAPTION_STROKE = 6
CAPTION_MAX_WIDTH = WIDTH - 2 * 60
STRIP_HEIGHT = 200
MIN_WORDS, MAX_WORDS = 2, 4
PAUSE_BREAK = 0.6   # a pause this long ends a caption group (if it has >= 2 words)
HOLD_AFTER = 0.4    # keep the last group on screen this long after its last word

TRAILING_PUNCTUATION = ",.;:"


def caption_y() -> int:
    """Top edge of the caption strip, so its centre sits at 70% height."""
    return round(HEIGHT * CAPTION_CENTER_RATIO) - STRIP_HEIGHT // 2


def clean(words: Sequence[Word]) -> List[Word]:
    out = []
    for w in words:
        text = w.text.strip().rstrip(TRAILING_PUNCTUATION).strip()
        if text:
            out.append(Word(text, w.start, max(w.end, w.start)))
    return out


def _ends_phrase(raw: str) -> bool:
    return raw.rstrip().endswith((".", "?", "!", ",", ";", ":"))


def group_words(words: Sequence[Word], fits: Callable[[List[str]], bool] = lambda _: True) -> List[List[Word]]:
    """Split words into caption groups of 2-4 words.

    A group closes when it has 4 words, when the next word would not fit on
    one line, or (once it has at least 2 words) at the end of a phrase or
    before a pause. A lone trailing word is merged into the previous group,
    or takes a word from it, so no group is a single word unless the whole
    transcript is.
    """
    groups: List[List[Word]] = []
    current: List[Word] = []
    # Clean each word but remember whether its raw text ended a phrase.
    cleaned: List[Word] = []
    ends: List[bool] = []
    for w in words:
        c = clean([w])
        if c:
            cleaned.append(c[0])
            ends.append(_ends_phrase(w.text))

    for i, word in enumerate(cleaned):
        if current and (len(current) >= MAX_WORDS or not fits([w.text for w in current + [word]])):
            groups.append(current)
            current = []
        current.append(word)
        nxt = cleaned[i + 1] if i + 1 < len(cleaned) else None
        pause = nxt is not None and nxt.start - word.end >= PAUSE_BREAK
        if len(current) >= MIN_WORDS and (ends[i] or pause):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    # Fix single-word groups by merging with a neighbour where possible.
    fixed: List[List[Word]] = []
    for g in groups:
        if len(g) == 1 and fixed:
            prev = fixed[-1]
            merged = prev + g
            if len(merged) <= MAX_WORDS and fits([w.text for w in merged]):
                fixed[-1] = merged
                continue
            if len(prev) > MIN_WORDS:
                fixed[-1] = prev[:-1]
                fixed.append([prev[-1]] + g)
                continue
        fixed.append(list(g))
    # A single word at the very start: merge forward.
    if len(fixed) >= 2 and len(fixed[0]) == 1:
        merged = fixed[0] + fixed[1]
        if len(merged) <= MAX_WORDS and fits([w.text for w in merged]):
            fixed[:2] = [merged]
    return fixed


@dataclass
class CaptionState:
    start: float
    end: float
    group: int        # index into the groups list
    highlight: int    # index of the highlighted word within the group


def timeline(groups: Sequence[Sequence[Word]], total: float) -> List[CaptionState]:
    """When each (group, highlighted word) state is on screen."""
    states: List[CaptionState] = []
    for gi, group in enumerate(groups):
        g_start = group[0].start
        g_end = group[-1].end + HOLD_AFTER
        if gi + 1 < len(groups):
            g_end = min(g_end, groups[gi + 1][0].start)
        g_end = min(g_end, total)
        if states:
            g_start = max(g_start, states[-1].end)
        for wi, word in enumerate(group):
            s = g_start if wi == 0 else max(word.start, states[-1].end)
            e = g_end if wi == len(group) - 1 else min(max(group[wi + 1].start, s), g_end)
            if e > s:
                states.append(CaptionState(s, e, gi, wi))
    return states


def make_fits(font_path: str, size: int = CAPTION_SIZE) -> Callable[[List[str]], bool]:
    font = load_font(str(font_path), size)

    def fits(texts: List[str]) -> bool:
        return font.getlength(" ".join(texts)) + 2 * CAPTION_STROKE <= CAPTION_MAX_WIDTH

    return fits


def render_caption(texts: Sequence[str], highlight: int, font_path) -> Image.Image:
    """A transparent WIDTH x STRIP_HEIGHT strip with one word highlighted."""
    strip = Image.new("RGBA", (WIDTH, STRIP_HEIGHT), (0, 0, 0, 0))
    if not texts:
        return strip
    line = " ".join(texts)
    size = CAPTION_SIZE
    font = load_font(str(font_path), size)
    while size > CAPTION_MIN_SIZE and font.getlength(line) + 2 * CAPTION_STROKE > CAPTION_MAX_WIDTH:
        size -= 2
        font = load_font(str(font_path), size)
    space = font.getlength(" ")
    widths = [font.getlength(t) for t in texts]
    x = (WIDTH - (sum(widths) + space * (len(texts) - 1))) / 2
    draw = ImageDraw.Draw(strip)
    for i, (text, w) in enumerate(zip(texts, widths)):
        draw.text(
            (x, STRIP_HEIGHT / 2), text, font=font, fill=LIME if i == highlight else WHITE,
            anchor="lm", stroke_width=CAPTION_STROKE, stroke_fill=BLACK,
        )
        x += w + space
    return strip


def write_caption_track(words: Sequence[Word], font_path, work: Path, total: float) -> Path:
    """Render caption PNGs and an ffconcat file timing them; returns its path."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    groups = group_words(words, make_fits(font_path))
    states = timeline(groups, total)

    blank = work / "cap_blank.png"
    render_caption([], 0, font_path).save(blank)
    entries: List[Tuple[Path, float]] = []
    t = 0.0
    for n, st in enumerate(states):
        if st.start > t + 1e-3:
            entries.append((blank, st.start - t))
        png = work / f"cap_{n:04d}.png"
        render_caption([w.text for w in groups[st.group]], st.highlight, font_path).save(png)
        entries.append((png, st.end - st.start))
        t = st.end
    entries.append((blank, max(total - t, 0.04)))

    lines = ["ffconcat version 1.0"]
    for png, dur in entries:
        lines += [f"file '{png.resolve().as_posix()}'", f"duration {dur:.4f}"]
    # The concat demuxer ignores the last entry's duration unless it is repeated.
    lines.append(f"file '{entries[-1][0].resolve().as_posix()}'")
    track = work / "captions.ffconcat"
    track.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return track
