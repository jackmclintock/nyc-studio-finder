"""StreetEasy rentals.

StreetEasy sits behind PerimeterX and returns 403 to datacenter IPs (this
container and GitHub Actions runners alike). fetch() still tries the direct
request first, so we notice if that ever changes, then falls back to Tavily:

  1. tavily.extract() of ONE search-results URL: studios <= $2,500 in our
     StreetEasy areas, newest first.            (1 credit basic, 2 advanced)
  2. only if (1) gives no listing links: one tavily.search() restricted to
     streeteasy.com, parsing listing URLs/titles/snippets.         (1 credit)

At most MAX_CREDITS (2) Tavily credits per run. The Tavily path has NOT been
run against the live API (no key where it was written), so the parser is
deliberately tolerant: it accepts HTML, markdown or plain text, and any of
the listing URL shapes StreetEasy uses.

Listing URL shapes:
  https://streeteasy.com/rental/4735143                    (numeric rental id)
  https://streeteasy.com/building/<slug>/rental/4735143
  https://streeteasy.com/building/<building-slug>/<unit>   (search cards, unit pages)
source_id is the numeric rental id whenever the URL or card text gives one,
otherwise "unit:<building-slug>/<unit>", which is also stable across runs.
"""
import logging
import random
import re
import time
from urllib.parse import quote

from bs4 import BeautifulSoup

from .. import config, http, tavily
from . import base

NAME = "streeteasy"
LABEL = "StreetEasy"
NEEDS_TAVILY = True

log = logging.getLogger(__name__)

SITE = "https://streeteasy.com"
MAX_CREDITS = 2          # Tavily credits this source may spend per run
EXTRACT_DEPTH = "basic"  # search pages are server-rendered (Next.js), so basic should see the
                         # cards. "advanced" costs 2 credits and leaves no room for the search
                         # fallback; switch only if basic turns out to get the bot wall.
DIRECT_PAGES = 3         # if direct access ever works; sorted by date, so no price cut-off
SEARCH_QUERY = "studio for rent under $2500 Manhattan"

# ---------------------------------------------------------------------------
# StreetEasy area ids. The same ids are used by the site's `area:` URL filter
# and its GraphQL `areas` filter. None could be checked against StreetEasy
# from here (403). Evidence:
#   T  StreetEasy search-results page titles seen in web search results. A
#      multi-area page's title lists the area names in id order, e.g.
#      /for-rent/nyc/...|area:104,105,106,107,108,... is titled "Financial
#      District, Tribeca, Stuyvesant Town/PCV, Soho, Little Italy and 26 More",
#      and area:158,159,113,133 is "Gramercy Park, Kips Bay, Flatiron, and
#      NoMad". High confidence; each also agrees with R's region lists.
#   R  github.com/gotnosocks/apartments (commit f1fc080, Oct 2026), a project
#      that collects StreetEasy through its GraphQL API. Name stated next to
#      the id in its code, comments or test fixtures.
#   I  Inferred from R by elimination or list order, not stated outright.
#   A  Apify actor docs (apify.com/sian.agency/streeteasy-property-scraper):
#      "302=Williamsburg".
#   U  One of the 21 ids in R's "manhattan-below-59th" region. Those 21 are, in
#      some order: Kips Bay, Murray Hill, Lower East Side, Financial District,
#      Turtle Bay, Midtown, Sutton Place, Tribeca, Midtown South, Battery Park
#      City, Soho, Fulton/Seaport, Nolita, Two Bridges, Chinatown, Little Italy,
#      Noho, Hudson Square, Central Park South, Beekman, Civic Center. The
#      eight named above (T/R) are placed; which of the remaining 13 names each
#      U id carries is unknown. All are kept: the region is exactly our area
#      plus a few east-side/civic pockets that base.make() drops.
# ---------------------------------------------------------------------------
AREAS = [
    # (id, StreetEasy area name, evidence)
    (104, "Financial District", "T"),
    (105, "Tribeca", "T"),
    (106, "Stuyvesant Town/PCV", "T"),   # our alias maps it to Gramercy Park
    (107, "Soho", "T"),
    (108, "Little Italy", "T"),
    (109, "Lower East Side", "T"),
    (110, "Chinatown", "T"),
    (113, "Gramercy Park", "T"),
    (133, "Kips Bay", "T"),
    (158, "Flatiron", "T"),
    (159, "NoMad", "T"),
    (115, "Chelsea", "R"),
    (163, "West Chelsea", "R"),
    (116, "Greenwich Village", "R"),
    (118, "Noho", "R"),                  # child of Greenwich Village
    (124, "Midtown West", "R"),
    (146, "Hudson Yards", "R"),          # child of Midtown West
    (152, "Hell's Kitchen", "R"),        # child of Midtown West
    (157, "West Village", "I"),
    (117, "East Village", "I"),
    (136, "Lincoln Square", "I"),        # first child of Upper West Side (135)
    (302, "Williamsburg", "A"),
    # Unverified names (U). Among these: Murray Hill, Turtle Bay, Midtown South,
    # Battery Park City, Nolita, Two Bridges, Hudson Square (+ Fulton/Seaport,
    # Midtown, Sutton Place, Central Park South, Beekman, Civic Center).
    (103, None, "U"), (111, None, "U"), (112, None, "U"), (114, None, "U"),
    (120, None, "U"), (121, None, "U"), (122, None, "U"), (130, None, "U"),
    (131, None, "U"), (132, None, "U"), (134, None, "U"), (162, None, "U"),
    (166, None, "U"),
]
AREA_IDS = sorted({a[0] for a in AREAS})


