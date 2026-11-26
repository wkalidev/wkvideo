from datetime import timedelta

import pytest

from wkvideo.ideas.feeds import Item, article_paragraphs, fetch_all, recent
from wkvideo.ideas.score import (
    FIRST_PERSON, PRODUCTS, TOPICS, classify, find_action, hedges_in, make_hook, merge_duplicates, rank,
    region_for, same_story, score_item,
)

from ideas_samples import NOW, SOURCES, OfflineFetcher


def item(title, summary="", content="", source="Test", home=None, extra=None, age_days=1):
    return Item(source, title, "https://example.invalid/" + title.replace(" ", "-"), NOW - timedelta(days=age_days),
                summary, content, home, extra or {})


@pytest.fixture(scope="module")
def sample_ideas():
    fetch = OfflineFetcher()
    items, _ = recent([i for r in fetch_all(SOURCES, fetch) for i in r.items], NOW, 7)
    pages = {}
    for i in items:
        try:
            pages[i.link] = article_paragraphs(fetch(i.link).decode("utf-8"))
        except Exception:
            pass
    return merge_duplicates([score_item(i, NOW, pages.get(i.link, ())) for i in items])


def by_title(ideas):
    return {i.item.title: i for i in ideas}


# --- hooks -----------------------------------------------------------------

def test_every_hook_is_short_casual_and_never_first_person():
    longest = max(PRODUCTS, key=len)
    for topic in TOPICS:
        hooks = [topic.hook] + ([topic.product_hook.format(product=longest)] if topic.product_hook else [])
        for hook in hooks:
            assert len(hook) < 70, hook
            assert not FIRST_PERSON.search(hook), hook


def test_product_hook_uses_the_product_named_in_the_title():
    topic, _ = classify(item("Chrome emergency update fixes zero-day"))
    assert make_hook(topic, item("Chrome emergency update fixes zero-day")) == "Using Chrome? Update it today"
    plain = item("Emergency update fixes zero-day in popular router")
    assert make_hook(classify(plain)[0], plain) == "Hit update today. Here's why it can't wait"


def test_specific_topic_beats_catch_all_in_title():
    advisory = item("Fraud and scams advisory: November", content="Scammers use AI-generated voices of relatives.")
    assert classify(advisory)[0].name == "voice clone"


def test_ai_tool_but_not_ai_attacks():
    assert classify(item("New Gemini feature helps spot scam messages"))[0].name == "AI tool"
    topic, cats = classify(item("AI agents power new attacks on hospitals", "An AI tool helps attackers."))
    assert topic is None or topic.name != "AI tool"


# --- actions and uncertainty -----------------------------------------------

def test_find_action_prefers_advice_to_users():
    text = "Researchers found the flaw. Update your phone now. Users should enable automatic updates."
    assert find_action([text]) == "Users should enable automatic updates."


def test_find_action_takes_imperatives_and_skips_admin_advice():
    assert find_action(["Administrators should apply the patch."]) is None
    assert find_action(["Never sign a transaction you don't understand."]) == "Never sign a transaction you don't understand."
    assert find_action(["The company said it is investigating."]) is None


def test_hedges():
    assert hedges_in("The gang reportedly stole data", "it may have spread") == ["reportedly", "may have"]
    assert hedges_in("Google fixed the bug.") == []


# --- region ----------------------------------------------------------------

def test_region_named_in_source():
    label, found, points = region_for(item("Fake USPS texts", "Scammers send fake USPS texts."))
    assert label.startswith("US") and "USPS" in label and points < 0


def test_region_worldwide_said_by_source_or_unstated():
    assert region_for(item("Scam texts", "Reported in many countries."))[0] == "Worldwide (the source says so)"
    assert region_for(item("Scam texts", "A new scam."))[0].startswith("Worldwide (the source names no")


def test_region_home_source_is_marked_for_checking():
    label = region_for(item("Scam texts", "A new scam.", source="Fraud.org", home="US"))[0]
    assert label.startswith("Probably worldwide") and "Fraud.org mostly covers US" in label and "Check" in label


# --- scoring of the saved samples ------------------------------------------

