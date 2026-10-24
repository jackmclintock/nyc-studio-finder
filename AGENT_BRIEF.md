# Brief for scraper subagents

You are writing one or more source modules for a small NYC studio-apartment finder.
Project root: /home/user/nyc-studio-finder. Python 3.13, deps: requests, beautifulsoup4, lxml, python-dateutil.

## Read first (do not modify these; propose changes in your final report instead)
- finder/sources/__init__.py   the interface every source module must satisfy
- finder/sources/base.py       helpers: make(), parse_price(), resolve_neighborhood(), flags
- finder/models.py             the Listing dataclass
- finder/neighborhoods.py      canonical neighbourhoods, aliases, zip fallback
- finder/http.py               session(), get(), Blocked exception, bot-wall detection
- finder/tavily.py             extract(urls) and search(query, include_domains) with credit accounting
- finder/config.py             MAX_PRICE=2500, studios only, etc.

## Search criteria
Studios (0 bedrooms) at or under $2,500/month in these neighbourhoods:
Chelsea, Hudson Yards, Hudson Square, Tribeca, Hell's Kitchen, East Village, Lower East Side,
Kips Bay, Murray Hill, Turtle Bay, Two Bridges, Chinatown, Financial District, Battery Park City,
Williamsburg (Brooklyn), Midtown South, Midtown West, Lincoln Square, Flatiron, NoMad, West Village,
Nolita, Greenwich Village, Gramercy Park, Little Italy, NoHo, SoHo.
That is essentially Manhattan below roughly 66th St on the west and 59th St on the east, plus Williamsburg.
Include alcove studios and junior one-beds but flag them (base.extra_flags does this from text).

## Your module: finder/sources/<name>.py
Must expose NAME, LABEL, NEEDS_TAVILY and fetch() -> list[Listing]. Use base.make(...) to build each
Listing; it returns None for out-of-budget or out-of-area listings, so just skip Nones.
- Use finder.http.get() for requests so bot walls raise Blocked consistently.
- If the site blocks direct requests, your fetch() should: try direct first, and on Blocked fall back to
  Tavily (finder.tavily.extract or .search) if the fallback is viable, else re-raise Blocked.
  Set NEEDS_TAVILY = True if the direct route is known to fail from datacenter IPs.
- Tavily is rationed: a fallback must cost at most 2 to 3 credits per run. Prefer one extract() call on a
  search-results URL (1 credit per 5 URLs) over many search() calls. If you cannot test Tavily live (no
  key in this environment), write the fallback against the API as implemented in finder/tavily.py,
  parse defensively, and say clearly in your report that it is untested.
- Pass the source's neighbourhood string as raw_neigh, the zip if you have it, and the title/description
  as text so base can resolve the canonical neighbourhood. If your source lets you filter by
  neighbourhood server-side, do that and still pass raw_neigh.
- source_id must be stable across runs (listing id from the site, or a hash of the URL as last resort).
- url must deep-link to the listing on the hosting platform.
- Be polite: at most ~10 requests per run, time.sleep(0.5 to 1.5) between page fetches, stop paginating
  once prices exceed the budget if the site sorts by price.
- Do not write anything outside finder/sources/<name>.py except scratch files in /tmp/claude-0/.../scratchpad.
- No external deps beyond those listed. No Playwright or Selenium (this must run cheaply in GitHub Actions).

## Test it
    cd /home/user/nyc-studio-finder && python3 -m scripts.test_source <name>
Check: prices all <= 2500, every neighbourhood is a canonical one, URLs open to the right listing,
no duplicates, no 1-bedrooms sneaking in. Run it twice to confirm source_ids are stable.

## Environment note
This container goes out through a datacenter proxy, which is a fair stand-in for GitHub Actions
runners. If a site blocks you here it will very likely block in Actions too; do not assume otherwise.

## Report back (keep it under 300 words)
Which sources work directly, which need Tavily, which are dead ends and why; listing counts you saw;
any site-specific quirks (pagination, ids, neighbourhood naming); any alias or zip you think should be
added to finder/neighborhoods.py; and anything untested.
