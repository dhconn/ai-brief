#!/usr/bin/env python3
"""
ai_brief.py

AI Society & Economy Brief generator.
"""

from __future__ import annotations

import math
import os
import re
import time
import hashlib
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import feedparser
import requests
from dateutil import parser as date_parser
import json


# -----------------------------
# Configuration
# -----------------------------

DAYS_BACK = 5
MAX_ITEMS_PER_SOURCE = 25
TOP_N_FINAL = 10
REQUEST_TIMEOUT = 20

MAX_ARTICLE_AGE_DAYS = 21
STRONG_RECENCY_DAYS = 7
SEEN_FILE = "seen_articles.json"
MAX_SEEN_URLS = 2000

NEWSAPI_KEY = (os.getenv("NEWSAPI_KEY") or "").strip()

SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "465").strip())
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
EMAIL_FROM = (os.getenv("EMAIL_FROM") or "").strip()
EMAIL_TO = (os.getenv("EMAIL_TO") or "").strip()

SEARCH_QUERIES = [
    '"artificial intelligence" OR "generative AI" OR "large language model" OR chatbot',
    '"AI" AND (jobs OR labor OR employment OR wages OR productivity OR economy OR business)',
    '"AI regulation" OR "AI policy" OR "AI safety" OR "AI governance" OR antitrust',
    '"AI" AND (education OR schools OR universities OR healthcare OR media OR fraud OR privacy OR copyright)',
    '"AI" AND (energy OR power OR datacenter OR infrastructure)',
]

RSS_FEEDS = [
    # Newsletters / analysis
    "https://importai.substack.com/feed",
    "https://www.understandingai.org/feed",
    "https://www.noemamag.com/feed/",
    "https://www.exponentialview.co/feed",        # Added: Azeem Azhar (AI & Economy)
    "https://www.deeplearning.ai/the-batch/rss", # Added: Andrew Ng (Tech/Social impact)
    "https://aisnakeoil.substack.com/feed",      # Added: AI Snake Oil (Critical analysis)

    # Research & policy
    "https://hai.stanford.edu/news/rss.xml",
    "https://oecd.ai/en/news/rss",
    "https://www.imf.org/en/Blogs/RSS",
    "https://ainowinstitute.org/category/news/feed", # Added: AI Now (Policy)

    # Corporate & Academic Research
    "https://bair.berkeley.edu/blog/feed.xml",       # Added: Berkeley AI Research
    "https://research.google/blog/rss",              # Added: Google Research

    # Tech & Business journalism
    "https://www.technologyreview.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://venturebeat.com/category/ai/feed/",     # Added: VentureBeat AI
    "https://aibusiness.com/rss.xml",                # Added: AI Business
    "https://www.wired.com/feed/tag/ai/latest/rss",  # Added: Wired
    "https://feeds.bloomberg.com/technology/news.rss" # Added: Bloomberg Tech
]

KEYWORD_WEIGHTS = {
    # AI basics
    "artificial intelligence": 6,
    "generative ai": 6,
    "large language model": 6,
    "large language models": 6,
    "llm": 5,
    "llms": 5,
    "foundation model": 5,
    "foundation models": 5,
    "agent": 2,
    "agents": 2,
    "agentic": 3,
    "chatbot": 2,
    "chatbots": 2,
    "inference": 2,

    # economy, work, productivity
    "jobs": 7,
    "job": 7,
    "labor": 7,
    "employment": 7,
    "workforce": 7,
    "wages": 7,
    "productivity": 8,
    "economy": 8,
    "economic": 7,
    "business": 5,
    "enterprise": 4,
    "industry": 4,
    "industries": 4,
    "market": 4,
    "markets": 4,
    "competition": 4,
    "capital spending": 5,

    # law / policy / governance
    "regulation": 7,
    "policy": 6,
    "governance": 6,
    "antitrust": 6,
    "lawsuit": 5,
    "lawsuits": 5,
    "court": 5,
    "courts": 5,
    "congress": 4,
    "commission": 4,
    "compliance": 4,

    # institutions / society
    "education": 6,
    "school": 4,
    "schools": 4,
    "university": 4,
    "universities": 4,
    "healthcare": 6,
    "hospital": 4,
    "hospitals": 4,
    "misinformation": 6,
    "fraud": 6,
    "bias": 5,
    "copyright": 6,
    "privacy": 6,
    "surveillance": 6,
    "election": 4,

    # infrastructure / deployment economics
    "energy": 5,
    "power": 5,
    "electricity": 5,
    "datacenter": 5,
    "data center": 5,
    "infrastructure": 4,

    # downweight generic launch chatter
    "launch": 0,
    "launched": 0,
    "announced": 0,
    "release": 0,
}

