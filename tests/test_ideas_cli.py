import re

import pytest

from wkvideo import cli as wkvideo_cli
from wkvideo.ideas import feeds as feeds_module
from wkvideo.ideas.cli import main
from wkvideo.ideas.feeds import FeedError
from wkvideo.ideas.score import FIRST_PERSON

from ideas_samples import FILES, OfflineFetcher


@pytest.fixture
def run(tmp_path):
    """Run `wkvideo ideas` offline against the samples, as of 2026-11-25 (UTC)."""
    feeds = tmp_path / "feeds.txt"
    feeds.write_text(
        "".join(f"{name} | {kind} | {url}{' | home=US' if home else ''}\n" for name, kind, url, home in [
            ("BleepingComputer", "rss", "https://www.bleepingcomputer.com/feed/", False),
            ("The Hacker News", "rss", "https://feeds.feedburner.com/TheHackersNews", False),
            ("rekt.news", "rss", "https://rekt.news/rss/feed.xml", False),
            ("Fraud.org", "rss", "https://fraud.org/feed/", True),
            ("FTC consumer alerts", "rss", "https://consumer.ftc.gov/consumer-alerts/feed", True),
            ("Google fraud and scams advisory", "rss", "https://blog.google/technology/safety-security/rss/", False),
            ("DefiLlama hacks", "defillama-hacks", "https://api.llama.fi/hacks", False),
        ]),
        encoding="utf-8",
    )

    def _run(*extra, fetch=None):
        args = ["--out-dir", str(tmp_path / "ideas"), "--feeds", str(feeds), "--date", "2026-11-25", "-q", *extra]
        code = main(args, fetch=fetch or OfflineFetcher())
        out = tmp_path / "ideas" / "2026-11-25.md"
        return code, (out.read_text(encoding="utf-8") if out.exists() else None)

    return _run


def sections(md):
    return re.findall(r"^## \d+\. (.+)$", md, re.M)


def test_writes_dated_markdown_with_ranked_ideas(run):
    code, md = run()
    assert code == 0
    assert md.startswith("# Stay Safe video ideas · 2026-11-25")
    hooks = sections(md)
    assert hooks[0] == "Using Chrome? Update it today"
    assert len(hooks) == 9          # only 9 sample items have a 'what to do' in the source
    assert "Only 9 items had a" in md
    for hook in hooks:
        assert len(hook) < 70 and not FIRST_PERSON.search(hook)


def test_each_idea_has_every_field(run):
    _, md = run()
    blocks = re.split(r"^## \d+\. ", md, flags=re.M)[1:]
    for block in blocks[: len(sections(md))]:
        for field in ("**Why it matters:**", "**What to do:**", "**Region:**", "**Source:**"):
            assert field in block, (field, block)
        assert re.search(r"\*\*Source:\*\* \[.+\]\(https?://\S+\) · .+ · \d{4}-\d{2}-\d{2}", block)


def test_first_idea_contents(run):
    _, md = run()
    first = re.split(r"^## \d+\. ", md, flags=re.M)[1]
    assert "“Users are advised to update Chrome to version 150.0.7800.45 as soon as possible.” (from the feed)" in first
    assert "**Region:** Worldwide (the source names no country or region)" in first
    assert "(https://www.bleepingcomputer.com/news/security/chrome-emergency-update-fixes-zero-day/) · BleepingComputer · 2026-11-24" in first
    assert "**Also covered by:** [The Hacker News]" in first


def test_uncertain_regional_and_failed_sources_are_reported(run):
    _, md = run()
    assert "**⚠ Uncertain:** the source says “reportedly”" in md
    assert "**Region:** Brazil" in md
    assert "**Region:** US (the source names: 'USPS'" in md
    assert "| FTC consumer alerts | ⚠ failed: HTTP 403 (the site blocks automated requests) |" in md
    assert "- business news: 2" in md
    assert "Ransomware gang claims attack" not in md.split("## Skipped")[0]


def test_without_article_pages_fewer_items_have_advice(run):
    _, md = run("--max-articles", "0")
    assert "That browser extension might be watching you" not in sections(md)
    assert "Malicious Browser Extensions With 2 Million Installs" in md.split("## Skipped")[1]


def test_top_limits_the_list(run):
    _, md = run("--top", "3")
    assert len(sections(md)) == 3 and "Only" not in md


def test_all_sources_failing_writes_nothing(run):
    def down(url):
        raise FeedError("could not connect: offline")

    code, md = run(fetch=down)
    assert code == 1 and md is None


def test_default_feed_list_is_created_and_kept(tmp_path, monkeypatch):
    fetch = OfflineFetcher()
    monkeypatch.setattr(feeds_module, "http_get", fetch)
    out_dir = tmp_path / "ideas"
    assert main(["--out-dir", str(out_dir), "--date", "2026-11-25", "-q"]) == 0
    feeds = out_dir / "feeds.txt"
    assert "DefiLlama hacks | defillama-hacks | https://api.llama.fi/hacks" in feeds.read_text(encoding="utf-8")
    assert set(fetch.calls) >= {u for u in FILES if "feed" in u or "rss" in u or "hacks" in u}

    feeds.write_text("Only fraud | rss | https://fraud.org/feed/\n", encoding="utf-8")   # user edit
    fetch.calls.clear()
    assert main(["--out-dir", str(out_dir), "--date", "2026-11-25", "-q"]) == 0
    assert fetch.calls == ["https://fraud.org/feed/"]
    assert feeds.read_text(encoding="utf-8") == "Only fraud | rss | https://fraud.org/feed/\n"


def test_wkvideo_ideas_subcommand(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(feeds_module, "http_get", OfflineFetcher())
    out_dir = tmp_path / "ideas"
    assert wkvideo_cli.main(["ideas", "--out-dir", str(out_dir), "--date", "2026-11-25", "-q"]) == 0
    assert capsys.readouterr().out.strip() == str(out_dir / "2026-11-25.md")
