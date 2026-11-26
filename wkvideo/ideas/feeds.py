"""The feed list (feeds.txt), fetching, and parsing of RSS, Atom and DefiLlama hacks."""

from __future__ import annotations

import html
import json
import re
import shutil
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .. import __version__

BUNDLED_FEEDS = Path(__file__).resolve().parent / "feeds.txt"
KINDS = ("rss", "defillama-hacks")
USER_AGENT = f"wkvideo-ideas/{__version__} (+https://github.com/wkalidev/wkvideo)"
TIMEOUT = 30
DEFILLAMA_HACKS_PAGE = "https://defillama.com/hacks"


class FeedError(ValueError):
    pass


@dataclass
class Source:
    name: str
    kind: str
    url: str
    home: Optional[str] = None   # region the source mostly covers, if set in feeds.txt


@dataclass
class Item:
    source: str
    title: str
    link: str
    published: datetime          # timezone-aware, UTC
    summary: str = ""            # plain text, as given by the feed
    content: str = ""            # plain text of the full post, when the feed includes it
    home: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(t for t in (self.title, self.summary, self.content) if t)


# --- feeds.txt -------------------------------------------------------------

def parse_feed_list(text: str) -> List[Source]:
    sources = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or not all(parts[:3]):
            raise FeedError(f"feeds.txt line {n}: expected 'name | kind | url', got: {raw!r}")
        name, kind, url = parts[:3]
        if kind not in KINDS:
            raise FeedError(f"feeds.txt line {n}: unknown kind {kind!r} (use one of: {', '.join(KINDS)})")
        home = None
        for opt in parts[3:]:
            key, _, value = opt.partition("=")
            if key.strip() == "home" and value.strip():
                home = value.strip()
            elif opt:
                raise FeedError(f"feeds.txt line {n}: unknown option {opt!r}")
        sources.append(Source(name, kind, url, home))
    return sources


def load_feed_list(path: Path) -> List[Source]:
    return parse_feed_list(Path(path).read_text(encoding="utf-8"))


def ensure_feed_list(path: Path) -> bool:
    """Copy the default feeds.txt to ``path`` if it doesn't exist. Returns True if copied."""
    path = Path(path)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLED_FEEDS, path)
    return True


# --- fetching --------------------------------------------------------------

Fetcher = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        hint = " (the site blocks automated requests)" if exc.code in (401, 403, 429) else ""
        raise FeedError(f"HTTP {exc.code}{hint}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FeedError(f"could not connect: {reason}") from None


# --- text helpers ----------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Containers that hold an article's own text on common news and blog sites,
# most specific first ("post-body" often wraps the byline and tags too).
BODY_MARKS = {
    "body": re.compile(r"articlebody|article-body|article__body|entry-content|story-body", re.I),
    "post": re.compile(r"post-body", re.I),
}


def _is_body(attrs, mark) -> bool:
    a = dict(attrs)
    if mark is BODY_MARKS["body"] and (a.get("itemprop") or "").lower() == "articlebody":
        return True
    return any(mark.search(a.get(k) or "") for k in ("class", "id"))


class _TextExtractor(HTMLParser):
    """Collects text, skipping scripts and page furniture.

    With ``scope`` set ("body", "post" or "article"), only text inside the
    first matching element is kept: an article body container, or <article>.
    """

    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "figure", "template"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "br", "div", "blockquote", "tr", "section", "article"}

    def __init__(self, scope: Optional[str] = None):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip = 0
        self.scope = scope
        self.depth = 0 if scope else 1     # > 0 while inside the scope element
        self.done = False

    def _starts_scope(self, tag, attrs) -> bool:
        if self.scope in BODY_MARKS:
            return _is_body(attrs, BODY_MARKS[self.scope])
        return tag == "article"

    def handle_starttag(self, tag, attrs):
        if self.scope and not self.done:
            if self.depth == 0 and self._starts_scope(tag, attrs) and tag not in VOID:
                self.depth = 1
                return
            if self.depth and tag not in VOID:
                self.depth += 1
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.scope and self.depth and tag not in VOID:
            self.depth -= 1
            if self.depth == 0:
                self.done = True          # only the first matching element
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip and self.depth and not (self.scope and self.done):
            self.parts.append(data)


# Inline CSS that some page builders put in the post text (".kt-btn{color:#555}").
CSS_LINE = re.compile(r"\{[^{}]*:[^{}]*\}|^[.#][\w-]+[^{]*\{")


def _extract(markup: str, scope: Optional[str] = None) -> str:
    parser = _TextExtractor(scope)
    parser.feed(markup)
    parser.close()
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line and not CSS_LINE.search(line))


TRAILER_MAX_CHARS = 600


