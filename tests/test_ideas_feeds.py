from datetime import datetime, timezone

import pytest

from wkvideo.ideas.feeds import (
    BUNDLED_FEEDS, DEFILLAMA_HACKS_PAGE, FeedError, Source, article_paragraphs, ensure_feed_list, fetch_all,
    html_to_text, load_feed_list, parse_date, parse_defillama_hacks, parse_feed_list, parse_rss, recent,
)

from ideas_samples import DATA, NOW, SOURCES, OfflineFetcher


def by_title(items):
    return {i.title: i for i in items}


def test_bundled_feed_list_has_all_requested_sources():
    names = [s.name for s in load_feed_list(BUNDLED_FEEDS)]
    assert names == [
        "BleepingComputer", "The Hacker News", "rekt.news", "Fraud.org", "FTC consumer alerts",
        "Google fraud and scams advisory", "DefiLlama hacks",
    ]
    assert all(s.url.startswith("https://") for s in load_feed_list(BUNDLED_FEEDS))


def test_parse_feed_list_options_and_comments():
    sources = parse_feed_list("# comment\n\nA | rss | https://a.example/feed | home=UK\nB|defillama-hacks|https://b\n")
    assert sources == [Source("A", "rss", "https://a.example/feed", "UK"), Source("B", "defillama-hacks", "https://b")]


@pytest.mark.parametrize("line", ["just a name", "A | atom | https://x", "A | rss | https://x | colour=red"])
def test_parse_feed_list_rejects_bad_lines(line):
    with pytest.raises(FeedError, match="line 1"):
        parse_feed_list(line)


def test_ensure_feed_list_copies_once(tmp_path):
    path = tmp_path / "ideas" / "feeds.txt"
    assert ensure_feed_list(path) is True
    path.write_text("Mine | rss | https://mine.example/feed\n", encoding="utf-8")
    assert ensure_feed_list(path) is False          # never overwrites the user's edits
    assert load_feed_list(path)[0].name == "Mine"


def test_parse_dates():
    utc = timezone.utc
    assert parse_date("Wed, 25 Nov 2026 09:50:22 +0530") == datetime(2026, 11, 25, 4, 20, 22, tzinfo=utc)
    assert parse_date("2026-11-21T14:00:00.123456789Z") == datetime(2026, 11, 21, 14, 0, 0, 123456, tzinfo=utc)
    assert parse_date("2026-11-19") == datetime(2026, 11, 19, tzinfo=utc)
    assert parse_date("not a date") is None and parse_date("") is None


def test_parse_rss_bleepingcomputer():
    items = by_title(parse_rss((DATA / "bleepingcomputer.xml").read_bytes(), SOURCES[0]))
    chrome = items["Chrome emergency update fixes zero-day exploited in attacks"]
    assert chrome.link.endswith("/chrome-emergency-update-fixes-zero-day/")
    assert chrome.published == datetime(2026, 11, 24, 15, 15, tzinfo=timezone.utc)
    assert chrome.summary.startswith("Google has released an emergency Chrome update")
    assert chrome.source == "BleepingComputer" and chrome.content == ""


def test_parse_rss_content_encoded_and_entities():
    item = parse_rss((DATA / "fraud.xml").read_bytes(), SOURCES[3])[0]
    assert item.home == "US"
    assert "Don’t click on links in unsolicited texts." in item.content
    assert "\n" in item.content and "<p>" not in item.content


def test_parse_atom():
    items = by_title(parse_rss((DATA / "google.atom").read_bytes(), SOURCES[5]))
    advisory = items["Fraud and scams advisory: November 2026"]
    assert advisory.link == "https://blog.google/technology/safety-security/fraud-and-scams-advisory-november-2026/"
    assert advisory.summary.startswith("Scammers are using AI-generated voices")   # <img> dropped
    assert "family code word" in advisory.content
    gemini = items["New Gemini feature helps spot scam messages"]
    assert gemini.published == datetime(2026, 11, 21, 14, 0, 0, 123456, tzinfo=timezone.utc)  # from <updated>


def test_parse_rss_rejects_garbage():
    with pytest.raises(FeedError):
        parse_rss(b"<html><body>not a feed", SOURCES[0])


def test_parse_defillama_hacks():
    items = by_title(parse_defillama_hacks((DATA / "defillama_hacks.json").read_bytes(), SOURCES[6]))
    wallet = items["SampleWallet hack: Private Key Compromised"]
    assert wallet.published == datetime(2026, 11, 24, tzinfo=timezone.utc)
    assert wallet.link == DEFILLAMA_HACKS_PAGE                       # no per-hack source given
    assert "amount lost: $2.57M" in wallet.summary and "target: Wallet" in wallet.summary
    assert wallet.content.startswith("An attacker reportedly obtained private keys")
    assert items["SampleSwap V3 hack: Incorrect Share Accounting"].link == "https://example.invalid/sampleswap-postmortem"


def test_html_to_text_and_article_paragraphs():
    page = (DATA / "pages" / "malicious-browser-extensions.html").read_text(encoding="utf-8")
    paras = article_paragraphs(page)
    assert any(p.startswith("Users who installed any of the extensions") for p in paras)
    assert not any("ignore this script" in p for p in paras)          # <script> skipped
    assert not any("Subscribe" in p for p in paras)                   # <footer> skipped
    assert html_to_text("a&nbsp;b &amp; c<br>d") == "a b & c\nd"


def test_fetch_all_reports_blocked_source_and_keeps_going():
    results = {r.source.name: r for r in fetch_all(SOURCES, OfflineFetcher())}
    assert results["FTC consumer alerts"].items == []
    assert "403" in results["FTC consumer alerts"].error
    assert len(results["BleepingComputer"].items) == 6 and results["BleepingComputer"].error is None


def test_recent_keeps_last_seven_days():
    items = [i for r in fetch_all(SOURCES, OfflineFetcher()) for i in r.items]
    kept, dropped = recent(items, NOW, 7)
    titles = {i.title for i in kept}
    assert "Old phishing campaign targets streaming subscribers" not in titles   # Nov 10
    assert "Transparent When You Want It, Private When You Need It" not in titles
    assert "Old Protocol hack: Flashloan Governance Attack" not in titles
    assert "Fraud and scams advisory: November 2026" in titles                   # Nov 19, 6.9 days old
    assert dropped == 3


def test_article_paragraphs_use_only_the_article_body():
    filler = "This sentence is here to make the article body long enough to count as the story text. " * 3
    page = (
        "<html><body><div class='post-body'><span>By Someone, Nov 20</span>"
        f"<div id='articlebody' class='articlebody clear'><p>{filler}</p>"
        "<p>Users should only install apps from the official store and check the developer name.</p></div>"
        "<div class='related'><p>The Important Questions You Should be Asking Your Pentesters today</p></div>"
        "</div><aside><p>Subscribe to our newsletter for weekly security news and tips from us.</p></aside>"
        "</body></html>"
    )
    paras = article_paragraphs(page)
    assert paras[-1] == "Users should only install apps from the official store and check the developer name."
    assert not any("Pentesters" in p or "newsletter" in p for p in paras)