HIGH_SIGNAL_DOMAINS = {
    "imf.org": 5,
    "oecd.ai": 5,
    "oecd.org": 5,
    "nber.org": 5,
    "hai.stanford.edu": 5,
    "exponentialview.co": 5,      # New
    "ainowinstitute.org": 5,     # New
    "aisnakeoil.substack.com": 4, # New (Specific high-quality newsletter)
    "deeplearning.ai": 4,         # New
    "bair.berkeley.edu": 4,      # New
    "research.google": 4,         # New
    "stanford.edu": 4,
    "reuters.com": 4,
    "apnews.com": 4,
    "ft.com": 4,
    "economist.com": 4,
    "bloomberg.com": 4,
    "wsj.com": 4,
    "venturebeat.com": 3,         # New
    "aibusiness.com": 3,          # New
    "technologyreview.com": 3,
    "arstechnica.com": 3,
    "understandingai.org": 3,
    "noemamag.com": 3,
    "nytimes.com": 3,
    "wired.com": 2,               # New
    "substack.com": 1,
}

LOW_SIGNAL_PATTERNS = [
    "funding round",
    "raises",
    "seed round",
    "series a",
    "series b",
    "product launch",
]

USER_AGENT = "AIBriefBot/0.3"


# -----------------------------
# Data model
# -----------------------------

@dataclass
class Article:
    source: str
    title: str
    url: str
    published_at: Optional[datetime]
    description: str
    content_hint: str
    domain: str
    query: str
    raw_score: float = 0.0
    age_score: float = 0.0
    source_score: float = 0.0
    impact_score: float = 0.0
    penalty_score: float = 0.0
    total_score: float = 0.0
    tags: Optional[List[str]] = None


