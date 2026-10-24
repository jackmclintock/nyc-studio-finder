"""Zumper (and, by extension, PadMapper, which is the same inventory).

The public search pages sit behind a Fastly "Client Challenge" JS wall, but the
JSON API the SPA uses answers plain requests with no token or cookie:

    POST https://www.zumper.com/api/t/1/pages/listables
    {"bedrooms": [0], "max_price": 2500, "min_lat": .., "max_lat": .., "min_lng": .., "max_lng": ..,
     "limit": 100, "offset": 0, "description_length": 400}

Keys are snake_case (camelCase keys are silently ignored). limit is capped at 100.
One bounding box covers Manhattan from Battery Park to ~W 72nd plus Williamsburg;
out-of-area hits (Greenpoint, LIC, UES, Weehawken) are dropped below.
"""
import re
import time
from datetime import datetime, timezone

from .. import config, neighborhoods, tavily
from ..http import Blocked, session, looks_like_bot_wall
from . import base

NAME = "zumper"
LABEL = "Zumper"
NEEDS_TAVILY = False

SITE = "https://www.zumper.com"
API = SITE + "/api/t/1/pages/listables"
SEARCH_URL = SITE + "/apartments-for-rent/new-york-ny/studios/under-2500"
IMG = "https://img.zumpercdn.com/{}/1280x960"
BOX = {"min_lat": 40.698, "max_lat": 40.780, "min_lng": -74.022, "max_lng": -73.930}
PAGE = 100
MAX_PAGES = 4

# Zumper's "studio" bucket includes private rooms in shares and office/commercial space.
NOT_A_STUDIO = re.compile(
    r"\b(private room|shared (bath|bathroom|kitchen)|room (for rent|available)|large room|"
    r"rooms? in (a|an|my|our)\b|office (space|for rent|suite|unit)|professional office|"
    r"commercial (space|condo|unit|property)|retail space|medical office)",
    re.I,
)
URL_ID = re.compile(r"/(?:listings|rentals|apartment-buildings)/(p?\d+p?)/")


def _make(*a, flags=(), **kw):
    """base.make, plus our own flags. base.make currently raises TypeError on every call
    (`list(dict.fromkeys(...) + [...])`: misplaced paren), so mirror its logic until fixed."""
    try:
        lst = base.make(*a, **kw)
    except TypeError:
        lst = _make_compat(*a, **kw)
    if lst:
        lst.flags += [f for f in flags if f not in lst.flags]
    return lst


def _make_compat(source, source_id, url, price, title, raw_neigh=None, zipcode=None, text=None, **kw):
    from ..models import Listing
    price = base.parse_price(price)
    if not base.within_budget(price):
        return None
    neigh, nflags = base.resolve_neighborhood(raw_neigh, zipcode, " ".join(filter(None, [title, text, kw.get("address")])))
    if not neigh:
        return None
    flags = list(dict.fromkeys(nflags + base.extra_flags(" ".join(filter(None, [title, text])))))
    desc = kw.pop("description", None)
    if desc:
        desc = re.sub(r"\s+", " ", desc).strip()[:300]
    return Listing(source=source, source_id=str(source_id), url=url, price=price,
                   title=(title or "").strip()[:140], neighborhood=neigh,
                   neighborhood_raw=raw_neigh, description=desc, flags=flags, **kw)


def _url_id(path):
    m = URL_ID.search(path or "")
    return m.group(1) if m else None


def _post(s, body):
    r = s.post(API, json=body, timeout=25,
               headers={"Accept": "application/json", "Content-Type": "application/json",
                        "Origin": SITE, "Referer": SEARCH_URL})
    ctype = r.headers.get("content-type", "")
    if r.status_code in (401, 403, 429, 503) or "json" not in ctype or looks_like_bot_wall(r.text):
        raise Blocked(f"{API} -> {r.status_code} {ctype}")
    r.raise_for_status()
    return r.json()


# Zumper names that neighborhoods.normalize gets wrong or misses. "Midtown East" would
# substring-match "midtown" -> Midtown South. Resolve these by zip first, then the default.
ZUMPER_NAMES = {"midtown east": "Turtle Bay", "civic center": "Tribeca", "bowery": "Lower East Side"}


def _resolve_area(raw, zipcode):
    """Zumper always names a neighbourhood. If that name (or the zip) is out of area, skip:
    do not let base fall back to free text, where 'near Williamsburg' would leak Greenpoint in."""
    key = (raw or "").strip().lower()
    if key in ZUMPER_NAMES:
        return neighborhoods.from_zip(zipcode) or ZUMPER_NAMES[key]
    return neighborhoods.normalize(raw) or neighborhoods.from_zip(zipcode)


