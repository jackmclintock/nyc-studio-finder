"""The one shape every scraper must produce."""
from dataclasses import dataclass, field, asdict
from typing import Optional, List


@dataclass
class Listing:
    source: str                 # short id of the source, e.g. "craigslist"
    source_id: str              # stable id on that source (so re-runs dedupe)
    url: str                    # link to the listing on the hosting platform
    price: int                  # USD per month
    title: str
    neighborhood: Optional[str]         # canonical name from neighborhoods.normalize, or None
    neighborhood_raw: Optional[str] = None   # what the source actually said
    address: Optional[str] = None
    bedrooms: float = 0         # 0 = studio
    bathrooms: Optional[float] = None
    sqft: Optional[int] = None
    no_fee: Optional[bool] = None
    posted_at: Optional[str] = None      # ISO 8601 if the source gives it
    image_url: Optional[str] = None
    description: Optional[str] = None    # short, max ~300 chars
    flags: List[str] = field(default_factory=list)  # e.g. "alcove", "junior-1br", "sublet", "area-inferred"
    via_tavily: bool = False

    @property
    def key(self):
        return f"{self.source}:{self.source_id}"

    def to_dict(self):
        d = asdict(self)
        d["key"] = self.key
        return d

    def is_valid(self):
        return bool(self.url and self.price and self.source_id and self.title)
