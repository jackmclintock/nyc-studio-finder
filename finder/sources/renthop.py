"""RentHop (renthop.com).

RentHop sits behind a Cloudflare managed challenge: every path, including
robots.txt and sitemap.xml, returns 403 to automated clients from datacenter
IPs. It publishes no public RSS/Atom feed or JSON API for renters (its XML
feeds are inbound, for agents). So fetch() makes one direct attempt (to notice
if that ever changes) and otherwise reads the filtered results page through
one Tavily extract call (1 credit at basic depth).

Listing URLs: https://www.renthop.com/listings/<street-slug>/<unit>/<numeric id>
(the unit segment is sometimes absent). The trailing number is RentHop's
listing id and is the source_id.

The Tavily path is UNTESTED (no key in the dev environment); the parser is
written defensively against markdown-ish text: links as [text](url), images
as ![alt](src), prices as $1,234.
"""
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import config, http, neighborhoods, tavily
from ..models import Listing
from ..http import Blocked
from . import base

NAME = "renthop"
LABEL = "RentHop"
NEEDS_TAVILY = False   # Tavily extract returns nothing for RentHop (tested 2026-10); direct try only, no credits spent
TRY_TAVILY = False

BASE_URL = "https://www.renthop.com"
# Studios (bedrooms[]=0), max $2,500, NYC.
SEARCH_URL = ("https://www.renthop.com/search/nyc?min_price=0&max_price=2500"
              "&bedrooms%5B%5D=0&sort=hopscore&page=1")
TAVILY_URL = SEARCH_URL
TAVILY_DEPTH = "basic"          # 1 credit for one URL; "advanced" would be 2
EXTRA_FLAGS = ()

LISTING_RE = re.compile(
    r"(?:https?://(?:www\.)?renthop\.com)?"
    r"(/listings/[A-Za-z0-9%._~-]+(?:/[A-Za-z0-9%._~-]+)?/(\d{6,}))(?!\d)")


def _ident(m):
    return m.group(2), m.group(1)


def _path_neigh(path):
    return None                 # RentHop URLs carry the street, not the neighbourhood


def _path_zip(path):
    return None

