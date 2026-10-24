# NYC Studio Finder

A small, free tool that checks the main New York rental platforms every hour for
studios at or under $2,500 a month in lower and midtown Manhattan and Williamsburg,
publishes them to a web page with new listings highlighted, and emails a digest
twice a day (8 am and 6 pm New York time) plus an immediate alert whenever a new
listing appears at or under $2,200.

Everything runs on GitHub Actions and GitHub Pages. There is no server and nothing to pay for
beyond the Tavily free tier.

## One-time setup (about ten minutes)

### 1. Secrets and variables
In the repo on GitHub: **Settings → Secrets and variables → Actions**.

Under **Secrets**, add:

| Name | Value |
| --- | --- |
| `TAVILY_API_KEY` | From https://app.tavily.com (API Keys). Only used for sites that block direct requests. |
| `GMAIL_ADDRESS` | The Gmail address the emails should come from. |
| `GMAIL_APP_PASSWORD` | A 16-character Gmail app password for that account (see below). Not your normal password. |

Under **Variables**, add:

| Name | Value |
| --- | --- |
| `RECIPIENT_EMAIL` | Who gets the emails. |
| `CC_EMAIL` | Optional, comma separated. |

**Getting a Gmail app password:** the Google account needs 2-Step Verification turned on.
Then go to https://myaccount.google.com/apppasswords, type a name like "studio finder",
click Create, and copy the 16-character code into the `GMAIL_APP_PASSWORD` secret.
You can revoke it from the same page any time.

### 2. Turn on GitHub Pages
**Settings → Pages → Build and deployment**: Source "Deploy from a branch", branch `main`,
folder `/docs`, Save. The site appears at `https://<your-username>.github.io/nyc-studio-finder/`
a minute or two after the first run.

### 3. Run it once by hand
**Actions → Find studios → Run workflow**. Leave "Force a digest email" unticked the first
time. The first run seeds the history, so nothing is emailed; from the second run on, new
listings are highlighted and alerts go out. Tick the digest box on a later manual run if
you want to see what the digest email looks like straight away.

After that it runs itself. The schedule is set for twice an hour because GitHub delays or
drops some scheduled runs on a best-effort basis, so this keeps roughly hourly coverage.

## What it searches

Studios (including alcove studios and junior one-beds, which are flagged) at or under
$2,500 in: Chelsea, Hudson Yards, Hudson Square, Tribeca, Hell's Kitchen, East Village,
Lower East Side, Kips Bay, Murray Hill, Turtle Bay, Two Bridges, Chinatown, Financial
District, Battery Park City, Williamsburg, Midtown South, Midtown West, Lincoln Square,
Flatiron, NoMad, West Village, Nolita, Greenwich Village, Gramercy Park, Little Italy,
NoHo, SoHo.

To change the budget, alert threshold, digest times or neighbourhoods, edit
`finder/config.py` and `finder/neighborhoods.py`. Nothing else needs touching.

## Sources

See the "Source health" panel at the bottom of the page for what worked on the latest run.

| Source | How | Notes |
| --- | --- | --- |
| Craigslist, Zumper, Realtor.com, Apartment List, Nooklyn, NYBits, Listings Project, Leasebreak | Direct | Reliable from GitHub's runners. PadMapper is a mirror of Zumper. Leasebreak is furnished sublets, flagged as such. |
| StreetEasy, Zillow (also covers Trulia and HotPads) | Direct, intermittent | Their bot protection lets some hourly runs through and blocks others. Listings found on a good run stay on the board; a blocked run is reported, not treated as "everything gone". |
| Apartments.com | Tavily | Read through Tavily every six hours, about 120 credits a month against the free 1,000. |
| RentHop, CityRealty | Not available | RentHop blocks both direct and Tavily reads. CityRealty loads listings with JavaScript after the page. Both show as blocked on the page so it is honest about coverage. |
| Facebook Marketplace, Localize | Not covered | Facebook needs a login. Localize is a frozen 2020 snapshot. |

## Running locally

    pip install -r requirements.txt
    python -m finder.run --no-email          # fetch, merge, render docs/index.html
    python -m scripts.test_source craigslist # try one source

Without Gmail credentials, emails are written to `data/last_email.html` instead of sent.
