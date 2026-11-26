"""Score feed items as "Stay Safe" short-video topics for a worldwide audience.

Everything shown to the viewer comes from the source: "why it matters" is
the source's lead sentence and "what to do" is a sentence the source itself
says, quoted. The hook is a fixed template for the topic, so it adds no
facts. Items without a "what to do" for ordinary people are skipped, and
wording like "reportedly" is flagged as uncertain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .feeds import Item

# --- topics ----------------------------------------------------------------

SCAM, ACCOUNT, CRYPTO, AI_TOOL = "scam", "account security", "crypto", "AI tool"
CATEGORY_POINTS = {SCAM: 32, ACCOUNT: 28, CRYPTO: 24, AI_TOOL: 20}


@dataclass(frozen=True)
class Topic:
    name: str
    category: str
    pattern: str
    hook: str
    product_hook: Optional[str] = None   # used when a known product is named; {product}
    generic: bool = False                # a catch-all: loses to any specific topic


# Ordered from most to least specific: the first match picks the hook.
TOPICS: Tuple[Topic, ...] = (
    Topic("voice clone", SCAM, r"deepfake|voice clon|cloned voice|ai-generated voice|fake video call",
          "That panicked call from family might not be real"),
    Topic("text scam", SCAM,
          r"smishing|(toll|delivery|package|parcel|postal|redelivery)\b.{0,60}\b(texts?|sms|messages?)\b"
          r"|\b(texts?|sms)\b.{0,60}\b(toll|delivery|package|parcel)",
          "Got a text saying you owe a small fee? Read this first"),
    Topic("job scam", SCAM, r"job (offer|scam)s?|fake (job|recruiter)|recruit(er|ment) scam|task scam",
          "Job offer out of nowhere? Check this before replying"),
    Topic("investment scam", SCAM, r"romance scam|pig butchering|investment scam|fake (trading|investment)",
          "New online friend talking investments? Careful"),
    Topic("tech support scam", SCAM, r"tech support scam|fake (virus|security) (alert|warning|pop-?up)",
          "A pop-up says call support? Don't pick up the phone"),
    Topic("qr scam", SCAM, r"\bqr code|quishing", "Think twice before scanning that QR code"),
    Topic("wallet drainer", CRYPTO,
          r"drainer|fake airdrop|sign(ing|ed)? (a |the )?(malicious )?transaction|token approvals?|approval phishing",
          "Signing that wallet pop-up could empty it"),
    Topic("crypto keys", CRYPTO, r"private keys?|seed phrase|recovery phrase|key compromise",
          "Where your crypto keys live matters. Here's why"),
    Topic("phishing", SCAM, r"phishing|fake (login|sign-in|website|site|page)|impersonat",
          "That message looks real. That's the problem",
          "Got a message from {product}? Look twice"),
    Topic("browser extension", ACCOUNT, r"(malicious|fake|rogue) (browser )?(extension|add-on)s?|extensions? (that )?steal",
          "That browser extension might be watching you"),
    Topic("malicious app", ACCOUNT,
          r"(malicious|fake|rogue|trojani[sz]ed) (android |ios |mobile )?apps?|banking trojan|android malware|spyware|stalkerware",
          "That app might not be what it says it is",
          "That {product} app might not be what it says it is"),
    Topic("urgent update", ACCOUNT,
          r"zero-day|0-day|emergency (security )?update|actively exploited|exploited in (the wild|attacks)",
          "Hit update today. Here's why it can't wait",
          "Using {product}? Update it today"),
    Topic("data breach", ACCOUNT, r"data breach|customer data|data (was |were )?(stolen|leaked|exposed)|leaked (data|database)",
          "Your data could be in this new leak",
          "Use {product}? Read this about their breach"),
    Topic("account takeover", ACCOUNT,
          r"account takeover|hijacked accounts?|sim swap|two-factor|\b2fa\b|\bmfa\b|passkeys?|password (manager|reuse|leak)|infostealer|session cookies",
          "One setting that keeps your accounts yours",
          "Your {product} account needs this one setting"),
    Topic("crypto hack", CRYPTO,
          r"(crypto|defi|wallet|exchange|bridge|protocol|token|blockchain)\b.{0,80}\b(hack|exploit|drain|stolen|theft)"
          r"|(hack|exploit|drain|stolen|theft)\w*\b.{0,80}\b(crypto|defi|wallet|exchange|bridge|token)",
          "Another crypto platform got drained. The lesson for you", generic=True),
    Topic("AI tool", AI_TOOL,
          r"\b(ai|gemini|chatgpt|copilot|claude|llm)\b.{0,80}\b(feature|tool|app|helps?|detects?|protects?|flag)",
          "A new AI feature worth turning on",
          "A new {product} feature worth turning on"),
    Topic("scam", SCAM, r"\bscam|fraud", "A new scam is making the rounds. Spot it fast", generic=True),
)

# Products ordinary viewers use, matched case-insensitively on word boundaries.
PRODUCTS = (
    "Chrome", "Firefox", "Safari", "Edge", "iPhone", "iOS", "Android", "Windows", "macOS", "WhatsApp",
    "Telegram", "Signal", "Gmail", "Outlook", "Instagram", "Facebook", "TikTok", "YouTube", "PayPal",
    "1Password", "LastPass", "Bitwarden", "MetaMask", "Coinbase", "Binance", "Steam", "Discord", "Zoom",
    "Netflix", "Amazon", "Gemini", "ChatGPT", "Copilot",
)
# A product that is only the attack's delivery vehicle shouldn't name the hook's
# subject for these topics (e.g. "Android banking trojan" is about fake bank apps).
PRODUCT_HOOK_MAX = 70

FIRST_PERSON = re.compile(r"\b(i|i'm|i've|me|my|mine|we|we're|us|our|ours)\b", re.I)

CONSUMER = re.compile(
    r"\b(users?|customers?|consumers?|people|shoppers?|account holders?|subscribers?|players?|parents|"
    r"seniors|travell?ers|investors|holders)\b", re.I)
ENTERPRISE = re.compile(
    r"\b(pan-os|firewalls?|vpn (appliance|gateway)s?|servers?|enterprise|critical infrastructure|industrial|"
    r"ics|scada|controllers?|organi[sz]ations|government agenc(y|ies)|ransomware gang|nuget|npm|pypi|"
    r"developers?|devops|kubernetes|geoserver|cve-\d{4}-\d+|data centers?|hospital network|agencies)\b", re.I)
BUSINESS = re.compile(
    r"\b(acquires?|acquisition|merger|funding round|raises \$|series [a-e]\b|earnings|revenue|ipo|"
    r"stock price|shares (rose|fell)|appoints?|new ceo|partnership with|valuation|quarterly)\b", re.I)

# --- regions ---------------------------------------------------------------

REGIONS: Dict[str, str] = {
    "US": r"\bu\.s\.|united states|\bamericans?\b|\birs\b|\busps\b|\bfbi\b|\bftc\b|medicare|social security|"
          r"\bzelle\b|\bvenmo\b|cash app|\bcisa\b|veterans affairs|thanksgiving",
    "UK": r"united kingdom|\bu\.k\.|\buk\b|britain|british|\bhmrc\b|royal mail|\bnhs\b|action fraud",
    "EU": r"\beuropean union\b|\beu\b|\beurope(an)?\b",
    "India": r"\bindia(n)?\b|\bupi\b|aadhaar",
    "Brazil": r"\bbrazil(ian)?\b|\bpix\b",
    "Australia": r"\baustralia(n)?\b|scamwatch",
    "Canada": r"\bcanad(a|ian)\b",
    "Singapore": r"\bsingapore(an)?\b",
    "Japan": r"\bjapan(ese)?\b",
    "South Korea": r"south korea(n)?",
    "Germany": r"\bgerman(y)?\b",
    "France": r"\bfrance\b|\bfrench\b",
    "Italy": r"\bital(y|ian)\b",
    "Switzerland": r"\bswitzerland\b|\bswiss\b",
    "Spain": r"\bspain\b|\bspanish\b",
    "Nigeria": r"\bnigeria(n)?\b",
    "Philippines": r"\bphilippines\b|\bfilipino\b",
    "Asia Pacific": r"asia[- ]pacific|\bapac\b",
    "Latin America": r"latin america",
}
WORLDWIDE_HINT = re.compile(r"\b(worldwide|globally|global|many countries|around the world|international)\b", re.I)

# --- actions and uncertainty -----------------------------------------------

VIEWER = r"(users?|customers?|consumers?|people|owners?|holders?|recipients?|shoppers?|parents|anyone|everyone|you|victims|subscribers?|account holders?)"
USER_ADVICE = [
    re.compile(rf"\b{VIEWER}\b[^.;]{{0,80}}\b(should|must|need to|are (advised|urged|encouraged|recommended|asked|told)|"
               rf"is (advised|recommended)|can protect)\b", re.I),
    re.compile(rf"\b(recommends?|recommended|advises?|advised|urges?|urged|encourages?|encouraged|warns?)\b[^.;]{{0,40}}\b{VIEWER}\b"
               r"[^.;]{0,20}\bto\b", re.I),
]
IMPERATIVE = re.compile(
    r"^(?:(?:so|and|also|instead|to stay safe|to protect yourself|to be safe|if in doubt|for now|"
    r"to turn it on)[,:]?\s+)?"
    r"(update|install|enable|turn (on|off)|change|reset|check|avoid|never|don['’]t|do not|be (wary|careful|cautious|suspicious)|"
    r"verify|report|delete|remove|uninstall|use|contact|hang up|ignore|block|revoke|review|keep|make sure|"
    r"look for|watch out|only|set up|call back|go directly|visit|log in|sign out|stop|pause|treat|open|"
    r"move|don['’]t click|do not click|freeze|lock)\b", re.I)
NOT_FOR_VIEWERS = re.compile(
    r"\b(administrators?|admins|organi[sz]ations?|companies|it teams?|defenders|developers?|security teams?|"
    r"vendors?|network operators?|enterprises?|agencies)\b", re.I)
HEDGES = re.compile(
    r"\b(reportedly|allegedly|unconfirmed|not (yet )?(been )?confirmed|may have|might have|possibly|potentially|"
    r"believed to|appears? to|suspected|unverified|according to (early|initial|preliminary) reports|"
    r"is said to|are said to|claims? to|claimed)\b", re.I)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"'”’)]?\s+(?=[A-Z0-9\"'“‘(])")
MAX_SENTENCE = 280


def sentences(text: str) -> List[str]:
    out = []
    for para in text.split("\n"):
        out += [s.strip() for s in SENTENCE_SPLIT.split(para.strip()) if s.strip()]
    return out


def first_sentence(text: str, limit: int = 300) -> str:
    for s in sentences(text):
        if len(s.split()) >= 5:
            return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"
    return ""


def find_action(texts: Iterable[str]) -> Optional[str]:
    """The source's own advice to ordinary people, quoted; None if it gives none."""
    best: Optional[Tuple[int, str]] = None
    for text in texts:
        for s in sentences(text):
            if len(s) > MAX_SENTENCE or len(s.split()) < 4:
                continue
            if any(p.search(s) for p in USER_ADVICE):
                rank = 0
            elif IMPERATIVE.search(s):
                rank = 1
            else:
                continue
            # Advice addressed to admins or companies isn't something viewers can do.
            subject = s.split(" should ")[0] if " should " in s else s[:60]
            if NOT_FOR_VIEWERS.search(subject) and not re.search(rf"\b{VIEWER}\b", subject, re.I):
                continue
            if best is None or rank < best[0]:
                best = (rank, s)
        if best and best[0] == 0:
            break
    return best[1] if best else None


