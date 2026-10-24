"""Listings Project (listingsproject.com): weekly, community-vetted listings, mostly sublets and shares.

The old /listings/new-york-city URL returns 410. The current NYC real-estate
index is server-rendered HTML split by category:

    https://www.listingsproject.com/real-estate/new-york-city/rentals?subcategories[]=apt_rent&keyword=studio&sort=least-expensive
    https://www.listingsproject.com/real-estate/new-york-city/sublets?subcategories[]=apt_sublet&keyword=studio&sort=least-expensive

The subcategory filter leaves out rooms (room_rent and room_sublet), so only
whole apartments come back. keyword=studio matches title and body text, which
cuts the result set to roughly 10 rentals and 25 sublets. There is no bedroom
or price filter. sort=least-expensive sorts on the raw number whatever the
unit ($120/day comes before $1,800/month), so once a page shows a number above
budget, everything after it is above budget too, and pagination stops there.

Quirks:
- Each page starts with one or two "Featured Listing" cards that ignore the
  sort, so they are skipped when deciding whether to stop paginating.
- Prices may be /month, /week, /day or a bare total for fixed dates. Only
  /month prices are kept, because the others are short stays.
- There is no structured bedroom count. A listing counts as a studio only if its
  title says studio (not "studio space", "art studio" or "office/studio") and
  does not mention a bedroom count. Alcove and junior-1BR flags come from base.
- The neighbourhood field is free text ("Kips Bay, Manhattan",
  "Greenpoint a few blocks from the Williamsburg border, Brooklyn"). Only the
  part before the first comma is passed as raw_neigh. The substring matcher in
  normalize() can still misread phrases like "near Williamsburg", so a raw name
  that starts with a known out-of-area neighbourhood is rejected.
- Sublets and lease takeovers are flagged ("sublet", "lease-takeover"). Each
  card shows the availability window, which goes in the description.
- The listing id is the numeric data-listingid on the bookmark widget (stable).
  The URL is /listings/<slug>.
- There is no posted date on the index. The site publishes weekly.
"""
import re
import time

from bs4 import BeautifulSoup

from ..http import get, session
from .. import config
from . import base

NAME = "listingsproject"
LABEL = "Listings Project"
NEEDS_TAVILY = False

ROOT = "https://www.listingsproject.com"
FEEDS = [
    ("rentals", "apt_rent", False),
    ("sublets", "apt_sublet", True),
]
MAX_PAGES = 3

STUDIO_TITLE = re.compile(r"\bstudio\b", re.I)
NOT_A_HOME = re.compile(r"(art|artist|office|work|recording|music|dance|photo|yoga|studio)\s*[/&-]?\s*studio\s*(space)?"
                        r"|studio\s+space|studio/office|office/studio", re.I)
HAS_BEDROOMS = re.compile(r"\b([1-9]|one|two|three|four)[\s-]*(br|bd|bdr|bed|bedroom|bedrooms)\b|\b[1-9]\s*b(r|d)\b", re.I)
# Neighbourhoods that start a raw string but are outside our area (to stop substring false positives).
OUT_OF_AREA_PREFIX = re.compile(
    r"^(greenpoint|bushwick|bed[\s-]*stuy|bedford|clinton hill|fort greene|crown heights|park slope|"
    r"prospect|flatbush|ridgewood|astoria|long island city|lic|harlem|east harlem|washington heights|"
    r"upper east|upper west|inwood|sunset park|gowanus|red hook|carroll gardens|cobble hill|"
    r"boerum hill|brooklyn heights|dumbo|downtown brooklyn|jersey city|hoboken)\b", re.I)


def _url(path, sub, page):
    u = f"{ROOT}/real-estate/new-york-city/{path}?subcategories%5B%5D={sub}&keyword=studio&sort=least-expensive"
    return u + (f"&page={page}" if page > 1 else "")