# -----------------------------
# Helpers
# -----------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def iso_days_ago(days: int) -> str:
    return (now_utc() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return normalize_whitespace(text)

def extract_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        domain = ""
    return domain.replace("www.", "")

def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

def article_age_days(published_at: Optional[datetime]) -> Optional[float]:
    if not published_at:
        return None
    return max((now_utc() - published_at.astimezone(timezone.utc)).total_seconds() / 86400.0, 0.0)

def recency_score(published_at: Optional[datetime]) -> float:
    age_days = article_age_days(published_at)
    if age_days is None:
        return 0.0
    if age_days <= 1:
        return 6.0
    if age_days <= 3:
        return 5.0
    if age_days <= STRONG_RECENCY_DAYS:
        return 3.5
    if age_days <= 14:
        return 1.5
    if age_days <= MAX_ARTICLE_AGE_DAYS:
        return 0.5
    return -10.0

def keyword_score(text: str) -> Tuple[float, List[str]]:
    score = 0.0
    tags = []
    lower = text.lower()

    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw in lower:
            score += weight
            tags.append(kw)

    return score, sorted(set(tags))

def impact_lane_score(text: str) -> float:
    lower = text.lower()

    work = any(k in lower for k in ["jobs", "labor", "employment", "wages", "workforce"])
    econ = any(k in lower for k in ["economy", "economic", "productivity", "business", "market", "enterprise", "industry"])
    policy = any(k in lower for k in ["regulation", "policy", "governance", "court", "lawsuit", "antitrust", "compliance"])
    society = any(k in lower for k in ["education", "healthcare", "fraud", "misinformation", "privacy", "copyright", "surveillance"])
    infra = any(k in lower for k in ["energy", "power", "electricity", "datacenter", "data center", "infrastructure"])

    lanes = sum([work, econ, policy, society, infra])

    if lanes >= 4:
        return 8.0
    if lanes == 3:
        return 6.0
    if lanes == 2:
        return 4.0
    if lanes == 1:
        return 1.0
    return 0.0

def generic_story_penalty(text: str) -> float:
    lower = text.lower()
    penalty = 0.0

    generic_capability = any(k in lower for k in ["agent", "agents", "chatbot", "chatbots", "llm", "llms"])
    real_world = any(k in lower for k in [
        "jobs", "labor", "employment", "wages", "productivity", "economy", "policy", "regulation",
        "education", "healthcare", "privacy", "fraud", "energy", "power", "copyright"
    ])

    if generic_capability and not real_world:
        penalty += 3.0

    for pattern in LOW_SIGNAL_PATTERNS:
        if pattern in lower:
            penalty += 2.0

    return penalty

def source_signal_score(domain: str) -> float:
    for candidate, weight in HIGH_SIGNAL_DOMAINS.items():
        if domain == candidate or domain.endswith("." + candidate):
            return float(weight)
    return 0.0

def safe_get(url: str, params: Optional[Dict] = None) -> Optional[requests.Response]:
    max_attempts = 4
    backoff_seconds = 5

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )

            if resp.status_code == 429:
                print(f"[warn] rate limited: {url} :: attempt {attempt}/{max_attempts}")
                if attempt < max_attempts:
                    time.sleep(backoff_seconds * attempt)
                    continue

            resp.raise_for_status()
            return resp

        except requests.exceptions.RequestException as exc:
            print(f"[warn] request failed: {url} :: {exc}")
            if attempt < max_attempts:
                time.sleep(backoff_seconds * attempt)
            else:
                return None

    return None

def title_fingerprint(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha1(t.encode("utf-8")).hexdigest()

def is_probable_duplicate(a: Article, b: Article) -> bool:
    if a.url == b.url:
        return True
    if title_fingerprint(a.title) == title_fingerprint(b.title):
        return True

    wa = set(w for w in re.findall(r"[a-z0-9]+", a.title.lower()) if len(w) > 3)
    wb = set(w for w in re.findall(r"[a-z0-9]+", b.title.lower()) if len(w) > 3)
    if not wa or not wb:
        return False

    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    return overlap >= 0.8

def classify_lane(text: str) -> str:
    lower = text.lower()

    if any(k in lower for k in ["jobs", "labor", "employment", "wages", "workforce"]):
        return "Work & labor"
    if any(k in lower for k in ["economy", "productivity", "market", "business", "enterprise", "industry"]):
        return "Economy & business"
    if any(k in lower for k in ["regulation", "policy", "court", "lawsuit", "governance", "antitrust", "compliance"]):
        return "Policy & law"
    if any(k in lower for k in ["education", "healthcare", "privacy", "fraud", "misinformation", "copyright", "surveillance"]):
        return "Social institutions"
    return "Capabilities & deployment"

def is_analysis_source(article: Article) -> bool:
    return article.domain in [
        "importai.substack.com",
        "understandingai.org",
        "noemamag.com",
        "exponentialview.co",        # Added
        "deeplearning.ai",           # Added
        "aisnakeoil.substack.com",   # Added
    ]

def short_summary(article: Article) -> str:
    desc = clean_text(article.description or article.content_hint or "")
    combined = f"{article.title}. {desc}".strip()
    lane = classify_lane(combined)
    lower = combined.lower()

    what_happened = desc.split(". ")[0].strip() if desc else article.title.strip()
    if what_happened and not what_happened.endswith("."):
        what_happened += "."

    if lane == "Work & labor":
        why = "This could affect hiring, task design, wages, or how human work is allocated."
        watch = "Watch for evidence of adoption at scale, measurable productivity gains, or workforce displacement."
    elif lane == "Economy & business":
        why = "This matters if it changes enterprise adoption, competitive dynamics, capital spending, or productivity."
        watch = "Watch for follow-on moves by large firms, spending commitments, and proof of business value."
    elif lane == "Policy & law":
        why = "This matters because legal and regulatory decisions will shape how quickly AI deploys and under what constraints."
        watch = "Watch for agency action, court rulings, and whether this becomes a broader precedent."
    elif lane == "Social institutions":
        why = "This matters because AI effects often show up first in trust-sensitive institutions such as schools, media, healthcare, and privacy."
        watch = "Watch for replication, real-world incidents, and institutional responses."
    else:
        why = "This is mainly a capability or deployment development, but it could become more important if it changes costs or reliability."
        watch = "Watch for signs that it moves from demo or product news into measurable real-world impact."

    if any(k in lower for k in ["energy", "power", "electricity", "datacenter", "data center", "infrastructure"]):
        why += " It may also matter for infrastructure demand and energy economics."

    return f"What happened: {what_happened} Why it matters: {why} What to watch: {watch}"

def email_is_configured() -> bool:
    return all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO])

