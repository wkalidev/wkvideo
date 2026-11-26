"""Collect ideas from the sources and write them as Markdown."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Sequence

from .feeds import DEFILLAMA_HACKS_PAGE, FeedError, FetchResult, Fetcher, Item, Source, article_paragraphs, fetch_all, recent
from .score import Idea, merge_duplicates, rank, score_item

NO_ACTION = "the source gives no 'what to do' for viewers"


@dataclass
class Collection:
    ideas: List[Idea]              # ranked, usable
    skipped: List[Idea]
    results: List[FetchResult]
    too_old: int
    articles_read: int
    window: tuple                  # (start, end) datetimes


def collect(
    sources: Sequence[Source],
    now: datetime,
    days: int = 7,
    fetch: Fetcher = None,
    max_articles: int = 25,
    log: Callable[[str], None] = lambda _: None,
) -> Collection:
    """Fetch, filter, score and de-duplicate.

    Feeds often carry only a teaser, so for the best candidates whose feed
    text has no advice for viewers, the article page itself is read (at most
    ``max_articles`` pages) and searched for it.
    """
    from .feeds import http_get

    fetch = fetch or http_get
    results = fetch_all(list(sources), fetch)
    for r in results:
        log(f"{r.source.name}: " + (f"failed ({r.error})" if r.error else f"{len(r.items)} items"))
    items, too_old = recent([i for r in results for i in r.items], now, days)

    ideas: Dict[int, Idea] = {id(i): score_item(i, now) for i in items}
    candidates = sorted(
        (d for d in ideas.values() if d.skip == NO_ACTION and d.item.link != DEFILLAMA_HACKS_PAGE),
        key=lambda d: -d.score,
    )[:max_articles]
    read = 0
    for idea in candidates:
        try:
            paragraphs = article_paragraphs(fetch(idea.item.link).decode("utf-8", errors="replace"))
        except FeedError as exc:
            log(f"  could not read {idea.item.link}: {exc}")
            continue
        read += 1
        ideas[id(idea.item)] = score_item(idea.item, now, paragraphs)

    merged = merge_duplicates(list(ideas.values()))
    return Collection(
        ideas=rank(merged), skipped=[d for d in merged if d.skip], results=results, too_old=too_old,
        articles_read=read, window=(now - timedelta(days=days), now),
    )


# --- Markdown --------------------------------------------------------------

def _md(text: str) -> str:
    """Escape text for use inside a Markdown link label."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _date(item: Item) -> str:
    return item.published.strftime("%Y-%m-%d")


def _idea_block(n: int, idea: Idea) -> List[str]:
    item = idea.item
    where = "from the feed" if idea.action_from == "feed" else "from the article"
    lines = [
        f"## {n}. {idea.hook}",
        "",
        f"- **Why it matters:** “{idea.why}”" if idea.why else "- **Why it matters:** (the source has no summary sentence)",
        f"- **What to do:** “{idea.action}” ({where})",
        f"- **Region:** {idea.region}",
        f"- **Source:** [{_md(item.title)}]({item.link}) · {item.source} · {_date(item)}",
    ]
    if item.extra.get("amount") or item.extra.get("technique"):
        facts = item.summary
        lines.append(f"- **Listed facts:** {facts} (DefiLlama)")
    if idea.also:
        others = ", ".join(f"[{a.source}]({a.link}) · {_date(a)}" for a in idea.also)
        lines.append(f"- **Also covered by:** {others}")
    if idea.uncertain:
        words = ", ".join(f"“{w}”" for w in idea.uncertain)
        lines.append(f"- **⚠ Uncertain:** the source says {words}. Say so in the video or confirm first.")
    cats = ", ".join(idea.categories)
    lines.append(f"- Topic: {idea.topic.name} ({cats}) · score {idea.score}")
    return lines + [""]


def render(col: Collection, top: int, today: str) -> str:
    start, end = col.window
    end -= timedelta(seconds=1)   # a window ending at midnight ends on the day before
    chosen = col.ideas[:top]
    out = [
        f"# Stay Safe video ideas · {today}",
        "",
        f"Top {len(chosen)} of {len(col.ideas)} usable items published "
        f"{start:%Y-%m-%d} to {end:%Y-%m-%d} (UTC), from the sources in `feeds.txt`.",
        "",
        "“Why it matters” and “What to do” are quoted from the source. The hooks are fixed "
        "templates per topic: check each one fits the story before recording.",
        "",
    ]
    if len(chosen) < top:
        out += [f"> Only {len(chosen)} items had a “what to do” in the source this week, "
                f"so this list is shorter than {top}.", ""]
    for n, idea in enumerate(chosen, start=1):
        out += _idea_block(n, idea)

    out += ["## Sources", "", "| Source | Status |", "| --- | --- |"]
    for r in col.results:
        n = len(r.items)
        status = f"⚠ failed: {r.error}" if r.error else f"{n} item{'' if n == 1 else 's'} fetched"
        out.append(f"| {r.source.name} | {status} |")
    out.append("")
    if col.articles_read:
        out += [f"Article pages read to look for advice: {col.articles_read}.", ""]

    reasons = Counter(d.skip for d in col.skipped)
    out += ["## Skipped", "", f"- older than the window: {col.too_old}"]
    out += [f"- {reason}: {count}" for reason, count in reasons.most_common()]
    no_action = [d for d in col.skipped if d.skip == NO_ACTION][:10]
    if no_action:
        out += ["", "Relevant but without a “what to do” in the source (worth a manual look):", ""]
        out += [f"- [{_md(d.item.title)}]({d.item.link}) · {d.item.source} · {_date(d.item)}" for d in no_action]
    return "\n".join(out) + "\n"
