"""Group the same apartment listed on several platforms.

apartment_key() builds a loose fingerprint from street + unit + price. It only
fires when a listing has an address with a street name and a unit, so it never
merges two different apartments in the same building.
"""
import re

UNIT = re.compile(r"(?:#|\bapt\.?|\bunit|\bfl(?:oor)?\.?|\bste\.?|\bsuite)\s*([a-z0-9-]+)", re.I)
HOUSE = re.compile(r"^\s*\d+[a-z]?\s+")
STREET_NOISE = re.compile(r"\b(street|st|avenue|ave|place|pl|road|rd|boulevard|blvd|east|west|north|south|e|w|n|s)\b\.?", re.I)
ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b", re.I)


def apartment_key(rec):
    addr = (rec.get("address") or "").split(",")[0]
    if not addr:
        return None
    m = UNIT.search(addr)
    if not m:
        return None
    unit = m.group(1).lower().lstrip("0") or "0"
    street = UNIT.sub("", addr)
    street = HOUSE.sub("", street)
    street = ORDINAL.sub(r"\1", street)
    street = STREET_NOISE.sub("", street)
    street = re.sub(r"[^a-z0-9]+", " ", street.lower()).strip()
    street = re.sub(r"\s+", " ", street)
    # Drop cross-street hints like "mercer & prince": keep first token group
    street = street.split(" ")[0] if street else ""
    if not street:
        return None
    return f"{street}|{unit}|{rec.get('price')}"


def group(records):
    """Return {key: [records]} for records that share an apartment_key (2+ only)."""
    groups = {}
    for r in records:
        k = apartment_key(r)
        if k:
            groups.setdefault(k, []).append(r)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _score(r):
    return (1 if r.get("image_url") else 0, 1 if r.get("description") else 0, -len(r.get("flags") or []))


def collapse(records):
    """Mark duplicates: the best record of each group gets rec["also"] = [{source_label, url}],
    the rest get rec["dup_of"] = primary key. Returns the list of primary records only."""
    for r in records:
        r.pop("also", None)
        r.pop("dup_of", None)
    out = []
    grouped = group(records)
    hidden = set()
    for k, rows in grouped.items():
        rows.sort(key=lambda r: (_score(r), r.get("first_seen") or ""), reverse=True)
        primary = rows[0]
        primary["also"] = [{"source": r["source"], "source_label": r.get("source_label") or r["source"], "url": r["url"]} for r in rows[1:]]
        primary["first_seen"] = min(r["first_seen"] for r in rows)
        for r in rows[1:]:
            r["dup_of"] = primary["key"]
            hidden.add(r["key"])
    return [r for r in records if r["key"] not in hidden]
