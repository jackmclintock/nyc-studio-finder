"""Leasebreak: furnished sublets and lease takeovers, mostly short term.

Direct requests work from GitHub's runners (tested 2026-10). The site's
advanced search takes form fields named asm[...]: apartmentSizeId=1 is
"Studios", privateRoom=entire excludes rooms, bIds[1]=Manhattan, bIds[2]=Brooklyn.
Every listing is flagged "sublet". Monthly rent is taken from the 12-month row of
the duration-pricing table when present, else the low end of the price range.
"""
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..http import Blocked
from . import base

NAME = "leasebreak"
LABEL = "Leasebreak"
NEEDS_TAVILY = False      # direct works; if it stops, the page just shows blocked (no credits spent)

BASE_URL = "https://www.leasebreak.com"
SEARCH_URL = (f"{BASE_URL}/advanced-search?asm[state]=1&asm[privateRoom]=entire&asm[apartmentSizeId]=1"
              f"&asm[minPrice]=0&asm[maxPrice]=2500&asm[bIds][1]=1&asm[bIds][2]=2")
MAX_PAGES = 2

ID_RE = re.compile(r"/short-term-rental-details/(\d+)/")
PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{3,6})")
BEDS_RE = re.compile(r"\b([1-9])\s*(?:-\s*)?(?:bed(?:room)?s?|br|bd)\b", re.I)


def _monthly_rent(card):
    text = card.get_text(" ", strip=True)
    # 12-month row of the duration pricing table, if any
    for tr in card.select("table.flexible-price-table tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) >= 2 and re.match(r"12\s*Months?", cells[0], re.I):
            m = PRICE_RE.search(cells[1])
            if m:
                return base.parse_price(m.group(1))
    el = card.select_one(".detail-right-price") or card.select_one(".search-item-header-price")
    prices = [base.parse_price(p) for p in PRICE_RE.findall(el.get_text(" ", strip=True) if el else text)]
    prices = [p for p in prices if p]
    return min(prices) if prices else None


def parse_html(html):
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for card in soup.select("div.search-item"):
        a = card.select_one("a[href*='short-term-rental-details']")
        if not a:
            continue
        m = ID_RE.search(a["href"])
        if not m or m.group(1) in seen:
            continue
        lid = m.group(1)
        url = urljoin(BASE_URL, a["href"].split("?")[0])
        h3 = card.find("h3")
        address = h3.get_text(" ", strip=True) if h3 else None
        neigh_el = card.select_one("span.text")
        raw = neigh_el.get_text(" ", strip=True) if neigh_el else None
        text = card.get_text(" ", strip=True)
        if BEDS_RE.search(text) and not re.search(r"\bstudio\b", text, re.I):
            continue
        price = _monthly_rent(card)
        img = card.select_one("img.no-crop") or card.select_one(".search-item-img img")
        move_in = re.search(r"Earliest Move-In\s*(\d{1,2}/\d{1,2}/\d{2,4})", text)
        desc = "Furnished sublet via Leasebreak." + (f" Earliest move-in {move_in.group(1)}." if move_in else "")
        l = base.make(NAME, lid, url, price, f"Studio sublet at {address}" if address else "Studio sublet",
                      raw_neigh=raw, text=f"{address or ''} {raw or ''}", address=address,
                      image_url=img["src"] if img and img.get("src", "").startswith("http") else None,
                      description=desc, flags=["sublet"])
        if l:
            seen.add(lid)
            out.append(l)
    return out, len(soup.select("div.search-item"))


def fetch():
    out, s = [], http.session()
    for page in range(1, MAX_PAGES + 1):
        url = SEARCH_URL + (f"&page={page}" if page > 1 else "")
        r = http.get(url, s=s, retries=1)
        listings, n_cards = parse_html(r.text)
        out.extend(listings)
        if n_cards < 20:
            break
        time.sleep(1)
    seen, dedup = set(), []
    for l in out:
        if l.source_id not in seen:
            seen.add(l.source_id)
            dedup.append(l)
    return dedup