def search_url(page=1):
    """Studios (beds<1) at or under MAX_PRICE in our areas, newest first.

    Filter syntax as seen in StreetEasy's own indexed URLs:
    /for-rent/nyc/status:open|price:-2700|area:104,105,...?sort_by=se_score and
    /for-rent/nyc/price:-1300|beds<1|baths>=1.
    """
    filters = "|".join([
        "status:open",
        f"price:-{config.MAX_PRICE}",
        "beds<1",
        "area:" + ",".join(map(str, AREA_IDS)),
    ])
    url = f"{SITE}/for-rent/nyc/{quote(filters, safe=':,-')}?sort_by=listed_desc"
    if page > 1:
        url += f"&page={page}"
    return url


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
NON_UNIT = {"rentals", "sales", "for-rent", "for-sale", "floorplans", "documents", "amenities",
            "reviews", "photos", "map", "sale", "rental", "past-transactions", "price-history",
            "building-info", "contact", "schools", "transit", "nearby"}

# A listing link: absolute streeteasy URL, or a site-relative path right after
# a delimiter (markdown "(", quote, "=", whitespace) so CDN image paths do not match.
LINK_RE = re.compile(
    r"(?:https?://(?:www\.)?streeteasy\.com|(?<=[(\"'=\s]))"
    r"(/(?:building/([a-z0-9][a-z0-9_\-%]*)/(?:rental/(\d{5,10})|([a-z0-9][a-z0-9_\-%]*))"
    r"|rental/(\d{5,10})))"
    r"/?(?![a-z0-9_\-/%])", re.I)
RENTAL_ID_RE = re.compile(r"/rental/(\d{5,10})\b|\blisting[_\s-]?id[\"'\s:=]+\"?(\d{5,10})", re.I)
PRICE_RE = re.compile(r"\$\s?(\d{1,2},\d{3}|\d{3,5})(?![\d,])")
BEDS_RE = re.compile(r"\b([1-9])\s*(?:-\s*)?(?:beds?|bd|br|bedrooms?)\b", re.I)
STUDIO_RE = re.compile(r"\bstudio\b", re.I)
JUNIOR_RE = re.compile(r"\b(jr\.?|junior)\s*(1|one)\b", re.I)
AREA_IN_RE = re.compile(
    r"\b(?:rental\s+unit|rental|condo|co-?op|condop|townhouse|house|multi-family|apartment|"
    r"studio|unit)\s+in\s+([A-Z][A-Za-z'’./ -]{2,40}?)"
    r"(?=\s*(?:$|\n|[|,•·\]\)\[]|\s{2}|\$|\d|manhattan|brooklyn))", re.M)
