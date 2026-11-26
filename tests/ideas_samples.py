"""Shared helpers for the `wkvideo ideas` tests: the saved sample feeds and an offline fetcher."""

from datetime import datetime, timezone
from pathlib import Path

from wkvideo.ideas.feeds import FeedError, Source

DATA = Path(__file__).resolve().parent / "data" / "ideas"
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

SOURCES = [
    Source("BleepingComputer", "rss", "https://www.bleepingcomputer.com/feed/"),
    Source("The Hacker News", "rss", "https://feeds.feedburner.com/TheHackersNews"),
    Source("rekt.news", "rss", "https://rekt.news/rss/feed.xml"),
    Source("Fraud.org", "rss", "https://fraud.org/feed/", home="US"),
    Source("FTC consumer alerts", "rss", "https://consumer.ftc.gov/consumer-alerts/feed", home="US"),
    Source("Google fraud and scams advisory", "rss", "https://blog.google/technology/safety-security/rss/"),
    Source("DefiLlama hacks", "defillama-hacks", "https://api.llama.fi/hacks"),
]

FILES = {
    "https://www.bleepingcomputer.com/feed/": "bleepingcomputer.xml",
    "https://feeds.feedburner.com/TheHackersNews": "thehackernews.xml",
    "https://rekt.news/rss/feed.xml": "rekt.xml",
    "https://fraud.org/feed/": "fraud.xml",
    "https://blog.google/technology/safety-security/rss/": "google.atom",
    "https://api.llama.fi/hacks": "defillama_hacks.json",
    "https://thehackernews.com/2026/11/malicious-browser-extensions.html": "pages/malicious-browser-extensions.html",
    "https://www.bleepingcomputer.com/news/security/fake-toll-payment-texts/": "pages/fake-toll-payment-texts.html",
    "https://www.bleepingcomputer.com/news/security/ransomware-gang-claims-hospital-attack/":
        "pages/ransomware-gang-claims-hospital-attack.html",
}


class OfflineFetcher:
    """Serves the saved samples; the FTC feed answers 403 like the real one does to bots."""

    def __init__(self):
        self.calls = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if "ftc.gov" in url:
            raise FeedError("HTTP 403 (the site blocks automated requests)")
        name = FILES.get(url)
        if name is None:
            raise FeedError("HTTP 404")
        return (DATA / name).read_bytes()
