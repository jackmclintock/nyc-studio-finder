"""Debug aid: try several searchQueryState shapes against Zillow's JSON endpoint."""
import json, sys
sys.path.insert(0, ".")
from finder.http import session
from finder.sources import zillow

box = zillow.BOXES["manhattan"]
base = zillow._sqs(box)
variants = {
    "current": base,
    "region+zoom": {**base, "usersSearchTerm": "New York, NY", "mapZoom": 13,
                     "regionSelection": [{"regionId": 6181, "regionType": 6}]},
    "longkeys": {"pagination": {}, "usersSearchTerm": "New York, NY", "mapBounds": box, "mapZoom": 13,
                 "regionSelection": [{"regionId": 6181, "regionType": 6}], "isMapVisible": True, "isListVisible": True,
                 "filterState": {"isForRent": {"value": True}, "isForSaleByAgent": {"value": False},
                                 "isForSaleByOwner": {"value": False}, "isNewConstruction": {"value": False},
                                 "isComingSoon": {"value": False}, "isAuction": {"value": False},
                                 "isForSaleForeclosure": {"value": False}, "beds": {"min": 0, "max": 0},
                                 "monthlyPayment": {"max": 2500}, "sortSelection": {"value": "days"}}},
    "nobounds-region": {**base, "mapBounds": None, "usersSearchTerm": "New York, NY",
                        "regionSelection": [{"regionId": 6181, "regionType": 6}]},
}
s = session()
s.headers.update({"Accept": "*/*", "Content-Type": "application/json", "Origin": zillow.SITE,
                  "Referer": f"{zillow.SITE}/new-york-ny/rentals/"})
for name, sqs in variants.items():
    sqs = {k: v for k, v in sqs.items() if v is not None}
    body = {"searchQueryState": sqs, "wants": {"cat1": ["listResults", "mapResults"], "cat2": ["total"]}, "requestId": 5, "isDebugRequest": False}
    print(f"\n### VARIANT {name}")
    try:
        r = s.put(f"{zillow.SITE}/async-create-search-page-state", json=body, timeout=25)
    except Exception as e:
        print("### FAILED", e); continue
    print("### status", r.status_code, "bytes", len(r.text))
    try:
        j = r.json()
    except Exception:
        print(r.text[:800]); continue
    print("### title:", j.get("searchPageSeoObject", {}).get("windowTitle"), "| totalResultCount:", (j.get("cat1") or {}).get("searchList", {}).get("totalResultCount"))
    res = ((j.get("cat1") or {}).get("searchResults") or {}).get("listResults") or []
    print("### listResults:", len(res), "| mapResults:", len(((j.get("cat1") or {}).get("searchResults") or {}).get("mapResults") or []))
    for it in res[:8]:
        units = [(u.get("beds"), u.get("price")) for u in (it.get("units") or [])][:4]
        print(f"  {it.get('beds')}bd {it.get('unformattedPrice') or it.get('price')} | {it.get('address')} | {it.get('statusText')} | units={units} | {it.get('detailUrl')}")
