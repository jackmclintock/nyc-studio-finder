"""Thin Tavily client. Used only when a source blocks direct requests.

Credits: search basic = 1, advanced = 2. Extract basic = 1 per 5 URLs, advanced = 2 per 5 URLs.
Every call increments a module-level counter so run.py can enforce the per-run budget.
"""
import requests
from . import config

credits_used = 0
API = "https://api.tavily.com"


class TavilyUnavailable(Exception):
    pass


def _key():
    if not config.TAVILY_API_KEY:
        raise TavilyUnavailable("TAVILY_API_KEY not set")
    return config.TAVILY_API_KEY


def _charge(n):
    global credits_used
    if credits_used + n > config.TAVILY_MAX_CREDITS_PER_RUN:
        raise TavilyUnavailable(f"Tavily budget exhausted for this run ({credits_used} used)")
    credits_used += n


def _refund(n):
    global credits_used
    credits_used = max(0, credits_used - n)


def extract(urls, depth="basic"):
    """Return {url: raw_content} for up to 20 URLs. Charged per 5 URLs."""
    if isinstance(urls, str):
        urls = [urls]
    urls = urls[:20]
    key = _key()
    cost = (1 if depth == "basic" else 2) * ((len(urls) + 4) // 5)
    _charge(cost)
    try:
        r = requests.post(f"{API}/extract", json={"urls": urls, "extract_depth": depth},
                          headers={"Authorization": f"Bearer {key}"}, timeout=60)
        r.raise_for_status()
    except requests.RequestException:
        _refund(cost)
        raise
    data = r.json()
    out = {item["url"]: item.get("raw_content", "") for item in data.get("results", [])}
    for f in data.get("failed_results", []):
        out.setdefault(f.get("url"), "")
    return out


def search(query, include_domains=None, max_results=10, depth="basic", days=None):
    """Return list of {url, title, content}. 1 credit basic, 2 advanced."""
    key = _key()
    cost = 1 if depth == "basic" else 2
    _charge(cost)
    body = {"query": query, "search_depth": depth, "max_results": max_results,
            "include_raw_content": False}
    if include_domains:
        body["include_domains"] = include_domains
    if days:
        body["days"] = days
        body["topic"] = "news"
    try:
        r = requests.post(f"{API}/search", json=body,
                          headers={"Authorization": f"Bearer {key}"}, timeout=60)
        r.raise_for_status()
    except requests.RequestException:
        _refund(cost)
        raise
    return r.json().get("results", [])
