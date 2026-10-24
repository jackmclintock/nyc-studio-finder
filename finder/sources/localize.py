"""Localize (localize.city): dead end. Returns [] without making any requests.

Checked 2026-10-24 from the project's proxy:
- https://www.localize.city/ answers 200, but it is a frozen static copy of the
  old React app served by Apache. Its embedded config still has URLs rewritten
  from a Wayback Machine snapshot ("/web/20200612005517/https://www.localize.city/api2").
- The rentals page https://www.localize.city/for-rent/new-york-ny also answers 200
  (about 420KB), but the listings in it are the same frozen snapshot: dates in
  the payload are from May 2020, and the count ("9996 apartments") never changes.
- Every live endpoint the app would call (/api2/..., /api2/graphql, /search/...)
  returns Apache's 404 page. There is no live search API to query.

The consumer listings product has been shut down, and what is left would only
give six-year-old listings. fetch() therefore returns [] instead of scraping the
snapshot. If Localize ever comes back, the for-rent page above is where to start.
"""

NAME = "localize"
LABEL = "Localize"
NEEDS_TAVILY = False


def fetch():
    return []
