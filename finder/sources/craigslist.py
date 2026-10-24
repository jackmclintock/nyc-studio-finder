"""Craigslist (newyork). Manhattan apartments plus Brooklyn filtered to Williamsburg.

newyork.craigslist.org/search/mnh/apa now 301s to www.craigslist.org/search/subarea/mnh?cat=apa,
a JS shell whose results come from a JSON endpoint that answers plain requests:

    GET https://sapi.craigslist.org/web/v8/postings/search/full
        ?batch=3-0-360-1-0&searchPath=mnh/apa&cc=US&lang=en
        &max_price=2500&min_bedrooms=0&max_bedrooms=0&sort=date

batch = <areaId 3=newyork>-<start>-<count>-<sortId 1=newest>-0. One call returns the whole
(small) result set, so there is no pagination. Each item is a compact array, decoded as in
the site's search.js:
    [0] postingId - minPostingId     [1] postedTs - minPostedDate     [2] categoryId
    [3] price (-1 = none)            [4] "locIdx:descIdx[:nhIdx]~lat~lon"
    [5] first image size             then, in any order: title (str), dedupeKey (negative int),
    [13, uuid] [4, imageIds...] [5, bedrooms, sqft] [6, seo slug] [10, price string]
Post URLs are https://www.craigslist.org/view/d/<seo>/<uuid> (the old
newyork.craigslist.org/mnh/apa/d/<seo>/<postingId>.html form redirects there).

Neighbourhoods: Manhattan nh codes live in search.js's area table (120 Battery Park,
121 Downtown, 122 Financial District, 123 TriBeCa, 124 SoHo, 125 Nolita / Bowery,
126 Lower East Side, 127 Greenwich Village, 128 West Village, 129 East Village,
130 Union Square, 131 Murray Hill, 132 Gramercy, 133 Flatiron, 134 Chelsea, 135 Midtown,
136 Midtown East, 137 Midtown West, 160 Chinatown / Lit Italy; out of area: 138 UWS,
139 UES, 140 Inwood / Wash Hts, 141 Harlem / Morningside, 159 East Harlem). Brooklyn has
no nh codes. The nh filter works (repeat nh=...), but it drops posts with no structured
neighbourhood (~15% of Manhattan) and the whole Manhattan studio set is ~25-30 posts, so by
default we take all of Manhattan in one call and let base resolve the area from Craigslist's
location text. Set USE_NH_FILTER = True to filter server-side instead.
"""
import re
import time
from datetime import datetime, timezone

from .. import config, neighborhoods, tavily
from ..http import Blocked, get, session
from ..models import Listing
from . import base

NAME = "craigslist"
LABEL = "Craigslist"
NEEDS_TAVILY = False

API = "https://sapi.craigslist.org/web/v8/postings/search/full"
WWW = "https://www.craigslist.org"
AREA_ID = 3  # newyork
SORT_NEWEST = 1
BATCH_COUNT = 360
IMG = "https://images.craigslist.org/{}_600x450.jpg"

USE_NH_FILTER = False
NH_IN_AREA = [120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 160]

# (subarea, only keep this canonical neighbourhood or None for any)
SEARCHES = [("mnh", None), ("brk", "Williamsburg")]

ROOMISH = re.compile(r"\b(rooms?|roommates?|share[sd]?|sharing|sro)\b", re.I)
# "room"/"shared" phrases that do not mean a room in a share
NOT_ROOMISH = re.compile(
    r"\b(?:\d+|one|two|three|four)\s*-?\s*rooms?\b|"
    r"\b(?:living|dining|bed|bath|laundry|storage|bonus|family|mud|media|sun|great|work|show|"
    r"fitness|game|package|mail|play|bike|party|club)\s*-?\s*rooms?\b|"
    r"\bshared\s+(?:terrace|roof\w*|laundry|garden|back\s*yard|yard|courtyard|patio|deck|"
    r"outdoor|gym|amenit\w+|lounge|balcony)\b",
    re.I,
)
MULTI_BED = re.compile(r"\b(?:[1-9]|one|two|three)\s*-?\s*(?:br|bd|bdr|bdrm|beds?|bedrooms?)\b", re.I)
NO_FEE = re.compile(r"\bno[\s-]*(?:broker[\s-]*)?fee\b", re.I)

# Rough outlines used only to veto listings whose area was inferred from title text
# (e.g. "Weehawken ... 10 min to Midtown West"). (lat, lon), generous margins.
LOWER_MANHATTAN = [
    (40.6980, -74.0230), (40.7110, -74.0210), (40.7300, -74.0150), (40.7500, -74.0120),
    (40.7700, -73.9990), (40.7810, -73.9910), (40.7770, -73.9740), (40.7660, -73.9690),
    (40.7630, -73.9550), (40.7450, -73.9660), (40.7300, -73.9670), (40.7100, -73.9720),
    (40.7050, -73.9980), (40.6990, -74.0110),
]
WILLIAMSBURG_BOX = (40.6970, 40.7260, -73.9730, -73.9240)  # min_lat, max_lat, min_lon, max_lon