def hedges_in(*texts: str) -> List[str]:
    found: List[str] = []
    for t in texts:
        for m in HEDGES.finditer(t or ""):
            word = m.group(0).lower()
            if word not in found:
                found.append(word)
    return found


def find_products(text: str) -> List[str]:
    return [p for p in PRODUCTS if re.search(rf"(?<![\w-]){re.escape(p)}(?![\w-])", text, re.I)]


def regions_in(text: str) -> Dict[str, str]:
    """Regions named in the text, with the words that matched."""
    found = {}
    for region, pattern in REGIONS.items():
        m = re.search(pattern, text, re.I)
        if m:
            found[region] = m.group(0)
    return found


# --- scoring ---------------------------------------------------------------

@dataclass
class Idea:
    item: Item
    score: int = 0
    topic: Optional[Topic] = None
    categories: List[str] = field(default_factory=list)
    hook: str = ""
    why: str = ""
    action: Optional[str] = None
    action_from: str = ""            # "feed" or "article"
    region: str = ""
    region_evidence: Dict[str, str] = field(default_factory=dict)
    uncertain: List[str] = field(default_factory=list)
    skip: Optional[str] = None       # reason, when the item is left out
    also: List[Item] = field(default_factory=list)  # the same story from other sources


def classify(item: Item) -> Tuple[Optional[Topic], List[str]]:
    """The item's main topic (first match in the title, else in the text) and all its categories."""
    in_title: List[Topic] = []
    in_text: List[Topic] = []
    for topic in TOPICS:
        if topic.category == AI_TOOL and re.search(r"\b(attacks?|malware|exploit|hack|abuse|warns?)\b", item.title, re.I):
            continue  # AI used *against* people isn't an AI tool tip
        if re.search(topic.pattern, item.title, re.I):
            in_title.append(topic)
        elif re.search(topic.pattern, item.text, re.I):
            in_text.append(topic)
    # Specific topics first (title, then text), then the catch-alls.
    matched = ([t for t in in_title if not t.generic] + [t for t in in_text if not t.generic]
               + [t for t in in_title if t.generic] + [t for t in in_text if t.generic])
    cats: List[str] = []
    for topic in matched:
        if topic.category not in cats:
            cats.append(topic.category)
    return (matched[0] if matched else None), cats


