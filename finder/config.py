"""Search criteria and runtime settings. Edit here, not in the scrapers."""
import os

MAX_PRICE = 2500            # USD per month, inclusive
ALERT_PRICE = 2200          # new listings at or under this trigger an immediate email
BEDROOMS = 0                # studio

# Listings are kept on the page for this long after they stop appearing on their source.
GONE_GRACE_HOURS = 48
# A listing counts as "new" (highlighted) for this long after first being seen.
NEW_HIGHLIGHT_HOURS = 24

# Twice-daily digest, New York local time (24h).
DIGEST_HOURS_NY = (8, 18)
TIMEZONE = "America/New_York"

RECIPIENT = os.environ.get("RECIPIENT_EMAIL", "sam.mclintock97@gmail.com")
CC = [e for e in os.environ.get("CC_EMAIL", "jack.mclintock@gmail.com").split(",") if e]
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# Public URL of the GitHub Pages site, used in emails. Set by the workflow.
SITE_URL = os.environ.get("SITE_URL", "")

# Tavily is rationed: sources that need it are only refreshed every N hours.
TAVILY_REFRESH_HOURS = 6          # default; a source module can override with its own TAVILY_REFRESH_HOURS
TAVILY_MAX_CREDITS_PER_RUN = 8

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
