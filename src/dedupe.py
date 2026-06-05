from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: str) -> str:
    if not url:
        return ""

    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/+$", "", parts.path or "/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_title(title: str) -> str:
    lowered = (title or "").lower()
    return re.sub(r"[\W_]+", "", lowered, flags=re.UNICODE)


def titles_are_similar(title_a: str, title_b: str, threshold: float = 0.9) -> bool:
    normalized_a = normalize_title(title_a)
    normalized_b = normalize_title(title_b)

    if not normalized_a or not normalized_b:
        return False
    if normalized_a == normalized_b:
        return True

    return SequenceMatcher(None, normalized_a, normalized_b).ratio() >= threshold


def filter_new_items(
    news_items: Iterable[dict],
    is_sent: Callable[[str], bool] | None = None,
    title_threshold: float = 0.9,
) -> list[dict]:
    unique_items: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []

    for item in news_items:
        url = normalize_url(str(item.get("url", "")))
        title = str(item.get("title", "")).strip()

        if not url or not title:
            continue
        if url in seen_urls:
            continue
        if is_sent and is_sent(url):
            continue
        if any(titles_are_similar(title, seen_title, title_threshold) for seen_title in seen_titles):
            continue

        copied = dict(item)
        copied["url"] = url
        copied["title"] = title
        unique_items.append(copied)
        seen_urls.add(url)
        seen_titles.append(title)

    return unique_items
