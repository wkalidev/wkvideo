"""Check post.json against each platform's limits and write post.md.

``wkvideo post FOLDER`` reads ``FOLDER/post.json``, checks every field,
prints the counts and writes ``FOLDER/post.md``. It exits with 1 (and
writes nothing) if a limit is exceeded.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"(?<![\w#])#\w+")
MENTION_RE = re.compile(r"(?<![\w@])@\w+")


def graphemes(text: str) -> int:
    """Approximate user-perceived characters: combining marks, ZWJ and variation selectors join."""
    count, join_next = 0, False
    for ch in text:
        if unicodedata.combining(ch) or ch in "︎️" or 0x1F3FB <= ord(ch) <= 0x1F3FF:
            continue
        if ch == "‍":
            join_next = True
            continue
        if join_next:
            join_next = False
            continue
        count += 1
    return count


def x_length(text: str) -> int:
    """X's weighted length: links count 23, CJK and emoji count 2, other characters 1."""
    total = 0
    for m in URL_RE.finditer(text):
        total += 23
    text = URL_RE.sub("", text)
    join_next = False
    for ch in text:
        cp = ord(ch)
        if unicodedata.combining(ch) or ch in "︎️" or 0x1F3FB <= cp <= 0x1F3FF:
            continue
        if ch == "‍":
            join_next = True
            continue
        if join_next:
            join_next = False
            continue
        light = (cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F
                 or 0x2032 <= cp <= 0x2037)
        total += 1 if light else 2
    return total


def youtube_tags_length(tags: List[str]) -> int:
    """YouTube's count: tags joined by commas, tags with spaces counted with quotes."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


@dataclass
class Rule:
    field: str
    label: str
    limit: int
    measure: Callable[[object], int] = len
    unit: str = "chars"
    required: bool = True


RULES = [
    Rule("youtube_title", "YouTube Shorts title", 100),
    Rule("youtube_description", "YouTube description", 5000, lambda s: len(s.encode("utf-8")), "bytes"),
    Rule("youtube_tags", "YouTube tags", 500, youtube_tags_length),
    Rule("tiktok_caption", "TikTok caption", 2200),
    Rule("instagram_caption", "Instagram caption", 2200),
    Rule("facebook_caption", "Facebook caption", 63206),
    Rule("bluesky_post", "Bluesky", 300, graphemes, "graphemes"),
    Rule("x_post", "X", 280, x_length, "weighted chars"),
]
EXTRA_LIMITS = {
    # field: (what, limit, counter)
    "instagram_caption": [("hashtags", 5, lambda s: len(HASHTAG_RE.findall(s))),
                          ("mentions", 20, lambda s: len(MENTION_RE.findall(s)))],
    "tiktok_caption": [("hashtags", 5, lambda s: len(HASHTAG_RE.findall(s)))],
    "youtube_tags": [("tags", 15, len)],
}
NO_ANGLE_BRACKETS = ("youtube_title", "youtube_description")  # YouTube rejects < and >


@dataclass
class Check:
    field: str
    label: str
    value: int
    limit: int
    unit: str

    @property
    def ok(self) -> bool:
        return self.value <= self.limit


def check_post(post: dict) -> "tuple[List[Check], List[str]]":
    """Counts for every field and a list of problems (empty when all limits pass)."""
    checks: List[Check] = []
    problems: List[str] = []
    for rule in RULES:
        value = post.get(rule.field)
        if value in (None, "", []):
            if rule.required:
                problems.append(f"{rule.field}: missing")
            continue
        if rule.field == "youtube_tags":
            if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                problems.append("youtube_tags: must be a list of strings")
                continue
        elif not isinstance(value, str):
            problems.append(f"{rule.field}: must be text")
            continue
        c = Check(rule.field, rule.label, rule.measure(value), rule.limit, rule.unit)
        checks.append(c)
        if not c.ok:
            problems.append(f"{rule.field}: {c.value}/{c.limit} {c.unit}")
        for what, limit, count in EXTRA_LIMITS.get(rule.field, []):
            n = count(value)
            if n > limit:
                problems.append(f"{rule.field}: {n} {what} (max {limit})")
        if rule.field in NO_ANGLE_BRACKETS and re.search(r"[<>]", value):
            problems.append(f"{rule.field}: YouTube doesn't allow < or >")
    unknown = sorted(set(post) - {r.field for r in RULES})
    if unknown:
        problems.append(f"unknown fields: {', '.join(unknown)}")
    return checks, problems


def render_md(name: str, post: dict, checks: List[Check], video: str = "output.mp4",
              notes: Optional[List[str]] = None) -> str:
    by_field = {c.field: c for c in checks}

    def head(field: str, label: str) -> str:
        c = by_field.get(field)
        if c is None or field in ("youtube_description", "youtube_tags", "facebook_caption"):
            return f"## {label}"
        return f"## {label} ({c.value}/{c.limit} chars)"

    parts = [f"# {name}", "", f"Video: `{video}`", ""]
    sections = [
        ("youtube_title", "YouTube Shorts title"),
        ("youtube_description", "YouTube description"),
        ("youtube_tags", "YouTube tags"),
        ("tiktok_caption", "TikTok caption"),
        ("instagram_caption", "Instagram caption"),
        ("facebook_caption", "Facebook caption"),
        ("bluesky_post", "Bluesky"),
        ("x_post", "X"),
    ]
    for field, label in sections:
        value = post.get(field)
        if not value:
            continue
        text = ", ".join(value) if field == "youtube_tags" else value
        parts += [head(field, label), "", text, ""]
    if notes:
        parts += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    parts += [
        "## Posting", "",
        "- Slots: 8h and 18h Brussels, one video per slot",
        "- TikTok: add a trending sound at very low volume",
        "",
    ]
    return "\n".join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="wkvideo post",
                                description="Check post.json against platform limits and write post.md.")
    p.add_argument("folder", type=Path, help="video folder with post.json")
    p.add_argument("--note", action="append", default=[], help="extra line for the Notes section (repeatable)")
    args = p.parse_args(argv)
    path = args.folder / "post.json"
    try:
        post = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: {path}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(post, dict):
        print(f"error: {path}: expected an object", file=sys.stderr)
        return 2
    checks, problems = check_post(post)
    for c in checks:
        print(f"{'ok ' if c.ok else 'TOO LONG'} {c.label}: {c.value}/{c.limit} {c.unit}")
    if problems:
        for pr in problems:
            print(f"error: {pr}", file=sys.stderr)
        return 1
    out = args.folder / "post.md"
    out.write_text(render_md(args.folder.resolve().name, post, checks, notes=args.note), encoding="utf-8")
    print(f"Wrote {out}")
    return 0