def make_hook(topic: Topic, item: Item) -> str:
    products = find_products(item.title)
    if topic.product_hook and products:
        hook = topic.product_hook.format(product=products[0])
        if len(hook) < PRODUCT_HOOK_MAX:
            return hook
    return topic.hook


def region_for(item: Item, article: Sequence[str] = ()) -> Tuple[str, Dict[str, str], int]:
    """(label, evidence, points). Only what the source names counts."""
    text = " ".join([item.text, *article])
    found = regions_in(text)
    if found:
        names = ", ".join(found)
        return f"{names} (the source names: {', '.join(repr(v) for v in found.values())})", found, -12
    if WORLDWIDE_HINT.search(text):
        return "Worldwide (the source says so)", {}, 10
    if item.home:
        return (f"Probably worldwide: the source names no country, but {item.source} mostly covers "
                f"{item.home}. Check before using.", {}, 2)
    return "Worldwide (the source names no country or region)", {}, 8


def score_item(item: Item, now: datetime, article: Sequence[str] = ()) -> Idea:
    idea = Idea(item)
    title, text = item.title, item.text
    topic, cats = classify(item)
    consumer_topic = topic is not None
    if BUSINESS.search(title) and not (topic and topic.category in (SCAM, ACCOUNT, CRYPTO)):
        idea.skip = "business news"
        return idea
    if not consumer_topic:
        idea.skip = "not about scams, account security, crypto hacks or AI tools"
        return idea
    idea.topic, idea.categories = topic, cats

    enterprise = len(set(m.group(0).lower() for m in ENTERPRISE.finditer(title)))
    enterprise += len(set(m.group(0).lower() for m in ENTERPRISE.finditer(item.summary))) // 2
    products = find_products(text)
    consumer = bool(CONSUMER.search(text)) or bool(products)
    if enterprise >= 2 and not find_products(title):
        idea.skip = "aimed at companies or IT teams, not everyday users"
        return idea

    texts = [item.summary, item.content, *article]
    idea.action = find_action(texts)
    if idea.action:
        in_feed = idea.action in item.summary or idea.action in item.content
        idea.action_from = "feed" if in_feed else "article"
    idea.why = first_sentence(item.content if item.extra else (item.summary or item.content))
    idea.hook = make_hook(topic, item)
    idea.region, idea.region_evidence, region_points = region_for(item, article)
    idea.uncertain = hedges_in(title, idea.why, idea.action or "")
    if item.extra.get("technique", "").lower() == "unknown":
        idea.uncertain.append("technique listed as unknown")

    points = CATEGORY_POINTS[topic.category] + min(10, 5 * (len(cats) - 1))
    points += 12 if products else 0
    points += 6 if consumer else 0
    points += region_points
    points -= 10 * min(enterprise, 2)
    points -= 4 if idea.uncertain else 0
    age_days = max(0.0, (now - item.published).total_seconds() / 86400)
    points += round(6 * max(0.0, 1 - age_days / 7))
    if topic.category == CRYPTO and item.extra.get("target") not in ("", None, "Wallet", "CEX"):
        points -= 6   # protocol-level bug: harder to turn into advice for normal users
    if idea.action:
        points += 20
    idea.score = max(0, min(100, points))
    if not idea.action:
        idea.skip = "the source gives no 'what to do' for viewers"
    return idea


# --- duplicates ------------------------------------------------------------

STOP = set("the and for with from that this into over after new are has have its their how why what you your "
           "was were will can more than about says said warns".split())


def _tokens(title: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", title.lower()) if len(t) >= 3 and t not in STOP}


def same_story(a: Item, b: Item) -> bool:
    ta, tb = _tokens(a.title), _tokens(b.title)
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 3 and len(shared) / min(len(ta), len(tb)) >= 0.5


def merge_duplicates(ideas: List[Idea]) -> List[Idea]:
    """Keep the best-scoring version of each story; note the other sources on it."""
    ordered = sorted(ideas, key=lambda i: (i.skip is not None, -i.score, -i.item.published.timestamp()))
    kept: List[Idea] = []
    for idea in ordered:
        twin = next((k for k in kept if same_story(k.item, idea.item)), None)
        if twin is None:
            kept.append(idea)
        else:
            twin.also.append(idea.item)
    return kept


def rank(ideas: List[Idea]) -> List[Idea]:
    return sorted((i for i in ideas if not i.skip), key=lambda i: (-i.score, -i.item.published.timestamp()))
