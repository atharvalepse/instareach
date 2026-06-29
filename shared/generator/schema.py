"""LeadEnrichment — the contract between the extension and the generator.

The field names mirror exactly what the igscrapper extension emits in
"full enrichment" mode (its CSV columns / hydrate record), so a scraped row
drops straight in with no remapping. `from_dict` tolerates the messy real
shapes: "yes"/"no" strings, "video-first (reels/short-form)" labels,
space-joined "#a #b" hashtags, and blank/missing cells.
"""

from dataclasses import dataclass, field
from typing import List, Optional


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"yes", "true", "1", "y"}


def _int(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None


@dataclass
class LeadEnrichment:
    """Normalized view of one scraped lead, ready for message generation."""

    username: str
    full_name: str = ""
    relationship: str = ""          # follower | following | follower+following
    bio: str = ""
    category: str = ""              # e.g. "Fitness Trainer", "Musician/Band"
    is_business: bool = False
    is_private: bool = False
    is_verified: bool = False
    followers: Optional[int] = None
    posts_total: Optional[int] = None
    top_hashtags: List[str] = field(default_factory=list)   # ["#yoga", "#fitness"]
    inferred_activity: str = ""     # the extension's activity label
    days_since_last_post: Optional[int] = None

    # ---- derived signals (filled in __post_init__) ----------------------
    first_name: str = ""
    primary_topic: str = ""         # cleaned first hashtag, no '#'
    activity_kind: str = ""         # video | carousel | photo | mixed | none
    clean_category: str = ""        # first segment, lowercased

    def __post_init__(self):
        self.first_name = self._derive_first_name(self.full_name)
        self.primary_topic = self.top_hashtags[0].lstrip("#").strip() if self.top_hashtags else ""
        self.activity_kind = self._derive_activity(self.inferred_activity)
        self.clean_category = self.category.split("/")[0].strip().lower() if self.category else ""

    @staticmethod
    def _derive_first_name(full_name: str) -> str:
        """First plausible name token, or "" (we'd rather say 'there' than guess).

        Accepts a token as a name only if it's capitalized (real names usually
        are in full_name) or reasonably long — so handles like "rk" or stray
        initials don't become "Hey Rk".
        """
        for token in str(full_name).replace(".", " ").split():
            letters = "".join(ch for ch in token if ch.isalpha())
            if len(letters) >= 2 and (not letters.islower() or len(letters) >= 4):
                return letters[:1].upper() + letters[1:]
        return ""

    @staticmethod
    def _derive_activity(label: str) -> str:
        l = str(label).lower()
        if "video" in l or "reel" in l:
            return "video"
        if "carousel" in l:
            return "carousel"
        if "photo" in l or "lifestyle" in l:
            return "photo"
        if "no public posts" in l:
            return "none"
        if "mixed" in l:
            return "mixed"
        return ""

    @classmethod
    def from_dict(cls, d: dict) -> "LeadEnrichment":
        """Build from a raw extension row (CSV/JSON) with tolerant parsing."""
        raw_tags = d.get("top_hashtags", "")
        if isinstance(raw_tags, str):
            tags = [t for t in raw_tags.replace(",", " ").split() if t.strip()]
        else:
            tags = list(raw_tags or [])
        return cls(
            username=str(d.get("username", "")).lstrip("@").strip(),
            full_name=str(d.get("full_name", "") or "").strip(),
            relationship=str(d.get("relationship", "") or "").strip(),
            bio=str(d.get("bio", "") or "").strip(),
            category=str(d.get("category", "") or "").strip(),
            is_business=_truthy(d.get("is_business")),
            is_private=_truthy(d.get("is_private")),
            is_verified=_truthy(d.get("is_verified")),
            followers=_int(d.get("followers")),
            posts_total=_int(d.get("posts_total")),
            top_hashtags=tags,
            inferred_activity=str(d.get("inferred_activity", "") or "").strip(),
            days_since_last_post=_int(d.get("days_since_last_post")),
        )
