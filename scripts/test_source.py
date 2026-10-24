"""Run one source and print what it returns. Usage: python -m scripts.test_source craigslist"""
import importlib
import json
import sys
import time
import traceback

sys.path.insert(0, ".")
from finder.http import Blocked

name = sys.argv[1]
mod = importlib.import_module(f"finder.sources.{name}")
t = time.time()
try:
    listings = mod.fetch()
except Blocked as e:
    print(f"BLOCKED: {e}")
    sys.exit(2)
except Exception:
    traceback.print_exc()
    sys.exit(1)
print(f"{len(listings)} listings in {time.time()-t:.1f}s")
bad = [l for l in listings if not l.is_valid()]
if bad:
    print(f"WARNING {len(bad)} invalid listings")
for l in listings[:8]:
    print(json.dumps(l.to_dict(), indent=1, default=str)[:700])
    print("---")
neighs = {}
for l in listings:
    neighs[l.neighborhood] = neighs.get(l.neighborhood, 0) + 1
print("by neighbourhood:", neighs)
print("price range:", min((l.price for l in listings), default=None), max((l.price for l in listings), default=None))
