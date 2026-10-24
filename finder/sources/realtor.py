"""Realtor.com rentals via the site's own GraphQL endpoint.

The HTML pages (/apartments/...) sit behind Kasada: from datacenter IPs they return
a 429 JS challenge (ips.js, KP_UIDz cookie), which is not a real rate limit, so
retrying does not help. The GraphQL endpoint the SRP calls (frontdoor/graphql)
answers plain POSTs, so that is the primary route. Searching "New York, NY"
covers all five boroughs; one page sorted by price (limit 200) is normally enough,
and base.make() drops everything outside the search area (mostly by zip, since
the API's neighborhoods field is usually null).

If GraphQL is ever blocked, we fall back to one Tavily extract of the filtered
studio results page (1 credit).
"""
import re
import time

import requests

from .. import config, tavily
from ..http import Blocked, looks_like_bot_wall, session
from . import base

NAME = "realtor"
LABEL = "Realtor.com"
NEEDS_TAVILY = False

SITE = "https://www.realtor.com"
GQL = f"{SITE}/frontdoor/graphql?client_id=rdc-search-for-rent-search&schema=vesta"
RESULTS_URL = f"{SITE}/apartments/New-York_NY/beds-studio/price-na-{config.MAX_PRICE}"
PAGE = 200
MAX_PAGES = 3

QUERY = """query ConsumerSearchQuery($query: HomeSearchCriteria!, $limit: Int, $offset: Int, $sort: [SearchAPISort]) {
  home_search(query: $query, limit: $limit, offset: $offset, sort: $sort) {
    total count
    results {
      property_id listing_id status list_price list_price_min list_price_max list_date href
      flags { is_pending }
      location { address { line city postal_code coordinate { lat lon } } neighborhoods { name level } }
      description { beds beds_min beds_max baths sqft name type text }
      primary_photo { href }
    }
  }
}"""

# Rough outlines (lon, lat) of the search area: Manhattan below ~66th St W / 59th St E,
# and Williamsburg. Used only to decide whether free-text neighbourhood matching is
# safe when the zip is not in the fallback table (otherwise "near Williamsburg" in a
# Greenpoint ad, or "minutes from Midtown" in a Harlem one, would be accepted).
AREA = [
    [(-74.020, 40.700), (-74.000, 40.705), (-73.990, 40.709), (-73.976, 40.711),
     (-73.971, 40.726), (-73.972, 40.737), (-73.969, 40.746), (-73.962, 40.753),
     (-73.957, 40.760), (-73.973, 40.765), (-73.981, 40.768), (-73.982, 40.774),
     (-73.990, 40.778), (-74.001, 40.765), (-74.010, 40.746), (-74.013, 40.725),
     (-74.018, 40.710)],
    [(-73.970, 40.700), (-73.928, 40.700), (-73.928, 40.722), (-73.962, 40.722), (-73.970, 40.712)],
]


def _inside(lon, lat, poly):
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _in_box(coord):
    try:
        return any(_inside(coord["lon"], coord["lat"], p) for p in AREA)
    except (TypeError, KeyError):
        return False


NOT_HOMES = re.compile(r"\b(parking|garage space|storage unit)\b", re.I)


def _gql(s, offset):
    body = {
        "operationName": "ConsumerSearchQuery",
        "query": QUERY,
        "variables": {
            "query": {"status": ["for_rent"], "search_location": {"location": "New York, NY"},
                      "beds": {"max": config.BEDROOMS}, "list_price": {"max": config.MAX_PRICE}},
            "limit": PAGE, "offset": offset,
            "sort": [{"field": "list_price", "direction": "asc"}],
        },
    }
    try:
        r = s.post(GQL, json=body, timeout=30)
    except requests.RequestException as e:
        raise Blocked(f"realtor graphql unreachable: {e}")
    if r.status_code in (403, 429, 503) or looks_like_bot_wall(r.text):
        raise Blocked(f"realtor graphql -> {r.status_code}")
    r.raise_for_status()
    try:
        d = r.json()
        hs = d["data"]["home_search"]
    except (ValueError, KeyError, TypeError):
        raise Blocked(f"realtor graphql unexpected payload: {r.text[:200]}")
    if hs is None:
        raise Blocked(f"realtor graphql errors: {str(d.get('errors'))[:200]}")
    return hs