def test_samples_top_ideas(sample_ideas):
    ranked = [i.item.title for i in rank(sample_ideas)]
    assert ranked[0] == "Chrome emergency update fixes zero-day exploited in attacks"
    assert set(ranked) == {
        "Chrome emergency update fixes zero-day exploited in attacks",
        "New Gemini feature helps spot scam messages",
        "Fake toll payment texts spread in new phishing wave",
        "Wallet Drainer Hits Users Through Fake Airdrop Site",
        "Fraud and scams advisory: November 2026",
        "Malicious Browser Extensions With 2 Million Installs Steal Session Cookies",
        "SampleWallet hack: Private Key Compromised",
        "Android Banking Trojan Reportedly Poses as Bank Apps in Brazil",
        "Watch Out for Fake Delivery Texts This Holiday Season",
    }


@pytest.mark.parametrize("title, reason", [
    ("Acme Security acquires CloudGuard in $2 billion deal", "business news"),
    ("Google Cloud announces record quarterly revenue", "business news"),
    ("13 Malicious NuGet Packages Target .NET Developers With Fake Job Assessments", "companies or IT teams"),
    ("CISA warns of critical flaw in industrial refrigeration controllers", "not about"),
    ("Ransomware gang claims attack on regional hospital network", "not about"),
    ("SampleSwap V3 hack: Incorrect Share Accounting", "no 'what to do'"),
])
def test_samples_skipped(sample_ideas, title, reason):
    idea = by_title(sample_ideas)[title]
    assert idea.skip and reason in idea.skip


def test_action_is_quoted_from_feed_or_article(sample_ideas):
    ideas = by_title(sample_ideas)
    chrome = ideas["Chrome emergency update fixes zero-day exploited in attacks"]
    assert chrome.action == "Users are advised to update Chrome to version 150.0.7800.45 as soon as possible."
    assert chrome.action_from == "feed"
    ext = ideas["Malicious Browser Extensions With 2 Million Installs Steal Session Cookies"]
    assert ext.action.startswith("Users who installed any of the extensions are recommended to remove them")
    assert ext.action_from == "article"
    for idea in rank(sample_ideas):   # every action is a verbatim sentence from the source
        source_text = " ".join([idea.item.summary, idea.item.content]) + " ".join(article_texts())
        assert idea.action in source_text


def article_texts():
    from ideas_samples import DATA
    return [p for f in (DATA / "pages").glob("*.html") for p in article_paragraphs(f.read_text(encoding="utf-8"))]


def test_why_is_the_sources_lead_sentence(sample_ideas):
    advisory = by_title(sample_ideas)["Fraud and scams advisory: November 2026"]
    assert advisory.why.startswith("Scammers are using AI-generated voices to impersonate family members")
    wallet = by_title(sample_ideas)["SampleWallet hack: Private Key Compromised"]
    assert wallet.why.startswith("An attacker reportedly obtained private keys")   # DefiLlama description


def test_uncertain_and_regional_items_are_marked(sample_ideas):
    ideas = by_title(sample_ideas)
    assert ideas["SampleWallet hack: Private Key Compromised"].uncertain == ["reportedly"]
    brazil = ideas["Android Banking Trojan Reportedly Poses as Bank Apps in Brazil"]
    assert brazil.region.startswith("Brazil") and "reportedly" in brazil.uncertain
    usps = ideas["Watch Out for Fake Delivery Texts This Holiday Season"]
    assert usps.region.startswith("US")
    toll = ideas["Fake toll payment texts spread in new phishing wave"]
    assert toll.region == "Worldwide (the source says so)"      # "many countries", from the article page


def test_regional_story_ranks_below_worldwide_ones(sample_ideas):
    ranked = [i.item.title for i in rank(sample_ideas)]
    assert ranked[-1] == "Watch Out for Fake Delivery Texts This Holiday Season"


def test_duplicate_stories_are_merged(sample_ideas):
    chrome = by_title(sample_ideas)["Chrome emergency update fixes zero-day exploited in attacks"]
    assert [a.source for a in chrome.also] == ["The Hacker News"]
    assert "Google Patches Actively Exploited Chrome Zero-Day" not in by_title(sample_ideas)
    assert not same_story(item("Chrome update fixes zero-day"), item("Fake toll payment texts spread"))
