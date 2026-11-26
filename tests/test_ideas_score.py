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
    ("SampleSwap V3 hack: Incorrect Share Accounting", "protocol-level crypto hack"),
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


# --- regressions from a run against the live feeds -------------------------

def test_headlines_and_cut_off_sentences_are_not_advice():
    assert find_action(["The Important Questions You Should be Asking Your Pentesters"]) is None
    assert find_action(["Users should update their routers and..."]) is None
    assert find_action(["Fusionist advised users to suspend ACE transfers while investigating"]) is None
    assert find_action(["Users should update their routers."]) == "Users should update their routers."


def test_roundups_are_skipped():
    idea = score_item(item("Weekly Recap: Chrome 0-Day, Fake Apps and More", "Users should update Chrome."), NOW)
    assert idea.skip == "a roundup of several stories"


@pytest.mark.parametrize("title, summary", [
    ("VMware vCenter Hit by Critical Auth Bypass Flaw After Zero-Day Exploitation",
     "Broadcom shipped patches. Users are advised to update to the latest version."),
    ("Hackers Exploit Critical ASP.NET Anti-CSRF Library Flaw Allowing Unauthenticated RCE",
     "Researchers have urged users to stop using the library after a flaw was exploited in the wild."),
    ("WHMCS SQL Injection Could Expose Customer Data and Admin Password Hashes",
     "A flaw could let anyone retrieve customer records. Users should update."),
])
def test_server_and_library_flaws_are_it_only(title, summary):
    assert score_item(item(title, summary), NOW).skip == "aimed at companies or IT teams, not everyday users"


def test_consumer_update_is_kept():
    idea = score_item(item("WhatsApp Patches 3 Security Flaws in Android App", "Users should update the app."), NOW)
    assert idea.skip is None and idea.hook == "Using WhatsApp? Update it today"


def test_unpatched_flaw_never_says_update():
    idea = score_item(item("New Unpatched Windows 0-Day Lets Someone Run Programs on a Locked PC",
                           "Users should not leave their PC unattended."), NOW)
    assert idea.hook == "Using Windows? This flaw has no fix yet"
    assert "update" not in idea.hook.lower()


def test_generic_security_words_dont_merge_different_stories():
    a = item("Hackers Exploit Critical ASP.NET Anti-CSRF Library Flaw Allowing Unauthenticated RCE")
    b = item("Hackers now exploit critical Drupal RCE flaw to hijack sites")
    assert not same_story(a, b)
    one = item("1Password Hacked, Customer Data and Source Code Stolen in GitHub Account Breach")
    two = item("1Password customer data stolen after employee GitHub account breach")
    assert same_story(one, two)


def test_same_product_and_topic_merge_only_when_close_in_time():
    a, b = item("Chrome emergency update fixes zero-day"), item("Google patches actively exploited Chrome zero-day")
    topic = classify(a)[0]
    assert same_story(a, b, topic, topic)
    old = item("Google patches actively exploited Chrome zero-day", age_days=6)
    assert not same_story(a, old, topic, topic)


def test_user_facing_defillama_hack_is_kept():
    hack = item("SomeWallet hack: Phishing", content="Attackers phished users. Users should revoke approvals.",
                extra={"target": "DeFi Protocol", "technique": "Phishing", "classification": "Social Engineering"})
    assert score_item(hack, NOW).skip is None


def test_unpatched_hooks_are_short_and_not_first_person():
    from wkvideo.ideas.score import UNPATCHED_HOOK, UNPATCHED_PRODUCT_HOOK

    for hook in (UNPATCHED_HOOK, UNPATCHED_PRODUCT_HOOK.format(product=max(PRODUCTS, key=len))):
        assert len(hook) < 70 and not FIRST_PERSON.search(hook)


def test_advising_form_counts_as_advice():
    s = "WhatsApp is advising users to keep their apps and devices up to date to ensure optimal protection."
    assert find_action([s]) == s


def test_threatsday_bulletin_is_a_roundup():
    title = "ThreatsDay: GPT Jailbreak Flaws, PNG Malware, $500M Ransom Demands + 17 More Stories"
    assert score_item(item(title, "Users should keep an eye out."), NOW).skip == "a roundup of several stories"


def test_product_names_are_case_sensitive_whole_words():
    from wkvideo.ideas.score import find_products

    assert find_products("stop exposing the devices on the internet edge") == []
    assert find_products("a signal to zoom in") == []
    assert find_products("Update Microsoft Edge and Chrome") == ["Microsoft Edge", "Chrome"]


def test_firewall_end_of_life_is_it_only():
    idea = score_item(item("SonicWall Warns Gen 6 Firewalls Won't Get Security Patches, Even for Critical Flaws",
                           "SonicWall urged customers to stop exposing the devices on the internet edge."), NOW)
    assert idea.skip == "aimed at companies or IT teams, not everyday users"


def test_feed_truncation_marker_is_dropped_from_why():
    idea = score_item(item("1Password customer data stolen after GitHub account breach",
                           "1Password has disclosed a data breach affecting some customer data. [...]",
                           "Customers should be wary of emails asking them to reset passwords."), NOW)
    assert idea.why == "1Password has disclosed a data breach affecting some customer data."