def send_email(subject: str, body: str) -> None:
    if not email_is_configured():
        print("[info] email not configured; skipping email delivery")
        return

    try:
        # We explicitly use 'html' and ensure the charset is utf-8
        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        # Added for Gmail: Explicitly set Content-Type header again
        msg.add_header("Content-Type", "text/html")

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        print(f"[done] emailed digest to {EMAIL_TO}")
    except Exception as exc:
        print(f"[warn] email delivery failed: {exc}")

def load_seen_urls() -> set[str]:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(str(x) for x in data if x)
        return set()
    except FileNotFoundError:
        return set()
    except Exception as exc:
        print(f"[warn] could not read {SEEN_FILE}: {exc}")
        return set()


def save_seen_urls(urls: set[str]) -> None:
    trimmed = sorted(urls)[-MAX_SEEN_URLS:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2)


def filter_unseen_articles(articles: List[Article], seen_urls: set[str]) -> List[Article]:
    return [a for a in articles if a.url not in seen_urls]


def update_seen_urls(seen_urls: set[str], articles: List[Article]) -> set[str]:
    for a in articles:
        seen_urls.add(a.url)
    return seen_urls

# -----------------------------
# Source connectors
# -----------------------------

def fetch_newsapi(query: str) -> List[Article]:
    if not NEWSAPI_KEY:
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "from": iso_days_ago(DAYS_BACK),
        "pageSize": MAX_ITEMS_PER_SOURCE,
        "apiKey": NEWSAPI_KEY,
    }

    resp = safe_get(url, params=params)
    if not resp:
        return []

    try:
        payload = resp.json()
    except Exception:
        return []

    items = []
    for item in payload.get("articles", []):
        title = clean_text(item.get("title", ""))
        url_ = item.get("url", "")
        if not title or not url_:
            continue

        items.append(
            Article(
                source=(item.get("source") or {}).get("name", "NewsAPI"),
                title=title,
                url=url_,
                published_at=parse_dt(item.get("publishedAt")),
                description=clean_text(item.get("description", "")),
                content_hint=clean_text(item.get("content", "")),
                domain=extract_domain(url_),
                query=query,
            )
        )
    return items

