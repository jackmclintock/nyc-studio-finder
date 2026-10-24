"""NYBits (nybits.com): NYC listings, mostly no-fee units posted by managers and owners.

Search is a plain GET form, filtered server-side by layout, max rent and
neighbourhood:

    https://www.nybits.com/search/?_a!process=y&!!atypes=studio&!!rmin=0&!!rmax=2500
        &!!fee=any&!!orderby=dateposted&nei=chelsea&nei=east_village&...

Quirks:
- A search with no matches comes back as HTTP 404 with a normal page saying
  "Sorry, no records found." That is an empty result, not an error.
- Results are 20 per page; the next page adds _search_start=20 (then 40...).
- Many prices are "net effective rent" (gross is higher). The rmax filter is
  applied to the displayed figure, so we keep it and flag "net-effective".
- Neighbourhood is in the card address as "(Financial District, MN)".
  "Sutton Place" has no canonical alias yet; EXTRA_AREA passes a hint in the
  text so base can still resolve it.
- Posted date is relative ("Posted < 3 hours", "Posted yesterday",
  "Posted 4 days ago"); converted to an approximate ISO timestamp.
- Inventory is dominated by big managed buildings, so studios at or under
  $2,500 in our area are rare; zero results on a given day is normal.
"""
import datetime as dt
import re
import time
from urllib.parse import urlencode

from bs4 import BeautifulSoup
import requests

from ..http import Blocked, looks_like_bot_wall, session
from . import base

NAME = "nybits"
LABEL = "NYBits"
NEEDS_TAVILY = False

SEARCH = "https://www.nybits.com/search/"
PAGE_SIZE = 20
MAX_PAGES = 4

# NYBits neighbourhood codes inside our search area.
NEI = [
    # Downtown
    "alphabet_city", "battery_park_city", "central_village", "chinatown", "civic_center",
    "east_village", "financial_district", "hudson_square", "little_italy", "lower_east_side",
    "meatpacking_district", "noho", "nolita", "soho", "south_street_seaport", "tribeca",
    "two_bridges", "west_village",
    # Midtown
    "central_midtown", "chelsea", "flatiron_district", "garment_district", "gramercy_park",
    "hells_kitchen", "hudson_yards", "kips_bay", "midtown_east", "midtown_south", "murray_hill",
    "nomad", "stuy_town", "sutton_place", "tudor_city", "union_square",
    # Uptown (only the part below 66th St)
    "lincoln_square",
    # Brooklyn
    "williamsburg", "east_williamsburg",
]

# NYBits names that neighborhoods.normalize() does not know -> canonical hint.
# Only consulted when normalize() fails on the raw name, so it is harmless once
# neighborhoods.py gains the alias.
EXTRA_AREA = {"sutton place": "Turtle Bay"}


def _url(start=0):
    params = [("_a!process", "y"), ("!!atypes", "studio"), ("!!rmin", "0"), ("!!rmax", "2500"),
              ("!!fee", "any"), ("!!orderby", "dateposted")]
    params += [("nei", n) for n in NEI]
    if start:
        params += [("_search_start", str(start)), ("_recordstoshow", str(PAGE_SIZE))]
    return SEARCH + "?" + urlencode(params)


def _get(s, url):
    """Like http.get but treats NYBits' 404 'no records found' page as a valid empty page."""
    last = None
    for attempt in range(3):
        try:
            r = s.get(url, timeout=25)
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (403, 429, 503) or looks_like_bot_wall(r.text):
            raise Blocked(f"{url[:80]} -> {r.status_code}")
        if r.status_code == 404 and "no records found" in r.text.lower():
            return ""
        if r.status_code >= 500:
            last = RuntimeError(f"{url[:80]} -> {r.status_code}")
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.text
    raise last or RuntimeError("request failed")


def _posted(text, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    t = (text or "").lower()
    m = re.search(r"(\d+)\s*(minute|hour|day|week|month)s?", t)
    if "yesterday" in t:
        d = now - dt.timedelta(days=1)
    elif "today" in t:
        d = now
    elif m:
        n, unit = int(m.group(1)), m.group(2)
        mult = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30}[unit]
        d = now - dt.timedelta(days=n * mult)
    else:
        return None
    return d.replace(microsecond=0).isoformat()


def _parse(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    total = None
    m = re.search(r"out of\s+([\d,]+)", soup.get_text(" ", strip=True))
    if m:
        total = int(m.group(1).replace(",", ""))
    for card in soup.select("article.card"):
        a = card.select_one("a.card__title[href]")
        if not a:
            continue
        href = a["href"]
        idm = re.search(r"/apartmentlistings/([0-9a-f]+)\.html", href)
        if not idm:
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        addr_el = card.select_one(".card__adress")
        addr_txt = " ".join(addr_el.get_text(" ", strip=True).split()) if addr_el else ""
        nm = re.search(r"\(([^()]+?),\s*([A-Z]{2})\s*\)", addr_txt)
        raw_neigh = nm.group(1).strip() if nm else None
        street = re.sub(r"\s*\([^()]*\)\s*$", "", addr_txt).strip() or None
        price_el = card.select_one(".card__price")
        price_txt = " ".join(price_el.get_text(" ", strip=True).split()) if price_el else ""
        labels = [l.get_text(" ", strip=True).lower() for l in card.select(".card__label")]
        facilities = [li.get_text(" ", strip=True) for li in card.select(".card__facilities li")]
        info = card.select_one(".card__info")
        posted_txt = info.find("span").get_text(" ", strip=True) if info and info.find("span") else ""
        img = card.select_one("img[src]")
        out.append(dict(id=idm.group(1), url=href, title=title, raw_neigh=raw_neigh, address=street,
                        price_txt=price_txt, labels=labels, facilities=facilities,
                        posted_txt=posted_txt, image=img["src"] if img else None))
    return out, total


def fetch():
    s = session()
    # http.session() advertises br, but brotli is not a dependency, so br bodies come back undecoded.
    s.headers["Accept-Encoding"] = "gzip, deflate"
    rows = []
    for page in range(MAX_PAGES):
        html = _get(s, _url(page * PAGE_SIZE))
        if not html:
            break
        got, total = _parse(html)
        rows.extend(got)
        if not got or total is None or (page + 1) * PAGE_SIZE >= total:
            break
        time.sleep(1.0)

    results, seen = [], set()
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        fac = " ".join(r["facilities"]).lower()
        if "studio" not in fac and "studio" not in r["title"].lower():
            continue
        flags = []
        if "net effective" in r["price_txt"].lower():
            flags.append("net-effective")
        hint = EXTRA_AREA.get((r["raw_neigh"] or "").lower())
        bath = re.search(r"([\d.]+)\s*Bath", " ".join(r["facilities"]))
        listing = base.make(
            NAME, r["id"], r["url"], r["price_txt"], r["title"],
            raw_neigh=r["raw_neigh"],
            text=" ".join(filter(None, [hint, " ".join(r["facilities"])])),
            address=r["address"],
            bedrooms=0,
            bathrooms=float(bath.group(1)) if bath else None,
            no_fee=True if "no fee" in r["labels"] else None,
            posted_at=_posted(r["posted_txt"]),
            image_url=r["image"],
            description=" · ".join(filter(None, [r["price_txt"], ", ".join(r["facilities"])])),
            flags=flags,
        )
        if listing:
            results.append(listing)
    return results