# ------------------------------------------------- parsing (Tavily markdown) ---
LINK_RE = re.compile(r"(!?)\[([^\[\]]*)\]\(\s*<?([^()\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
IMG_RE = re.compile(r"!\[[^\[\]]*\]\(\s*<?([^()\s>]+)>?[^)]*\)")
PRICE_RE = re.compile(r"\$\s?(\d{1,2},\d{3}|\d{3,5})(?![\d,])(\s*(?:/|per\s+)\s*(?:night|nt|week|wk|day))?", re.I)
BEDS_RE = re.compile(r"\b([1-9])\s*-?\s*(?:bed(?:room)?s?|bd|br)\b", re.I)
ZIP_RE = re.compile(r"\bNY,?\s*(1[01]\d{3})\b")
GENERIC_TITLES = re.compile(r"^(check availability|view( details| listing)?|details|more|see more|contact|"
                            r"save|share|photos?|\d+\s*photos?|next|previous|hide|report)$", re.I)
BOROUGH_LINE = re.compile(r",\s*(manhattan|brooklyn|queens|bronx|staten island)\s*$", re.I)
OUT_OF_AREA = re.compile(
    r"\b(upper east side|upper west side|harlem|washington heights|inwood|yorkville|lenox hill|"
    r"carnegie hill|morningside heights|hamilton heights|manhattan valley|roosevelt island|"
    r"sugar hill|astoria|long island city|greenpoint|bushwick|bedford[- ]stuyvesant|bed[- ]stuy|"
    r"crown heights|park slope|prospect heights|fort greene|clinton hill|downtown brooklyn|"
    r"brooklyn heights|dumbo|sunnyside|jackson heights|ridgewood|flatbush|jersey city|hoboken)\b", re.I)
CAP = 1500                      # max chars of text attributed to one listing


def _plain(md):
    s = IMG_RE.sub(" ", md or "")
    s = LINK_RE.sub(lambda m: "" if m.group(1) else m.group(2), s)
    s = re.sub(r"(?:https?://|/listings/|/nyc/)\S+", " ", s)
    s = re.sub(r"(?m)^\s*#+\s*", "", s)
    s = re.sub(r"[*_>`|]+", " ", s)
    return s


def _lines(md):
    return [re.sub(r"\s+", " ", ln).strip(" -•\t") for ln in _plain(md).splitlines()
            if ln.strip(" -•\t")]


def _price(seg):
    for m in PRICE_RE.finditer(seg):
        if m.group(2):          # nightly / weekly rate, not monthly rent
            continue
        p = base.parse_price(m.group(1))
        if p and 500 <= p <= 20000:
            return p
    return None


def _link_start(text, pos):
    """If pos is the URL inside a markdown link '[...](url)', return the index of that
    link's opening '[' (handles '[![img](src)](url)'); else the start of pos's line."""
    j = pos
    while j > 0 and text[j - 1] in "< ":
        j -= 1
    if text[max(0, j - 2):j] == "](":
        depth, k = 0, j - 2
        while k >= 0 and j - k < 600:
            if text[k] == "]":
                depth += 1
            elif text[k] == "[":
                depth -= 1
                if depth == 0:
                    return k - 1 if k > 0 and text[k - 1] == "!" else k
            k -= 1
    return text.rfind("\n", 0, pos) + 1


def _segments(text):
    """Yield (id, path, segment) for each run of links to the same listing.
    A segment runs from that listing's first link to the next listing's first link."""
    runs = []
    for m in LISTING_RE.finditer(text):
        lid, path = _ident(m)
        if runs and runs[-1][0] == lid:
            continue
        runs.append((lid, path, _link_start(text, m.start())))
    for i, (lid, path, start) in enumerate(runs):
        end = runs[i + 1][2] if i + 1 < len(runs) else len(text)
        yield lid, path, text[start:min(end, start + CAP)]


def _titles(text):
    """Map listing id -> first non-generic link text pointing at it."""
    out = {}
    for m in LINK_RE.finditer(text):
        if m.group(1):
            continue
        lm = LISTING_RE.search(m.group(3))
        label = re.sub(r"\s+", " ", m.group(2)).strip()
        if lm and label and not GENERIC_TITLES.match(label):
            out.setdefault(_ident(lm)[0], label)
    return out


def _raw_neigh(lines):
    """First short line that reads like a neighbourhood: 'Chelsea, Downtown Manhattan,
    Manhattan', 'East Village / StuyTown', 'Upper West Side'."""
    for ln in lines:
        if len(ln) <= 80 and BOROUGH_LINE.search(ln):
            return ln
    for ln in lines:
        if len(ln) <= 60 and "$" not in ln and not re.match(r"^\d", ln) and (
                neighborhoods.normalize(ln) or OUT_OF_AREA.search(ln)):
            return ln
    return None


def parse(text, via_tavily):
    titles = _titles(text)
    seen, out = set(), []
    for lid, path, seg in _segments(text):
        if lid in seen:
            continue
        price = _price(seg)
        if price is None:
            continue
        lines = _lines(seg)
        plain = " ".join(lines)
        beds = BEDS_RE.search(plain)
        if beds and not base.STUDIO_WORDS.search(plain) and not base.ALCOVE.search(plain):
            continue                                    # a 1BR+ that slipped through
        hint = _path_neigh(path)
        if hint is False:
            continue                                    # URL says out of area
        raw = hint or _raw_neigh(lines)
        if raw and OUT_OF_AREA.search(raw):
            continue
        title = titles.get(lid) or next((ln for ln in lines if not PRICE_RE.search(ln) and len(ln) > 5), None)
        title = title or f"Studio, {LABEL} #{lid}"
        zm = ZIP_RE.search(plain)
        img = IMG_RE.search(seg)
        l = base.make(NAME, lid, urljoin(BASE_URL, path), price, title,
                      raw_neigh=raw, zipcode=_path_zip(path) or (zm.group(1) if zm else None),
                      text=plain[:600], address=title if re.match(r"^\d", title) else None,
                      no_fee=True if re.search(r"\bno[- ]fee\b", plain, re.I) else None,
                      image_url=img.group(1) if img and img.group(1).startswith("http") else None,
                      description=plain, via_tavily=via_tavily, flags=list(EXTRA_FLAGS))
        if l:
            seen.add(lid)
            out.append(l)
    return out


def _html_to_md(html):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for a in soup.find_all("a", href=True):
        a.replace_with(f"[{a.get_text(' ', strip=True)}]({urljoin(BASE_URL, a['href'])})")
    return soup.get_text("\n")


HOME_DATA = os.path.join(config.DATA_DIR, "renthop.json")
HOME_MAX_AGE_HOURS = 3


def _from_home_runner():
    """Listings uploaded by local/renthop_local.py (fetched from a home connection)."""
    if not os.path.exists(HOME_DATA):
        return None
    with open(HOME_DATA) as f:
        data = json.load(f)
    fetched = datetime.fromisoformat(data.get("fetched_at"))
    age_h = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
    if age_h > HOME_MAX_AGE_HOURS:
        raise Blocked(f"home runner data is {age_h:.0f}h old (Mac asleep or runner stopped)")
    if data.get("status") == "blocked":
        raise Blocked(f"home runner was blocked: {data.get('note', '')[:120]}")
    out = [Listing(**{k: v for k, v in d.items() if k != "key"}) for d in data.get("listings", [])]
    print(f"[{NAME}] {len(out)} listings from home runner, fetched {age_h * 60:.0f} min ago")
    return out


def fetch():
    home = _from_home_runner()
    if home is not None:
        return home
    try:
        r = http.get(SEARCH_URL, retries=0)          # one direct try, to notice if it opens up
    except Blocked as e:
        blocked = e
    else:
        print(f"[{NAME}] direct request succeeded ({r.status_code}); the site may have opened up")
        return parse(_html_to_md(r.text), via_tavily=False)
    if not TRY_TAVILY:
        raise Blocked(f"{blocked}; RentHop also blocks Tavily reads (tested 2026-10)")
    try:
        pages = tavily.extract([TAVILY_URL], depth=TAVILY_DEPTH)
    except tavily.TavilyUnavailable as e:
        raise Blocked(f"{blocked}; Tavily unavailable: {e}") from None
    content = "\n".join(v for v in pages.values() if v)
    if not content.strip():
        raise Blocked(f"{blocked}; Tavily extract returned no content")
    return parse(content, via_tavily=True)