def strip_trailer(markup: str) -> str:
    """Drop a short block after the post's last <hr>: usually a sign-off or an ad for the site's own product."""
    cut = markup.lower().rfind("<hr")
    if cut <= 0:
        return markup
    after, before = len(_extract(markup[cut:])), len(_extract(markup[:cut]))
    if after <= TRAILER_MAX_CHARS and before > 2 * after:
        return markup[:cut]
    return markup


def html_to_text(markup: str) -> str:
    """Plain text from an HTML fragment or page; paragraphs become separate lines."""
    return _extract(markup) if markup else ""


def article_paragraphs(page: str, min_words: int = 8) -> List[str]:
    """The prose paragraphs of an article page.

    Only the article's own text is used when the page marks it (an article
    body container, else <article>), so sidebars and "related" links are not
    mistaken for the story. Short lines like menus are dropped.
    """
    text = ""
    for scope in ("body", "post", "article", None):
        text = _extract(page, scope)
        if len(text.split()) >= 40:
            break
    return [p for p in text.split("\n") if len(p.split()) >= min_words]


# --- dates -----------------------------------------------------------------

def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)           # RSS: RFC 822
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        iso = value.replace("Z", "+00:00")         # Atom: ISO 8601 (3.9 can't read "Z")
        iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --- parsers ---------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    links = [c for c in entry if _local(c.tag) == "link"]
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return links[0].get("href", "") if links else ""


def parse_rss(data: bytes, source: Source) -> List[Item]:
    """Items from an RSS 2.0 or Atom feed. Items without a title, link or date are skipped."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedError(f"not a valid feed: {exc}") from None
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "item":            # RSS
            title = _child_text(el, "title")
            link = _child_text(el, "link") or _child_text(el, "guid")
            date = _child_text(el, "pubDate", "date", "published", "updated")
            summary = _child_text(el, "description")
            content = _child_text(el, "encoded")
        elif name == "entry":         # Atom
            title = _child_text(el, "title")
            link = _atom_link(el)
            date = _child_text(el, "published", "updated")
            summary = _child_text(el, "summary")
            content = _child_text(el, "content")
        else:
            continue
        published = parse_date(date)
        title = html_to_text(title)
        if not (title and link and published):
            continue
        items.append(Item(
            source=source.name, title=title, link=link.strip(), published=published,
            summary=html_to_text(summary), content=html_to_text(strip_trailer(content)), home=source.home,
        ))
    return items


def _money(amount) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return ""
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    if value >= 1e3:
        return f"${value / 1e3:.0f}K"
    return f"${value:.0f}"


def parse_defillama_hacks(data: bytes, source: Source) -> List[Item]:
    """Items from DefiLlama's hacks list (a JSON array of incidents)."""
    try:
        entries = json.loads(data)
    except ValueError as exc:
        raise FeedError(f"not valid JSON: {exc}") from None
    if not isinstance(entries, list):
        raise FeedError("expected a JSON list of hacks")
    items = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("name") or not e.get("date"):
            continue
        published = datetime.fromtimestamp(int(e["date"]), tz=timezone.utc)
        technique = (e.get("technique") or "").strip()
        classification = (e.get("classification") or "").strip()
        how = technique if technique and technique.lower() != "unknown" else classification
        title = f"{e['name']} hack" + (f": {how}" if how else "")
        lost = _money(e.get("amount"))
        chains = ", ".join(e.get("chain") or [])
        facts = "; ".join(x for x in (
            f"amount lost: {lost}" if lost else "",
            f"chain: {chains}" if chains else "",
            f"target: {e['targetType']}" if e.get("targetType") else "",
            f"technique: {technique}" if technique else "",
        ) if x)
        link = (e.get("source") or "").strip() or DEFILLAMA_HACKS_PAGE
        items.append(Item(
            source=source.name, title=title, link=link, published=published,
            summary=facts, content=(e.get("description") or "").strip(), home=source.home,
            extra={
                "name": str(e["name"]), "technique": technique, "classification": classification,
                "target": str(e.get("targetType") or ""), "amount": lost,
            },
        ))
    return items


PARSERS = {"rss": parse_rss, "defillama-hacks": parse_defillama_hacks}


@dataclass
class FetchResult:
    source: Source
    items: List[Item]
    error: Optional[str] = None


def fetch_all(sources: List[Source], fetch: Fetcher = http_get) -> List[FetchResult]:
    """Fetch and parse every source; a failing source is reported, not fatal."""
    results = []
    for src in sources:
        try:
            results.append(FetchResult(src, PARSERS[src.kind](fetch(src.url), src)))
        except FeedError as exc:
            results.append(FetchResult(src, [], str(exc)))
    return results


def recent(items: List[Item], now: datetime, days: int) -> Tuple[List[Item], int]:
    """Items published in the last ``days`` days (up to a day in the future, for time zones)."""
    lo = now.timestamp() - days * 86400
    hi = now.timestamp() + 86400
    kept = [i for i in items if lo <= i.published.timestamp() <= hi]
    return kept, len(items) - len(kept)
