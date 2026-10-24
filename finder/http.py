"""Shared HTTP session with sane headers, retries and a blocked-detection helper."""
import random
import time
import requests

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


class Blocked(Exception):
    """Raised when a source refuses us (403, captcha, bot wall). Caller may fall back to Tavily."""


def session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def get(url, s=None, retries=2, timeout=25, **kw):
    s = s or session()
    last = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, timeout=timeout, **kw)
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (403, 429, 503) or looks_like_bot_wall(r.text):
            raise Blocked(f"{url} -> {r.status_code}")
        if r.status_code >= 500:
            last = RuntimeError(f"{url} -> {r.status_code}")
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r
    raise last or RuntimeError("request failed")


BOT_MARKERS = (
    "captcha", "are you a human", "access denied", "px-captcha", "perimeterx",
    "unusual traffic", "verify you are human", "cf-challenge", "just a moment...",
    "client challenge", "_fs-ch-",
)


def looks_like_bot_wall(text):
    if not text or len(text) > 20000:
        return False
    t = text.lower()
    return any(m in t for m in BOT_MARKERS)
