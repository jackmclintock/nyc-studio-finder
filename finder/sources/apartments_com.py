"""Apartments.com (CoStar). Akamai returns 403 "Access Denied" to datacenter IPs.

We try the filtered studio search page once directly (the HTML parser is ready
if it ever gets through), then fall back to one Tavily extract call covering
three search-results URLs (1 credit: Tavily charges per 5 URLs).
"""
import hashlib
import re

import requests
from bs4 import BeautifulSoup

from .. import tavily
from ..http import Blocked, get, looks_like_bot_wall
from . import base

NAME = "apartments_com"
LABEL = "Apartments.com"
NEEDS_TAVILY = True
TAVILY_REFRESH_HOURS = 6   # the only Tavily consumer: 1 credit per refresh, ~120 a month

SITE = "https://www.apartments.com"
SEARCH_URLS = [
    f"{SITE}/new-york-ny/studios-under-2500/",
    f"{SITE}/manhattan-ny/studios-under-2500/",
    f"{SITE}/williamsburg-brooklyn-ny/studios-under-2500/",
]

# Property pages: /<property-slug>/<id>/ where id is ~7 lowercase alphanumerics.
LISTING_RE = re.compile(r"(?:https?://(?:www\.)?apartments\.com)?/([a-z0-9-]+-ny)/([a-z0-9]{6,8})/")
CITY_SLUGS = {"new-york-ny", "manhattan-ny", "brooklyn-ny", "williamsburg-brooklyn-ny"}
NOT_IDS = {"studios", "student", "luxury", "houses", "condos", "lofts", "cheap", "pet", "amenities"}

ZIP_RE = re.compile(r"\b(?:NY|New York)[\s,-]+(1[01]\d{3})\b")
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
PRICE = re.compile(r"\$\s?(\d{1,2},?\d{3})(?:\s*[-–]\s*\$?\s?(\d{1,2},?\d{3}))?\+?")
STUDIO = re.compile(r"\bstudio\b", re.I)
MULTI_BED = re.compile(r"\b[1-9]\s*(?:beds?|bedrooms?|br|bd)\b", re.I)
ADDRESS_RE = re.compile(r"([0-9][^\n\[\]|$]{3,80}?,\s*(?:New York|Brooklyn|Manhattan),\s*NY\s*1\d{4})")


def _zip(*texts):
    for t in texts:
        if t:
            m = ZIP_RE.search(t)
            if m:
                return m.group(1)
    return None


def _id(url):
    m = LISTING_RE.search(url)
    return m.group(2) if m else hashlib.sha1(url.encode()).hexdigest()[:16]


def _make(pid, url, price_lo, price_hi, title, address, card_text, via_tavily):
    flags = base.extra_flags(card_text)
    if price_hi and base.parse_price(price_hi) != base.parse_price(price_lo):
        flags.append("price-from")
    return base.make(NAME, pid, url, price_lo, f"Studio - {title}" if title else "Studio",
                     zipcode=_zip(address, card_text), text=" ".join(filter(None, [title, address])),
                     address=address, flags=flags, via_tavily=via_tavily)


# --- direct HTML route (Akamai-blocked as of 2026-10) ------------------------

def _from_html(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("article.placard, li.mortar-wrapper article, article[data-listingid]"):
        url = card.get("data-url") or (card.select_one("a.property-link") or {}).get("href")
        if not url:
            continue
        pid = card.get("data-listingid") or _id(url)
        title_el = card.select_one(".property-title, .js-placardTitle")
        addr_el = card.select_one(".property-address, .property-address-wrapper")
        price_el = card.select_one(".property-pricing, .price-range, .property-rents")
        beds_el = card.select_one(".property-beds, .bed-range")
        beds = beds_el.get_text(" ", strip=True) if beds_el else ""
        if beds and not STUDIO.search(beds):
            continue
        m = PRICE.search(price_el.get_text(" ", strip=True) if price_el else "")
        if not m:
            continue
        address = addr_el.get_text(" ", strip=True) if addr_el else None
        title = title_el.get_text(" ", strip=True) if title_el else address
        out.append(_make(pid, url, m.group(1), m.group(2), title, address,
                         card.get_text(" ", strip=True), False))
    return out


def _direct():
    # One direct request only: if it is blocked the others will be too.
    r = get(SEARCH_URLS[0], retries=0)
    out = _from_html(r.text)
    if not out:
        raise Blocked("apartments.com page had no parsable listing cards")
    return out


# --- Tavily route ------------------------------------------------------------

CARD_RE = re.compile(
    r"\[(?P<name>[^\]\n]{2,120})\n\n(?P<addr>[^\]\n]{5,160})\]\((?P<url>https?://(?:www\.)?apartments\.com/[a-z0-9-]+/[a-z0-9]{6,8}/)\)")
ROOM_RE = re.compile(r"\bRoom for Rent\b", re.I)
STUDENT_RE = re.compile(r"\b(FOUND Study|student|intern|dorm)\b", re.I)


def _parse_tavily(text):
    """Tavily returns each card as "[Name\n\nAddress](url)" followed by lines like
    "Studio", "$1,800" or "$2,080+", "Total Monthly Price", "Apartment for Rent"
    or "Room for Rent", until the next card link."""
    out = []
    # Unit rows render as "[Studio\n\n$2,400](url)" links; keep only real property
    # cards (name + postal address) as segment boundaries so unit rows stay with their card.
    cards = [m for m in CARD_RE.finditer(text) if re.search(r",\s*[A-Z]{2}\s*\d{5}", m.group("addr"))]
    for i, m in enumerate(cards):
        name, address, url = m.group("name").strip(), m.group("addr").strip(), m.group("url")
        seg = text[m.end(): cards[i + 1].start() if i + 1 < len(cards) else m.end() + 1500]
        seg = IMG.sub(" ", seg)
        plain = MD_LINK.sub(lambda mm: mm.group(1), seg)
        plain = re.sub(r"https?://\S+", " ", plain)
        if not STUDIO.search(plain):
            continue
        # Price belongs to this card only if it appears before any bed count other than studio.
        pm = PRICE.search(plain)
        if not pm:
            continue
        if MULTI_BED.search(plain[: pm.start()]):
            continue
        flags = []
        if ROOM_RE.search(plain):
            flags.append("room-or-share")
        if STUDENT_RE.search(name + " " + plain[:200]):
            flags.append("student-housing")
        if ", NJ" in address or ", CT" in address:
            continue
        l = _make(_id(url), url, pm.group(1), pm.group(2), name, address, plain, True)
        if l:
            l.flags = list(dict.fromkeys(l.flags + flags))
            out.append(l)
    return out


def _via_tavily(reason):
    try:
        pages = tavily.extract(SEARCH_URLS)  # 3 URLs -> 1 credit
    except tavily.TavilyUnavailable as e:
        raise Blocked(f"{reason}; tavily unavailable: {e}")
    except requests.RequestException as e:
        raise Blocked(f"{reason}; tavily failed: {e}")
    text = "\n\n".join(v or "" for v in pages.values())
    if not text.strip() or (looks_like_bot_wall(text) and not LISTING_RE.search(text)):
        raise Blocked(f"{reason}; tavily got no usable content")
    return _parse_tavily(text)


def _dedupe(listings):
    seen, out = set(), []
    for l in listings:
        if l and l.source_id not in seen:
            seen.add(l.source_id)
            l.flags = list(dict.fromkeys(l.flags))
            out.append(l)
    return out


def fetch():
    try:
        return _dedupe(_direct())
    except Blocked as e:
        return _dedupe(_via_tavily(str(e)))