def _parse(html):
    soup = BeautifulSoup(html, "lxml")
    cards = []
    for h in soup.select("h4 a[href^='/listings/']"):
        card = h.find_parent("div", class_="flex-auto")
        if card is None:
            continue
        outer = card.find_parent("div", class_="flex")
        price_el = card.select_one("span.font-semibold")
        spans = card.select("span.text-white")
        dates = None
        for sp in spans:
            t = sp.get_text(" ", strip=True)
            if sp is not price_el and re.search(r"\b20\d\d\b", t):
                dates = t
        meta_el = card.select_one("div.text-grey-dark")
        meta = [p.strip() for p in (meta_el.get_text("|", strip=True) if meta_el else "").split("|") if p.strip()]
        bm = card.select_one("[data-listingid]")
        snippet_el = card.select_one("p.text-sm")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        snippet = re.sub(r"\s*See more\s*$", "", snippet)
        img = outer.select_one("img[src]") if outer else None
        cards.append(dict(
            id=bm["data-listingid"] if bm else None,
            href=h["href"],
            title=" ".join(h.get_text(" ", strip=True).split()),
            price_txt=price_el.get_text(" ", strip=True) if price_el else "",
            dates=dates,
            meta=meta,
            featured=bool(card.select_one(".featured-listing-label")),
            snippet=snippet,
            image=img["src"] if img else None,
        ))
    return cards


def _monthly(price_txt):
    t = price_txt.lower().replace(" ", "")
    if "/month" not in t and "/mo" not in t:
        return None
    return base.parse_price(price_txt)


def _is_studio(title, snippet):
    if HAS_BEDROOMS.search(title):
        return False
    clean = NOT_A_HOME.sub(" ", title)
    if STUDIO_TITLE.search(clean):
        return True
    # Title silent on layout: accept only an explicit "studio apartment" in the snippet.
    snip = NOT_A_HOME.sub(" ", snippet or "")
    return bool(re.search(r"\bstudio\s+(apartment|apt|unit)\b", snip, re.I)) and not HAS_BEDROOMS.search(snip)


def fetch():
    s = session()
    # http.session() advertises br, but brotli is not a dependency, so br bodies come back undecoded.
    s.headers["Accept-Encoding"] = "gzip, deflate"
    results, seen = [], set()
    first = True
    for path, sub, is_sublet in FEEDS:
        for page in range(1, MAX_PAGES + 1):
            if not first:
                time.sleep(1.0)
            first = False
            html = get(_url(path, sub, page), s=s).text
            cards = _parse(html)
            if not cards:
                break
            has_next = f"page={page + 1}" in html
            sorted_prices = []
            for c in cards:
                p = base.parse_price(c["price_txt"])
                if not c["featured"] and p:
                    sorted_prices.append(p)
                sid = c["id"] or c["href"].rsplit("/", 1)[-1]
                if sid in seen:
                    continue
                price = _monthly(c["price_txt"])
                if price is None or not _is_studio(c["title"], c["snippet"]):
                    continue
                meta = c["meta"]
                location = meta[0] if meta else ""
                raw_neigh = location.split(",")[0].strip() or None
                if raw_neigh and OUT_OF_AREA_PREFIX.search(raw_neigh):
                    continue
                cats = " ".join(meta[1:]).lower()
                flags = []
                if is_sublet or "sublet" in cats:
                    flags.append("sublet")
                if "lease takeover" in cats:
                    flags.append("lease-takeover")
                if "room" in cats:  # belt and braces: never a whole unit
                    continue
                desc = " | ".join(filter(None, [c["dates"], c["snippet"]]))
                listing = base.make(
                    NAME, sid, ROOT + c["href"], price, c["title"],
                    raw_neigh=raw_neigh,
                    text=" ".join(filter(None, [location, c["snippet"]])),
                    bedrooms=0,
                    no_fee=True if "no broker" in cats or "no fee" in cats else None,
                    image_url=c["image"],
                    description=desc,
                    flags=flags,
                )
                if listing:
                    seen.add(sid)
                    results.append(listing)
            # Sorted ascending: once the cheapest non-featured price on this page is past budget, stop.
            if (sorted_prices and max(sorted_prices) > config.MAX_PRICE) or not has_next:
                break
    return results
