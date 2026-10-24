"""Canonical neighbourhood list, aliases and a zip-code fallback.

Every scraper must call normalize() on whatever neighbourhood string its source
gives it. Returns the canonical name, or None if the listing is outside the
search area.
"""
import re

CANONICAL = [
    "Chelsea", "Hudson Yards", "Hudson Square", "Tribeca", "Hell's Kitchen",
    "East Village", "Lower East Side", "Kips Bay", "Murray Hill", "Turtle Bay",
    "Two Bridges", "Chinatown", "Financial District", "Battery Park City",
    "Williamsburg", "Midtown South", "Midtown West", "Lincoln Square", "Flatiron",
    "NoMad", "West Village", "Nolita", "Greenwich Village", "Gramercy Park",
    "Little Italy", "NoHo", "SoHo",
]

# lowercase alias -> canonical
ALIASES = {
    "chelsea": "Chelsea", "west chelsea": "Chelsea",
    "hudson yards": "Hudson Yards",
    "hudson square": "Hudson Square",
    "tribeca": "Tribeca", "tri-beca": "Tribeca", "civic center": "Tribeca",
    "hell's kitchen": "Hell's Kitchen", "hells kitchen": "Hell's Kitchen", "clinton": "Hell's Kitchen",
    "east village": "East Village", "alphabet city": "East Village",
    "lower east side": "Lower East Side", "les": "Lower East Side", "bowery": "Lower East Side",
    "kips bay": "Kips Bay",
    "murray hill": "Murray Hill",
    "turtle bay": "Turtle Bay", "midtown east": "Turtle Bay", "tudor city": "Turtle Bay", "beekman": "Turtle Bay", "sutton place": "Turtle Bay",
    "two bridges": "Two Bridges",
    "chinatown": "Chinatown",
    "financial district": "Financial District", "fidi": "Financial District",
    "south street seaport": "Financial District", "seaport": "Financial District", "fulton/seaport": "Financial District",
    "battery park city": "Battery Park City", "battery park": "Battery Park City",
    "williamsburg": "Williamsburg", "south williamsburg": "Williamsburg", "north williamsburg": "Williamsburg",
    "east williamsburg": "Williamsburg",
    "midtown south": "Midtown South", "midtown": "Midtown South", "garment district": "Midtown South",
    "koreatown": "Midtown South",
    "midtown west": "Midtown West", "theater district": "Midtown West", "times square": "Midtown West",
    "central park south": "Midtown West",
    "lincoln square": "Lincoln Square", "lincoln center": "Lincoln Square",
    "flatiron": "Flatiron", "flatiron district": "Flatiron", "union square": "Flatiron",
    "nomad": "NoMad", "rose hill": "NoMad",
    "west village": "West Village", "meatpacking district": "West Village", "meatpacking": "West Village",
    "nolita": "Nolita",
    "greenwich village": "Greenwich Village", "the village": "Greenwich Village", "central village": "Greenwich Village",
    "gramercy park": "Gramercy Park", "gramercy": "Gramercy Park", "stuyvesant town": "Gramercy Park",
    "stuytown": "Gramercy Park", "stuy town": "Gramercy Park", "peter cooper village": "Gramercy Park",
    "little italy": "Little Italy",
    "noho": "NoHo",
    "soho": "SoHo",
}

# Zip codes that fall mostly inside the search area. Used only when a source
# gives no usable neighbourhood name. The mapping is the dominant neighbourhood.
ZIP_FALLBACK = {
    "10001": "Chelsea", "10011": "Chelsea", "10018": "Midtown West", "10019": "Hell's Kitchen",
    "10036": "Hell's Kitchen", "10013": "Tribeca", "10007": "Tribeca", "10038": "Financial District",
    "10004": "Financial District", "10005": "Financial District", "10006": "Financial District",
    "10280": "Battery Park City", "10281": "Battery Park City", "10282": "Battery Park City",
    "10271": "Financial District", "10279": "Financial District", "10020": "Midtown West", "10002": "Lower East Side",
    "10003": "East Village", "10009": "East Village", "10012": "SoHo", "10014": "West Village",
    "10010": "Flatiron", "10016": "Murray Hill", "10017": "Turtle Bay", "10022": "Turtle Bay",
    "10023": "Lincoln Square", "10069": "Lincoln Square", "11249": "Williamsburg", "11211": "Williamsburg",
}


# Named areas that are definitely outside the search. If a source says one of
# these, we stop there rather than let a zip or description pull it back in.
EXCLUDE = {
    "upper west side", "uws", "upper east side", "ues", "harlem", "east harlem", "washington heights",
    "inwood", "morningside heights", "manhattan valley", "yorkville", "lenox hill", "carnegie hill",
    "roosevelt island", "greenpoint", "bushwick", "bedford-stuyvesant", "bed-stuy",
    "bed stuy", "clinton hill", "fort greene", "downtown brooklyn", "brooklyn heights", "dumbo",
    "park slope", "crown heights", "prospect heights", "long island city", "lic", "astoria",
    "sunnyside", "ridgewood", "jersey city", "hoboken", "stuyvesant heights", "boerum hill",
    "cobble hill", "carroll gardens", "gowanus", "red hook", "sunset park", "bay ridge", "flatbush",
}


def is_excluded(name):
    if not name:
        return False
    key = re.sub(r"\s+", " ", str(name).strip().lower())
    return key in EXCLUDE or any(re.search(r"\b" + re.escape(e) + r"\b", key) for e in EXCLUDE if len(e) > 4)


def normalize(name):
    """Map a source's neighbourhood string to a canonical name, or None."""
    if not name:
        return None
    key = re.sub(r"\s+", " ", str(name).strip().lower())
    key = key.replace("’", "'")
    if key in ALIASES:
        return ALIASES[key]
    # Try "Neighbourhood, Manhattan" and similar
    for part in re.split(r"[,/|]", key):
        part = part.strip()
        if part in ALIASES:
            return ALIASES[part]
    # Substring match for things like "Prime West Village"
    for alias, canon in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(alias) + r"\b", key):
            return canon
    return None


def from_zip(zipcode):
    if not zipcode:
        return None
    z = str(zipcode).strip()[:5]
    return ZIP_FALLBACK.get(z)


def find_in_text(text):
    """Scan free text (title, description) for a neighbourhood mention."""
    if not text:
        return None
    return normalize(text)