def fetch_gdelt(query: str) -> List[Article]:
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(MAX_ITEMS_PER_SOURCE),
        "sort": "DateDesc",
        "timespan": f"{DAYS_BACK}d",
    }

    resp = safe_get(url, params=params)
    if not resp:
        return []

    try:
        payload = resp.json()
    except Exception:
        return []

    items = []
    for item in payload.get("articles", []):
        title = clean_text(item.get("title", ""))
        url_ = item.get("url", "")
        if not title or not url_:
            continue

        items.append(
            Article(
                source=clean_text(item.get("sourcecountry", "") or extract_domain(url_) or "GDELT"),
                title=title,
                url=url_,
                published_at=parse_dt(item.get("seendate")),
                description=clean_text(item.get("excerpt", "")),
                content_hint="",
                domain=extract_domain(url_),
                query=query,
            )
        )
    return items

def fetch_rss() -> List[Article]:
    items: List[Article] = []

    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            print(f"[warn] rss parse failed: {feed_url} :: {exc}")
            continue

        feed_title = clean_text(getattr(parsed.feed, "title", "") or "RSS")

        for entry in parsed.entries[:MAX_ITEMS_PER_SOURCE]:
            title = clean_text(getattr(entry, "title", ""))
            url_ = getattr(entry, "link", "")
            if not title or not url_:
                continue

            published_at = None
            for field in ("published", "updated", "created"):
                if getattr(entry, field, None):
                    published_at = parse_dt(getattr(entry, field))
                    if published_at:
                        break

            description = clean_text(getattr(entry, "summary", "") or "")

            items.append(
                Article(
                    source=feed_title,
                    title=title,
                    url=url_,
                    published_at=published_at,
                    description=description,
                    content_hint="",
                    domain=extract_domain(url_),
                    query="rss",
                )
            )

    return items


# -----------------------------
# Ranking and digest generation
# -----------------------------

def score_article(article: Article) -> Article:
    combined = " ".join([
        article.title or "",
        article.description or "",
        article.content_hint or "",
        article.domain or "",
    ])

    raw_score, tags = keyword_score(combined)
    age = recency_score(article.published_at)
    source = source_signal_score(article.domain)
    impact = impact_lane_score(combined)
    penalty = generic_story_penalty(combined)

    article.raw_score = raw_score
    article.age_score = age
    article.source_score = source
    article.impact_score = impact
    article.penalty_score = penalty
    article.total_score = raw_score + age + source + impact - penalty
    article.tags = tags[:8]
    return article

def dedupe_articles(articles: List[Article]) -> List[Article]:
    kept: List[Article] = []
    for article in sorted(articles, key=lambda a: a.total_score, reverse=True):
        if any(is_probable_duplicate(article, existing) for existing in kept):
            continue
        kept.append(article)
    return kept

def filter_articles(articles: List[Article]) -> List[Article]:
    filtered = []
    for a in articles:
        age_days = article_age_days(a.published_at)
        if age_days is not None and age_days > MAX_ARTICLE_AGE_DAYS:
            continue
        if a.total_score < 9:
            continue
        filtered.append(a)
    return filtered