@pytest.mark.parametrize("title, topic", [
    ("ROMULUS ClickFix Attacks Deliver RAT That Can Spy on Users and Control PCs", "paste-a-command trick"),
    ("New extortion gang launches large-scale vishing campaign", "phone scam"),
    ("Hackers Exploited GL.iNet Cloud Flaw to Install Malware on Routers", "router"),
])
def test_more_consumer_topics(title, topic):
    assert classify(item(title))[0].name == topic


def test_attacker_steps_are_not_advice():
    steps = ("Use fake Zillow apartment-review pages that serve a fake verification prompt to install "
             "NetSupport RAT on visitors' Windows computers.")
    assert find_action([steps]) is None
    warn = "Never paste a command from a website into the Run box, even if a fake CAPTCHA asks for it."
    assert find_action([steps, warn]) == warn
    assert find_action(["Don't install apps from links in texts, even if they look like your bank's app."])


# --- full-post feeds (Malwarebytes format) ---------------------------------

@pytest.fixture(scope="module")
def malwarebytes():
    from wkvideo.ideas.feeds import Source, parse_rss
    from ideas_samples import DATA

    src = Source("Malwarebytes Labs", "rss", "https://www.malwarebytes.com/blog/feed/index.xml")
    return {i.title: i for i in parse_rss((DATA / "malwarebytes.xml").read_bytes(), src)}


def test_promo_trailer_and_inline_css_are_not_post_text(malwarebytes):
    post = malwarebytes["A bot tested a leaked cloud key within seconds of exposure"]
    assert "downloading Malwarebytes" not in post.content
    assert "Try it now" not in post.content and "{" not in post.content
    assert "Set spending limits and alerts." in post.content


def test_advice_comes_from_the_stay_safe_section(malwarebytes):
    post = malwarebytes["A bot tested a leaked cloud key within seconds of exposure"]
    assert find_action([post.summary, post.content]) == "Keep secrets out of what you share."
    assert region_for(post)[0] == "Worldwide (the source names no country or region)"


def test_topic_comes_from_the_opening_not_a_passing_mention():
    opening = "A bot found a leaked cloud key within seconds. " * 20          # well past the opening
    post = item("A bot tested a leaked cloud key within seconds of exposure",
                content=opening + "One test machine sat behind a home router.")
    topic, _ = classify(post)
    assert topic is None or topic.name != "router"


def test_statistics_and_promos_are_not_advice():
    assert find_action(["Only around 6% went on to sustained use of account resources."]) is None
    assert find_action(["Keep threats off your devices by downloading Malwarebytes today."]) is None
    assert find_action(["Only install apps from the official store."]) == "Only install apps from the official store."


def test_week_in_security_is_a_roundup(malwarebytes):
    idea = score_item(malwarebytes["A week in security (November 16 – November 22)"], NOW)
    assert idea.skip == "a roundup of several stories"


def test_ai_routers_are_not_network_routers():
    ai = item("Researchers planted secrets in AI prompts", content=(
        "Some services take your prompts and send them on to AI providers. These services, known as routers, "
        "give you access to different models."))
    assert classify(ai)[0] is None or classify(ai)[0].name != "router"
    for title in ("Hackers Exploited GL.iNet Cloud Flaw to Install Malware on Routers",
                  "Botnet hijacks home routers to send spam"):
        assert classify(item(title))[0].name == "router"


def test_ai_risk_stories_are_not_ai_tools():
    muse = item("Muse AI got nosy and started making things up, Meta insiders say",
                "Meta's AI agent Muse surfaced information from other apps and helps itself to data.")
    topic, _ = classify(muse)
    assert topic is None or topic.category != "AI tool"
    body_only = item("Company reorganises its security team", content="The AI feature helps users.")
    assert classify(body_only)[0] is None or classify(body_only)[0].category != "AI tool"


def test_menu_steps_with_capitalised_names_are_advice():
    step = "Open About Google Chrome from Chrome’s Settings or Help menu."
    assert find_action([step]) == step


def test_negated_need_and_page_promos_are_not_advice():
    assert find_action(["To avoid a fake giveaway, you don’t need to decide whether every frame is AI-generated."]) is None
    assert find_action(["Stop threats before they can do any harm."]) is None
    assert find_action(["Browser Guard blocks phishing pages and malicious sites automatically."]) is None


def test_catch_all_scam_hook_claims_nothing():
    topic, _ = classify(item("US launches center to disrupt scams and warn victims sooner"))
    assert topic.name == "scam"
    assert "new" not in topic.hook.lower()


@pytest.mark.parametrize("title, topic", [
    ("Deepfake Elon Musk nearly costs viewers $4.7 million in crypto", "deepfake"),
    ("Scammers use AI-generated voices to impersonate a relative in trouble", "voice clone"),
    ("Hackers leak data linked to Visa, Mastercard, and JPMorgan customers", "data breach"),
])
def test_deepfake_voice_clone_and_leak_topics(title, topic):
    assert classify(item(title))[0].name == topic