ZIP_RE = re.compile(r"\b(?:NY|New York),?\s+(\d{5})\b")
SQFT_RE = re.compile(r"\b(\d{1,2},\d{3}|\d{3,4})\s*(?:ft²|ft2|sq\.?\s*ft\.?|sqft|square feet)", re.I)
MD_LINK_TEXT_RE = r"\[([^\[\]]{3,140})\]\(\s*(?:https?://(?:www\.)?streeteasy\.com)?PATH[^)]*\)"
BOT_WALL_RE = re.compile(r"px-captcha|perimeterx|press\s*&\s*hold|access to this page has been denied|"
                         r"are you a human|verify you are human", re.I)


def _key_and_url(m):
    """(source_id, canonical url) for a LINK_RE match."""
    _, slug, nested_id, unit, plain_id = m.groups()
    rid = nested_id or plain_id
    if rid:
        return rid, f"{SITE}/rental/{rid}"
    slug, unit = slug.lower(), unit.lower()
    if unit in NON_UNIT:
        return None, None
    return f"unit:{slug}/{unit}", f"{SITE}/building/{slug}/{unit}"


def _links(text):
    """Every listing link in text: list of (pos, end, source_id, url, path)."""
    out = []
    for m in LINK_RE.finditer(text):
        sid, url = _key_and_url(m)
        if sid:
            out.append((m.start(), m.end(), sid, url, m.group(1)))
    return out


def _price(segment):
    for m in PRICE_RE.finditer(segment):
        p = int(m.group(1).replace(",", ""))
        if p >= 500:
            return p
    return None


def _address(segment, path, slug_unit):
    m = re.search(MD_LINK_TEXT_RE.replace("PATH", re.escape(path)), segment, re.I)
    if m and re.search(r"\d", m.group(1)) and not m.group(1).startswith("!"):
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"\b\d{1,5}[A-Za-z]?\s+(?:[NSEW]\.?\s+|North\s+|South\s+|East\s+|West\s+)?"
                  r"[A-Za-z0-9 .'-]{2,40}?\s(?:Street|St|Avenue|Ave|Place|Pl|Road|Rd|Boulevard|Blvd|"
                  r"Lane|Ln|Drive|Dr|Square|Sq|Terrace|Way|Broadway|Bowery)\.?(?:\s*#\s*[\w-]+)?",
                  segment)
    if m:
        return m.group(0).strip()
    if slug_unit:   # "305-west-45-street-new_york/04g" -> "305 West 45 Street #04G"
        slug, _, unit = slug_unit.partition("/")
        slug = re.sub(r"[-_](new[-_]york|manhattan|brooklyn)$", "", slug)
        return slug.replace("-", " ").title() + (f" #{unit.upper()}" if unit else "")
    return None


def _area_name(text):
    m = AREA_IN_RE.search(text or "")
    if not m:
        return None
    name = re.split(r",|\s+-\s+|\s+\|", m.group(1))[0].strip(" .-")
    return name or None