def _from_api(l):
    path = l.get("url") or ""
    sid = _url_id(path) or str(l.get("listing_id"))
    if l.get("min_bedrooms") not in (0, None) or l.get("property_type") == 16 or "room-for-rent" in path:
        return None
    raw = l.get("neighborhood_name")
    zipcode = l.get("zipcode")
    canon = _resolve_area(raw, zipcode) if raw else None
    if raw and not canon:
        return None
    desc = l.get("short_description") or ""
    if NOT_A_STUDIO.search(desc[:400]):
        return None
    addr = ", ".join(filter(None, [l.get("address"), l.get("city"), " ".join(filter(None, [l.get("state"), zipcode]))]))
    name = l.get("building_name")
    title = f"Studio at {l.get('address') or name or raw}" + (f" ({name})" if name and l.get("address") else "")
    flags = []
    if (l.get("max_bedrooms") or 0) > 0:
        flags.append("price-from")  # building listing with several unit types
    no_fee = (not l["has_fees"]) if isinstance(l.get("has_fees"), bool) else None
    posted = None
    if l.get("listed_on"):
        posted = datetime.fromtimestamp(l["listed_on"], timezone.utc).isoformat()
    imgs = l.get("image_ids") or []
    lst = _make(
        NAME, sid, SITE + path, l.get("min_price"), title,
        raw_neigh=canon or raw, zipcode=zipcode, text=desc, address=addr,
        bedrooms=0, bathrooms=l.get("min_bathrooms"), no_fee=no_fee, posted_at=posted,
        image_url=IMG.format(imgs[0]) if imgs else None, description=desc or None, flags=flags,
    )
    if lst:
        lst.neighborhood_raw = raw  # keep what Zumper said, not our pre-resolved name
    return lst


def _fetch_direct():
    s = session()
    out, seen = [], set()
    for page in range(MAX_PAGES):
        body = dict(BOX, bedrooms=[config.BEDROOMS], max_price=config.MAX_PRICE,
                    limit=PAGE, offset=page * PAGE, description_length=400)
        rows = _post(s, body).get("listables") or []
        for l in rows:
            lst = _from_api(l)
            if lst and lst.source_id not in seen:
                seen.add(lst.source_id)
                out.append(lst)
        if len(rows) < PAGE:
            break
        time.sleep(1.0)
    return out


# ---------- Tavily fallback (UNTESTED: no key in the dev environment) ----------
LINK = re.compile(r"(?:https?://www\.zumper\.com)?(/(?:listings|apartment-buildings)/p?\d+p?/[a-z0-9'%\-]+)", re.I)
PRICE = re.compile(r"\$\s?([\d,]{3,6})")


def _fetch_tavily():
    pages = tavily.extract([SEARCH_URL])
    text = pages.get(SEARCH_URL) or next(iter(pages.values()), "") or ""
    out, seen = [], set()
    links = list(LINK.finditer(text))
    for i, m in enumerate(links):
        path = m.group(1)
        sid = _url_id(path)
        if not sid or sid in seen:
            continue
        slug = path.rstrip("/").rsplit("/", 1)[-1].lower()
        if not slug.startswith("studio-") and "/apartment-buildings/" not in path:
            continue  # slug carries the bedroom count: studio-..., 1-bedroom-...
        # price of this card: text after the link up to the next link, else the text just before it
        nxt = links[i + 1].start() if i + 1 < len(links) else len(text)
        prev = links[i - 1].end() if i else 0
        prices = []
        for window in (text[m.end(): min(nxt, m.end() + 400)], text[max(prev, m.start() - 300): m.start()]):
            prices = [p for p in (int(x.replace(",", "")) for x in PRICE.findall(window)) if 500 <= p <= 20000]
            if prices:
                break
        if not prices:
            continue
        raw = re.sub(r"^studio-|-new-york-ny$|-brooklyn-ny$", "", slug).replace("-", " ")
        canon = _resolve_area(raw, None)
        if not canon:
            continue
        lst = _make(NAME, sid, SITE + path, min(prices), f"Studio in {raw.title()}",
                    raw_neigh=canon, via_tavily=True)
        if lst:
            lst.neighborhood_raw = raw
            seen.add(sid)
            out.append(lst)
    return out


def fetch():
    try:
        return _fetch_direct()
    except Blocked:
        if not config.TAVILY_API_KEY:
            raise
        try:
            return _fetch_tavily()
        except tavily.TavilyUnavailable as e:
            raise Blocked(f"zumper API blocked and Tavily unavailable: {e}")
