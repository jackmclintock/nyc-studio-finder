"""One run: fetch every source, merge into the store, render the page, send
alerts and (at digest hours) the digest, persist state.

    python -m finder.run            normal hourly run
    python -m finder.run --digest   force a digest email this run
    python -m finder.run --no-email skip all email (local testing)
"""
import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, dedupe, emailer, render, store, tavily
from .http import Blocked
from .sources import all_sources


def ny_now():
    return datetime.now(ZoneInfo(config.TIMEZONE))


def run(force_digest=False, no_email=False, only=None):
    listings_store = store.load_listings()
    state = store.load_state()
    t0 = time.time()
    fresh, status, ok_sources = [], [], set()
    labels = {}

    for mod in all_sources():
        labels[mod.NAME] = mod.LABEL
        if only and mod.NAME not in only:
            continue
        entry = {"name": mod.NAME, "label": mod.LABEL, "status": "error", "count": None, "note": ""}
        status.append(entry)

        # Ration Tavily-backed sources.
        if getattr(mod, "NEEDS_TAVILY", False):
            last = state["tavily_last_used_at"].get(mod.NAME)
            every = getattr(mod, "TAVILY_REFRESH_HOURS", config.TAVILY_REFRESH_HOURS)
            if last and store.parse(last) > store.now() - timedelta(hours=every):
                entry.update(status="skipped", note=f"Tavily-backed; refreshed every {every}h, last {last[:16]}Z")
                continue
            if not config.TAVILY_API_KEY:
                entry.update(status="blocked", note="Direct access refused and no Tavily key configured")
                continue

        before = tavily.credits_used
        try:
            got = [l for l in mod.fetch() if l and l.is_valid()]
            seen = set()
            got = [l for l in got if not (l.key in seen or seen.add(l.key))]
            fresh.extend(got)
            ok_sources.add(mod.NAME)
            entry.update(status="ok", count=len(got))
            used = tavily.credits_used - before
            if used:
                entry["note"] = f"{used} Tavily credit{'s' if used != 1 else ''}"
                state["tavily_last_used_at"][mod.NAME] = store.iso(store.now())
            print(f"[{mod.NAME}] ok, {len(got)} listings" + (f", {used} tavily credits" if used else ""))
        except Blocked as e:
            entry.update(status="blocked", note=str(e)[:160])
            print(f"[{mod.NAME}] blocked: {e}")
        except tavily.TavilyUnavailable as e:
            entry.update(status="blocked", note=f"Tavily unavailable: {e}"[:160])
            print(f"[{mod.NAME}] tavily unavailable: {e}")
        except Exception as e:
            entry.update(status="error", note=f"{type(e).__name__}: {e}"[:160])
            print(f"[{mod.NAME}] error:")
            traceback.print_exc()

    listings_store, new_keys, gone_keys = store.merge(listings_store, fresh, ok_sources)
    for rec in listings_store.values():
        rec["source_label"] = labels.get(rec["source"], rec["source"])
    print(f"merged: {len(fresh)} fetched, {len(new_keys)} new, {len(gone_keys)} gone, {len(store.active(listings_store))} active")

    render.render(listings_store, status, state)

    # Same apartment on several platforms: only the primary record counts as new.
    primaries = dedupe.collapse(store.active(listings_store))
    primary_keys = {r["key"] for r in primaries}
    old_apartments = {dedupe.apartment_key(r) for r in listings_store.values() if r["key"] not in new_keys}
    new_keys = [k for k in new_keys if k in primary_keys
                and not (dedupe.apartment_key(listings_store[k]) and dedupe.apartment_key(listings_store[k]) in old_apartments)]

    # Immediate alerts for new, cheap, whole-apartment listings.
    alert_pool = [listings_store[k] for k in new_keys
                  if listings_store[k]["price"] <= config.ALERT_PRICE
                  and not ({"sublet", "room-or-share"} & set(listings_store[k].get("flags") or []))
                  and k not in state["alerted_keys"]]
    if alert_pool and not no_email and state["runs"]:
        # state["runs"] guard: never alert on the very first run, which sees everything as new.
        emailer.send(f"{len(alert_pool)} new studio{'s' if len(alert_pool) != 1 else ''} under ${config.ALERT_PRICE:,}",
                     emailer.build_alert(alert_pool))
        state["alerted_keys"] = (state["alerted_keys"] + [l["key"] for l in alert_pool])[-2000:]

    # Twice-daily digest.
    now_ny = ny_now()
    last_digest = store.parse(state["last_digest_at"]) if state["last_digest_at"] else None
    due = now_ny.hour in config.DIGEST_HOURS_NY and (last_digest is None or store.now() - last_digest > timedelta(hours=5))
    if (due or force_digest) and not no_email:
        since = last_digest or (store.now() - timedelta(hours=12))
        since_label = since.astimezone(ZoneInfo(config.TIMEZONE)).strftime("%a %-I %p") if last_digest else "the last 12 hours"
        new_since = [r for r in primaries
                     if store.parse(r["first_seen"]) >= since and r["key"] not in state["digested_keys"]]
        drops = [r for r in primaries
                 if r.get("price_history") and store.parse(r["price_history"][-1]["at"]) >= since
                 and r["price_history"][-1]["to"] < r["price_history"][-1]["from"]]
        subject = f"NYC studios: {len(new_since)} new under ${config.MAX_PRICE:,}" + (f", {len(drops)} price drop{'s' if len(drops) != 1 else ''}" if drops else "")
        emailer.send(subject, emailer.build_digest(new_since, drops, len(store.active(listings_store)), status, since_label))
        state["last_digest_at"] = store.iso(store.now())
        state["digested_keys"] = (state["digested_keys"] + [r["key"] for r in new_since])[-4000:]

    state["runs"] = (state["runs"] + [{
        "at": store.iso(store.now()), "fetched": len(fresh), "new": len(new_keys), "gone": len(gone_keys),
        "active": len(store.active(listings_store)), "tavily_credits": tavily.credits_used,
        "seconds": round(time.time() - t0, 1),
        "sources": {s["name"]: s["status"] for s in status},
    }])[-500:]
    store.save_json(store.LISTINGS_PATH, listings_store)
    store.save_json(store.STATE_PATH, state)
    return status


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    st = run(force_digest=a.digest, no_email=a.no_email, only=a.only)
    bad = [s for s in st if s["status"] == "error"]
    sys.exit(0)  # errors in individual sources are reported on the page, not fatal
