"""Debug aid: run the named sources with every HTTP and Tavily call logged in full.
Usage: python -m scripts.debug_fetch streeteasy zillow ...   (needs TAVILY_API_KEY)
Prints raw responses so parsers can be tuned from the Actions log. Writes nothing."""
import importlib
import re
import sys
import traceback

sys.path.insert(0, ".")
from finder import http, tavily
from finder.http import Blocked

CAP = int(__import__("os").environ.get("DEBUG_CAP", "9000"))


def strip_html(t):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


import requests
_req = requests.Session.request
def logged_request(self, method, url, *a, **kw):
    r = _req(self, method, url, *a, **kw)
    body = kw.get("json")
    print(f"\n### HTTP {method} {url} -> {r.status_code} bytes={len(r.content)}" + (f" json_body={str(body)[:600]}" if body else ""))
    if "tavily.com" not in url:
        print("### BODY HEAD:\n" + r.text[:3500])
    return r
requests.Session.request = logged_request

_get = http.get
def logged_get(url, *a, **kw):
    print(f"\n### DIRECT GET {url}")
    try:
        r = _get(url, *a, **kw)
    except Blocked as e:
        print(f"### BLOCKED {e}")
        raise
    except Exception as e:
        print(f"### ERROR {type(e).__name__}: {e}")
        raise
    print(f"### status={r.status_code} bytes={len(r.text)} final_url={r.url}")
    print("### HTML HEAD:\n" + r.text[:3000])
    print("### TEXT:\n" + strip_html(r.text)[:CAP])
    return r
http.get = logged_get

_extract = tavily.extract
def logged_extract(urls, depth="basic"):
    print(f"\n### TAVILY EXTRACT depth={depth} urls={urls}")
    out = _extract(urls, depth)
    for u, c in out.items():
        print(f"### EXTRACT RESULT {u} chars={len(c or '')}\n{(c or '')[:CAP]}\n### END")
    return out
tavily.extract = logged_extract

_search = tavily.search
def logged_search(query, include_domains=None, max_results=10, depth="basic", days=None):
    print(f"\n### TAVILY SEARCH q={query!r} domains={include_domains} n={max_results} depth={depth}")
    res = _search(query, include_domains, max_results, depth, days)
    for r in res:
        print(f"- {r.get('url')}\n  title: {r.get('title')}\n  content: {(r.get('content') or '')[:400]}")
    return res
tavily.search = logged_search

for name in sys.argv[1:]:
    print(f"\n\n======== SOURCE {name} ========")
    mod = importlib.import_module(f"finder.sources.{name}")
    # modules may have imported the names directly; patch those too
    for attr, fn in (("get", logged_get),):
        if hasattr(mod, attr) and getattr(mod, attr) is _get:
            setattr(mod, attr, fn)
    try:
        ls = mod.fetch()
        print(f"\n### RESULT {name}: {len(ls)} listings")
        for l in ls[:15]:
            print(f"  {l.price} {l.neighborhood} | {l.title[:60]!r} | {l.address} | {l.url} | {l.flags}")
    except Blocked as e:
        print(f"\n### RESULT {name}: BLOCKED {e}")
    except Exception:
        print(f"\n### RESULT {name}: EXCEPTION")
        traceback.print_exc()
print(f"\n### tavily credits used: {tavily.credits_used}")