def _make(sid, url, price, title, raw_neigh, text, address, flags, zipcode=None):
    seg = text or ""
    rid = RENTAL_ID_RE.search(seg)
    if sid.startswith("unit:") and rid:
        sid = rid.group(1) or rid.group(2)
        url = f"{SITE}/rental/{sid}"
    sqft = SQFT_RE.search(seg)
    no_fee = True if re.search(r"\bno[\s-]fee\b", seg, re.I) else None
    flags = list(flags)
    if JUNIOR_RE.search(seg) and "alcove-or-junior" not in flags:
        flags.append("alcove-or-junior")
    return base.make(
        NAME, sid, url, price, title or address or "StreetEasy studio",
        raw_neigh=raw_neigh, zipcode=zipcode or (ZIP_RE.search(seg).group(1) if ZIP_RE.search(seg) else None),
        text=seg[:1500], address=address, bedrooms=0,
        sqft=int(sqft.group(1).replace(",", "")) if sqft else None,
        no_fee=no_fee, description=seg[:600], flags=flags, via_tavily=True,
    )


def parse_results_text(text, via_tavily=True):
    """Parse a StreetEasy search-results page given as HTML, markdown or text.

    Returns (listings, n_cards): n_cards counts listing links found before any
    filtering, so the caller can tell "parsed nothing" from "nothing in scope".
    """
    if not text:
        return [], 0
    if "<a " in text and "</" in text:
        text = html_to_markdown(text)
    links = _links(text)
    # Group into cards: consecutive occurrences of one listing are one card.
    cards = []   # [first_pos, last_end, sid, url, path]
    for pos, end, sid, url, path in links:
        if cards and cards[-1][2] == sid:
            cards[-1][1] = end
            continue
        if any(c[2] == sid for c in cards):     # repeated later (e.g. a "featured" strip)
            continue
        cards.append([pos, end, sid, url, path])
    if not cards:
        return [], 0

    # StreetEasy cards read "Rental unit in Chelsea" either just before or just
    # after the first link of the card. Decide which from the page as a whole.
    def lookback(i):
        lo = cards[i - 1][1] if i else max(0, cards[i][0] - 300)
        return text[max(lo, cards[i][0] - 300):cards[i][0]]

    def forward(i):
        hi = cards[i + 1][0] if i + 1 < len(cards) else min(len(text), cards[i][1] + 1500)
        return text[cards[i][0]:hi]

    before = sum(1 for i in range(len(cards)) if _area_name(lookback(i)))
    after = sum(1 for i in range(len(cards)) if _area_name(forward(i)))
    area_before = before > after

    out = []
    for i, (pos, end, sid, url, path) in enumerate(cards):
        seg = forward(i)
        if STUDIO_RE.search(seg) is None and BEDS_RE.search(seg) and not JUNIOR_RE.search(seg):
            continue    # a 1+ bedroom slipped through the filter
        raw = _area_name(lookback(i)) if area_before else _area_name(seg)
        raw = raw or _area_name(seg) or _area_name(lookback(i))
        slug_unit = path.split("/building/", 1)[1] if "/building/" in path and "/rental/" not in path else None
        address = _address(seg, path, slug_unit)
        price = _price(seg)
        title = f"Studio at {address}" if address else None
        l = _make(sid, url, price, title, raw, (raw or "") + "\n" + seg, address, [])
        if l:
            l.via_tavily = via_tavily
            out.append(l)
    return out, len(cards)


def parse_search_results(results):
    """Parse tavily.search() results: [{url, title, content}]."""
    out, n = [], 0
    for r in results or []:
        url = (r.get("url") or "").strip()
        m = LINK_RE.match(url)
        if not m:
            continue
        sid, canon = _key_and_url(m)
        if not sid:
            continue
        n += 1
        title = re.sub(r"\s*[|\-–]\s*StreetEasy\s*$|^StreetEasy:\s*", "", r.get("title") or "").strip()
        content = r.get("content") or ""
        blob = f"{title}\n{content}"
        if not STUDIO_RE.search(blob):
            continue    # search results are unfiltered; only keep what says studio
        if BEDS_RE.search(blob) and not JUNIOR_RE.search(blob) and not STUDIO_RE.search(title):
            continue
        if re.search(r"\b(no longer available|off market|rented|unavailable|sold)\b", blob, re.I):
            continue
        raw = _area_name(blob)
        if raw is None:
            mt = re.search(r"\bin\s+([A-Z][A-Za-z'’./ -]{2,40}?)(?:,|\s*:|\s*\||\s*$)", title)
            raw = mt.group(1).strip() if mt else None
        slug_unit = None
        if "/building/" in canon:
            slug_unit = canon.split("/building/", 1)[1]
        address = _address(title, m.group(1), slug_unit)
        l = _make(sid, canon, _price(blob), title or (f"Studio at {address}" if address else None),
                  raw, blob, address, ["search-snippet"])
        if l:
            out.append(l)
    return out, n


