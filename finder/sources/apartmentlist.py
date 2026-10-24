"""Apartment List (apartmentlist.com): building-level rental listings with unit pricing.

Two-step scrape, 3 to 5 requests per run:

1. The server-rendered studio category pages
       https://www.apartmentlist.com/ny/new-york/c/studio-apartments-for-rent   (Manhattan only)
       https://www.apartmentlist.com/ny/brooklyn/c/studio-apartments-for-rent
   are Next.js App Router pages. The Next.js flight data (self.__next_f.push
   chunks) embeds every matching building in one list
   ("initialResults": {"listings": [{rental_id, lat, lon, prices: {"0": min studio rent}}]}),
   which is about 180 buildings per borough with no pagination. We keep buildings whose
   cheapest studio is within budget and whose lat/lon falls in a loose box
   around our area (Manhattan below about 66th St, and Williamsburg).
2. Card details for those buildings come from the site's own tRPC endpoint:
       GET /api/trpc/listings.fetchListingCardsById?input={"json":{"rentalIds":[...],"preferences":{}}}
   It returns the neighbourhood, formatted address with zip, slug, photos and
   priceToFloorplans with per-unit price, bed count and availability.

Each studio unit within budget becomes a listing. source_id is the unit_rental_id
(stable, e.g. "p8544607-3d"), and the url is the building page.

Quirks:
- Some feed-sourced buildings (source_name "Intellirent") show units whose
  available_on is years old and whose updated_at is many months old. These are
  likely stale, so they are flagged "possibly-stale".
- There is no posted date. posted_at is the unit's updated_at.
- No fee info is available per unit (no_fee stays None).
"""
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

from ..http import Blocked, get, session
from .. import config
from . import base

NAME = "apartmentlist"
LABEL = "Apartment List"
NEEDS_TAVILY = False

ROOT = "https://www.apartmentlist.com"
PAGES = [
    ROOT + "/ny/new-york/c/studio-apartments-for-rent",
    ROOT + "/ny/brooklyn/c/studio-apartments-for-rent",
]
TRPC = ROOT + "/api/trpc/listings.fetchListingCardsById"
BATCH = 25
IMG = "https://cdn.apartmentlist.com/image/upload/f_auto,q_auto,t_web-base/{}.jpg"

# Loose boxes (lat_min, lat_max, lon_min, lon_max); base.make() does the precise area check.
BOXES = [
    (40.698, 40.7785, -74.025, -73.955),   # Manhattan, Battery to about 66th St (west) and 59th St (east)
    (40.695, 40.7265, -73.972, -73.930),   # Williamsburg and East Williamsburg
]
STALE_DAYS = 180


def _flight(html):
    parts = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html)
    out = []
    for p in parts:
        try:
            out.append(json.loads(p))
        except ValueError:
            pass
    return "".join(out)


def _initial_results(html):
    f = _flight(html)
    i = f.find('"initialResults"')
    if i < 0:
        return []
    start = f.find("{", i)
    try:
        obj, _ = json.JSONDecoder().raw_decode(f, start)
    except ValueError:
        return []
    return obj.get("listings") or []


def _in_area(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in BOXES)


def _cards(s, ids):
    inp = json.dumps({"json": {"rentalIds": ids, "preferences": {}}}, separators=(",", ":"))
    r = get(f"{TRPC}?input={quote(inp)}", s=s,
            headers={"Accept": "application/json", "x-trpc-source": "nextjs-react"})
    try:
        data = r.json()["result"]["data"]["json"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(f"apartmentlist tRPC unexpected payload: {r.text[:200]}")
    return (data.get("data") or {}).get("listings") or []


def _zip(addr):
    m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", addr or "")
    return m.group(1) if m else None


def _age_days(ts):
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).days


def fetch():
    s = session()
    # http.session() advertises br, but brotli is not a dependency.
    s.headers["Accept-Encoding"] = "gzip, deflate"

    ids = []
    for i, url in enumerate(PAGES):
        if i:
            time.sleep(1.0)
        html = get(url, s=s).text
        rows = _initial_results(html)
        if not rows and "initialResults" not in html:
            raise Blocked(f"{url}: no embedded results (layout change or bot wall)")
        for x in rows:
            p = (x.get("prices") or {}).get("0")
            if p is None or p > config.MAX_PRICE or not _in_area(x.get("lat"), x.get("lon")):
                continue
            if x.get("rental_id") and x["rental_id"] not in ids:
                ids.append(x["rental_id"])

    cards = []
    for i in range(0, len(ids), BATCH):
        time.sleep(0.8)
        cards.extend(_cards(s, ids[i:i + BATCH]))

    results, seen = [], set()
    for c in cards:
        slug = c.get("slug") or ""
        url = ROOT + slug if slug.startswith("/") else (slug or ROOT)
        addr = c.get("formatted_address")
        neigh = c.get("neighborhood")
        photo = (c.get("first_photo") or [{}])[0]
        if photo.get("id"):
            image = IMG.format(photo["id"])
        elif photo.get("url"):  # satellite map tile when the building has no photos
            image = ("https:" + photo["url"] if photo["url"].startswith("//") else photo["url"]).replace("{size}", "600x400")
        else:
            image = None
        summary = (c.get("sampleSummary") or {}).get("text")
        for fp in (c.get("priceToFloorplans") or {}).values():
            if fp.get("bed") not in (0, "0"):
                continue
            for u in fp.get("units") or []:
                uid = u.get("unit_rental_id") or f"{c.get('rental_id')}-{u.get('id')}"
                if uid in seen:
                    continue
                price = u.get("price") or u.get("base_price") or fp.get("price")
                flags = []
                age = _age_days(u.get("updated_at"))
                if age is not None and age > STALE_DAYS:
                    flags.append("possibly-stale")
                unit_name = u.get("display_name") or u.get("name")
                if unit_name and re.search(r"\bbed\b", unit_name, re.I):  # floorplan placeholder, not a unit
                    unit_name = None
                title = f"Studio at {c.get('display_name') or addr}" + (f", unit {unit_name}" if unit_name else "")
                bits = []
                if u.get("available_on"):
                    bits.append(f"Available {u['available_on']}")
                if u.get("lease_length"):
                    bits.append(f"{u['lease_length']}-month lease")
                if summary:
                    bits.append(summary)
                listing = base.make(
                    NAME, uid, url, price, title,
                    raw_neigh=neigh,
                    zipcode=_zip(addr),
                    text=" ".join(filter(None, [fp.get("name"), addr])),
                    address=addr,
                    bedrooms=0,
                    bathrooms=fp.get("bath"),
                    sqft=u.get("sqft") or fp.get("sqft") or None,
                    posted_at=u.get("updated_at"),
                    image_url=image,
                    description=" | ".join(bits) or None,
                    flags=flags,
                )
                if listing:
                    seen.add(uid)
                    results.append(listing)
    return results
