"""RentHop home runner.

RentHop refuses requests from cloud servers, so this script runs on a home
computer instead: it fetches RentHop's studio search over the home connection
with an ordinary HTTP request, parses the listings, and uploads them to the
repo's "renthop-data" branch. The hourly GitHub job reads that file and merges
RentHop into the board.

If RentHop answers with a bot check even from home, the runner records
"blocked" and stops. It does not try to get past the check.

    python3 -m local.renthop_local            fetch, parse, upload
    python3 -m local.renthop_local --dry-run  fetch and parse only, print results

The first page fetched is saved to local/last_renthop.html so the parser can be
tuned to what RentHop actually serves.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from finder.http import Blocked, get, session  # noqa: E402
from finder.sources import renthop  # noqa: E402

REPO = os.environ.get("STUDIO_FINDER_REPO", "jackmclintock/nyc-studio-finder")
BRANCH = "renthop-data"
DATA_PATH = "data/renthop.json"
PAGES = 3
DEBUG_HTML = os.path.join(ROOT, "local", "last_renthop.html")
LOG = os.path.join(ROOT, "local", "renthop_local.log")


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def page_url(page):
    return renthop.SEARCH_URL.replace("page=1", f"page={page}")


def fetch_pages():
    """Ordinary HTTP requests from this machine. Raises Blocked if RentHop refuses."""
    s = session()
    pages = []
    for p in range(1, PAGES + 1):
        r = get(page_url(p), s=s, retries=1)
        pages.append(r.text)
        if not renthop.LISTING_RE.search(r.text):
            break
        time.sleep(2)
    return pages


def parse_pages(pages):
    listings, seen = [], set()
    for html in pages:
        for l in renthop.parse(renthop._html_to_md(html), via_tavily=False):
            if l.source_id not in seen:
                seen.add(l.source_id)
                listings.append(l)
    return listings


def gh(*args, check=True):
    return subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=check)


def upload(payload):
    """PUT data/renthop.json on the renthop-data branch via the GitHub API (needs `gh auth login`)."""
    content = base64.b64encode(json.dumps(payload, indent=1, default=str).encode()).decode()
    if gh(f"repos/{REPO}/git/ref/heads/{BRANCH}", check=False).returncode != 0:
        main = gh(f"repos/{REPO}/git/ref/heads/main", "-q", ".object.sha").stdout.strip()
        gh("-X", "POST", f"repos/{REPO}/git/refs", "-f", f"ref=refs/heads/{BRANCH}", "-f", f"sha={main}")
        log(f"created branch {BRANCH}")
    sha = gh(f"repos/{REPO}/contents/{DATA_PATH}?ref={BRANCH}", "-q", ".sha", check=False).stdout.strip()
    args = ["-X", "PUT", f"repos/{REPO}/contents/{DATA_PATH}",
            "-f", f"message=RentHop home runner {payload['fetched_at']}",
            "-f", f"content={content}", "-f", f"branch={BRANCH}"]
    if sha:
        args += ["-f", f"sha={sha}"]
    gh(*args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        pages = fetch_pages()
    except Blocked as e:
        log(f"BLOCKED: RentHop refused the request from this connection ({e})")
        if not a.dry_run:
            upload({"fetched_at": now, "status": "blocked", "note": str(e)[:200], "listings": []})
        sys.exit(2)
    except Exception as e:
        log(f"ERROR fetching: {type(e).__name__}: {e}")
        sys.exit(1)

    if pages:
        with open(DEBUG_HTML, "w") as f:
            f.write(pages[0])
    listings = parse_pages(pages)
    n_links = sum(len(renthop.LISTING_RE.findall(h)) for h in pages)
    log(f"fetched {len(pages)} page(s): {n_links} listing links, {len(listings)} studios in scope")
    if not n_links or (n_links and not listings):
        log("nothing usable parsed: send local/last_renthop.html to Claude so the parser can be tuned")

    payload = {"fetched_at": now, "status": "ok" if n_links else "no-links", "via": "requests",
               "pages": len(pages), "listings": [l.to_dict() for l in listings]}
    for l in listings[:40]:
        print(f"  {l.price} {l.neighborhood} | {l.title[:60]} | {l.url}")
    if a.dry_run:
        print("dry run: not uploading")
        return
    upload(payload)
    log(f"uploaded {len(listings)} listings to {REPO}@{BRANCH}")


if __name__ == "__main__":
    main()
