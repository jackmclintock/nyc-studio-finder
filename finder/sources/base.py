"""Helpers shared by scrapers."""
import re
from ..models import Listing
from .. import config, neighborhoods

STUDIO_WORDS = re.compile(r"\b(studio|0\s*br|0\s*bed|0\s*bedroom)\b", re.I)
ALCOVE = re.compile(r"\b(alcove|jr\.?\s*1|junior\s*(1|one)|convertible)\b", re.I)
SUBLET = re.compile(r"\b(sublet|sublease|lease\s*break|short[-\s]term)\b", re.I)
# Rooms in shares get mislabelled as studios on several platforms.
ROOM = re.compile(r"\b(private\s+room|room\s+for\s+rent|shared\s+bath(?:room)?s?|roommates?|room\s+in\s+an?\b|"
                  r"furnished\s+room|large\s+room\s+in|rooms?\s+available|co-?living|room\s+share|share\s+apartment|"
                  r"shared\s+apartment|shared\s+kitchen|no\s+kitchen)\b", re.I)


def parse_price(text):
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = re.search(r"\$?\s*([\d,]{3,7})", str(text))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def within_budget(price):
    return price is not None and 500 <= price <= config.MAX_PRICE


def extra_flags(text):
    flags = []
    if text:
        if ALCOVE.search(text):
            flags.append("alcove-or-junior")
        if SUBLET.search(text):
            flags.append("sublet")
        if ROOM.search(text):
            flags.append("room-or-share")
    return flags


def resolve_neighborhood(raw=None, zipcode=None, text=None):
    """Return (canonical, flags). Tries raw name, then zip, then free text."""
    n = neighborhoods.normalize(raw)
    if n:
        return n, []
    if neighborhoods.is_excluded(raw):
        return None, []
    n = neighborhoods.from_zip(zipcode)
    if n:
        return n, ["area-inferred"]
    n = neighborhoods.find_in_text(text)
    if n:
        return n, ["area-inferred"]
    return None, []


def make(source, source_id, url, price, title, raw_neigh=None, zipcode=None, text=None, **kw):
    """Build a Listing or return None if it is out of scope."""
    price = parse_price(price)
    if not within_budget(price):
        return None
    neigh, nflags = resolve_neighborhood(raw_neigh, zipcode, " ".join(filter(None, [title, text, kw.get("address")])))
    if not neigh:
        return None
    flags = list(dict.fromkeys(nflags + extra_flags(" ".join(filter(None, [title, text]))) + kw.pop("flags", [])))
    desc = kw.pop("description", None)
    if desc:
        desc = re.sub(r"\s+", " ", desc).strip()[:300]
    return Listing(source=source, source_id=str(source_id), url=url, price=price,
                   title=(title or "").strip()[:140], neighborhood=neigh,
                   neighborhood_raw=raw_neigh, description=desc, flags=flags, **kw)