def _in_poly(lat, lon, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_search_area(lat, lon):
    a, b, c, d = WILLIAMSBURG_BOX
    return _in_poly(lat, lon, LOWER_MANHATTAN) or (a <= lat <= b and c <= lon <= d)


def _clean(title):
    """Underscores are word characters, so "STUDIO_EAST VILLAGE" defeats every \\b regex."""
    return re.sub(r"[_\s]+", " ", title or "").strip()


def is_room_or_share(title):
    return bool(ROOMISH.search(NOT_ROOMISH.sub(" ", _clean(title))))


def _make(*a, flags=(), **kw):
    """base.make plus our flags. base.make currently raises TypeError on every call
    (`list(dict.fromkeys(...) + kw.pop("flags", []))`: misplaced paren), so mirror it."""
    try:
        lst = base.make(*a, **kw)
    except TypeError:
        lst = _make_compat(*a, **kw)
    if lst:
        lst.flags += [f for f in flags if f not in lst.flags]
    return lst


def _make_compat(source, source_id, url, price, title, raw_neigh=None, zipcode=None, text=None, **kw):
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


def _query(s, subarea):
    params = [
        ("batch", f"{AREA_ID}-0-{BATCH_COUNT}-{SORT_NEWEST}-0"), ("searchPath", f"{subarea}/apa"),
        ("cc", "US"), ("lang", "en"), ("max_price", config.MAX_PRICE),
        ("min_bedrooms", config.BEDROOMS), ("max_bedrooms", config.BEDROOMS), ("sort", "date"),
    ]
    if USE_NH_FILTER and subarea == "mnh":
        params += [("nh", n) for n in NH_IN_AREA]
    r = get(API, s=s, params=params,
            headers={"Accept": "application/json", "Referer": WWW + "/", "Origin": WWW})
    if "json" not in r.headers.get("content-type", ""):
        raise Blocked(f"{API} -> {r.status_code} {r.headers.get('content-type')}")
    data = r.json().get("data")
    if not isinstance(data, dict) or "items" not in data:
        raise RuntimeError(f"craigslist sapi: unexpected payload for {subarea}")
    return data


def _decode(data):
    """Yield dicts for each compact item."""
    dec = data.get("decode") or {}
    min_pid = dec.get("minPostingId", 0)
    min_ts = dec.get("minPostedDate", 0)
    descs = dec.get("locationDescriptions") or []
    nhs = dec.get("neighborhoods") or []
    head = 6 if dec.get("version", 2) > 1 else 5
    for it in data.get("items") or []:
        try:
            loc = str(it[4]).split("~")
            idx = loc[0].split(":")
            d = {
                "pid": min_pid + it[0],
                "ts": min_ts + it[1],
                "price": it[3] if it[3] != -1 else None,
                "desc": descs[int(idx[1])] if len(idx) > 1 and int(idx[1]) < len(descs) else None,
                "nh": nhs[int(idx[2])] if len(idx) > 2 and int(idx[2]) < len(nhs) else None,
                "lat": float(loc[1]) if len(loc) > 2 else None,
                "lon": float(loc[2]) if len(loc) > 2 else None,
            }
        except (IndexError, ValueError, TypeError):
            continue
        for x in it[head:]:
            if isinstance(x, str):
                d["title"] = x
            elif isinstance(x, int) and x < 0:
                d["dedupe"] = x
            elif isinstance(x, list) and x:
                tag, rest = x[0], x[1:]
                if tag == 13 and rest:
                    d["uuid"] = rest[0]
                elif tag == 4:
                    d["images"] = rest
                elif tag == 5 and rest:
                    d["bedrooms"] = rest[0]
                    d["sqft"] = rest[1] if len(rest) > 1 else None
                elif tag == 6 and rest:
                    d["seo"] = rest[0]
        yield d


def _to_listing(d, subarea, only):
    title = (d.get("title") or "").strip()
    if not title:
        return None
    if (d.get("bedrooms") or 0) > 0:
        return None
    clean = _clean(title)
    if MULTI_BED.search(clean) and not (base.STUDIO_WORDS.search(clean) or base.ALCOVE.search(clean)):
        return None
    seo = d.get("seo") or "x"
    if d.get("uuid"):
        url = f"{WWW}/view/d/{seo}/{d['uuid']}"
    else:
        url = f"https://newyork.craigslist.org/{subarea}/apa/d/{seo}/{d['pid']}.html"
    img = None
    imgs = d.get("images") or []
    if imgs and isinstance(imgs[0], str):
        img = IMG.format(imgs[0].split(":", 1)[-1])
    flags = ["room-or-share"] if is_room_or_share(title) else []
    raw = (d.get("desc") or "").strip() or None
    lst = _make(
        NAME, d["pid"], url, d.get("price"), title,
        raw_neigh=raw, text=" ".join(filter(None, [clean, d.get("nh")])),
        bedrooms=0, sqft=d.get("sqft") or None,
        no_fee=True if NO_FEE.search(clean) else None,
        posted_at=datetime.fromtimestamp(d["ts"], timezone.utc).isoformat() if d.get("ts") else None,
        image_url=img, flags=flags,
    )
    if not lst:
        return None
    if only and lst.neighborhood != only:
        return None
    if "area-inferred" in lst.flags and d.get("lat") is not None and not _in_search_area(d["lat"], d["lon"]):
        return None
    return lst


def _fetch_direct():
    s = session()
    out, seen_ids, seen_keys = [], set(), set()
    for i, (subarea, only) in enumerate(SEARCHES):
        if i:
            time.sleep(1.0)
        items = sorted(_decode(_query(s, subarea)), key=lambda d: -d["ts"])  # newest wins on dupes
        for d in items:
            key_title = (re.sub(r"\W+", " ", (d.get("title") or "").lower()).strip(), d.get("price"))
            dup_keys = {key_title} | ({("dk", subarea, d["dedupe"])} if "dedupe" in d else set())
            if d["pid"] in seen_ids or dup_keys & seen_keys:
                continue
            lst = _to_listing(d, subarea, only)
            if lst:
                seen_ids.add(d["pid"])
                seen_keys |= dup_keys
                out.append(lst)
    return out


# ---------- Tavily fallback (UNTESTED: no key in the dev environment) ----------
# The www search page carries a no-JS list: <li class="cl-static-search-result"><a href=
# "/view/d/<seo>/<uuid>"> title, price, location </a>. Extract both search pages (1 credit).
# Tavily's default markdown should render each card as "[title $price location](url)"; we also
# accept "[title](url) $price location". The numeric post id is not on that page, so these
# listings key on the uuid ("u-" prefix) and will not dedupe against direct-route ids.
TAVILY_URLS = {
    f"{WWW}/search/subarea/{sub}?cat=apa&max_price={config.MAX_PRICE}&min_bedrooms=0&max_bedrooms=0&sort=date": only
    for sub, only in SEARCHES
}
VIEW = r"(?:https?://www\.craigslist\.org)?/view/d/([a-z0-9-]+)/([A-Za-z0-9]{16,32})"
MD_LINK = re.compile(r"\[([^\]]*)\]\(" + VIEW + r"[^)]*\)")
PRICE = re.compile(r"\$\s?([\d,]{3,6})")


def _fetch_tavily():
    pages = tavily.extract(list(TAVILY_URLS))
    out, seen = [], set()
    for url, only in TAVILY_URLS.items():
        text = pages.get(url) or ""
        links = list(MD_LINK.finditer(text))
        for i, m in enumerate(links):
            inside, seo, uuid = m.group(1), m.group(2), m.group(3)
            if uuid in seen:
                continue
            after = text[m.end(): links[i + 1].start() if i + 1 < len(links) else m.end() + 200]
            lines = [l.strip(" *#|-") for l in re.split(r"\n|\s{2,}", inside + "\n" + after)]
            lines = [l for l in lines if l]
            if not lines:
                continue
            title = re.sub(r"\s*\$\s?[\d,]{3,6}\s*$", "", lines[0]).strip() or seo.replace("-", " ")
            pm = PRICE.search(inside) or PRICE.search(after)
            price = int(pm.group(1).replace(",", "")) if pm else None
            loc = next((l for l in lines[1:] if not PRICE.fullmatch(l.replace(" ", ""))), None)
            clean = _clean(title)
            if MULTI_BED.search(clean) and not base.STUDIO_WORDS.search(clean):
                continue
            lst = _make(NAME, f"u-{uuid}", f"{WWW}/view/d/{seo}/{uuid}", price, title,
                        raw_neigh=loc, text=clean, bedrooms=0, via_tavily=True,
                        flags=["room-or-share"] if is_room_or_share(title) else [])
            if lst and (not only or lst.neighborhood == only):
                seen.add(uuid)
                out.append(lst)
    return out


def fetch():
    try:
        return _fetch_direct()
    except Blocked:
        try:
            return _fetch_tavily()
        except tavily.TavilyUnavailable as e:
            raise Blocked(f"craigslist blocked and Tavily unavailable: {e}")