def generate_digest(articles: List[Article]) -> str:
    today = now_utc().strftime("%Y-%m-%d")
    lines: List[str] = []

    # Start HTML Document
    lines.append("<html>")
    lines.append("<head><style>body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; } h1 { color: #2c3e50; } h2 { border-bottom: 2px solid #eee; padding-top: 20px; } h3 { margin-bottom: 5px; color: #2980b9; }</style></head>")
    lines.append("<body>")
    
    lines.append(f"<h1>AI Society & Economy Brief — {today}</h1>")
    lines.append("<p>This is an automated digest ranked for likely relevance to society, work, policy, and the economy.</p>")

    # 1. Analysis / Commentary Section
    analysis_articles = [a for a in articles if is_analysis_source(a)]
    if analysis_articles:
        lines.append("<h2>AI Analysis & Commentary</h2>")
        for a in analysis_articles[:3]:
            pub = a.published_at.strftime("%Y-%m-%d %H:%M UTC") if a.published_at else "date unknown"
            lines.append("<div>")
            lines.append(f"<h3><a href='{a.url}'>{a.title}</a></h3>")
            lines.append(f"<p><strong>Source:</strong> {a.source} ({a.domain})<br>")
            lines.append(f"<strong>Published:</strong> {pub}</p>")
            lines.append(f"<p><em>{short_summary(a)}</em></p>")
            lines.append("</div><hr>")

    # 2. Thematic Lanes
    other_articles = [a for a in articles if not is_analysis_source(a)]
    lanes_map: Dict[str, List[Article]] = {}
    for article in other_articles:
        lane = classify_lane(f"{article.title} {article.description}")
        lanes_map.setdefault(lane, []).append(article)

    preferred_order = ["Work & labor", "Economy & business", "Policy & law", "Social institutions", "Capabilities & deployment"]

    for lane in preferred_order:
        items = lanes_map.get(lane, [])
        if not items:
            continue

        lines.append(f"<h2>{lane}</h2>")
        for a in items[:3]:
            pub = a.published_at.strftime("%Y-%m-%d %H:%M UTC") if a.published_at else "date unknown"
            tags = ", ".join(a.tags or [])
            lines.append("<div>")
            lines.append(f"<h3><a href='{a.url}'>{a.title}</a></h3>")
            lines.append(f"<p><strong>Source:</strong> {a.source} | <strong>Score:</strong> {a.total_score:.1f}</p>")
            if tags:
                lines.append(f"<p><small>Tags: {tags}</small></p>")
            lines.append(f"<p>{short_summary(a)}</p>")
            lines.append("</div>")

    # Close HTML Document
    lines.append("</body></html>")
    return "\n".join(lines)

def save_digest(html_content: str) -> str:
    os.makedirs("archive", exist_ok=True)
    # Changed extension to .html to ensure we aren't confusing the format
    filename = f"archive/ai_digest_{now_utc().strftime('%Y%m%d')}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    return filename

# -----------------------------
# Main
# -----------------------------

def main() -> None:
    print("[info] collecting articles...")
    articles: List[Article] = []

    for query in SEARCH_QUERIES:
        if NEWSAPI_KEY:
            articles.extend(fetch_newsapi(query))
            time.sleep(1)

        articles.extend(fetch_gdelt(query))
        time.sleep(4)

    articles.extend(fetch_rss())

    print(f"[info] fetched {len(articles)} raw items")

    scored = [score_article(a) for a in articles]
    filtered = filter_articles(scored)
    print(f"[info] {len(filtered)} items after filtering")

    deduped = dedupe_articles(filtered)
    print(f"[info] {len(deduped)} items after dedupe")

    seen_urls = load_seen_urls()
    unseen = filter_unseen_articles(deduped, seen_urls)
    print(f"[info] {len(unseen)} items after seen-article filtering")

    analysis_items = [a for a in unseen if is_analysis_source(a)]
    analysis_items = sorted(analysis_items, key=lambda a: a.total_score, reverse=True)[:3]
    normal_items = [a for a in unseen if not is_analysis_source(a)]
    normal_items = sorted(normal_items, key=lambda a: a.total_score, reverse=True)[:TOP_N_FINAL]

final_items = analysis_items + normal_items

    if len(final_items) < 5:
        print("[info] not enough unseen items; allowing fallback items")
        final_items = sorted(deduped, key=lambda a: a.total_score, reverse=True)[:TOP_N_FINAL]

    digest = generate_digest(final_items)
    outfile = save_digest(digest)

    print(f"[done] wrote {outfile}")

    seen_urls = update_seen_urls(seen_urls, final_items)
    save_seen_urls(seen_urls)
    print(f"[done] updated {SEEN_FILE}")

    send_email(
        subject=f"AI Society & Economy Brief — {now_utc().strftime('%Y-%m-%d')}",
        body=digest,
    )

if __name__ == "__main__":
    main()
