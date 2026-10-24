"""Persistent state: data/listings.json (every listing we have ever seen, with
first_seen/last_seen) and data/state.json (run metadata, digest bookkeeping).

Both are committed back to the repo by the GitHub Action, which is what gives
us "new since last time" without a database.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from . import config

LISTINGS_PATH = os.path.join(config.DATA_DIR, "listings.json")
STATE_PATH = os.path.join(config.DATA_DIR, "state.json")


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.replace(microsecond=0).isoformat()


def parse(ts):
    return datetime.fromisoformat(ts)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True, default=str)
    os.replace(tmp, path)


def load_listings():
    return load_json(LISTINGS_PATH, {})


def load_state():
    return load_json(STATE_PATH, {
        "runs": [], "last_digest_at": None, "alerted_keys": [], "digested_keys": [],
        "tavily_last_used_at": {},
    })


def merge(existing, fresh, sources_ok):
    """Merge this run's listings into the store.

    existing: dict key -> record; fresh: list[Listing]; sources_ok: set of source
    names that returned successfully this run (so we only mark things 'gone' for
    sources we actually heard from).
    Returns (store, new_keys, gone_keys).
    """
    t = now()
    ts = iso(t)
    new_keys, seen_keys = [], set()
    for l in fresh:
        rec = l.to_dict()
        k = rec["key"]
        seen_keys.add(k)
        if k in existing:
            old = existing[k]
            price_hist = old.get("price_history", [])
            if old.get("price") != rec["price"]:
                price_hist.append({"at": ts, "from": old.get("price"), "to": rec["price"]})
            rec["first_seen"] = old.get("first_seen", ts)
            rec["price_history"] = price_hist
        else:
            rec["first_seen"] = ts
            rec["price_history"] = []
            new_keys.append(k)
        rec["last_seen"] = ts
        rec["gone"] = False
        existing[k] = rec

    gone_keys = []
    grace = timedelta(hours=config.GONE_GRACE_HOURS)
    for k, rec in list(existing.items()):
        if k in seen_keys:
            continue
        if rec["source"] not in sources_ok:
            continue  # source failed this run; don't judge its listings
        if not rec.get("gone"):
            rec["gone"] = True
            rec["gone_at"] = ts
            gone_keys.append(k)
        if parse(rec["last_seen"]) < t - grace:
            del existing[k]
    return existing, new_keys, gone_keys


def is_new(rec, t=None):
    t = t or now()
    return parse(rec["first_seen"]) >= t - timedelta(hours=config.NEW_HIGHLIGHT_HOURS)


def active(store):
    return [r for r in store.values() if not r.get("gone")]
