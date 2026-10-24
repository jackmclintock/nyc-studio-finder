"""Registry of sources. Each module exposes:

    NAME: str                 short id, e.g. "craigslist"
    LABEL: str                human label, e.g. "Craigslist"
    NEEDS_TAVILY: bool        True if the source is known to block direct requests
    def fetch() -> list[Listing]

fetch() must return only studios at or under config.MAX_PRICE inside the
search area (neighborhood not None), already deduplicated by source_id.
It must raise finder.http.Blocked if the source refuses us, so run.py can
record that honestly rather than treat it as "no listings".
"""
import importlib
import pkgutil

def all_sources():
    mods = []
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_") or m.name == "base":
            continue
        mod = importlib.import_module(f"{__name__}.{m.name}")
        if hasattr(mod, "fetch") and hasattr(mod, "NAME"):
            mods.append(mod)
    return sorted(mods, key=lambda m: m.NAME)