def html_to_markdown(html):
    """Flatten HTML to text, keeping links as [text](href) so the text parser works on it."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    for a in soup.find_all("a", href=True):
        a.replace_with(f" [{a.get_text(' ', strip=True)}]({a['href']}) ")
    return soup.get_text("\n")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def _dedupe(listings):
    seen, out = set(), []
    for l in listings:
        if l.source_id not in seen:
            seen.add(l.source_id)
            out.append(l)
    return out


def _fetch_direct():
    s = http.session()
    s.headers["Referer"] = SITE + "/"
    found, any_cards = [], False
    for page in range(1, DIRECT_PAGES + 1):
        if page > 1:
            time.sleep(random.uniform(0.5, 1.5))
        r = http.get(search_url(page), s=s, retries=1)
        if BOT_WALL_RE.search(r.text[:50000]):
            raise http.Blocked(f"{search_url(page)} -> bot wall in 200 response")
        listings, n = parse_results_text(r.text, via_tavily=False)
        if not n:
            break
        any_cards = True
        before = len({l.source_id for l in found})
        found.extend(listings)
        if len({l.source_id for l in found}) == before:
            break
    if not any_cards:
        # 200 but no listing links: page layout changed or a soft block. Let
        # the Tavily fallback have a go rather than report a false zero.
        raise http.Blocked("StreetEasy direct: 200 but no listing links parsed")
    log.warning("streeteasy: direct request WORKED (%d listings); Tavily not needed", len(found))
    return _dedupe(found)


def _fetch_tavily(reason):
    if not config.TAVILY_API_KEY:
        raise http.Blocked(f"{reason}; Tavily fallback unavailable (TAVILY_API_KEY not set)")
    start = tavily.credits_used
    errors = []

    # Strategy 1: extract one results URL.
    try:
        url = search_url()
        pages = tavily.extract([url], depth=EXTRACT_DEPTH)
        text = "\n".join(v or "" for v in pages.values())
        if text and BOT_WALL_RE.search(text[:5000]) and not _links(text):
            errors.append("extract: Tavily got the bot wall")
        else:
            listings, n = parse_results_text(text)
            if n:
                return _dedupe(listings)
            errors.append(f"extract: no listing links in {len(text)} chars")
    except Exception as e:   # TavilyUnavailable (budget), network, HTTP, JSON shape
        errors.append(f"extract: {type(e).__name__}: {e}")

    # Strategy 2: one search, if the budget still allows it.
    if tavily.credits_used - start + 1 <= MAX_CREDITS:
        try:
            results = tavily.search(SEARCH_QUERY, include_domains=["streeteasy.com"], max_results=20)
            listings, n = parse_search_results(results)
            if n:
                return _dedupe(listings)
            errors.append(f"search: no listing URLs in {len(results or [])} results")
        except Exception as e:
            errors.append(f"search: {type(e).__name__}: {e}")
    else:
        errors.append("search: skipped, StreetEasy credit cap reached")
    raise http.Blocked(f"{reason}; Tavily fallback failed ({'; '.join(errors)})")


def fetch():
    try:
        return _fetch_direct()
    except http.Blocked as e:
        reason = f"direct blocked: {e}"
    except Exception as e:   # timeouts, 5xx after retries, connection resets
        reason = f"direct failed: {type(e).__name__}: {e}"
    log.info("streeteasy: %s; trying Tavily", reason)
    return _fetch_tavily(reason)
