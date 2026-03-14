#!/usr/bin/env python3
"""
ai_brief.py

AI Society & Economy Brief generator.

What it does:
- Pulls AI-related items from:
  1) NewsAPI (optional, requires NEWSAPI_KEY)
  2) GDELT DOC 2.0 (no key required, but may rate-limit)
  3) RSS feeds, including Substack/newsletters
- Scores items for likely relevance to society/economy impacts
- Deduplicates similar items
- Writes a markdown digest
- Optionally emails the digest

Install:
    py -m pip install requests feedparser python-dateutil

Run:
    py ai_brief.py

Optional environment variables:
    NEWSAPI_KEY=your_newsapi_key
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=465
    SMTP_USER=you@example.com
    SMTP_PASSWORD=your_app_password
    EMAIL_FROM=you@example.com
    EMAIL_TO=you@example.com
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


# -----------------------------
# Configuration
# -----------------------------

DAYS_BACK = 5
MAX_ITEMS_PER_SOURCE = 25
TOP_N_FINAL = 12
REQUEST_TIMEOUT = 20

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "465").strip())
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()

# Search queries
SEARCH_QUERIES = [
    '"artificial intelligence" OR "generative AI" OR "large language model" OR chatbot',
    '"AI" AND (jobs OR labor OR employment OR wages OR productivity OR economy OR business)',
    '"AI regulation" OR "AI policy" OR "AI safety" OR "AI governance"',
    '"AI" AND (education OR schools OR universities OR healthcare OR media OR fraud OR privacy)',
    '"AI" AND (energy OR power OR datacenter OR infrastructure)',
]

# RSS feeds, including newsletters / Substacks
RSS_FEEDS = [
    "https://importai.substack.com/feed",
    "https://www.technologyreview.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://www.understandingai.org/feed",
    "https://www.noemamag.com/feed/",
    "https://www.newyorker.com/feed/news",
    "https://www.newyorker.com/feed/magazine/rss",
]

KEYWORD_WEIGHTS = {
    # direct AI relevance
    "artificial intelligence": 6,
    "generative ai": 6,
    "large language model": 6,
    "large language models": 6,
    "llm": 5,
    "llms": 5,
    "foundation model": 5,
    "foundation models": 5,
    "chatbot": 4,
    "chatbots": 4,
    "agent": 3,
    "agents": 3,
    "agentic": 4,
    "inference": 3,
    "model": 1,

    # economy / work / business
    "jobs": 5,
    "job": 5,
    "labor": 5,
    "employment": 5,
    "workforce": 5,
    "wages": 5,
    "productivity": 6,
    "economy": 6,
    "economic": 5,
    "business": 4,
    "enterprise": 4,
    "industry": 3,
    "industries": 3,
    "market": 3,
    "markets": 3,
    "competition": 3,
    "capital spending": 4,

    # law / policy / governance
    "regulation": 5,
    "policy": 4,
    "governance": 4,
    "antitrust": 4,
    "lawsuit": 4,
    "lawsuits": 4,
    "court": 4,
    "courts": 4,
    "congress": 3,
    "commission": 3,
    "compliance": 3,

    # institutions / society
    "education": 4,
    "school": 3,
    "schools": 3,
    "university": 3,
    "universities": 3,
    "healthcare": 4,
    "hospital": 3,
    "hospitals": 3,
    "misinformation": 4,
    "fraud": 4,
    "bias": 4,
    "copyright": 4,
    "privacy": 4,
    "surveillance": 4,
    "energy": 3,
    "power": 3,
    "electricity": 3,
    "datacenter": 3,
    "data center": 3,
    "election": 3,

    # lower-signal generic launch language
    "launch": 1,
    "launched": 1,
    "announced": 1,
    "release": 1,
}

HIGH_SIGNAL_DOMAINS = {
    "imf.org": 4,
    "oecd.ai": 4,
    "oecd.org": 4,
    "nber.org": 4,
    "hai.stanford.edu": 4,
    "stanford.edu": 3,
    "reuters.com": 3,
    "apnews.com": 3,
    "ft.com": 3,
    "economist.com": 3,
    "bloomberg.com": 3,
    "wsj.com": 3,
    "nytimes.com": 2,
    "technologyreview.com": 2,
    "arstechnica.com": 2,
    "understandingai.org": 2,
    "noemamag.com": 2,
    "substack.com": 1,
}

USER_AGENT = "AIBriefBot/0.2"


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

def recency_score(published_at: Optional[datetime]) -> float:
    if not published_at:
        return 0.5
    age_hours = max((now_utc() - published_at.astimezone(timezone.utc)).total_seconds() / 3600.0, 0.0)
    return max(0.0, 3.5 - math.log1p(age_hours))

def keyword_score(text: str) -> Tuple[float, List[str]]:
    score = 0.0
    tags = []
    lower = text.lower()

    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw in lower:
            score += weight
            tags.append(kw)

    impact_groups = 0
    if any(k in lower for k in ["jobs", "labor", "employment", "wages", "workforce"]):
        impact_groups += 1
    if any(k in lower for k in ["economy", "economic", "productivity", "business", "market", "enterprise"]):
        impact_groups += 1
    if any(k in lower for k in ["regulation", "policy", "governance", "court", "lawsuit", "antitrust", "compliance"]):
        impact_groups += 1
    if any(k in lower for k in ["education", "healthcare", "fraud", "misinformation", "privacy", "copyright", "surveillance"]):
        impact_groups += 1
    if any(k in lower for k in ["energy", "power", "electricity", "datacenter", "data center", "infrastructure"]):
        impact_groups += 1

    if impact_groups >= 2:
        score += 4
    if impact_groups >= 3:
        score += 3

    return score, sorted(set(tags))

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

def short_summary(article: Article) -> str:
    desc = clean_text(article.description or article.content_hint or "")
    lower = f"{article.title} {desc}".lower()

    impact_lines = []

    if any(k in lower for k in ["jobs", "labor", "employment", "wages", "workforce"]):
        impact_lines.append("Likely labor-market or workplace implications.")
    if any(k in lower for k in ["productivity", "business", "enterprise", "industry", "economy", "market"]):
        impact_lines.append("Potential business or macroeconomic relevance.")
    if any(k in lower for k in ["regulation", "policy", "governance", "court", "lawsuit", "antitrust", "compliance"]):
        impact_lines.append("Relevant to regulation, courts, or governance.")
    if any(k in lower for k in ["education", "healthcare", "misinformation", "fraud", "privacy", "copyright", "surveillance"]):
        impact_lines.append("Possible downstream institutional or social effects.")
    if any(k in lower for k in ["energy", "power", "electricity", "datacenter", "data center", "infrastructure"]):
        impact_lines.append("Could matter for infrastructure, energy demand, or deployment economics.")

    if not impact_lines:
        impact_lines.append("Primarily a capability or deployment story; broader effects may emerge later.")

    first_sentence = desc.split(". ")[0].strip()
    if first_sentence and not first_sentence.endswith("."):
        first_sentence += "."
    if not first_sentence:
        first_sentence = "This item appears relevant based on headline and source metadata."

    return f"{first_sentence} {' '.join(impact_lines[:2])}".strip()

def email_is_configured() -> bool:
    return all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO])

def send_email(subject: str, body: str) -> None:
    if not email_is_configured():
        print("[info] email not configured; skipping email delivery")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    print(f"[done] emailed digest to {EMAIL_TO}")


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

    article.raw_score = raw_score
    article.age_score = age
    article.source_score = source
    article.total_score = raw_score + age + source
    article.tags = tags[:8]
    return article

def dedupe_articles(articles: List[Article]) -> List[Article]:
    kept: List[Article] = []
    for article in sorted(articles, key=lambda a: a.total_score, reverse=True):
        if any(is_probable_duplicate(article, existing) for existing in kept):
            continue
        kept.append(article)
    return kept

def group_lane(article: Article) -> str:
    lower = f"{article.title} {article.description} {article.content_hint}".lower()

    if any(k in lower for k in ["jobs", "labor", "employment", "wages", "workforce"]):
        return "Work & labor"
    if any(k in lower for k in ["economy", "productivity", "market", "business", "enterprise", "industry"]):
        return "Economy & business"
    if any(k in lower for k in ["regulation", "policy", "court", "lawsuit", "governance", "antitrust", "compliance"]):
        return "Policy & law"
    if any(k in lower for k in ["education", "healthcare", "privacy", "fraud", "misinformation", "copyright", "surveillance"]):
        return "Social institutions"
    return "Capabilities & deployment"

def generate_digest(articles: List[Article]) -> str:
    today = now_utc().strftime("%Y-%m-%d")
    lines: List[str] = []

    lines.append(f"# AI Society & Economy Brief — {today}")
    lines.append("")
    lines.append("This is an automated first-pass digest ranked for likely relevance to society, work, policy, and the economy.")
    lines.append("")

    lanes: Dict[str, List[Article]] = {}
    for article in articles:
        lane = group_lane(article)
        lanes.setdefault(lane, []).append(article)

    preferred_order = [
        "Work & labor",
        "Economy & business",
        "Policy & law",
        "Social institutions",
        "Capabilities & deployment",
    ]

    for lane in preferred_order:
        lane_items = lanes.get(lane, [])
        if not lane_items:
            continue

        lines.append(f"## {lane}")
        lines.append("")

        for a in lane_items[:4]:
            pub = a.published_at.strftime("%Y-%m-%d %H:%M UTC") if a.published_at else "date unknown"
            tags = ", ".join(a.tags or [])

            lines.append(f"### {a.title}")
            lines.append(f"- Source: {a.source} ({a.domain})")
            lines.append(f"- Published: {pub}")
            lines.append(f"- Score: {a.total_score:.1f}")
            if tags:
                lines.append(f"- Tags: {tags}")
            lines.append(f"- Why it may matter: {short_summary(a)}")
            lines.append(f"- Link: {a.url}")
            lines.append("")

    return "\n".join(lines)

def save_digest(markdown: str) -> str:
    filename = f"ai_digest_{now_utc().strftime('%Y%m%d')}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(markdown)
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
    scored = [a for a in scored if a.total_score >= 6]

    print(f"[info] {len(scored)} items after relevance filtering")

    deduped = dedupe_articles(scored)
    print(f"[info] {len(deduped)} items after dedupe")

    final_items = sorted(deduped, key=lambda a: a.total_score, reverse=True)[:TOP_N_FINAL]

    digest = generate_digest(final_items)
    outfile = save_digest(digest)

    print(f"[done] wrote {outfile}")

    send_email(
        subject=f"AI Society & Economy Brief — {now_utc().strftime('%Y-%m-%d')}",
        body=digest,
    )


if __name__ == "__main__":
    main()
