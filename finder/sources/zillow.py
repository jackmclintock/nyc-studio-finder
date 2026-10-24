"""Zillow (also covers Trulia and HotPads, which are fed from the same inventory).

Direct access is behind PerimeterX: both the HTML search pages and the JSON
endpoint (async-create-search-page-state) return 403 + px-captcha from
datacenter IPs. We try the JSON endpoint once (cheap, and the parser is ready
if it ever works), then fall back to a single Tavily extract call covering two
filtered search-results URLs (1 credit: Tavily charges per 5 URLs).
"""
import hashlib
import json
import re
from urllib.parse import quote

import requests

from .. import config, tavily
from ..http import Blocked, looks_like_bot_wall, session
from . import base

NAME = "zillow"
LABEL = "Zillow / Trulia / HotPads"
NEEDS_TAVILY = True

SITE = "https://www.zillow.com"

# Bounding boxes: Manhattan below ~66th St (W) / 59th St (E), and Williamsburg.
BOXES = {
    "manhattan": {"west": -74.025, "east": -73.955, "south": 40.700, "north": 40.776},
    "williamsburg": {"west": -73.975, "east": -73.930, "south": 40.700, "north": 40.725},
}


def _sqs(box, term="New York, NY"):
    # Tested 2026-10: the short-key form ("fr", "mp", beds max only) ignores the
    # bedroom filter; this long-key form with min+max beds and a region returns
    # studios only. Bounds narrow it to lower Manhattan / Williamsburg.
    return {
        "pagination": {},
        "usersSearchTerm": term,
        "mapBounds": box,
        "mapZoom": 13,
        "regionSelection": [{"regionId": 6181, "regionType": 6}],   # New York, NY
        "isMapVisible": True,
        "isListVisible": True,
        "filterState": {
            "isForRent": {"value": True}, "isForSaleByAgent": {"value": False},
            "isForSaleByOwner": {"value": False}, "isNewConstruction": {"value": False},
            "isComingSoon": {"value": False}, "isAuction": {"value": False},
            "isForSaleForeclosure": {"value": False},
            "beds": {"min": config.BEDROOMS, "max": config.BEDROOMS},
            "monthlyPayment": {"max": config.MAX_PRICE},
            "sortSelection": {"value": "days"},
        },
    }


def _search_url(box, term="New York NY"):
    q = quote(json.dumps(_sqs(box, term), separators=(",", ":")))
    return f"{SITE}/new-york-ny/rentals/?searchQueryState={q}"


# --- ids and urls ----------------------------------------------------------

# /homedetails/<slug>/<zpid>_zpid/   /apartments/new-york-ny/<slug>/<id>/   /b/<slug>-<id>/
LISTING_RE = re.compile(
    r"(?:https?://(?:www\.)?zillow\.com)?"
    r"(/homedetails/[A-Za-z0-9-]+/\d+_zpid/?"
    r"|/apartments/[a-z0-9-]+/[a-z0-9-]+/[A-Za-z0-9]{4,12}/?"
    r"|/b/[A-Za-z0-9-]+/?)"
)


def _id_from_url(url):
    m = re.search(r"/(\d+)_zpid", url)
    if m:
        return m.group(1)
    m = re.search(r"/apartments/[a-z0-9-]+/[a-z0-9-]+/([A-Za-z0-9]{4,12})/?$", url)
    if m:
        return m.group(1)
    m = re.search(r"/b/[A-Za-z0-9-]*?-([A-Za-z0-9]{5,8})/?$", url)
    if m:
        return m.group(1)
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _abs(url):
    return url if url.startswith("http") else SITE + url


ZIP_RE = re.compile(r"\b(?:NY|New York)[\s,-]+(1[01]\d{3})\b")


def _zip(*texts):
    for t in texts:
        if t:
            m = ZIP_RE.search(t)
            if m:
                return m.group(1)
    return None


# --- direct JSON route (blocked as of 2026-10; kept for when it is not) -----

def _direct():
    s = session()
    s.headers.update({"Accept": "*/*", "Content-Type": "application/json",
                      "Origin": SITE, "Referer": f"{SITE}/new-york-ny/rentals/"})
    out = []
    for i, box in enumerate(BOXES.values()):
        body = {"searchQueryState": _sqs(box), "wants": {"cat1": ["listResults", "mapResults"], "cat2": ["total"]},
                "requestId": i + 2, "isDebugRequest": False}
        try:
            r = s.put(f"{SITE}/async-create-search-page-state", json=body, timeout=25)
        except requests.RequestException as e:
            raise Blocked(f"zillow json endpoint unreachable: {e}")
        if r.status_code in (403, 429, 503) or looks_like_bot_wall(r.text):
            raise Blocked(f"zillow json endpoint -> {r.status_code}")
        r.raise_for_status()
        try:
            res = r.json()["cat1"]["searchResults"]["listResults"]
        except (ValueError, KeyError, TypeError):
            raise Blocked("zillow json endpoint returned unexpected payload")
        out.extend(_from_json(res))
    return out


