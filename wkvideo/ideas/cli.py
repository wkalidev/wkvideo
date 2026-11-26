"""``wkvideo ideas``: write this week's top Stay Safe video topics to a Markdown file."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .feeds import FeedError, ensure_feed_list, load_feed_list
from .report import collect, render

DEFAULT_OUT_DIR = Path.home() / "wk-content" / "ideas"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wkvideo ideas",
        description="Collect fresh topics for short 'Stay Safe' videos from public feeds and write the top ones "
                    "to OUT_DIR/YYYY-MM-DD.md.",
    )
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help=f"where to write the ideas file (default: {DEFAULT_OUT_DIR})")
    p.add_argument("--feeds", type=Path,
                   help="feed list to use (default: OUT_DIR/feeds.txt, created from the built-in list on first run)")
    p.add_argument("--days", type=int, default=7, help="keep items from the last N days (default: 7)")
    p.add_argument("--top", type=int, default=10, help="number of ideas to write (default: 10)")
    p.add_argument("--max-articles", type=int, default=25,
                   help="article pages to read when a feed item has no advice in it (default: 25; 0 = none)")
    p.add_argument("--date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                   help="treat this day as today (file name and time window); default: today")
    p.add_argument("-q", "--quiet", action="store_true", help="only print the output path")
    return p


def main(argv: Optional[List[str]] = None, fetch=None) -> int:
    args = build_parser().parse_args(argv)
    log = (lambda _: None) if args.quiet else (lambda msg: print(msg, file=sys.stderr))
    if args.days < 1 or args.top < 1 or args.max_articles < 0:
        print("error: --days and --top must be at least 1, --max-articles at least 0", file=sys.stderr)
        return 2

    if args.date:
        today = args.date
        now = datetime.combine(today + timedelta(days=1), time(0), tzinfo=timezone.utc)
    else:
        today = date.today()
        now = datetime.now(timezone.utc)

    feeds_path = args.feeds or args.out_dir / "feeds.txt"
    try:
        if args.feeds is None and ensure_feed_list(feeds_path):
            log(f"Created the feed list {feeds_path} (edit it to change the sources)")
        sources = load_feed_list(feeds_path)
    except (FeedError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print(f"error: no sources in {feeds_path}", file=sys.stderr)
        return 1

    col = collect(sources, now, args.days, fetch=fetch, max_articles=args.max_articles, log=log)
    if all(r.error for r in col.results):
        print("error: every source failed; nothing written", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{today.isoformat()}.md"
    out.write_text(render(col, args.top, today.isoformat()), encoding="utf-8")
    failed = [r.source.name for r in col.results if r.error]
    if failed:
        log(f"Warning: could not fetch {', '.join(failed)} (see the Sources section)")
    log(f"{len(col.ideas)} usable ideas, top {min(args.top, len(col.ideas))} written")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