def _from_result(x):
    desc = x.get("description") or {}
    loc = x.get("location") or {}
    addr = loc.get("address") or {}
    if (x.get("flags") or {}).get("is_pending"):
        return None
    beds = desc.get("beds")
    if beds is None:
        beds = desc.get("beds_max")
    if beds not in (0, None):
        return None
    text = desc.get("text") or ""
    line = addr.get("line") or ""
    if NOT_HOMES.search(line) or (NOT_HOMES.search(text) and not base.STUDIO_WORDS.search(text)):
        return None
    price = x.get("list_price") or x.get("list_price_min")
    url = x.get("href") or ""
    if url and not url.startswith("http"):
        url = SITE + url
    neighs = loc.get("neighborhoods") or []
    raw = next((n.get("name") for n in neighs if n.get("level") == "neighborhood"), None) or \
        (neighs[0].get("name") if neighs else None)
    address = ", ".join(filter(None, [line, addr.get("city"), "NY " + (addr.get("postal_code") or "")]))
    name = desc.get("name")
    title = f"Studio - {name}" if name else f"Studio - {line}"
    photo = (x.get("primary_photo") or {}).get("href")
    if photo:
        photo = photo.replace("http://", "https://")
    in_box = _in_box(addr.get("coordinate"))
    return base.make(
        NAME, x.get("property_id") or x.get("listing_id"), url, price, title,
        raw_neigh=raw, zipcode=addr.get("postal_code"),
        text=text if in_box else None,
        address=address, bathrooms=desc.get("baths"), sqft=desc.get("sqft"),
        posted_at=x.get("list_date"), image_url=photo, description=text or None,
        flags=[] if in_box else base.extra_flags(text),  # base flags `text` itself when passed
    )


def _direct():
    s = session()
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json",
                      "Origin": SITE, "Referer": f"{SITE}/apartments/New-York_NY",
                      "rdc-client-name": "RDC_WEB_SRP_FR_PAGE", "rdc-client-version": "3.0.2515"})
    out, offset = [], 0
    for page in range(MAX_PAGES):
        if page:
            time.sleep(1.0)
        hs = _gql(s, offset)
        res = hs.get("results") or []
        out.extend(_from_result(x) for x in res)
        offset += len(res)
        if not res or offset >= (hs.get("total") or 0):
            break
    return out


# --- Tavily fallback (untested: no key in the dev environment) -------------

# /rentals/details/<slug>_<zip>_M12345-67890 ; property_id is the digits joined.
LISTING_RE = re.compile(r"(?:https?://(?:www\.)?realtor\.com)?(/rentals/details/[A-Za-z0-9_-]+?_M(\d{5})-(\d{5}))")
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
PRICE = re.compile(r"\$\s?(\d{1,2},?\d{3})")
MULTI_BED = re.compile(r"\b[1-9]\s*(?:beds?|bedrooms?|bd|br)\b", re.I)


def _link_start(text, pos):
    """If the URL at pos is the target of a markdown link `[...](url)`, return where that link starts."""
    if not text.startswith("](", pos - 2):
        return pos
    depth = 0
    for i in range(pos - 2, max(-1, pos - 600), -1):
        c = text[i]
        if c == "]":
            depth += 1
        elif c == "[":
            depth -= 1
            if depth == 0:
                return i - 1 if i and text[i - 1] == "!" else i
    return pos


def _parse_tavily(text):
    firsts, seen = [], set()
    for m in LISTING_RE.finditer(text):
        pid = m.group(2) + m.group(3)
        if pid not in seen:
            seen.add(pid)
            firsts.append((_link_start(text, m.start()), pid, m.group(1)))
    out = []
    for i, (pos, pid, path) in enumerate(firsts):
        end = firsts[i + 1][0] if i + 1 < len(firsts) else min(len(text), pos + 1500)
        seg = IMG.sub(" ", text[pos:end])
        plain = MD_LINK.sub(lambda m: "" if m.group(0).startswith("!") else m.group(1), seg)
        plain = re.sub(r"https?://\S+", " ", plain)
        if MULTI_BED.search(plain) and not base.STUDIO_WORDS.search(plain):
            continue
        pm = PRICE.search(plain)
        if not pm:
            continue
        # Slug looks like 358-W-58th-St-EF_New-York_NY_10019_M93311-95276
        parts = path.rsplit("/", 1)[-1].split("_")
        zipcode = next((p for p in parts if re.fullmatch(r"1\d{4}", p)), None)
        street = parts[0].replace("-", " ") if parts else None
        address = ", ".join(filter(None, [street, " ".join(parts[1:2]).replace("-", " "), f"NY {zipcode or ''}".strip()]))
        out.append(base.make(NAME, pid, SITE + path, pm.group(1), f"Studio - {street}",
                             zipcode=zipcode, address=address, flags=base.extra_flags(plain),
                             via_tavily=True))
    return out


def _via_tavily(reason):
    try:
        pages = tavily.extract([RESULTS_URL])  # 1 credit
    except tavily.TavilyUnavailable as e:
        raise Blocked(f"{reason}; tavily unavailable: {e}")
    except requests.RequestException as e:
        raise Blocked(f"{reason}; tavily failed: {e}")
    text = "\n\n".join(v or "" for v in pages.values())
    if not LISTING_RE.search(text):
        raise Blocked(f"{reason}; tavily got no listing links")
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
