"""Debug aid: fetch pages directly and print structural clues for writing a parser.
Usage: python -m scripts.debug_html <url|streeteasy> ...
Prints: status, title, script inventory, ld+json, __NEXT_DATA__ head, context around key tokens,
and hrefs containing 'rent'. Writes nothing."""
import re
import sys
from collections import Counter

sys.path.insert(0, ".")
from finder import http

CAP = 7000
TOKENS = ["/rental/", "/building/", "\"price\"", "listingId", "__NEXT_DATA__", "application/ld+json",
          "data-listing", "listing-card", "rental-details", "short-term-rental-details", "apollo", "__APOLLO_STATE__",
          "window.__", "searchResults", "\"bedrooms\"", "studio"]


def show(url):
    print(f"\n\n======== URL {url}")
    try:
        r = http.get(url, retries=1)
    except Exception as e:
        print(f"### FAILED {type(e).__name__}: {e}")
        return
    h = r.text
    print(f"### status={r.status_code} bytes={len(h)} final_url={r.url}")
    m = re.search(r"<title[^>]*>(.*?)</title>", h, re.S | re.I)
    print("### title:", (m.group(1).strip()[:300] if m else None))
    print("### scripts:")
    for sm in re.finditer(r"<script([^>]*)>(.*?)</script>", h, re.S | re.I):
        attrs, body = sm.group(1), sm.group(2)
        if len(body) > 2000 or "ld+json" in attrs or "__NEXT_DATA__" in attrs:
            print(f"  attrs={attrs.strip()[:120]!r} len={len(body)} head={body[:160]!r}")
    for sm in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", h, re.S | re.I):
        print("### LD+JSON:\n" + sm.group(1)[:4000])
    nm = re.search(r"<script[^>]*id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", h, re.S)
    if nm:
        print("### NEXT_DATA head:\n" + nm.group(1)[:CAP])
    print("### token counts:", {t: h.count(t) for t in TOKENS})
    for t in ["/rental/", "/building/", "short-term-rental-details", "rental-details", "listing-card", "data-listing", "__APOLLO_STATE__", "window.__"]:
        idxs = [m.start() for m in re.finditer(re.escape(t), h)][:3]
        for i in idxs:
            print(f"### CONTEXT {t!r} @{i}:\n" + h[max(0, i - 400): i + 900].replace("\n", " ") + "\n### END")
    hrefs = Counter(re.findall(r'href="([^"]*rent[^"]*)"', h))
    print("### hrefs with 'rent' (unique, first 60):")
    for u, c in list(hrefs.items())[:60]:
        print(f"  {c}x {u[:160]}")


for arg in sys.argv[1:]:
    if arg == "streeteasy":
        from finder.sources import streeteasy
        show(streeteasy.search_url(1))
    else:
        show(arg)