def _from_json(results):
    out = []
    for it in results or []:
        url = it.get("detailUrl")
        if not url:
            continue
        url = _abs(url)
        info = (it.get("hdpData") or {}).get("homeInfo") or {}
        price = it.get("unformattedPrice") or info.get("price")
        beds = it.get("beds", info.get("bedrooms"))
        if it.get("units"):  # building card: pick the studio unit price
            studio = [u for u in it["units"] if str(u.get("beds", "")).strip() in ("0", "Studio", "studio")]
            if not studio:
                continue
            price = base.parse_price(studio[0].get("price"))
            beds = 0
        if beds not in (0, "0", None):
            continue
        addr = it.get("address") or ""
        title = it.get("buildingName") or addr or "Studio"
        out.append(base.make(
            NAME, it.get("zpid") or _id_from_url(url), url, price,
            title if title.lower().startswith("studio") else f"Studio - {title}",
            zipcode=it.get("addressZipcode") or info.get("zipcode") or _zip(addr),
            text=it.get("buildingName"), address=addr or None,
            bathrooms=it.get("baths"), sqft=it.get("area"), image_url=it.get("imgSrc"),
        ))
    return out


# --- Tavily route ------------------------------------------------------------

IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
PRICE = r"\$\s?(\d{1,2},?\d{3})"
STUDIO_PRICE = re.compile(PRICE + r"\+?\s*(?:/mo)?\s*(?:Studio|0\s*bds?)\b", re.I)
PRICE_RE = re.compile(PRICE + r"\+?(?:\s*/\s*mo)?", re.I)
MULTI_BED = re.compile(r"\b[1-9]\s*(?:bds?|beds?|bedrooms?|br)\b", re.I)
STUDIO = re.compile(r"\b(studio|0\s*bds?)\b", re.I)
ADDRESS_RE = re.compile(r"([0-9][^\n\[\]|$]{3,80}?,\s*(?:New York|Brooklyn|Manhattan),\s*NY\s*1\d{4})")


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


def _segments(text):
    """Split page text into one chunk per listing: from a listing's first link to the next listing's."""
    firsts = []
    seen = set()
    for m in LISTING_RE.finditer(text):
        sid = _id_from_url(m.group(1))
        if sid not in seen:
            seen.add(sid)
            firsts.append((_link_start(text, m.start()), sid, m.group(1)))
    for i, (pos, sid, path) in enumerate(firsts):
        end = firsts[i + 1][0] if i + 1 < len(firsts) else min(len(text), pos + 1500)
        yield sid, path, text[pos:end]


def _parse_tavily(text):
    out = []
    for sid, path, seg in _segments(text):
        seg = IMG.sub(' ', seg)
        # Collect link texts (often the address) and make a plain-text version of the card.
        link_texts = [m.group(1) for m in MD_LINK.finditer(seg) if not m.group(0).startswith("!")]
        plain = MD_LINK.sub(lambda m: "" if m.group(0).startswith("!") else m.group(1), seg)
        plain = re.sub(r"https?://\S+", " ", plain)
        m = STUDIO_PRICE.search(plain)
        if m:
            price = m.group(1)
        else:
            if MULTI_BED.search(plain) and not STUDIO.search(plain):
                continue  # a 1-bed+ card sneaking into a studio search
            m = PRICE_RE.search(plain)
            if not m:
                continue
            price = m.group(1)
        am = ADDRESS_RE.search(" ".join(link_texts) + "\n" + plain)
        address = re.sub(r"\s+", " ", am.group(1)).strip(" ,|-") if am else None
        slug_addr = re.sub(r"-", " ", path.split("/")[2]) if path.startswith("/homedetails/") else None
        address = address or slug_addr
        link_texts = [t.strip(" #*") for t in link_texts]
        name = next((t.split("|")[0].strip() for t in link_texts if t.strip() and not re.match(r"^\d", t.strip())
                     and "$" not in t and len(t) < 80), None)
        title = f"Studio - {name or address or 'Zillow listing'}"
        out.append(base.make(
            NAME, sid, _abs(path), price, title,
            zipcode=_zip(address, path.replace("-", " "), plain),
            text=" ".join(filter(None, [name, address])), address=address,
            flags=base.extra_flags(plain), via_tavily=True,
        ))
    return out


def _via_tavily(reason):
    urls = [_search_url(BOXES["manhattan"]), _search_url(BOXES["williamsburg"], "Williamsburg Brooklyn NY")]
    try:
        pages = tavily.extract(urls)  # 2 URLs -> 1 credit
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
