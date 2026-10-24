"""Debug aid: run Tavily searches and print results. Usage: QUERIES env, one per line: "query|domain"."""
import os, sys
sys.path.insert(0, ".")
from finder import tavily
for line in os.environ.get("QUERIES", "").splitlines():
    if not line.strip():
        continue
    q, _, dom = line.partition("|")
    print(f"\n### TAVILY SEARCH {q!r} domain={dom!r}")
    try:
        res = tavily.search(q.strip(), include_domains=[dom.strip()] if dom.strip() else None, max_results=20)
    except Exception as e:
        print("### FAILED", e); continue
    for r in res:
        print(f"- {r.get('url')}\n  title: {r.get('title')}\n  content: {(r.get('content') or '')[:500]}")
for u in os.environ.get("EXTRACT_URLS", "").split():
    print(f"\n### TAVILY EXTRACT {u}")
    try:
        out = tavily.extract([u])
    except Exception as e:
        print("### FAILED", e); continue
    for k, v in out.items():
        print(f"### RESULT {k} chars={len(v or '')}\n{(v or '')[:12000]}\n### END")
print("### credits:", tavily.credits_used)
