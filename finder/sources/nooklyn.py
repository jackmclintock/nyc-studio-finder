"""Nooklyn (nooklyn.com): brokerage with a heavy Brooklyn inventory.

The /rentals page is a React shell; listings come from a JSON API the page
itself calls:

    POST https://nooklyn.com/api/v3/web/listings.search
    body {"bed_list": [0], "max_price": 2500, "page": 1, "per_page": 100, "order": "newest"}

Quirks:
- Prices in the response are in cents (245000 == $2,450).
- The server-side max_price filter is applied to the *net effective* rent, so the
  response includes listings whose gross rent is above budget but which have a
  concession (e.g. "3 months free"). We price by gross rent (what you actually
  pay each month) and let base.make() drop the rest; concessions are noted in the
  description and flagged "net-effective".
- Nearly all inventory is Brooklyn/Bronx/Queens; base.make() keeps only our
  area (in practice Williamsburg and the odd Manhattan listing).
- The search payload has no description, zip or fee info, so for the few
  in-area hits we fetch the listing record (GET listings.fetch), capped at
  MAX_DETAILS requests. "move_in_cost" lists upfront charges; if it is present
  and has no broker fee we mark no_fee=True.
- No posted date is exposed; date_available is put in the description instead.
"""
import json
import time

from ..http import Blocked, get, looks_like_bot_wall, session
from . import base

NAME = "nooklyn"
LABEL = "Nooklyn"
NEEDS_TAVILY = False

ROOT = "https://nooklyn.com"
SEARCH = ROOT + "/api/v3/web/listings.search"
FETCH = ROOT + "/api/v3/web/listings.fetch"
PER_PAGE = 100
MAX_PAGES = 3
MAX_DETAILS = 6


def _post(s, url, body):
    r = s.post(url, data=json.dumps(body), timeout=25,
               headers={"Content-Type": "application/json", "Accept": "application/json",
                        "Origin": ROOT, "Referer": ROOT + "/rentals?bedrooms=0"})
    if r.status_code in (403, 429, 503) or looks_like_bot_wall(r.text):
        raise Blocked(f"{url} -> {r.status_code}")
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        raise Blocked(f"{url} returned non-JSON ({len(r.text)} bytes)")


def _dollars(cents):
    try:
        return int(round(float(cents) / 100))
    except (TypeError, ValueError):
        return None


def _image(img):
    if isinstance(img, dict):
        return img.get("wide") or img.get("medium") or img.get("thumb")
    return None


def _search(s):
    out = []
    for page in range(1, MAX_PAGES + 1):
        data = _post(s, SEARCH, {"bed_list": [0], "max_price": 2500, "page": page,
                                 "per_page": PER_PAGE, "order": "newest"})
        if not data.get("ok", True):
            raise RuntimeError(f"nooklyn search not ok: {str(data)[:200]}")
        out.extend(data.get("listings") or [])
        if page >= (data.get("page_count") or 1):
            break
        time.sleep(1.0)
    return out


def _detail(s, lid):
    try:
        r = get(FETCH, s=s, params={"listing_id": lid}, headers={"Accept": "application/json"})
        return (r.json() or {}).get("listing") or {}
    except Blocked:
        raise
    except Exception:
        return {}


def fetch():
    s = session()
    # http.session() advertises br, but brotli is not a dependency, so br bodies come back undecoded.
    s.headers["Accept-Encoding"] = "gzip, deflate"
    s.get(ROOT + "/rentals?bedrooms=0", timeout=25)  # pick up cookies like a browser would
    raw = _search(s)

    candidates = []
    seen = set()
    for x in raw:
        lid = x.get("id")
        if lid is None or lid in seen:
            continue
        seen.add(lid)
        try:
            beds = float(x.get("bedrooms") or 0)
        except (TypeError, ValueError):
            beds = 0
        if beds != 0 or x.get("rental") is False:
            continue
        price = _dollars(x.get("price"))
        neigh = (x.get("neighborhood") or {}).get("name")
        title = f"Studio at {x.get('short_address') or x.get('address') or 'Nooklyn listing'}"
        # Pre-screen cheaply before spending a detail request.
        probe = base.make(NAME, lid, ROOT, price, title, raw_neigh=neigh, text=x.get("address"))
        if probe is None:
            continue
        candidates.append((x, price, neigh, title))

    results = []
    for i, (x, price, neigh, title) in enumerate(candidates):
        d = {}
        if i < MAX_DETAILS:
            time.sleep(0.7)
            d = _detail(s, x["id"])
        merged = {**x, **{k: v for k, v in d.items() if v not in (None, "", [])}}
        desc_bits = []
        conc = merged.get("concessions") or []
        flags = []
        if conc:
            c = conc[0]
            ner = c.get("net_effective_rent")
            desc_bits.append(f"{c.get('unit_free')} {c.get('unit_type')}(s) free on {c.get('lease_term')}-month lease"
                             + (f", net effective ${round(ner):,}" if ner else ""))
            flags.append("net-effective")
        if merged.get("date_available"):
            desc_bits.append(f"Available {merged['date_available']}")
        if merged.get("description"):
            desc_bits.append(merged["description"])
        move_in = merged.get("move_in_cost")
        no_fee = None
        if isinstance(move_in, list) and move_in:
            no_fee = not any("broker" in str(m).lower() or "fee" in str(m).lower() for m in move_in)
        if merged.get("platform_listing"):
            flags.append("sublet")
        url = ROOT + (merged.get("listing_url") or merged.get("url") or f"/listings/{x['id']}")
        listing = base.make(
            NAME, x["id"], url, price, title,
            raw_neigh=neigh,
            zipcode=merged.get("zip_code"),
            text=" ".join(filter(None, [merged.get("description"), merged.get("address")])),
            address=merged.get("address") or merged.get("short_address"),
            bedrooms=0,
            bathrooms=merged.get("bathrooms"),
            sqft=merged.get("square_feet"),
            no_fee=no_fee,
            image_url=_image(merged.get("image")),
            description=" | ".join(desc_bits) or None,
            flags=flags,
        )
        if listing:
            results.append(listing)
    return results
