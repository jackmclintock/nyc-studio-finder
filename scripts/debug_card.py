"""Debug aid: dump the first N elements matching a CSS selector, plus every form's fields.
Usage: CARDS env, one per line: url|selector|N"""
import os, sys
sys.path.insert(0, ".")
from bs4 import BeautifulSoup
from finder import http

for line in os.environ.get("CARDS", "").splitlines():
    if not line.strip():
        continue
    url, _, rest = line.partition("|")
    sel, _, n = rest.partition("|")
    n = int(n or 2)
    print(f"\n\n======== CARDS {url} selector={sel!r}")
    try:
        r = http.get(url.strip(), retries=1)
    except Exception as e:
        print(f"### FAILED {type(e).__name__}: {e}")
        continue
    soup = BeautifulSoup(r.text, "lxml")
    els = soup.select(sel.strip())
    print(f"### status={r.status_code} matches={len(els)}")
    for el in els[:n]:
        html = str(el)
        print(f"### ELEMENT len={len(html)}\n{html[:14000]}\n### END ELEMENT")
    for f in soup.find_all("form")[:6]:
        print(f"### FORM action={f.get('action')} method={f.get('method')} id={f.get('id')}")
        for inp in f.find_all(["input", "select"]):
            opts = [(o.get("value"), o.get_text(strip=True)[:25]) for o in inp.find_all("option")][:40]
            print(f"   {inp.name} name={inp.get('name')} type={inp.get('type')} value={inp.get('value')} {opts if opts else ''}")
